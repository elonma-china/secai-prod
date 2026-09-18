#!/usr/bin/env python3
"""gen_answers.py — Chuẩn hoá answers cho các KPI dựa-trên-answer.

Tái dùng kết quả đã chạy `eval/results/_acc/run2/results.jsonl` (996/1000 pass, deepseek-v4-flash)
+ join với dataset gốc để có question/metadata/files. KHÔNG chạy lại agent.

Nếu sau này đổi model (Qwen): chạy lại acceptance_runner với --run-id runQ rồi trỏ --results vào đó.

Output answers.jsonl mỗi dòng:
  {id, capability, bucket, difficulty, question, answer, tools, files, gold:{cve_id,cwe,...}, error}

Usage:
  python3 gen_answers.py --results ../../results/_acc/run2/results.jsonl --out ../raw-logs/answers.jsonl
"""
import argparse
import json
from pathlib import Path

KPI_DIR = Path(__file__).resolve().parents[1]
EVAL_DIR = KPI_DIR.parent
DS_DIR = EVAL_DIR / "datasets-acceptance"


def load_dataset():
    ds = {}
    for fp in sorted(DS_DIR.glob("acceptance_*.jsonl")):
        for line in fp.read_text(encoding="utf-8").splitlines():
            if line.strip():
                s = json.loads(line)
                ds[s["id"]] = s
    return ds


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--results", default=str(EVAL_DIR / "results/_acc/run2/results.jsonl"))
    ap.add_argument("--out", default=str(KPI_DIR / "raw-logs" / "answers.jsonl"))
    args = ap.parse_args()

    ds = load_dataset()
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with out_path.open("w", encoding="utf-8") as f:
        for line in Path(args.results).read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            r = json.loads(line)
            s = ds.get(r["id"])
            if not s:
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
    print(f"answers.jsonl: {n} dòng → {out_path}")


if __name__ == "__main__":
    main()
