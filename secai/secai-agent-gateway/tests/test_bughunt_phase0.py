"""Bughunt: Linch agent port of BugHunter agent.py (no langgraph)."""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from linch import Agent, InMemoryRunStore, InMemorySessionStore
from linch.config import FeatureFlags
from linch.evals import ScriptedProvider, TextTurn
from linch.events import WorkflowEvent
from linch.tools.registry import empty_tools

from secai_gateway.api.sse import event_to_sse
from secai_gateway.bughunt import (
    AGENT_TOOL_NAMES,
    BUGHUNT_PHASES,
    bughunt_flow,
    build_bughunt_tools,
    stream_bughunt_workflow,
)
from secai_gateway.bughunt.memory import HuntMemory, make_run_state
from secai_gateway.bughunt.recon import (
    hunt_from_surface,
    normalize_base_url,
    validate_hypotheses,
)
from secai_gateway.bughunt.llm_hunt import (
    extract_json_object,
    merge_hunt,
    normalize_hunt_payload,
)
from secai_gateway.agent.profiles import PROFILES

_FAKE_SURFACE = {
    "base_url": "http://example.com",
    "reachable": True,
    "tech": ["php", "secaidemo", "login-form"],
    "endpoints": [{"path": "/", "ok": True, "status": 200}],
    "findings": [
        {
            "kind": "fingerprint",
            "title": "Server banner on /",
            "severity": "info",
            "path": "/",
            "evidence": "Server: SecAI-DemoServer/0.1",
        },
        {
            "kind": "disclosure",
            "title": "robots.txt present",
            "severity": "low",
            "path": "/robots.txt",
            "evidence": "Disallow: /admin",
        },
    ],
    "errors": [],
    "summary": "Reachable http://example.com; tech=['php']; 2 findings; 1/1 paths OK",
}


@pytest.fixture
def offline_workflow_services(monkeypatch):
    # Even use_llm=False still runs HTTP verification and may launch cve_scan.sh.
    monkeypatch.setattr("secai_gateway.bughunt.workflow.verify_high_signal", AsyncMock(
        return_value={"ok": False, "leads": [], "notes": [],
                      "summary": "verify_high_signal: no high-signal hits in offline fixture"}))
    monkeypatch.setattr("secai_gateway.bughunt.map_cve.run_cve_sweep", AsyncMock(
        return_value={"cves": [], "skipped": True, "reason": "offline fixture"}))


def _agent() -> Agent:
    return Agent(
        model="scripted",
        provider=ScriptedProvider([TextTurn("unused")]),
        tools=empty_tools(),
        session_store=InMemorySessionStore(),
        run_store=InMemoryRunStore(),
        features=FeatureFlags(skills=False, subagents=False, mcp=False),
        max_turns=2,
    )


def test_no_langgraph_import():
    import secai_gateway.bughunt as pkg
    import secai_gateway.bughunt.tools as tools_mod
    import secai_gateway.bughunt.workflow as wf_mod
    for mod in (pkg, tools_mod, wf_mod):
        assert "langgraph" not in getattr(mod, "__dict__", {})


def test_agent_tool_registry():
    tools = build_bughunt_tools()
    for name in AGENT_TOOL_NAMES:
        assert name in tools, name
    assert "bh_recon_engine" not in tools  # replaced by run_recon
    profile = PROFILES["bughunt"]
    assert profile.max_turns >= 20
    for name in AGENT_TOOL_NAMES:
        assert name in profile.tool_names, name


def test_hunt_memory_persists(tmp_path, monkeypatch):
    monkeypatch.setenv("GATEWAY_DB_PATH", str(tmp_path / "gw.db"))
    state = make_run_state(run_id="bh-mem-1", target="example.com")
    assert state.workdir.is_dir()
    state.memory.working_memory = "notes"
    state.memory.record_step("run_recon", "live hosts: 1")
    path = Path(state.memory.session_file)
    assert path.is_file()
    reloaded = HuntMemory(path)
    assert reloaded.working_memory == "notes"
    assert "run_recon" in reloaded.completed_steps
    assert reloaded.step_count == 1


def test_normalize_base_url():
    assert normalize_base_url("127.0.0.1:8765") == "http://127.0.0.1:8765"
    assert normalize_base_url("https://example.com/path") == "https://example.com"


