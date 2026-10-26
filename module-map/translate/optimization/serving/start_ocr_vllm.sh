#!/usr/bin/env bash
python3 /workspace/secai-specialist-services/bootstrap.py stop --root /workspace/secai-specialist-services >/dev/null 2>&1
pkill -f "[f]ast_ocr_vllm" 2>/dev/null; pkill -f "[p]ort 18771" 2>/dev/null; sleep 4
cd /workspace/fastmt
nohup python3 -m vllm.entrypoints.openai.api_server --model /workspace/secai-translation-ocr/qwen3vl-model --served-model-name ocr \
  --host 127.0.0.1 --port 18771 --dtype bfloat16 --max-model-len 16384 --max-num-seqs 32 \
  --max-num-batched-tokens 32768 --gpu-memory-utilization 0.30 --limit-mm-per-prompt '{"image":1}' \
  --mm-processor-cache-gb 0 > /workspace/fastmt/vllm-ocr.log 2>&1 &
for i in $(seq 1 240); do curl -s -m 2 -o /dev/null -w "%{http_code}" http://127.0.0.1:18771/health | grep -q 200 && break; sleep 2; done
curl -s -m 2 -o /dev/null -w "vllm-ocr health %{http_code}\n" http://127.0.0.1:18771/health
nohup /workspace/secai-specialist-services/venv/bin/python /workspace/fastmt/fast_ocr_vllm.py > /workspace/fastmt/ocr-svc.log 2>&1 &
sleep 5; ss -ltn | grep -E "18771|18083"; nvidia-smi --query-gpu=memory.used --format=csv,noheader; tail -3 /workspace/fastmt/ocr-svc.log; grep -E "ERROR|Error" /workspace/fastmt/vllm-ocr.log | head -3
