"""Result-building helpers for KPI Studio "Live" run (`showcase/kpi_run.py`).

Stdlib-only (không pandas/clearml — cùng nguyên tắc với `kpi_studio.py`) — chuyển kết quả
THẬT từ 2 nguồn CLI bench khác nhau thành đúng shape `Result` mà FE (`renderRunSummary`)
đang tiêu thụ (xem KPI-RUN-CONTRACT.md):

  - Nhóm B/C: `bench_kpi.py --out <file>` ghi 1 file JSON PHẲNG ``{kpi_id: value_number}``
    (không có pass/threshold) — pass/threshold ở đây được TỰ TÍNH lại từ catalog KPI Studio
    (`kpi_studio._KPI`, field ``op``/``threshold``), KHÔNG dùng logic pass nội bộ của
    bench_kpi.py (script đó có vài quyết định kinh doanh riêng — vd R-PERF-TPS vượt trần vẫn
    tính đạt — catalog Studio không mã hoá lại quyết định đó, xem CONTRACT).
  - Nhóm A: `bench.sh` ghi ``runs/latest/**/kpi-summary.json`` — mỗi entry ĐÃ có sẵn
    value/pass/thr/unit/name (tính bởi `build_reports.py` phía script) — map thẳng, không
    tính lại gì.

Import catalog trực tiếp từ `kpi_studio.py` (module tĩnh, không I/O) để 2 module không lệch
định nghĩa ngưỡng/tên KPI.
"""

from __future__ import annotations

from typing import Any

from .kpi_studio import _KPI, _threshold_display

# {code: kpi_dict} — tra cứu O(1) theo test_id, dùng chung cho cả build B/C lẫn A.
_KPI_INDEX: dict[str, dict[str, Any]] = {k["code"]: k for k in _KPI}


def group_of(test_id: str) -> str | None:
    """Nhóm (A/B/C) của 1 test_id theo catalog Studio, hoặc None nếu không có trong catalog."""
    kpi = _KPI_INDEX.get(test_id)
    return kpi["group"] if kpi else None


def kpi_ids_for_group(group: str) -> list[str]:
    """Danh sách test_id thuộc 1 nhóm, giữ đúng thứ tự khai báo trong catalog."""
    g = group.upper()
    return [k["code"] for k in _KPI if k["group"] == g]


def all_kpi_ids() -> list[str]:
    return [k["code"] for k in _KPI]


def _bc_pass(kpi: dict[str, Any], value: float) -> bool:
    """Tính pass cho 1 KPI nhóm B/C theo đúng công thức trong CONTRACT (op + threshold của
    catalog Studio). ``op == "~"`` (R-PERF-RPS/R-PERF-CCU) KHÔNG nằm trong danh sách công thức
    của CONTRACT — 2 KPI đó tự nhận "KHÔNG PHẢI GATE THẬT" trong ghi chú catalog (enforced
    False/"partial"), nên coi có số đo là đạt (không so ngưỡng), khớp tinh thần
    `kpi_studio._pass_rule` cho các KPI không enforced."""
    # KPI không phải "gate thật" (enforced != True: R-PERF-TPS/TTFT/RPS/CCU) — hồ sơ gốc/Demo
    # luôn coi "có số đo là đạt", không so ngưỡng (xem kpi_studio._pass_rule). Live theo đúng vậy
    # để 2 chế độ nhất quán; chỉ KPI enforced=True mới so ngưỡng cứng bên dưới.
    if kpi.get("enforced") is not True:
        return True
    op, thr = kpi["op"], kpi["threshold"]
    if op == "range" and isinstance(thr, (list, tuple)) and len(thr) == 2:
        lo, hi = thr
        return lo <= value <= hi
    if op == ">":
        return value > thr
    if op == ">=":
        return value >= thr
    if op == "<":
        return value < thr
    if op == "<=":
        return value <= thr
    if op == "=":
        return value == thr
    # "~" hoặc op lạ khác — không phải gate thật, có số đo là coi như đạt.
    return True


def build_bc_result(test_id: str, raw_value: Any) -> dict[str, Any]:
    """1 Result cho 1 KPI nhóm B/C, từ giá trị đọc trong file --out phẳng của bench_kpi.py
    (``raw_value`` có thể là None nếu KPI đó không xuất hiện trong file — bench_kpi.py lỗi/
    KPI ngoài scope --group đã chạy)."""
    kpi = _KPI_INDEX.get(test_id)
    if kpi is None:
        return {"test_id": test_id, "name": test_id, "group": "?", "value": None, "unit": "",
                "threshold": "", "pass": False,
                "error": f"KPI '{test_id}' không có trong catalog KPI Studio."}

    base = {"test_id": test_id, "name": kpi["name"], "group": kpi["group"], "unit": kpi["unit"],
            "threshold": _threshold_display(kpi)}
    if raw_value is None:
        return {**base, "value": None, "pass": False,
                "error": "Không đo được — KPI này vắng mặt trong kết quả bench_kpi.py."}
    try:
        value = float(raw_value)
    except (TypeError, ValueError):
        return {**base, "value": None, "pass": False,
                "error": f"Giá trị đo không phải số: {raw_value!r}"}
    return {**base, "value": value, "pass": _bc_pass(kpi, value)}


def build_bc_results(test_ids: list[str], flat: dict[str, Any]) -> list[dict[str, Any]]:
    """Danh sách Result cho scope (giữ đúng thứ tự ``test_ids``) từ file --out phẳng."""
    return [build_bc_result(tid, flat.get(tid)) for tid in test_ids]


def build_a_result(test_id: str, entry: dict[str, Any] | None) -> dict[str, Any]:
    """1 Result cho 1 KPI nhóm A, từ 1 entry của ``kpi-summary.json`` (đã có sẵn value/pass/
    thr/unit/name — map thẳng, KHÔNG tính lại — theo đúng CONTRACT)."""
    kpi = _KPI_INDEX.get(test_id)
    fallback_name = kpi["name"] if kpi else test_id
    fallback_unit = kpi["unit"] if kpi else ""
    fallback_thr = _threshold_display(kpi) if kpi else ""
    group = kpi["group"] if kpi else "A"

    if entry is None:
        return {"test_id": test_id, "name": fallback_name, "group": group, "value": None,
                "unit": fallback_unit, "threshold": fallback_thr, "pass": False,
                "error": "Không có trong kpi-summary.json của lần chạy này."}

    value = entry.get("value")
    result: dict[str, Any] = {
        "test_id": test_id,
        "name": entry.get("name") or fallback_name,
        "group": group,
        "value": value if isinstance(value, (int, float)) else None,
        "unit": entry.get("unit", fallback_unit),
        "threshold": entry.get("thr", fallback_thr),
        "pass": bool(entry.get("pass")),
    }
    note = entry.get("note")
    if note:
        result["note"] = note
    if not isinstance(value, (int, float)):
        result["error"] = "Không đo được (value rỗng/không hợp lệ trong kpi-summary.json)."
    return result


def build_a_results(test_ids: list[str], kpi_summary: dict[str, Any]) -> list[dict[str, Any]]:
    """Danh sách Result cho scope (giữ đúng thứ tự ``test_ids``) từ ``kpi-summary.json``."""
    return [build_a_result(tid, kpi_summary.get(tid)) for tid in test_ids]
