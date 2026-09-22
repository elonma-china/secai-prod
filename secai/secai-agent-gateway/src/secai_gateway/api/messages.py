"""Per-endpoint user-message builders (mirror the legacy gemini field templates) and the
statistical-report JSON parser."""
from __future__ import annotations

import json
import re
from typing import Any

from ..security import require_public_input
from .literal_mask import mask_literals
from .schemas import LegacyRequest


def _field(body: LegacyRequest, name: str) -> Any:
    return (body.model_dump() or {}).get(name)


def translate_langs(body: LegacyRequest) -> tuple[Any, Any]:
    """Resolve (source, target) languages from the request. `language` is the TARGET in the
    FE contract ("dịch sang <language>"), so it falls back into `tgt` — NOT `src`.
    (Previously it was OR'd into `src`, which silently dropped the caller's target: e.g.
    English text + language="vi" hit the auto-detect branch and echoed English back.)"""
    return _field(body, "source_lang"), (_field(body, "target_lang") or _field(body, "language"))


# TR1 fix (2026-09-22): the model has a strong learned bias to answer Vietnamese-sourced
# translate requests in English, strong enough to override an explicit target_lang="vi"
# whenever the instruction reads as a same-language (no-op) translate — reproduced live on
# :8131 with EVERY phrasing of "dịch từ vi sang vi" (bare code AND full name "Tiếng Việt"
# on both sides), while cross-language requests (vi→en, en→vi, vi→fr, en→en, fr→fr) were
# already correct. Bare 2-letter target codes make it worse (target_lang="vi" alone, no
# source, also came back in English) — the model doesn't reliably resolve "vi" as a
# language name the way it does "en"/"fr". Fix: (1) map short codes to their full Vietnamese
# display name for the instruction sentence, (2) always append an explicit language-lock
# guard (below).
#
# ROUND 2 (2026-09-22): the first guard wording ("nếu văn bản gốc đã ở ngôn ngữ đích, giữ
# nguyên") was itself whack-a-mole — it covered the literal source==target case but a live
# CVE-record changelog/diff shape (`history[].old`/`.new`, "mô tả ... TRƯỚC KHI được cập
# nhật bởi ...") still flipped to English, and separately so did short CVE-title-style
# strings ("Lỗ hổng thực thi mã từ xa trong Apache Struts2" → the canonical English CVE
# title the model has memorized). Both reproduced deterministically. Listing more trigger
# phrases would never end, so the guard below is an unconditional, absolute language lock —
# ALWAYS output entirely in the target language, regardless of the source text's topic,
# style, tense/history framing, or how familiar/canonical the English phrasing is — instead
# of a conditional "if already in target, keep it" rule. Verified live: the two new failure
# shapes above (+ the JSON `history[].old/new` path) plus the original source==target case
# and all 4 cross-language directions, 3-5 reruns each, 100% correct.
_LANG_DISPLAY = {"vi": "tiếng Việt", "en": "tiếng Anh"}


def _lang_display_name(code: Any) -> str | None:
    """Full Vietnamese display name for a short language code, or None if unknown/unmapped
    (in which case the raw value is left as-is — it already works untranslated, e.g. "fr")."""
    if not code:
        return None
    mapped = _LANG_DISPLAY.get(str(code).strip().lower())
    return mapped if mapped and mapped.lower() != str(code).strip().lower() else None


def _keep_target_language_note(tgt: Any) -> str:
    """Absolute language-lock: target_lang always wins, unconditionally — not just in the
    literal source==target case, but for ANY content shape (titles, changelog/history
    old-vs-new diffs, familiar English security phrasing, ...) that could otherwise tempt
    the model to drift into English or another language."""
    name = _lang_display_name(tgt)
    label = f"{tgt} ({name})" if name else tgt
    return (
        f" QUY TẮC NGÔN NGỮ BẮT BUỘC: TOÀN BỘ bản dịch phải được viết bằng {label}, không có "
        "ngoại lệ — bất kể văn bản gốc nói về chủ đề gì, thuộc phong cách nào (kể cả tiêu "
        "đề/tên gọi ngắn gọn, mô tả trạng thái cũ/trước khi cập nhật/nhật ký thay đổi — "
        "old/before/changelog/history), hay văn bản gốc đã sẵn ở ngôn ngữ này. Dù câu tiếng "
        "Anh tương ứng nghe quen thuộc/phổ biến trong lĩnh vực bảo mật đến đâu, TUYỆT ĐỐI "
        "KHÔNG được tự ý quay về phiên bản tiếng Anh quen thuộc đó hay chuyển sang bất kỳ "
        f"ngôn ngữ nào khác ngoài {label}, vì bất kỳ lý do gì."
    )


