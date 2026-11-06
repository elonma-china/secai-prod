"""Default-off private GPU broker: one disposable OCR or MT child per FIFO stage.

Importing this module neither samples the GPU nor starts a model. The single
ASGI process owns the cross-process lock; each child inherits it until death.
"""
from __future__ import annotations

import asyncio
import base64
import binascii
import hmac
import ipaddress
import json
import os
import re
import time
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.requests import ClientDisconnect

from deploy.mt_cpu_service.service import (
    ServiceFailure, TranslationRuntime, _unique_object, _valid_request,
)
from evals.multilang import gpu_specialist_server as gpu

from .coordinator import GpuBudget, GpuStageQueue, StageFailure
from .mt_diagnostic import GuardedBackend, Q4_CANDIDATE, diagnostic_settings
from .owner import GpuStageOwner, NvidiaSmiObserver, PinnedCommand

_OCR_MODEL = "Qwen/Qwen3-VL-2B-Instruct"
_OCR_CHILD_SHA = "a5f60b38383de43c7111c67778042269213b4f1804a52f1f9ff11dded1a344b5"
_OCR_BOUNDED_SHA = "9308887a3939f08d000b833fa92190b1f826e0b273a86b7a89271ed88098d1aa"
_OCR_HELPER_SHA = "d8edaaf7a9dae4d784b9cef1d361bb855216922dcd0dbb3622f62cdfb8289543"
_OCR_WEIGHT_SHA = "7de1838c87a5349b016c26a1c3f7d2bc400a3d485f95ef39a7059ffd734977a0"
_OCR_CHILD = Path(__file__).parents[2] / "evals/translation_ocr/real_quality/qwen_ocr_child.py"
_OCR_BOUNDED = _OCR_CHILD.with_name("qwen_server_real_pages_bounded.py")
_OCR_HELPER = Path(__file__).parents[1] / "ocr/server.py"
_MAX_MT_BODY = 128 * 1024
_MAX_OCR_BODY = 15_100_000
_MAX_RESULT = 256 * 1024


def _error(status: int, code: str) -> JSONResponse:
    return JSONResponse({"error": code}, status_code=status,
                        headers={"Cache-Control": "no-store"})


def _authorized(request: Request, token: str) -> bool:
    return bool(token) and hmac.compare_digest(request.headers.get("authorization", ""),
                                                f"Bearer {token}")


def _loopback(request: Request) -> bool:
    try:
        return bool(request.client and ipaddress.ip_address(request.client.host).is_loopback)
    except ValueError:
        return False


async def _body(request: Request, cap: int, deadline: float) -> dict[str, Any]:
    data = bytearray()
    async def read() -> None:
        async for part in request.stream():
            data.extend(part)
            if len(data) > cap:
                raise StageFailure("INPUT_TOO_LONG", pre_admission=True)
    await asyncio.wait_for(read(), max(0, deadline - time.monotonic()))
    result = json.loads(data, object_pairs_hook=_unique_object)
    if not isinstance(result, dict):
        raise ValueError("JSON object required")
    return result


def _ocr_request(body: dict[str, Any]) -> bool:
    if set(body) != {"model", "image_base64"} or body.get("model") != _OCR_MODEL:
        return False
    encoded = body.get("image_base64")
    if not isinstance(encoded, str) or not encoded or len(encoded) > 15_000_000:
        return False
    try:
        decoded = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError):
        return False
    return 0 < len(decoded) <= 10 * 1024 * 1024


def _ocr_result(value: Any) -> bool:
    if not isinstance(value, dict) or set(value) != {"model", "blocks"} or value["model"] != _OCR_MODEL:
        return False
    rows = value["blocks"]
    if not isinstance(rows, list) or len(rows) > 1024:
        return False
    total = 0
    for row in rows:
        if not isinstance(row, dict) or set(row) != {"text", "bbox", "confidence"}:
            return False
        value, box, confidence = row["text"], row["bbox"], row["confidence"]
        if not isinstance(value, str) or not value or "\x00" in value:
            return False
        try:
            value.encode("utf-8")
        except UnicodeError:
            return False
        total += len(value)
        if total > 200_000:
            return False
        if box is not None and (not isinstance(box, list) or len(box) != 4 or
                                any(type(x) is not int or x < 0 for x in box)):
            return False
        if confidence is not None and (type(confidence) not in (int, float) or
                                       not 0 <= confidence <= 1):
            return False
    return True


