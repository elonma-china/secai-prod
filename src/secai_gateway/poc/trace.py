"""Deterministic "generation trace" — Mục 4 của `/ai/exploit*`: giải thích QUY TRÌNH đã tạo
ra PoC cho request này (tìm trong danh mục nội bộ → ưu tiên nguồn nào → có cắt gọn/hợp nhất
không → chèn kết quả), theo yêu cầu khách hàng (xem `module-map/explain-poc/`).

Thiết kế cốt lõi (đã chốt qua nhiều vòng review, xem `module-map/explain-poc/01-api-va-luong-xu-ly.txt`):
mọi câu ở đây PHẢI sinh TẤT ĐỊNH từ các biến nhánh Python đã tồn tại sẵn trong
`routes.py::_build_exploit_grounding`/`exploit_prefetch` và từ per-evidence flags do
`poc/slicer.py`/`poc/consolidate.py`/`poc/fixed_pocs.py` gắn — KHÔNG giao cho LLM tự kể lại
quy trình (đó là thứ `prompts/exploit.md`'s `evidence_policy` cấm tuyệt đối cho Mục 1-3).

Tín hiệu "AI thực sự hợp nhất" CHỈ đúng khi evidence có key `consolidated_from` (chỉ
`consolidate.py::consolidate_poc` set key này) — KHÔNG dùng bool `consolidated` đơn thuần, vì
`fixed_pocs.py::load_fixed` và `slicer.py::slice_entry` (tất định, không LLM) cũng set
`consolidated=True` để hiển thị cùng banner nhẹ cho người dùng (xem comment trong 2 file đó).
"""
from __future__ import annotations

from typing import Any

# Banner tách biệt rõ với Mục 1-3 (LLM viết) — người dùng trên UI phải phân biệt được ngay
# đây là hệ thống tự chèn, không phải model tự kể.
TRACE_HEADING = "## 4. Quy trình tạo PoC"
TRACE_BANNER = "🔧 Quy trình hệ thống (tự động, không phải AI viết):"

_NOT_FOUND = (
    "Không tìm thấy CVE này trong danh mục PoC công khai lẫn cơ sở dữ liệu nội bộ — "
    "không đủ căn cứ để tạo PoC."
)
_SYNTHESIS_FAILED = (
    "Có đủ dữ liệu tham khảo nhưng hệ thống chưa tạo được bản minh hoạ hợp lệ, vui lòng thử lại."
)
_SYNTHESIZED = (
    "Không tìm thấy mã khai thác công khai nào cho CVE này trong danh mục nội bộ. Hệ thống đã "
    "xác nhận CVE này tồn tại trong cơ sở dữ liệu nội bộ (dựa trên mô tả kỹ thuật/CWE/CVSS) và "
    "tự tổng hợp một đoạn minh hoạ dựa trên thông tin đó — KHÔNG phải mã đã kiểm chứng."
)
_CANONICAL = (
    "Hệ thống tìm thấy một PoC đã được xác minh chạy thành công trên môi trường thử nghiệm cho "
    "đúng CVE này — trả về nguyên bản, không chỉnh sửa."
)
_FIXED = (
    "Hệ thống tìm thấy một PoC đã được kỹ thuật viên hiệu chỉnh và xác minh chạy được trên môi "
    "trường thử nghiệm — trả về bản đã hiệu chỉnh."
)
_LLM_MERGE = (
    "Mã khai thác gồm nhiều tệp phức tạp — hệ thống dùng AI hợp nhất thành một tệp duy nhất "
    "chạy được, có kiểm tra lại để đảm bảo không đổi tham số/logic gốc."
)
_PRUNED = (
    "Danh mục PoC nội bộ có sẵn mã khai thác thật gồm nhiều tệp — giữ nguyên cấu trúc gốc, chỉ "
    "loại bỏ phần không cần thiết cho việc khai thác. Bước này tất định, không qua AI."
)
_SINGLE_FILE = (
    "Danh mục PoC nội bộ có sẵn mã khai thác thật (một tệp) cho CVE này — trích xuất nguyên văn "
    "phần liên quan, loại bỏ phần không cần thiết (banner, mã trang trí). Bước này tất định, "
    "không qua AI."
)
_CACHE_HIT = (
    "Mã khai thác không có sẵn trong danh mục nội bộ nhưng đã được hệ thống tải về và lưu lại "
    "từ một lần tra cứu trước — dùng lại bản đã lưu."
)
_GITHUB_ONDEMAND = (
    "Danh mục nội bộ chỉ có liên kết chưa tải — hệ thống tự động tải mã khai thác thật từ nguồn "
    "công khai tương ứng."
)
_WEB_REAL = (
    "Không có mã khai thác thật trong danh mục nội bộ — hệ thống tìm kiếm trên internet và sử "
    "dụng mã tìm được làm căn cứ."
)
_GENERIC_REAL = (
    "Danh mục PoC nội bộ có sẵn mã khai thác thật cho CVE này — sử dụng làm căn cứ chính."
)
_GENERIC_UNKNOWN = (
    "Không xác định được chính xác quy trình đã dùng cho yêu cầu này."
)

