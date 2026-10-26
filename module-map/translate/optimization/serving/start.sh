#!/usr/bin/env bash
mkdir -p /workspace/fastmt; cd /workspace/fastmt; export FASTMT_REPLICAS="${FASTMT_REPLICAS:-3}" FASTMT_PARALLEL="${FASTMT_PARALLEL:-12}"
pkill -f "[r]un_llama.sh" 2>/dev/null; pkill -f "[l]lama-server" 2>/dev/null; pkill -f "[f]ast_mt.py" 2>/dev/null; sleep 1
FASTMT_REPLICAS=$R FASTMT_PARALLEL=$FASTMT_PARALLEL nohup bash /workspace/fastmt/run_llama.sh >/dev/null 2>&1 &
R="${FASTMT_REPLICAS:-3}"
for p in $(seq 18767 $((18767+R-1))); do for i in $(seq 1 120); do curl -s -m 2 http://127.0.0.1:$p/health | grep -q ok && break; sleep 1; done; echo "$p: $(curl -s -m 2 http://127.0.0.1:$p/health)"; done
FASTMT_REPLICAS=$R FASTMT_PARALLEL=$FASTMT_PARALLEL nohup /workspace/secai-specialist-services/venv/bin/python /workspace/fastmt/fast_mt.py > /workspace/fastmt/svc.log 2>&1 &
sleep 4; ss -ltn | grep -E "1876|18868"
