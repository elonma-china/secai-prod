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

# Bracket glyphs a model may re-render a placeholder with (same set technical_tokens uses
# for v2). ``_LOOSE_PH_RE`` matches every variant: ⟦L1⟧ [L1] <L1> ［L1］ 〈L1〉 …
_BRACKET_OPEN = "⟦⟨〈《「『【［[<＜(（"
_BRACKET_CLOSE = "⟧⟩〉》」』】］]>＞)）"
_LOOSE_PH_RE = re.compile(
    "[" + re.escape(_BRACKET_OPEN) + r"]\s*L\s*(\d+)\s*[" + re.escape(_BRACKET_CLOSE) + "]")
# Source text that already looks like a placeholder (any bracket variant, or any ⟦…⟧, or a
# lone ⟦/⟧) is masked as a literal FIRST, so it can neither collide with our tokens nor be
# mistaken for a "stray/renumbered" token by the integrity check.
_SOURCE_PH_LOOK = re.compile(
    r"⟦[^⟧\n]{0,64}⟧|" + _LOOSE_PH_RE.pattern + r"|[⟦⟧]")


class PlaceholderIntegrityError(Exception):
    """The model output does not carry every ``⟦Ln⟧`` exactly once (or has stray ones)."""


# ── credential labels ────────────────────────────────────────────────────────────
_LATIN_LABELS = (
    r"pass(?:word|wd|phrase|code)?|pwd|pw|secret|token|api[_-]?key|access[_-]?key"
    r"|private[_-]?key|secret[_-]?key|apikey|auth(?:orization)?|bearer|user(?:name)?"
    r"|login|account|credentials?|passcode"
    r"|mật\s*khẩu|mat\s*khau|mk|tài\s*khoản|tai\s*khoan|tên\s*đăng\s*nhập"
    r"|khóa|khoa|mã\s*pin|ma\s*pin|mã\s*xác\s*thực"
)
_CJK_LABELS = (
    r"密码|密碼|口令|账号|帐号|賬號|账户|用户名|用戶名|密钥|密鑰|令牌|"
    r"パスワード|ユーザー名|アカウント|비밀번호|암호|계정"
)
_LABEL = rf"(?:(?<![^\W_])(?:{_LATIN_LABELS})|(?:{_CJK_LABELS}))(?:[_-]\w+)*"
_LABEL_NB = rf"(?:(?:{_LATIN_LABELS})|(?:{_CJK_LABELS}))"  # inside lookaheads (no lookbehind)

# Where an unquoted, space-containing value stops: end of line, before the next label in
# the same sentence, or at a sentence-final ". ".
_STOP = (rf"(?:[,;]\s*[\"']?{_LABEL_NB}\s*[\"']?\s*[:=：﹕]"
         rf"|\s+(?:và|and|&)\s+{_LABEL_NB}\b|\.(?:\s|$))")
_QVALUE = r"\"[^\"\n]*\"|'[^'\n]*'|“[^”\n]*”|‘[^’\n]*’|「[^」\n]*」"

# label SEP value, SEP = [:=：] (optionally with quotes around the label). Value is the rest
# of the line (stopping at _STOP) or a quoted string. Only the VALUE is masked so the label
# is still translated. Over-masking is harmless: every mask is restored verbatim.
_CRED_STRONG = re.compile(
    rf"(?i)({_LABEL}[\"']?)(\s*[:=：﹕]>?\s*+)(?!\s|⟦L\d)"
    rf"((?:{_QVALUE})|(?:(?!{_STOP})[^\n])*[^\s.,;:])"
)
# "mật khẩu (của root) là X" / "password is X" / "Pwd - X": a connector word, single token.
_CRED_CONNECT = re.compile(
    rf"(?i)({_LABEL}(?:\s+(?:của|cua|of|for|cho)\s+\S+)?)"
    r"(\s+(?:is|là|la|are|=|-|–|—)\s++)(?!\s|⟦L\d)"
    rf"((?:{_QVALUE})|[^\s]*[^\s.,;:])"
)
# "pass Abc@123" / "pw Hunter2!": bare whitespace — only when the next token looks like a
# secret (has a digit or symbol), so "pass the salt" is left alone.
_CRED_BARE = re.compile(
    rf"(?i)({_LABEL})(\s++)(?!⟦L\d)((?=\S*[\d!@#$%^&*])\S*[^\s.,;:])"
)
# shell flags: mysql -u root -pSecr3t!  /  --password=x handled by _CRED_STRONG
_SHELL_P = re.compile(r"(?<![\w-])(-p)(?=[^\s-])()(?!⟦L\d)(\S{2,})")
# JSON / quoted-key form: "password": "x"  /  'pwd': 123
_CRED_JSON = re.compile(
    rf"(?i)([\"']{_LABEL}[\"']\s*:\s*+)(?!\s|⟦L\d)()((?:{_QVALUE})|[^\s,}}\]]+)"
)

