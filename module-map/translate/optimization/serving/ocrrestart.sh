#!/bin/bash
# usage: ocrrestart.sh VAR=VAL ...
for p in $(pgrep -f "fast_ocr_vllm.p[y]"); do kill $p; done
sleep 2
cd /workspace/fastmt
env "$@" setsid nohup /workspace/secai-specialist-services/venv/bin/python /workspace/fastmt/fast_ocr_vllm.py > /workspace/fastmt/ocr-svc.log 2>&1 < /dev/null &
for i in $(seq 1 30); do curl -s -m2 http://127.0.0.1:18083/health | grep -q ready && break; sleep 1; done
echo up
