#!/usr/bin/env bash
# Fine-tune Qwen3-Reranker-0.6B with SWIFT listwise loss.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

MODEL="${MODEL:-Qwen/Qwen3-Reranker-0.6B}"
OUT="${OUT:-$ROOT/outputs/qwen3-reranker-06b}"
# TRAIN/VAL override-able (mặc định vẫn file tĩnh gốc, hành vi cũ không đổi) — dùng khi
# mlops/04_auto_finetune.sh trỏ vào 1 file train đã ghép thêm CVE mới (xem
# mlops/scripts/_lib/build_incremental_train_data.py), không đụng file tĩnh gốc trong repo.
TRAIN="${TRAIN:-$ROOT/data/reranker/train.jsonl}"
VAL="${VAL:-$ROOT/data/reranker/validation.jsonl}"
SWIFT_TRAIN="${SWIFT_TRAIN:-$ROOT/data/reranker/swift_train.jsonl}"
SWIFT_VAL="${SWIFT_VAL:-$ROOT/data/reranker/swift_validation.jsonl}"

python scripts/prepare_reranker_swift_data.py --input "$TRAIN" --output "$SWIFT_TRAIN"
python scripts/prepare_reranker_swift_data.py --input "$VAL" --output "$SWIFT_VAL"

USE_HF=1 MAX_POSITIVE_SAMPLES=1 MAX_NEGATIVE_SAMPLES=1 \
${SWIFT_CMD:-swift} sft \
  --model "$MODEL" --use_hf true --model_type "${RERANK_MODEL_TYPE:-qwen3_reranker}" \
  --task_type generative_reranker --loss_type listwise_reranker --tuner_type full \
  --dataset "$SWIFT_TRAIN" --val_dataset "$SWIFT_VAL" \
  --output_dir "$OUT" --num_train_epochs 1 --learning_rate 6e-6 --max_length 2048 \
  --per_device_train_batch_size 2 --per_device_eval_batch_size 2 --gradient_accumulation_steps 8 \
  --eval_strategy steps --eval_steps 100 --save_steps 100 --save_total_limit 2 --logging_steps 10 \
  --label_names labels --dataloader_drop_last true
