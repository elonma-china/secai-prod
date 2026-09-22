#!/usr/bin/env bash
# Giai đoạn 7 — Promote hoặc Hold (rollback-safe theo đúng yêu cầu hợp đồng: "Nếu mô hình sau
# khi retrain không vượt qua được ngưỡng kiểm định chất lượng, hệ thống sẽ giữ nguyên model hiện
# tại đang serving"):
#   PASS (candidate đạt lại ngưỡng so baseline) -> approve baseline mới + push candidate lên
#     repo PRODUCTION trên HF + rent instance production mới (tự tải model mới từ HF) + trỏ lại
#     gateway .env + docker compose up -d gateway + smoke test /health.
#   FAIL -> log rõ giữ nguyên serving hiện tại, KHÔNG đụng gì tới production/HF production.
# Luôn huỷ instance candidate tạm ở cuối (dù pass hay fail) để không tốn tiền thuê thêm.
set -uo pipefail  # KHÔNG set -e: phải chạy tới bước dọn instance candidate dù lỗi giữa chừng
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FT="$ROOT/../../finetune"

if [ $# -lt 4 ]; then
  echo "usage: 10_promote_or_hold.sh <candidate_decision.json> <endpoint.json> <artifacts_manifest.json> <run_started_at_iso> [--allow-deploy]" >&2
  exit 64
fi
CANDIDATE_DECISION="$1"; ENDPOINT_JSON="$2"; MANIFEST="$3"; RUN_STARTED_AT="$4"; shift 4
ALLOW_DEPLOY=0
[ "${1:-}" = "--allow-deploy" ] && ALLOW_DEPLOY=1

DECISION_VALUE="$(python3 -c "import json;print(json.load(open('$CANDIDATE_DECISION'))['decision'])")"
INSTANCE_ID="$(python3 -c "import json;print(json.load(open('$ENDPOINT_JSON'))['instance_id'])")"
DOWN_SCRIPT="$(python3 -c "import json;print(json.load(open('$ENDPOINT_JSON'))['down_script'])")"

cleanup_candidate() {
  echo "[10_promote_or_hold] huỷ instance candidate tạm ($INSTANCE_ID) — tránh tốn tiền thuê thêm"
  "$DOWN_SCRIPT" "$INSTANCE_ID" || echo "[10_promote_or_hold] CẢNH BÁO: huỷ instance $INSTANCE_ID thất bại — huỷ tay: $DOWN_SCRIPT $INSTANCE_ID" >&2
}
trap cleanup_candidate EXIT

if [ "$DECISION_VALUE" != "NO_FINETUNE_REQUIRED" ]; then
  echo "[10_promote_or_hold] candidate KHÔNG đạt (decision=$DECISION_VALUE) — GIỮ NGUYÊN model hiện tại đang serving."
  echo "[10_promote_or_hold] repo candidate trên HF được GIỮ LẠI (không xoá) để debug; production KHÔNG bị đụng."
  exit 3
fi
echo "[10_promote_or_hold] candidate ĐẠT ngưỡng (decision=NO_FINETUNE_REQUIRED so baseline) — promote"

if [ "$ALLOW_DEPLOY" != "1" ]; then
  echo "[10_promote_or_hold] thiếu --allow-deploy — CHỈ preview, KHÔNG ghi đè HF production / redeploy thật:"
  python3 -c "
import json
m = json.load(open('$MANIFEST'))
for comp, d in m.items():
    print(f'  sẽ chạy: 07_push_candidate_to_hf.py --local-dir {d} --component {comp} --target production')
print('  sẽ chạy: serving-up-cyber-f1.sh --yes --label <production> --replace-old (trỏ repo production)')
print('  sẽ chạy: cập nhật gateway .env + docker compose up -d gateway + smoke test /health')
"
  exit 0
fi

echo "[10_promote_or_hold] approve baseline mới (dựa trên số đo candidate vừa đạt)"
REPORT_DIR="$(dirname "$CANDIDATE_DECISION")"
python3 "$FT/scripts/approve_finetune_baseline.py" --approve \
  --report embedding="$REPORT_DIR/embedding.json" \
  --report reranker="$REPORT_DIR/reranker.json" \
  --report llm="$REPORT_DIR/llm-qa.json"

echo "[10_promote_or_hold] push component vừa finetune -> repo PRODUCTION"
for COMP in $(python3 -c "import json;print(' '.join(json.load(open('$MANIFEST'))))"); do
  DIR="$(python3 -c "import json;print(json.load(open('$MANIFEST'))['$COMP'])")"
  python3 "$ROOT/07_push_candidate_to_hf.py" --local-dir "$DIR" --component "$COMP" --target production
done

CONF_FILE="$(mktemp)"
python3 -c "
import sys, shlex
sys.path.insert(0, '$ROOT')
from _lib import common
c = common.load_config()
vals = {
    'SERVING_UP': str(common.resolve_path(c['vast_serving']['up_script'])),
    'PROD_LABEL': c['vast_serving']['production_label'],
    'LLM_MODEL': c['hf']['production_repos']['llm'],
    'EMBED_MODEL': c['hf']['production_repos']['embedding'],
    'RERANK_MODEL': c['hf']['production_repos']['reranker'],
    'GW_ENV': str(common.resolve_path(c['gateway']['env_file'])),
    'GW_DOCKER_ENV': str(common.resolve_path(c['gateway']['docker_env_file'])),
    'COMPOSE_DIR': str(common.resolve_path(c['gateway']['compose_dir'])),
    'HEALTH_URL': c['gateway']['default_health_url'],
}
for k, v in vals.items():
    print(f'{k}={shlex.quote(v)}')
" > "$CONF_FILE"
# shellcheck source=/dev/null
source "$CONF_FILE"; rm -f "$CONF_FILE"

echo "[10_promote_or_hold] rent instance PRODUCTION mới (label=$PROD_LABEL, --replace-old huỷ bản cũ sau khi healthy)"
LOG="$(mktemp)"
"$SERVING_UP" --yes --label "$PROD_LABEL" --replace-old \
  --llm-model "$LLM_MODEL" --embed-model "$EMBED_MODEL" --rerank-model "$RERANK_MODEL" \
  ${SERVING_EXTRA_ARGS:-} \
  2>&1 | tee "$LOG"
NEW_LLM_URL="$(grep -m1 '^  LLM URL' "$LOG" | sed -E 's/.*(http:\/\/[^ ]+)\/v1.*/\1/')"
NEW_EMBED_URL="$(grep -m1 '^  EMBED URL' "$LOG" | sed -E 's/.*(http:\/\/[^ ]+).*/\1/')"
NEW_RERANK_URL="$(grep -m1 '^  RERANK URL' "$LOG" | sed -E 's/.*(http:\/\/[^ ]+).*/\1/')"
NEW_API_KEY="$(grep -m1 '^  API KEY' "$LOG" | sed -E 's/.*: *//')"
rm -f "$LOG"
if [ -z "$NEW_LLM_URL" ]; then
  echo "[10_promote_or_hold] LỖI: không đọc được endpoint production mới — DỪNG, KHÔNG cập nhật .env (production CŨ vẫn đang chạy, an toàn, chưa bị --replace-old huỷ vì stack mới chưa healthy)" >&2
  exit 1
fi

echo "[10_promote_or_hold] cập nhật gateway .env -> $NEW_LLM_URL"
for ENV_FILE in "$GW_ENV" "$GW_DOCKER_ENV"; do
  [ -f "$ENV_FILE" ] || continue
  python3 "$ROOT/_lib/update_env_file.py" --file "$ENV_FILE" \
    --set "QWEN_BASE_URL=${NEW_LLM_URL}/v1" --set "QWEN_API_KEY=${NEW_API_KEY}" \
    --set "EMBEDDING_API_URL=${NEW_EMBED_URL}" --set "EMBEDDING_API_KEY=${NEW_API_KEY}" \
    --set "RERANKER_API_URL=${NEW_RERANK_URL}" --set "RERANKER_API_KEY=${NEW_API_KEY}"
done

echo "[10_promote_or_hold] docker compose up -d gateway"
( cd "$COMPOSE_DIR" && docker compose up -d gateway )

sleep 5
if curl -fsS "$HEALTH_URL" 2>/dev/null | grep -q '"status":"ok"'; then
  echo "[10_promote_or_hold] /health OK — promote thành công, model mới đang serving"
else
  echo "[10_promote_or_hold] CẢNH BÁO: /health không OK ngay sau redeploy — kiểm tra tay: $HEALTH_URL (có thể chỉ cần thêm vài giây khởi động gateway)" >&2
fi

echo "[10_promote_or_hold] cập nhật watermark state/last_finetune.json"
python3 -c "
import sys, json
sys.path.insert(0, '$ROOT')
from _lib import common
state = common.load_state()
manifest = json.load(open('$MANIFEST'))
for comp in state['components']:
    state['components'][comp]['watermark'] = '$RUN_STARTED_AT'
for comp in manifest:
    state['components'][comp]['approved_at'] = '$RUN_STARTED_AT'
common.save_state(state)
print('watermark ->', '$RUN_STARTED_AT', 'cho toàn bộ 3 component; approved_at ghi thêm cho:', list(manifest))
"
