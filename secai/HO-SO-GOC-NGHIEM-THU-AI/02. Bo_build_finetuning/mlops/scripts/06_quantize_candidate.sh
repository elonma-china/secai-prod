#!/usr/bin/env bash
# Giai đoạn 5 — Quantize candidate LLM: merge LoRA (SFT hoặc DPO) vào base thành fp16 đầy đủ,
# rồi AWQ experts-only — GỌI NGUYÊN quantize_experts_only.py (không viết lại thuật toán
# quantize), chỉ đổi MODEL_ID/SAVE_DIR để trỏ vào checkpoint candidate thay vì HF Hub gốc.
#
# Yêu cầu: chạy trên máy có `quantization/requirements.txt` đã cài (torch cu126 + llmcompressor
# — xem quantization/README.md), KHÔNG chạy được trên máy điều phối thường (không đủ VRAM/lib).
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
QUANT="$ROOT/../../quantization"

if [ $# -lt 2 ]; then
  echo "usage: 06_quantize_candidate.sh <lora_adapter_dir> <out_dir>" >&2
  echo "  <lora_adapter_dir>: thư mục/HF repo LoRA đã train (SFT hoặc DPO) — merge_lora.py tự" >&2
  echo "  đọc base model thật từ adapter_config.json bên trong, không cần truyền riêng." >&2
  exit 64
fi
ADAPTER_DIR="$1"; OUT_DIR="$2"
MERGED_DIR="$OUT_DIR/merged-fp16"
AWQ_DIR="$OUT_DIR/awq"
mkdir -p "$MERGED_DIR" "$AWQ_DIR"

echo "[06_quantize_candidate] merge LoRA ($ADAPTER_DIR)"
python3 "$ROOT/_lib/merge_lora.py" --adapter "$ADAPTER_DIR" --output-dir "$MERGED_DIR"

echo "[06_quantize_candidate] AWQ experts-only quantize (khớp scheme DuyTa/Cyber-F1-AWQ đang serving)"
MODEL_ID="$MERGED_DIR" \
SAVE_DIR="$AWQ_DIR" \
IGNORE_JSON="$QUANT/ignore_experts_only.json" \
NUM_CALIBRATION_SAMPLES="${NUM_CALIBRATION_SAMPLES:-256}" \
MAX_SEQUENCE_LENGTH="${MAX_SEQUENCE_LENGTH:-2048}" \
python3 "$QUANT/quantize_experts_only.py"

echo "[06_quantize_candidate] xong -> $AWQ_DIR (push lên HF ở bước 07)"
