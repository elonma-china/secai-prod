#!/usr/bin/env python3
"""lam_answers.py — Bản thay thế `gen_answers.py` CÓ hỗ trợ `--dataset`.

VÌ SAO CẦN FILE NÀY
-------------------
`source/eval/kpi/scripts/gen_answers.py:21` hard-code:

    DS_DIR = EVAL_DIR / "datasets-acceptance"

và nối kết quả với bộ đề bằng `ds.get(r["id"])`, gặp id lạ thì `continue`.
Nó KHÔNG có cờ `--dataset`. Hệ quả: chạy

    bash run_eval_all.sh --dataset <thư-mục-khác>

thì bước 2 vẫn nối vào bộ 1000 câu CŨ → không id nào khớp → **answers.jsonl RỖNG**,
kéo theo 7 KPI dựa-trên-đáp-án (Faithfulness, Relevance, Entity, Hallucination,
SAST, Exploit, Remediation) im lặng trả 0/null mà KHÔNG báo lỗi.

Đã kiểm chứng: cho results.jsonl chứa id `ind_dd_0000` → gen_answers.py in
"answers.jsonl: 0 dòng".

File này giữ NGUYÊN schema đầu ra của gen_answers.py, chỉ thêm `--dataset` và
cảnh báo khi tỉ lệ khớp id thấp.

Dùng:
    python3 lam_answers.py --dataset <bộ-đề> --results <results.jsonl> --out <answers.jsonl>
"""
import argparse
import glob
import json
import os
import sys
from pathlib import Path


def load_dataset(ds_dir):
    ds = {}
    files = sorted(glob.glob(os.path.join(ds_dir, "*.jsonl")))
    for fp in files:
        if os.path.basename(fp).startswith("_"):
            continue                      # bỏ tệp phụ trợ (_pool.jsonl…)
        for line in Path(fp).read_text(encoding="utf-8").splitlines():
            if line.strip():
                s = json.loads(line)
                ds[s["id"]] = s
    return ds, files


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", required=True, help="thư mục bộ đề (chứa *.jsonl)")
    ap.add_argument("--results", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    ds, files = load_dataset(args.dataset)
    if not ds:
        sys.exit(f"✗ Không nạp được mẫu nào từ {args.dataset}")

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    n = miss = 0
    with out_path.open("w", encoding="utf-8") as f:
        for line in Path(args.results).read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            r = json.loads(line)
            s = ds.get(r["id"])
            if not s:
                miss += 1
                continue
            meta = s.get("metadata") or {}
            rec = {
                "id": r["id"], "capability": r.get("capability"), "bucket": r.get("bucket"),
                "difficulty": r.get("difficulty"), "question": s["input"],
                "answer": r.get("text", "") or "", "tools": r.get("tools", []),
                "files": s.get("files", []),
                "expected": s.get("expected", {}),
                "tags": s.get("tags", []),
                "gold": {"cve_id": meta.get("cve_id"), "cwe": meta.get("cwe", []),
                         "severity": meta.get("severity"), "products": meta.get("products", []),
                         "fake_cve": meta.get("fake_cve"), "fake_product": meta.get("fake_product")},
                "error": r.get("error"),
            }
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            n += 1

    print(f"answers.jsonl: {n} dòng → {out_path}  "
          f"(bộ đề {len(ds)} mẫu từ {len(files)} tệp)")
    if miss:
        print(f"  ! {miss} dòng results KHÔNG khớp id nào trong bộ đề")
    if n == 0:
        sys.exit("✗ answers.jsonl RỖNG — results.jsonl và bộ đề không cùng một bộ id. "
                 "Kiểm tra lại --dataset có đúng thư mục đã dùng để sinh đáp án không.")


if __name__ == "__main__":
    main()
