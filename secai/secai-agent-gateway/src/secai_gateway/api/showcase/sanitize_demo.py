"""Nhóm L — `POST /showcase-api/sanitize-demo` (docs/nghiem-thu-showcase/01-API-DESIGN.md §Nhóm L).

Chạy ĐÚNG bộ dò/làm sạch mà KPI **R-SEC-PII** (F1 98.06%, tiêu chí nghiệm thu dòng 554–555) đo:
``detector.py`` trong hồ sơ gốc (``03. Kpi_results/Santization/Evaluation_code/detector.py``) —
hai tầng "Regex rules + entropy classifier", bắt **email / ip / phone / api_key / password**.
Đây là chính mã nguồn kịch bản trỏ tới ("chạy đoạn code kia"), không phải bản mock hay hàm khác.

Stdlib-only nên import trực tiếp từ file trong hồ sơ gốc (Settings.showcase_sanitizer_detector).
Nội dung xử lý trong bộ nhớ, không log/lưu.
"""

from __future__ import annotations

import importlib.util
from collections import Counter
from functools import lru_cache
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel

from ...config import get_settings
from ._common import not_found

router = APIRouter(prefix="/showcase-api", tags=["showcase-sanitize-demo"])


@lru_cache(maxsize=1)
def _detector() -> Any:
    """Nạp detector.py thật trong hồ sơ gốc (cache 1 lần)."""
    path = get_settings().showcase_sanitizer_detector
    spec = importlib.util.spec_from_file_location("secai_kpi_detector", path)
    if spec is None or spec.loader is None:
        raise not_found(f"Không nạp được detector.py: {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class SanitizeDemoRequest(BaseModel):
    text: str


@router.post("/sanitize-demo")
def sanitize_demo(body: SanitizeDemoRequest) -> dict:
    text = body.text or ""
    det = _detector()
    spans = det.detect(text)
    counts = Counter(s["type"] for s in spans)
    return {
        "original_length": len(text),
        "sanitized_text": det.redact(text),
        "entities_found": [{"type": t, "count": c} for t, c in sorted(counts.items())],
        "engine": "detector.py (hồ sơ gốc · 03. Kpi_results/Santization/Evaluation_code) — chính bộ "
                  "dò mà KPI R-SEC-PII đo (F1 98.06%): Regex + entropy, bắt email/ip/phone/api_key/password",
        "source": "03. Kpi_results/Santization/Evaluation_code/detector.py",
    }
