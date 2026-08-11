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
from .map_cve import map_cve_phase
from .memory import BughuntRunState, make_run_state
from .recon import hunt_from_surface, probe_http_surface
from .report import write_report
from .tools import AGENT_TOOL_NAMES
from .triage import triage_leads_llm
from .verify import leads_to_findings_log, verify_high_signal

logger = logging.getLogger(__name__)

# Outer Linch phases (FE chips + SSE).
BUGHUNT_PHASES: tuple[str, ...] = ("AgentHunt", "Validate", "MapCVE", "Report", "Exploit")

AGENT_SYSTEM = """\
You are an elite autonomous bug bounty hunter operating within an authorized bug bounty program or VAPT engagement.
You have a set of tools that execute real security scans. Use them strategically.

CORE RULES:
1. Always start with run_recon (or bh_http_probe then run_recon) if no recon data exists yet.
   Prefer quick=false when the scope is a real domain (not a tiny lab).
   Lab/private IP targets force quick recon automatically.
2. After recon, read_recon_summary once, then call run_parallel_hunt_wave IMMEDIATELY.
   Do NOT call run_vuln_scan / run_verify_leads / run_cors_check / run_secret_hunt / run_api_fuzz
   one-by-one when the wave covers them — that wastes wall-clock.
3. After the wave, deepen only if needed: sqlmap/CMS/RCE/JWT based on wave signals.
4. Prioritize by impact: CMS exploits > RCE > SQLi > IDOR/API > secrets > CORS/JWT > info.
5. If Drupal or WordPress is detected → run_cms_exploit immediately.
6. If Java/Tomcat/JBoss/Spring is detected → run_rce_scan + run_post_param_discovery.
7. Soft-skip = missing binary on host, NOT a clean target — try the next relevant tool.
8. Maintain notes via update_working_memory after significant discoveries.
9. Call finish only after recon + run_parallel_hunt_wave (or equivalent), or when truly no surface remains.
10. DO NOT invent CVE IDs. Only cite tool evidence.
11. DO NOT repeat a tool that already completed unless explicitly justified.
12. robots.txt Disallow paths and recon sensitive_paths MUST be covered (wave verify does this).

Think step by step. Pick the highest-impact next action given what you know.
When done, call the finish tool with a brief verdict.
"""


def _human_result_text(text: str, verdict: str) -> str:
    """Prefer plain verdict; re-dump JSON with real Unicode (not \\uXXXX)."""
    import json

    v = (verdict or "").strip()
    if v:
        return v
    raw = (text or "").strip()
    if not raw:
        return ""
    if raw.startswith("{") and raw.endswith("}"):
        try:
            obj = json.loads(raw)
            if isinstance(obj, dict) and obj.get("verdict"):
                return str(obj["verdict"])
            return json.dumps(obj, ensure_ascii=False)
        except (json.JSONDecodeError, TypeError):
            pass
    return raw


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
    """Prefer structured verify leads; never promote tool-dump noise."""
    _TOOL_DUMP = {
        "bh_http_probe",
        "run_recon",
        "run_vuln_scan",
        "run_verify_leads",
        "run_parallel_hunt_wave",
        "run_cors_check",
        "run_secret_hunt",
        "run_api_fuzz",
        "run_param_discovery",
        "run_post_param_discovery",
        "run_sqlmap_targeted",
        "run_sqlmap_on_file",
        "run_js_analysis",
        "run_jwt_audit",
        "update_working_memory",
        "read_recon_summary",
        "read_findings_summary",
        "finish",
    }
    hyps: list[dict[str, Any]] = []
    for f in state.memory.findings_log[-40:]:
        tool = str(f.get("tool") or "")
        sev = str(f.get("severity") or "").upper()
        vul = str(f.get("vul_class") or "").strip()
        source = str(f.get("source") or "")
        # Structured verify / explicit vul_class (not the tool name itself)
        if tool == "verify_high_signal" or source == "verify_high_signal" or (
            vul and vul not in _TOOL_DUMP and not vul.startswith("run_")
        ):
            conf = float(f.get("confidence") or (0.85 if sev in {"HIGH", "CRITICAL"} else 0.7))
            hyps.append({
                "vul_class": vul or "finding",
                "cve": None,
                "confidence": conf,
                "evidence": str(f.get("evidence") or f.get("text") or "")[:400],
                "path": f.get("path"),
                "next": "Validate lead; do not claim CVE without template evidence.",
                "source": source or tool or "agent",
            })
            continue
        # Never promote scanner/tool wrappers as leads
        if tool in _TOOL_DUMP or tool.startswith("run_"):
            continue
    # de-dupe by vul_class+path
    seen: set[tuple[str, str]] = set()
    unique: list[dict[str, Any]] = []
    for h in hyps:
        key = (str(h.get("vul_class")), str(h.get("path") or ""))
        if key in seen:
            continue
        seen.add(key)
        unique.append(h)
    if not unique and state.surface:
        return hunt_from_surface(state.surface)
    summary = state.verdict or state.memory.findings_summary()
    return {
        "hypotheses": unique,
        "summary": (summary or f"{len(unique)} agent lead(s)")[:400],
        "source": "agent",
    }


