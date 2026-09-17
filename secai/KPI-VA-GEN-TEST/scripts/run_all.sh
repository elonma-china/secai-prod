#!/bin/bash
# run_all.sh — điều phối nghiệm thu KPI AI (VPS phần truy hồi/perf/KB · local RAGAS/report).
# Yêu cầu: ssh alias `secai`, đã rsync eval/kpi → /opt/secai/kpi, DATA_ROOT BM25 trên VPS.
# Env: DEEPSEEK_API_KEY (RAGAS). Đổi model: LLM_BASE_URL/LLM_MODEL/LLM_API_KEY.
set -e
KPI_DIR="$(cd "$(dirname "$0")/.." && pwd)"
SSH=${SSH:-secai}
REMOTE=${REMOTE:-/opt/secai/kpi}
MODEL=${LLM_MODEL:-deepseek-v4-flash}
DATE=$(date +%F)

echo "== 0. answers.jsonl từ run acceptance =="
python3 "$KPI_DIR/scripts/gen_answers.py"

echo "== 1. rsync eval/kpi → VPS =="
rsync -az "$KPI_DIR/" "$SSH:$REMOTE/"

echo "== 2. [VPS] retrieval + LLM-KB metrics + perf =="
ssh "$SSH" "cd $REMOTE/scripts && DATA_ROOT=/opt/secai/data python3 retrieval_kpi.py"
ssh "$SSH" "cd $REMOTE/scripts && DATA_ROOT=/opt/secai/data python3 llm_kpi.py --no-ragas"
ssh "$SSH" "cd $REMOTE/scripts && DATA_ROOT=/opt/secai/data python3 perf_kpi.py --concurrency 0"

echo "== 3. pull VPS raw-logs về local =="
rsync -az "$SSH:$REMOTE/raw-logs/" "$KPI_DIR/raw-logs/"

echo "== 4. [LOCAL] task accuracy + sanitization =="
python3 "$KPI_DIR/scripts/task_kpi.py"
python3 "$KPI_DIR/sanitizer/gen_pii_dataset.py"
python3 "$KPI_DIR/scripts/sanitization_kpi.py"

echo "== 5. [LOCAL] faithfulness (RAGAS, cần tunnel embed nếu dùng) =="
ssh -fN -L 8010:localhost:8010 "$SSH" 2>/dev/null || true
LLM_API_KEY="${LLM_API_KEY:-$DEEPSEEK_API_KEY}" python3 "$KPI_DIR/scripts/llm_kpi.py" --no-embed --no-kb || echo "  (faithfulness skip: kiểm tra ragas + LLM key)"

echo "== 6. build reports + radar + xlsx + manifest =="
python3 "$KPI_DIR/scripts/build_reports.py" --model "$MODEL" --date "$DATE"
echo "Xong. Xem $KPI_DIR/reports/ + raw-logs/kpi-summary.xlsx"
