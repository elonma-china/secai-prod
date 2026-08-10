"""Bughunt Linch tools — port of claude-bug-bounty ``agent.py`` ToolDispatcher.

Tools read ``ToolContext.deps`` (:class:`BughuntRunState`) for per-run isolation
(multi-user / CCU). Deep arsenal tools soft-skip when scripts/binaries are missing.
"""
from __future__ import annotations

import asyncio
import json
import logging
import shutil
from pathlib import Path
from typing import Any

from linch.tools.base import ToolContext, ToolResult
from linch.tools.function import tool

from .engine import bugbounty_root, run_recon_engine
from .memory import BughuntRunState
from .recon import probe_http_surface
from .workdir import findings_dir, recon_dir

logger = logging.getLogger(__name__)

# Tool names exposed to the ReAct agent (profile + wf.agent filter).
AGENT_TOOL_NAMES: tuple[str, ...] = (
    "bh_http_probe",
    "run_recon",
    "run_vuln_scan",
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
        live = out / "live" / "hosts.txt"
    if live.is_file():
        n = sum(1 for ln in live.read_text(encoding="utf-8", errors="replace").splitlines() if ln.strip())
        lines.append(f"Live hosts: {n}")
    for rel in ("urls/all.txt", "urls/urls.txt", "urls.txt"):
        fp = out / rel
        if fp.is_file():
            n = sum(1 for ln in fp.read_text(encoding="utf-8", errors="replace").splitlines() if ln.strip())
            lines.append(f"URLs: {n}")
            break
    for rel in ("nuclei/nuclei.txt", "nuclei.txt"):
        fp = out / rel
        if fp.is_file():
            n = sum(1 for ln in fp.read_text(encoding="utf-8", errors="replace").splitlines() if ln.strip())
            lines.append(f"Nuclei lines: {n}")
            break
    exp = out / "exposure"
    if exp.is_dir():
        hits = [p.name for p in exp.iterdir() if p.is_file() and p.stat().st_size > 0][:8]
        if hits:
            lines.append(f"Exposure files: {', '.join(hits)}")
    return "\n".join(lines) if lines else "(recon dir empty / tools soft-skipped)"


def _tail_file(path: Path, limit: int = 40) -> str:
    if not path.is_file():
        return ""
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    return "\n".join(lines[-limit:])


async def _run_script(
    *,
    script: Path,
    argv: list[str],
    cwd: Path,
    timeout_s: int,
    on_log: Any | None,
) -> tuple[int, str, str]:
    if not script.is_file():
        return 127, "", f"script missing: {script}"

    def _sync() -> tuple[int, str, str]:
        import subprocess
        proc = subprocess.Popen(
            ["bash", str(script), *argv],
            cwd=str(cwd),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
        out_chunks: list[str] = []
        err_chunks: list[str] = []
        assert proc.stdout and proc.stderr
        for line in proc.stdout:
            out_chunks.append(line)
            if on_log:
                on_log("stdout", line.rstrip("\n"))
        for line in proc.stderr:
            err_chunks.append(line)
            if on_log:
                on_log("stderr", line.rstrip("\n"))
        try:
            rc = proc.wait(timeout=timeout_s)
        except subprocess.TimeoutExpired:
            proc.kill()
            rc = 124
        return rc, "".join(out_chunks)[-8000:], "".join(err_chunks)[-4000:]

    return await asyncio.to_thread(_sync)


def _soft_skip(tool_name: str, reason: str) -> ToolResult:
    text = f"{tool_name}: soft-skipped — {reason}"
    return ToolResult(content=text, summary=text)


def build_bughunt_tools() -> dict[str, Any]:
    """Register BugHunter-style tools for the Linch agent."""

    @tool(
        name="bh_http_probe",
        description=(
            "Quick deterministic HTTP surface probe (headers, common paths, robots, login). "
            "Use for a fast reachability check; prefer run_recon for full arsenal."
        ),
        scope="read",
    )
    async def bh_http_probe(ctx: ToolContext, target: str | None = None) -> ToolResult:
        st = _state(ctx)
        tgt = (target or st.target).strip()
        try:
            surface = await probe_http_surface(tgt)
        except Exception as exc:  # noqa: BLE001
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
        return ToolResult(content=obs, summary=f"bh_http_probe reachable={slim['reachable']}")

    @tool(
        name="run_recon",
        description=(
            "Run full BugHunter recon_engine (subfinder/httpx/katana/nmap/ffuf/nuclei when installed). "
            "MUST be first if no recon data exists. Writes into this run's isolated recon/ dir."
        ),
        scope="read",
    )
    async def run_recon(
        ctx: ToolContext,
        scope_lock: bool = False,
        max_urls: int = 100,  # noqa: ARG001 — reserved for BB parity
    ) -> ToolResult:
        st = _state(ctx)
        from ..config import get_settings
        settings = get_settings()
        seed: list[str] = []
        if st.surface.get("reachable") and st.surface.get("base_url"):
            seed = [str(st.surface["base_url"]).rstrip("/") + "/"]
        elif not (scope_lock or st.scope_lock):
            # light probe first to seed live URLs for IP/lab targets
            try:
                surface = await probe_http_surface(st.target)
                st.surface = surface
                if surface.get("reachable") and surface.get("base_url"):
                    seed = [str(surface["base_url"]).rstrip("/") + "/"]
            except Exception:  # noqa: BLE001
                pass
        out = recon_dir(st.workdir)
        try:
            result = await run_recon_engine(
                st.target,
                settings=settings,
                quick=True,
                seed_urls=seed,
                run_id=st.run_id,
                out_dir=out,
                on_log=st.on_log,
            )
        except Exception as exc:  # noqa: BLE001
            obs = f"run_recon error: {type(exc).__name__}: {exc}"
            st.memory.record_step("run_recon", obs)
            return ToolResult(content=obs, summary=obs, is_error=True)
        summary = result.get("summary_text") or result.get("reason") or result.get("error") or ""
        counts = result.get("counts") or {}
        obs = (
            f"run_recon: ok={result.get('ok')} skipped={result.get('skipped')}\n"
            f"{summary}\ncounts={counts}\n{_summarize_recon_dir(out)}"
        )
        st.memory.record_step("run_recon", obs)
        return ToolResult(content=obs, summary=summary or "run_recon done")

    @tool(
        name="run_vuln_scan",
        description=(
            "Run BugHunter vuln_scanner.sh against this run's recon dir "
            "(XSS/SQLi/SSTI/nuclei custom checks). Prefer after run_recon."
        ),
        scope="read",
    )
    async def run_vuln_scan(
        ctx: ToolContext,
        quick: bool = True,
        full: bool = False,
    ) -> ToolResult:
        st = _state(ctx)
        from ..config import get_settings
        settings = get_settings()
        bb = bugbounty_root(settings)
        script = bb / "tools" / "vuln_scanner.sh"
        if not script.is_file():
            obs = "run_vuln_scan: soft-skipped — vuln_scanner.sh not found"
            st.memory.record_step("run_vuln_scan", obs)
            return _soft_skip("run_vuln_scan", "vuln_scanner.sh not found")
        rdir = recon_dir(st.workdir)
        argv = [str(rdir)]
        if quick and not full:
            argv.append("--quick")
        if full:
            argv.append("--full")
        timeout = int(getattr(settings, "bughunt_recon_timeout_s", None) or 600)
        rc, stdout, stderr = await _run_script(
            script=script, argv=argv, cwd=bb, timeout_s=timeout, on_log=st.on_log,
        )
        # Copy summary into findings if present under recon
        fdir = findings_dir(st.workdir)
        for cand in (rdir / "summary.txt", bb / "findings" / "summary.txt"):
            if cand.is_file():
                dest = fdir / "summary.txt"
                dest.write_text(cand.read_text(encoding="utf-8", errors="replace"), encoding="utf-8")
                break
        obs = (
            f"run_vuln_scan rc={rc}\n"
            f"{stdout[-4000:] or '(no stdout)'}\n"
            f"{stderr[-1500:]}"
        )
        st.memory.record_step("run_vuln_scan", obs)
        return ToolResult(
            content=obs,
            summary=f"run_vuln_scan rc={rc}",
            is_error=rc not in (0, 124) and "soft-skipped" not in obs,
        )

    def _make_soft_tool(name: str, description: str, need_bin: str | None = None):
        @tool(name=name, description=description, scope="read")
        async def _fn(ctx: ToolContext) -> ToolResult:
            st = _state(ctx)
            if need_bin and not shutil.which(need_bin):
                obs = f"{name}: soft-skipped — `{need_bin}` not on PATH"
                st.memory.record_step(name, obs)
                return _soft_skip(name, f"{need_bin} missing")
            # Prefer summarizing existing recon/findings over inventing scan output.
            rsum = _summarize_recon_dir(recon_dir(st.workdir))
            obs = (
                f"{name}: soft-skipped — full BB hunt.py hook not wired in gateway; "
                f"use run_recon/run_vuln_scan evidence.\nRecon snapshot:\n{rsum}"
            )
            st.memory.record_step(name, obs)
            return ToolResult(content=obs, summary=f"{name} soft-skipped")

        return _fn

    run_js_analysis = _make_soft_tool(
        "run_js_analysis",
        "Analyse JS bundles from recon for secrets/endpoints/GraphQL hints.",
    )
    run_secret_hunt = _make_soft_tool(
        "run_secret_hunt",
        "Scan for leaked secrets (TruffleHog/GitHound patterns) on recon artifacts.",
        need_bin=None,
    )
    run_param_discovery = _make_soft_tool(
        "run_param_discovery",
        "Discover GET parameters (arjun/paramspider) on live hosts.",
        need_bin="arjun",
    )
    run_api_fuzz = _make_soft_tool(
        "run_api_fuzz",
        "Fuzz API endpoints for IDOR/auth bypass when APIs found in recon.",
    )
    run_cors_check = _make_soft_tool(
        "run_cors_check",
        "Test CORS misconfigurations on live hosts.",
    )
    run_cms_exploit = _make_soft_tool(
        "run_cms_exploit",
        "CMS-specific checks (Drupal/WordPress/Joomla) when CMS detected.",
    )
    run_rce_scan = _make_soft_tool(
        "run_rce_scan",
        "RCE vector scan (Log4Shell/SSTI/Tomcat) when Java stacks detected.",
    )
    run_sqlmap_targeted = _make_soft_tool(
        "run_sqlmap_targeted",
        "sqlmap against parameterized URLs from recon.",
        need_bin="sqlmap",
    )
    run_jwt_audit = _make_soft_tool(
        "run_jwt_audit",
        "Audit JWT tokens found in recon for alg confusion / weak secrets.",
    )

    @tool(
        name="run_post_param_discovery",
        description=(
            "Discover POST form parameters (lightpanda/arjun). Pass cookies if behind auth."
        ),
        scope="read",
    )
    async def run_post_param_discovery(ctx: ToolContext, cookies: str = "") -> ToolResult:
        st = _state(ctx)
        _ = cookies or st.cookies
        obs = (
            "run_post_param_discovery: soft-skipped — BB post-param hook not wired; "
            f"cookies_set={bool(cookies or st.cookies)}\n{_summarize_recon_dir(recon_dir(st.workdir))}"
        )
        st.memory.record_step("run_post_param_discovery", obs)
        return ToolResult(content=obs, summary="run_post_param_discovery soft-skipped")

    @tool(
        name="run_sqlmap_on_file",
        description="Run sqlmap against a raw HTTP request file (Burp-style).",
        scope="read",
    )
    async def run_sqlmap_on_file(
        ctx: ToolContext,
        request_file: str,
        level: int = 5,
        risk: int = 3,
    ) -> ToolResult:
        st = _state(ctx)
        path = Path(request_file)
        # Only allow files inside this run's workdir (CCU / path traversal safety).
        try:
            path.resolve().relative_to(st.workdir.resolve())
        except ValueError:
            obs = f"run_sqlmap_on_file: refused — path outside run workdir: {request_file}"
            st.memory.record_step("run_sqlmap_on_file", obs)
            return ToolResult(content=obs, summary=obs, is_error=True)
        if not path.is_file():
            obs = f"run_sqlmap_on_file: file not found: {request_file}"
            st.memory.record_step("run_sqlmap_on_file", obs)
            return ToolResult(content=obs, summary=obs, is_error=True)
        if not shutil.which("sqlmap"):
            obs = "run_sqlmap_on_file: soft-skipped — sqlmap not on PATH"
            st.memory.record_step("run_sqlmap_on_file", obs)
            return _soft_skip("run_sqlmap_on_file", "sqlmap missing")
        obs = (
            f"run_sqlmap_on_file: soft-skipped — would run sqlmap -r {path} "
            f"--level={level} --risk={risk} (gateway defers full sqlmap exec)"
        )
        st.memory.record_step("run_sqlmap_on_file", obs)
        return ToolResult(content=obs, summary=obs)

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
        return ToolResult(content=body, summary="read_recon_summary")

    @tool(
        name="read_findings_summary",
        description="Summarize vulnerability findings discovered so far in this run.",
        scope="read",
    )
    async def read_findings_summary(ctx: ToolContext) -> ToolResult:
        st = _state(ctx)
        parts = [st.memory.findings_summary()]
        fsum = findings_dir(st.workdir) / "summary.txt"
        tail = _tail_file(fsum, 30)
        if tail:
            parts.append("findings/summary.txt:\n" + tail)
        body = "\n\n".join(parts)
        st.memory.add_observation("read_findings_summary", body)
        st.memory.save()
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
        return ToolResult(
            content=f"Working memory updated ({len(notes or '')} chars).",
            summary="update_working_memory",
        )

    @tool(
        name="finish",
        description=(
            "Signal hunt complete. Call when high-priority tools ran or no further surface. "
            "Provide a brief verdict."
        ),
        scope="read",
    )
    async def finish(ctx: ToolContext, verdict: str) -> ToolResult:
        st = _state(ctx)
        st.verdict = (verdict or "").strip()
        obs = f"FINISH: {st.verdict or 'Hunt complete.'}"
        st.memory.record_step("finish", obs)
        return ToolResult(content=obs, summary=obs)

    return {
        "bh_http_probe": bh_http_probe,
        "run_recon": run_recon,
        "run_vuln_scan": run_vuln_scan,
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
