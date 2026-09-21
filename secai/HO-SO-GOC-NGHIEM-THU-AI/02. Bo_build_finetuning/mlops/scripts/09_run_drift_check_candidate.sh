#!/usr/bin/env bash
# Giai đoạn 6c — Requalify: đo model CANDIDATE (vừa rent tạm ở Giai đoạn 6b, endpoint đọc từ
# endpoint.json) trên CÙNG bộ eval CVE mới, so lại `approved_metrics.json` — tái dùng đúng hàm
# `run_gate_eval` như bước 03 (không chép lại logic).
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=_lib/gate_eval_common.sh
source "$ROOT/_lib/gate_eval_common.sh"

if [ $# -lt 3 ]; then
  echo "usage: 09_run_drift_check_candidate.sh <adapted_eval_dir> <report_out_dir> <endpoint.json>" >&2
  exit 64
fi
EVAL_DIR="$1"; REPORT_DIR="$2"; ENDPOINT_JSON="$3"
mkdir -p "$REPORT_DIR"

CONF_FILE="$(mktemp)"
python3 -c "
import json, shlex
e = json.load(open('$ENDPOINT_JSON'))
vals = {
    'LLM_URL': e['llm_url'], 'LLM_KEY': e['api_key'], 'LLM_MODEL': e['llm_model'],
    'EMBED_REPO': e['embed_model'], 'RERANK_REPO': e['rerank_model'],
}
for k, v in vals.items():
    print(f'{k}={shlex.quote(v)}')
" > "$CONF_FILE"
# shellcheck source=/dev/null
source "$CONF_FILE"; rm -f "$CONF_FILE"

run_gate_eval "$EVAL_DIR" "$REPORT_DIR" "$EMBED_REPO" "$RERANK_REPO" "$LLM_URL" "$LLM_KEY" "$LLM_MODEL"
