#!/usr/bin/env python3
"""Adapter: chuyển output của gen_testset.py (schema `acceptance_*.jsonl`) sang 3 format mà các
evaluator của gói này cần (`eval_embedding.py`/`eval_reranker.py`/`eval_llm_qa.py`).

Bộ synthetic này dùng cho: (1) PHÁT HIỆN SUY GIẢM ở stage 3 — đo model ĐANG SERVING (production
CHƯA hề train trên CVE mới của chu kỳ này → hợp lệ) và (2) BỔ SUNG TRAIN ở stage 4/DPO 4b.
KHÔNG dùng để requalify/gate promote (stage 6c) — cổng đó chấm trên BENCHMARK cố định
(benchmarks/data + finetune/data/*/test.jsonl) mà candidate KHÔNG train trên đó, để tránh rò rỉ
train/test và bắt được regression. Xem run_pipeline.sh + mlops/README.md.

Grounding text lấy từ `_meta/pool.jsonl` (mô tả CVE gen_testset tra Qdrant/NVD) — distant
supervision; thực tế chỉ câu "định danh" (có metadata.cve_id) được ground (~1 câu/CVE), các loại
khắc phục/tình huống không có gold rời nên bị loại — xem giới hạn ở mlops/README.md.
"""
from __future__ import annotations

import argparse
import json
import random
from pathlib import Path


def read_jsonl(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def load_samples(gen_dir: Path) -> list[dict]:
    samples = []
    for path in sorted(gen_dir.glob("acceptance_*.jsonl")):
        samples.extend(read_jsonl(path))
    return samples


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--gen-dir", type=Path, required=True, help="Thư mục output của gen_testset.py")
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--negatives-per-query", type=int, default=3)
    args = parser.parse_args()
    rng = random.Random(args.seed)

    pool = {row["cve_id"]: row for row in read_jsonl(args.gen_dir / "_meta" / "pool.jsonl") if row.get("cve_id")}
    samples = load_samples(args.gen_dir)
    args.out_dir.mkdir(parents=True, exist_ok=True)

    embedding_rows, reranker_rows, llm_rows = [], [], []
    grounded_cves = [cve for cve, rec in pool.items() if str(rec.get("text") or "").strip()]

    skipped_no_ground = 0
    for sample in samples:
        cve_id = (sample.get("metadata") or {}).get("cve_id")
        question = sample.get("input")
        rec = pool.get(cve_id) if cve_id else None
        text = str((rec or {}).get("text") or "").strip()
        if not question or not text:
            skipped_no_ground += 1
            continue

        embedding_rows.append({"record_id": sample["id"], "query": question, "positive": text, "label": 1})
        others = [c for c in grounded_cves if c != cve_id]
        negatives = [pool[c]["text"] for c in rng.sample(others, min(args.negatives_per_query, len(others)))]
        if negatives:
            reranker_rows.append({"record_id": sample["id"], "query": question, "positive": text, "negative": negatives})
        llm_rows.append({"id": sample["id"], "question": question, "answer": text})

    def write(name: str, rows: list[dict]) -> None:
        (args.out_dir / name).write_text(
            "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + ("\n" if rows else ""),
            encoding="utf-8")
        print(f"[adapt_eval_set] {name}: {len(rows)} dòng")

    write("embedding.jsonl", embedding_rows)
    write("reranker.jsonl", reranker_rows)
    write("llm_test.jsonl", llm_rows)
    if skipped_no_ground:
        print(f"[adapt_eval_set] bỏ qua {skipped_no_ground}/{len(samples)} câu không có grounding text "
              f"(CVE không nằm trong pool.jsonl hoặc pool thiếu field 'text')")
    if not embedding_rows:
        raise SystemExit("[adapt_eval_set] 0 câu có grounding — không đủ dữ liệu để chạy gate, dừng")


if __name__ == "__main__":
    main()
