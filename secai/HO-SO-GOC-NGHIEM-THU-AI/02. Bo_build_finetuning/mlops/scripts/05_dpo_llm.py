#!/usr/bin/env python3
"""Giai đoạn 4b — DPO cho LLM: xây cặp preference THẲNG từ report `llm-qa.json` của Giai đoạn 3
(không cần gọi judge thêm lần nào) rồi gọi `unsloth_dpo.run_unsloth_dpo` (tái dùng
`load_base_model` của `unsloth_sft.py`, xem finetune/src/secai_ft/trainers/unsloth_dpo.py).

chosen  = gold answer của câu hỏi đó trong llm_test.jsonl (mô tả CVE thật, suy ra tự động).
rejected = chính candidate answer mà model đang serving đã trả lời SAI (report ghi lại
           `correctness`/`hallucination` cho từng câu — dùng thẳng, không phát sinh dữ liệu mới).
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _lib.call_llm_predictions import SYSTEM_PROMPT  # cùng system prompt đã dùng để lấy candidate answer

FT_ROOT = Path(__file__).resolve().parents[2] / "finetune"


def build_pairs(llm_qa_report: dict, test_rows: dict[str, dict], correctness_threshold: float) -> list[dict]:
    pairs = []
    for record in llm_qa_report.get("records", []):
        if "error" in record:
            continue
        bad = record.get("correctness", 1.0) < correctness_threshold or bool(record.get("hallucination"))
        if not bad:
            continue
        row = test_rows.get(record["id"])
        if not row:
            continue
        rejected = str(record.get("answer") or "").strip()
        chosen = str(row.get("answer") or "").strip()
        if not rejected or not chosen or rejected == chosen:
            continue
        pairs.append({
            "prompt": [{"role": "system", "content": SYSTEM_PROMPT},
                       {"role": "user", "content": row["question"]}],
            "chosen": chosen, "rejected": rejected,
        })
    return pairs


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--llm-qa-report", type=Path, required=True, help="report của Giai đoạn 3 (llm-qa.json)")
    parser.add_argument("--test", type=Path, required=True, help="llm_test.jsonl {id,question,answer}")
    parser.add_argument("--base-model", help="Ghi đè base_model trong config (mặc định dùng giá trị trong config DPO)")
    parser.add_argument("--config", type=Path, default=FT_ROOT / "configs/qwen35_cyber_f1_dpo.json")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--correctness-threshold", type=float, default=0.75,
                        help="Khớp --correctness-pass-score mặc định của eval_llm_qa.py")
    parser.add_argument("--val-fraction", type=float, default=0.1)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--min-pairs", type=int, default=8,
                        help="Dưới ngưỡng này, DPO không đủ tín hiệu để train — thoát code 2")
    args = parser.parse_args()
    random.seed(args.seed)

    llm_qa_report = json.loads(args.llm_qa_report.read_text(encoding="utf-8"))
    test_rows = {row["id"]: row for row in
                (json.loads(line) for line in args.test.read_text(encoding="utf-8").splitlines() if line.strip())}

    pairs = build_pairs(llm_qa_report, test_rows, args.correctness_threshold)
    print(f"[05_dpo_llm] {len(pairs)} cặp preference xây được từ {len(llm_qa_report.get('records', []))} record")
    if len(pairs) < args.min_pairs:
        print(f"[05_dpo_llm] {len(pairs)} < min_pairs={args.min_pairs} — bỏ qua DPO chu kỳ này "
              "(model đang serving đã đủ tốt trên bộ CVE mới, không đủ ví dụ sai để DPO học)")
        raise SystemExit(2)

    random.shuffle(pairs)
    n_val = max(1, int(len(pairs) * args.val_fraction))
    val_pairs, train_pairs = pairs[:n_val], pairs[n_val:]

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    train_path, val_path = out_dir / "dpo_train.jsonl", out_dir / "dpo_validation.jsonl"
    train_path.write_text("\n".join(json.dumps(p, ensure_ascii=False) for p in train_pairs) + "\n", encoding="utf-8")
    val_path.write_text("\n".join(json.dumps(p, ensure_ascii=False) for p in val_pairs) + "\n", encoding="utf-8")
    print(f"[05_dpo_llm] train={len(train_pairs)} val={len(val_pairs)} -> {out_dir}")

    config = json.loads(args.config.read_text(encoding="utf-8"))
    if args.base_model:
        config["base_model"] = args.base_model
    config["dataset_path"] = str(train_path)
    config["validation_path"] = str(val_path)
    config["output_dir"] = str(out_dir / "model")
    config["logging"]["tensorboard_dir"] = str(out_dir / "tensorboard")

    sys.path.insert(0, str(FT_ROOT / "src"))
    from secai_ft.trainers.unsloth_dpo import run_unsloth_dpo
    run_unsloth_dpo(config)


if __name__ == "__main__":
    main()
