"""Bughunt Linch tools — BugHunter agent tool surface on real arsenal scripts.

Tools read ``ToolContext.deps`` (:class:`BughuntRunState`) for per-run isolation
(multi-user / CCU). Deep tools call ``arsenal.py``; soft-skip only when binaries missing.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from linch.tools.base import ToolContext, ToolResult
from linch.tools.function import tool

from . import arsenal
from .engine import _is_ip_lab_target, run_recon_engine
from .memory import BughuntRunState
from .recon import probe_http_surface
from .verify import leads_to_findings_log, verify_high_signal
from .wave import run_parallel_hunt_wave
from .workdir import findings_dir, recon_dir

logger = logging.getLogger(__name__)

AGENT_TOOL_NAMES: tuple[str, ...] = (
    "bh_http_probe",
    "run_recon",
    "run_parallel_hunt_wave",
    "run_vuln_scan",
    "run_verify_leads",
    "run_js_analysis",
    "run_secret_hunt",
    "run_param_discovery",
    "run_post_param_discovery",
    "run_api_fuzz",
    "run_cors_check",
    "run_cms_exploit",
    "run_rce_scan",
    "run_sqlmap_targeted",
    "run_sqlmap_on_file",
    "run_jwt_audit",
    "read_recon_summary",
    "read_findings_summary",
    "update_working_memory",
    "finish",
)


def _state(ctx: ToolContext) -> BughuntRunState:
    deps = getattr(ctx, "deps", None)
    if not isinstance(deps, BughuntRunState):
        raise RuntimeError(
            "bughunt tool missing RunOptions.deps=BughuntRunState "
            "(workflow must inject per-run state)"
        )
    return deps


def _summarize_recon_dir(out: Path) -> str:
    lines: list[str] = []
    live = out / "live" / "httpx_full.txt"
    if not live.is_file():
        live = out / "live" / "urls.txt"
    if live.is_file():
        n = sum(1 for ln in live.read_text(encoding="utf-8", errors="replace").splitlines() if ln.strip())
        lines.append(f"Live hosts: {n}")
    for rel in ("urls/all.txt", "urls/with_params.txt", "urls/js_files.txt", "urls/api_endpoints.txt"):
        fp = out / rel
        if fp.is_file():
            n = sum(1 for ln in fp.read_text(encoding="utf-8", errors="replace").splitlines() if ln.strip())
            lines.append(f"{rel}: {n}")
    for rel in ("nuclei/findings.jsonl", "nuclei/nuclei.txt"):
        fp = out / rel
        if fp.is_file():
            n = sum(1 for ln in fp.read_text(encoding="utf-8", errors="replace").splitlines() if ln.strip())
            lines.append(f"Nuclei lines: {n}")
            break
    return "\n".join(lines) if lines else "(recon dir empty)"


def _tail_file(path: Path, limit: int = 40) -> str:
    if not path.is_file():
        return ""
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    return "\n".join(lines[-limit:])


def _emit_log(st: BughuntRunState, stream: str, line: str) -> None:
    fn = st.on_log
    if fn is None:
        return
    try:
        fn(stream, line)
    except Exception:  # noqa: BLE001
        logger.debug("on_log failed", exc_info=True)


def _tool_timeout_ms(settings: Any) -> float:
    raw = getattr(settings, "bughunt_tool_timeout_ms", None)
    if raw:
        return float(raw)
    return float(int(getattr(settings, "bughunt_recon_timeout_s", 600) or 600) * 1000)


def _done(st: BughuntRunState, name: str, obs: str, *, is_error: bool = False) -> ToolResult:
    st.memory.record_step(name, obs)
    if "soft-skipped" in obs:
        st.memory.add_finding(name, "INFO", obs[:500])
    elif is_error:
        st.memory.add_finding(name, "LOW", obs[:500])
    summary = obs.split("\n", 1)[0][:200]
    return ToolResult(content=obs, summary=summary, is_error=is_error)


def build_bughunt_tools() -> dict[str, Any]:
    """Register BugHunter-style tools for the Linch agent."""

    @tool(
        name="bh_http_probe",
        description=(
            "Quick deterministic HTTP surface probe (headers, common paths, robots, login). "
            "Use for a fast reachability check; prefer run_recon for full arsenal."
        ),
        scope="read",
        execution_timeout_ms=60_000,
    )
    async def bh_http_probe(ctx: ToolContext, target: str | None = None) -> ToolResult:
        st = _state(ctx)
        tgt = (target or st.target).strip()
        _emit_log(st, "sys", f"bh_http_probe → {tgt}")
        try:
            surface = await probe_http_surface(tgt)
        except Exception as exc:  # noqa: BLE001
            _emit_log(st, "stderr", f"bh_http_probe error: {exc}")
            return ToolResult(
                content=json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}"}),
                summary="bh_http_probe error",
                is_error=True,
            )
        st.surface = surface
        slim = {
            "ok": True,
            "base_url": surface.get("base_url"),
            "reachable": surface.get("reachable"),
            "tech": surface.get("tech"),
            "findings": (surface.get("findings") or [])[:20],
            "summary": surface.get("summary"),
        }
        obs = json.dumps(slim, ensure_ascii=False)
        st.memory.record_step("bh_http_probe", obs)
        _emit_log(st, "stdout", slim.get("summary") or f"reachable={slim['reachable']}")
        return ToolResult(content=obs, summary=f"bh_http_probe reachable={slim['reachable']}")

    @tool(
        name="run_recon",
        description=(
            "Run full BugHunter recon_engine (subfinder/httpx/katana/nmap/ffuf/nuclei when installed). "
            "MUST be first if no recon data exists. Writes into this run's isolated recon/ dir. "
            "Pass quick=false for deeper recon (slower). Streams live scanner logs over SSE."
        ),
        scope="read",
        execution_timeout_ms=600_000,
    )
    async def run_recon(
        ctx: ToolContext,
        scope_lock: bool = False,
        max_urls: int = 100,  # noqa: ARG001 — reserved for BB parity
        quick: bool = True,
    ) -> ToolResult:
        st = _state(ctx)
        from ..config import get_settings
        settings = get_settings()
        run_recon.execution_timeout_ms = _tool_timeout_ms(settings)  # type: ignore[attr-defined]
        seed: list[str] = []
        _emit_log(st, "sys", f"run_recon start target={st.target} scope_lock={scope_lock} quick={quick}")
        # Lab / private IP: force quick recon (full nmap/gau/nuclei is low ROI).
        if _is_ip_lab_target(st.target) and not quick:
            _emit_log(st, "sys", "lab IP → forcing run_recon quick=True")
            quick = True
        if not st.surface.get("reachable") or _is_ip_lab_target(st.target):
            try:
                surface = await probe_http_surface(st.target)
                st.surface = surface
                _emit_log(st, "stdout", f"probe: {surface.get('summary')}")
            except Exception as exc:  # noqa: BLE001
                st.surface = {
                    "reachable": False,
                    "summary": f"probe failed: {type(exc).__name__}: {exc}",
                    "findings": [],
                    "tech": [],
                }
                _emit_log(st, "stderr", st.surface["summary"])
        if st.surface.get("reachable") and st.surface.get("base_url"):
            seed = [str(st.surface["base_url"]).rstrip("/") + "/"]
        elif _is_ip_lab_target(st.target):
            probe_sum = st.surface.get("summary") or "unreachable"
            obs = (
                f"run_recon aborted: target unreachable (lab IP/port)\n"
                f"Probe: {probe_sum}\n"
                f"Start the demo/service on {st.target} then retry."
            )
            _emit_log(st, "stderr", "run_recon aborted: unreachable")
            return _done(st, "run_recon", obs, is_error=True)
        out = recon_dir(st.workdir)
        try:
            result = await run_recon_engine(
                st.target,
                settings=settings,
                quick=bool(quick),
                seed_urls=seed,
                run_id=st.run_id,
                out_dir=out,
                on_log=st.on_log,
            )
        except Exception as exc:  # noqa: BLE001
            obs = f"run_recon error: {type(exc).__name__}: {exc}"
            _emit_log(st, "stderr", obs)
            return _done(st, "run_recon", obs, is_error=True)
        summary = result.get("summary_text") or result.get("reason") or result.get("error") or ""
        counts = result.get("counts") or {}
        probe_line = f"Probe: {st.surface.get('summary')}\n" if st.surface.get("summary") else ""
        obs = (
            f"run_recon: ok={result.get('ok')} skipped={result.get('skipped')}\n"
            f"{probe_line}"
            f"{summary}\ncounts={counts}\n{_summarize_recon_dir(out)}"
        )
        _emit_log(st, "sys", summary or "run_recon done")
        return _done(st, "run_recon", obs)

    @tool(
        name="run_vuln_scan",
        description=(
            "Run BugHunter vuln_scanner.sh against this run's recon dir "
            "(XSS/SQLi/SSTI/nuclei/CMS checks). Prefer after run_recon. "
            "Use full=true for deepest scan; quick=true for a fast subset."
        ),
        scope="read",
        execution_timeout_ms=600_000,
    )
    async def run_vuln_scan(
        ctx: ToolContext,
        quick: bool = False,
        full: bool = False,
    ) -> ToolResult:
        st = _state(ctx)
        from ..config import get_settings
        settings = get_settings()
        run_vuln_scan.execution_timeout_ms = _tool_timeout_ms(settings)  # type: ignore[attr-defined]
        _emit_log(st, "sys", f"run_vuln_scan quick={quick} full={full}")
        obs, _ran = await arsenal.vuln_scan(
            st.workdir, quick=quick, full=full, settings=settings, on_log=st.on_log,
        )
        _emit_log(st, "sys", obs.split("\n", 1)[0])
        return _done(st, "run_vuln_scan", obs, is_error="soft-skipped" in obs and "not found" in obs)

    @tool(
        name="run_parallel_hunt_wave",
        description=(
            "PERFORMANCE: after run_recon, fan-out independent scanners in parallel "
            "(verify_high_signal + vuln_scan + cors + secrets + api_fuzz). "
            "Prefer this INSTEAD of calling those tools one-by-one. "
            "Then optionally deepen with sqlmap/CMS if wave finds signals."
        ),
        scope="read",
        execution_timeout_ms=600_000,
    )
    async def run_parallel_hunt_wave_tool(ctx: ToolContext) -> ToolResult:
        st = _state(ctx)
        from ..config import get_settings
        settings = get_settings()
        run_parallel_hunt_wave_tool.execution_timeout_ms = _tool_timeout_ms(settings)  # type: ignore[attr-defined]
        _emit_log(st, "sys", "run_parallel_hunt_wave …")
        result = await run_parallel_hunt_wave(st, settings=settings)
        obs = result.get("summary") or "parallel_hunt_wave done"
        for r in result.get("results") or []:
            chunk = str((r or {}).get("obs") or "")[:400]
            if chunk:
                obs += f"\n--- {r.get('name')} ---\n{chunk}"
        _emit_log(st, "stdout", obs[:2000])
        return _done(st, "run_parallel_hunt_wave", obs)

    @tool(
        name="run_verify_leads",
        description=(
            "Deterministic high-signal HTTP PoCs: secrets (.env/debug), XSS reflection, "
            "SQLi errors, IDOR, path traversal, open redirect, default creds. "
            "REQUIRED after recon when sensitive paths or params exist — scanners often miss these."
        ),
        scope="read",
        execution_timeout_ms=120_000,
    )
    async def run_verify_leads(ctx: ToolContext) -> ToolResult:
        st = _state(ctx)
        _emit_log(st, "sys", "run_verify_leads …")
        try:
            result = await verify_high_signal(
                st.target,
                recon_dir=recon_dir(st.workdir),
            )
        except Exception as exc:  # noqa: BLE001
            return _done(st, "run_verify_leads", f"run_verify_leads error: {exc}", is_error=True)
        leads = list(result.get("leads") or [])
        for item in leads_to_findings_log(leads):
            st.memory.findings_log.append({
                **item,
                "ts": __import__("datetime").datetime.now().isoformat(),
            })
        # Persist JSON for report / triage
        out = findings_dir(st.workdir) / "verify_high_signal.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        lines = [result.get("summary") or "verify_high_signal done"]
        for lead in leads[:12]:
            lines.append(
                f"- [{lead.get('severity')}] {lead.get('vul_class')} @ {lead.get('path')}: "
                f"{str(lead.get('evidence') or '')[:160]}"
            )
        obs = "\n".join(lines)
        _emit_log(st, "stdout", obs[:1500])
        return _done(st, "run_verify_leads", obs)

    @tool(
        name="run_js_analysis",
        description=(
            "Analyse JS bundles from recon for secrets/endpoints/GraphQL hints "
            "(secrets_hunter js-bundle + recon/js summary)."
        ),
        scope="read",
        execution_timeout_ms=600_000,
    )
    async def run_js_analysis(ctx: ToolContext) -> ToolResult:
        st = _state(ctx)
        from ..config import get_settings
        settings = get_settings()
        run_js_analysis.execution_timeout_ms = _tool_timeout_ms(settings)  # type: ignore[attr-defined]
        _emit_log(st, "sys", "run_js_analysis …")
        obs, _ = await arsenal.js_analysis(st.workdir, settings=settings, on_log=st.on_log)
        return _done(st, "run_js_analysis", obs)

    @tool(
        name="run_secret_hunt",
        description=(
            "Scan recon artifacts for leaked secrets (trufflehog/noseyparker/gitleaks via secrets_hunter)."
        ),
        scope="read",
        execution_timeout_ms=600_000,
    )
    async def run_secret_hunt(ctx: ToolContext) -> ToolResult:
        st = _state(ctx)
        from ..config import get_settings
        settings = get_settings()
        run_secret_hunt.execution_timeout_ms = _tool_timeout_ms(settings)  # type: ignore[attr-defined]
        _emit_log(st, "sys", "run_secret_hunt …")
        obs, _ = await arsenal.secret_hunt(st.workdir, settings=settings, on_log=st.on_log)
        return _done(st, "run_secret_hunt", obs)

    @tool(
        name="run_param_discovery",
        description="Discover GET parameters (arjun/x8 via param_discovery.sh) on live hosts.",
        scope="read",
        execution_timeout_ms=600_000,
    )
    async def run_param_discovery(ctx: ToolContext) -> ToolResult:
        st = _state(ctx)
        from ..config import get_settings
        settings = get_settings()
        run_param_discovery.execution_timeout_ms = _tool_timeout_ms(settings)  # type: ignore[attr-defined]
        _emit_log(st, "sys", "run_param_discovery …")
        obs, _ = await arsenal.param_discovery(st.workdir, settings=settings, on_log=st.on_log)
        return _done(st, "run_param_discovery", obs)

    @tool(
        name="run_post_param_discovery",
        description=(
            "Discover POST form parameters with arjun. Pass cookies if behind auth. "
            "Useful for login/Java/Spring forms."
        ),
        scope="read",
        execution_timeout_ms=600_000,
    )
    async def run_post_param_discovery(ctx: ToolContext, cookies: str = "") -> ToolResult:
        st = _state(ctx)
        from ..config import get_settings
        settings = get_settings()
        run_post_param_discovery.execution_timeout_ms = _tool_timeout_ms(settings)  # type: ignore[attr-defined]
        ck = cookies or st.cookies
        if cookies:
            st.cookies = cookies
        _emit_log(st, "sys", "run_post_param_discovery …")
        obs, _ = await arsenal.post_param_discovery(
            st.workdir, cookies=ck, surface=st.surface, settings=settings, on_log=st.on_log,
        )
        return _done(st, "run_post_param_discovery", obs)

    @tool(
        name="run_api_fuzz",
        description=(
            "Fuzz API endpoints for IDOR/auth bypass (zero_day_fuzzer + nuclei api/idor tags)."
        ),
        scope="read",
        execution_timeout_ms=600_000,
    )
    async def run_api_fuzz(ctx: ToolContext) -> ToolResult:
        st = _state(ctx)
        from ..config import get_settings
        settings = get_settings()
        run_api_fuzz.execution_timeout_ms = _tool_timeout_ms(settings)  # type: ignore[attr-defined]
        _emit_log(st, "sys", "run_api_fuzz …")
        obs, _ = await arsenal.api_fuzz(st.workdir, settings=settings, on_log=st.on_log)
        return _done(st, "run_api_fuzz", obs)

    @tool(
        name="run_cors_check",
        description="Test live hosts for CORS misconfigurations (cors_scanner.py).",
        scope="read",
        execution_timeout_ms=300_000,
    )
    async def run_cors_check(ctx: ToolContext) -> ToolResult:
        st = _state(ctx)
        from ..config import get_settings
        settings = get_settings()
        _emit_log(st, "sys", "run_cors_check …")
        obs, _ = await arsenal.cors_check(
            st.workdir, cookies=st.cookies, settings=settings, on_log=st.on_log,
        )
        return _done(st, "run_cors_check", obs)

    @tool(
        name="run_cms_exploit",
        description="CMS-specific nuclei checks (Drupal/WordPress/Joomla) when CMS detected.",
        scope="read",
        execution_timeout_ms=600_000,
    )
    async def run_cms_exploit(ctx: ToolContext) -> ToolResult:
        st = _state(ctx)
        from ..config import get_settings
        settings = get_settings()
        run_cms_exploit.execution_timeout_ms = _tool_timeout_ms(settings)  # type: ignore[attr-defined]
        _emit_log(st, "sys", "run_cms_exploit …")
        obs, _ = await arsenal.cms_exploit(st.workdir, settings=settings, on_log=st.on_log)
        return _done(st, "run_cms_exploit", obs)

    @tool(
        name="run_rce_scan",
        description="RCE vector scan via nuclei (Log4Shell/SSTI/Tomcat/JBoss tags).",
        scope="read",
        execution_timeout_ms=600_000,
    )
    async def run_rce_scan(ctx: ToolContext) -> ToolResult:
        st = _state(ctx)
        from ..config import get_settings
        settings = get_settings()
        run_rce_scan.execution_timeout_ms = _tool_timeout_ms(settings)  # type: ignore[attr-defined]
        _emit_log(st, "sys", "run_rce_scan …")
        obs, _ = await arsenal.rce_scan(st.workdir, settings=settings, on_log=st.on_log)
        return _done(st, "run_rce_scan", obs)

    @tool(
        name="run_sqlmap_targeted",
        description="sqlmap against parameterized URLs from recon (capped list, batch mode).",
        scope="read",
        execution_timeout_ms=600_000,
    )
    async def run_sqlmap_targeted(ctx: ToolContext) -> ToolResult:
        st = _state(ctx)
        from ..config import get_settings
        settings = get_settings()
        run_sqlmap_targeted.execution_timeout_ms = _tool_timeout_ms(settings)  # type: ignore[attr-defined]
        _emit_log(st, "sys", "run_sqlmap_targeted …")
        obs, _ = await arsenal.sqlmap_targeted(st.workdir, settings=settings, on_log=st.on_log)
        return _done(st, "run_sqlmap_targeted", obs)

    @tool(
        name="run_sqlmap_on_file",
        description="Run sqlmap against a raw HTTP request file inside this run's workdir.",
        scope="read",
        execution_timeout_ms=600_000,
    )
    async def run_sqlmap_on_file(
        ctx: ToolContext,
        request_file: str,
        level: int = 5,
        risk: int = 3,
    ) -> ToolResult:
        st = _state(ctx)
        from ..config import get_settings
        settings = get_settings()
        run_sqlmap_on_file.execution_timeout_ms = _tool_timeout_ms(settings)  # type: ignore[attr-defined]
        path = Path(request_file)
        try:
            path.resolve().relative_to(st.workdir.resolve())
        except ValueError:
            obs = f"run_sqlmap_on_file: refused — path outside run workdir: {request_file}"
            return _done(st, "run_sqlmap_on_file", obs, is_error=True)
        if not path.is_file():
            obs = f"run_sqlmap_on_file: file not found: {request_file}"
            return _done(st, "run_sqlmap_on_file", obs, is_error=True)
        _emit_log(st, "sys", f"run_sqlmap_on_file → {path}")
        obs, _ = await arsenal.sqlmap_on_file(
            st.workdir, path, level=level, risk=risk, settings=settings, on_log=st.on_log,
        )
        return _done(st, "run_sqlmap_on_file", obs)

    @tool(
        name="run_jwt_audit",
        description="Audit JWT tokens found in recon artifacts (alg confusion / claims analysis).",
        scope="read",
        execution_timeout_ms=300_000,
    )
    async def run_jwt_audit(ctx: ToolContext) -> ToolResult:
        st = _state(ctx)
        from ..config import get_settings
        settings = get_settings()
        _emit_log(st, "sys", "run_jwt_audit …")
        obs, _ = await arsenal.jwt_audit(st.workdir, settings=settings, on_log=st.on_log)
        return _done(st, "run_jwt_audit", obs)

    @tool(
        name="read_recon_summary",
        description="Summarize current recon data for this run (live hosts, URLs, nuclei, exposure).",
        scope="read",
    )
    async def read_recon_summary(ctx: ToolContext) -> ToolResult:
        st = _state(ctx)
        body = _summarize_recon_dir(recon_dir(st.workdir))
        if st.surface:
            body = f"Probe: {st.surface.get('summary')}\n{body}"
        st.memory.add_observation("read_recon_summary", body)
        st.memory.save()
        _emit_log(st, "stdout", body[:500])
        return ToolResult(content=body, summary="read_recon_summary")

    @tool(
        name="read_findings_summary",
        description="Summarize vulnerability findings discovered so far in this run.",
        scope="read",
    )
    async def read_findings_summary(ctx: ToolContext) -> ToolResult:
        st = _state(ctx)
        parts = [st.memory.findings_summary()]
        fdir = findings_dir(st.workdir)
        parts.append("findings tree:\n" + arsenal.summarize_findings_tree(fdir))
        fsum = fdir / "summary.txt"
        tail = _tail_file(fsum, 30)
        if tail:
            parts.append("findings/summary.txt:\n" + tail)
        body = "\n\n".join(parts)
        st.memory.add_observation("read_findings_summary", body)
        st.memory.save()
        _emit_log(st, "stdout", body[:500])
        return ToolResult(content=body, summary="read_findings_summary")

    @tool(
        name="update_working_memory",
        description="Update persistent working notes for this target (survives across steps).",
        scope="write",
    )
    async def update_working_memory(ctx: ToolContext, notes: str) -> ToolResult:
        st = _state(ctx)
        st.memory.working_memory = notes or ""
        st.memory.record_step("update_working_memory", f"notes ({len(notes or '')} chars)")
        _emit_log(st, "sys", f"working_memory updated ({len(notes or '')} chars)")
        return ToolResult(
            content=f"Working memory updated ({len(notes or '')} chars).",
            summary="update_working_memory",
        )

    @tool(
        name="finish",
        description=(
            "Signal hunt complete. Call after recon + run_parallel_hunt_wave "
            "(or equivalent deep tools) finished. Provide a brief verdict."
        ),
        scope="read",
    )
    async def finish(ctx: ToolContext, verdict: str) -> ToolResult:
        st = _state(ctx)
        st.verdict = (verdict or "").strip()
        obs = f"FINISH: {st.verdict or 'Hunt complete.'}"
        st.memory.record_step("finish", obs)
        _emit_log(st, "sys", obs)
        return ToolResult(content=obs, summary=obs)

    return {
        "bh_http_probe": bh_http_probe,
        "run_recon": run_recon,
        "run_parallel_hunt_wave": run_parallel_hunt_wave_tool,
        "run_vuln_scan": run_vuln_scan,
        "run_verify_leads": run_verify_leads,
        "run_js_analysis": run_js_analysis,
        "run_secret_hunt": run_secret_hunt,
        "run_param_discovery": run_param_discovery,
        "run_post_param_discovery": run_post_param_discovery,
        "run_api_fuzz": run_api_fuzz,
        "run_cors_check": run_cors_check,
        "run_cms_exploit": run_cms_exploit,
        "run_rce_scan": run_rce_scan,
        "run_sqlmap_targeted": run_sqlmap_targeted,
        "run_sqlmap_on_file": run_sqlmap_on_file,
        "run_jwt_audit": run_jwt_audit,
        "read_recon_summary": read_recon_summary,
        "read_findings_summary": read_findings_summary,
        "update_working_memory": update_working_memory,
        "finish": finish,
    }
