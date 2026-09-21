#!/usr/bin/env python3
"""llm-kpi-summary.json (entity/hallucination/relevance) + faithfulness-summary.json (faith_judge)
-> llm.json đúng schema check_finetune_thresholds.py. Metric = ĐÚNG chỉ tiêu hợp đồng tầng LLM,
đo trên FULL RAG PIPELINE (agent secAI): faithfulness / answer_relevance / entity_precision /
hallucination_rate.
"""
import argparse
import json
from pathlib import Path

SUITE = "secai-llm-product-v1"


def pct(d, *keys):
    """Lấy giá trị % đầu tiên tồn tại & là số trong dict con, trả fraction 0..1 (None nếu thiếu)."""
    for k in keys:
        v = d.get(k)
        if isinstance(v, (int, float)):
            return round(v / 100.0, 6)
    return None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--llm-summary", required=True, help="llm-kpi-summary.json")
    ap.add_argument("--faith-summary", required=True, help="faithfulness-summary.json")
    ap.add_argument("--out", required=True, help="đường dẫn llm.json")
    args = ap.parse_args()

    s = json.loads(Path(args.llm_summary).read_text(encoding="utf-8"))
    fa = {}
    fp = Path(args.faith_summary)
    if fp.exists():
        fa = json.loads(fp.read_text(encoding="utf-8"))

    ent = s.get("R-KPI-ENTITY100", {})
    hal = s.get("R-KPI-HALLU2", {})
    rel = s.get("R-KPI-RELEV90", {})

    metrics = {}
    v = pct(fa, "mean_faithfulness_pct")
    if v is not None:
        metrics["faithfulness"] = v
    v = pct(rel, "mean_relevance_pct")
    if v is not None:
        metrics["answer_relevance"] = v
    v = pct(ent, "mean_precision_pct")
    if v is not None:
        metrics["entity_precision"] = v
    v = pct(hal, "hallucination_rate_pct")
    if v is not None:
        metrics["hallucination_rate"] = v

    # examples: số mẫu nhỏ nhất trong các metric đã đo (bảo thủ cho min_examples)
    ns = [d.get("n") for d in (ent, hal, rel, fa) if isinstance(d.get("n"), int)]
    examples = min(ns) if ns else 0

    report = {
        "schema_version": 1, "component": "llm", "suite_id": SUITE,
        "examples": examples, "judge_errors": 0,
        "metrics": metrics,
        "detail": {"entity": ent, "hallucination": hal, "relevance": rel, "faithfulness": fa},
    }
    out = Path(args.out); out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"metrics": metrics, "examples": examples}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
