#!/bin/bash
set -o pipefail
source /root/quant-venv/bin/activate
# Disable Xet (its high-perf mode stalled + buffered into RAM). Use classic transfer.
export HF_HUB_DISABLE_XET=1
unset HF_XET_HIGH_PERFORMANCE
export TOKENIZERS_PARALLELISM=false
export PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True
export MODEL_ID="DuyTa/Cyber-F1"
export SAVE_DIR="/root/Cyber-F1-AWQ-W4A16"
# 32 = smoke run (fast, lower quality). Use 256 (or 512) for production.
export NUM_CALIBRATION_SAMPLES=32
export MAX_SEQUENCE_LENGTH=2048
export GROUP_SIZE=128

echo "=== [$(date)] downloading model (Xet disabled, visible progress) ==="
hf download "$MODEL_ID" 2>&1 | tail -1
echo "download rc=${PIPESTATUS[0]}  disk:"; df -h / | tail -1

echo "=== [$(date)] starting AWQ quantization ==="
python /root/quantize_cyberf1_awq.py
echo "=== [$(date)] quantize rc=$? ==="
ls -la "$SAVE_DIR" 2>/dev/null; du -sh "$SAVE_DIR" 2>/dev/null
echo "=== ALL DONE [$(date)] ==="
