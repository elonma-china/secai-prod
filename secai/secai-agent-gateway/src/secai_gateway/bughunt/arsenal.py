"""BugHunter arsenal adapters — run real ``claude-bug-bounty/tools/*`` into a per-run workdir.

Soft-skip only when the script/binary is missing. All findings land under
``data/bughunt/{run_id}/findings/`` (CCU-safe).
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import shutil
from pathlib import Path
from typing import Any, Callable

from .engine import bugbounty_root
from .workdir import findings_dir, recon_dir

logger = logging.getLogger(__name__)

LogFn = Callable[[str, str], None]

JWT_RE = re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b")
MAX_LIVE_URLS = 40
MAX_PARAM_URLS = 30
MAX_API_URLS = 25


def _timeout_s(settings: Any | None) -> int:
    if settings is None:
        from ..config import get_settings
        settings = get_settings()
    return int(getattr(settings, "bughunt_recon_timeout_s", None) or 600)


def tools_dir(settings: Any | None = None) -> Path:
    return bugbounty_root(settings) / "tools"


def summarize_findings_tree(fdir: Path, *, limit: int = 40) -> str:
    if not fdir.is_dir():
        return "(no findings dir)"
    lines: list[str] = []
    for path in sorted(fdir.rglob("*")):
        if not path.is_file() or path.stat().st_size == 0:
            continue
        rel = path.relative_to(fdir)
        n = sum(1 for ln in path.read_text(encoding="utf-8", errors="replace").splitlines() if ln.strip())
        lines.append(f"{rel}: {n} lines ({path.stat().st_size}B)")
        if len(lines) >= limit:
            break
    return "\n".join(lines) if lines else "(findings empty)"


def _read_lines(path: Path, *, limit: int | None = None) -> list[str]:
    if not path.is_file():
        return []
    out: list[str] = []
    for ln in path.read_text(encoding="utf-8", errors="replace").splitlines():
        s = ln.strip()
        if not s or s.startswith("#"):
            continue
        # httpx full lines: URL first token
        if s.startswith("http://") or s.startswith("https://"):
            out.append(s.split()[0])
        elif "://" in s:
            out.append(s.split()[0])
        else:
            out.append(s)
        if limit is not None and len(out) >= limit:
            break
    return out


def live_urls(rdir: Path, *, limit: int = MAX_LIVE_URLS) -> list[str]:
    for rel in ("live/urls.txt", "live/httpx_full.txt", "urls/all.txt"):
        urls = _read_lines(rdir / rel, limit=limit)
        if urls:
            return urls
    return []


def param_urls(rdir: Path, *, limit: int = MAX_PARAM_URLS) -> list[str]:
    for rel in ("urls/with_params.txt", "params/all.txt", "urls/all.txt"):
        urls = _read_lines(rdir / rel, limit=limit)
        if urls:
            return [u for u in urls if "?" in u][:limit] or urls[:limit]
    return []


def api_urls(rdir: Path, *, limit: int = MAX_API_URLS) -> list[str]:
    for rel in ("urls/api_endpoints.txt", "js/endpoints.txt", "live/urls.txt"):
        urls = _read_lines(rdir / rel, limit=limit)
        if urls:
            return urls
    return live_urls(rdir, limit=limit)


def write_url_list(path: Path, urls: list[str]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(urls) + ("\n" if urls else ""), encoding="utf-8")
    return path


def extract_jwts(rdir: Path, *, limit: int = 20) -> list[str]:
    found: list[str] = []
    seen: set[str] = set()
    for path in rdir.rglob("*"):
        if not path.is_file() or path.stat().st_size == 0 or path.stat().st_size > 2_000_000:
            continue
        if path.suffix.lower() in {".png", ".jpg", ".jpeg", ".gif", ".woff", ".woff2", ".zip"}:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for m in JWT_RE.finditer(text):
            tok = m.group(0)
            if tok in seen:
                continue
            seen.add(tok)
            found.append(tok)
            if len(found) >= limit:
                return found
    return found


async def run_argv(
    argv: list[str],
    *,
    cwd: Path,
    timeout_s: int,
    on_log: LogFn | None = None,
    env: dict[str, str] | None = None,
    label: str = "arsenal",
) -> tuple[int, str, str]:
    """Run argv with concurrent stdout/stderr pumps → SSE."""
    if not argv:
        return 127, "", "empty argv"
    full = list(argv)
    if shutil.which("stdbuf") and full[0] not in {"stdbuf"}:
        full = ["stdbuf", "-oL", "-eL", *full]

    child_env = os.environ.copy()
    if env:
        child_env.update({k: str(v) for k, v in env.items() if v is not None})
    go_bin = str(Path.home() / "go" / "bin")
    child_env["PATH"] = f"{go_bin}:/usr/local/bin:{child_env.get('PATH', '')}"

    if on_log:
        on_log("sys", f"{label}: {' '.join(argv[:8])}{'…' if len(argv) > 8 else ''}")

    proc = await asyncio.create_subprocess_exec(
        *full,
        cwd=str(cwd),
        env=child_env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    out_chunks: list[str] = []
    err_chunks: list[str] = []

    async def _pump(reader: asyncio.StreamReader | None, name: str, chunks: list[str]) -> None:
        if reader is None:
            return
        while True:
            line_b = await reader.readline()
            if not line_b:
                break
            line = line_b.decode("utf-8", errors="replace").rstrip("\n")
            chunks.append(line + "\n")
            if on_log and line:
                try:
                    on_log(name, line)
                except Exception:  # noqa: BLE001
                    logger.debug("on_log failed", exc_info=True)

    pumps = [
        asyncio.create_task(_pump(proc.stdout, "stdout", out_chunks)),
        asyncio.create_task(_pump(proc.stderr, "stderr", err_chunks)),
    ]
    timed_out = False
    try:
        await asyncio.wait_for(proc.wait(), timeout=timeout_s)
    except asyncio.TimeoutError:
        timed_out = True
        proc.kill()
        try:
            await proc.wait()
        except Exception:  # noqa: BLE001
            pass
        if on_log:
            on_log("sys", f"{label} timed out after {timeout_s}s")
    await asyncio.gather(*pumps, return_exceptions=True)
    rc = 124 if timed_out else (proc.returncode if proc.returncode is not None else 1)
    return rc, "".join(out_chunks)[-8000:], "".join(err_chunks)[-4000:]


async def run_bash_script(
    script: Path,
    argv: list[str],
    *,
    cwd: Path,
    timeout_s: int,
    on_log: LogFn | None = None,
    env: dict[str, str] | None = None,
    label: str | None = None,
) -> tuple[int, str, str]:
    if not script.is_file():
        return 127, "", f"script missing: {script}"
    return await run_argv(
        ["bash", str(script), *argv],
        cwd=cwd,
        timeout_s=timeout_s,
        on_log=on_log,
        env=env,
        label=label or script.name,
    )


def _obs(name: str, rc: int, stdout: str, stderr: str, extra: str = "") -> str:
    parts = [f"{name} rc={rc}"]
    if extra:
        parts.append(extra)
    if stdout.strip():
        parts.append(stdout[-4000:])
    if stderr.strip():
        parts.append(f"stderr:\n{stderr[-1500:]}")
    return "\n".join(parts)


# ── High-level tool runners ───────────────────────────────────────────────────


async def secret_hunt(
    workdir: Path,
    *,
    settings: Any | None = None,
    on_log: LogFn | None = None,
) -> tuple[str, bool]:
    td = tools_dir(settings)
    script = td / "secrets_hunter.sh"
    if not script.is_file():
        return "run_secret_hunt: soft-skipped — secrets_hunter.sh not found", False
    rdir = recon_dir(workdir)
    out = findings_dir(workdir) / "secrets"
    out.mkdir(parents=True, exist_ok=True)
    rc, so, se = await run_bash_script(
        script,
        ["--js-bundle", str(rdir), "--out", str(out)],
        cwd=bugbounty_root(settings),
        timeout_s=_timeout_s(settings),
        on_log=on_log,
        env={"SECRETS_OUT_DIR": str(out)},
        label="secrets_hunter",
    )
    # Also filesystem-scan recon dir if trufflehog available and js pass was thin
    extra = summarize_findings_tree(out)
    return _obs("run_secret_hunt", rc, so, se, extra), True


async def js_analysis(
    workdir: Path,
    *,
    settings: Any | None = None,
    on_log: LogFn | None = None,
) -> tuple[str, bool]:
    """JS surface summary + secrets_hunter js-bundle pass."""
    rdir = recon_dir(workdir)
    fdir = findings_dir(workdir) / "js"
    fdir.mkdir(parents=True, exist_ok=True)
    notes: list[str] = []
    js_files = _read_lines(rdir / "urls" / "js_files.txt", limit=50)
    endpoints = _read_lines(rdir / "js" / "endpoints.txt", limit=50)
    notes.append(f"js_files listed: {len(js_files)}")
    notes.append(f"js endpoints listed: {len(endpoints)}")
    if endpoints:
        write_url_list(fdir / "endpoints.txt", endpoints)
    # Dump small sample of js/ tree
    js_dir = rdir / "js"
    if js_dir.is_dir():
        samples = [p.name for p in js_dir.iterdir() if p.is_file()][:20]
        notes.append(f"recon/js artifacts: {', '.join(samples) or '(none)'}")
    secret_obs, ran = await secret_hunt(workdir, settings=settings, on_log=on_log)
    notes.append(secret_obs)
    (fdir / "summary.txt").write_text("\n".join(notes), encoding="utf-8")
    return "run_js_analysis:\n" + "\n".join(notes), ran or True


async def param_discovery(
    workdir: Path,
    *,
    settings: Any | None = None,
    on_log: LogFn | None = None,
) -> tuple[str, bool]:
    td = tools_dir(settings)
    script = td / "param_discovery.sh"
    if not script.is_file():
        return "run_param_discovery: soft-skipped — param_discovery.sh not found", False
    if not shutil.which("arjun") and not shutil.which("x8"):
        return "run_param_discovery: soft-skipped — arjun/x8 not on PATH", False
    rdir = recon_dir(workdir)
    urls = live_urls(rdir, limit=15)
    if not urls:
        return "run_param_discovery: no live URLs in recon — run_recon first", False
    list_path = rdir / "params" / "discovery_targets.txt"
    write_url_list(list_path, urls)
    out = findings_dir(workdir) / "params"
    out.mkdir(parents=True, exist_ok=True)
    rc, so, se = await run_bash_script(
        script,
        ["-l", str(list_path)],
        cwd=bugbounty_root(settings),
        timeout_s=_timeout_s(settings),
        on_log=on_log,
        env={"PARAM_OUT_DIR": str(out)},
        label="param_discovery",
    )
    return _obs("run_param_discovery", rc, so, se, summarize_findings_tree(out)), True


async def post_param_discovery(
    workdir: Path,
    *,
    cookies: str = "",
    surface: dict[str, Any] | None = None,
    settings: Any | None = None,
    on_log: LogFn | None = None,
) -> tuple[str, bool]:
    if not shutil.which("arjun"):
        return "run_post_param_discovery: soft-skipped — arjun not on PATH", False
    rdir = recon_dir(workdir)
    fdir = findings_dir(workdir) / "params_post"
    fdir.mkdir(parents=True, exist_ok=True)
    urls: list[str] = []
    base = (surface or {}).get("base_url")
    if base:
        urls.append(str(base).rstrip("/") + "/login")
        urls.append(str(base).rstrip("/") + "/")
    urls.extend(live_urls(rdir, limit=5))
    # dedupe
    seen: set[str] = set()
    targets = []
    for u in urls:
        if u not in seen:
            seen.add(u)
            targets.append(u)
    if not targets:
        return "run_post_param_discovery: no URLs to probe", False
    chunks: list[str] = []
    for url in targets[:5]:
        argv = ["arjun", "-u", url, "-m", "POST", "-oB", str(fdir / "arjun_post.json")]
        if cookies:
            argv.extend(["--headers", f"Cookie: {cookies}"])
        rc, so, se = await run_argv(
            argv,
            cwd=bugbounty_root(settings),
            timeout_s=min(180, _timeout_s(settings)),
            on_log=on_log,
            label=f"arjun-post:{url}",
        )
        chunks.append(_obs(f"arjun POST {url}", rc, so, se))
    return "run_post_param_discovery:\n" + "\n".join(chunks), True


async def cors_check(
    workdir: Path,
    *,
    cookies: str = "",
    settings: Any | None = None,
    on_log: LogFn | None = None,
) -> tuple[str, bool]:
    td = tools_dir(settings)
    script = td / "cors_scanner.py"
    if not script.is_file():
        return "run_cors_check: soft-skipped — cors_scanner.py not found", False
    rdir = recon_dir(workdir)
    urls = live_urls(rdir, limit=20)
    if not urls:
        return "run_cors_check: no live URLs — run_recon first", False
    list_path = findings_dir(workdir) / "cors" / "targets.txt"
    write_url_list(list_path, urls)
    out_json = findings_dir(workdir) / "cors" / "results.json"
    out_json.parent.mkdir(parents=True, exist_ok=True)
    argv = [sys_executable(), str(script), "-l", str(list_path), "--json"]
    if cookies:
        argv.extend(["--cookie", cookies])
    rc, so, se = await run_argv(
        argv,
        cwd=bugbounty_root(settings),
        timeout_s=min(300, _timeout_s(settings)),
        on_log=on_log,
        label="cors_scanner",
    )
    if so.strip():
        out_json.write_text(so, encoding="utf-8")
    return _obs("run_cors_check", rc, so, se), True


def sys_executable() -> str:
    import sys
    return sys.executable


async def jwt_audit(
    workdir: Path,
    *,
    settings: Any | None = None,
    on_log: LogFn | None = None,
) -> tuple[str, bool]:
    td = tools_dir(settings)
    script = td / "jwt_scanner.py"
    if not script.is_file():
        return "run_jwt_audit: soft-skipped — jwt_scanner.py not found", False
    tokens = extract_jwts(recon_dir(workdir))
    if not tokens:
        return "run_jwt_audit: no JWT tokens found in recon artifacts", True
    fdir = findings_dir(workdir) / "jwt"
    fdir.mkdir(parents=True, exist_ok=True)
    chunks: list[str] = []
    for i, tok in enumerate(tokens[:10]):
        rc, so, se = await run_argv(
            [sys_executable(), str(script), tok, "--analyze"],
            cwd=bugbounty_root(settings),
            timeout_s=60,
            on_log=on_log,
            label=f"jwt_analyze_{i}",
        )
        (fdir / f"token_{i}_analyze.txt").write_text(so or se, encoding="utf-8")
        chunks.append(_obs(f"jwt[{i}]", rc, so, se))
    return f"run_jwt_audit: analyzed {min(len(tokens), 10)} token(s)\n" + "\n".join(chunks), True


async def nuclei_tags(
    workdir: Path,
    *,
    tags: str,
    label: str,
    settings: Any | None = None,
    on_log: LogFn | None = None,
    limit: int = MAX_LIVE_URLS,
) -> tuple[str, bool]:
    bin_path = shutil.which("nuclei")
    if not bin_path:
        home_n = Path.home() / "go" / "bin" / "nuclei"
        bin_path = str(home_n) if home_n.is_file() else ""
    if not bin_path:
        return f"{label}: soft-skipped — nuclei not on PATH", False
    rdir = recon_dir(workdir)
    urls = live_urls(rdir, limit=limit)
    if not urls:
        return f"{label}: no live URLs — run_recon first", False
    sub = findings_dir(workdir) / label.replace("run_", "")
    sub.mkdir(parents=True, exist_ok=True)
    targets = write_url_list(sub / "targets.txt", urls)
    out = sub / "nuclei.txt"
    argv = [
        bin_path, "-l", str(targets),
        "-tags", tags,
        "-severity", "medium,high,critical",
        "-silent", "-o", str(out),
    ]
    rc, so, se = await run_argv(
        argv,
        cwd=bugbounty_root(settings),
        timeout_s=_timeout_s(settings),
        on_log=on_log,
        label=label,
    )
    n = sum(1 for ln in out.read_text(encoding="utf-8", errors="replace").splitlines() if ln.strip()) if out.is_file() else 0
    return _obs(label, rc, so, se, f"nuclei hits: {n} → {out}"), True


async def cms_exploit(
    workdir: Path,
    *,
    settings: Any | None = None,
    on_log: LogFn | None = None,
) -> tuple[str, bool]:
    return await nuclei_tags(
        workdir,
        tags="cms,wordpress,drupal,joomla,wp-plugin",
        label="run_cms_exploit",
        settings=settings,
        on_log=on_log,
    )


async def rce_scan(
    workdir: Path,
    *,
    settings: Any | None = None,
    on_log: LogFn | None = None,
) -> tuple[str, bool]:
    return await nuclei_tags(
        workdir,
        tags="rce,log4shell,ssti,deserialization,tomcat,jboss",
        label="run_rce_scan",
        settings=settings,
        on_log=on_log,
    )


async def api_fuzz(
    workdir: Path,
    *,
    settings: Any | None = None,
    on_log: LogFn | None = None,
) -> tuple[str, bool]:
    td = tools_dir(settings)
    rdir = recon_dir(workdir)
    urls = api_urls(rdir)
    if not urls:
        return "run_api_fuzz: no API/live URLs — run_recon first", False
    fdir = findings_dir(workdir) / "api"
    fdir.mkdir(parents=True, exist_ok=True)
    write_url_list(fdir / "targets.txt", urls)
    zdf = td / "zero_day_fuzzer.py"
    chunks: list[str] = []
    if zdf.is_file():
        # Prefer recon-dir mode when available
        rc, so, se = await run_argv(
            [sys_executable(), str(zdf), "--recon-dir", str(rdir)],
            cwd=bugbounty_root(settings),
            timeout_s=min(400, _timeout_s(settings)),
            on_log=on_log,
            env={"FINDINGS_DIR": str(fdir)},
            label="zero_day_fuzzer",
        )
        chunks.append(_obs("zero_day_fuzzer", rc, so, se))
    # Always add nuclei API/IDOR pass when nuclei exists
    nuc_obs, nuc_ok = await nuclei_tags(
        workdir,
        tags="api,idor,auth-bypass,exposure",
        label="run_api_fuzz_nuclei",
        settings=settings,
        on_log=on_log,
        limit=MAX_API_URLS,
    )
    chunks.append(nuc_obs)
    if not zdf.is_file() and not nuc_ok:
        return "run_api_fuzz: soft-skipped — zero_day_fuzzer.py and nuclei missing", False
    return "run_api_fuzz:\n" + "\n".join(chunks), True


async def sqlmap_targeted(
    workdir: Path,
    *,
    settings: Any | None = None,
    on_log: LogFn | None = None,
) -> tuple[str, bool]:
    if not shutil.which("sqlmap"):
        return "run_sqlmap_targeted: soft-skipped — sqlmap not on PATH", False
    rdir = recon_dir(workdir)
    urls = param_urls(rdir, limit=MAX_PARAM_URLS)
    if not urls:
        return "run_sqlmap_targeted: no parameterized URLs in recon", False
    fdir = findings_dir(workdir) / "sqli"
    fdir.mkdir(parents=True, exist_ok=True)
    list_path = write_url_list(fdir / "targets.txt", urls)
    argv = [
        "sqlmap", "-m", str(list_path),
        "--batch", "--random-agent",
        "--level=2", "--risk=2",
        "--threads=2",
        f"--output-dir={fdir / 'sqlmap_out'}",
    ]
    rc, so, se = await run_argv(
        argv,
        cwd=bugbounty_root(settings),
        timeout_s=min(500, _timeout_s(settings)),
        on_log=on_log,
        label="sqlmap_targeted",
    )
    return _obs("run_sqlmap_targeted", rc, so, se, summarize_findings_tree(fdir)), True


async def sqlmap_on_file(
    workdir: Path,
    request_file: Path,
    *,
    level: int = 5,
    risk: int = 3,
    settings: Any | None = None,
    on_log: LogFn | None = None,
) -> tuple[str, bool]:
    if not shutil.which("sqlmap"):
        return "run_sqlmap_on_file: soft-skipped — sqlmap not on PATH", False
    fdir = findings_dir(workdir) / "sqli"
    fdir.mkdir(parents=True, exist_ok=True)
    argv = [
        "sqlmap", "-r", str(request_file),
        "--batch", "--random-agent",
        f"--level={int(level)}", f"--risk={int(risk)}",
        f"--output-dir={fdir / 'sqlmap_req'}",
    ]
    rc, so, se = await run_argv(
        argv,
        cwd=bugbounty_root(settings),
        timeout_s=min(500, _timeout_s(settings)),
        on_log=on_log,
        label="sqlmap_on_file",
    )
    return _obs("run_sqlmap_on_file", rc, so, se), True


async def vuln_scan(
    workdir: Path,
    *,
    quick: bool = False,
    full: bool = False,
    settings: Any | None = None,
    on_log: LogFn | None = None,
) -> tuple[str, bool]:
    td = tools_dir(settings)
    script = td / "vuln_scanner.sh"
    if not script.is_file():
        return "run_vuln_scan: soft-skipped — vuln_scanner.sh not found", False
    rdir = recon_dir(workdir)
    fdir = findings_dir(workdir)
    fdir.mkdir(parents=True, exist_ok=True)
    argv = [str(rdir)]
    if full:
        argv.append("--full")
    elif quick:
        argv.append("--quick")
    rc, so, se = await run_bash_script(
        script,
        argv,
        cwd=bugbounty_root(settings),
        timeout_s=_timeout_s(settings),
        on_log=on_log,
        env={"FINDINGS_OUT_DIR": str(fdir)},
        label="vuln_scanner",
    )
    return _obs("run_vuln_scan", rc, so, se, summarize_findings_tree(fdir)), True
