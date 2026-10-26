#!/usr/bin/env bash
pkill -f "[r]un_llama.sh" 2>/dev/null; pkill -f "[l]lama-server" 2>/dev/null; pkill -f "[f]ast_mt" 2>/dev/null; sleep 3
cd /workspace/fastmt
nohup python3 -m vllm.entrypoints.openai.api_server --model /workspace/hymt2-bf16 --served-model-name hymt \
  --host 127.0.0.1 --port 18770 --dtype bfloat16 --max-model-len 4096 --max-num-seqs 128 \
  --max-num-batched-tokens 16384 --gpu-memory-utilization 0.55 --enable-prefix-caching \
  > /workspace/fastmt/vllm.log 2>&1 &
for i in $(seq 1 240); do curl -s -m 2 -o /dev/null -w "%{http_code}" http://127.0.0.1:18770/health | grep -q 200 && break; sleep 2; done
curl -s -m 2 -o /dev/null -w "vllm health %{http_code}\n" http://127.0.0.1:18770/health
nohup /workspace/secai-specialist-services/venv/bin/python /workspace/fastmt/fast_mt_vllm.py > /workspace/fastmt/svc.log 2>&1 &
sleep 8; ss -ltn | grep -E "18770|18868"; nvidia-smi --query-gpu=memory.used --format=csv,noheader; tail -3 /workspace/fastmt/svc.log
