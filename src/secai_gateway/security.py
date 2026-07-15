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


def sanitize_output_language(text: str, *, max_ratio: float = 0.15) -> str:
    """Strip stray CJK character runs from assistant output. Below `max_ratio` this is a
    clean cosmetic fix (a few contaminated tokens in otherwise VI/EN text); above it the
    response is likely broken in a deeper way, so it's left untouched rather than mangled
    into nonsense — the caller can detect this via ``cjk_ratio`` if it wants to log/alert."""
    if not text:
        return text
    ratio = cjk_ratio(text)
    if ratio == 0 or ratio > max_ratio:
        return text
    return _CJK_RUN.sub("", text)
