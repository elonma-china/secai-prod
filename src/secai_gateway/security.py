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
    r"(tool_discipline|qdrant_search|neo4j_search|poc_search|web_fetch"
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


# Internal tool names the model must never echo into the visible answer (base_system.md
# output_integrity forbids it, but the model doesn't always comply). A leak looks like
# `qdrant_search(query="CVE-2026-4253333")` — a bare call echo, not prose. strip_reasoning_leak
# lists these as markers too, but it only cuts text when it ALSO finds an "answer pivot"; a
# standalone call echo has no pivot and slips through. This strips the echo directly.
_TOOL_NAMES_ALT = (
    r"qdrant_search|neo4j_search|poc_search|poc_fetch|poc_verify|poc_scan|poc_exec"
    r"|poc_kt_exploit|web_fetch|run_recon|run_vuln_scan|bh_http_probe"
)
# Whole-line echo (optional markdown/quote lead-in, optional trailing punctuation) → drop the
# entire line including its newline, so no blank gap is left behind.
_TOOL_CALL_LINE = re.compile(
    r"(?im)^[ \t>*_#-]*(?:" + _TOOL_NAMES_ALT + r")\s*\([^\n]*\)[ \t.。;:]*$\n?"
)
# Inline echo mid-sentence → remove just the `name(...)` span, leave surrounding words.
_TOOL_CALL_INLINE = re.compile(
    r"(?i)\b(?:" + _TOOL_NAMES_ALT + r")\s*\([^\n]*?\)"
)
_MULTI_BLANK = re.compile(r"\n{3,}")


def strip_tool_call_echoes(text: str) -> str:
    """Remove leaked internal tool-call echoes (e.g. ``qdrant_search(query="…")``) from the
    visible answer. Fail-open: never blanks the whole message."""
    if not text:
        return text
    cleaned = _TOOL_CALL_LINE.sub("", text)
    cleaned = _TOOL_CALL_INLINE.sub("", cleaned)
    cleaned = _MULTI_BLANK.sub("\n\n", cleaned)
    return cleaned.strip() or text.strip()


# The exploit model sometimes narrates HOW it built the report instead of just writing it —
# echoing exploit.md's own methodology wording and referring to the report's sections in
# prose. Observed live: "Kết luận về Mục 2: Vì không có tài liệu … được xác thực trong dữ
# liệu nội bộ … nên chỉ dựa vào đó để trình bày cách chạy bằng tiếng Việt (diễn giải lại,
# không sao chép nguyên văn)." base_system.md output_integrity already forbids this; the
# model doesn't reliably comply, so strip it deterministically (same approach as
# strip_system_narration / strip_tool_call_echoes).
#
# "(… không sao chép nguyên văn)" — a methodology aside lifted verbatim from exploit.md;
# never legitimate user-facing content. Removed inline so an otherwise-useful line survives.
_META_METHOD_PAREN = re.compile(r"[ \t]*\((?:[^)\n]*\bsao chép nguyên văn\b[^)\n]*)\)")
# Any remaining line still carrying that methodology phrase (not in parentheses) → drop it.
_META_METHOD_LINE = re.compile(r"(?im)^[^\n]*\bsao chép nguyên văn\b[^\n]*\n?")
# The model referring to the report's own sections in prose ("Kết luận về Mục 2: …",
# "Lưu ý về Mục 3: …"). Requires BOTH a label lead-word AND a "Mục 1–3" reference, so a
# legit "Tóm lại, lỗ hổng cho phép RCE" (no section number) or "mục tiêu"/"mục đích" is
# never matched, and the "## 2."/"## 3." headings (no lead word) are untouched.
_META_SECTION_LINE = re.compile(
    r"(?im)^[ \t>*_#-]*"
    r"(?:kết luận|tóm lại|tóm tắt|ghi chú|lưu ý|nhận xét|về việc|đối với|liên quan(?:\s+đến)?)"
    r"[^\n]*\bmục\s*[1-3]\b[^\n]*\n?"
)


def strip_meta_narration(text: str) -> str:
    """Remove leaked meta-commentary where the model narrates how it constructed the report
    (methodology asides + prose references to its own "Mục N" sections), leaving the actual
    technical content. Fail-open: never blanks the whole message."""
    if not text:
        return text
    cleaned = _META_METHOD_PAREN.sub("", text)
    cleaned = _META_SECTION_LINE.sub("", cleaned)
    cleaned = _META_METHOD_LINE.sub("", cleaned)
    cleaned = _MULTI_BLANK.sub("\n\n", cleaned)
    return cleaned.strip() or text.strip()


