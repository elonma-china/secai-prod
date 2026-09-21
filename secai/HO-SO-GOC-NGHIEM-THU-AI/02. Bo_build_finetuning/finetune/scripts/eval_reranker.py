#!/usr/bin/env python3
"""Evaluate Qwen3-Reranker scores on test positives and negatives."""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
from pathlib import Path

SYSTEM = 'Judge whether the Document meets the requirements based on the Query and the Instruct provided. Note that the answer can only be "yes" or "no".'
INSTRUCTION = 'Given a Vietnamese cybersecurity search query, retrieve passages from the CVE knowledge base that directly answer it.'


def read_rows(path: Path) -> list[dict]:
    rows = []
    for line_no, line in enumerate(path.open(encoding="utf-8"), 1):
        row = json.loads(line)
        missing = {"query", "positive", "negative"} - row.keys()
        if missing:
            raise ValueError(f"{path}:{line_no}: missing {sorted(missing)}")
        negatives = row["negative"] if isinstance(row["negative"], list) else [row["negative"]]
        negatives = [str(item).strip() for item in negatives if str(item).strip()]
        if not str(row["query"]).strip() or not str(row["positive"]).strip() or not negatives:
            raise ValueError(f"{path}:{line_no}: empty query/positive/negative")
        rows.append({"query": row["query"], "positive": row["positive"], "negatives": negatives})
    return rows


def prompt(tokenizer, query: str, document: str) -> str:
    messages = [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": f"<Instruct>: {INSTRUCTION}\n<Query>: {query}\n<Document>: {document}"},
    ]
    try:
        return tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True, enable_thinking=False)
    except TypeError:
        return tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)


def calculate(ranks: list[int]) -> dict[str, float]:
    def mean(items):
        values = list(items)
        return sum(values) / len(values)
    return {
        "pairwise_accuracy": mean(rank == 1 for rank in ranks),
        "recall@1": mean(rank == 1 for rank in ranks),
        "recall@2": mean(rank <= 2 for rank in ranks),
        "mrr@2": mean(1 / rank if rank <= 2 else 0 for rank in ranks),
        "ndcg@2": mean(1 / math.log2(rank + 1) if rank <= 2 else 0 for rank in ranks),
        "map@2": mean(1 / rank if rank <= 2 else 0 for rank in ranks),
    }


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--test", type=Path, default=root / "data/reranker/test.jsonl")
    parser.add_argument("--batch-size", type=int, default=16)
    parser.add_argument("--max-length", type=int, default=2048)
    parser.add_argument(
        "--output",
        type=Path,
        help="Write a machine-readable evaluation report for the fine-tune quality gate.",
    )
    args = parser.parse_args()

    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer

    rows = read_rows(args.test)
    tokenizer = AutoTokenizer.from_pretrained(args.model)
    tokenizer.padding_side = "left"
    if tokenizer.pad_token_id is None:
        tokenizer.pad_token = tokenizer.eos_token
    model = AutoModelForCausalLM.from_pretrained(args.model, torch_dtype=torch.bfloat16, device_map="auto").eval()
    yes_ids, no_ids = tokenizer.encode("yes", add_special_tokens=False), tokenizer.encode("no", add_special_tokens=False)
    if len(yes_ids) != 1 or len(no_ids) != 1:
        raise ValueError(f"Expected single-token yes/no labels, got yes={yes_ids}, no={no_ids}")

    candidates, positive_positions = [], []
    for row in rows:
        positive_positions.append(len(candidates))
        candidates.append((row["query"], row["positive"], True))
        candidates.extend((row["query"], negative, False) for negative in row["negatives"])
    scores = []
    with torch.inference_mode():
        for start in range(0, len(candidates), args.batch_size):
            batch = candidates[start:start + args.batch_size]
            encoded = tokenizer([prompt(tokenizer, query, document) for query, document, _ in batch],
                                return_tensors="pt", padding=True, truncation=True, max_length=args.max_length)
            encoded = {key: value.to(model.device) for key, value in encoded.items()}
            logits = model(**encoded).logits[:, -1, :]
            scores.extend((logits[:, yes_ids[0]] - logits[:, no_ids[0]]).float().cpu().tolist())

    ranks, offset = [], 0
    for row in rows:
        width = 1 + len(row["negatives"])
        group = scores[offset:offset + width]
        ranks.append(1 + sum(score > group[0] for score in group[1:]))
        offset += width
    metric_values = calculate(ranks)
    # Every group contains exactly one relevant document. In this fixed candidate-set
    # benchmark, Precision@1 and Recall@1 are therefore the same quantity.
    metric_values["precision@1"] = metric_values["recall@1"]
    result = {
        "schema_version": 1,
        "component": "reranker",
        "suite_id": "secai-reranker-candidate-set-v1",
        "model": str(args.model),
        "dataset": str(args.test),
        "evaluated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "examples": len(rows),
        "metrics": metric_values,
    }
    print(json.dumps(result, indent=2))
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