_REAL_TIERS = {"real", "real_pruned", "real_consolidated"}
_SYNTHESIZED_TIERS = {"synthesized_cve", "synthesized_readme"}


def build_generation_trace(
    acquisition: dict[str, Any],
    pre_evidence: list[dict[str, Any]] | None,
    injected: dict[str, Any],
) -> list[str]:
    """Trả về list câu tiếng Việt (1 câu trong tuyệt đại đa số case) mô tả quy trình đã tạo
    PoC cho request này. Tất định: chỉ đọc `acquisition` (flags từ `_build_exploit_grounding`)
    + `pre_evidence[0]` (evidence item thắng, đã được chèn vào Mục 1) + `injected` (report của
    `enforce_section1` — nguồn chân lý cho `section1_tier`/`candidate_status`). Không gọi LLM,
    không I/O, không raise (fail-open — case lạ rơi vào câu generic, không bao giờ trống)."""
    tier = injected.get("section1_tier") or "not_found"
    top = (pre_evidence or [{}])[0] if pre_evidence else {}

    if tier == "not_found":
        return [_NOT_FOUND]
    if tier == "synthesis_failed":
        return [_SYNTHESIS_FAILED]
    if tier in _SYNTHESIZED_TIERS:
        return [_SYNTHESIZED]
    if tier in _REAL_TIERS:
        if top.get("canonical"):
            return [_CANONICAL]
        if top.get("fixed"):
            return [_FIXED]
        if top.get("consolidated_from"):
            return [_LLM_MERGE]
        if top.get("pruned"):
            return [_PRUNED]
        if top.get("extracted"):
            if acquisition.get("cached_hit"):
                return [_CACHE_HIT]
            if acquisition.get("github_ondemand"):
                return [_GITHUB_ONDEMAND]
            if acquisition.get("web_real"):
                return [_WEB_REAL]
            return [_SINGLE_FILE]
        # Real evidence present but none of the known per-evidence flags matched (e.g. a
        # verbatim DB entry injected by a path that predates this module, or future code
        # paths) — fail-open to a generic-but-true sentence rather than a wrong specific one.
        return [_GENERIC_REAL]
    return [_GENERIC_UNKNOWN]


def render_generation_trace(trace: list[str]) -> str:
    """Markdown block cho Mục 4 — nối vào cuối `text` (description/exploit_guide/content).
    Heading dùng đúng dạng `## 4.` để khớp `FileCode.tsx::splitSection1` trên FE (coi mọi
    heading đánh số 2-9 là ranh giới section, tự rơi vào card "Phân tích / hướng dẫn")."""
    lines = "\n".join(f"- {s}" for s in trace) if trace else f"- {_GENERIC_UNKNOWN}"
    return f"\n\n{TRACE_HEADING}\n\n{TRACE_BANNER}\n\n{lines}\n"
