"""Lossless, occurrence-aware masking of technical literals for translation.

All matches are found in the original string before replacement. This prevents a
later expression from matching inside a placeholder or nesting a prior match.
"""
from __future__ import annotations

import ipaddress
import re
import secrets
from dataclasses import dataclass

from secai_gateway.security import _SECRET_PATTERNS, _SENSITIVE_IDENTIFIERS

from .types import TranslationError


@dataclass(frozen=True)
class ProtectedText:
    text: str
    _literals: tuple[str, ...]
    _prefix: str


_PLACEHOLDER_LIKE = re.compile(r"⟦[A-Za-z][A-Za-z0-9:]*⟧")
_CREDENTIAL = re.compile(
    r"(?i)\b(?:password|passwd|pwd|secret|token|api[_-]?key|authorization|"
    r"mật\s*khẩu|tài\s*khoản)\s*[:=]\s*(\S+)")
_IP = re.compile(r"(?<![\w.:])(?:\d{1,3}\.){3}\d{1,3}(?:/\d{1,2})?(?![\w./])")
_PATTERNS: tuple[re.Pattern[str], ...] = (
    _PLACEHOLDER_LIKE,
    re.compile(r"```[\s\S]*?```|`[^`\n]+`"),
    re.compile(r"-----BEGIN[^\n]*-----[\s\S]*?-----END[^\n]*-----"),
    re.compile(r"\b(?:https?|ftp|ssh|file)://[^\s<>\"')\]]+", re.I),
    re.compile(r"\bCVE-\d{4}-\d{4,7}\b|\bCWE-\d{1,5}\b", re.I),
    re.compile(r"(?<!\w)[A-Za-z]:\\[^\s\"']+|(?<![\w/])/(?:[\w.-]+/)+[\w.-]+"),
    re.compile(r"\b[0-9a-fA-F]{16,}\b"),
    re.compile(r"\b[A-Za-z][A-Za-z0-9_.+-]*[ /-]v?\d+(?:\.\d+){1,4}(?:[-+][\w.]+)?\b"),
    re.compile(r"(?m)(?:^|(?<=\n))\s*\$\s*[^\n]+"),
    re.compile(r"\b(?:curl|wget|nmap|ssh|sudo|pip|npm|docker|kubectl)\s+(?:--?[\w-]+|[^\s]+)[^\n]*"),
    *_SECRET_PATTERNS,
    _SENSITIVE_IDENTIFIERS,
)


def literal_spans(text: str) -> tuple[tuple[int, int], ...]:
    """Return maximal disjoint technical spans in source code-point offsets."""
    candidates: list[tuple[int, int]] = []
    for match in _IP.finditer(text):
        try:
            ipaddress.ip_network(match.group(), strict=False)
        except ValueError:
            continue
        candidates.append(match.span())
    for match in _CREDENTIAL.finditer(text):
        candidates.append(match.span(1))
    for pattern in _PATTERNS:
        candidates.extend(match.span() for match in pattern.finditer(text))
    if not candidates:
        return ()
    # Merge true overlaps; adjacent tokens remain distinct occurrences.
    merged: list[list[int]] = []
    for start, end in sorted(set(candidates)):
        if merged and start < merged[-1][1]:
            merged[-1][1] = max(end, merged[-1][1])
        else:
            merged.append([start, end])
    return tuple((start, end) for start, end in merged)


def protect_literals(text: str) -> ProtectedText:
    spans = literal_spans(text)
    prefix = f"⟦T{secrets.token_hex(12)}:"
    while prefix in text:
        prefix = f"⟦T{secrets.token_hex(12)}:"
    chunks: list[str] = []
    literals: list[str] = []
    cursor = 0
    for start, end in spans:
        chunks.extend((text[cursor:start], f"{prefix}{len(literals)}⟧"))
        literals.append(text[start:end])
        cursor = end
    chunks.append(text[cursor:])
    return ProtectedText("".join(chunks), tuple(literals), prefix)


def restore_literals(output: str, protected: ProtectedText) -> str:
    prefix = protected._prefix
    marker = re.compile(re.escape(prefix) + r"(\d+)⟧")
    found = list(marker.finditer(output))
    if (
        len(found) != len(protected._literals)
        or [int(match.group(1)) for match in found] != list(range(len(found)))
        or prefix in marker.sub("", output)
    ):
        raise TranslationError("TOKEN_INTEGRITY_FAILED", "Technical literal integrity check failed")
    cursor = 0
    chunks: list[str] = []
    for match, literal in zip(found, protected._literals):
        chunks.extend((output[cursor:match.start()], literal))
        cursor = match.end()
    chunks.append(output[cursor:])
    return "".join(chunks)
