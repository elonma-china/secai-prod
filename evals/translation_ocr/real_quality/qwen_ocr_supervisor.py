"""CPU-only single-flight supervisor for one disposable Qwen OCR child/request."""
from __future__ import annotations

import asyncio
import fcntl
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException, Request as FastAPIRequest
from pydantic import BaseModel, Field

MODEL_ID = "Qwen/Qwen3-VL-2B-Instruct"
REVISION = "89644892e4d85e24eaac8bacfd4f463576704203"
CHILD_PATH = Path(__file__).with_name("qwen_ocr_child.py")
DEADLINE_SECONDS = 100.0
RESPONSE_SECONDS = 110.0
SAMPLE_INTERVAL = .25
MAX_OWNED_MIB = int(os.environ.get("OCR_MAX_PEAK_MIB", "6144"))
if not 1 <= MAX_OWNED_MIB <= 7168:
    raise RuntimeError("invalid OCR GPU budget")
_lock = threading.Lock()
_baseline = None
_last_error = ""
app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)


def _check_shared_cleanup_history(lock_path: Path) -> None:
    # The MT owner and guarded diagnostic use this durable veto namespace.
    # Check only after acquiring the shared flock to exclude an active writer.
    if (lock_path.parent / "poisoned-cleanup.json").exists():
        raise ValueError("prior GPU cleanup unproved")
    for run_dir in (lock_path.parent / "evidence").glob("gpu-run-*"):
        if not run_dir.is_dir():
            raise ValueError("invalid GPU run record")
        summary = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
        if not isinstance(summary, dict) or summary.get("clean_exit_attested") is not True:
            raise ValueError("prior GPU cleanup unproved")


