#!/usr/bin/env bash
# Đổi chế độ serving OCR/MT trên instance thử nghiệm 55235250 (ssh3.vast.ai:20500).
#   apply_mode.sh ondemand            # như baseline (không resident, parallel 1)
#   apply_mode.sh resident N          # resident + MT --parallel N
# Giữ token hiện có, expected-incumbents 0 (instance này không có LLM/embed/rerank).
# Sau install tự bỏ thư mục CUDA compat 580.95 khỏi LD_LIBRARY_PATH (host driver 580.173 mới hơn compat).
set -euo pipefail
MODE="${1:?ondemand|resident}"; N="${2:-1}"
SRV=/home/ict/AIEngines/secai/secai-agent-gateway/secai-serving-vast
HOST=root@ssh3.vast.ai; PORT=20500
EXTRA=(${EXTRA_FLAGS:-})
[ "$MODE" = resident ] && EXTRA+=(--resident --mt-parallel "$N")
"$SRV/serving-specialists-install.sh" --host "$HOST" --port "$PORT" --reuse-artifacts \
  --python /workspace/secai-specialist-services/venv/bin/python \
  --mt-token-file /workspace/secai-specialist-services/mt.token \
  --ocr-token-file /workspace/secai-specialist-services/ocr.token \
  --expected-incumbents 0 "${EXTRA[@]}" 2>&1 | grep -v -E 'Welcome|Have fun' | tail -8 || true
ssh -p "$PORT" "$HOST" 'cd /workspace/secai-specialist-services && python3 - <<EOF
import json
p="config.json"; c=json.load(open(p))
def walk(o):
    if isinstance(o,dict):
        for k,v in o.items():
            if k=="LD_LIBRARY_PATH" and isinstance(v,str):
                o[k]=":".join(x for x in v.split(":") if "compat" not in x)
            else: walk(v)
    elif isinstance(o,list):
        for x in o: walk(x)
walk(c); json.dump(c,open(p,"w"),indent=2)
EOF
python3 bootstrap.py restart --root /workspace/secai-specialist-services >/dev/null 2>&1; sleep 8
python3 bootstrap.py ready --root /workspace/secai-specialist-services --smoke 2>&1 | tail -2
nvidia-smi --query-gpu=memory.used --format=csv,noheader' 2>&1 | grep -v -E 'Welcome|Have fun'
