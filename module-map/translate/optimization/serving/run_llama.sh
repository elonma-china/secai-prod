#!/usr/bin/env bash
# llama-server Hy-MT2-7B Q4_K_M luôn nạp, N slot song song (continuous batching). Tự khởi động lại khi chết.
# Dùng binary/model/thư viện đã ghim của bundle; KHÔNG dùng thư mục CUDA compat (driver host mới hơn).
P="${FASTMT_PARALLEL:-12}"      # slot mỗi replica
R="${FASTMT_REPLICAS:-3}"      # số llama-server chạy song song trên cùng GPU
ROOT=/workspace/secai-gpu-specialists
export LD_LIBRARY_PATH=/usr/local/cuda-13.0/lib64 CUDA_VISIBLE_DEVICES=0
ALIAS=$(PYTHONPATH=/workspace/secai-specialist-services/source python3 -c "from evals.multilang import gpu_specialist_server as g;print(g.MODEL_ALIAS)")
OVR=$(PYTHONPATH=/workspace/secai-specialist-services/source python3 -c "from evals.multilang import gpu_specialist_server as g;print(g.EOG_OVERRIDE)")
run_one() {
  local PORT=$1
  while true; do
    $ROOT/runtime/llama.cpp/build-cuda/bin/llama-server --model $ROOT/model/Hy-MT2-7B-Q4_K_M.gguf \
      --host 127.0.0.1 --port $PORT --threads 8 --threads-batch 8 \
      --parallel "$P" --ctx-size $((4096*P)) --batch-size 4096 --ubatch-size 1024 \
      --n-gpu-layers 99 --fit off --flash-attn on --cache-ram 0 \
      --override-kv "$OVR" --verbosity 1 --no-webui --alias "$ALIAS" >> /workspace/fastmt/llama-$PORT.log 2>&1
    echo "llama-server $PORT exited $?; restart in 3s" >> /workspace/fastmt/llama-$PORT.log; sleep 3
  done
}
for i in $(seq 0 $((R-1))); do run_one $((18767+i)) & sleep 2; done
wait
