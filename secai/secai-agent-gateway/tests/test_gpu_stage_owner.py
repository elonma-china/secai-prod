"""Tiny CPU child probes for stage ownership; never load a model or GPU."""
from __future__ import annotations

import asyncio
import hashlib
import os
import sys
import time
from pathlib import Path

import pytest

from deploy.gpu_stage.coordinator import GpuBudget, GpuSample, StageFailure
from deploy.gpu_stage.owner import GpuStageOwner, PinnedCommand


def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _command(tmp_path: Path) -> PinnedCommand:
    script = tmp_path / "tiny_child.py"
    script.write_text("import time\ntime.sleep(30)\n", encoding="utf-8")
    return PinnedCommand(binary=Path(sys.executable), args=(str(script),),
                         sha256=_hash(Path(sys.executable)),
                         artifacts=((script, _hash(script)),))


class FakeObserver:
    def __init__(self):
        self.samples = 0
        self.breach = False
        self.delay_cleanup = False
        self.host_pid_offset = 0

    def sample(self, expected_pid=None):
        alive = expected_pid is not None and Path(f"/proc/{expected_pid}").exists()
        if alive:
            self.samples += 1
            if self.breach and self.samples == 2:
                # PID vanished from GPU accounting while child still lives.
                return GpuSample("GPU-test", 1000, {})
            return GpuSample("GPU-test", 9000, {expected_pid + self.host_pid_offset: 100})
        return GpuSample("GPU-test", 4000 if self.delay_cleanup and expected_pid else 9000, {})


async def fake_proof(proc, host_pid):
    return proc.pid == host_pid  # CPU probe only; production uses host-PID proof.


@pytest.mark.asyncio
async def test_owned_stage_returns_only_after_child_reaped_and_reserve_verified(tmp_path):
    observer = FakeObserver()
    observer.host_pid_offset = 1000  # nvidia-smi host PID differs from container PID.
    owner = GpuStageOwner(tmp_path / "owner.lock", GpuBudget("GPU-test", frozenset()),
                          observer, sample_interval=.02, cleanup_grace=.2)
    await owner.start()
    spawned = []

    async def work(proc, cancel, deadline):
        spawned.append(proc.pid)
        await asyncio.sleep(.1)
        return {"translated_text": "ok"}

    async def proof(proc, host_pid):
        return host_pid == proc.pid + 1000

    try:
        result = await owner.run(_command(tmp_path), work, proof,
                                 deadline=time.monotonic() + 2,
                                 cancel=asyncio.Event())
        assert result == {"translated_text": "ok"}
        assert observer.samples >= 1 and spawned
        assert not Path(f"/proc/{spawned[0]}").exists()
        assert not owner.poisoned
        summaries = list((tmp_path / "evidence").glob("gpu-run-broker-*/summary.json"))
        assert len(summaries) == 1
        assert __import__("json").loads(summaries[0].read_text())["clean_exit_attested"] is True
    finally:
        await owner.close()


@pytest.mark.asyncio
async def test_incomplete_persistent_stage_vetoes_fresh_owner(tmp_path):
    evidence = tmp_path / "evidence" / "gpu-run-broker-crashed"
    evidence.mkdir(parents=True)
    owner = GpuStageOwner(tmp_path / "owner.lock", GpuBudget("GPU-test", frozenset()),
                          FakeObserver())
    with pytest.raises(StageFailure, match="POISONED"):
        await owner.start()
    assert owner._fd is None


@pytest.mark.asyncio
async def test_spawn_observes_durable_incomplete_stage_before_child(tmp_path):
    owner = GpuStageOwner(tmp_path / "owner.lock", GpuBudget("GPU-test", frozenset()),
                          FakeObserver(), sample_interval=.02, cleanup_grace=.2)
    await owner.start()
    observed = []
    original = owner._spawn

    def checked_spawn(command):
        records = list((tmp_path / "evidence").glob("gpu-run-broker-*"))
        observed.append(len(records) == 1 and not (records[0] / "summary.json").exists())
        return original(command)

    owner._spawn = checked_spawn
    try:
        await owner.run(_command(tmp_path), lambda *_: asyncio.sleep(.1, result="ok"),
                        fake_proof, deadline=time.monotonic() + 2, cancel=asyncio.Event())
        assert observed == [True]
    finally:
        await owner.close()


@pytest.mark.asyncio
async def test_observed_low_free_during_live_child_never_recovers_into_success(tmp_path):
    observer = FakeObserver()
    observer.breach = True
    owner = GpuStageOwner(tmp_path / "owner.lock", GpuBudget("GPU-test", frozenset()),
                          observer, sample_interval=.02, cleanup_grace=.2)
    await owner.start()
    spawned = []

    async def work(proc, cancel, deadline):
        spawned.append(proc.pid)
        await asyncio.sleep(.1)
        return "would be success"

    try:
        with pytest.raises(StageFailure, match="GPU_OWNER"):
            await owner.run(_command(tmp_path), work, fake_proof,
                            deadline=time.monotonic() + 2,
                            cancel=asyncio.Event())
        assert observer.samples >= 2
        assert spawned and not Path(f"/proc/{spawned[0]}").exists()
        assert owner.poisoned
    finally:
        await owner.close()


@pytest.mark.asyncio
async def test_unproved_post_reap_reserve_poison_blocks_another_spawn(tmp_path):
    observer = FakeObserver()
    observer.delay_cleanup = True
    owner = GpuStageOwner(tmp_path / "owner.lock", GpuBudget("GPU-test", frozenset()),
                          observer, sample_interval=.02, cleanup_grace=.08)
    await owner.start()

    async def work(proc, cancel, deadline):
        await asyncio.sleep(.05)
        return "done"

    try:
        with pytest.raises(StageFailure, match="GPU_CLEANUP"):
            await owner.run(_command(tmp_path), work, fake_proof,
                            deadline=time.monotonic() + 2,
                            cancel=asyncio.Event())
        assert owner.poisoned
        with pytest.raises(StageFailure, match="POISONED"):
            await owner.run(_command(tmp_path), work, fake_proof,
                            deadline=time.monotonic() + 2,
                            cancel=asyncio.Event())
        assert not await owner.close()
        incomplete = list((tmp_path / "evidence").glob("gpu-run-broker-*"))
        assert len(incomplete) == 1 and not (incomplete[0] / "summary.json").exists()
        # Simulate owner process death: its flock disappears, but the next
        # process must still reject the durable incomplete stage.
        os.close(owner._fd)
        owner._fd = None
        restarted = GpuStageOwner(tmp_path / "owner.lock", GpuBudget("GPU-test", frozenset()),
                                  FakeObserver())
        with pytest.raises(StageFailure, match="POISONED"):
            await restarted.start()
    finally:
        await owner.close()


@pytest.mark.asyncio
async def test_hash_mismatch_never_spawns(tmp_path):
    observer = FakeObserver()
    owner = GpuStageOwner(tmp_path / "owner.lock", GpuBudget("GPU-test", frozenset()),
                          observer)
    await owner.start()
    spec = _command(tmp_path)
    Path(spec.args[0]).write_text("changed", encoding="utf-8")

    async def unreachable(proc, cancel, deadline):
        raise AssertionError("model child started after hash mismatch")

    try:
        with pytest.raises(StageFailure, match="ARTIFACT"):
            await owner.run(spec, unreachable, fake_proof,
                            deadline=time.monotonic() + 2,
                            cancel=asyncio.Event())
        assert observer.samples == 0
    finally:
        await owner.close()