def _mt_command() -> PinnedCommand:
    log = Path(os.environ.get("GPU_STAGE_LOG_DIR", "/tmp/secai-gpu-stage-logs")) / f"mt-{time.time_ns()}.log"
    child_key_file = os.environ.get("GPU_STAGE_MT_TOKEN_FILE", "/missing/mt-token")
    libraries = tuple((gpu.SERVER.parent / name, digest)
                      for name, digest in gpu.RUNTIME_SO_HASHES.items())
    return PinnedCommand(
        binary=gpu.SERVER,
        args=tuple(gpu.server_command(gpu.FILE_DIAGNOSTIC_CONTEXT_SIZE)[1:] +
                   ["--api-key-file", child_key_file]),
        sha256=gpu.SERVER_SHA256,
        artifacts=((gpu.MODEL, gpu.MODEL_SHA256), (gpu.COMPAT_LIBRARY, gpu.COMPAT_SHA256),
                   *libraries),
        environment={"LD_LIBRARY_PATH": "/usr/local/cuda-13.0/compat:/usr/local/cuda-13.0/lib64",
                     "CUDA_VISIBLE_DEVICES": "0"}, stdio="log", log_path=log)


def _ocr_command() -> PinnedCommand:
    python = Path(os.environ.get("GPU_STAGE_OCR_PYTHON", "/missing/ocr-python"))
    model = Path(os.environ.get("GPU_STAGE_OCR_MODEL_PATH", "/missing/ocr-model"))
    weight = Path(os.environ.get("GPU_STAGE_OCR_WEIGHT_PATH", str(model / "model.safetensors")))
    if (not model.is_absolute() or not weight.is_absolute() or
            weight != model / "model.safetensors"):
        raise StageFailure("ARTIFACT", pre_admission=True)
    owner_pid_file = Path(os.environ.get("GPU_STAGE_OCR_PID_DIR", "/tmp/secai-gpu-stage-ocr")) / f"owner-{time.time_ns()}"
    owner_pid_file.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    return PinnedCommand(
        binary=python, args=(str(_OCR_CHILD),),
        sha256=os.environ.get("GPU_STAGE_OCR_PYTHON_SHA256", ""),
        artifacts=((_OCR_CHILD, _OCR_CHILD_SHA), (_OCR_BOUNDED, _OCR_BOUNDED_SHA),
                   (_OCR_HELPER, _OCR_HELPER_SHA), (weight, _OCR_WEIGHT_SHA)),
        environment={"OCR_MODEL_ID": _OCR_MODEL, "OCR_MODEL_PATH": str(model),
                     "OCR_OWNED_PID_FILE": str(owner_pid_file), "OCR_MAX_PEAK_MIB": "6144",
                     "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1",
                     "PYTHONPATH": f"{_OCR_CHILD.parent}:{_OCR_HELPER.parent}",
                     "CUDA_VISIBLE_DEVICES": "0"}, stdio="pipes")


async def _mt_proof(proc, host_pid: int, command: PinnedCommand) -> bool | None:
    try:
        listener = await asyncio.to_thread(gpu.listener_pid)
        if listener is not None and listener != proc.pid:
            return False
        if listener is None or not await asyncio.to_thread(gpu._full_offload, command.log_path):
            return None
        return True
    except (OSError, RuntimeError, ValueError):
        return False


