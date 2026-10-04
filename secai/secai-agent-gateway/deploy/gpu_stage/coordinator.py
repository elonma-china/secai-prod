"""CPU-only stage admission and GPU proof rules for one future broker owner.

The queue is local to the single broker process. A cross-process owner lock and
disposable child runner must wrap it before any GPU backend is enabled.
"""
from __future__ import annotations

import asyncio
import time
from contextlib import suppress
from dataclasses import dataclass
from typing import Awaitable, Callable, TypeVar

T = TypeVar("T")


class StageFailure(Exception):
    def __init__(self, code: str, *, pre_admission: bool = False, poison: bool = False):
        super().__init__(code)
        self.code = code
        self.pre_admission = pre_admission
        self.poison = poison


@dataclass(frozen=True)
class GpuSample:
    uuid: str
    free_mib: int
    processes: dict[int, int]  # GPU host PID -> used MiB on this UUID


@dataclass(frozen=True)
class GpuBudget:
    uuid: str
    incumbent_pids: frozenset[int]
    max_owned_mib: int = 6144
    min_free_mib: int = 2048

    def _owned(self, sample: GpuSample, owner_host_pid: int | None,
               *, before_load: bool = False) -> tuple[int, int | None]:
        if (sample.uuid != self.uuid or type(sample.free_mib) is not int or
                type(sample.processes) is not dict or
                any(type(pid) is not int or type(used) is not int or used < 0
                    for pid, used in sample.processes.items()) or
                not self.incumbent_pids <= sample.processes.keys()):
            raise StageFailure("GPU_OWNER", poison=True)
        extra = sample.processes.keys() - self.incumbent_pids
        if len(extra) > 1 or (before_load and extra):
            raise StageFailure("GPU_OWNER", poison=True)
        if owner_host_pid is not None and extra != {owner_host_pid}:
            raise StageFailure("GPU_OWNER", poison=True)
        discovered = owner_host_pid if owner_host_pid is not None else next(iter(extra), None)
        owned = sample.processes.get(discovered, 0) if discovered is not None else 0
        if owned > self.max_owned_mib:
            raise StageFailure("GPU_BUDGET", poison=True)
        return owned, discovered

    def check_preload(self, sample: GpuSample) -> None:
        self._owned(sample, None, before_load=True)
        if sample.free_mib < self.min_free_mib + self.max_owned_mib:
            raise StageFailure("GPU_RESERVE", poison=True)

    def check_running(self, sample: GpuSample,
                      owner_host_pid: int | None) -> tuple[int, int | None]:
        """No vanished-PID exception while the child is alive (OCR-R7)."""
        owned, discovered = self._owned(sample, owner_host_pid)
        if sample.free_mib < self.min_free_mib + max(0, self.max_owned_mib - owned):
            raise StageFailure("GPU_RESERVE", poison=True)
        return owned, discovered

    def check_reaped(self, sample: GpuSample) -> bool:
        """Call only after wait() confirms child death; retain slot during lag."""
        self._owned(sample, None, before_load=True)
        if sample.free_mib < self.min_free_mib:
            raise StageFailure("GPU_RESERVE", poison=True)
        return sample.free_mib >= self.min_free_mib + self.max_owned_mib


class GpuStageQueue:
    """Finite FIFO for OCR and MT stages sharing one future owned GPU runner.

    A timed-out or disconnected caller receives an error, while the internal
    operation keeps the slot until it finishes cleanup. The operation must
    observe the passed cancellation event and fail closed if cleanup is unproved.
    """

    def __init__(self, *, max_pending: int):
        if max_pending < 1:
            raise ValueError("max_pending must be positive")
        self.max_pending = max_pending
        self.pending = 0
        self.poisoned = False
        self._slot = asyncio.Lock()
        self._admission = asyncio.Lock()

    async def submit(self, stage: str, operation: Callable[[asyncio.Event], Awaitable[T]],
                     *, deadline: float, disconnected: asyncio.Event | None = None) -> T:
        if stage not in {"ocr", "mt"}:
            raise ValueError("unknown GPU stage")
        if deadline <= time.monotonic():
            raise StageFailure("TIMEOUT", pre_admission=True)
        async with self._admission:
            if self.poisoned:
                raise StageFailure("POISONED", pre_admission=True)
            if self.pending >= self.max_pending:
                raise StageFailure("BUSY", pre_admission=True)
            self.pending += 1
        cancel = asyncio.Event()
        admitted = asyncio.Event()
        worker = asyncio.create_task(self._run(operation, cancel, admitted, deadline))
        def consume_late(task: asyncio.Task) -> None:
            with suppress(asyncio.CancelledError):
                task.exception()
        worker.add_done_callback(consume_late)
        disconnect_wait = (asyncio.create_task(disconnected.wait())
                           if disconnected is not None else None)
        try:
            pending = {worker} | ({disconnect_wait} if disconnect_wait else set())
            done, _ = await asyncio.wait(pending, timeout=max(0, deadline - time.monotonic()),
                                          return_when=asyncio.FIRST_COMPLETED)
            if worker in done:
                return await worker
            cancel.set()
            if disconnected is not None and disconnected.is_set():
                raise StageFailure("DISCONNECTED", pre_admission=not admitted.is_set())
            raise StageFailure("TIMEOUT", pre_admission=not admitted.is_set())
        except asyncio.CancelledError:
            cancel.set()
            raise
        finally:
            if disconnect_wait is not None:
                disconnect_wait.cancel()
                with suppress(asyncio.CancelledError):
                    await disconnect_wait

    async def _run(self, operation: Callable[[asyncio.Event], Awaitable[T]],
                   cancel: asyncio.Event, admitted: asyncio.Event, deadline: float) -> T:
        acquired = False
        acquire = asyncio.create_task(self._slot.acquire())
        stopped = asyncio.create_task(cancel.wait())
        try:
            done, _ = await asyncio.wait({acquire, stopped},
                                          timeout=max(0, deadline - time.monotonic()),
                                          return_when=asyncio.FIRST_COMPLETED)
            if acquire in done and not acquire.cancelled():
                acquired = acquire.result()
            if stopped in done or not acquired or time.monotonic() >= deadline:
                raise StageFailure("TIMEOUT", pre_admission=True)
            if self.poisoned:
                raise StageFailure("POISONED", pre_admission=True)
            admitted.set()
            try:
                return await operation(cancel)
            except StageFailure as exc:
                if exc.poison:
                    self.poisoned = True
                raise
        finally:
            if not acquire.done():
                acquire.cancel()
                with suppress(asyncio.CancelledError):
                    await acquire
            elif not acquired and not acquire.cancelled() and acquire.exception() is None:
                # A disconnect can race with lock acquisition.
                self._slot.release()
            stopped.cancel()
            with suppress(asyncio.CancelledError):
                await stopped
            if acquired:
                self._slot.release()
            async with self._admission:
                self.pending -= 1
