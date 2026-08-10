"""BugHunter ``recon_engine.sh`` runner — ship the big recon arsenal into SecAI.

Runs the clone script under ``BUGBOUNTY_ROOT`` (host) or via ``secai-kali-executor``
(``backend=kali`` / ``auto``). Soft-deps inside the script skip missing binaries;
we always seed ``live/urls.txt`` from the HTTP probe so IP/lab targets still feed
ffuf/nuclei/exposure when those tools exist.
"""
from __future__ import annotations

import asyncio
import importlib.util
import logging
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

# Soft-deps recon_engine.sh cares about (plus curl/dig which are usually present).
_ARSENAL = (
    "subfinder",
    "amass",
    "httpx",
    "katana",
    "gau",
    "nuclei",
    "nmap",
    "ffuf",
    "dig",
    "curl",
)


def bugbounty_root(settings: Any | None = None) -> Path:
    """Resolve claude-bug-bounty root (env / settings / sibling of gateway)."""
    if settings is not None:
        configured = (getattr(settings, "bugbounty_root", None) or "").strip()
        if configured:
            return Path(configured).expanduser().resolve()
    env = (os.environ.get("BUGBOUNTY_ROOT") or "").strip()
    if env:
        return Path(env).expanduser().resolve()
    # secai/secai-agent-gateway → sibling secai/claude-bug-bounty
    here = Path(__file__).resolve()
    sibling = here.parents[3] / "claude-bug-bounty"  # .../secai/claude-bug-bounty
    if sibling.is_dir():
        return sibling
    return Path("/home/ict/AIEngines/secai/claude-bug-bounty")


def recon_engine_script(settings: Any | None = None) -> Path:
    return bugbounty_root(settings) / "tools" / "recon_engine.sh"


def arsenal_status() -> dict[str, Any]:
    """Which external binaries are on PATH (host)."""
    present: list[str] = []
    missing: list[str] = []
    for name in _ARSENAL:
        # Avoid shell aliases (e.g. gau → git add --update).
        path = shutil.which(name)
        if path and Path(path).is_file():
            present.append(name)
        else:
            missing.append(name)
    return {
        "present": present,
        "missing": missing,
        "ready": bool(set(present) & {"httpx", "nuclei", "nmap", "ffuf", "curl"}),
    }


def _host_from_target(target: str) -> str:
    raw = (target or "").strip()
    if "://" not in raw:
        # host:port or bare host
        return raw.split("/")[0]
    parsed = urlparse(raw if "://" in raw else f"http://{raw}")
    return parsed.netloc or parsed.path.split("/")[0]


def _seed_recon_dir(out: Path, *, target: str, seed_urls: list[str]) -> None:
    """Pre-create recon_engine layout + seed live hosts from HTTP probe."""
    for sub in ("subdomains", "live", "ports", "urls", "js", "dirs", "params", "nuclei", "exposure"):
        (out / sub).mkdir(parents=True, exist_ok=True)
    host = _host_from_target(target)
    # Strip port for subdomain list when it's clearly host:port — engine IP mode uses full token.
    (out / "subdomains" / "all.txt").write_text(host + "\n", encoding="utf-8")
    urls = [u.rstrip("/") + "/" for u in seed_urls if u]
    if not urls and host:
        # Best-effort default for IP/lab when probe didn't run.
        scheme = "https" if host.endswith(":443") else "http"
        urls = [f"{scheme}://{host}/"]
    if urls:
        (out / "live" / "urls.txt").write_text("\n".join(urls) + "\n", encoding="utf-8")


