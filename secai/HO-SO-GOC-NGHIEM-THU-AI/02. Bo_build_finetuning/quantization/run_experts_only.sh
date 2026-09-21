#!/bin/bash
# Experts-only AWQ — replicates the coverage/scheme of DuyTa/Cyber-F1-AWQ
# (only routed experts quantized; int4/gs32/ASYM/mse). Run inside tmux on the server.
set -o pipefail
source /root/quant-venv/bin/activate
export HF_HUB_DISABLE_XET=1
unset HF_XET_HIGH_PERFORMANCE
export TOKENIZERS_PARALLELISM=false
export PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True
export MODEL_ID="DuyTa/Cyber-F1"
export SAVE_DIR="/root/Cyber-F1-AWQ-experts-only"
export IGNORE_JSON="/root/ignore_experts_only.json"

# Calibration. Leave CALIB_JSONL unset for the general neuralmagic/calibration set,
# or point it at a cyber/CTI/Vietnamese/code/tool-call chat JSONL for domain-matched scales.
# export CALIB_JSONL="/root/calib_cyber_vi.jsonl"
export NUM_CALIBRATION_SAMPLES=16    # smoke; 256-512 for production
export MAX_SEQUENCE_LENGTH=2048

echo "=== [$(date)] ensure model cached ==="
hf download "$MODEL_ID" > /root/logs/dl2.log 2>&1; echo "download rc=$?"
echo "=== [$(date)] starting EXPERTS-ONLY AWQ ==="
python /root/quantize_experts_only.py
echo "=== [$(date)] rc=$? ==="
ls -la "$SAVE_DIR"/*.safetensors 2>/dev/null | awk '{s+=$5} END {print "safetensors total GB:", s/1e9}'
du -sh "$SAVE_DIR" 2>/dev/null
echo "=== ALL DONE [$(date)] ==="
