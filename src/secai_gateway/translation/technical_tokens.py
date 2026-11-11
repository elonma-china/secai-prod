"""Lossless, occurrence-aware masking of technical literals for translation.

All matches are found in the original string before replacement. This prevents a
later expression from matching inside a placeholder or nesting a prior match.
"""
from __future__ import annotations

import hashlib
import hmac
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
# Value stops before a trailing "," or ";" (sentence punctuation, not part of a
# secret). Over-masking is harmless: mask-all-then-restore is lossless.
_CREDENTIAL = re.compile(
    r"(?i)(?<![A-Za-z0-9_])(?:(?:password|passwd|pwd|secret|token|api[_-]?key|authorization|"
    r"mật\s*khẩu|tài\s*khoản)\s*[:=]\s*|(?P<prose>mật\s*khẩu\s+là\s+))"
    r"(?P<value>\S+?)(?=[,;]?(?:\s|$))")
# Weaker labels also get masked in the source, but a translated label ("Người dùng:"
# -> "User:") must not turn its translated value into an "invented literal" when the
# output is re-scanned, so these are skipped by the output-side integrity check.
_CREDENTIAL_WEAK = re.compile(
    r"(?i)(?<![A-Za-z0-9_])(?:pass|pw|user(?:name)?|login|account|mk)\s*[:=]\s*"
    r"(\S+?)(?=[,;]?(?:\s|$))")
_IP = re.compile(r"(?<![A-Za-z0-9_.:])(?:\d{1,3}\.){3}\d{1,3}(?:/\d{1,2})?"
                 r"(?![A-Za-z0-9_/]|\.[A-Za-z0-9])")
_IPV6 = re.compile(r"(?<![A-Za-z0-9_.:%-])[0-9A-Fa-f:.]+"
                   r"(?:%[A-Za-z0-9_.-]+)?(?:/\d{1,3})?"
                   r"(?![A-Za-z0-9_:/%-]|\.[A-Za-z0-9])")
_URL_START = re.compile(r"(?<![A-Za-z0-9_.-])[a-z][a-z0-9+.-]*://", re.I)
_KNOWN_SCHEMES = ("https", "http", "ftp", "ssh", "file")
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
    # Each argument must be a whole ASCII token not glued to a non-ASCII letter, so
    # "ssh root@host -p 22 với" does not swallow the "v" of the Vietnamese "với".
    # Only Latin-script letters stop a token: CJK/Cyrillic/Arabic prose legitimately
    # touches an ASCII argument without a space.
    re.compile(r"(?<![A-Za-z0-9_])(?:curl|wget|nmap|ssh|sudo|pip|npm|docker|kubectl)\s+"
               r"[A-Za-z0-9_./:@%?=&+~-]+(?![A-Za-z0-9_./:@%?=&+~-])(?![À-ɏḀ-ỿ])"
               r"(?:\s+[A-Za-z0-9_./:@%?=&+~-]+(?![A-Za-z0-9_./:@%?=&+~-])(?![À-ɏḀ-ỿ]))*"),
    re.compile(r"(?<![A-Za-z0-9_.%+-])[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}"),
    re.compile(r"(?<![\w.+-])\+?\d[\d ()-]{7,}\d(?!\w)"),
    *(re.compile(pattern.pattern, (pattern.flags & ~re.UNICODE) | re.ASCII)
      for pattern in _SECRET_PATTERNS),
    re.compile(_SENSITIVE_IDENTIFIERS.pattern,
               (_SENSITIVE_IDENTIFIERS.flags & ~re.UNICODE) | re.ASCII),
)


def _url_spans(text: str) -> tuple[tuple[int, int], ...]:
    spans: list[tuple[int, int]] = []
    for match in _URL_START.finditer(text):
        scheme = match.group()[:-3].lower()
        if scheme not in _KNOWN_SCHEMES and scheme.endswith(_KNOWN_SCHEMES):
            continue  # "prefixhttps://": a longer identifier, not the known scheme
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


def literal_spans(text: str, *, weak_labels: bool = True,
                  prose_secrets_only: bool = False) -> tuple[tuple[int, int], ...]:
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
        # "mật khẩu là <word>" is ordinary Vietnamese prose ("the password is a long string"):
        # when re-scanning a *translation*, only a secret-looking value (digit/symbol) counts.
        if (prose_secrets_only and match.group("prose")
                and all(ch.isalpha() for ch in match.group("value").strip(".,;:!?()\"'")) ):
            continue
        candidates.append(match.span("value"))
    if weak_labels:
        for match in _CREDENTIAL_WEAK.finditer(text):
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


def _prefix(text: str, salt: bytes | None) -> str:
    """HMAC(salt, text) prefix: reproducible per request salt, unguessable without it."""
    counter = 0
    while True:
        if salt is None:
            body = secrets.token_hex(12)
        else:
            body = hmac.new(salt, f"{counter}\x00{text}".encode(), hashlib.sha256).hexdigest()[:24]
        prefix = f"⟦T{body}:"
        if prefix not in text:
            return prefix
        counter += 1


def protect_literals(text: str, salt: bytes | None = None) -> ProtectedText:
    spans = literal_spans(text)
    prefix = _prefix(text, salt)
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
    return restore_literals_with_ranges(output, protected)[0]


def restore_literals_with_ranges(
        output: str, protected: ProtectedText) -> tuple[str, tuple[tuple[int, int], ...]]:
    """Restore literals and return the (start, end) offsets each one occupies in the result."""
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
    position = 0
    chunks: list[str] = []
    ranges: list[tuple[int, int]] = []
    for match, literal in zip(found, protected._literals):
        gap = output[cursor:match.start()]
        chunks.extend((gap, literal))
        position += len(gap)
        ranges.append((position, position + len(literal)))
        position += len(literal)
        cursor = match.end()
    chunks.append(output[cursor:])
    return "".join(chunks), tuple(ranges)
