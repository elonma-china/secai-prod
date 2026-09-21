#!/usr/bin/env python3
"""Run Cyber-F1 v2 Qwen3-30B-A3B-Instruct-2507 MoE SFT from a standalone finetune handoff."""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=Path("configs/qwen35_cyber_f1.json"))
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--dataset-path", type=Path,
                        help="Override config's dataset_path (e.g. mlops/ incremental train file "
                             "with newly-collected CVEs appended); omitted means use config as-is")
    parser.add_argument("--max-train-samples", type=int,
                        help="Optional smoke cap; omitted means use all train records")
    parser.add_argument("--max-eval-samples", type=int,
                        help="Optional smoke cap for validation; the final end-of-training eval "
                             "otherwise runs on the full split and can OOM a smoke-sized GPU")
    parser.add_argument("--max-steps", type=int, help="Optional step cap; -1 means full epochs")
    args = parser.parse_args()

    project = Path(__file__).resolve().parents[1]
    config = json.loads((project / args.config).read_text())
    for key in ("dataset_path", "validation_path", "test_path"):
        if key in config:
            config[key] = str(project / config[key])
    config["output_dir"] = str(project / (args.output_dir or Path(config["output_dir"])))
    config["logging"]["tensorboard_dir"] = str(project / config["logging"]["tensorboard_dir"])
    if args.dataset_path is not None:
        config["dataset_path"] = str(project / args.dataset_path)
    if args.max_train_samples is not None:
        config["max_train_samples"] = args.max_train_samples
    if args.max_eval_samples is not None:
        config["max_eval_samples"] = args.max_eval_samples
    if args.max_steps is not None:
        config["max_steps"] = args.max_steps

    from secai_ft.trainers.unsloth_sft import run_unsloth_sft
    run_unsloth_sft(config)


if __name__ == "__main__":
    main()
