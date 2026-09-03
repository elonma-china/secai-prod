"""Adapter chạy tool cho lane1 — menu curated, TÁI DÙNG runner sandbox có sẵn (không dựng lại).

- http_request : in-process qua `primitives._raw_http` (stdlib, PATH NGUYÊN VĂN cho OGNL/traversal,
                 air-gap, chạm host-port trực tiếp, không tốn container). Vũ khí chính.
- nmap/sqlmap/gobuster/searchsploit : `toolbox.run_tool` (hardened docker, argv an toàn), net='host'
                 để chạm lab publish 127.0.0.1:180xx.
- run_cmd      : `toolbox.run_raw_command` (RCE có chủ đích, gated exec_enabled) chạy payload/msf tùy ý.

Mọi lỗi (tool tắt, sai param) → Observation(ok=False) để vòng ReAct tiếp tục, KHÔNG raise ra ngoài.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

from . import primitives

TOOL_MENU = frozenset({"http_request", "nmap", "sqlmap", "searchsploit", "gobuster", "run_cmd", "ask_user"})

_MAX_OBS = 8000   # cắt observation thô (prompt sẽ cắt tiếp còn ngắn hơn)


@dataclass
class Observation:
    tool: str
    ok: bool
    text: str                                       # human-readable (KÈM dòng request) → prompt/stream FE
    meta: dict[str, Any] = field(default_factory=dict)
    body: str = ""                                  # CHỈ output server/tool (KHÔNG echo request) → oracle


def _base_url(host: str, port: str | None, tls: bool) -> str:
    if port:
        return f"{'https' if tls else 'http'}://{host}:{port}"
    return f"{'https' if tls else 'http'}://{host}"


async def _http_request(action: Any, host: str, port: str | None, timeout_s: int) -> Observation:
    a = getattr(action, "args", {}) or {}
    method = str(a.get("method") or "GET").upper()
    path = str(a.get("path") or "/")
    headers = a.get("headers") if isinstance(a.get("headers"), dict) else {}
    body = a.get("body")
    tls = bool(a.get("tls"))
    p = int(port) if (port and str(port).isdigit()) else (443 if tls else 80)
    try:
        st, resp = await asyncio.to_thread(
            primitives._raw_http, host, p, method, path,
            headers={str(k): str(v) for k, v in (headers or {}).items()},
            body=(str(body) if body is not None else None),
            timeout=min(float(timeout_s), 30.0), tls=tls)
    except Exception as exc:
        return Observation("http_request", False, f"request lỗi: {exc}",
                           {"method": method, "path": path}, body="")
    text = f"HTTP {st} {method} {path}\n{resp[:_MAX_OBS]}"
    # body = CHỈ response server (không kèm request path chứa payload) → oracle không khớp nhầm payload.
    return Observation("http_request", True, text, {"status": st, "method": method, "path": path},
                       body=resp[:_MAX_OBS])


async def _scoped(settings: Any, action: Any, tool: str, host: str, port: str | None,
                  run_id: str, net: str) -> Observation:
    from .backend import PocError
    from .runners.toolbox import run_tool
    a = dict(getattr(action, "args", {}) or {})
    # Điền target mặc định về ĐÚNG host của ta nếu LLM bỏ trống (command_risk đã scope-check).
    if tool == "nmap":
        a.setdefault("target", host)
    elif tool in ("sqlmap", "gobuster"):
        a.setdefault("target", _base_url(host, port, bool(a.get("tls"))))
    try:
        res = await run_tool(settings, tool, a, run_id=run_id, net=net)
    except PocError as exc:
        return Observation(tool, False, f"{tool} lỗi: {exc}", {})
    tail = str(res.get("output_tail") or "")
    summary = {k: v for k, v in res.items() if k != "output_tail"}
    return Observation(tool, True, f"{summary}\n{tail}"[:_MAX_OBS], summary, body=tail[:_MAX_OBS])


async def _run_cmd(settings: Any, action: Any, run_id: str) -> Observation:
    from .backend import PocError
    from .runners.toolbox import run_raw_command
    shell = str((getattr(action, "args", {}) or {}).get("shell") or "")
    priv = bool(getattr(settings, "exec_privileged", True))
    try:
        res = await run_raw_command(settings, shell=shell, privileged=priv, run_id=run_id)
    except PocError as exc:
        return Observation("run_cmd", False, f"run_cmd lỗi: {exc}", {})
    out = (res.get("stdout") or "") + (("\n" + res["stderr"]) if res.get("stderr") else "")
    return Observation("run_cmd", res.get("rc") == 0, out[:_MAX_OBS] or "(rỗng)",
                       {"rc": res.get("rc"), "timed_out": res.get("timed_out")}, body=out[:_MAX_OBS])


async def run_lane1_tool(settings: Any, action: Any, *, run_id: str, host: str,
                         port: str | None, net: str = "host", timeout_s: int = 180) -> Observation:
    """Chạy action (đã qua command_risk) trong sandbox; trả Observation (không raise)."""
    tool = getattr(action, "tool", "")
    if tool not in TOOL_MENU:
        return Observation(tool, False, f"tool '{tool}' ngoài menu {sorted(TOOL_MENU)}", {})
    if tool == "http_request":
        return await _http_request(action, host, port, timeout_s)
    if tool == "run_cmd":
        return await _run_cmd(settings, action, run_id)
    return await _scoped(settings, action, tool, host, port, run_id, net)
