"""CPU-only contention and cleanup checks for on-demand GPU translation."""
from __future__ import annotations

import asyncio
import time

import httpx
import pytest

from deploy.gpu_stage.coordinator import GpuStageQueue
from deploy.gpu_stage.mt_diagnostic import diagnostic_settings
from deploy.gpu_stage.mt_on_demand import OnDemandMtRuntime
from deploy.mt_cpu_service.service import ServiceFailure, create_app


class FakeOwner:
    def __init__(self, *, busy_attempts: int = 0, close_result: bool = True):
        self.busy_attempts = busy_attempts
        self.close_result = close_result
        self.starts = self.runs = self.closes = 0

    async def start(self):
        self.starts += 1
        if self.starts <= self.busy_attempts:
            raise BlockingIOError("OCR holds shared lock")

    async def run(self, command, work, proof, *, deadline, cancel):
        self.runs += 1
        assert command.args[command.args.index("--ctx-size") + 1] == "4096"
        assert not cancel.is_set() and deadline > time.monotonic()
        return "Bonjour"

    async def close(self):
        self.closes += 1
        return self.close_result


def _runtime(owner, *, queue=None):
    settings = diagnostic_settings(token="private-mt-test-token", token_file=None)
    return OnDemandMtRuntime(settings, queue or GpuStageQueue(max_pending=2),
                             owner_factory=lambda: owner, retry_seconds=.01)


@pytest.mark.asyncio
async def test_waits_for_existing_ocr_lock_then_releases_after_mt_stage():
    owner = FakeOwner(busy_attempts=2)
    result = await _runtime(owner).translate("Xin chào", "vi", "fr",
                                             deadline=time.monotonic() + 2)
    assert result == "Bonjour"
    assert owner.starts == 3 and owner.runs == 1 and owner.closes == 1


@pytest.mark.asyncio
async def test_ocr_lock_wait_is_bounded_and_does_not_start_model():
    owner = FakeOwner(busy_attempts=1000)
    with pytest.raises(ServiceFailure) as error:
        await _runtime(owner).translate("Xin chào", "vi", "fr",
                                        deadline=time.monotonic() + .05)
    assert error.value.status == 504
    assert owner.runs == 0 and owner.closes == 0


@pytest.mark.asyncio
async def test_unproved_cleanup_poison_blocks_next_mt_stage():
    owner = FakeOwner(close_result=False)
    queue = GpuStageQueue(max_pending=2)
    runtime = _runtime(owner, queue=queue)
    with pytest.raises(ServiceFailure) as error:
        await runtime.translate("Xin chào", "vi", "fr", deadline=time.monotonic() + 2)
    assert error.value.status == 503 and queue.poisoned
    with pytest.raises(ServiceFailure):
        await runtime.translate("Chào", "vi", "fr", deadline=time.monotonic() + 2)
    assert owner.runs == 1


@pytest.mark.asyncio
async def test_child_error_is_private_unavailable_after_lock_release():
    class CrashingOwner(FakeOwner):
        async def run(self, *_args, **_kwargs):
            self.runs += 1
            raise RuntimeError("private model path")

    owner = CrashingOwner()
    with pytest.raises(ServiceFailure) as error:
        await _runtime(owner).translate("Xin chào", "vi", "fr",
                                        deadline=time.monotonic() + 2)
    assert error.value.status == 503 and error.value.code == "UNAVAILABLE"
    assert owner.closes == 1


@pytest.mark.asyncio
async def test_existing_private_translate_contract_uses_on_demand_owner():
    owner = FakeOwner()
    settings = diagnostic_settings(token="private-mt-test-token", token_file=None)
    app = create_app(settings, _runtime(owner))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://127.0.0.1") as client:
        response = await client.post("/v1/translate", json={
            "text": "Xin chào", "source_lang": "vi", "target_lang": "fr"},
            headers={"Authorization": "Bearer private-mt-test-token"})
    assert response.status_code == 200
    assert response.json() == {"translated_text": "Bonjour", "model": "tencent/Hy-MT2-7B"}
    assert owner.starts == owner.runs == owner.closes == 1