def test_bughunt_phases_fixed_order(tmp_path, monkeypatch, offline_workflow_services):
    monkeypatch.setenv("GATEWAY_DB_PATH", str(tmp_path / "gw.db"))
    agent = _agent()
    events: list[WorkflowEvent] = []

    async def _run():
        with patch(
            "secai_gateway.bughunt.workflow.probe_http_surface",
            new=AsyncMock(return_value=_FAKE_SURFACE),
        ):
            return await agent.run_workflow(
                lambda wf: bughunt_flow(
                    wf, target="example.com", use_llm=False, use_recon_engine=False,
                    run_id="bh-test-phases",
                ),
                run_id="bh-test-phases",
                on_event=lambda e: events.append(e) if isinstance(e, WorkflowEvent) else None,
            )

    result = asyncio.run(_run())
    assert result["status"] == "recon_ok"
    assert result["target"] == "example.com"
    assert result["phases"] == list(BUGHUNT_PHASES)
    assert result["run_id"] == "bh-test-phases"
    assert result["workdir"]
    assert result["surface"]["reachable"] is True
    assert result["surface"]["findings"]
    assert result["hunt"]["hypotheses"]
    assert result.get("llm_hunt") is False
    assert result.get("backend") == "linch-agent"
    assert "leads" in result["validation"]
    phase_titles = [e.title for e in events if e.kind == "phase"]
    assert phase_titles == list(BUGHUNT_PHASES)
    assert any(e.kind == "agent_end" and e.title == "recon-probe" for e in events)


def test_bughunt_requires_target():
    agent = _agent()

    async def _run():
        return await agent.run_workflow(lambda wf: bughunt_flow(wf, target="  "))

    try:
        asyncio.run(_run())
        assert False, "expected ValueError"
    except ValueError as exc:
        assert "target" in str(exc).lower()


def test_workflow_event_to_sse_phase():
    frame = event_to_sse(WorkflowEvent(kind="phase", title="AgentHunt"))
    assert frame is not None
    text = frame.decode()
    assert "event: phase" in text
    assert '"title": "AgentHunt"' in text


def test_stream_bughunt_workflow_yields_phases_then_result(tmp_path, monkeypatch, offline_workflow_services):
    monkeypatch.setenv("GATEWAY_DB_PATH", str(tmp_path / "gw.db"))
    agent = _agent()

    async def _collect():
        phases = []
        result = None
        with patch(
            "secai_gateway.bughunt.workflow.probe_http_surface",
            new=AsyncMock(return_value=_FAKE_SURFACE),
        ):
            async for item in stream_bughunt_workflow(
                agent, target="lab.local", run_id="bh-stream-1",
                use_llm=False, use_recon_engine=False,
            ):
                if isinstance(item, WorkflowEvent) and item.kind == "phase":
                    phases.append(item.title)
                elif isinstance(item, dict) and item.get("type") == "bughunt_result":
                    result = item["result"]
        return phases, result

    phases, result = asyncio.run(_collect())
    assert phases == list(BUGHUNT_PHASES)
    assert result["status"] == "recon_ok"
    assert result["phases"] == list(BUGHUNT_PHASES)
    assert result["surface"]["findings"]
    assert "bh-stream-1" in result["run_id"] or result["run_id"] == "bh-stream-1"


def test_bugbounty_root_prefers_settings_then_environment_and_resolves_script(tmp_path, monkeypatch):
    from secai_gateway.bughunt.engine import bugbounty_root, recon_engine_script

    settings_root = tmp_path / "from-settings"
    env_root = tmp_path / "from-env"
    settings_root.mkdir()
    (settings_root / "tools").mkdir()
    script = settings_root / "tools" / "recon_engine.sh"
    script.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    monkeypatch.setenv("BUGBOUNTY_ROOT", str(env_root))

    settings = SimpleNamespace(bugbounty_root=str(settings_root))
    assert bugbounty_root(settings) == settings_root.resolve()
    assert recon_engine_script(settings) == script.resolve()
    assert recon_engine_script(settings).is_file()

    # An empty configured value falls through to BUGBOUNTY_ROOT, even when its script is
    # absent; callers can inspect the resolved expected path without requiring the clone.
    empty_settings = SimpleNamespace(bugbounty_root="")
    assert bugbounty_root(empty_settings) == env_root.resolve()
    assert recon_engine_script(empty_settings) == (env_root / "tools" / "recon_engine.sh").resolve()
    assert not recon_engine_script(empty_settings).is_file()


