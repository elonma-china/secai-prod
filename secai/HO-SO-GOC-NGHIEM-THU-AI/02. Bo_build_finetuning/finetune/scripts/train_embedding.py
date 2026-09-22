#!/usr/bin/env python3
"""Fine-tune BGE-M3 on query/positive pairs with Cached MNRL."""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def read_rows(path: Path) -> list[dict]:
    rows: list[dict] = []
    for line_no, line in enumerate(path.open(encoding="utf-8"), 1):
        row = json.loads(line)
        missing = {"query", "positive"} - row.keys()
        if missing or not str(row.get("query", "")).strip() or not str(row.get("positive", "")).strip():
            raise ValueError(f"{path}:{line_no}: missing or empty query/positive")
        rows.append(row)
    if not rows:
        raise ValueError(f"{path}: no records")
    return rows


def build_ir_evaluator(rows):
    from sentence_transformers.evaluation import InformationRetrievalEvaluator

    queries = {f"q{i}": row["query"] for i, row in enumerate(rows)}
    corpus = {f"d{i}": row["positive"] for i, row in enumerate(rows)}
    relevant = {f"q{i}": {f"d{i}"} for i in range(len(rows))}
    return InformationRetrievalEvaluator(
        queries, corpus, relevant, name="validation",
        accuracy_at_k=[1, 5, 10], precision_recall_at_k=[1, 5, 10],
        mrr_at_k=[10], ndcg_at_k=[10], map_at_k=[100], show_progress_bar=False,
    )


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser()
    parser.add_argument("--train", type=Path, default=root / "data/embedding/train.jsonl")
    parser.add_argument("--validation", type=Path, default=root / "data/embedding/validation.jsonl")
    parser.add_argument("--output-dir", type=Path, default=root / "outputs/bge-m3")
    parser.add_argument("--epochs", type=float, default=1)
    parser.add_argument("--batch-size", type=int, default=8)
    parser.add_argument("--max-seq-length", type=int, default=1024)
    parser.add_argument("--learning-rate", type=float, default=2e-5)
    parser.add_argument("--no-unsloth", action="store_true")
    parser.add_argument("--base-model", default="BAAI/bge-m3",
                        help="Model nền để fine-tune. Pipeline retrain truyền model ĐANG SERVING "
                             "(DuyTa/sec-embedding) để NỐI TIẾP thay vì train lại từ base gốc.")
    args = parser.parse_args()

    from datasets import Dataset
    from sentence_transformers import SentenceTransformer, SentenceTransformerTrainer, SentenceTransformerTrainingArguments, losses

    train_rows, val_rows = read_rows(args.train), read_rows(args.validation)
    train_ds = Dataset.from_dict({
        "anchor": [row["query"] for row in train_rows],
        "positive": [row["positive"] for row in train_rows],
    })

    model = None
    if not args.no_unsloth:
        try:
            from unsloth import FastSentenceTransformer
            model = FastSentenceTransformer.from_pretrained(args.base_model, max_seq_length=args.max_seq_length)
            model = FastSentenceTransformer.get_peft_model(
                model, r=32, lora_alpha=64, lora_dropout=0,
                target_modules=["key", "query", "dense", "value"], bias="none",
                task_type="FEATURE_EXTRACTION",
            )
        except Exception as exc:  # noqa: BLE001
            print(f"[embedding] Unsloth unavailable ({exc}); falling back to sentence-transformers")
    if model is None:
        model = SentenceTransformer(args.base_model)
    model.max_seq_length = args.max_seq_length

    loss = losses.CachedMultipleNegativesRankingLoss(model, mini_batch_size=args.batch_size)
    train_args = SentenceTransformerTrainingArguments(
        output_dir=str(args.output_dir), num_train_epochs=args.epochs,
        per_device_train_batch_size=args.batch_size, learning_rate=args.learning_rate,
        bf16=True, eval_strategy="epoch", save_strategy="epoch", logging_steps=10,
        report_to="none",
    )
    trainer = SentenceTransformerTrainer(
        model=model, args=train_args, train_dataset=train_ds, loss=loss,
        evaluator=build_ir_evaluator(val_rows),
    )
    trainer.train()
    model.save_pretrained(str(args.output_dir))
    print(f"[embedding] saved: {args.output_dir}")


if __name__ == "__main__":
    main()
