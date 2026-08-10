"""Bughunt: Linch agent port of BugHunter agent.py (no langgraph)."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

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


def test_bughunt_phases_fixed_order(tmp_path, monkeypatch):
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


def test_stream_bughunt_workflow_yields_phases_then_result(tmp_path, monkeypatch):
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


def test_bugbounty_root_and_arsenal():
    from secai_gateway.bughunt.engine import arsenal_status, bugbounty_root, recon_engine_script
    root = bugbounty_root()
    assert root.is_dir(), root
    assert recon_engine_script().is_file()
    status = arsenal_status()
    assert "present" in status and "missing" in status


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


def test_bughunt_http_stream(tmp_path, monkeypatch):
    monkeypatch.setenv("GATEWAY_DB_PATH", str(tmp_path / "gw.db"))
    monkeypatch.setenv("VERIFY_ALLOW_ALL", "true")

    from fastapi.testclient import TestClient
    from secai_gateway.app import create_app

    class _Stub:
        def __init__(self):
            self.agent = _agent()

        def stream_bughunt(self, *, target, scope_note="", run_id=None):
            return stream_bughunt_workflow(
                self.agent, target=target, scope_note=scope_note, run_id=run_id,
                use_llm=False, use_recon_engine=False,
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