def test_arsenal_status_uses_controlled_path_and_ready_contract(tmp_path, monkeypatch):
    from secai_gateway.bughunt.engine import arsenal_status

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    subfinder = bin_dir / "subfinder"
    subfinder.write_text("", encoding="utf-8")
    monkeypatch.setattr("secai_gateway.bughunt.engine.shutil.which", lambda name: str(subfinder) if name == "subfinder" else None)
    status = arsenal_status()
    assert status["present"] == ["subfinder"]
    assert "nmap" in status["missing"]
    assert status["ready"] is False

    nmap = bin_dir / "nmap"
    nmap.write_text("", encoding="utf-8")
    monkeypatch.setattr(
        "secai_gateway.bughunt.engine.shutil.which",
        lambda name: str({"subfinder": subfinder, "nmap": nmap}[name]) if name in {"subfinder", "nmap"} else None,
    )
    assert arsenal_status()["ready"] is True


@pytest.mark.skipif(
    os.environ.get("RUN_BUGBOUNTY_EXTERNAL_SMOKE") != "1",
    reason="external integration; set RUN_BUGBOUNTY_EXTERNAL_SMOKE=1 to require the real claude-bug-bounty clone and recon_engine.sh",
)
def test_bugbounty_external_clone_and_arsenal_smoke():
    from secai_gateway.bughunt.engine import arsenal_status, bugbounty_root, recon_engine_script

    root = bugbounty_root()
    assert root.is_dir(), f"external claude-bug-bounty clone missing: {root}"
    script = recon_engine_script()
    assert script.is_file(), f"external recon engine script missing: {script}"
    status = arsenal_status()
    assert "present" in status and "missing" in status and isinstance(status["ready"], bool)


def test_llm_hunt_json_helpers():
    text = 'Here:\n```json\n{"hypotheses":[{"vul_class":"auth_surface","cve":"CVE-1","confidence":0.7,"evidence":"login","next":"check"}],"summary":"ok"}\n```'
    data = extract_json_object(text)
    assert data is not None
    norm = normalize_hunt_payload(data)
    assert norm is not None
    assert norm["hypotheses"][0]["cve"] is None  # CVE stripped
    assert norm["hypotheses"][0]["source"] == "llm"
    merged = merge_hunt(
        {"hypotheses": [{"vul_class": "stack_php", "confidence": 0.3}], "summary": "base"},
        norm,
    )
    assert merged["source"] == "llm+deterministic"
    classes = {h["vul_class"] for h in merged["hypotheses"]}
    assert "auth_surface" in classes and "stack_php" in classes


def test_hunt_validate_helpers():
    surface = {
        "reachable": True,
        "tech": ["php", "login-form", "secaidemo"],
        "findings": [
            {"kind": "disclosure", "title": "robots.txt present", "evidence": "Disallow: /admin"},
        ],
        "summary": "ok",
    }
    hunt = hunt_from_surface(surface)
    assert hunt["hypotheses"]
    assert all(h.get("cve") is None for h in hunt["hypotheses"])
    val = validate_hypotheses(surface, hunt)
    assert val["ok"] is True
    assert val["leads"]


def test_bughunt_http_stream(tmp_path, monkeypatch, offline_workflow_services):
    monkeypatch.setenv("GATEWAY_DB_PATH", str(tmp_path / "gw.db"))
    monkeypatch.setenv("VERIFY_ALLOW_ALL", "true")

    from fastapi.testclient import TestClient
    from secai_gateway.app import create_app

    class _Stub:
        def __init__(self):
            self.agent = _agent()

        def stream_bughunt(self, *, target, scope_note="", run_id=None,
                          enable_exploit=False, run_cve_sweep=True):
            return stream_bughunt_workflow(
                self.agent, target=target, scope_note=scope_note, run_id=run_id,
                use_llm=False, use_recon_engine=False,
                enable_exploit=enable_exploit, run_cve_sweep=run_cve_sweep,
            )

    app = create_app(runner=_Stub())  # type: ignore[arg-type]
    client = TestClient(app)
    with patch(
        "secai_gateway.bughunt.workflow.probe_http_surface",
        new=AsyncMock(return_value=_FAKE_SURFACE),
    ):
        with client.stream(
            "POST", "/ai/bughunt/stream",
            json={"target": "example.com", "run_id": "bh-http-1"},
        ) as resp:
            assert resp.status_code == 200
            body = "".join(resp.iter_text())
    assert "event: phase" in body
    assert '"title": "AgentHunt"' in body
    assert '"title": "Validate"' in body
    assert "event: final" in body
    chunks = [c for c in body.split("\n\n") if c.startswith("event: final")]
    assert chunks
    data_line = [ln for ln in chunks[-1].splitlines() if ln.startswith("data: ")][0]
    payload = json.loads(data_line[len("data: "):])
    assert payload["ok"] is True
    assert payload["result"]["phases"] == list(BUGHUNT_PHASES)
    assert payload["result"]["status"] == "recon_ok"
    assert payload["result"]["surface"]["findings"]