def _summarize_dir(recon_dir: Path) -> dict[str, Any]:
    """Prefer BugHunter ReconAdapter; fall back to line counts."""
    adapter_py = bugbounty_root() / "tools" / "recon_adapter.py"
    if adapter_py.is_file():
        try:
            spec = importlib.util.spec_from_file_location("bb_recon_adapter", adapter_py)
            if spec and spec.loader:
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
                adapter = mod.ReconAdapter(recon_dir)
                adapter.normalize()
                return {
                    "counts": adapter.summary(),
                    "live_hosts": adapter.get_live_hosts()[:50],
                    "api_endpoints": adapter.get_api_endpoints()[:40],
                    "sensitive_paths": adapter.get_sensitive_paths()[:40],
                    "interesting_params": adapter.get_interesting_params()[:40],
                    "config_exposure": adapter.get_config_exposure()[:20],
                    "js_secrets": adapter.get_js_secrets()[:20],
                    "subdomains": adapter.get_subdomains()[:80],
                }
        except Exception as exc:  # noqa: BLE001
            logger.warning("ReconAdapter failed: %s", exc)

    def _n(rel: str) -> int:
        p = recon_dir / rel
        if not p.is_file():
            return 0
        return sum(1 for ln in p.read_text(encoding="utf-8", errors="replace").splitlines() if ln.strip())

    return {
        "counts": {
            "subdomains": _n("subdomains/all.txt"),
            "live_hosts": _n("live/urls.txt"),
            "urls": _n("urls/all.txt"),
            "parameterized_urls": _n("urls/with_params.txt"),
            "js_files": _n("urls/js_files.txt"),
            "api_endpoints": _n("urls/api_endpoints.txt"),
            "nuclei": _n("nuclei/findings.jsonl"),
            "open_ports": _n("ports/open_ports.txt"),
        },
        "live_hosts": [],
        "api_endpoints": [],
        "sensitive_paths": [],
        "interesting_params": [],
        "config_exposure": [],
        "js_secrets": [],
        "subdomains": [],
    }


async def _run_host(
    *,
    script: Path,
    target: str,
    quick: bool,
    out: Path,
    timeout_s: int,
    cwd: Path,
) -> dict[str, Any]:
    env = os.environ.copy()
    env["RECON_OUT_DIR"] = str(out)
    # Prefer Go tools if installed later.
    go_bin = str(Path.home() / "go" / "bin")
    env["PATH"] = f"{go_bin}:{env.get('PATH', '')}"
    # IP/CIDR: skip wildcard subdomain noise (recon_engine respects TARGET_TYPE).
    host = _host_from_target(target)
    if host.replace(".", "").replace(":", "").isdigit() or ":" in host.split("/")[0]:
        # host:port or IPv4
        bare = host.split(":")[0]
        if all(p.isdigit() for p in bare.split(".")):
            env["TARGET_TYPE"] = "ip"
            env["SCOPE_LOCK"] = "1"

    argv = ["bash", str(script), target]
    if quick:
        argv.append("--quick")
    proc = await asyncio.create_subprocess_exec(
        *argv,
        cwd=str(cwd),
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    timed_out = False
    try:
        stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=timeout_s)
    except asyncio.TimeoutError:
        timed_out = True
        proc.kill()
        await proc.wait()
        stdout_b, stderr_b = b"", b"timed out"
    stdout = stdout_b.decode("utf-8", "replace")
    stderr = stderr_b.decode("utf-8", "replace")
    return {
        "rc": proc.returncode if proc.returncode is not None else -1,
        "timed_out": timed_out,
        "stdout_tail": stdout[-6000:],
        "stderr_tail": stderr[-3000:],
        "backend": "host",
    }


async def _run_kali(
    *,
    settings: Any,
    script_host: Path,
    bb_root: Path,
    target: str,
    quick: bool,
    out: Path,
    timeout_s: int,
    run_id: str,
) -> dict[str, Any]:
    """Mount bug-bounty + outdir into Kali; run recon_engine with container tools."""
    from ..poc.runners.docker_exec import ExecConfig, run_raw

    image = getattr(settings, "kali_executor_image", None) or "secai-kali-executor:latest"
    # host network so lab/127.0.0.1 on the gateway host is reachable.
    cfg = ExecConfig(
        image=image,
        timeout_s=timeout_s,
        net="host",
        mem=getattr(settings, "verify_mem", None) or "2g",
        cpus=getattr(settings, "verify_cpus", None) or "2",
        pids=max(256, int(getattr(settings, "verify_pids", 256) or 256)),
        max_concurrent=int(getattr(settings, "verify_max_concurrent", 4) or 4),
    )
    quick_flag = "--quick" if quick else ""
    # target is gateway-built (allowlisted); still quote for shell.
    import shlex
    tgt = shlex.quote(target)
    shell = (
        f"export RECON_OUT_DIR=/out PATH=/usr/local/bin:/usr/bin:/bin; "
        f"bash /bb/tools/recon_engine.sh {tgt} {quick_flag}"
    ).strip()
    res = await run_raw(
        cfg,
        shell=shell,
        privileged=True,
        mounts=[
            (str(bb_root), "/bb"),
            (str(out), "/out"),
        ],
        run_id=run_id or "bh-recon",
    )
    return {
        "rc": res.rc,
        "timed_out": res.timed_out,
        "stdout_tail": (res.stdout or "")[-6000:],
        "stderr_tail": (res.stderr or "")[-3000:],
        "backend": "kali",
    }


