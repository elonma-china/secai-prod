"""CPU-only checks for the opt-in warm MT child lease."""
from __future__ import annotations

import asyncio
import time

import httpx
import pytest

from deploy.gpu_stage import mt_on_demand
from deploy.gpu_stage.coordinator import GpuStageQueue, StageFailure
from deploy.gpu_stage.mt_diagnostic import diagnostic_settings
from deploy.gpu_stage.mt_on_demand import OnDemandMtRuntime
from deploy.mt_cpu_service.service import ServiceFailure, create_app


class FakeProc:
    pid = 4242

    def __init__(self):
        self.dead = False

    def poll(self):
        return 1 if self.dead else None


class FakeOwner:
    spawns = 0
    closes = 0
    busy_attempts = 0

    def __init__(self, shared):
        self.shared = shared

    async def start(self):
        self.shared["starts"] += 1

    async def run(self, command, work, proof, *, deadline, cancel):
        self.shared["spawns"] += 1
        proc = FakeProc()
        self.shared["procs"].append(proc)
        try:
            return await work(proc, cancel, deadline)
        finally:
            proc.dead = True  # the real owner kills and reaps the child here

    async def close(self):
        self.shared["closes"] += 1
        return self.shared["close_result"]


def _runtime(shared, *, idle=.3, queue=None):
    settings = diagnostic_settings(token="private-mt-test-token", token_file=None)
    return OnDemandMtRuntime(settings, queue or GpuStageQueue(max_pending=2),
                             owner_factory=lambda: FakeOwner(shared), retry_seconds=.01,
                             warm_idle_seconds=idle, warm_max_seconds=30)


@pytest.fixture
def shared(monkeypatch):
    state = {"starts": 0, "spawns": 0, "closes": 0, "close_result": True, "procs": [],
             "work": 0, "fail": None, "delay": 0.0}

    async def fake_work(proc, cancel, deadline, *, text, target, command, token):
        state["work"] += 1
        assert proc.poll() is None
        if state["delay"]:
            await asyncio.sleep(state["delay"])
        if state["fail"] is not None:
            raise state["fail"]
        return f"{target}:{text}"

    monkeypatch.setattr(mt_on_demand, "_work", fake_work)
    return state


async def _call(runtime, text="Xin", wait=2):
    return await runtime.translate(text, "vi", "fr", deadline=time.monotonic() + wait)


async def test_child_reused_across_calls_then_torn_down_on_idle(shared):
    runtime = _runtime(shared)
    assert await _call(runtime, "a") == "fr:a"
    assert await _call(runtime, "b") == "fr:b"
    assert shared["spawns"] == 1 and shared["work"] == 2 and shared["closes"] == 0
    await asyncio.sleep(.7)  # idle window expires
    assert shared["closes"] == 1 and shared["procs"][0].dead
    assert runtime._lease is None
    assert await _call(runtime, "c") == "fr:c"
    assert shared["spawns"] == 2  # a fresh lease starts a fresh child
    await runtime.aclose()
    assert shared["closes"] == 2


async def test_default_zero_keeps_spawn_per_call(shared, monkeypatch):
    monkeypatch.delenv("GPU_MT_WARM_IDLE_SECONDS", raising=False)
    settings = diagnostic_settings(token="private-mt-test-token", token_file=None)
    runtime = OnDemandMtRuntime(settings, GpuStageQueue(max_pending=2),
                                owner_factory=lambda: FakeOwner(shared), retry_seconds=.01)
    assert runtime.warm_idle == 0

    async def fake_run(self, command, work, proof, *, deadline, cancel):
        shared["spawns"] += 1
        return "cold"
    monkeypatch.setattr(FakeOwner, "run", fake_run)
    assert await _call(runtime) == "cold" and await _call(runtime) == "cold"
    assert shared["spawns"] == 2 and shared["closes"] == 2 and shared["work"] == 0


def test_env_enables_and_bad_values_fall_back(monkeypatch):
    settings = diagnostic_settings(token="private-mt-test-token", token_file=None)
    make = lambda: OnDemandMtRuntime(settings, GpuStageQueue(max_pending=2), lambda: None)
    monkeypatch.setenv("GPU_MT_WARM_IDLE_SECONDS", "20")
    assert make().warm_idle == 20
    monkeypatch.setenv("GPU_MT_WARM_IDLE_SECONDS", "abc")
    assert make().warm_idle == 0
    monkeypatch.setenv("GPU_MT_WARM_IDLE_SECONDS", "-5")
    assert make().warm_idle == 0


async def test_failure_mid_lease_tears_down_and_poisons(shared):
    queue = GpuStageQueue(max_pending=2)
    runtime = _runtime(shared, queue=queue)
    assert await _call(runtime, "ok") == "fr:ok"
    shared["fail"] = StageFailure("GPU_OWNER", poison=True)
    with pytest.raises(ServiceFailure) as error:
        await _call(runtime, "bad")
    assert error.value.code == "GPU_OWNER" and error.value.status == 503
    await asyncio.sleep(.05)
    assert shared["spawns"] == 1 and shared["closes"] == 1 and shared["procs"][0].dead
    assert queue.poisoned
    with pytest.raises(ServiceFailure) as again:
        await _call(runtime, "next")
    assert again.value.code == "POISONED" and shared["spawns"] == 1


async def test_unproved_cleanup_at_idle_expiry_poisons(shared):
    queue = GpuStageQueue(max_pending=2)
    shared["close_result"] = False
    runtime = _runtime(shared, idle=.1, queue=queue)
    assert await _call(runtime) == "fr:Xin"
    await asyncio.sleep(.5)
    assert shared["closes"] == 1 and queue.poisoned
    with pytest.raises(ServiceFailure):
        await _call(runtime)
    assert shared["spawns"] == 1


