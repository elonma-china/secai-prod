"""Nhiều-người-dùng-đồng-thời: concurrency cap + container naming (docker_exec.py) và việc
run_id được thread xuống tới lệnh docker thật (toolbox.py). Không cần docker thật — patch
`asyncio.create_subprocess_exec`."""
from __future__ import annotations

import asyncio
import re

import pytest

from secai_gateway.poc.runners import docker_exec as de
from secai_gateway.poc.runners import toolbox as tb
from secai_gateway.poc.runners.docker_exec import ExecConfig, _container_name, run_executor, run_raw


class _FakeProc:
    def __init__(self, out: bytes = b"ok", err: bytes = b"") -> None:
        self._out, self._err = out, err
        self.returncode = 0

    async def communicate(self):
        return self._out, self._err

    def kill(self) -> None:
        pass

    async def wait(self) -> None:
        pass


def _patch_subprocess(monkeypatch, tracker: dict):
    """Fake subprocess ghi lại số lệnh chạy đồng thời cao nhất quan sát được, và toàn bộ
    argv của mỗi lời gọi (để kiểm tra --name/threading run_id)."""
    tracker.setdefault("current", 0)
    tracker.setdefault("max_seen", 0)
    tracker.setdefault("calls", [])

    async def fake_exec(*argv, **kw):
        tracker["calls"].append(argv)
        tracker["current"] += 1
        tracker["max_seen"] = max(tracker["max_seen"], tracker["current"])
        await asyncio.sleep(0.05)   # đủ để các task khác có cơ hội chạy chồng lên nhau
        tracker["current"] -= 1
        return _FakeProc()

    monkeypatch.setattr(de.asyncio, "create_subprocess_exec", fake_exec)


@pytest.fixture(autouse=True)
def _reset_semaphore():
    """`_semaphore` là singleton module-level — reset giữa các test để kích cỡ test này
    không rò sang test khác."""
    de._sem, de._sem_size = None, None
    yield
    de._sem, de._sem_size = None, None


async def _run_n(n: int, cfg: ExecConfig):
    await asyncio.gather(*(run_executor(cfg, ["selftest"]) for _ in range(n)))


@pytest.mark.asyncio
async def test_concurrency_cap_binds_to_one(monkeypatch):
    """max_concurrent=1 → không bao giờ có 2 lệnh chạy chồng lên nhau, dù gọi đồng thời."""
    tracker: dict = {}
    _patch_subprocess(monkeypatch, tracker)
    cfg = ExecConfig(max_concurrent=1, timeout_s=5)
    await _run_n(5, cfg)
    assert tracker["max_seen"] == 1


@pytest.mark.asyncio
async def test_concurrency_cap_allows_parallelism_up_to_limit(monkeypatch):
    """max_concurrent=3 với 6 lệnh gọi đồng thời → được phép chạy chồng (không tự tuần tự hoá
    quá mức), nhưng không bao giờ vượt trần."""
    tracker: dict = {}
    _patch_subprocess(monkeypatch, tracker)
    cfg = ExecConfig(max_concurrent=3, timeout_s=5)
    await _run_n(6, cfg)
    assert 2 <= tracker["max_seen"] <= 3


def test_semaphore_singleton_reused_same_size():
    assert de._semaphore(4) is de._semaphore(4)


def test_semaphore_recreated_on_size_change():
    a = de._semaphore(4)
    b = de._semaphore(2)
    assert a is not b
    assert b._value == 2  # type: ignore[attr-defined]


def test_container_name_empty_without_run_id():
    assert _container_name("") == []


def test_container_name_unique_per_call_same_run_id():
    """Cùng 1 run_id gọi nhiều lệnh (multi-turn) vẫn ra tên container khác nhau — không đụng
    độ khi 2 lệnh của cùng 1 phiên chồng lấp thời gian."""
    a = _container_name("run-abc")
    b = _container_name("run-abc")
    assert a != b
    for flags in (a, b):
        assert flags[0] == "--name"
        assert re.match(r"^secai-run-abc-[0-9a-f]{8}$", flags[1])


@pytest.mark.asyncio
async def test_run_id_reaches_docker_command_for_verify_and_raw(monkeypatch):
    """run_id truyền vào run_executor/run_raw phải thực sự xuất hiện trong argv `docker run`
    (qua --name) — đây là cơ chế truy vết khi nhiều người dùng gọi đồng thời."""
    tracker: dict = {}
    _patch_subprocess(monkeypatch, tracker)
    cfg = ExecConfig(timeout_s=5)
    await run_executor(cfg, ["nuclei"], run_id="user42-verify")
    await run_raw(cfg, argv=["id"], run_id="user42-exec", privileged=False)
    joined = [" ".join(c) for c in tracker["calls"]]
    assert any("secai-user42-verify-" in c for c in joined)
    assert any("secai-user42-exec-" in c for c in joined)


@pytest.mark.asyncio
async def test_toolbox_run_tool_threads_run_id(monkeypatch):
    """run_tool(...) (đường /ai/tool và tool poc_scan) phải truyền run_id xuống tới lệnh
    docker thật cho searchsploit (không cần target/authz)."""
    tracker: dict = {}
    _patch_subprocess(monkeypatch, tracker)

    class _S:
        verify_enabled = True
        verify_allow_all = True
        verify_targets: list[str] = []

    await tb.run_tool(_S(), "searchsploit", {"query": "log4j"}, run_id="user7-scan")
    joined = [" ".join(c) for c in tracker["calls"]]
    assert any("secai-user7-scan-" in c for c in joined)