async def _ensure_verify_leads(
    state: BughuntRunState,
    *,
    on_log: Any | None = None,
) -> dict[str, Any]:
    """Always run high-signal verify once (agent may skip; scanners miss labs)."""
    if "run_verify_leads" in state.memory.completed_steps:
        # Still reload from disk if present
        vf = state.workdir / "findings" / "verify_high_signal.json"
        if vf.is_file():
            import json
            try:
                return json.loads(vf.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                pass
    if on_log:
        on_log("sys", "AgentHunt: auto run_verify_leads (high-signal PoCs)")
    result = await verify_high_signal(state.target, recon_dir=state.workdir / "recon")
    for item in leads_to_findings_log(list(result.get("leads") or [])):
        from datetime import datetime
        state.memory.findings_log.append({**item, "ts": datetime.now().isoformat()})
    out = state.workdir / "findings" / "verify_high_signal.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    import json
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    state.memory.completed_steps.append("run_verify_leads")
    state.memory.save()
    return result


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
    verify = await _ensure_verify_leads(state)
    await _emit_agent_end(
        wf,
        title="verify-high-signal",
        result_text=verify.get("summary") or "",
        structured_output=verify,
    )
    vleads = list(verify.get("leads") or [])
    if vleads:
        merged = list(vleads) + list(hunt.get("hypotheses") or [])
        hunt = {
            **hunt,
            "hypotheses": merged,
            "summary": f"{verify.get('summary')}; {len(merged)} lead(s)"[:400],
        }
    return {
        "surface": surface,
        "engine": engine_result,
        "hunt": hunt,
        "agent_text": "",
        "llm": False,
        "verify": verify,
    }


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
    # Raise Linch tool ceiling for arsenal scripts (default agent is 30s).
    from ..config import get_settings
    settings = get_settings()
    agent = wf._agent  # noqa: SLF001
    prev_timeout = getattr(agent, "tool_timeout_ms", None)
    prev_turns = getattr(agent, "max_turns", None)
    agent.tool_timeout_ms = float(
        getattr(settings, "bughunt_tool_timeout_ms", None)
        or (int(getattr(settings, "bughunt_recon_timeout_s", 600) or 600) * 1000)
    )
    # Profile bughunt max_turns=32 — apply for this hunt only.
    agent.max_turns = 32
    if state.on_log:
        state.on_log(
            "sys",
            f"bughunt tool_timeout_ms={int(agent.tool_timeout_ms)} "
            f"max_turns={agent.max_turns} "
            f"(recon_timeout_s={getattr(settings, 'bughunt_recon_timeout_s', 600)})",
        )
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
    finally:
        agent.tool_timeout_ms = prev_timeout
        agent.max_turns = prev_turns
    await _emit_agent_end(
        wf,
        title="bughunt-agent",
        result_text=_human_result_text(text or "", state.verdict)[:2000],
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
    # Safety net: if agent skipped the parallel wave, run it once (not serial tool spam).
    from ..config import get_settings as _gs
    from .wave import run_parallel_hunt_wave as _wave

    wave_result: dict[str, Any] | None = None
    verify: dict[str, Any]
    if "run_parallel_hunt_wave" not in state.memory.completed_steps:
        if state.on_log:
            state.on_log("sys", "AgentHunt: auto run_parallel_hunt_wave")
        wave_result = await _wave(state, settings=_gs())
        await _emit_agent_end(
            wf,
            title="parallel-hunt-wave",
            result_text=wave_result.get("summary") or "",
            structured_output=wave_result,
        )
    verify = await _ensure_verify_leads(state)
    await _emit_agent_end(
        wf,
        title="verify-high-signal",
        result_text=verify.get("summary") or "",
        structured_output=verify,
    )
    hunt = _hypotheses_from_memory(state)
    # Merge verify leads into hunt hypotheses (front of list)
    vleads = list(verify.get("leads") or [])
    if vleads:
        existing = {(str(h.get("vul_class")), str(h.get("path") or "")) for h in (hunt.get("hypotheses") or [])}
        merged = list(hunt.get("hypotheses") or [])
        for lead in vleads:
            key = (str(lead.get("vul_class")), str(lead.get("path") or ""))
            if key in existing:
                continue
            merged.insert(0, lead)
            existing.add(key)
        hunt = {
            **hunt,
            "hypotheses": merged,
            "summary": (
                f"{verify.get('summary')}; {len(merged)} lead(s) after verify"
            )[:400],
        }
    return {
        "surface": state.surface,
        "engine": {"ok": True, "recon_dir": str(state.workdir / "recon"), "source": "agent_tools"},
        "hunt": hunt,
        "agent_text": text or state.verdict or "",
        "llm": True,
        "verify": verify,
        "wave": wave_result,
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
    enable_exploit: bool = False,
    run_cve_sweep: bool = True,
) -> dict[str, Any]:
    """Agent-driven bughunt. Phases: AgentHunt → Validate → MapCVE → Report → (Exploit).

    ``enable_exploit``: opt-in; also requires ``POC_EXPLOIT_ENABLED`` + API URL.
    ``use_llm``: None = auto (on when provider is not ScriptedProvider).
    """
    target = (target or "").strip()
    if not target:
        raise ValueError("bughunt_flow requires a non-empty target")

    rid = (run_id or getattr(wf, "_run_id", None) or "").strip() or f"bh:{uuid4().hex}"
    llm_on = provider_supports_llm(wf._agent) if use_llm is None else bool(use_llm)  # noqa: SLF001

    from ..config import get_settings
    settings = get_settings()
    state = make_run_state(
        run_id=rid,
        target=target,
        scope_note=scope_note,
        settings=settings,
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

    # ── Validate (triage-validation skill — LLM 7Q + hard floor) ────────────
    await wf.phase("Validate")
    completed.append("Validate")
    finding_hints = list(state.memory.findings_log[-30:])
    if on_log:
        on_log("sys", "Validate: LLM triage-validation (7-question gate)")
    validation = await triage_leads_llm(
        wf,
        surface,
        hunt,
        findings_hints=finding_hints,
        scope_note=scope_note,
        use_llm=llm_on,
    )
    await _emit_agent_end(
        wf,
        title="validate-leads",
        result_text=validation.get("summary") or "",
        structured_output=validation,
    )

    # ── MapCVE (/scan-cves + artifact parse) ────────────────────────────────
    await wf.phase("MapCVE")
    completed.append("MapCVE")
    from .engine import _is_ip_lab_target
    do_sweep = bool(run_cve_sweep and surface.get("reachable") and not _is_ip_lab_target(target))
    if on_log:
        on_log(
            "sys",
            "MapCVE: parsing findings"
            + (" + cve_scan.sh" if do_sweep else " (CVE sweep skipped for lab IP)"),
        )
    mapped = await map_cve_phase(
        state.workdir,
        leads=list(validation.get("leads") or []),
        settings=settings,
        on_log=on_log,
        run_sweep=do_sweep,
    )
    await _emit_agent_end(
        wf,
        title="map-cve",
        result_text=mapped.get("summary") or "",
        structured_output={
            "cves": mapped.get("cves"),
            "cve_leads": mapped.get("cve_leads"),
            "sweep": mapped.get("sweep"),
        },
    )

    # Merge CVE leads into reportable set (dedupe by cve|vul_class|evidence prefix)
    report_leads = list(validation.get("leads") or [])
    for cl in mapped.get("cve_leads") or []:
        report_leads.append(cl)

    # ── Report (report-writing skill) ───────────────────────────────────────
    await wf.phase("Report")
    completed.append("Report")
    report = write_report(
        state.workdir,
        target=target,
        scope_note=scope_note,
        leads=report_leads,
        cves=list(mapped.get("cves") or []),
        validation_summary=str(validation.get("summary") or ""),
        map_summary=str(mapped.get("summary") or ""),
    )
    await _emit_agent_end(
        wf,
        title="report-draft",
        result_text=report.get("summary") or "",
        structured_output=report,
    )
    if on_log:
        on_log("sys", report.get("summary") or "report written")

    # ── Exploit (opt-in + PoC API) ──────────────────────────────────────────
    exploit: dict[str, Any] = {
        "ok": False,
        "skipped": True,
        "reason": "enable_exploit=false",
        "results": [],
        "summary": "Exploit: skipped (opt-in off)",
    }
    await wf.phase("Exploit")
    completed.append("Exploit")
    if enable_exploit:
        from .exploit_phase import run_exploit_phase
        exploit = await run_exploit_phase(
            target=target,
            cves=list(mapped.get("cves") or []),
            settings=settings,
            on_log=on_log,
            run_id=rid,
        )
    await _emit_agent_end(
        wf,
        title="exploit-phase",
        result_text=exploit.get("summary") or "",
        structured_output=exploit,
    )

    status = "recon_ok" if surface.get("reachable") else "unreachable"
    if mapped.get("cves"):
        status = "cve_mapped"
    if exploit.get("ok"):
        status = "exploit_attempted"
    note = (scope_note or "").strip() or None
    message = (
        f"{surface.get('summary')} | {hunt.get('summary')} | {validation.get('summary')} | "
        f"{mapped.get('summary')} | {report.get('summary')} | {exploit.get('summary')}"
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
        "map_cve": mapped,
        "report": report,
        "exploit": exploit,
        "memory": state.memory.to_dict(),
        "verdict": state.verdict or None,
        "agent_text": pack.get("agent_text") or "",
        "llm_hunt": bool(pack.get("llm")),
        "backend": "linch-agent",
        "message": message,
    }
