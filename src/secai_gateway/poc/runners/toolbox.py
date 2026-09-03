"""Scoped pentest tools cho secai gọi vào Kali executor (ngoài 3 verify engine).

An toàn: gateway TỰ dựng argv từ tham số đã validate rồi chạy qua `raw` bằng exec (không shell,
không nối chuỗi) → không có command/argv injection. KHÔNG mở raw tùy ý cho người dùng. Target-based
tool dùng chung authz allowlist với verify. Bật/tắt chung flag `verify_enabled`.

Danh mục: nmap (recon) · searchsploit (offline, không target) · gobuster (dir brute) ·
sqlmap (SQLi) · hydra (login brute). Xem docs/poc-verify-architecture.md."""
from __future__ import annotations

import json
import re
from typing import Any, Callable

from .docker_exec import ExecConfig, run_executor

# Giá trị tham số: không khoảng trắng, không bắt đầu bằng '-' (tránh bị hiểu thành flag).
_SAFE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.\-:/_%?=&@]*$")
_PORTS = re.compile(r"^[0-9]{1,5}(?:[,-][0-9]{1,5})*$")
_HYDRA_SERVICES = {"ssh", "ftp", "http-get", "http-post-form", "smb", "rdp", "mysql",
                   "postgres", "telnet", "vnc", "http-head"}


def _need(params: dict[str, Any], key: str) -> str:
    v = str(params.get(key) or "").strip()
    if not v:
        raise ValueError(f"thiếu tham số '{key}'")
    return v


def _safe(v: str, key: str) -> str:
    if not _SAFE.match(v):
        raise ValueError(f"'{key}' có ký tự không hợp lệ")
    return v


# ── runners ──────────────────────────────────────────────────────────────────
async def _nmap(cfg: ExecConfig, p: dict[str, Any], run_id: str = "") -> dict[str, Any]:
    target = _safe(_need(p, "target"), "target")
    ports = str(p.get("ports") or "1-1000")
    if not _PORTS.match(ports):
        raise ValueError("'ports' không hợp lệ")
    res = await run_executor(cfg, ["raw", "nmap", "-Pn", "-sT", "-T4", "--open", "-p", ports, target],
                             run_id=run_id)
    open_ports = []
    for line in res.stdout.splitlines():
        m = re.match(r"^(\d{1,5})/tcp\s+open\s+(\S+)", line.strip())
        if m:
            open_ports.append({"port": int(m.group(1)), "service": m.group(2)})
    return {"tool": "nmap", "target": target, "open_ports": open_ports,
            "rc": res.rc, "output_tail": res.stdout[-1500:]}


async def _searchsploit(cfg: ExecConfig, p: dict[str, Any], run_id: str = "") -> dict[str, Any]:
    query = _need(p, "query")
    if query.startswith("-"):
        raise ValueError("'query' không hợp lệ")
    res = await run_executor(cfg, ["raw", "searchsploit", "--json", query], run_id=run_id)
    results = []
    try:
        data = json.loads(res.stdout)
        for e in data.get("RESULTS_EXPLOIT", [])[:30]:
            results.append({"title": e.get("Title"), "path": e.get("Path"), "type": e.get("Type")})
    except (ValueError, AttributeError):
        pass
    return {"tool": "searchsploit", "query": query, "count": len(results), "results": results, "rc": res.rc}


async def _gobuster(cfg: ExecConfig, p: dict[str, Any], run_id: str = "") -> dict[str, Any]:
    target = _safe(_need(p, "target"), "target")
    wordlist = str(p.get("wordlist") or "/usr/share/dirb/wordlists/common.txt")
    if not _SAFE.match(wordlist):
        raise ValueError("'wordlist' không hợp lệ")
    threads = min(max(int(p.get("threads") or 20), 1), 50)
    res = await run_executor(cfg, ["raw", "gobuster", "dir", "-u", target, "-w", wordlist,
                                   "-q", "-t", str(threads), "--no-error"], run_id=run_id)
    found = [l.strip() for l in res.stdout.splitlines() if l.strip().startswith("/")][:100]
    return {"tool": "gobuster", "target": target, "found": found, "count": len(found),
            "rc": res.rc, "output_tail": res.stdout[-1500:]}


async def _sqlmap(cfg: ExecConfig, p: dict[str, Any], run_id: str = "") -> dict[str, Any]:
    target = _safe(_need(p, "target"), "target")
    res = await run_executor(cfg, ["raw", "sqlmap", "-u", target, "--batch", "--flush-session",
                                   "--disable-coloring", "--level=1", "--risk=1"], run_id=run_id)
    low = res.stdout.lower()
    vulnerable = any(s in low for s in ("is vulnerable", "sqlmap identified", "injectable", "parameter is"))
    return {"tool": "sqlmap", "target": target, "vulnerable": vulnerable,
            "rc": res.rc, "output_tail": res.stdout[-2000:]}


