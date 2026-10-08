"""Transient pre-load GPU shortage must be a non-poisoning, pre-admission refusal.

CPU-only: tiny sleeping child, scripted GPU observer, no model, no GPU.
A free-memory shortfall measured BEFORE any child exists proves nothing about
our own cleanup, so it must self-recover; every other failure stays poisoning.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import sys
import time
from pathlib import Path

import pytest

from deploy.gpu_stage import mt_on_demand
from deploy.gpu_stage.coordinator import GpuBudget, GpuSample, GpuStageQueue, StageFailure
from deploy.gpu_stage.mt_diagnostic import diagnostic_settings
from deploy.gpu_stage.mt_on_demand import OnDemandMtRuntime
from deploy.gpu_stage.owner import GpuStageOwner, PinnedCommand
from deploy.mt_cpu_service.service import ServiceFailure

UUID = "GPU-test"
FULL_RESERVE = 2048 + 6144  # min_free_mib + max_owned_mib defaults
INCUMBENTS = frozenset({11, 22})


def _budget() -> GpuBudget:
    return GpuBudget("GPU-pinned", INCUMBENTS)



# --------------------------------------------------------------------------- budget


def test_preload_shortfall_is_transient_pre_admission_and_non_poisoning():
    budget = _budget()
    for free in (0, 1000, FULL_RESERVE - 1):
        with pytest.raises(StageFailure) as exc:
            budget.check_preload(GpuSample("GPU-pinned", free, {11: 4000, 22: 1000}))
        assert exc.value.code == "GPU_RESERVE"
        assert exc.value.poison is False
        assert exc.value.pre_admission is True
    # Exactly the full reserve is admitted.
    budget.check_preload(GpuSample("GPU-pinned", FULL_RESERVE, {11: 4000, 22: 1000}))


def test_preload_still_poisons_for_unexpected_extra_process():
    budget = _budget()
    with pytest.raises(StageFailure) as exc:
        budget.check_preload(GpuSample("GPU-pinned", 9000, {11: 4000, 22: 1000, 33: 100}))
    assert exc.value.code == "GPU_OWNER" and exc.value.poison is True
    # Foreign process wins over a shortfall: still poisoning GPU_OWNER.
    with pytest.raises(StageFailure) as exc:
        budget.check_preload(GpuSample("GPU-pinned", 100, {11: 4000, 22: 1000, 33: 100}))
    assert exc.value.code == "GPU_OWNER" and exc.value.poison is True


def test_preload_still_poisons_for_missing_incumbent_or_changed_gpu():
    budget = _budget()
    for sample in (GpuSample("GPU-pinned", 9000, {11: 4000}),
                   GpuSample("GPU-pinned", 100, {11: 4000}),  # shortfall + missing incumbent
                   GpuSample("GPU-changed", 9000, {11: 4000, 22: 1000})):
        with pytest.raises(StageFailure) as exc:
            budget.check_preload(sample)
        assert exc.value.code == "GPU_OWNER" and exc.value.poison is True


def test_running_over_budget_and_running_reaped_shortfalls_remain_poisoning():
    budget = _budget()
    with pytest.raises(StageFailure) as exc:
        budget.check_running(GpuSample("GPU-pinned", 3000, {11: 4000, 22: 1000, 33: 6145}), 33)
    assert exc.value.code == "GPU_BUDGET" and exc.value.poison is True
    with pytest.raises(StageFailure) as exc:
        budget.check_running(GpuSample("GPU-pinned", 1000, {11: 4000, 22: 1000}), None)
    assert exc.value.code == "GPU_RESERVE" and exc.value.poison is True
    with pytest.raises(StageFailure) as exc:
        budget.check_reaped(GpuSample("GPU-pinned", 1000, {11: 4000, 22: 1000}))
    assert exc.value.code == "GPU_RESERVE" and exc.value.poison is True


# --------------------------------------------------------------------------- queue


@pytest.mark.asyncio
async def test_queue_does_not_latch_on_preload_shortage_and_recovers():
    queue = GpuStageQueue(max_pending=1)
    budget = _budget()
    free = {"mib": 1000}

    async def stage(_cancel):
        budget.check_preload(GpuSample("GPU-pinned", free["mib"], {11: 4000, 22: 1000}))
        return "ok"

    with pytest.raises(StageFailure) as exc:
        await queue.submit("mt", stage, deadline=time.monotonic() + 1)
    assert exc.value.code == "GPU_RESERVE" and exc.value.poison is False
    assert queue.poisoned is False
    assert queue.pending == 0

    free["mib"] = 9000  # memory came back: the very next stage succeeds
    assert await queue.submit("mt", stage, deadline=time.monotonic() + 1) == "ok"
    assert queue.poisoned is False


@pytest.mark.asyncio
async def test_queue_still_latches_on_poisoning_failure():
    queue = GpuStageQueue(max_pending=1)
    budget = _budget()

    async def bad(_cancel):
        budget.check_preload(GpuSample("GPU-pinned", 9000, {11: 4000, 22: 1000, 33: 1}))

    async def never(_cancel):
        raise AssertionError("a poisoned queue must not admit another stage")

    with pytest.raises(StageFailure) as exc:
        await queue.submit("mt", bad, deadline=time.monotonic() + 1)
    assert exc.value.code == "GPU_OWNER" and exc.value.poison is True
    assert queue.poisoned is True
    with pytest.raises(StageFailure) as exc:
        await queue.submit("mt", never, deadline=time.monotonic() + 1)
    assert exc.value.code == "POISONED" and exc.value.pre_admission is True


@pytest.mark.asyncio
@pytest.mark.parametrize("poison", [True, False])
async def test_queue_logs_stage_failure_code_and_poison_without_request_text(caplog, poison):
    queue = GpuStageQueue(max_pending=1)
    secret = "SECRET-USER-DOCUMENT-TEXT-xyz"
    code = "GPU_OWNER" if poison else "GPU_RESERVE"

    async def failing(_cancel):
        # Make every channel a careless logger could use carry the request text:
        # exception args, str(), and the chained cause.
        failure = StageFailure(code, poison=poison)
        failure.args = (code, secret)
        raise failure from ValueError(secret)

    with caplog.at_level(logging.DEBUG):
        with pytest.raises(StageFailure):
            await queue.submit("mt", failing, deadline=time.monotonic() + 1)
    warnings = [r for r in caplog.records
                if r.levelno == logging.WARNING and code in r.getMessage()]
    assert warnings, [r.getMessage() for r in caplog.records]
    message = warnings[0].getMessage().lower()
    assert "poison" in message and str(poison).lower() in message
    # Sanity: the secret really is reachable through the exception, so absence
    # below proves the logger chose not to emit it.
    with pytest.raises(StageFailure) as raised:
        await GpuStageQueue(max_pending=1).submit("mt", failing, deadline=time.monotonic() + 1)
    assert secret in str(raised.value.args) and secret in str(raised.value.__cause__)
    assert secret not in caplog.text
    for record in caplog.records:
        assert secret not in record.getMessage() and secret not in str(record.args)
        assert secret not in repr(record.__dict__.get("exc_text"))
        assert record.exc_info is None


# --------------------------------------------------------------------------- owner


def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _command(tmp_path: Path) -> PinnedCommand:
    script = tmp_path / "tiny_child.py"
    script.write_text("import time\ntime.sleep(30)\n", encoding="utf-8")
    return PinnedCommand(binary=Path(sys.executable), args=(str(script),),
                         sha256=_hash(Path(sys.executable)),
                         artifacts=((script, _hash(script)),))


class ScriptedObserver:
    """Settable free memory; the live child (if any) owns 100 MiB."""

    def __init__(self, free: int, preload_script: list[GpuSample] | None = None):
        self.free = free
        self.preload_samples = 0
        self.child_samples = 0
        self.preload_script = list(preload_script or [])

    def sample(self, expected_pid=None):
        if expected_pid is None:
            self.preload_samples += 1
            if self.preload_script:
                return self.preload_script.pop(0)
            return GpuSample(UUID, self.free, {})
        self.child_samples += 1
        if Path(f"/proc/{expected_pid}").exists():
            return GpuSample(UUID, self.free, {expected_pid: 100})
        return GpuSample(UUID, 9000, {})


async def _proof(proc, host_pid):
    return proc.pid == host_pid


@pytest.mark.asyncio
async def test_owner_preload_shortage_leaves_no_evidence_and_does_not_poison(tmp_path):
    observer = ScriptedObserver(free=1000)
    owner = GpuStageOwner(tmp_path / "owner.lock", GpuBudget(UUID, frozenset()), observer,
                          sample_interval=.02, cleanup_grace=.2)
    await owner.start()

    async def unreachable(proc, cancel, deadline):
        raise AssertionError("no child may start under a preload shortage")

    try:
        with pytest.raises(StageFailure) as exc:
            await owner.run(_command(tmp_path), unreachable, _proof,
                            deadline=time.monotonic() + 2, cancel=asyncio.Event())
        assert exc.value.code == "GPU_RESERVE"
        assert exc.value.poison is False and exc.value.pre_admission is True
        assert owner.poisoned is False
        assert not list((tmp_path / "evidence").glob("gpu-run-broker-*"))

        # Memory recovers: the same owner is not vetoed and completes a run.
        observer.free = 9000

        async def work(proc, cancel, deadline):
            await asyncio.sleep(.1)
            return "done"

        result = await owner.run(_command(tmp_path), work, _proof,
                                 deadline=time.monotonic() + 3, cancel=asyncio.Event())
        assert result == "done" and owner.poisoned is False
        summaries = list((tmp_path / "evidence").glob("gpu-run-broker-*/summary.json"))
        assert len(summaries) == 1
    finally:
        await owner.close()

    # A fresh owner is not vetoed by any history either.
    fresh = GpuStageOwner(tmp_path / "owner.lock", GpuBudget(UUID, frozenset()),
                          ScriptedObserver(9000))
    await fresh.start()
    await fresh.close()


@pytest.mark.asyncio
async def test_owner_foreign_process_preload_still_poisons(tmp_path):
    class Foreign(ScriptedObserver):
        def sample(self, expected_pid=None):
            if expected_pid is None:
                return GpuSample(UUID, 9000, {999: 10})  # unexpected extra process
            return super().sample(expected_pid)

    owner = GpuStageOwner(tmp_path / "owner.lock", GpuBudget(UUID, frozenset()),
                          Foreign(9000))
    await owner.start()
    try:
        async def unreachable(proc, cancel, deadline):
            raise AssertionError("must not start")

        with pytest.raises(StageFailure) as exc:
            await owner.run(_command(tmp_path), unreachable, _proof,
                            deadline=time.monotonic() + 2, cancel=asyncio.Event())
        assert exc.value.code == "GPU_OWNER" and exc.value.poison is True
    finally:
        await owner.close()


def _spy_command(tmp_path: Path, attests: list[str]) -> PinnedCommand:
    base = _command(tmp_path)

    class Spy(PinnedCommand):
        def attest(self) -> None:
            attests.append("attest")
            super().attest()

    return Spy(binary=base.binary, args=base.args, sha256=base.sha256,
               artifacts=base.artifacts)


async def _never_work(proc, cancel, deadline):
    raise AssertionError("no child may start")


@pytest.mark.asyncio
async def test_sustained_shortage_never_attests_and_creates_no_evidence(tmp_path):
    observer = ScriptedObserver(free=1000)
    attests: list[str] = []
    owner = GpuStageOwner(tmp_path / "owner.lock", GpuBudget(UUID, frozenset()), observer)
    await owner.start()
    try:
        with pytest.raises(StageFailure) as exc:
            await owner.run(_spy_command(tmp_path, attests), _never_work, _proof,
                            deadline=time.monotonic() + 2, cancel=asyncio.Event())
        assert exc.value.code == "GPU_RESERVE" and exc.value.poison is False
        assert exc.value.pre_admission is True
        assert attests == []  # cheap gate: no multi-GB hashing while VRAM is short
        assert observer.child_samples == 0 and not owner.poisoned
        assert not list((tmp_path / "evidence").glob("gpu-run-*"))
    finally:
        await owner.close()


@pytest.mark.asyncio
async def test_shortage_appearing_only_after_attest_is_still_non_poisoning(tmp_path):
    ok, short = GpuSample(UUID, 9000, {}), GpuSample(UUID, 1000, {})
    observer = ScriptedObserver(free=9000, preload_script=[ok, short])
    attests: list[str] = []
    owner = GpuStageOwner(tmp_path / "owner.lock", GpuBudget(UUID, frozenset()), observer)
    await owner.start()
    try:
        with pytest.raises(StageFailure) as exc:
            await owner.run(_spy_command(tmp_path, attests), _never_work, _proof,
                            deadline=time.monotonic() + 2, cancel=asyncio.Event())
        assert exc.value.code == "GPU_RESERVE" and exc.value.poison is False
        assert exc.value.pre_admission is True
        assert attests == ["attest"]  # the authoritative post-attest check caught it
        assert observer.child_samples == 0 and not owner.poisoned
        assert not list((tmp_path / "evidence").glob("gpu-run-*"))
    finally:
        await owner.close()


@pytest.mark.asyncio
async def test_foreign_process_on_early_sample_poisons_before_attest(tmp_path):
    foreign = GpuSample(UUID, 9000, {999: 10})
    observer = ScriptedObserver(free=9000, preload_script=[foreign])
    attests: list[str] = []
    owner = GpuStageOwner(tmp_path / "owner.lock", GpuBudget(UUID, frozenset()), observer)
    await owner.start()
    try:
        with pytest.raises(StageFailure) as exc:
            await owner.run(_spy_command(tmp_path, attests), _never_work, _proof,
                            deadline=time.monotonic() + 2, cancel=asyncio.Event())
        assert exc.value.code == "GPU_OWNER" and exc.value.poison is True
        assert attests == [] and observer.child_samples == 0
        assert not list((tmp_path / "evidence").glob("gpu-run-*"))
    finally:
        await owner.close()


# --------------------------------------------------------------------------- mt_on_demand


@pytest.mark.asyncio
async def test_mt_on_demand_preload_shortage_is_503_gpu_reserve_then_recovers(
        tmp_path, monkeypatch):
    observer = ScriptedObserver(free=1000)
    command = _command(tmp_path)
    monkeypatch.setattr(mt_on_demand, "_command", lambda _token_file: command)

    async def fake_work(proc, cancel, deadline, **_kw):
        await asyncio.sleep(.1)
        return "Bonjour"

    async def fake_proof(proc, host_pid, _command):
        return proc.pid == host_pid

    monkeypatch.setattr(mt_on_demand, "_work", fake_work)
    monkeypatch.setattr(mt_on_demand, "_proof", fake_proof)

    def owner_factory():
        return GpuStageOwner(tmp_path / "owner.lock", GpuBudget(UUID, frozenset()), observer,
                             sample_interval=.02, cleanup_grace=.2)

    queue = GpuStageQueue(max_pending=2)
    settings = diagnostic_settings(token="private-mt-test-token", token_file=None)
    runtime = OnDemandMtRuntime(settings, queue, owner_factory, retry_seconds=.01)

    spawned: list[object] = []
    original_spawn = GpuStageOwner._spawn

    def spy_spawn(self, cmd):
        child = original_spawn(self, cmd)
        spawned.append(child)
        return child

    monkeypatch.setattr(GpuStageOwner, "_spawn", spy_spawn)
    with pytest.raises(ServiceFailure) as error:
        await runtime.translate("Xin chào", "vi", "fr", deadline=time.monotonic() + 3)
    assert error.value.status == 503 and error.value.code == "GPU_RESERVE"
    assert spawned == [] and observer.child_samples == 0
    assert queue.poisoned is False
    assert not list((tmp_path / "evidence").glob("gpu-run-broker-*"))

    observer.free = 9000
    result = await runtime.translate("Xin chào", "vi", "fr", deadline=time.monotonic() + 5)
    assert result == "Bonjour"
    assert len(spawned) == 1 and queue.poisoned is False
