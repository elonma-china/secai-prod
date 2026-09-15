#!/usr/bin/env python3
"""Rà soát hồ sơ gốc (HO-SO-GOC-NGHIEM-THU-AI) — đủ file chưa, API có khớp số không.

    python3 scripts/audit_hosogoc.py                 # chỉ kiểm file
    python3 scripts/audit_hosogoc.py --api http://127.0.0.1:8199   # kiểm thêm số API trả về

Kiểm 3 lớp:
  1. ĐỦ    — đối chiếu với danh mục file thật trên Drive (filelist.json, 543 mục).
  2. DÙNG  — mọi đường dẫn code showcase đọc có tồn tại không.
  3. KHỚP  — số API trả về có đúng bằng số trong file hồ sơ gốc không.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path("/home/ict/AIEngines/secai/HO-SO-GOC-NGHIEM-THU-AI")
INVENTORY = Path(__file__).resolve().parent / "hosogoc-filelist.json"
KPI = ROOT / "03. Kpi_results"
TRAIN = ROOT / "01. Ho_so_huan_luyen"

# Ảnh chụp màn hình rời ở gốc Drive — cố ý KHÔNG tải (xem docs/nghiem-thu-showcase/00-TONG-QUAN.md §0.2).
def _is_root_screenshot(path: str) -> bool:
    return "/" not in path and path.lower().endswith((".png", ".jpg", ".jpeg"))


# Đường dẫn code showcase đọc (tương đối so với ROOT).
API_SOURCES = {
    "F1 /ai/finetune/datasets": "01. Ho_so_huan_luyen/datasets/manifest.json",
    "F2 /datasets/{id}/sample": "01. Ho_so_huan_luyen/datasets/samples/qwen_instruction_samples.jsonl",
    "F3 /runs/{id}/log (step)": "01. Ho_so_huan_luyen/finetuning/logs/training-log.jsonl",
    "F3 /runs/{id}/log (epoch)": "01. Ho_so_huan_luyen/finetuning/reports/epoch-summary.csv",
    "F3 run index": "01. Ho_so_huan_luyen/finetuning/reports/training-evidence-index.json",
    "F4 /runs/{id}/charts": "01. Ho_so_huan_luyen/finetuning/dashboards/figures",
    "F5 /kpi-baseline": "01. Ho_so_huan_luyen/finetuning/reports/approved_metrics.json",
    "F6 /dataset-appendix": "01. Ho_so_huan_luyen/datasets/phu-luc-ky-thuat-v1.md",
    "F7 /data-processing (cleaning)": "01. Ho_so_huan_luyen/finetuning/reports/preprocessing-report.json",
    "F7 /data-processing (eda)": "01. Ho_so_huan_luyen/finetuning/reports/eda-summary.json",
    "F7 /data-processing (schema)": "01. Ho_so_huan_luyen/datasets/processed/manifest.json",
    "D1 /ai/model-info": "01. Ho_so_huan_luyen/finetuning/checkpoints/manifest.json",
    "K1-K3 /ai/kpi-bench": "03. Kpi_results/kpi-summary.json",
    "K run-log (quality)": "03. Kpi_results/Model/quality/run_eval_all.log",
    "K run-log (models)": "03. Kpi_results/Model/models/host/tp_embed_host.log",
}

# raw_log từng KPI mà /ai/kpi-bench/tests/{id}/result dùng cho bảng mẫu chi tiết.
KPI_RAW_LOGS = {
    "R-LLM-SAST85": "03. Kpi_results/Model/quality/raw-logs/sast.jsonl",
    "R-LLM-EXPLOIT85": "03. Kpi_results/Model/quality/raw-logs/exploit-explanation.jsonl",
    "R-LLM-REMED60": "03. Kpi_results/Model/quality/raw-logs/auto-remediation.jsonl",
    "R-KPI-FAITH98": "03. Kpi_results/Model/quality/raw-logs/llm-faithfulness.jsonl",
    "R-KPI-EMB96": "03. Kpi_results/Model/quality/raw-logs/embedding-hit-rate-at-10.jsonl",
    "R-KPI-RERANK90": "03. Kpi_results/Model/quality/raw-logs/reranking-precision-at-1.jsonl",
    "R-KPI-RELEV90": "03. Kpi_results/Model/quality/raw-logs/llm-answer-relevance.jsonl",
    "R-KPI-ENTITY100": "03. Kpi_results/Model/quality/raw-logs/llm-entity-precision.jsonl",
    "R-KPI-HALLU2": "03. Kpi_results/Model/quality/raw-logs/llm-hallucination.jsonl",
    "R-SEC-PII": "03. Kpi_results/Santization/Logs/sanitization.jsonl",
    "R-KPI-RADAR90": "03. Kpi_results/Model/quality/reports/radar-data.json",
}


def check_complete() -> int:
    print("=" * 70)
    print("1. ĐỦ — đối chiếu danh mục file trên Drive")
    print("=" * 70)
    if not INVENTORY.is_file():
        print(f"  (bỏ qua: không có danh mục {INVENTORY.name})")
        return 0
    items = json.load(INVENTORY.open())
    wanted = [d["path"] for d in items if not _is_root_screenshot(d["path"])]
    missing = [p for p in wanted if not (ROOT / p).is_file()]
    print(f"  Cần có : {len(wanted)} file (đã trừ {len(items) - len(wanted)} ảnh chụp màn hình)")
    print(f"  Đang có: {len(wanted) - len(missing)}")
    print(f"  Thiếu  : {len(missing)}")
    if missing:
        for folder, n in Counter(p.split("/")[0] for p in missing).most_common():
            print(f"     - {folder}: {n}")
        print("  Ví dụ thiếu:")
        for p in missing[:10]:
            print(f"     · {p}")
    return len(missing)


def check_api_sources() -> int:
    print()
    print("=" * 70)
    print("2. DÙNG — đường dẫn code showcase đọc")
    print("=" * 70)
    bad = 0
    for label, rel in {**API_SOURCES, **{f"K raw_log {k}": v for k, v in KPI_RAW_LOGS.items()}}.items():
        path = ROOT / rel
        ok = path.is_dir() if not path.suffix else path.is_file()
        if not ok:
            bad += 1
        print(f"  {'OK   ' if ok else 'THIẾU'} {label:<34} {rel}")
    return bad


def check_numbers(api_base: str | None) -> int:
    print()
    print("=" * 70)
    print("3. KHỚP — số API trả về vs số trong hồ sơ gốc")
    print("=" * 70)
    summary_path = KPI / "kpi-summary.json"
    if not summary_path.is_file():
        print("  (bỏ qua: chưa có 03. Kpi_results/kpi-summary.json)")
        return 1
    summary = json.load(summary_path.open())
    print(f"  Hồ sơ gốc có {len(summary)} KPI; đạt: {sum(1 for v in summary.values() if v.get('pass'))}")
    if not api_base:
        print("  (thêm --api http://127.0.0.1:8199 để đối chiếu trực tiếp với API)")
        return 0

    import urllib.request

    bad = 0
    for test_id, row in summary.items():
        url = f"{api_base}/ai/kpi-bench/tests/{test_id}/result"
        try:
            with urllib.request.urlopen(url, timeout=10) as r:
                got = json.load(r)
        except Exception as exc:  # noqa: BLE001
            print(f"  LỖI  {test_id}: {exc}")
            bad += 1
            continue
        same = got.get("value") == row.get("value") and got.get("pass") == row.get("pass")
        if not same:
            bad += 1
        print(f"  {'KHỚP ' if same else 'LỆCH '} {test_id:<20} hồ sơ gốc={row.get('value')} "
              f"pass={row.get('pass')} | API={got.get('value')} pass={got.get('pass')}")
    return bad


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--api", help="base URL của gateway để đối chiếu số, vd http://127.0.0.1:8199")
    args = ap.parse_args()

    if not ROOT.is_dir():
        print(f"Không thấy hồ sơ gốc tại {ROOT}")
        return 2

    missing = check_complete()
    bad_sources = check_api_sources()
    bad_numbers = check_numbers(args.api)

    print()
    print("=" * 70)
    print(f"KẾT LUẬN: thiếu {missing} file · {bad_sources} nguồn API chưa có · {bad_numbers} chỗ lệch số")
    print("=" * 70)
    return 0 if (bad_sources == 0 and bad_numbers == 0) else 1


if __name__ == "__main__":
    sys.exit(main())