# HTTP auth schemes: mask the whole "Bearer <token>" so the TOKEN is protected, not just
# the scheme word. Runs BEFORE _CRED so an enclosing "Authorization: Bearer …" leaves only
# the (translatable) label behind. Min-8 value avoids masking the bare word "token".
_AUTH_SCHEME = re.compile(r"(?i)\b(?:Bearer|Basic|Digest|Token)\s+[A-Za-z0-9._~+/=-]{8,}={0,2}")

_PHONE = re.compile(r"(?<![\w.])\+?\d[\d ().\-]{6,}\d(?![\w])")

# Whole-span literals that must be masked BEFORE credential labels so a label inside them
# (fenced code, URL query "?user=a&password=b") is protected as part of the whole span.
_SPANS_FIRST: tuple[re.Pattern[str], ...] = (
    re.compile(r"```.*?```", re.S),                                   # fenced code block
    re.compile(r"`[^`\n]+`"),                                          # inline code
    re.compile(r"-----BEGIN[^-]*-----.*?-----END[^-]*-----", re.S),    # PEM key/cert
    # spotlight wrapper tag in the source would be angle-escaped by wrap_untrusted_content
    re.compile(r"(?:<|&lt;)/?untrusted_retrieved_content(?:\s[^>&]*)?(?:>|&gt;)", re.I),
    # any scheme:// URL / connection string (incl. user:pass@host, query credentials)
    re.compile(r"\b[a-z][a-z0-9+.-]{1,15}://[^\s<>\"')\]]*[^\s<>\"')\].,;:!?]", re.I),
    re.compile(r"\b[\w.-]+:[^\s@/:]+@[\w-]+(?:\.[\w-]+)+(?::\d+)?(?:/[^\s<>\"')\]]*)?"),  # user:pass@host
)

