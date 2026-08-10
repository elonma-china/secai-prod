"""Linch bughunt workflow — BugHunter agent.py ReAct mapped to ``wf.agent``.

Python owns outer phases (AgentHunt → Validate). Inside AgentHunt the LLM
orchestrates tools via Linch subagent ReAct (no langgraph dependency).
"""
from __future__ import annotations

import logging
from typing import Any
from uuid import uuid4

from linch.events import WorkflowEvent
from linch.session import RunOptions
from linch.workflow import WorkflowContext

from .llm_hunt import provider_supports_llm
from .memory import BughuntRunState, make_run_state
from .recon import hunt_from_surface, probe_http_surface, validate_hypotheses
from .tools import AGENT_TOOL_NAMES

logger = logging.getLogger(__name__)

# Outer Linch phases (FE chips + SSE). Inner tool loop is ReAct inside AgentHunt.
BUGHUNT_PHASES: tuple[str, ...] = ("AgentHunt", "Validate")

AGENT_SYSTEM = """\
You are an elite autonomous bug bounty hunter operating within an authorized bug bounty program or VAPT engagement.
You have a set of tools that execute real security scans. Use them strategically.

CORE RULES:
1. Always start with run_recon (or bh_http_probe then run_recon) if no recon data exists yet.
2. After recon, read_recon_summary to understand the attack surface before choosing next tool.
3. Prioritize by impact: CMS exploits > RCE > SQLi > IDOR > secrets > info.
4. If Drupal or WordPress is detected → run_cms_exploit immediately.
5. If Java/Tomcat/JBoss/Spring is detected → run_rce_scan + run_post_param_discovery.
6. If parameterized URLs found → run_sqlmap_targeted.
7. If JWT tokens appear in any recon data → run_jwt_audit.
8. Maintain notes via update_working_memory after each significant discovery.
9. Call finish when: all high-priority tools done, time running low, or no new attack surface.
10. DO NOT invent CVE IDs. Only cite tool evidence.
11. DO NOT repeat a tool that already completed unless explicitly justified.

Think step by step. Pick the highest-impact next action given what you know.
When done, call the finish tool with a brief verdict.
"""


async def _emit_agent_end(
    wf: WorkflowContext,
    *,
    title: str,
    result_text: str,
    structured_output: dict[str, Any] | None = None,
) -> None:
    await wf._emit(  # noqa: SLF001
        WorkflowEvent(
            kind="agent_end",
            title=title,
            result_text=result_text,
            structured_output=structured_output,
        )
    )


def build_agent_prompt(*, target: str, scope_note: str, memory_summary: str = "") -> str:
    note = (scope_note or "").strip() or "(none)"
    mem = (memory_summary or "").strip() or "(empty — new session)"
    return (
        f"{AGENT_SYSTEM}\n\n"
        f"Target: {target}\n"
        f"Scope note: {note}\n\n"
        f"Session memory snapshot:\n{mem}\n\n"
        f"Begin hunting. Use tools. End with finish(verdict=...)."
    )


def _hypotheses_from_memory(state: BughuntRunState) -> dict[str, Any]:
    hyps: list[dict[str, Any]] = []
    for f in state.memory.findings_log[-12:]:
        hyps.append({
            "vul_class": str(f.get("tool") or "finding")[:80],
            "cve": None,
            "confidence": 0.4 if str(f.get("severity")).upper() in {"LOW", "INFO"} else 0.65,
            "evidence": str(f.get("text") or "")[:400],
            "next": "Validate lead; do not claim CVE without template evidence.",
            "source": "agent",
        })
    if not hyps and state.surface:
        return hunt_from_surface(state.surface)
    summary = state.verdict or state.memory.findings_summary()
    return {
        "hypotheses": hyps,
        "summary": (summary or f"{len(hyps)} agent lead(s)")[:400],
        "source": "agent",
    }


async def _offline_agent_hunt(
    wf: WorkflowContext,
    *,
    target: str,
    state: BughuntRunState,
    use_recon_engine: bool | None,
) -> dict[str, Any]:
    """Deterministic smoke path (tests / ScriptedProvider): probe + optional engine."""
    surface = await probe_http_surface(target)
    state.surface = surface
    await _emit_agent_end(
        wf,
        title="recon-probe",
        result_text=surface.get("summary") or "",
        structured_output={
            "base_url": surface.get("base_url"),
            "reachable": surface.get("reachable"),
            "tech": surface.get("tech"),
            "finding_count": len(surface.get("findings") or []),
            "findings": (surface.get("findings") or [])[:12],
        },
    )
    engine_result: dict[str, Any] | None = None
    run_engine = use_recon_engine
    if run_engine is None:
        run_engine = False  # offline default: skip long arsenal
    if run_engine:
        from ..config import get_settings
        from .engine import run_recon_engine
        from .workdir import recon_dir
        seed = []
        if surface.get("reachable") and surface.get("base_url"):
            seed = [str(surface["base_url"]).rstrip("/") + "/"]
        await wf._emit(  # noqa: SLF001
            WorkflowEvent(
                kind="agent_start",
                title="recon-engine",
                result_text="running recon_engine.sh",
            )
        )
        try:
            engine_result = await run_recon_engine(
                target,
                settings=get_settings(),
                quick=True,
                seed_urls=seed,
                run_id=state.run_id,
                out_dir=recon_dir(state.workdir),
                on_log=state.on_log,
            )
        except Exception as exc:  # noqa: BLE001
            engine_result = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        await _emit_agent_end(
            wf,
            title="recon-engine",
            result_text=(engine_result or {}).get("summary_text")
            or (engine_result or {}).get("error")
            or "done",
            structured_output={"ok": (engine_result or {}).get("ok"), "counts": (engine_result or {}).get("counts")},
        )
    hunt = hunt_from_surface(surface)
    return {"surface": surface, "engine": engine_result, "hunt": hunt, "agent_text": "", "llm": False}