def build_translate_prompt(text: str, src: Any, tgt: Any) -> tuple[str, list[str]]:
    """Mask literals in ``text`` and wrap it in the translate instruction for (src, tgt).

    Returns ``(prompt, literals)``. Literal spans (credentials, tokens, URLs, code, hashes)
    are masked to ``⟦L{i}⟧`` placeholders BEFORE the model — the caller (routes.translate/*)
    restores them via ``unmask_literals`` after generation, so the model can neither reword
    nor drop them. Masking also strips every secret pattern the input guard would 422 on, so
    — unlike the other endpoints — translate does NOT call ``require_public_input``:
    credential-bearing text is translatable and the secrets stay server-side.
    """
    masked, literals = mask_literals(text)
    # Giữ nguyên thuật ngữ kỹ thuật (tên hàm/API, gói/thư viện/sản phẩm, giao thức, mã
    # CVE/CWE, đoạn code, số phiên bản...) — chỉ dịch phần văn xuôi diễn giải xung quanh.
    note = (
        " Giữ nguyên (không dịch) các thuật ngữ kỹ thuật/chuyên ngành: tên hàm, API, "
        "gói/thư viện/sản phẩm, giao thức, mã CVE/CWE, đoạn code, tên biến, số phiên bản "
        "— chỉ dịch phần văn xuôi diễn giải."
    )
    keep_note = _keep_target_language_note(tgt) if tgt else ""
    if src and tgt:
        return f"Dịch văn bản sau từ {src} sang {tgt}.{note}{keep_note}\n\n{masked}", literals
    if tgt:
        return f"Dịch văn bản sau sang {tgt}.{note}{keep_note}\n\n{masked}", literals
    return f"Dịch văn bản sau (tự nhận diện ngôn ngữ nguồn; Việt→Anh hoặc Anh→Việt).{note}\n\n{masked}", literals


def translate_prompt(body: LegacyRequest) -> tuple[str, list[str]]:
    """Single-chunk convenience wrapper: build the prompt for the whole supplied text."""
    src, tgt = translate_langs(body)
    return build_translate_prompt(body.supplied_text(), src, tgt)


def report_prompt(body: LegacyRequest) -> str:
    logs = _field(body, "logs")
    logs = require_public_input(str(logs)) if logs else require_public_input(body.supplied_text())
    sections = _field(body, "sections") or ["overview", "details", "recommendations"]
    return f"Viết báo cáo theo các phần: {', '.join(map(str, sections))}.\n\nLogs/Dữ liệu:\n{logs}"


def statistical_prompt(body: LegacyRequest) -> str:
    from_date = _field(body, "from_date") or "?"
    to_date = _field(body, "to_date") or "?"
    summary = _field(body, "summary")
    detail = _field(body, "detail")
    if not summary and not detail:
        summary = body.supplied_text()  # fall back to any free text supplied
    parts = [f"Khoảng thời gian: từ {from_date} đến {to_date}"]
    if summary:
        parts.append(f"Tổng quan: {require_public_input(str(summary))}")
    if detail:
        parts.append(f"Chi tiết lỗ hổng: {require_public_input(str(detail))}")
    return "\n".join(parts)


def parse_statistical(text: str) -> dict[str, str]:
    """Destructure the model's JSON into the 3 legacy fields; fall back to raw text."""
    match = re.search(r"\{.*\}", text.strip(), re.S)
    try:
        data = json.loads(match.group(0) if match else text)
        if not isinstance(data, dict):
            raise ValueError
    except (ValueError, json.JSONDecodeError):
        return {"report_title": "", "report_summary": text, "report_detail": ""}
    norm = {str(k).lower().replace(" ", "_"): str(v) for k, v in data.items()}
    return {
        "report_title": norm.get("report_title", ""),
        "report_summary": norm.get("report_summary", ""),
        "report_detail": norm.get("report_detail", ""),
    }
