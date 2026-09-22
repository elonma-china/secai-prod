#!/usr/bin/env python3
"""retrieval-summary.json (retrieval_kpi.py) -> embedding.json + reranker.json đúng schema mà
check_finetune_thresholds.py đọc (component/suite_id/examples/metrics). Metric = ĐÚNG chỉ tiêu
hợp đồng: embedding Hit@10 (id-aware, headline = R-KPI-EMB96) + reranker Precision@1 (R-KPI-RERANK90).
Kèm các sub-metric minh bạch (hybrid, dense-only) để calibrate/soi giới hạn embedding.
"""
import argparse
import json
from pathlib import Path

SUITE = "secai-retrieval-product-v1"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--summary", required=True, help="retrieval-summary.json")
    ap.add_argument("--out-dir", required=True)
    args = ap.parse_args()

    s = json.loads(Path(args.summary).read_text(encoding="utf-8"))
    n = int(s.get("n", 0))
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)

    embedding = {
        "schema_version": 1, "component": "embedding", "suite_id": SUITE,
        "dataset": s.get("mode", ""), "model": s.get("model_embed", ""),
        "examples": n,
        "metrics": {
            "hit_rate_at_10": round(s.get("hit_rate_at_10_pct", 0.0) / 100.0, 6),
            "hit_rate_at_10_hybrid": round(s.get("hit_rate_at_10_hybrid_pct", 0.0) / 100.0, 6),
            "hit_rate_at_10_dense_only": round(s.get("hit_rate_at_10_dense_only_pct", 0.0) / 100.0, 6),
        },
    }
    reranker = {
        "schema_version": 1, "component": "reranker", "suite_id": SUITE,
        "dataset": s.get("mode", ""), "model": s.get("model_rerank", ""),
        "examples": n,
        "metrics": {"precision_at_1": round(s.get("precision_at_1_pct", 0.0) / 100.0, 6)},
    }
    (out / "embedding.json").write_text(json.dumps(embedding, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (out / "reranker.json").write_text(json.dumps(reranker, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"embedding": embedding["metrics"], "reranker": reranker["metrics"], "examples": n},
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