async def _ocr_proof(_proc, host_pid: int, command: PinnedCommand) -> bool | None:
    path = Path(command.environment["OCR_OWNED_PID_FILE"])
    try:
        recorded = await asyncio.to_thread(path.read_text, encoding="ascii")
    except FileNotFoundError:
        return None
    try:
        return int(recorded) == host_pid
    except ValueError:
        return False


async def _mt_work(proc, cancel: asyncio.Event, deadline: float,
                   text: str, target: str, log_path: Path, token: str) -> str:
    # Never send a prompt to a health endpoint belonging to another process.
    while time.monotonic() < deadline and not cancel.is_set() and proc.poll() is None:
        listener = await asyncio.to_thread(gpu.listener_pid)
        if listener is not None and listener != proc.pid:
            raise StageFailure("GPU_OWNER", poison=True)
        if listener == proc.pid and await asyncio.to_thread(gpu._full_offload, log_path):
            break
        await asyncio.sleep(.1)
    else:
        raise StageFailure("TIMEOUT")
    async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{gpu.PORT}",
                                 headers={"Authorization": f"Bearer {token}"},
                                 trust_env=False, follow_redirects=False) as client:
        backend = GuardedBackend(client)
        while time.monotonic() < deadline and not cancel.is_set() and proc.poll() is None:
            try:
                await backend.start(deadline=min(deadline, time.monotonic() + 1))
                break
            except (ServiceFailure, httpx.HTTPError, ValueError):
                await asyncio.sleep(.1)
        if not backend.live or cancel.is_set():
            raise StageFailure("TIMEOUT")
        runtime = TranslationRuntime(diagnostic_settings(token="broker-internal-token", token_file=None), backend)
        return await runtime._translate_inner(text, target, deadline)


async def _ocr_work(proc, _cancel: asyncio.Event, deadline: float,
                    body: dict[str, Any]) -> dict[str, Any]:
    payload = json.dumps({**body, "calibration_prompt": "conditional"},
                         separators=(",", ":")).encode("utf-8")
    try:
        stdout, _ = await asyncio.wait_for(asyncio.to_thread(proc.communicate, payload),
                                            max(0, deadline - time.monotonic()))
        if len(stdout) > _MAX_RESULT:
            raise StageFailure("BAD_UPSTREAM")
        message = json.loads(stdout, object_pairs_hook=_unique_object)
        if (not isinstance(message, dict) or set(message) != {"status", "body"}
                or message["status"] != 200 or not _ocr_result(message["body"])):
            raise StageFailure("BAD_UPSTREAM")
        return message["body"]
    except (UnicodeError, ValueError, TypeError):
        raise StageFailure("BAD_UPSTREAM") from None


