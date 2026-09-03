"""run_lane1 driver: fake runner (JSON scripted) + monkeypatch tool. Kiểm success/dedup/parse-fail
và HỢP ĐỒNG SSE (chỉ yield step/log, không final)."""
from __future__ import annotations

import asyncio

import secai_gateway.poc.lane1 as lane1
from secai_gateway.poc.lane1 import Lane1Outcome, run_lane1
from secai_gateway.poc.lane1_tools import Observation


class _S:
    lane1_net = "host"
    verify_allow_all = True
    verify_enabled = True
    exec_enabled = True
    lane1_use_raw_cmd = True
    exec_privileged = False


class _Runner:
    def __init__(self, scripts):
        self.scripts = list(scripts)
        self.calls = 0
    async def complete(self, profile, prompt, session_id=None):
        self.calls += 1
        return (self.scripts.pop(0) if self.scripts else "no json here"), {}


def _fake_tool_factory(success_marker: str):
    async def fake_tool(settings, action, *, run_id, host, port, net="host", timeout_s=180):
        path = str((action.args or {}).get("path") or "")
        if success_marker and path == success_marker:
            return Observation(action.tool, True, "HTTP 200 GET /exploit\nuid=0(root) gid=0(root)",
                               {"status": 200}, body="uid=0(root) gid=0(root)")
        return Observation(action.tool, True, f"HTTP 404 {path}", {"status": 404}, body="not found")
    return fake_tool


def _run(gen):
    async def go():
        return [ev async for ev in gen]
    return asyncio.run(go())


def _outcome_and_events(scripts, success_marker, monkeypatch, max_steps=3):
    monkeypatch.setattr(lane1, "run_lane1_tool", _fake_tool_factory(success_marker))
    oc = Lane1Outcome()
    evs = _run(run_lane1(_S(), _Runner(scripts), cve="CVE-2026-63030", host="127.0.0.1", port="18054",
                         vuln_class="sqli-info-leak", success_signal="wp_users", command="id",
                         base_index=5, run_id="t", max_steps=max_steps, step_timeout_s=20,
                         total_timeout_s=120, outcome=oc))
    return oc, evs


def test_success_and_early_stop(monkeypatch):
    scripts = ['{"thought":"khai thác","tool":"http_request","args":{"path":"/exploit"},"done":false}',
               '{"thought":"thừa","tool":"http_request","args":{"path":"/other"},"done":false}']
    oc, evs = _outcome_and_events(scripts, "/exploit", monkeypatch)
    assert oc.verdict == "exploit_success" and oc.steps_to_success == 1
    # dừng sớm: action thứ 2 KHÔNG được chạy
    assert not any("/other" in (e.get("detail", "") + e.get("line", "")) for e in evs)


def test_only_step_and_log_events(monkeypatch):
    scripts = ['{"thought":"x","tool":"http_request","args":{"path":"/exploit"},"done":false}']
    _, evs = _outcome_and_events(scripts, "/exploit", monkeypatch)
    assert evs and all(e.get("type") in ("step", "log") for e in evs)
    assert not any(e.get("type") == "final" for e in evs)


def test_dedup_repeated_action(monkeypatch):
    same = '{"thought":"lặp","tool":"http_request","args":{"path":"/x"},"done":false}'
    oc, evs = _outcome_and_events([same, same, same], "/never", monkeypatch, max_steps=3)
    assert any("[dedup]" in e.get("line", "") for e in evs)
    assert oc.verdict == "exploit_failed"


def test_parse_failure_stops_clean(monkeypatch):
    oc, evs = _outcome_and_events(["blah blah", "still not json"], "/never", monkeypatch, max_steps=2)
    assert oc.verdict in ("exploit_failed", "inconclusive")
    assert any("không trả JSON" in e.get("detail", "") for e in evs)
    assert all(e.get("type") in ("step", "log") for e in evs)


def test_no_runner_errors_gracefully(monkeypatch):
    monkeypatch.setattr(lane1, "run_lane1_tool", _fake_tool_factory("/x"))
    oc = Lane1Outcome()
    evs = _run(run_lane1(_S(), None, cve="C", host="127.0.0.1", port="18054", vuln_class="",
                         success_signal=None, command="id", base_index=5, run_id="t",
                         max_steps=3, step_timeout_s=5, total_timeout_s=30, outcome=oc))
    assert oc.verdict == "error" and all(e.get("type") in ("step", "log") for e in evs)
