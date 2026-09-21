#!/usr/bin/env python3
"""Export benchmark prompts without reference answers for the inference runtime."""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> None:
    root = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser()
    parser.add_argument("--test", type=Path, default=root / "benchmarks/data/llm_qa_harness_v1.jsonl")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    rows = [json.loads(line) for line in args.test.open(encoding="utf-8") if line.strip()]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps({"id": row["id"], "question": row["question"]}, ensure_ascii=False) + "\n")
    print(f"Exported {len(rows)} prompts without references -> {args.output}")


if __name__ == "__main__":
    main()