def create_broker_app(*, owner: GpuStageOwner, queue: GpuStageQueue,
                      mt_token: str, ocr_token: str,
                      enable_mt: bool = False, enable_ocr: bool = False) -> FastAPI:
    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        await owner.start()
        try:
            yield
        finally:
            await owner.close()

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)

    async def run(request: Request, stage: str) -> JSONResponse:
        deadline = time.monotonic() + (105 if stage == "mt" else 120)
        token = mt_token if stage == "mt" else ocr_token
        if not _loopback(request):
            return _error(403, "FORBIDDEN")
        if not _authorized(request, token):
            return _error(401, "UNAUTHORIZED")
        if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json":
            return _error(400, "BAD_REQUEST")
        try:
            body = await _body(request, _MAX_MT_BODY if stage == "mt" else _MAX_OCR_BODY, deadline)
            if (stage == "mt" and not _valid_request(body)) or (stage == "ocr" and not _ocr_request(body)):
                return _error(400, "BAD_REQUEST")
            if not (enable_mt if stage == "mt" else enable_ocr):
                return _error(503, "DISABLED")
            if stage == "mt" and body["source_lang"] == body["target_lang"]:
                return JSONResponse({"translated_text": body["text"], "model": Q4_CANDIDATE.model},
                                    headers={"Cache-Control": "no-store"})
            disconnected = asyncio.Event()
            async def watch_disconnect() -> None:
                while not disconnected.is_set():
                    if await request.is_disconnected():
                        disconnected.set()
                        return
                    await asyncio.sleep(.05)
            watcher = asyncio.create_task(watch_disconnect())
            try:
                async def operation(cancel: asyncio.Event):
                    command = _mt_command() if stage == "mt" else _ocr_command()
                    if stage == "mt":
                        async def work(proc, active_cancel, until):
                            return await _mt_work(proc, active_cancel, until,
                                                  body["text"], body["target_lang"],
                                                  command.log_path, mt_token)
                        async def proof(proc, host_pid):
                            return await _mt_proof(proc, host_pid, command)
                    else:
                        async def work(proc, active_cancel, until):
                            return await _ocr_work(proc, active_cancel, until, body)
                        async def proof(proc, host_pid):
                            return await _ocr_proof(proc, host_pid, command)
                    return await owner.run(command, work, proof, deadline=deadline, cancel=cancel)
                value = await queue.submit(stage, operation, deadline=deadline,
                                           disconnected=disconnected)
            finally:
                watcher.cancel()
                with suppress(asyncio.CancelledError):
                    await watcher
            response = ({"translated_text": value, "model": Q4_CANDIDATE.model}
                        if stage == "mt" else value)
            return JSONResponse(response, headers={"Cache-Control": "no-store"})
        except StageFailure as exc:
            status = (429 if exc.code == "BUSY" else 413 if exc.code == "INPUT_TOO_LONG" else
                      504 if exc.code == "TIMEOUT" else 503)
            return _error(status, exc.code)
        except ServiceFailure as exc:
            return _error(exc.status, exc.code)
        except (TimeoutError, asyncio.TimeoutError):
            return _error(504, "TIMEOUT")
        except (ClientDisconnect, httpx.TransportError):
            return _error(503, "UNAVAILABLE")
        except (UnicodeError, ValueError, TypeError):
            return _error(400, "BAD_REQUEST")
        except Exception:
            # Child, filesystem, and GPU sampling details are private. The
            # owner keeps its reservation until its own cleanup completes.
            return _error(503, "UNAVAILABLE")

    @app.post("/v1/translate")
    async def translate(request: Request):
        return await run(request, "mt")

    @app.post("/v1/ocr")
    async def ocr(request: Request):
        return await run(request, "ocr")

    return app


def _private_token(env_name: str) -> str:
    path = Path(os.environ[env_name])
    if path.stat().st_mode & 0o077:
        raise ValueError("private token file permissions")
    token = path.read_text(encoding="ascii").removesuffix("\n")
    if not re.fullmatch(r"[A-Za-z0-9_-]{32,256}", token):
        raise ValueError("invalid private token")
    return token


def create_gpu_stage_app() -> FastAPI:
    """Uvicorn factory; both stage types default OFF until explicitly enabled."""
    if os.getenv("GPU_STAGE_BROKER_ENABLED") != "1":
        raise RuntimeError("GPU broker disabled")
    if gpu.POISONED.exists():
        raise RuntimeError("prior GPU cleanup is unproved")
    enable_mt = os.getenv("GPU_STAGE_MT_ENABLED") == "1"
    enable_ocr = os.getenv("GPU_STAGE_OCR_ENABLED") == "1"
    if not (enable_mt or enable_ocr):
        raise RuntimeError("no GPU stage enabled")
    mt_token = _private_token("GPU_STAGE_MT_TOKEN_FILE") if enable_mt else ""
    ocr_token = _private_token("GPU_STAGE_OCR_TOKEN_FILE") if enable_ocr else ""
    owner = GpuStageOwner(gpu.LOCK,
                          GpuBudget(gpu.GPU_UUID, gpu.EXPECTED_INCUMBENTS),
                          NvidiaSmiObserver())
    return create_broker_app(owner=owner, queue=GpuStageQueue(max_pending=2),
                             mt_token=mt_token, ocr_token=ocr_token,
                             enable_mt=enable_mt, enable_ocr=enable_ocr)