# Remaining whole-span literals, masked after credentials, in priority order.
_SPANS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+"),  # JWT
    # prefixed API keys with an internal separator (sk_live_…, ghp_…, xoxb-…) — the
    # gateway's own secret regex misses these (it stops at the second '_').
    re.compile(r"\b(?:sk|pk|rk|ghp|gho|ghs|ghu|glpat|xox[baprs])[-_][A-Za-z0-9_-]{10,}\b"),
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
    ``⟦L{i}⟧`` and its original stored at ``literals[i]``; indices are dense and in order of
    appearance, and every one appears in ``masked_text`` exactly once. Nested placeholders
    (a credential inside a code block / URL) are resolved when the enclosing span is stored,
    so ``literals`` holds final, placeholder-free originals. Guarantees the masked text
    carries nothing ``security.redact_public_input`` would flag (the trailing safety net
    masks exactly those patterns), so it is safe to send to the model and never 422s."""
    if not text:
        return text, []
    raw: list[str] = []

    def store(span: str) -> str:
        # Resolve inner placeholders now (they all refer to earlier, already-final entries).
        # Single pass: the restored text is never rescanned, so user text that looks like a
        # placeholder (masked verbatim in step 0) stays untouched.
        resolved = _PH_RE.sub(
            lambda m: raw[int(m.group(1))] if int(m.group(1)) < len(raw) else m.group(0), span)
        raw.append(resolved)
        return _PH.format(len(raw) - 1)

    def store_new(m: re.Match[str]) -> str:
        # A safety-net span that already carries a placeholder was masked by an earlier,
        # more precise pass (e.g. "password: ⟦L0⟧"); keep the label translatable.
        span = m.group(0)
        return span if "⟦L" in span else store(span)

    def cred(m: re.Match[str]) -> str:
        return f"{m.group(1)}{m.group(2)}{store(m.group(3))}"

    def phone(m: re.Match[str]) -> str:
        return store(m.group(0)) if sum(c.isdigit() for c in m.group(0)) >= 9 else m.group(0)

    # 0. text that already looks like a placeholder → literal (no collision / false alarms)
    text = _SOURCE_PH_LOOK.sub(lambda m: store(m.group(0)), text)
    # 1. structural spans that can contain credentials (code, PEM, URL, user:pass@host)
    for rx in _SPANS_FIRST:
        text = rx.sub(lambda m: store(m.group(0)), text)
    # 2. auth-scheme tokens ("Bearer <token>") before the label rule → protect the token
    text = _AUTH_SCHEME.sub(lambda m: store(m.group(0)), text)
    # 3. credential values (value group only; label + separator preserved for translation)
    for rx in (_CRED_JSON, _CRED_STRONG, _CRED_CONNECT, _CRED_BARE, _SHELL_P):
        text = rx.sub(cred, text)
    # 4. remaining structural whole-span literals
    for rx in _SPANS:
        text = rx.sub(lambda m: store(m.group(0)), text)
    text = _PHONE.sub(phone, text)
    # 5. safety net — anything the gateway input guard would redact/422, preserved not dropped
    for rx in _SECRET_PATTERNS:
        text = rx.sub(store_new, text)
    text = _SENSITIVE_IDENTIFIERS.sub(store_new, text)
    # 6. compact: renumber to dense, order-of-appearance ids; drop entries swallowed by an
    #    enclosing span (they were resolved into it).
    literals: list[str] = []
    remap: dict[int, int] = {}

    def renumber(m: re.Match[str]) -> str:
        old = int(m.group(1))
        if old not in remap:
            remap[old] = len(literals)
            literals.append(raw[old])
        return _PH.format(remap[old])

    return _PH_RE.sub(renumber, text), literals


def unmask_literals(text: str, literals: list[str]) -> str:
    """Inverse of :func:`mask_literals`: restore ``⟦L{i}⟧`` placeholders from ``literals``
    (single pass; restored text is never rescanned). Out-of-range/garbled placeholders are
    left as-is rather than raising. For the strict, integrity-checked variant used by the
    translate endpoints see :func:`restore_checked`."""
    if not literals or not text:
        return text

    def restore(m: re.Match[str]) -> str:
        i = int(m.group(1))
        return literals[i] if 0 <= i < len(literals) else m.group(0)

    return _PH_RE.sub(restore, text)


def placeholder_ids(masked: str) -> list[int]:
    """Indices of the placeholders present in ``masked`` (order of appearance)."""
    return [int(m.group(1)) for m in _PH_RE.finditer(masked)]


def restore_checked(output: str, literals: list[str], expected: list[int] | None = None) -> str:
    """Restore placeholders in a model ``output`` ONLY if every expected one is present
    exactly once and nothing stray remains; else raise :class:`PlaceholderIntegrityError`.

    ``expected`` = the placeholder ids sent to the model for this chunk (default: all).
    Bracket-glyph variants (``[L1]`` ``<L1>`` ``［L1］`` ``〈L1〉`` …) are normalised first.
    Order is not enforced (translation may legitimately reorder), multiplicity is."""
    want = sorted(range(len(literals)) if expected is None else expected)
    canon = _LOOSE_PH_RE.sub(lambda m: _PH.format(int(m.group(1))), output)
    found = sorted(int(m.group(1)) for m in _PH_RE.finditer(canon))
    if found != want:
        raise PlaceholderIntegrityError(
            f"placeholders expected {want[:20]} got {found[:20]}")
    if "⟦" in _PH_RE.sub("", canon) or "⟧" in _PH_RE.sub("", canon):
        raise PlaceholderIntegrityError("stray placeholder bracket in output")
    return _PH_RE.sub(lambda m: literals[int(m.group(1))], canon)
