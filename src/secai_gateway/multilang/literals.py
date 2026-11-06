"""Occurrence-preserving literal protection. Source text is never logged or persisted."""
from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import unicodedata
from dataclasses import dataclass

from .errors import TranslationError


PATTERNS = (
    re.compile(r"```[\s\S]*?```|`[^`\n]+`"),
    re.compile(r"-----BEGIN [^-]+-----[\s\S]*?-----END [^-]+-----"),
    re.compile(r"\b(?:Bearer|Basic|Digest)\s+[A-Za-z0-9._~+/=-]{8,}", re.I),
    re.compile(r"(?i)\b(?:password|passwd|pwd|secret|token|api[_-]?key|mật\s*khẩu|tài\s*khoản)\s*[:=]\s*(\S+?)(?=[,;]?(?:\s|$))", re.I),
    re.compile(r"\b(?:https?|ftp|ssh|file)://[^\s<>\"')\]]*[^\s<>\"')\].,;:!?]", re.I),
    re.compile(r"\b[\w.+-]+@[\w.-]+\.[\w.-]+\b"),
    re.compile(r"(?<![A-Za-z0-9_-])(?:CVE|CWE)-\d{1,7}(?:-\d{4,7})?(?![A-Za-z0-9_-])", re.I),
    re.compile(r"(?<![A-Za-z0-9_.-])(?:\d{1,3}\.){3}\d{1,3}(?:/\d{1,2})?(?::\d+)?(?![A-Za-z0-9_-]|\.\d)"),
    re.compile(r"\b[0-9a-fA-F]{32,}\b"),
    re.compile(r"\b(?:[A-Za-z0-9+/]{24,}={0,2}|eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+)\b"),
    re.compile(r"(?<!\w)(?:[A-Za-z]:\\[^\s]+|/(?:[\w.\-]+/)+[\w.\-]+)"),
    re.compile(r"(?<![A-Za-z0-9_.-])(?:v?\d+(?:\.\d+){1,4}|\d{4}-\d{2}-\d{2}|\d+(?:[.,]\d+)?)(?![A-Za-z0-9_.-])"),
    re.compile(r"⟦\s*ML-[^⟧]*⟧"),  # user-supplied placeholder-looking content
)
TOKEN_SHAPE = re.compile(r"⟦ML-[A-Za-z0-9_-]+-\d+⟧")


@dataclass(frozen=True)
class Protected:
    masked: str
    literals: tuple[str, ...]
    tokens: tuple[str, ...]

    def restore(self, output: str, expected: tuple[str, ...] | None = None) -> str:
        wanted = expected if expected is not None else self.tokens
        seen = tuple(TOKEN_SHAPE.findall(output))
        if seen != wanted or "⟦ML-" in TOKEN_SHAPE.sub("", output):
            raise TranslationError("OUTPUT_VALIDATION_FAILED")
        lookup = dict(zip(self.tokens, self.literals))
        return TOKEN_SHAPE.sub(lambda m: lookup[m.group(0)], output)


def _nonce(text: str, salt: bytes | None) -> str:
    """HMAC(salt, text): reproducible for a given request salt, unguessable without it."""
    if salt is None:
        return secrets.token_hex(8)
    counter = 0
    while True:
        digest = hmac.new(salt, f"{counter}\x00{text}".encode(), hashlib.sha256).hexdigest()[:16]
        if f"⟦ML-{digest}-" not in text:
            return digest
        counter += 1


def protect(text: str, salt: bytes | None = None) -> Protected:
    found: list[tuple[int, int]] = []
    for rx in PATTERNS:
        for match in rx.finditer(text):
            start, end = match.span(1) if rx is PATTERNS[3] else match.span()
            if start >= 0 and end > start:
                found.append((start, end))
    # Overlapping matches are unioned (never dropped): a dropped longer match would
    # leave its remainder unprotected and open to rewriting by the model.
    spans: list[tuple[int, int]] = []
    for start, end in sorted(found):
        if spans and start < spans[-1][1]:
            spans[-1] = (spans[-1][0], max(end, spans[-1][1]))
        else:
            spans.append((start, end))
    nonce = _nonce(text, salt)
    while f"⟦ML-{nonce}-" in text:  # only reachable for the random (salt=None) path
        nonce = secrets.token_hex(8)
    chunks: list[str] = []
    literals: list[str] = []
    tokens: list[str] = []
    last = 0
    for start, end in spans:
        chunks.append(unicodedata.normalize("NFC", text[last:start]))
        token = f"⟦ML-{nonce}-{len(tokens)}⟧"
        chunks.append(token)
        literals.append(text[start:end])
        tokens.append(token)
        last = end
    chunks.append(unicodedata.normalize("NFC", text[last:]))
    return Protected("".join(chunks), tuple(literals), tuple(tokens))


def validate_unicode(text: str) -> None:
    if any(ch == "\x00" or 0xD800 <= ord(ch) <= 0xDFFF
           or 0x202A <= ord(ch) <= 0x202E or 0x2066 <= ord(ch) <= 0x2069
           for ch in text):
        raise TranslationError("UNSAFE_UNICODE")
