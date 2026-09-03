"""Deterministic literal masking for the translate endpoint.

The translator model (small, RCE-domain) over-refines security text and drops or
alters literal data — credentials, tokens, URLs, code, hashes. Customer report
(2026-09): "Nội dung dịch tinh chỉnh quá nhiều → mất dữ liệu quan trọng như tài
khoản và mật khẩu."

Fix: replace every literal span with an opaque placeholder ``⟦L0⟧`` BEFORE the model
sees the text, then restore the originals verbatim AFTER translation. The model can
neither reword nor lose a literal it never received, and — because the safety net
below masks exactly what ``security.redact_public_input`` would 422 on — secrets
never reach the model at all. That lets ``/ai/translate`` translate credential-bearing
text (previously a hard 422) while keeping the secrets server-side.

Ordering matters: structural/credential spans are masked first so a later, greedier
pattern (hex, base64, digit-run) cannot chew into an already-masked value; and the
placeholder syntax is chosen so no masking pattern re-matches it.
"""
from __future__ import annotations

import re

from ..security import _SECRET_PATTERNS, _SENSITIVE_IDENTIFIERS

# ⟦L{n}⟧ — U+27E6/U+27E7 are outside the CJK ranges sanitize_output_language() strips,
# and outside every masking pattern below, so a placeholder is never CJK-stripped nor
# re-masked. Restore is whitespace-tolerant in case the model pads the token.
_PH = "⟦L{}⟧"
_PH_RE = re.compile(r"⟦\s*L\s*(\d+)\s*⟧")

# label:value credentials (EN + VI). Mask ONLY the value so the label still translates
# ("Mật khẩu: ⟦L0⟧" → "Password: ⟦L0⟧" → "Password: 123456"). The negative lookahead
# stops it re-masking a value an earlier pass already replaced.
_CRED = re.compile(
    r"(?i)(?<![\w])"
    r"(pass(?:word|wd)?|pwd|secret|token|api[_-]?key|access[_-]?key|apikey"
    r"|auth(?:orization)?|bearer|user(?:name)?|login|account"
    r"|mật\s*khẩu|mat\s*khau|tài\s*khoản|tai\s*khoan"
    r"|khóa|khoa|mã\s*pin|ma\s*pin)"
    r"(\s*[:=]\s*)"
    r"(?!⟦L\d)"
    r"(\S*[^\s.,;:])"  # value: keep password specials (!@#…), drop a trailing delimiter
)

# HTTP auth schemes: mask the whole "Bearer <token>" so the TOKEN is protected, not just
# the scheme word. Runs BEFORE _CRED so an enclosing "Authorization: Bearer …" leaves only
# the (translatable) label behind. Min-8 value avoids masking the bare word "token".
_AUTH_SCHEME = re.compile(r"(?i)\b(?:Bearer|Basic|Digest|Token)\s+[A-Za-z0-9._~+/=-]{8,}={0,2}")

# Whole-span literals, masked entirely, in priority (longest/most-structural first).
_SPANS: tuple[re.Pattern[str], ...] = (
    re.compile(r"```.*?```", re.S),                                   # fenced code block
    re.compile(r"`[^`\n]+`"),                                          # inline code
    re.compile(r"-----BEGIN[^-]*-----.*?-----END[^-]*-----", re.S),    # PEM key/cert
    re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+"),  # JWT
    # prefixed API keys with an internal separator (sk_live_…, ghp_…, xoxb-…) — the
    # gateway's own secret regex misses these (it stops at the second '_').
    re.compile(r"\b(?:sk|pk|rk|ghp|gho|ghs|ghu|glpat|xox[baprs])[-_][A-Za-z0-9_-]{10,}\b"),
    re.compile(r"\b(?:https?|ftp|ssh|file)://[^\s<>\"')\]]+", re.I),   # URL
    re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b"),                       # email
    re.compile(r"\b[A-Za-z]:\\[^\s\"']+"),                            # windows path
    re.compile(r"(?<![\w/])/(?:[\w.\-]+/)+[\w.\-]+"),                  # unix abs path (2+ segs)
    re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
               r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"),                   # uuid
    re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}(?::\d+)?\b"),               # ipv4[:port]
    re.compile(r"\b[0-9a-fA-F]{16,}\b"),                              # hex / hash
    re.compile(r"\b[A-Za-z0-9+/]{24,}={0,2}\b"),                      # base64-ish blob
    re.compile(r"\bCVE-\d{4}-\d{4,7}\b", re.I),                       # CVE id
)


def mask_literals(text: str) -> tuple[str, list[str]]:
    """Return ``(masked_text, literals)``. Each literal span in ``text`` is replaced by
    ``⟦L{i}⟧`` and its original stored at ``literals[i]``. Guarantees the masked text
    carries nothing ``security.redact_public_input`` would flag (the trailing safety net
    masks exactly those patterns), so it is safe to send to the model and never 422s."""
    if not text:
        return text, []
    literals: list[str] = []

    def store(span: str) -> str:
        literals.append(span)
        return _PH.format(len(literals) - 1)

    def store_new(m: re.Match[str]) -> str:
        # A safety-net span that already carries a placeholder was masked by an earlier,
        # more precise pass (e.g. "password: ⟦L0⟧"); masking it again would nest tokens
        # that a single-pass unmask can't unwind, so leave it be.
        span = m.group(0)
        return span if "⟦L" in span else store(span)

    # 0. auth-scheme tokens ("Bearer <token>") before the label rule → protect the token
    text = _AUTH_SCHEME.sub(lambda m: store(m.group(0)), text)
    # 1. credential values (value group only; label + separator preserved for translation)
    text = _CRED.sub(lambda m: f"{m.group(1)}{m.group(2)}{store(m.group(3))}", text)
    # 2. structural whole-span literals
    for rx in _SPANS:
        text = rx.sub(lambda m: store(m.group(0)), text)
    # 3. safety net — anything the gateway input guard would redact/422, preserved not dropped
    for rx in _SECRET_PATTERNS:
        text = rx.sub(store_new, text)
    text = _SENSITIVE_IDENTIFIERS.sub(store_new, text)
    return text, literals


def unmask_literals(text: str, literals: list[str]) -> str:
    """Inverse of :func:`mask_literals`: restore ``⟦L{i}⟧`` placeholders from ``literals``.
    Out-of-range/garbled placeholders are left as-is rather than raising."""
    if not literals or not text:
        return text

    def restore(m: re.Match[str]) -> str:
        i = int(m.group(1))
        return literals[i] if 0 <= i < len(literals) else m.group(0)

    return _PH_RE.sub(restore, text)