async def run_recon_engine(
    target: str,
    *,
    settings: Any | None = None,
    quick: bool = True,
    seed_urls: list[str] | None = None,
    backend: str | None = None,
    timeout_s: int | None = None,
    run_id: str = "",
    out_dir: Path | str | None = None,
) -> dict[str, Any]:
    """Run BugHunter recon_engine and return a capped JSON summary."""
    if settings is None:
        from ..config import get_settings
        settings = get_settings()

    enabled = getattr(settings, "bughunt_recon_engine", True)
    if not enabled:
        return {"ok": False, "skipped": True, "reason": "bughunt_recon_engine disabled"}

    script = recon_engine_script(settings)
    if not script.is_file():
        return {
            "ok": False,
            "skipped": True,
            "reason": f"recon_engine.sh not found at {script}",
            "bugbounty_root": str(bugbounty_root(settings)),
            "arsenal": arsenal_status(),
        }

    # Authz — same allowlist as verify / bughunt stream.
    from ..poc.verify import target_allowed
    if not target_allowed(settings, target) and not getattr(settings, "verify_allow_all", False):
        allow = getattr(settings, "verify_targets", None) or []
        if allow:
            return {"ok": False, "error": f"Target '{target}' ngoài allowlist", "forbidden": True}

    backend_choice = (backend or getattr(settings, "bughunt_recon_backend", None) or "auto").lower()
    timeout = int(timeout_s or getattr(settings, "bughunt_recon_timeout_s", None) or 600)
    arsenal = arsenal_status()

    if out_dir is None:
        base = Path(getattr(settings, "gateway_db_path", "./data/gateway.db")).resolve().parent
        out = Path(tempfile.mkdtemp(prefix="bh-recon-", dir=str(base / "bughunt")))
    else:
        out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    _seed_recon_dir(out, target=target, seed_urls=list(seed_urls or []))

    # auto: prefer kali when host lacks nmap/nuclei/ffuf (common on this GPU host).
    use_kali = backend_choice == "kali"
    if backend_choice == "auto":
        host_heavy = set(arsenal["present"]) & {"nmap", "nuclei", "ffuf"}
        use_kali = len(host_heavy) < 2

    bb = bugbounty_root(settings)
    try:
        if use_kali:
            run_meta = await _run_kali(
                settings=settings,
                script_host=script,
                bb_root=bb,
                target=target,
                quick=quick,
                out=out,
                timeout_s=timeout,
                run_id=run_id,
            )
        else:
            run_meta = await _run_host(
                script=script,
                target=target,
                quick=quick,
                out=out,
                timeout_s=timeout,
                cwd=bb,
            )
    except Exception as exc:  # noqa: BLE001
        logger.exception("recon_engine failed")
        return {
            "ok": False,
            "error": f"{type(exc).__name__}: {exc}",
            "recon_dir": str(out),
            "arsenal": arsenal,
            "backend": "kali" if use_kali else "host",
        }

    summary = _summarize_dir(out)
    counts = summary.get("counts") or {}
    ok = (not run_meta.get("timed_out")) and (
        run_meta.get("rc") == 0 or any(int(v or 0) > 0 for v in counts.values())
    )
    return {
        "ok": ok,
        "target": target,
        "quick": quick,
        "recon_dir": str(out),
        "arsenal": arsenal,
        "bugbounty_root": str(bb),
        **run_meta,
        **summary,
        "summary_text": (
            f"recon_engine({run_meta.get('backend')}) counts={counts} "
            f"rc={run_meta.get('rc')} timed_out={run_meta.get('timed_out')}"
        ),
    }
