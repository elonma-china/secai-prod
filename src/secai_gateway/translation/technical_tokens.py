"""Lossless, occurrence-aware masking of technical literals for translation.

All matches are found in the original string before replacement. This prevents a
later expression from matching inside a placeholder or nesting a prior match.
"""
from __future__ import annotations

import ipaddress
import re
import secrets
import unicodedata
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
    r"(?i)(?<![A-Za-z0-9_])(?:password|passwd|pwd|secret|token|api[_-]?key|authorization|"
    r"mật\s*khẩu|tài\s*khoản)\s*[:=]\s*(\S+)")
_IP = re.compile(r"(?<![A-Za-z0-9_.:])(?:\d{1,3}\.){3}\d{1,3}(?:/\d{1,2})?"
                 r"(?![A-Za-z0-9_/]|\.[A-Za-z0-9])")
_IPV6 = re.compile(r"(?<![A-Za-z0-9_.:%-])[0-9A-Fa-f:.]+"
                   r"(?:%[A-Za-z0-9_.-]+)?(?:/\d{1,3})?"
                   r"(?![A-Za-z0-9_:/%-]|\.[A-Za-z0-9])")
_URL_START = re.compile(r"(?<![A-Za-z0-9_.-])(?:https?|ftp|ssh|file)://", re.I)
_URL_STOP = frozenset("<>\"'")
_QUOTED_PATH = re.compile(
    r"(?P<quote>['\"])(?P<path>(?:[A-Za-z]:\\|\\\\[A-Za-z0-9_.~-]+\\[A-Za-z0-9_.~-]+\\|"
    r"/)[^\n]*?)(?P=quote)")
# A bare dotted number after a quantity adverb is a measurement, not a
# product/version pair ("nearly 1.5 million"). An explicit v marker still
# denotes a version, and all other product names remain open-ended.
_QUANTITY_ADVERBS = frozenset({"about", "almost", "approximately", "around", "nearly", "roughly"})
_VERSION = re.compile(
    r"(?<![A-Za-z0-9_.+-])(?P<product>[A-Za-z][A-Za-z0-9_.+-]*)[ /-]"
    r"(?P<marker>v?)(?:\d+)(?:\.\d+){1,4}(?:[-+][A-Za-z0-9_.]+)?"
    r"(?![A-Za-z0-9_.+-])")
_PATTERNS: tuple[re.Pattern[str], ...] = (
    _PLACEHOLDER_LIKE,
    re.compile(r"```[\s\S]*?```|`[^`\n]+`"),
    re.compile(r"-----BEGIN[^\n]*-----[\s\S]*?-----END[^\n]*-----"),
    # ASCII identifier boundaries let CJK/Cyrillic/Arabic prose touch a token
    # without admitting a substring of a longer ASCII filename or identifier.
    re.compile(r"(?<![A-Za-z0-9_.-])(?:CVE-\d{4}-\d{4,7}|CWE-\d{1,5})(?![A-Za-z0-9_.-])", re.I),
    re.compile(r"(?<![A-Za-z0-9_])(?:[A-Za-z]:\\|"
               r"\\\\[A-Za-z0-9_.~-]+\\[A-Za-z0-9_.~-]+\\)"
               r"(?:[A-Za-z0-9_.~$-]+(?: [A-Za-z0-9_.~$-]+)*\\)*"
               r"[A-Za-z0-9_.~$-]+(?![A-Za-z0-9_.-])|"
               r"(?<![A-Za-z0-9_/])/(?:[A-Za-z0-9_.-]+(?: [A-Za-z0-9_.-]+)*/)+"
               r"[A-Za-z0-9_.-]+(?![A-Za-z0-9_.-])"),
    re.compile(r"(?<![A-Za-z0-9_])[0-9a-fA-F]{16,}(?![A-Za-z0-9_])"),
    re.compile(r"(?m)(?:^|(?<=\n))\s*\$\s*[^\n]+"),
    re.compile(r"(?<![A-Za-z0-9_])(?:curl|wget|nmap|ssh|sudo|pip|npm|docker|kubectl)\s+"
               r"[A-Za-z0-9_./:@%?=&+~-]+(?:\s+[A-Za-z0-9_./:@%?=&+~-]+)*"),
    *(re.compile(pattern.pattern, (pattern.flags & ~re.UNICODE) | re.ASCII)
      for pattern in _SECRET_PATTERNS),
    re.compile(_SENSITIVE_IDENTIFIERS.pattern,
               (_SENSITIVE_IDENTIFIERS.flags & ~re.UNICODE) | re.ASCII),
)


