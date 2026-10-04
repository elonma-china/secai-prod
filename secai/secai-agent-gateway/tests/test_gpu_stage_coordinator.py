"""CPU-only safety tests for the opt-in, shared GPU stage admission core."""
from __future__ import annotations

import asyncio
import time

import pytest

from deploy.gpu_stage.coordinator import GpuBudget, GpuSample, GpuStageQueue, StageFailure


def test_gpu_budget_requires_same_owner_and_continuous_reserve():
    budget = GpuBudget("GPU-pinned", frozenset({11, 22}), max_owned_mib=6144)
    baseline = GpuSample("GPU-pinned", 9000, {11: 4000, 22: 1000})
    budget.check_preload(baseline)
    assert budget.check_running(GpuSample("GPU-pinned", 8000, {11: 4000, 22: 1000,
                                                            33: 500}), None) == (500, 33)
    # The child is still alive: loss of its GPU PID gives no allocation credit.
    with pytest.raises(StageFailure, match="GPU_RESERVE"):
        budget.check_running(GpuSample("GPU-pinned", 1000, {11: 4000, 22: 1000}), None)
    with pytest.raises(StageFailure, match="GPU_RESERVE"):
        budget.check_running(GpuSample("GPU-pinned", 4000, {11: 4000, 22: 1000}), None)
    with pytest.raises(StageFailure, match="GPU_OWNER"):
        budget.check_running(GpuSample("GPU-pinned", 1000, {11: 4000, 22: 1000}), 33)
    with pytest.raises(StageFailure, match="GPU_OWNER"):
        budget.check_running(GpuSample("GPU-pinned", 9000, {11: 4000, 22: 1000,
                                                            44: 100}), 33)
    with pytest.raises(StageFailure, match="GPU_OWNER"):
        budget.check_running(GpuSample("GPU-changed", 9000, {11: 4000, 22: 1000}), 33)
    with pytest.raises(StageFailure, match="GPU_BUDGET"):
        budget.check_running(GpuSample("GPU-pinned", 3000, {11: 4000, 22: 1000,
                                                            33: 6145}), 33)
    # Only a proven dead child may enter cleanup lag; no success before 8192 MiB.
    assert not budget.check_reaped(GpuSample("GPU-pinned", 4000, {11: 4000, 22: 1000}))
    assert budget.check_reaped(baseline)
    with pytest.raises(StageFailure, match="GPU_RESERVE"):
        budget.check_reaped(GpuSample("GPU-pinned", 1000, {11: 4000, 22: 1000}))


@pytest.mark.asyncio
async def test_cancelled_waiter_never_interrupts_active_stage_and_fifo_survives():
    queue = GpuStageQueue(max_pending=2)
    active = asyncio.Event()
    finish = asyncio.Event()
    calls = []

    async def first(cancel):
        calls.append("ocr")
        active.set()
        await finish.wait()
        return "ocr-result"

    async def second(cancel):
        calls.append("cancelled-mt")
        return "bad"

    started = asyncio.create_task(queue.submit("ocr", first, deadline=time.monotonic() + 2))
    await asyncio.wait_for(active.wait(), 1)
    disconnected = asyncio.Event()
    waiting = asyncio.create_task(queue.submit("mt", second,
                                               deadline=time.monotonic() + 2,
                                               disconnected=disconnected))
    await asyncio.sleep(.02)
    assert queue.pending == 2
    disconnected.set()
    with pytest.raises(StageFailure) as exc:
        await waiting
    assert exc.value.code == "DISCONNECTED" and exc.value.pre_admission
    assert calls == ["ocr"] and not started.done()
    await asyncio.sleep(.02)
    assert queue.pending == 1
    finish.set()
    assert await started == "ocr-result"
    assert await queue.submit("mt", second, deadline=time.monotonic() + 1) == "bad"
    assert calls == ["ocr", "cancelled-mt"]


@pytest.mark.asyncio
async def test_deadline_does_not_free_slot_before_active_cleanup():
    queue = GpuStageQueue(max_pending=1)
    active = asyncio.Event()
    release = asyncio.Event()
    cancelled = asyncio.Event()

    async def slow(cancel):
        active.set()
        await cancel.wait()
        cancelled.set()
        await release.wait()  # stands in for child reap and GPU recovery
        return "too late"

    request = asyncio.create_task(queue.submit("mt", slow,
                                               deadline=time.monotonic() + .08))
    await asyncio.wait_for(active.wait(), 1)
    with pytest.raises(StageFailure) as exc:
        await request
    assert exc.value.code == "TIMEOUT" and not exc.value.pre_admission
    await asyncio.wait_for(cancelled.wait(), 1)
    with pytest.raises(StageFailure) as exc:
        await queue.submit("ocr", slow, deadline=time.monotonic() + 1)
    assert exc.value.code == "BUSY" and exc.value.pre_admission
    release.set()
    for _ in range(50):
        if queue.pending == 0:
            break
        await asyncio.sleep(.01)
    assert queue.pending == 0


@pytest.mark.asyncio
async def test_unproved_cleanup_poison_blocks_every_later_stage():
    queue = GpuStageQueue(max_pending=1)

    async def bad(_cancel):
        raise StageFailure("GPU_CLEANUP", poison=True)

    with pytest.raises(StageFailure, match="GPU_CLEANUP"):
        await queue.submit("ocr", bad, deadline=time.monotonic() + 1)
    assert queue.poisoned
    with pytest.raises(StageFailure, match="POISONED"):
        await queue.submit("mt", bad, deadline=time.monotonic() + 1)
