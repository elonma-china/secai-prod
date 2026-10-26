"""Owned disposable GPU-stage process, with CPU-testable resource proof.

This module has no model configuration or route. Importing it never starts a
process; an explicit, hash-pinned command is needed for each stage.
"""
from __future__ import annotations

import asyncio
import fcntl
import hashlib
import json
import os
import subprocess
import sys
import time
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import Awaitable, Callable, Literal, Protocol, TypeVar

from .coordinator import GpuBudget, GpuSample, StageFailure

T = TypeVar("T")
_CHILD_SUPERVISOR = Path(__file__).parents[1] / "mt_cpu_service" / "child_supervisor.py"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while block := source.read(4 * 1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


_DIGEST_CACHE: dict[Path, tuple[tuple[int, int, int, int], str]] = {}


def _cached_sha256(path: Path) -> str:
    """Digest memoized per process; any size/mtime/inode/ctime change re-hashes.

    The first call for a file always hashes it in full. The key is taken before
    hashing, so a file modified mid-read stores a stale key that cannot match
    the next stat and is re-hashed.
    """
    st = path.stat()
    key = (st.st_size, st.st_mtime_ns, st.st_ino, st.st_ctime_ns)
    hit = _DIGEST_CACHE.get(path)
    if hit is not None and hit[0] == key:
        return hit[1]
    digest = _sha256(path)
    _DIGEST_CACHE[path] = (key, digest)
    return digest


@dataclass(frozen=True)
class PinnedCommand:
    binary: Path
    args: tuple[str, ...]
    sha256: str
    artifacts: tuple[tuple[Path, str], ...]
    environment: dict[str, str] = field(default_factory=dict)
    stdio: Literal["null", "pipes", "log"] = "null"
    log_path: Path | None = None

    def attest(self) -> None:
        if (not self.binary.is_absolute() or not self.binary.is_file() or
                not os.access(self.binary, os.X_OK) or
                not self.sha256 or _cached_sha256(self.binary) != self.sha256 or
                not self.artifacts or any(not path.is_absolute() or not path.is_file() or
                                          not expected or _cached_sha256(path) != expected
                                          for path, expected in self.artifacts)):
            raise StageFailure("ARTIFACT", pre_admission=True)


class GpuObserver(Protocol):
    def sample(self, expected_pid: int | None = None) -> GpuSample: ...


class NvidiaSmiObserver:
    """Read one device's UUID, free memory and GPU host PIDs without CUDA."""

    def sample(self, expected_pid: int | None = None) -> GpuSample:
        def query(*args: str) -> str:
            return subprocess.check_output(["nvidia-smi", *args], text=True, timeout=3).strip()
        try:
            gpu = query("--query-gpu=uuid,memory.free", "--format=csv,noheader,nounits").splitlines()
            if len(gpu) != 1:
                raise ValueError("ambiguous GPU")
            uuid, free = (part.strip() for part in gpu[0].split(","))
            if not uuid or not free.isdecimal():
                raise ValueError("invalid GPU sample")
            processes: dict[int, int] = {}
            rows = query("--query-compute-apps=gpu_uuid,pid,used_gpu_memory",
                         "--format=csv,noheader,nounits")
            for line in rows.splitlines():
                parts = [part.strip() for part in line.split(",")]
                if (len(parts) != 3 or parts[0] != uuid or not parts[1].isdecimal()
                        or not parts[2].isdecimal() or int(parts[1]) in processes):
                    raise ValueError("invalid GPU process sample")
                processes[int(parts[1])] = int(parts[2])
            return GpuSample(uuid, int(free), processes)
        except (OSError, subprocess.SubprocessError, ValueError) as exc:
            raise StageFailure("GPU_SAMPLE", poison=True) from exc


class GpuStageOwner:
    """One broker lock and one child; no GPU stage can outlive its reservation."""

    def __init__(self, lock_path: Path, budget: GpuBudget, observer: GpuObserver,
                 *, sample_interval: float = .25, cleanup_grace: float = 10.0):
        if sample_interval <= 0 or cleanup_grace <= 0:
            raise ValueError("finite sampling and cleanup bounds required")
        self.lock_path = lock_path
        self.budget = budget
        self.observer = observer
        self.sample_interval = sample_interval
        self.cleanup_grace = cleanup_grace
        self.poisoned = False
        self._fd: int | None = None
        self._busy = asyncio.Lock()

    @property
    def _evidence_root(self) -> Path:
        # The guarded Q4 launcher checks this same gpu-run-* namespace.
        return self.lock_path.parent / "evidence"

    def _check_history(self) -> None:
        if (self.lock_path.parent / "poisoned-cleanup.json").exists():
            raise StageFailure("POISONED", pre_admission=True)
        try:
            for run_dir in self._evidence_root.glob("gpu-run-*"):
                if not run_dir.is_dir():
                    raise ValueError("invalid stage record")
                summary = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
                if not isinstance(summary, dict) or summary.get("clean_exit_attested") is not True:
                    raise ValueError("unproved stage cleanup")
        except (OSError, ValueError) as exc:
            raise StageFailure("POISONED", pre_admission=True) from exc

    @staticmethod
    def _fsync_dir(path: Path) -> None:
        descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def _arm_record(self) -> Path:
        # No child may be spawned until its incomplete record is durable.
        self._evidence_root.mkdir(mode=0o700, parents=True, exist_ok=True)
        self._fsync_dir(self._evidence_root.parent)
        run_dir = self._evidence_root / f"gpu-run-broker-{time.time_ns()}-{os.getpid()}"
        run_dir.mkdir(mode=0o700)
        self._fsync_dir(self._evidence_root)
        return run_dir

    def _complete_record(self, run_dir: Path, child_pid: int) -> None:
        # Only call after wait() and a fresh full-reserve GPU sample succeed.
        path = run_dir / "summary.json"
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_CLOEXEC, 0o600)
        try:
            data = json.dumps({"clean_exit_attested": True, "stage": "broker",
                               "owner_container_pid": child_pid,
                               "gpu_uuid": self.budget.uuid}, sort_keys=True).encode() + b"\n"
            with os.fdopen(descriptor, "wb", closefd=False) as sink:
                sink.write(data)
                sink.flush()
                os.fsync(sink.fileno())
        finally:
            os.close(descriptor)
        self._fsync_dir(run_dir)

    async def start(self) -> None:
        if self._fd is not None:
            raise RuntimeError("GPU owner already started")
        self.lock_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd = os.open(self.lock_path, os.O_CREAT | os.O_RDWR | os.O_CLOEXEC, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self._check_history()
        except BaseException:
            os.close(fd)
            raise
        self._fd = fd

    async def close(self) -> bool:
        # An unproved stage remains locked for operator intervention. A new
        # process must still pass its own before-load GPU proof after a crash.
        if self.poisoned or self._busy.locked():
            return False
        if self._fd is not None:
            fcntl.flock(self._fd, fcntl.LOCK_UN)
            os.close(self._fd)
            self._fd = None
        return True

    def _spawn(self, command: PinnedCommand) -> subprocess.Popen:
        assert self._fd is not None
        if command.stdio == "log":
            if command.log_path is None or not command.log_path.is_absolute():
                raise StageFailure("ARTIFACT", pre_admission=True)
            command.log_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            log_fd = os.open(command.log_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                             0o600)
        else:
            log_fd = None
        try:
            return subprocess.Popen(
                [sys.executable, str(_CHILD_SUPERVISOR), str(os.getpid()),
                 str(command.binary), *command.args],
                stdin=subprocess.PIPE if command.stdio == "pipes" else subprocess.DEVNULL,
                stdout=(subprocess.PIPE if command.stdio == "pipes" else
                        log_fd if log_fd is not None else subprocess.DEVNULL),
                stderr=log_fd if log_fd is not None else subprocess.DEVNULL,
                close_fds=True, pass_fds=(self._fd,),
                env={"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "LC_ALL": "C",
                     **command.environment},
            )
        finally:
            if log_fd is not None:
                os.close(log_fd)

    async def _cleanup(self, child: subprocess.Popen) -> None:
        try:
            if child.poll() is None:
                with suppress(ProcessLookupError):
                    child.terminate()
                try:
                    await asyncio.to_thread(child.wait, timeout=3)
                except subprocess.TimeoutExpired:
                    with suppress(ProcessLookupError):
                        child.kill()
                    await asyncio.to_thread(child.wait, timeout=5)
            if child.poll() is None:
                raise StageFailure("GPU_CLEANUP", poison=True)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise StageFailure("GPU_CLEANUP", poison=True) from exc
        until = time.monotonic() + self.cleanup_grace
        while True:
            sample = await asyncio.to_thread(self.observer.sample, child.pid)
            if self.budget.check_reaped(sample):
                return
            if time.monotonic() >= until:
                raise StageFailure("GPU_CLEANUP", poison=True)
            await asyncio.sleep(self.sample_interval)

    async def _deadline_watch(self, child: subprocess.Popen, deadline: float) -> None:
        await asyncio.sleep(max(0, deadline - time.monotonic()))
        if child.poll() is None:
            with suppress(ProcessLookupError):
                child.kill()  # The exact Popen child, never a GPU host PID.

    async def run(self, command: PinnedCommand,
                  work: Callable[[subprocess.Popen, asyncio.Event, float], Awaitable[T]],
                  prove_owner: Callable[[subprocess.Popen, int], Awaitable[bool | None]],
                  *, deadline: float, cancel: asyncio.Event) -> T:
        if self._fd is None or self.poisoned:
            raise StageFailure("POISONED", pre_admission=True)
        if self._busy.locked():
            raise StageFailure("BUSY", pre_admission=True)
        async with self._busy:
            if deadline <= time.monotonic() or cancel.is_set():
                raise StageFailure("TIMEOUT", pre_admission=True)
            # Cheap gate first: attest hashes multi-GB files, so do not pay for
            # it while VRAM is short. The check after attest stays authoritative.
            self.budget.check_preload(await asyncio.to_thread(self.observer.sample, None))
            await asyncio.wait_for(asyncio.to_thread(command.attest),
                                   timeout=max(0, deadline - time.monotonic()))
            self.budget.check_preload(await asyncio.to_thread(self.observer.sample, None))
            if deadline <= time.monotonic() or cancel.is_set():
                raise StageFailure("TIMEOUT", pre_admission=True)
            try:
                record = self._arm_record()
                child = self._spawn(command)
            except BaseException as exc:
                self.poisoned = True
                raise StageFailure("GPU_CLEANUP", poison=True) from exc
            watchdog = asyncio.create_task(self._deadline_watch(child, deadline))
            task = asyncio.create_task(work(child, cancel, deadline))
            seen_owner = False
            owner_host_pid = None
            result: T | None = None
            error: BaseException | None = None
            try:
                while True:
                    if cancel.is_set() or time.monotonic() >= deadline:
                        raise StageFailure("TIMEOUT")
                    if child.poll() is not None and not task.done():
                        raise StageFailure("CHILD_EXITED")
                    if child.poll() is None:
                        sample = await asyncio.to_thread(self.observer.sample, child.pid)
                        owned, owner_host_pid = self.budget.check_running(sample, owner_host_pid)
                        if owned > 0 and owner_host_pid is not None and not seen_owner:
                            # nvidia-smi may report a host PID while Popen has
                            # a container PID. An independent stage-specific
                            # proof must bind the model child to that GPU PID.
                            verdict = await prove_owner(child, owner_host_pid)
                            if verdict is False:
                                raise StageFailure("GPU_OWNER", poison=True)
                            seen_owner = verdict is True
                    if task.done():
                        result = await task
                        break
                    await asyncio.sleep(self.sample_interval)
            except BaseException as exc:
                error = exc
                if isinstance(exc, StageFailure) and exc.poison:
                    self.poisoned = True
            finally:
                cancel.set()
                cleanup = asyncio.create_task(self._cleanup(child))
                while not cleanup.done():
                    try:
                        await asyncio.shield(cleanup)
                    except asyncio.CancelledError:
                        continue  # A disconnected caller cannot free the slot.
                    except Exception:
                        break
                if cleanup.cancelled() or cleanup.exception() is not None:
                    self.poisoned = True
                    error = StageFailure("GPU_CLEANUP", poison=True)
                watchdog.cancel()
                with suppress(asyncio.CancelledError):
                    await watchdog
                if not task.done():
                    task.cancel()
                    done, _ = await asyncio.wait({task}, timeout=.5)
                    if task not in done:
                        self.poisoned = True
                        error = StageFailure("GPU_CLEANUP", poison=True)
                    else:
                        with suppress(asyncio.CancelledError, Exception):
                            task.exception()
                if not self.poisoned:
                    try:
                        self._complete_record(record, child.pid)
                    except BaseException:
                        self.poisoned = True
                        error = StageFailure("GPU_CLEANUP", poison=True)
            if error is not None:
                raise error
            if not seen_owner:
                raise StageFailure("GPU_OWNER_UNVERIFIED")
            if time.monotonic() >= deadline:
                raise StageFailure("TIMEOUT")
            return result  # type: ignore[return-value]
