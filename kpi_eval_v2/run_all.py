"""Orchestrator BỘ ĐÁNH GIÁ KPI v2.

Gọi 3 runner nhóm (quality/performance/pii), gộp kết quả, ghi summary + báo cáo.
Mỗi runner PHẢI expose hàm:
    kpi_eval_v2.quality.run:      run_quality() -> list[dict]
    kpi_eval_v2.performance.run:  run_performance() -> list[dict]
    kpi_eval_v2.pii.run:          run_pii() -> list[dict]
Mỗi dict tạo bằng schema.make_result().

Chạy:  python -m kpi_eval_v2.run_all [--only quality,pii]
"""
from __future__ import annotations

import argparse
import traceback

from . import schema


def _safe(group: str, fn):
    try:
        return list(fn())
    except Exception as e:  # noqa: BLE001 — 1 nhóm hỏng không được làm sập cả bộ
        traceback.print_exc()
        return [schema.make_result(
            f"R2-{group.upper()}-ERROR", None,
            method="(runner lỗi)", basis="", notes=f"{type(e).__name__}: {e}")]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="quality,performance,pii",
                    help="danh sách nhóm cần chạy, phân tách bởi dấu phẩy")
    args = ap.parse_args()
    only = {s.strip() for s in args.only.split(",") if s.strip()}

    results: list[dict] = []
    if "quality" in only:
        from .quality.run import run_quality
        results += _safe("quality", run_quality)
    if "performance" in only:
        from .performance.run import run_performance
        results += _safe("performance", run_performance)
    if "pii" in only:
        from .pii.run import run_pii
        results += _safe("pii", run_pii)

    # Gộp với summary cũ: giữ nguyên các nhóm KHÔNG chạy lần này (tránh --only ghi đè mất nhóm khác).
    import json
    from . import config
    prev_path = config.RESULTS / "kpi_v2_summary.json"
    if prev_path.exists():
        try:
            prev = json.loads(prev_path.read_text(encoding="utf-8")).get("results", [])
            kept = [r for r in prev if r.get("group") not in only]
            results = kept + results
        except Exception:  # noqa: BLE001
            pass

    path = schema.write_summary(results)
    npass = sum(1 for r in results if r["pass"] is True)
    nfail = sum(1 for r in results if r["pass"] is False)
    nna = sum(1 for r in results if r["pass"] is None)
    print(f"[kpi_eval_v2] {len(results)} KPI — ĐẠT {npass} / RỚT {nfail} / chưa đo {nna}")
    print(f"[kpi_eval_v2] summary: {path}")


if __name__ == "__main__":
    main()
