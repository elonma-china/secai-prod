"""Private on-demand MT stage sharing the existing OCR child's GPU flock.

This factory has no OCR route and starts no model at import or ASGI startup.
The lock is claimed only while a translated request owns a disposable child.
"""
from __future__ import annotations

import asyncio
import os
import logging
import re
import time
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import httpx

from deploy.mt_cpu_service.service import ServiceFailure, TranslationRuntime, create_app
from evals.multilang import gpu_specialist_server as gpu

from .coordinator import GpuBudget, GpuStageQueue, StageFailure
from .mt_diagnostic import GuardedBackend, diagnostic_settings
from .owner import GpuStageOwner, NvidiaSmiObserver, PinnedCommand

logger = logging.getLogger(__name__)
_WARM_POLL = .1
_WARM_END_MARGIN = 1.0


def _env_seconds(name: str, default: float, *, ceiling: float) -> float:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = float(raw)
    except ValueError:
        return default  # Bad config falls back to the safe default, never longer.
    return min(max(value, 0.0), ceiling) if value == value else default


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


@dataclass
class _Job:
    text: str
    target: str
    deadline: float
    future: asyncio.Future
    cancel: asyncio.Event = field(default_factory=asyncio.Event)


class _Lease:
    """One warm child. Jobs join while it is open; it closes before teardown."""

    def __init__(self, admit_deadline: float):
        self.admit_deadline = admit_deadline
        self.jobs: list[_Job] = []
        self.open = True
        self.started = False  # lock held and owner.run entered
        self.release = False  # early-release request: finish the current job, then close
        self.abort = asyncio.Event()  # all jobs gave up before the lock was held
        self.wake = asyncio.Event()
        self.task: asyncio.Task | None = None

    def kick(self) -> None:
        self.wake.set()


