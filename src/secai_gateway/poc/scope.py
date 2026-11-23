"""Phạm vi target của exploit-v2: kết quả chỉ tính khi bằng chứng đến từ đúng host:port người dùng nhập.

Đo 2026-11-23: nhập CVE nginx-ui + target `:19999` (ứng dụng khác), agent tự quét host, khai thác `:19000` và gateway
trả ok/3.3. Allowlist chỉ kiểm host nên không chặn được. Module thuần, chung cho mọi lab/CVE (so khớp theo CỔNG)."""
from __future__ import annotations

import os
import re
from typing import Any

_URL_RE = re.compile(r"https?://[^\s/'\"<>:@?#]+(?::(\d+))?", re.I)
_HOSTPORT_RE = re.compile(r"(?<![\w./-])(?:(?:\d{1,3}\.){3}\d{1,3}|localhost):(\d+)\b", re.I)
_SCRIPT_RE = re.compile(r"[\w./-]+\.(?:py|js|sh|php|rb|pl)\b")
_HEREDOC_RE = re.compile(r"cat\s+>\s*([^\s;|&]+)\s*<<\s*['\"]?(\w+)['\"]?\s*\n(.*?)\n\2\b", re.S)


def _valid(p: int) -> bool:
    return 1 <= p <= 65535


def target_port(target: str) -> int | None:
    """Cổng của target: `h:19999`/`http://h:19999/x` → 19999; `https://h` → 443; `http://h` → 80; không có → None."""
    t = (target or "").strip()
    if not t:
        return None
    m = _URL_RE.match(t)
    if m:
        if m.group(1):
            return int(m.group(1)) if _valid(int(m.group(1))) else None
        return 443 if t.lower().startswith("https") else 80
    m = re.match(r"^[^\s/:]+:(\d+)(?:/|$)", t)
    return int(m.group(1)) if m and _valid(int(m.group(1))) else None


def ports_in(text: str) -> set[int]:
    """Cổng của mọi URL `http(s)://host[:port]` (mặc định 80/443) và cặp `IPv4|localhost:port` trong text."""
    out: set[int] = set()
    for m in _URL_RE.finditer(text or ""):
        if m.group(1):
            if _valid(int(m.group(1))):
                out.add(int(m.group(1)))
        else:
            out.add(443 if m.group(0).lower().startswith("https") else 80)
    for m in _HOSTPORT_RE.finditer(text or ""):
        if _valid(int(m.group(1))):
            out.add(int(m.group(1)))
    return out


def _written_bodies(messages: list[dict[str, Any]]) -> dict[str, str]:
    """basename → thân file agent đã ghi (tool write hoặc heredoc `cat > f <<EOF`); bản sau cùng thắng."""
    bodies: dict[str, str] = {}
    for msg in messages:
        for part in msg.get("parts") or []:
            if part.get("type") != "tool":
                continue
            inp = (part.get("state") or {}).get("input") or {}
            if part.get("tool") == "write":
                path, content = str(inp.get("filePath") or inp.get("path") or ""), inp.get("content")
                if isinstance(content, str):
                    bodies[os.path.basename(path)] = content
            elif part.get("tool") == "bash":
                cmd = str(inp.get("command") or "")
                for m in _HEREDOC_RE.finditer(cmd) if "<<" in cmd else ():
                    bodies[os.path.basename(m.group(1))] = m.group(3)
    return bodies


def scope_verdict(target: str, messages: list[dict[str, Any]], *, proof_re: re.Pattern) -> tuple[str, int | None]:
    """("ok"|"mismatch"|"unknown", cổng_lệch). Chỉ xét lệnh bash đã xong có output khớp `proof_re`; endpoint của lệnh =
    cổng trong dòng lệnh ∪ cổng trong thân script mà lệnh chạy. ok: có lệnh bằng chứng nhắm cổng target; mismatch: có
    lệnh bằng chứng nhưng chỉ nhắm cổng khác; còn lại unknown (không hạ nhầm khi không xác định được)."""
    tport = target_port(target)
    if tport is None:
        return "unknown", None
    bodies = _written_bodies(messages)
    seen: list[int] = []
    for msg in messages:
        for part in msg.get("parts") or []:
            if part.get("type") != "tool" or part.get("tool") != "bash":
                continue
            state = part.get("state") or {}
            if state.get("status") != "completed" or not proof_re.search(str(state.get("output") or "")):
                continue
            cmd = str((state.get("input") or {}).get("command") or "")
            ports = ports_in(cmd)
            for name in _SCRIPT_RE.findall(cmd):
                ports |= ports_in(bodies.get(os.path.basename(name), ""))
            if tport in ports:
                return "ok", None
            seen.extend(sorted(ports))
    return ("mismatch", seen[0]) if seen else ("unknown", None)
