#!/usr/bin/env python3
"""Evaluate dense retrieval metrics on the embedding test split."""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
from pathlib import Path


def read_rows(path: Path) -> list[dict]:
    rows = [json.loads(line) for line in path.open(encoding="utf-8")]
    if not rows or any(not str(row.get("query", "")).strip() or not str(row.get("positive", "")).strip() for row in rows):
        raise ValueError("test records must contain non-empty query and positive")
    return rows


def metrics(ranks: list[int]) -> dict[str, float]:
    def recall(k: int) -> float:
        return sum(rank <= k for rank in ranks) / len(ranks)
    def mrr(k: int) -> float:
        return sum(1 / rank if rank <= k else 0 for rank in ranks) / len(ranks)
    def ndcg(k: int) -> float:
        return sum(1 / math.log2(rank + 1) if rank <= k else 0 for rank in ranks) / len(ranks)
    return {
        "recall@1": recall(1), "recall@5": recall(5), "recall@10": recall(10),
        "mrr@10": mrr(10), "ndcg@10": ndcg(10), "map@100": mrr(100),
    }


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--test", type=Path, default=root / "data/embedding/test.jsonl")
    parser.add_argument("--batch-size", type=int, default=64)
    parser.add_argument(
        "--output",
        type=Path,
        help="Write a machine-readable evaluation report for the fine-tune quality gate.",
    )
    args = parser.parse_args()

    from sentence_transformers import SentenceTransformer

    rows = read_rows(args.test)
    try:
        from unsloth import FastSentenceTransformer
        model = FastSentenceTransformer.from_pretrained(str(args.model))
    except Exception:  # Plain merged SentenceTransformer model.
        model = SentenceTransformer(str(args.model))
    queries = model.encode([row["query"] for row in rows], batch_size=args.batch_size,
                           normalize_embeddings=True, show_progress_bar=True)
    documents = model.encode([row["positive"] for row in rows], batch_size=args.batch_size,
                             normalize_embeddings=True, show_progress_bar=True)
    scores = queries @ documents.T
    ranks = []
    for index, row in enumerate(scores):
        rank = int((row > row[index]).sum()) + 1
        ranks.append(rank)
    result = {
        "schema_version": 1,
        "component": "embedding",
        "suite_id": "secai-embedding-pairwise-v1",
        "model": str(args.model),
        "dataset": str(args.test),
        "evaluated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "examples": len(rows),
        "metrics": metrics(ranks),
    }
    print(json.dumps(result, indent=2))
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