class OnDemandMtRuntime:
    def __init__(self, settings, queue: GpuStageQueue,
                 owner_factory: Callable[[], GpuStageOwner], *, retry_seconds: float = .1,
                 warm_idle_seconds: float | None = None,
                 warm_max_seconds: float | None = None):
        self.settings = settings
        self.queue = queue
        self.owner_factory = owner_factory
        self.retry_seconds = retry_seconds
        # 0 (default) keeps the original spawn-per-request behaviour.
        self.warm_idle = (_env_seconds("GPU_MT_WARM_IDLE_SECONDS", 0.0, ceiling=120.0)
                          if warm_idle_seconds is None else max(0.0, warm_idle_seconds))
        self.warm_max = (_env_seconds("GPU_MT_WARM_MAX_SECONDS", 600.0, ceiling=1800.0)
                         if warm_max_seconds is None else max(1.0, warm_max_seconds))
        self._lease: _Lease | None = None
        self._inflight = 0

    async def aclose(self) -> None:
        """Shutdown: stop accepting work and let the lease tear its child down."""
        lease = self._lease
        if lease is None:
            return
        lease.open = False
        lease.release = True
        lease.kick()
        if lease.task is not None:
            with suppress(asyncio.CancelledError, Exception):
                await asyncio.wait_for(asyncio.shield(lease.task), timeout=30)

    def request_release(self) -> None:
        """Ask the warm lease to drop the GPU lock as soon as it is idle."""
        lease = self._lease
        if lease is not None:
            lease.release = True
            lease.kick()

    @staticmethod
    def _service_failure(exc: BaseException) -> ServiceFailure:
        if isinstance(exc, ServiceFailure):
            return exc
        if isinstance(exc, StageFailure):
            status = 429 if exc.code == "BUSY" else 504 if exc.code == "TIMEOUT" else 503
            return ServiceFailure(status, exc.code)
        return ServiceFailure(503, "UNAVAILABLE")

    async def translate(self, text: str, source_lang: str, target_lang: str,
                        *, deadline: float, disconnected: asyncio.Event | None = None) -> str:
        if self.warm_idle > 0:
            return await self._translate_warm(text, target_lang, deadline, disconnected)
        return await self._translate_cold(text, target_lang, deadline, disconnected)

    async def _translate_warm(self, text: str, target_lang: str, deadline: float,
                              disconnected: asyncio.Event | None) -> str:
        if self.queue.poisoned:
            raise ServiceFailure(503, "POISONED")
        if deadline <= time.monotonic():
            raise ServiceFailure(504, "TIMEOUT")
        if self._inflight >= self.queue.max_pending:
            raise ServiceFailure(429, "BUSY")
        loop = asyncio.get_running_loop()
        job = _Job(text, target_lang, deadline, loop.create_future())
        job.future.add_done_callback(lambda f: f.cancelled() or f.exception())
        lease = self._lease
        if lease is None or not lease.open or lease.release:
            lease = _Lease(deadline)
            self._lease = lease
            lease.jobs.append(job)
            lease.task = asyncio.create_task(self._run_lease(lease, deadline))
        else:
            lease.jobs.append(job)
            lease.kick()
        self._inflight += 1
        waiter = (asyncio.create_task(disconnected.wait()) if disconnected is not None else None)
        try:
            pending = {job.future} | ({waiter} if waiter else set())
            done, _ = await asyncio.wait(pending, timeout=max(0, deadline - time.monotonic()),
                                         return_when=asyncio.FIRST_COMPLETED)
            if job.future in done:
                return job.future.result()
            gone = disconnected is not None and disconnected.is_set()
            raise ServiceFailure(503 if gone else 504, "DISCONNECTED" if gone else "TIMEOUT")
        finally:
            self._inflight -= 1
            if waiter is not None:
                waiter.cancel()
                with suppress(asyncio.CancelledError):
                    await waiter
            if not job.future.done():
                # Running job: _serve sees job.cancel and tears the child down.
                job.cancel.set()
                with suppress(ValueError):
                    lease.jobs.remove(job)
                if not lease.started and not any(not j.future.done() for j in lease.jobs):
                    lease.abort.set()
                lease.kick()

    async def _run_lease(self, lease: _Lease, first_deadline: float) -> None:
        error: BaseException | None = None
        try:
            await self.queue.submit(
                "mt", lambda cancel: self._lease_stage(lease, cancel),
                deadline=first_deadline + self.warm_max, disconnected=lease.abort)
        except BaseException as exc:  # noqa: BLE001 - mapped to private codes below
            error = exc
        finally:
            lease.open = False
            if self._lease is lease:
                self._lease = None
        failure = self._service_failure(error) if error is not None else None
        for job in lease.jobs:
            if not job.future.done():
                job.future.set_exception(failure or ServiceFailure(503, "UNAVAILABLE"))
        if error is not None and not isinstance(error, asyncio.CancelledError):
            logger.warning("mt warm lease ended code=%s", getattr(error, "code", "ERROR"))

    async def _lease_stage(self, lease: _Lease, cancel: asyncio.Event) -> None:
        owner = self.owner_factory()
        locked = False
        try:
            while not cancel.is_set() and time.monotonic() < lease.admit_deadline:
                try:
                    await owner.start()
                    locked = True
                    break
                except BlockingIOError:
                    await asyncio.sleep(min(self.retry_seconds,
                                            max(0, lease.admit_deadline - time.monotonic())))
            if not locked or cancel.is_set() or time.monotonic() >= lease.admit_deadline:
                raise StageFailure("TIMEOUT", pre_admission=True)
            lease.started = True
            command = _command(self.settings.token_file)
            until = time.monotonic() + self.warm_max

            async def work(proc, active_cancel, stop):
                return await self._serve(lease, proc, active_cancel, stop, command)

            async def proof(proc, host_pid):
                return await _proof(proc, host_pid, command)

            await owner.run(command, work, proof, deadline=until, cancel=cancel)
        finally:
            lease.open = False  # No joiner may attach once teardown starts.
            if locked:
                try:
                    closed = await owner.close()
                except Exception:
                    closed = False
                if not closed:
                    raise StageFailure("GPU_CLEANUP", poison=True)

    async def _next_job(self, lease: _Lease, cancel: asyncio.Event, stop: float) -> _Job | None:
        idle_end = time.monotonic() + self.warm_idle
        while True:
            while lease.jobs:
                job = lease.jobs.pop(0)
                if not job.future.done() and not job.cancel.is_set():
                    return job
            others_waiting = self.queue.pending > 1
            if (cancel.is_set() or lease.release or others_waiting or
                    time.monotonic() >= min(idle_end, stop - _WARM_END_MARGIN)):
                lease.open = False
                return None
            lease.wake.clear()
            try:
                await asyncio.wait_for(lease.wake.wait(), timeout=_WARM_POLL)
            except asyncio.TimeoutError:
                pass

    async def _serve(self, lease: _Lease, proc, cancel: asyncio.Event, stop: float,
                     command: PinnedCommand) -> None:
        while True:
            job = await self._next_job(lease, cancel, stop)
            if job is None:
                return None
            if proc.poll() is not None:
                exc = ServiceFailure(503, "UNAVAILABLE")
                job.future.set_exception(exc)
                raise StageFailure("CHILD_EXITED")
            task = asyncio.create_task(_work(
                proc, job.cancel, min(job.deadline, stop), text=job.text, target=job.target,
                command=command, token=self.settings.token))
            stoppers = [asyncio.create_task(job.cancel.wait()), asyncio.create_task(cancel.wait())]
            try:
                await asyncio.wait({task, *stoppers}, timeout=max(0, min(job.deadline, stop)
                                                                  - time.monotonic()),
                                   return_when=asyncio.FIRST_COMPLETED)
                if not task.done():
                    # Remote generation may still be running: no reuse, kill child.
                    raise StageFailure("TIMEOUT")
                result = task.result()
            except BaseException as exc:
                if not job.future.done():
                    job.future.set_exception(self._service_failure(exc))
                raise
            finally:
                for pending in (task, *stoppers):
                    if not pending.done():
                        pending.cancel()
                        with suppress(asyncio.CancelledError, Exception):
                            await pending
            if not job.future.done():
                job.future.set_result(result)

    async def _translate_cold(self, text: str, target_lang: str, deadline: float,
                              disconnected: asyncio.Event | None) -> str:
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
    app = create_app(settings, runtime)
    inner = app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(application):
        async with inner(application):
            try:
                yield
            finally:
                await runtime.aclose()  # warm lease: kill child, free the GPU lock

    app.router.lifespan_context = lifespan
    return app