def _acquire_shared_gpu_lock() -> int:
    """Claim MT's flock before baseline; the OCR child inherits this FD."""
    path = Path(os.environ.get("OCR_SHARED_GPU_LOCK_PATH",
                               "/workspace/secai-gpu-specialists/admission.lock"))
    if not path.is_absolute():
        raise HTTPException(503, "OCR GPU admission unavailable")
    try:
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        descriptor = os.open(path, os.O_CREAT | os.O_RDWR | os.O_CLOEXEC | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            _check_shared_cleanup_history(path)
        except BaseException:
            os.close(descriptor)
            raise
        return descriptor
    except (OSError, ValueError) as exc:
        raise HTTPException(503, "OCR GPU admission unavailable") from exc


class OcrRequest(BaseModel):
    model: str
    image_base64: str = Field(max_length=15_000_000)
    calibration_prompt: str = "conditional"


@dataclass(frozen=True)
class GpuSnapshot:
    uuid: str
    free: int
    processes: dict[int, tuple[str, int]]


@dataclass(frozen=True)
class GpuSample:
    free: int
    owned: int
    host_pid: int | None = None
    unidentified_pid: int | None = None


def _trace(request_id: str, event: str, **fields) -> None:
    path = os.environ.get("OCR_TRACE_PATH")
    if not path:
        return
    try:
        with open(path, "a", encoding="utf-8") as sink:
            sink.write(json.dumps({"time_unix": time.time(), "request_id": request_id,
                                   "event": event, **fields}, sort_keys=True) + "\n")
    except OSError:
        # Logging failure must not leak the admission lock or OCR input.
        pass


def _snapshot() -> GpuSnapshot:
    def run(*args):
        return subprocess.check_output(["nvidia-smi", *args], text=True, timeout=3).strip()
    gpu = run("--query-gpu=uuid,memory.free", "--format=csv,noheader,nounits").splitlines()
    if len(gpu) != 1:
        raise RuntimeError("GPU device ambiguous")
    uuid, free = [part.strip() for part in gpu[0].split(",")]
    rows = run("--query-compute-apps=gpu_uuid,pid,used_gpu_memory",
               "--format=csv,noheader,nounits")
    processes = {}
    for row in rows.splitlines():
        parts = [part.strip() for part in row.split(",")]
        if len(parts) != 3:
            raise RuntimeError("GPU process sample malformed")
        processes[int(parts[1])] = (parts[0], int(parts[2]))
    return GpuSnapshot(uuid, int(free), processes)


def _sample_gpu(expected_pid: int | None = None,
                allow_unknown: bool = False) -> GpuSample:
    if _baseline is None:
        raise RuntimeError("GPU baseline missing")
    current = _snapshot()
    if current.uuid != _baseline.uuid or not set(_baseline.processes) <= set(current.processes):
        raise RuntimeError("GPU or incumbent changed")
    extra = set(current.processes) - set(_baseline.processes)
    if len(extra) > 1:
        raise RuntimeError("GPU ownership ambiguous")
    if extra and current.processes[next(iter(extra))][0] != current.uuid:
        raise RuntimeError("GPU process on wrong device")
    if extra and expected_pid is None:
        if not allow_unknown:
            raise RuntimeError("GPU process has no child ownership proof")
        # No allocation is attributed to OCR until the child corroborates PID.
        return GpuSample(current.free, 0, None, next(iter(extra)))
    if extra and next(iter(extra)) != expected_pid:
        raise RuntimeError(f"GPU host PID does not match child proof: expected={expected_pid} observed={next(iter(extra))}")
    owned = current.processes[expected_pid][1] if extra else 0
    return GpuSample(current.free, owned, expected_pid if extra else None)


def _check_gpu(sample: GpuSample) -> None:
    if not isinstance(sample.free, int) or not isinstance(sample.owned, int):
        raise RuntimeError("GPU measurement missing")
    if sample.owned < 0 or sample.owned > MAX_OWNED_MIB:
        raise RuntimeError("OCR owned GPU budget breached")
    if sample.free < 2048 + max(0, MAX_OWNED_MIB - sample.owned):
        raise RuntimeError("GPU free reserve breached")


def _ensure_auth(authorization: str) -> None:
    token = os.environ.get("OCR_SERVICE_TOKEN", "")
    if not token or authorization != f"Bearer {token}":
        raise HTTPException(401, "Unauthorized")


@app.get("/health")
def health():
    return {"ready": _baseline is not None and not _lock.locked(),
            "model": MODEL_ID, "revision": REVISION}


@app.get("/v1/eval-status")
def eval_status(authorization: str = Header(default="")):
    _ensure_auth(authorization)
    return {"busy": _lock.locked(), "ready": _baseline is not None}


async def _kill_child(child):
    if child.returncode is None:
        child.kill()  # Only the Popen child we created, never a GPU host PID.
    try:
        await asyncio.wait_for(child.wait(), timeout=8)
    except asyncio.TimeoutError:
        raise RuntimeError("owned OCR child did not exit")


async def _deadline_watch(child, until: float, request_id: str, started: float):
    """Kill the owned child on wall time even if GPU sampling is stalled."""
    await asyncio.sleep(max(0, until-time.monotonic()))
    if child.returncode is None:
        try:
            child.kill()
        except ProcessLookupError:
            return
        _trace(request_id, "deadline_kill",
               elapsed_seconds=round(time.monotonic()-started, 3))


async def _run(body: OcrRequest, connection, request_id: str, started: float):
    global _baseline, _last_error
    child = None
    communication = None
    watchdog = None
    peak_owned, min_free, sample_count = 0, 1_000_000, 0
    owner_verified = False
    first_unidentified = None
    error = None
    gpu_lock_fd = None
    with tempfile.TemporaryDirectory(prefix="ocr-owned-") as directory:
        owner_file = Path(directory) / "gpu-host-pid"
        try:
            gpu_lock_fd = _acquire_shared_gpu_lock()
            if _baseline is None:
                _baseline = await asyncio.to_thread(_snapshot)
            _check_gpu(await asyncio.to_thread(_sample_gpu, None))
            environment = {**os.environ, "OCR_OWNED_PID_FILE": str(owner_file),
                           "OCR_REQUEST_ID": request_id}
            child = await asyncio.create_subprocess_exec(
                sys.executable, str(CHILD_PATH), stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
                env=environment, pass_fds=(gpu_lock_fd,),
            )
            payload = body.model_dump_json().encode("utf-8")
            watchdog = asyncio.create_task(_deadline_watch(
                child, started + DEADLINE_SECONDS, request_id, started))
            communication = asyncio.create_task(child.communicate(payload))
            while not communication.done():
                if await connection.is_disconnected():
                    error = "OCR request disconnected"
                    _trace(request_id, "disconnect_observed",
                           elapsed_seconds=round(time.monotonic()-started, 3))
                    break
                if time.monotonic() - started >= DEADLINE_SECONDS:
                    error = "OCR inference deadline exceeded"
                    break
                try:
                    expected_pid = int(owner_file.read_text()) if owner_file.exists() else None
                    sample = await asyncio.to_thread(_sample_gpu, expected_pid,
                                                      expected_pid is None)
                    if owner_verified and sample.host_pid is None and sample.owned == 0:
                        # The child has released its CUDA context; driver free
                        # memory can lag GPU PID disappearance. Keep admission
                        # locked until the final cleanup monitor sees 8192 MiB.
                        # The absolute floor still applies while this child is
                        # alive; later recovery cannot erase a sampled breach.
                        if not isinstance(sample.free, int) or sample.free < 2048:
                            raise RuntimeError("GPU free reserve breached")
                        _trace(request_id, "gpu_cleanup_pending", free_mib=sample.free)
                    else:
                        _check_gpu(sample)
                    if sample.unidentified_pid is not None:
                        if first_unidentified is None:
                            first_unidentified = time.monotonic()
                        elif time.monotonic() - first_unidentified > 1:
                            raise RuntimeError("GPU child ownership proof delayed")
                    else:
                        first_unidentified = None
                    sample_count += 1
                    peak_owned = max(peak_owned, sample.owned)
                    min_free = min(min_free, sample.free)
                    if sample.owned:
                        owner_verified = expected_pid is not None and sample.host_pid == expected_pid
                except Exception as exc:
                    error = "OCR GPU measurement unavailable"
                    _trace(request_id, "gpu_monitor_error", reason=str(exc))
                    break
                await asyncio.wait({communication}, timeout=SAMPLE_INTERVAL)
            if error:
                await _kill_child(child)
                if communication:
                    await communication
                raise HTTPException(503, error)
            output, _ = await communication
            if child.returncode != 0 or len(output) > 256_000:
                raise HTTPException(503, "OCR worker unavailable")
            try:
                result = json.loads(output)
            except (ValueError, UnicodeDecodeError):
                raise HTTPException(503, "OCR worker unavailable")
            if result.get("status") != 200:
                raise HTTPException(503, result.get("detail", "OCR worker unavailable"))
            if not owner_verified or peak_owned <= 0:
                raise HTTPException(503, "OCR GPU owner unverified")
            if time.monotonic() - started >= DEADLINE_SECONDS:
                raise HTTPException(503, "OCR inference deadline exceeded")
            return result["body"]
        except asyncio.CancelledError:
            if child is not None:
                await _kill_child(child)
            raise
        except HTTPException:
            raise
        except Exception as exc:
            _last_error = type(exc).__name__
            raise HTTPException(503, "OCR service unavailable") from exc
        finally:
            if watchdog is not None:
                watchdog.cancel()
                try:
                    await watchdog
                except asyncio.CancelledError:
                    pass
            # Fail closed until the owned process is gone and full preload
            # reserve is available. This may continue after HTTP 503 at 110s.
            while child is not None:
                if child.returncode is None:
                    try:
                        await _kill_child(child)
                    except Exception as exc:
                        _last_error = type(exc).__name__
                        await asyncio.sleep(SAMPLE_INTERVAL)
                        continue
                try:
                    sample = await asyncio.to_thread(_sample_gpu,
                             int(owner_file.read_text()) if owner_file.exists() else None)
                    _check_gpu(sample)
                    if sample.owned == 0:
                        break
                except Exception as exc:
                    _last_error = type(exc).__name__
                await asyncio.sleep(SAMPLE_INTERVAL)
            _trace(request_id, "released", elapsed_seconds=round(time.monotonic()-started, 3),
                   peak_owned_mib=peak_owned, min_free_mib=min_free,
                   gpu_samples=sample_count, owner_verified=owner_verified)
            if gpu_lock_fd is not None:
                os.close(gpu_lock_fd)


@app.post("/v1/ocr")
async def ocr(body: OcrRequest, connection: FastAPIRequest,
              authorization: str = Header(default=""),
              x_eval_request_id: str = Header(default="")):
    _ensure_auth(authorization)
    if body.model != MODEL_ID or body.calibration_prompt != "conditional":
        raise HTTPException(422, "Unsupported OCR request")
    request_id = x_eval_request_id if x_eval_request_id.isascii() and len(x_eval_request_id) <= 80 else "invalid-id"
    if not _lock.acquire(blocking=False):
        raise HTTPException(503, "OCR busy")
    started = time.monotonic()
    try:
        worker = asyncio.create_task(_run(body, connection, request_id, started))
    except Exception:
        _lock.release()
        raise
    def finish(done):
        try:
            done.exception()  # Consume a detached post-timeout error.
        except asyncio.CancelledError:
            pass
        _lock.release()
        _trace(request_id, "finished", elapsed_seconds=round(time.monotonic()-started, 3))
    worker.add_done_callback(finish)
    try:
        return await asyncio.wait_for(asyncio.shield(worker), timeout=RESPONSE_SECONDS)
    except asyncio.TimeoutError as exc:
        raise HTTPException(503, "OCR cleanup pending") from exc
