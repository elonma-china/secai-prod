#!/usr/bin/env python3
"""Convert secAI query/positive/negative records into SWIFT reranker groups."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

INSTRUCTION = "Given a Vietnamese cybersecurity search query, retrieve passages from the CVE knowledge base that directly answer it."


def convert(source: Path, destination: Path) -> int:
    destination.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    with source.open(encoding="utf-8") as src, destination.open("w", encoding="utf-8") as dst:
        for line_no, line in enumerate(src, 1):
            row = json.loads(line)
            missing = {"query", "positive", "negative"} - row.keys()
            if missing:
                raise ValueError(f"{source}:{line_no}: missing {sorted(missing)}")
            negatives = row["negative"] if isinstance(row["negative"], list) else [row["negative"]]
            negatives = [str(item).strip() for item in negatives if str(item).strip()]
            query, positive = str(row["query"]).strip(), str(row["positive"]).strip()
            if not query or not positive or not negatives:
                raise ValueError(f"{source}:{line_no}: query/positive/negative must be non-empty")
            swift_row = {
                "messages": [{"role": "system", "content": INSTRUCTION}, {"role": "user", "content": query}],
                "positive_messages": [[{"role": "assistant", "content": positive}]],
                "negative_messages": [[{"role": "assistant", "content": item}] for item in negatives],
            }
            dst.write(json.dumps(swift_row, ensure_ascii=False) + "\n")
            written += 1
    return written


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(f"converted {convert(args.input, args.output):,} groups -> {args.output}")


if __name__ == "__main__":
    main()
