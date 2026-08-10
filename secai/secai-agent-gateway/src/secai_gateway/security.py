from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException

_SECRET_PATTERNS = (
    re.compile(r"-----BEGIN (?:[A-Z ]+ )?PRIVATE KEY-----", re.I),
    re.compile(r"\b(?:sk|pk|api)[_-]?[A-Za-z0-9]{16,}\b", re.I),
    re.compile(r"\b(?:password|passwd|authorization|bearer)\s*[:=]\s*\S+", re.I),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
)
_SENSITIVE_IDENTIFIERS = re.compile(r"\b(?:\d[ -]*?){13,19}\b")


@dataclass(frozen=True, slots=True)
class Redaction:
    text: str
    changed: bool

    @property
    def digest(self) -> str:
        return hashlib.sha256(self.text.encode()).hexdigest()


def redact_public_input(value: Any) -> Redaction:
    text = value if isinstance(value, str) else str(value)
    redacted = text
    for pattern in _SECRET_PATTERNS:
        redacted = pattern.sub("[REDACTED_SECRET]", redacted)
    redacted = _SENSITIVE_IDENTIFIERS.sub("[REDACTED_IDENTIFIER]", redacted)
    return Redaction(redacted, redacted != text)


def require_public_input(value: Any) -> str:
    result = redact_public_input(value)
    if result.changed:
        raise HTTPException(422, "Only public or pre-redacted input may be sent to the gateway")
    return result.text


# ── Output-side language gate ────────────────────────────────────────────────
# This assistant is VI/EN-only; CJK ideographs in a response are always a generation
# artifact (observed e.g. "Poly讲讲 hệ thống"), never intentional content.
_CJK_RUN = re.compile(r"[一-鿿㐀-䶿豈-﫿]+")


def cjk_ratio(text: str) -> float:
    """Fraction of characters that are CJK ideographs."""
    if not text:
        return 0.0
    return sum(len(m) for m in _CJK_RUN.findall(text)) / len(text)


def remove_chinese_content(text: str) -> str:
    """Remove CJK ideographs from text (always, no ratio gate).

    Used on SSE token deltas where each chunk is too short for a reliable ratio check,
    and as the final strip for completed assistant output.
    """
    if not text:
        return text
    return _CJK_RUN.sub("", text)


# ── Reasoning / meta-commentary leak guard ───────────────────────────────────
# Even with thinking disabled, the small model occasionally streams its planning prose
# (self-deliberation, internal rule/tool names, self-directed formatting instructions)
# ahead of the real answer, ending on a "final answer" pivot line. That shape is never
# intended for the user. When we can see both the leak markers and the pivot, keep only
# the text after the last pivot; otherwise strip <think> blocks defensively and leave the
# content untouched (so a normal answer is never mangled).
_THINK_BLOCK = re.compile(r"(?is)<think>.*?</think>")
_THINK_TAG = re.compile(r"(?is)</?think>")
_REASONING_LEAK_MARKERS = re.compile(
    r"(tool_discipline|qdrant_search|neo4j_search|poc_search"
    r"|Ngữ cảnh\s*:|Người dùng (?:hỏi|đang hỏi|muốn)"
    r"|tôi (?:đã có|cần|nên|sẽ) (?:gọi|tra cứu|trả lời)"
    r"|Kết thúc bằng câu hỏi hỗ trợ"
    r"|Trả lời ngắn gọn, không thêm lời dẫn"
    r"|Cần tuân thủ ngôn ngữ)",
    re.I,
)
_ANSWER_PIVOT = re.compile(
    r"(?im)^[\s>*_#-]*(?:vậy\s+)?(?:thì\s+)?"
    r"(?:tôi\s+sẽ\s+trả\s+lời"
    r"|câu\s+trả\s+lời(?:\s+của\s+tôi)?\s*(?:là)?"
    r"|trả\s+lời\s+của\s+tôi(?:\s+là)?"
    r"|dưới\s+đây\s+là\s+câu\s+trả\s+lời)"
    r"\s*:?\s*$",
)


def strip_reasoning_leak(text: str) -> str:
    """Drop leaked chain-of-thought / meta planning that precedes the real answer."""
    if not text:
        return text
    cleaned = _THINK_TAG.sub("", _THINK_BLOCK.sub("", text))
    if _REASONING_LEAK_MARKERS.search(cleaned):
        pivots = list(_ANSWER_PIVOT.finditer(cleaned))
        if pivots:
            tail = cleaned[pivots[-1].end():].strip()
            if tail:  # never blank out the whole message
                cleaned = tail
    return cleaned.strip() or text.strip()


def sanitize_output_language(text: str, *, max_ratio: float = 0.15) -> str:
    """Clean assistant output before it reaches the user: drop leaked reasoning/meta
    planning, then strip CJK ideographs so responses stay VI/EN-only.

    Always strips. ``max_ratio`` is unused (kept for call-site compatibility);
    use ``cjk_ratio`` separately if you need contamination metrics.
    """
    return remove_chinese_content(strip_reasoning_leak(text))
