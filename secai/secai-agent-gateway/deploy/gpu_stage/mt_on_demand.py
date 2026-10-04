"""Private on-demand MT stage sharing the existing OCR child's GPU flock.

This factory has no OCR route and starts no model at import or ASGI startup.
The lock is claimed only while a translated request owns a disposable child.
"""
from __future__ import annotations

import asyncio
import os
import re
import time
from pathlib import Path
from typing import Callable

import httpx

from deploy.mt_cpu_service.service import ServiceFailure, TranslationRuntime, create_app
from evals.multilang import gpu_specialist_server as gpu

from .coordinator import GpuBudget, GpuStageQueue, StageFailure
from .mt_diagnostic import GuardedBackend, diagnostic_settings
from .owner import GpuStageOwner, NvidiaSmiObserver, PinnedCommand


def _command(token_file: Path) -> PinnedCommand:
    log_dir = Path(os.environ.get("GPU_STAGE_LOG_DIR", "/tmp/secai-gpu-stage-logs"))
    log = log_dir / f"mt-{time.time_ns()}.log"
    libraries = tuple((gpu.SERVER.parent / name, digest)
                      for name, digest in gpu.RUNTIME_SO_HASHES.items())
    return PinnedCommand(
        binary=gpu.SERVER,
        args=tuple(gpu.server_command(gpu.FILE_DIAGNOSTIC_CONTEXT_SIZE)[1:] +
                   ["--api-key-file", str(token_file)]),
        sha256=gpu.SERVER_SHA256,
        artifacts=((gpu.MODEL, gpu.MODEL_SHA256), (gpu.COMPAT_LIBRARY, gpu.COMPAT_SHA256),
                   *libraries),
        environment={"LD_LIBRARY_PATH": "/usr/local/cuda-13.0/compat:/usr/local/cuda-13.0/lib64",
                     "CUDA_VISIBLE_DEVICES": "0"},
        stdio="log", log_path=log)


async def _proof(proc, _host_pid: int, command: PinnedCommand) -> bool | None:
    try:
        listener = await asyncio.to_thread(gpu.listener_pid)
        if listener is not None and listener != proc.pid:
            return False
        if listener is None or not await asyncio.to_thread(gpu._full_offload, command.log_path):
            return None
        return True
    except (OSError, RuntimeError, ValueError):
        return False


async def _work(proc, cancel: asyncio.Event, deadline: float,
                *, text: str, target: str, command: PinnedCommand,
                token: str) -> str:
    while time.monotonic() < deadline and not cancel.is_set() and proc.poll() is None:
        listener = await asyncio.to_thread(gpu.listener_pid)
        if listener is not None and listener != proc.pid:
            raise StageFailure("GPU_OWNER", poison=True)
        if listener == proc.pid and await asyncio.to_thread(gpu._full_offload, command.log_path):
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
        runtime = TranslationRuntime(diagnostic_settings(token=token, token_file=None), backend)
        result = await runtime._translate_inner(text, target, deadline)
        try:
            result.encode("utf-8")
        except UnicodeError:
            raise ServiceFailure(502, "BAD_UPSTREAM") from None
        return result


class OnDemandMtRuntime:
    def __init__(self, settings, queue: GpuStageQueue,
                 owner_factory: Callable[[], GpuStageOwner], *, retry_seconds: float = .1):
        self.settings = settings
        self.queue = queue
        self.owner_factory = owner_factory
        self.retry_seconds = retry_seconds

    async def translate(self, text: str, source_lang: str, target_lang: str,
                        *, deadline: float, disconnected: asyncio.Event | None = None) -> str:
        async def stage(cancel: asyncio.Event) -> str:
            owner = self.owner_factory()
            locked = False
            try:
                while not cancel.is_set() and time.monotonic() < deadline:
                    try:
                        await owner.start()
                        locked = True
                        break
                    except BlockingIOError:
                        await asyncio.sleep(min(self.retry_seconds,
                                                max(0, deadline - time.monotonic())))
                if not locked:
                    raise StageFailure("TIMEOUT", pre_admission=True)
                if cancel.is_set() or time.monotonic() >= deadline:
                    raise StageFailure("TIMEOUT", pre_admission=True)
                command = _command(self.settings.token_file)

                async def work(proc, active_cancel, until):
                    return await _work(proc, active_cancel, until, text=text,
                                       target=target_lang, command=command,
                                       token=self.settings.token)

                async def proof(proc, host_pid):
                    return await _proof(proc, host_pid, command)

                return await owner.run(command, work, proof, deadline=deadline, cancel=cancel)
            finally:
                if locked:
                    try:
                        closed = await owner.close()
                    except Exception:
                        closed = False
                    if not closed:
                        raise StageFailure("GPU_CLEANUP", poison=True)

        try:
            return await self.queue.submit("mt", stage, deadline=deadline,
                                           disconnected=disconnected)
        except StageFailure as exc:
            status = 429 if exc.code == "BUSY" else 504 if exc.code == "TIMEOUT" else 503
            raise ServiceFailure(status, exc.code) from None
        except ServiceFailure:
            raise
        except Exception:
            raise ServiceFailure(503, "UNAVAILABLE") from None


def create_mt_stage_app():
    """Uvicorn --factory entrypoint; explicit flag and private token required."""
    if os.getenv("GPU_MT_ON_DEMAND_ENABLED") != "1":
        raise RuntimeError("on-demand GPU MT disabled")
    token_file = Path(os.environ["GPU_STAGE_MT_TOKEN_FILE"])
    if token_file.stat().st_mode & 0o077:
        raise ValueError("token file must be private")
    token = token_file.read_text(encoding="ascii").removesuffix("\n")
    if not re.fullmatch(r"[A-Za-z0-9_-]{32,256}", token):
        raise ValueError("invalid private token")
    settings = diagnostic_settings(token=token, token_file=token_file)

    def owner_factory() -> GpuStageOwner:
        return GpuStageOwner(gpu.LOCK, GpuBudget(gpu.GPU_UUID, gpu.EXPECTED_INCUMBENTS),
                             NvidiaSmiObserver())

    runtime = OnDemandMtRuntime(settings, GpuStageQueue(max_pending=2), owner_factory)
    return create_app(settings, runtime)
