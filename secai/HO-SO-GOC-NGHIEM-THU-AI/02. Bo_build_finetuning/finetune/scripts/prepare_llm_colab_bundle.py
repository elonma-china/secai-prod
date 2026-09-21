#!/usr/bin/env python3
"""Package the standalone Cyber_F1_v2 LLM splits and trainer for the Colab notebook."""
from __future__ import annotations

import argparse
import itertools
import json
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def copy_records(source: Path, destination: Path, count: int) -> None:
    with source.open("rb") as src, destination.open("wb") as dst:
        rows = list(itertools.islice(src, count))
        if len(rows) != count:
            raise ValueError(f"{source} contains {len(rows)} records; expected {count}")
        dst.writelines(rows)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=ROOT / "outputs" / "colab-qwen35-smoke")
    parser.add_argument("--samples", type=int, default=1000)
    parser.add_argument("--validation-samples", type=int, default=64)
    parser.add_argument("--max-steps", type=int, default=-1,
                        help="-1 runs one full epoch")
    parser.add_argument("--max-seq-length", type=int, default=32768)
    args = parser.parse_args()
    if args.max_seq_length <= 0:
        parser.error("--max-seq-length must be positive")

    config = json.loads((ROOT / "configs" / "qwen35_cyber_f1.json").read_text())
    args.output.mkdir(parents=True, exist_ok=True)
    copy_records(ROOT / config["dataset_path"], args.output / "train.jsonl", args.samples)
    copy_records(ROOT / config["validation_path"], args.output / "validation.jsonl", args.validation_samples)
    config.update({
        "dataset_path": "/content/secai-qwen35-smoke/data/train.jsonl",
        "validation_path": "/content/secai-qwen35-smoke/data/validation.jsonl",
        "output_dir": "/content/secai-qwen35-smoke/adapter",
        "logging": {"tensorboard_dir": "/content/secai-qwen35-smoke/tensorboard"},
        "max_train_samples": args.samples,
        "max_steps": args.max_steps,
        "epochs": 1,
        "batch_size": 1,
        "gradient_accumulation_steps": 1,
        "max_seq_length": args.max_seq_length,
        "eval_strategy": "epoch" if args.max_steps < 0 else "no",
        "save_strategy": "epoch" if args.max_steps < 0 else "steps",
        "save_steps": max(args.max_steps, 1),
        "save_total_limit": 1,
    })
    (args.output / "config.json").write_text(json.dumps(config, indent=2) + "\n")
    with zipfile.ZipFile(args.output / "secai_ft_source.zip", "w", zipfile.ZIP_DEFLATED) as archive:
        for path in (ROOT / "src" / "secai_ft").rglob("*.py"):
            archive.write(path, path.relative_to(ROOT / "src"))
    bundle = args.output / "qwen35-cyber-f1-smoke.zip"
    with zipfile.ZipFile(bundle, "w", zipfile.ZIP_DEFLATED) as archive:
        for name in ("train.jsonl", "validation.jsonl", "config.json", "secai_ft_source.zip"):
            archive.write(args.output / name, name)
    print(f"Prepared {args.samples} train + {args.validation_samples} validation records -> {bundle}")


if __name__ == "__main__":
    main()