# ── Output XSS neutralization (defense-in-depth) ─────────────────────────────
# This assistant EXPLAINS attacks (XSS/SQLi) and its answers legitimately contain payloads. A
# frontend that renders the answer as HTML/markdown WITHOUT sanitizing executes them — observed
# live on ias.id.vn: a markdown link `[Click me](javascript:alert(1))` and a raw `<img onerror=…>`
# fired `alert()` in the chat UI. The frontend is the correct place to sanitize (e.g. DOMPurify +
# blocking javascript: links), but as backend defense-in-depth we DEFANG active content so no
# answer the gateway emits can execute, while the payload stays visible/educational.
_ZWSP = "\u200b"
# Dangerous URL schemes in markdown links / hrefs. A zero-width space after the scheme name keeps
# it readable but breaks scheme recognition in every renderer/browser — HTML entities like `&#58;`
# are DECODED inside an href attribute and do NOT protect, but a ZWSP is not decoded/removed.
_ACTIVE_SCHEME = re.compile(r"(?i)\b(javascript|vbscript)(\s*):")
# `data:` only when it carries executable content (keep data:image/png inline images working).
_DATA_ACTIVE = re.compile(r"(?i)\b(data)(\s*):(?=\s*(?:text/html|image/svg))")
# Real HTML tags → escape the angle brackets so the browser can never build a live element
# (kills <script>, <img onerror>, <svg onload>, <iframe>, <a href=javascript:…>, <style>, …).
# The `<` must sit IMMEDIATELY before the tag name (no space) — real HTML never allows `< img`,
# so `a < b and c > d` (maths) is left untouched.
_HTML_TAGISH = re.compile(r"(?is)</?[a-z][a-z0-9-]*(?:[\s/][^<>]*)?/?>")
# Inline event handlers that could ride a reconstructed tag → break the '=' (belt-and-suspenders).
_EVENT_HANDLER = re.compile(r"(?i)\b(on[a-z]{3,})(\s*)=")


def neutralize_active_content(text: str) -> str:
    """Defang executable HTML/JS in assistant output so a frontend cannot run it (anti-XSS).

    Keeps the payload human-readable (educational) but inert: dangerous URL schemes get a
    zero-width break, HTML tags are angle-escaped, event-handler ``=`` is broken. Idempotent.
    """
    if not text:
        return text
    out = _ACTIVE_SCHEME.sub(lambda m: f"{m.group(1)}{m.group(2)}{_ZWSP}:", text)
    out = _DATA_ACTIVE.sub(lambda m: f"{m.group(1)}{m.group(2)}{_ZWSP}:", out)
    out = _HTML_TAGISH.sub(lambda m: m.group(0).replace("<", "&lt;").replace(">", "&gt;"), out)
    out = _EVENT_HANDLER.sub(lambda m: f"{m.group(1)}{m.group(2)}{_ZWSP}=", out)
    return out


def sanitize_output_language(text: str, *, max_ratio: float = 0.15) -> str:
    """Clean assistant output before it reaches the user: drop leaked reasoning/meta
    planning, strip CJK ideographs (VI/EN-only), then defang active HTML/JS (anti-XSS).

    Always strips. ``max_ratio`` is unused (kept for call-site compatibility);
    use ``cjk_ratio`` separately if you need contamination metrics.
    """
    return neutralize_active_content(remove_chinese_content(
        strip_meta_narration(strip_tool_call_echoes(strip_reasoning_leak(text)))))


# ── Untrusted-content spotlighting (indirect prompt-injection defense) ──────────
# Retrieved/fetched text (KB search hits, web/GitHub fetches, PoC source/README, evidence
# appendices) can carry adversarial instructions riding along as data — "indirect" prompt
# injection, since it never appears in the user's own message (the pre-agent chat gate in
# sanitize/ never sees it). Wrapping it in an explicit delimiter tag — paired with
# <untrusted_content_policy> in prompts/base_system.md — gives the model a structural
# boundary between trusted instructions and untrusted data instead of relying on prose
# framing alone. Used by agent/hooks.py (tool-call results) and api/routes.py (the
# deterministic prefetch/web-hints/consolidated prompt appendices).
UNTRUSTED_CONTENT_TAG = "untrusted_retrieved_content"
_FORGED_DELIM_RE = re.compile(r"</?" + UNTRUSTED_CONTENT_TAG + r"(?:\s[^>]*)?>", re.IGNORECASE)


def _xml_attr(value: str) -> str:
    return (value.replace("&", "&amp;").replace('"', "&quot;")
            .replace("<", "&lt;").replace(">", "&gt;"))


def wrap_untrusted_content(text: str, *, source: str) -> str:
    """Wrap externally-sourced text in an explicit untrusted-content delimiter.

    Any literal occurrence of the delimiter tag ALREADY inside ``text`` is neutralized first
    (angle-escaped) so attacker-controlled content cannot forge a closing tag and "escape" the
    boundary — a known weakness of naive delimiter-only spotlighting. No-op on empty text.
    """
    if not text:
        return text
    neutralized = _FORGED_DELIM_RE.sub(
        lambda m: m.group(0).replace("<", "&lt;").replace(">", "&gt;"), text)
    return (f'<{UNTRUSTED_CONTENT_TAG} source="{_xml_attr(source)}">\n{neutralized}\n'
            f'</{UNTRUSTED_CONTENT_TAG}>')
