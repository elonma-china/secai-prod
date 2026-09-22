#!/usr/bin/env bash
# Giai đoạn 6b — Requalify trên GPU thật: rent 1 instance TẠM (label riêng, KHÔNG đụng
# production) qua serving-up-cyber-f1.sh, trỏ --llm-model/--embed-model/--rerank-model vào repo
# CANDIDATE cho đúng những component vừa finetune (Giai đoạn 4/4b) — component KHÔNG đổi vẫn
# dùng bản production, để so sánh công bằng chỉ trên phần vừa train. vLLM tự tải model từ HF khi
# container khởi động — đây chính là bước "tải về máy GPU" của candidate.
#
# Ghi endpoint ra file JSON (serving-up-cyber-f1.sh không có output máy-đọc-được, script này
# parse đúng 3 dòng "LLM URL/EMBED URL/RERANK URL" mà nó in ra — xem serving-up-cyber-f1.sh
# dòng in kết quả cuối) để 09_run_drift_check_candidate.sh dùng lại.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [ $# -lt 2 ]; then
  echo "usage: 08_rent_candidate_serving.sh <finetune-decision.json> <endpoint_out.json>" >&2
  exit 64
fi
DECISION="$1"; ENDPOINT_OUT="$2"

CONF_FILE="$(mktemp)"
python3 -c "
import sys, shlex, json
sys.path.insert(0, '$ROOT')
from _lib import common
c = common.load_config()
decision = json.load(open('$DECISION'))
targets = set(decision.get('finetune_targets', []))
hf = c['hf']
def repo(component):
    bucket = 'candidate_repos' if component in targets else 'production_repos'
    return hf[bucket][component]
vals = {
    'SERVING_UP': str(common.resolve_path(c['vast_serving']['up_script'])),
    'SERVING_DOWN': str(common.resolve_path(c['vast_serving']['down_script'])),
    'LABEL': c['vast_serving']['candidate_label'],
    'LLM_MODEL': repo('llm'), 'EMBED_MODEL': repo('embedding'), 'RERANK_MODEL': repo('reranker'),
}
for k, v in vals.items():
    print(f'{k}={shlex.quote(v)}')
" > "$CONF_FILE"
# shellcheck source=/dev/null
source "$CONF_FILE"; rm -f "$CONF_FILE"

[ -x "$SERVING_UP" ] || { echo "[08_rent_candidate_serving] thiếu hoặc không chạy được: $SERVING_UP" >&2; exit 1; }
echo "[08_rent_candidate_serving] rent label=$LABEL LLM=$LLM_MODEL embed=$EMBED_MODEL rerank=$RERANK_MODEL"

LOG="$(mktemp)"
"$SERVING_UP" --yes --label "$LABEL" \
  --llm-model "$LLM_MODEL" --embed-model "$EMBED_MODEL" --rerank-model "$RERANK_MODEL" \
  ${SERVING_EXTRA_ARGS:-} \
  2>&1 | tee "$LOG"

LLM_URL="$(grep -m1 '^  LLM URL' "$LOG" | sed -E 's/.*(http:\/\/[^ ]+).*/\1/')"
EMBED_URL="$(grep -m1 '^  EMBED URL' "$LOG" | sed -E 's/.*(http:\/\/[^ ]+).*/\1/')"
RERANK_URL="$(grep -m1 '^  RERANK URL' "$LOG" | sed -E 's/.*(http:\/\/[^ ]+).*/\1/')"
API_KEY="$(grep -m1 '^  API KEY' "$LOG" | sed -E 's/.*: *//')"
IID="$(grep -m1 'instance id=' "$LOG" | sed -E 's/.*instance id=([0-9]+).*/\1/')"
rm -f "$LOG"

if [ -z "$LLM_URL" ] || [ -z "$EMBED_URL" ] || [ -z "$RERANK_URL" ]; then
  echo "[08_rent_candidate_serving] không đọc được endpoint từ output serving-up-cyber-f1.sh — xem log ở trên" >&2
  exit 1
fi

python3 -c "
import json
json.dump({
    'instance_id': '$IID', 'llm_url': '$LLM_URL', 'embed_url': '$EMBED_URL',
    'rerank_url': '$RERANK_URL', 'api_key': '$API_KEY',
    'llm_model': '$LLM_MODEL', 'embed_model': '$EMBED_MODEL', 'rerank_model': '$RERANK_MODEL',
    'label': '$LABEL', 'down_script': '$SERVING_DOWN',
}, open('$ENDPOINT_OUT', 'w'), ensure_ascii=False, indent=2)
"
echo "[08_rent_candidate_serving] instance=$IID -> $ENDPOINT_OUT"
