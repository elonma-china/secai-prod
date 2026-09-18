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
    if src and tgt:
        return f"Dịch văn bản sau từ {src} sang {tgt}.{note}\n\n{masked}", literals
    if tgt:
        return f"Dịch văn bản sau sang {tgt}.{note}\n\n{masked}", literals
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