def _url_spans(text: str) -> tuple[tuple[int, int], ...]:
    spans: list[tuple[int, int]] = []
    for match in _URL_START.finditer(text):
        end = match.end()
        unicode_body = False
        bracket_depth = 0
        paren_depth = 0
        while end < len(text):
            char = text[end]
            if char.isspace() or char in _URL_STOP:
                break
            if char == "(":
                paren_depth += 1
            elif char == ")":
                if not paren_depth:
                    break
                paren_depth -= 1
            if char == "[":
                bracket_depth += 1
            elif char == "]":
                if not bracket_depth:
                    break
                bracket_depth -= 1
            if ord(char) > 127:
                if unicodedata.category(char)[0] in {"P", "C"}:
                    break
                # ASCII URL plus adjoining script prose terminates here. A
                # Unicode host/path begins at an explicit URL separator.
                if not unicode_body and text[end - 1] not in "/.@":
                    break
                unicode_body = True
            end += 1
        while end > match.end() and text[end - 1] in ".,;:!?":
            end -= 1
        if end > match.end():
            spans.append((match.start(), end))
    return tuple(spans)


def literal_spans(text: str) -> tuple[tuple[int, int], ...]:
    """Return maximal disjoint technical spans in source code-point offsets."""
    candidates: list[tuple[int, int]] = []
    for match in _IP.finditer(text):
        try:
            ipaddress.ip_network(match.group(), strict=False)
        except ValueError:
            continue
        candidates.append(match.span())
    for match in _IPV6.finditer(text):
        token = match.group().rstrip(".,;")
        if ":" not in token:
            continue
        while token:
            try:
                ipaddress.ip_network(token, strict=False)
            except ValueError:
                if token[-1] != ":":
                    break
                token = token[:-1]
            else:
                candidates.append((match.start(), match.start() + len(token)))
                break
    for match in _CREDENTIAL.finditer(text):
        candidates.append(match.span(1))
    candidates.extend(match.span("path") for match in _QUOTED_PATH.finditer(text))
    candidates.extend(_url_spans(text))
    for match in _VERSION.finditer(text):
        if not match.group("marker") and match.group("product").casefold() in _QUANTITY_ADVERBS:
            continue
        candidates.append(match.span())
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


# The specialist MT model sometimes re-renders the placeholder brackets next to CJK
# text (observed on production: "⟦T…:0⟧" -> "⟨T…:0⟩" for ja/zh sources whose literal
# touches ideographs; ko, which spaces its literals, is untouched). The token body is a
# secret random prefix plus an index, so only the delimiter glyphs are normalised; a
# token that is missing, reordered, duplicated or invented still fails the check below.
_BRACKET_OPEN = "⟦⟨〈《「『【［[<＜(（"
_BRACKET_CLOSE = "⟧⟩〉》」』】］]>＞)）"


def _canonical_markers(output: str, prefix: str) -> str:
    body = re.escape(prefix[1:])  # prefix == "⟦" + "T<hex>:"
    loose = re.compile(
        "[" + re.escape(_BRACKET_OPEN) + r"]\s*" + body + r"(\d+)\s*[" + re.escape(_BRACKET_CLOSE) + "]")
    return loose.sub(lambda m: f"{prefix}{m.group(1)}⟧", output)


def restore_literals(output: str, protected: ProtectedText) -> str:
    prefix = protected._prefix
    output = _canonical_markers(output, prefix)
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
