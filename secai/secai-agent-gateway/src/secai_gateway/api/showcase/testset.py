"""Nhóm K bổ sung — `/showcase-api/testset/*`: chứng minh chuẩn hoá bộ dữ liệu đánh giá 1000
mẫu, trả lời thắc mắc "Bộ dữ liệu 1000 mẫu dùng để đánh giá có chuẩn không, theo chuẩn nào?".

Đọc NGUYÊN VĂN từ hồ sơ gốc (`settings.showcase_kpi_results_dir`), giống mọi endpoint nhóm K
khác trong kpi_bench.py — KHÔNG đọc bản làm việc cục bộ KPI-VA-GEN-TEST/ (xem cảnh báo ở
config.py: hai bên từng lệch số, khách chỉ nên thấy số khớp hồ sơ gốc).
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter

from ...config import get_settings
from ._common import not_found

router = APIRouter(prefix="/showcase-api/testset", tags=["showcase-testset"])


def _dossier() -> Path:
    return Path(get_settings().showcase_kpi_results_dir)


@router.get("/standardization")
def standardization() -> dict:
    """Toàn văn 'Chứng minh chuẩn hoá bộ dữ liệu đánh giá 1000 mẫu' — trả nguyên file markdown
    trong hồ sơ gốc, FE tự tách theo heading ## thành từng mục hiển thị riêng."""
    path = _dossier() / "CHUAN-HOA-BO-1000-MAU-DANH-GIA.md"
    if not path.is_file():
        raise not_found(f"Không tìm thấy hồ sơ chuẩn hoá bộ 1000 mẫu: {path}")
    return {
        "source": "03. Kpi_results/CHUAN-HOA-BO-1000-MAU-DANH-GIA.md",
        "format": "md",
        "content": path.read_text(encoding="utf-8"),
    }