async def _hydra(cfg: ExecConfig, p: dict[str, Any], run_id: str = "") -> dict[str, Any]:
    target = _safe(_need(p, "target"), "target")
    service = _need(p, "service")
    if service not in _HYDRA_SERVICES:
        raise ValueError(f"'service' phải thuộc {sorted(_HYDRA_SERVICES)}")
    user = _safe(_need(p, "username"), "username")
    pw = _need(p, "password")   # password có thể chứa ký tự đặc biệt; là value của -p nên an toàn với exec
    res = await run_executor(cfg, ["raw", "hydra", "-l", user, "-p", pw, "-f", target, service], run_id=run_id)
    low = res.stdout.lower()
    found = "login:" in low and "password:" in low and "1 valid password" in low
    return {"tool": "hydra", "target": target, "service": service, "credential_valid": found,
            "rc": res.rc, "output_tail": res.stdout[-1500:],
            "note": "brute bằng danh sách cần mount wordlist — bản này thử 1 cặp user/pass"}


# name -> (runner, needs_target_authz)
SCOPED_TOOLS: dict[str, tuple[Callable[..., Any], bool]] = {
    "nmap": (_nmap, True),
    "searchsploit": (_searchsploit, False),   # offline, không target
    "gobuster": (_gobuster, True),
    "sqlmap": (_sqlmap, True),
    "hydra": (_hydra, True),
}


# ── orchestration ─────────────────────────────────────────────────────────────
async def run_tool(settings: Any, tool: str, params: dict[str, Any], *, run_id: str,
                   net: str | None = None) -> dict[str, Any]:
    """Scoped tool: gated `verify_enabled`, authz target (tool cần target), argv do gateway dựng.
    `net` override docker network (vd 'host' cho lane1 chạm lab publish 127.0.0.1:180xx)."""
    from dataclasses import replace

    from ..backend import PocBadRequest, PocForbidden          # lazy: tránh vòng import
    from ..verify import _exec_cfg, target_allowed
    if not getattr(settings, "verify_enabled", False):
        raise PocForbidden("tool đang tắt (VERIFY_ENABLED=true để bật)")
    spec = SCOPED_TOOLS.get(tool)
    if spec is None:
        raise PocBadRequest(f"tool '{tool}' không hỗ trợ; có: {sorted(SCOPED_TOOLS)}")
    func, needs_target = spec
    if needs_target:
        target = str(params.get("target") or "")
        if not target_allowed(settings, target):
            raise PocForbidden(f"Target '{target}' ngoài allowlist (VERIFY_ALLOWED_TARGETS/VERIFY_ALLOW_ALL)")
    cfg = _exec_cfg(settings)
    if net:
        cfg = replace(cfg, net=net)
    try:
        return await func(cfg, params, run_id)
    except ValueError as exc:
        raise PocBadRequest(str(exc)) from exc


async def run_raw_command(settings: Any, *, argv: list[str] | None = None, shell: str | None = None,
                          timeout_s: int | None = None, privileged: bool | None = None,
                          run_id: str) -> dict[str, Any]:
    """RAW exec (RCE có chủ đích): chạy lệnh bất kỳ với privileged. Gated `exec_enabled`; caller
    được xác thực ở tầng endpoint (admin API key). KHÔNG sandbox khi privileged."""
    from dataclasses import replace

    from ..backend import PocBadRequest, PocForbidden
    from ..verify import _exec_cfg
    from .docker_exec import run_raw
    if not getattr(settings, "exec_enabled", False):
        raise PocForbidden("raw exec đang tắt (EXEC_ENABLED=true để bật)")
    if not argv and not shell:
        raise PocBadRequest("cần 'cmd' (list) hoặc 'shell' (chuỗi)")
    cfg = _exec_cfg(settings)
    if timeout_s:
        cfg = replace(cfg, timeout_s=int(timeout_s))
    priv = getattr(settings, "exec_privileged", True) if privileged is None else bool(privileged)
    res = await run_raw(cfg, argv=argv, shell=shell, privileged=priv, run_id=run_id)
    return {"rc": res.rc, "timed_out": res.timed_out, "privileged": priv,
            "stdout": res.stdout[-8000:], "stderr": res.stderr[-2000:]}
