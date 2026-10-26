#!/usr/bin/env bash
# Dựng/khởi động lại stack dịch CÔ LẬP (không đụng container production secai-translation-*).
# Dùng:  run_stack.sh [VAR=VALUE ...]   (ghi đè env khi thử cấu hình tối ưu)
set -euo pipefail
cd /home/ict/secai-opt
OVR=("$@")
GW_IMG=${GW_IMG:-secai-agent-gateway:translate-noacronym-20261010-gateway}
WK_IMG=${WK_IMG:-secai-agent-gateway:translate-opt-pdf}
mkenv() {  # $1 = gateway|worker
{
  cat base-$1.env
  echo "TRANSLATE_V2_CONVERTER_URL=http://127.0.0.1:18090"
  echo "TRANSLATE_V2_CONVERTER_TOKEN=$(cat conv.token)"
  echo "TRANSLATE_V2_OCR_BASE_URL=http://127.0.0.1:28083"
  echo "TRANSLATE_V2_SPECIALIST_BASE_URL=http://127.0.0.1:28868"
  echo "MULTILANG_SPECIALIST_BASE_URL=http://127.0.0.1:28868"
  echo "TRANSLATE_V2_OCR_API_KEY=$(cat ocr.token)"
  echo "TRANSLATE_V2_SPECIALIST_API_KEY=$(cat mt.token)"
  echo "MULTILANG_SPECIALIST_API_KEY=$(cat mt.token)"
  for kv in "${OVR[@]}"; do echo "$kv"; done
} > run.env.tmp
# biến cuối cùng thắng khi trùng khóa
python3 - "$1" <<'P'
import sys
d={}
for l in open('/home/ict/secai-opt/run.env.tmp'):
    l=l.rstrip('\n')
    if '=' in l: k,v=l.split('=',1); d[k]=v
open(f'/home/ict/secai-opt/run-{sys.argv[1]}.env','w').write(''.join(f'{k}={v}\n' for k,v in d.items()))
P
rm -f run.env.tmp; chmod 600 run-$1.env
}
mkenv gateway; mkenv worker
OVL=()
[ "${USE_OVERLAY:-1}" = 1 ] && OVL=(-v /home/ict/secai-opt/overlay/src:/app/src:ro)
docker rm -f secai-opt-gateway secai-opt-worker >/dev/null 2>&1 || true
# .doc: converter chạy dịch vụ riêng (secai-opt-converter, :18090) — worker/gateway chỉ gọi HTTP, KHÔNG gắn docker.sock.
DOCK=()
docker run -d --restart unless-stopped --ulimit nofile=65536:65536 --name secai-opt-gateway --network host --env-file run-gateway.env --user app "${OVL[@]}" "${DOCK[@]}" \
  -v secai-opt-data:/app/data -v /home/ict/secai-translation-docker-release/tokenizer:/app/translation-tokenizer:ro \
  --label secai.purpose=translate-opt "$GW_IMG" uvicorn secai_gateway.multilang_app:app --host 127.0.0.1 --port 18285 >/dev/null
docker run -d --restart unless-stopped --ulimit nofile=65536:65536 --name secai-opt-worker --network host --env-file run-worker.env --user app "${OVL[@]}" "${DOCK[@]}" \
  -v secai-opt-data:/app/data -v /home/ict/secai-translation-docker-release/tokenizer:/app/translation-tokenizer:ro \
  --label secai.purpose=translate-opt "$WK_IMG" python -m secai_gateway.translation.worker >/dev/null
echo started
