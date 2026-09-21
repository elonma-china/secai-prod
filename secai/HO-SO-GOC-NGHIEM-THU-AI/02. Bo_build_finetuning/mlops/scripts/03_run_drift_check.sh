#!/usr/bin/env bash
# Giai đoạn 3 — Phát hiện suy giảm (Drift Detection): đo MODEL ĐANG SERVING (production) trên
# bộ eval mới (giai đoạn 2) bằng đúng 3 evaluator của gate (eval_embedding/eval_reranker/
# eval_llm_qa.py — không viết lại), rồi chạy check_finetune_thresholds.py so baseline đã duyệt.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=_lib/gate_eval_common.sh
source "$ROOT/_lib/gate_eval_common.sh"

if [ $# -lt 2 ]; then
  echo "usage: 03_run_drift_check.sh <adapted_eval_dir> <report_out_dir>" >&2
  exit 64
fi
EVAL_DIR="$1"; REPORT_DIR="$2"
mkdir -p "$REPORT_DIR"

CONF_FILE="$(mktemp)"
python3 -c "
import sys, shlex
sys.path.insert(0, '$ROOT')
from _lib import common
c = common.load_config()
# Đã cutover sang Docker 2026-08-20 (xem CLAUDE.md secai-docker-unified-stack) — gateway ĐANG
# CHẠY đọc secai-docker/.env, không phải secai-agent-gateway/.env gốc (file đó có thể lỗi thời,
# đã hit live 2026-09-18: env_file trỏ 1 LLM instance đã bị huỷ, docker_env_file mới đúng cái
# đang serving thật). Merge, docker_env_file đè lên nếu có giá trị.
gw_plain = common.read_env_file(common.resolve_path(c['gateway']['env_file']))
gw_docker = common.read_env_file(common.resolve_path(c['gateway']['docker_env_file']))
gw = {**gw_plain, **{k: v for k, v in gw_docker.items() if v}}
vals = {
    'LLM_URL': gw.get('QWEN_BASE_URL', ''),
    'LLM_KEY': gw.get('QWEN_API_KEY', ''),
    'LLM_MODEL': c['hf']['production_repos']['llm'],
    'EMBED_REPO': c['hf']['production_repos']['embedding'],
    'RERANK_REPO': c['hf']['production_repos']['reranker'],
}
for k, v in vals.items():
    print(f'{k}={shlex.quote(v)}')
" > "$CONF_FILE"
# shellcheck source=/dev/null
source "$CONF_FILE"; rm -f "$CONF_FILE"

[ -n "$LLM_URL" ] || { echo "[03_run_drift_check] không đọc được QWEN_BASE_URL từ gateway .env — kiểm tra mlops/configs/pipeline.yml:gateway.env_file" >&2; exit 1; }

# DETECTION dùng policy RIÊNG (ngưỡng hợp bộ synthetic nhỏ gold-yếu) — KHÁC requalify (stage 6c
# để trống GATE_POLICY -> policy benchmark KPI đầy đủ). Xem finetune/configs/finetune_thresholds_detect.yml.
DETECT_POLICY="$(cd "$ROOT/../../finetune/configs" && pwd)/finetune_thresholds_detect.yml"
[ -f "$DETECT_POLICY" ] && export GATE_POLICY="$DETECT_POLICY"

run_gate_eval "$EVAL_DIR" "$REPORT_DIR" "$EMBED_REPO" "$RERANK_REPO" "$LLM_URL" "$LLM_KEY" "$LLM_MODEL"