async def test_job_timeout_kills_child_and_next_call_gets_fresh_one(shared):
    runtime = _runtime(shared)
    shared["delay"] = 5
    with pytest.raises(ServiceFailure) as error:
        await _call(runtime, wait=.3)
    assert error.value.status == 504
    await asyncio.sleep(.2)
    assert shared["closes"] == 1 and shared["procs"][0].dead
    shared["delay"] = 0
    assert await _call(runtime) == "fr:Xin"
    assert shared["spawns"] == 2
    await runtime.aclose()


async def test_busy_limit_and_concurrent_calls_share_one_child(shared):
    runtime = _runtime(shared)
    shared["delay"] = .2
    results = await asyncio.gather(_call(runtime, "a"), _call(runtime, "b"),
                                   _call(runtime, "c"), return_exceptions=True)
    busy = [r for r in results if isinstance(r, ServiceFailure)]
    assert len(busy) == 1 and busy[0].status == 429
    assert sorted(r for r in results if isinstance(r, str)) == ["fr:a", "fr:b"]
    assert shared["spawns"] == 1
    await runtime.aclose()


async def test_early_release_and_shutdown_free_the_lock(shared):
    runtime = _runtime(shared, idle=30)
    await _call(runtime)
    assert shared["closes"] == 0
    runtime.request_release()
    await asyncio.sleep(.3)
    assert shared["closes"] == 1 and shared["procs"][0].dead
    await _call(runtime)
    await runtime.aclose()  # shutdown path
    assert shared["closes"] == 2 and shared["procs"][1].dead


async def test_disconnect_before_lock_does_not_spawn(shared, monkeypatch):
    async def busy_start(self):
        raise BlockingIOError("OCR holds the lock")
    monkeypatch.setattr(FakeOwner, "start", busy_start)
    runtime = _runtime(shared)
    gone = asyncio.Event()
    task = asyncio.create_task(runtime.translate(
        "x", "vi", "fr", deadline=time.monotonic() + 5, disconnected=gone))
    await asyncio.sleep(.1)
    gone.set()
    with pytest.raises(ServiceFailure):
        await task
    await asyncio.sleep(.1)
    assert shared["spawns"] == 0 and runtime._lease is None


async def test_http_contract_and_lifespan_shutdown(shared):
    runtime = _runtime(shared)
    settings = diagnostic_settings(token="private-mt-test-token", token_file=None)
    app = create_app(settings, runtime)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://127.0.0.1") as client:
        for _ in range(2):
            response = await client.post("/v1/translate", json={
                "text": "Xin", "source_lang": "vi", "target_lang": "fr"},
                headers={"Authorization": "Bearer private-mt-test-token"})
            assert response.status_code == 200
    assert shared["spawns"] == 1
    await runtime.aclose()


# --- attest digest cache -------------------------------------------------

def _pinned(tmp_path):
    import hashlib
    from deploy.gpu_stage.owner import PinnedCommand
    binary = tmp_path / "server"
    binary.write_bytes(b"binary")
    binary.chmod(0o755)
    model = tmp_path / "model.gguf"
    model.write_bytes(b"model-bytes")
    h = lambda b: hashlib.sha256(b).hexdigest()
    return (PinnedCommand(binary=binary, args=(), sha256=h(b"binary"),
                          artifacts=((model, h(b"model-bytes")),)), binary, model)


def test_attest_cache_hits_then_misses_on_change(tmp_path, monkeypatch):
    from deploy.gpu_stage import owner
    owner._DIGEST_CACHE.clear()
    command, binary, model = _pinned(tmp_path)
    calls = []
    real = owner._sha256
    monkeypatch.setattr(owner, "_sha256", lambda p: calls.append(p) or real(p))
    command.attest()
    assert len(calls) == 2  # first spawn hashes everything
    command.attest()
    assert len(calls) == 2  # unchanged artifacts are not re-hashed
    model.write_bytes(b"tampered-bytes")  # size/mtime/ctime change
    with pytest.raises(StageFailure) as error:
        command.attest()
    assert error.value.code == "ARTIFACT" and len(calls) == 3
    model.write_bytes(b"model-bytes")  # restored content re-verifies by re-hash
    command.attest()
    assert len(calls) == 4


def test_attest_cache_misses_on_same_size_mtime_restore(tmp_path, monkeypatch):
    import os
    from deploy.gpu_stage import owner
    owner._DIGEST_CACHE.clear()
    command, binary, model = _pinned(tmp_path)
    command.attest()
    st = model.stat()
    model.write_bytes(b"MODEL-BYTES")  # same size
    os.utime(model, ns=(st.st_atime_ns, st.st_mtime_ns))  # mtime forged; ctime still moves
    with pytest.raises(StageFailure):
        command.attest()


async def test_warm_reuse_does_not_reattest(shared, tmp_path, monkeypatch):
    from deploy.gpu_stage import owner
    command, _b, _m = _pinned(tmp_path)
    attests = []
    monkeypatch.setattr(type(command), "attest", lambda self: attests.append(1))

    async def run(self, cmd, work, proof, *, deadline, cancel):
        await asyncio.to_thread(cmd.attest)  # real owner attests once per spawn
        shared["spawns"] += 1
        return await work(FakeProc(), cancel, deadline)
    monkeypatch.setattr(FakeOwner, "run", run)
    monkeypatch.setattr(mt_on_demand, "_command", lambda _t: command)
    runtime = _runtime(shared)
    await _call(runtime)
    await _call(runtime)
    assert len(attests) == 1 and shared["spawns"] == 1
    await runtime.aclose()