async def _llm_agent_hunt(
    wf: WorkflowContext,
    *,
    target: str,
    scope_note: str,
    state: BughuntRunState,
) -> dict[str, Any]:
    mem_snap = (
        f"completed={list(dict.fromkeys(state.memory.completed_steps)) or 'none'}\n"
        f"working_memory:\n{state.memory.working_memory or '(empty)'}\n"
        f"findings:\n{state.memory.findings_summary()}"
    )
    prompt = build_agent_prompt(target=target, scope_note=scope_note, memory_summary=mem_snap)
    try:
        text = await wf.agent(
            prompt,
            label="bughunt-agent",
            tools=list(AGENT_TOOL_NAMES),
            run_options=RunOptions(deps=state, final_tool_name="finish"),
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("bughunt agent failed: %s", exc)
        await _emit_agent_end(
            wf,
            title="bughunt-agent-error",
            result_text=f"{type(exc).__name__}: {exc}",
            structured_output={"ok": False, "error": str(exc)},
        )
        # Fall back to probe so Validate still has surface.
        surface = state.surface or await probe_http_surface(target)
        state.surface = surface
        hunt = hunt_from_surface(surface)
        return {
            "surface": surface,
            "engine": None,
            "hunt": hunt,
            "agent_text": "",
            "llm": False,
            "error": str(exc),
        }
    await _emit_agent_end(
        wf,
        title="bughunt-agent",
        result_text=(text or state.verdict or "")[:2000],
        structured_output={
            "verdict": state.verdict,
            "completed_steps": list(dict.fromkeys(state.memory.completed_steps)),
            "step_count": state.memory.step_count,
            "findings": state.memory.findings_log[-20:],
        },
    )
    if not state.surface:
        try:
            state.surface = await probe_http_surface(target)
        except Exception:  # noqa: BLE001
            state.surface = {"reachable": False, "summary": "probe failed after agent", "findings": [], "tech": []}
    hunt = _hypotheses_from_memory(state)
    return {
        "surface": state.surface,
        "engine": {"ok": True, "recon_dir": str(state.workdir / "recon"), "source": "agent_tools"},
        "hunt": hunt,
        "agent_text": text or state.verdict or "",
        "llm": True,
    }


async def bughunt_flow(
    wf: WorkflowContext,
    *,
    target: str,
    scope_note: str = "",
    use_llm: bool | None = None,
    use_recon_engine: bool | None = None,
    on_log: Any | None = None,
    run_id: str | None = None,
) -> dict[str, Any]:
    """Agent-driven bughunt. Outer control: AgentHunt → Validate.

    ``use_llm``: None = auto (on when provider is not ScriptedProvider).
    ``run_id``: isolation key for workdir/memory (defaults to workflow run id).
    """
    target = (target or "").strip()
    if not target:
        raise ValueError("bughunt_flow requires a non-empty target")

    rid = (run_id or getattr(wf, "_run_id", None) or "").strip() or f"bh:{uuid4().hex}"
    llm_on = provider_supports_llm(wf._agent) if use_llm is None else bool(use_llm)  # noqa: SLF001

    from ..config import get_settings
    state = make_run_state(
        run_id=rid,
        target=target,
        scope_note=scope_note,
        settings=get_settings(),
        on_log=on_log,
    )

    completed: list[str] = []
    await wf.phase("AgentHunt")
    completed.append("AgentHunt")

    if llm_on:
        pack = await _llm_agent_hunt(wf, target=target, scope_note=scope_note, state=state)
    else:
        pack = await _offline_agent_hunt(
            wf, target=target, state=state, use_recon_engine=use_recon_engine,
        )

    surface = pack["surface"]
    hunt = pack["hunt"]
    engine_result = pack.get("engine")

    await wf.phase("Validate")
    completed.append("Validate")
    validation = validate_hypotheses(surface, hunt)
    await _emit_agent_end(
        wf,
        title="validate-leads",
        result_text=validation.get("summary") or "",
        structured_output=validation,
    )

    status = "recon_ok" if surface.get("reachable") else "unreachable"
    note = (scope_note or "").strip() or None
    message = (
        f"{surface.get('summary')} | {hunt.get('summary')} | {validation.get('summary')}"
    )
    if state.verdict:
        message = f"{message} | verdict={state.verdict}"

    return {
        "status": status,
        "target": target,
        "scope_note": note,
        "phases": completed,
        "run_id": rid,
        "workdir": str(state.workdir),
        "surface": surface,
        "engine": engine_result,
        "hunt": hunt,
        "validation": validation,
        "memory": state.memory.to_dict(),
        "verdict": state.verdict or None,
        "agent_text": pack.get("agent_text") or "",
        "llm_hunt": bool(pack.get("llm")),
        "backend": "linch-agent",
        "message": message,
    }
