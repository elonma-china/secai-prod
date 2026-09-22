#!/usr/bin/env python3
"""Measure Qwen SFT context after rendering its real chat template and tool schema."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Iterable


def percentile(values: list[int], p: float) -> int:
    values = sorted(values)
    return values[min(len(values) - 1, round((len(values) - 1) * p))]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", required=True, help="HF model ID or local tokenizer directory")
    parser.add_argument("--dataset", type=Path, required=True, help="JSONL with a messages field")
    parser.add_argument("--max-seq-length", type=int, default=8192)
    parser.add_argument("--limit", type=int, default=0, help="0 audits every record")
    args = parser.parse_args()

    from transformers import AutoProcessor
    from secai_ft.tools_schema import TOOLS_SCHEMA, normalize_tool_calls

    processor = AutoProcessor.from_pretrained(args.model)
    tokenizer = getattr(processor, "tokenizer", processor)
    lengths: list[int] = []
    max_meta: dict[str, int] = {}
    max_seen = -1

    with args.dataset.open() as source:
        for line_number, line in enumerate(source, start=1):
            if args.limit and len(lengths) >= args.limit:
                break
            row = json.loads(line)
            messages = normalize_tool_calls(row["messages"])
            rendered = tokenizer.apply_chat_template(
                messages, tools=TOOLS_SCHEMA, tokenize=False,
                add_generation_prompt=False, enable_thinking=False,
            )
            length = len(tokenizer(rendered, add_special_tokens=False)["input_ids"])
            lengths.append(length)
            if length > max_seen:
                max_seen = length
                max_meta = {
                    "line": line_number,
                    "tokens": length,
                    "turns": len(messages),
                    "tool_calls": sum(len(message.get("tool_calls") or []) for message in messages),
                }
            if line_number % 1000 == 0:
                print(f"[context-audit] rendered {line_number:,} records", flush=True)

    if not lengths:
        raise ValueError("dataset is empty")
    report = {
        "records": len(lengths),
        "model": args.model,
        "max_seq_length": args.max_seq_length,
        "tokens": {f"p{p}": percentile(lengths, p / 100) for p in (50, 90, 95, 99)},
        "p99_5": percentile(lengths, 0.995),
        "p99_9": percentile(lengths, 0.999),
        "max": max(lengths),
        "over_max_seq_length": sum(length > args.max_seq_length for length in lengths),
        "longest_trace": max_meta,
        "tools_schema_tokens_alone": len(tokenizer(
            json.dumps(TOOLS_SCHEMA, ensure_ascii=False), add_special_tokens=False)["input_ids"]),
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
