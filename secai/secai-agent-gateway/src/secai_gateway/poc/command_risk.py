"""Command-risk cho lane1 (port concept PentestCode `command-risk`).

Cổng mỗi action LLM đề xuất TRƯỚC khi chạy sandbox:
  1. tool phải hợp phase hiện tại (PHASE_TOOLS);
  2. `run_cmd` (RCE có chủ đích) chỉ khi exec_enabled + lane1_use_raw_cmd, và shell KHÔNG khớp mẫu
     phá hoại (rm -rf /, mkfs, fork-bomb, shutdown, dd if=, ghi đè /dev…), KHÔNG lẫn tên tool nội bộ
     (dấu hiệu LLM nhầm tool-call thành shell — mẫu `_valid_shell_command` ở advance.py);
  3. mọi `target` trong args phải thuộc scope (verify.target_allowed) — payload thì tùy ý, nhưng
     ĐÍCH luôn bị khoá trong allowlist.
Payload khai thác được phép bất kỳ (đó là mục đích lab); ta chỉ chặn phá hoại HẠ TẦNG + ngoài scope.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from .phases import PHASE_TOOLS, Phase

# Tên tool nội bộ của gateway — lẫn vào shell = LLM nhầm (mẫu ở advance.py:_TOOL_WORDS).
_TOOL_WORDS = ("poc_search", "qdrant_search", "neo4j_search", "web_search", "web_fetch",
               "poc_fetch", "poc_verify", "poc_scan", "poc_exec", "fetch_reference")

# Mẫu PHÁ HOẠI hạ tầng (chặn cứng, không liên quan tới khai thác target).
_DESTRUCTIVE = [
    re.compile(r"\brm\s+-\w*r\w*\b"),                   # rm -rf/-r/-fr… (recursive delete, không cần cho RCE)
    re.compile(r"\bmkfs\b"),
    re.compile(r"\bshutdown\b|\breboot\b|\bhalt\b|\bpoweroff\b|\binit\s+0\b"),
    re.compile(r":\(\)\s*\{.*\};"),                      # fork bomb :(){ :|:& };:
    re.compile(r"\bdd\s+if=.*\bof=/dev/"),               # dd of=/dev/sdX
    re.compile(r">\s*/dev/sd[a-z]"),                     # ghi đè block device
    re.compile(r"\bchmod\s+-R\s+0*\s+/\b"),
    re.compile(r"\b(userdel|deluser|passwd)\b.*\broot\b"),
]


@dataclass
class RiskDecision:
    allow: bool
    reason: str = ""


def _shell_ok(shell: str) -> tuple[bool, str]:
    s = (shell or "").strip()
    if not s:
        return False, "shell rỗng"
    if len(s) > 4000:
        return False, "shell quá dài"
    if any(w in s for w in _TOOL_WORDS):
        return False, "lẫn tên tool nội bộ (LLM nhầm tool-call thành shell)"
    for pat in _DESTRUCTIVE:
        if pat.search(s):
            return False, f"mẫu phá hoại hạ tầng: {pat.pattern[:40]}"
    return True, ""


def classify(action: Any, phase: Phase, host: str, port: str | None, settings: Any) -> RiskDecision:
    """Trả RiskDecision(allow, reason). `action` có `.tool` và `.args` (dict)."""
    tool = getattr(action, "tool", "")
    args = getattr(action, "args", {}) or {}

    # 1 — tool hợp phase
    allowed = PHASE_TOOLS.get(phase, PHASE_TOOLS[Phase.EXPLOIT])
    if tool not in allowed:
        return RiskDecision(False, f"tool '{tool}' không thuộc phase {phase.value} ({sorted(allowed)})")

    # 2 — run_cmd: cần bật + shell an toàn hạ tầng
    if tool == "run_cmd":
        if not getattr(settings, "exec_enabled", False):
            return RiskDecision(False, "run_cmd tắt (EXEC_ENABLED=true để bật)")
        if not getattr(settings, "lane1_use_raw_cmd", True):
            return RiskDecision(False, "run_cmd bị khoá cho lane1 (LANE1_USE_RAW_CMD=false)")
        ok, why = _shell_ok(str(args.get("shell") or ""))
        if not ok:
            return RiskDecision(False, why)

    # 3 — scope: mọi target trong args phải trong allowlist (delegate verify)
    from .verify import target_allowed
    tgt = str(args.get("target") or "").strip()
    if tgt and not target_allowed(settings, tgt) and not getattr(settings, "verify_allow_all", False):
        return RiskDecision(False, f"target '{tgt}' ngoài allowlist")

    return RiskDecision(True, "")
