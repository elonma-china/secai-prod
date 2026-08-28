#!/bin/bash
# ----------------------------------------------------------------------------
# Backend CŨ (opencode :4096). Cho backend hiện đang triển khai
# (secai-agent-gateway) dùng chay_eval_gateway_moi.sh thay vì file này.
# ----------------------------------------------------------------------------
# ============================================================================
# chay_eval_bo_moi.sh — Chạy ĐỦ 11 KPI chất lượng trên MỘT BỘ ĐỀ BẤT KỲ.
#
# VÌ SAO KHÔNG DÙNG THẲNG `run_eval_all.sh --dataset <dir>`:
#   `gen_answers.py` (bước 2 của run_eval_all.sh) hard-code đường dẫn bộ đề
#   (`gen_answers.py:21`) và KHÔNG có cờ --dataset. Với bộ đề mới, không id nào
#   khớp → answers.jsonl RỖNG → 7 KPI dựa-trên-đáp-án im lặng trả 0/null.
#   Script này chạy đúng 9 bước như run_eval_all.sh nhưng thay bước 2 bằng
#   `lam_answers.py` (có --dataset). Các bước khác gọi nguyên script gốc.
#
# Dùng:
#   ./chay_eval_bo_moi.sh --dataset ./testset-v2 [--workers 2] [--limit N] [--no-faith]
#
# Yêu cầu: source/eval/kpi/.env.bench đã điền (LLM/embed/rerank + key judge).
# Kết quả: source/eval/kpi/runs/<NGÀY-GIỜ>-evalindep/{raw-logs,reports,summary.md}
# ============================================================================
set -o pipefail
HERE="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"

DS=""; W=2; LIMIT=""; DO_FAITH=1; FAITH_SAMPLE=2000
while [ $# -gt 0 ]; do
  case "$1" in
    --dataset)      DS="$2"; shift 2 ;;
    --workers)      W="$2"; shift 2 ;;
    --limit)        LIMIT="$2"; shift 2 ;;
    --no-faith)     DO_FAITH=0; shift ;;
    --faith-sample) FAITH_SAMPLE="$2"; shift 2 ;;
    -h|--help)      sed -n '2,21p' "$0"; exit 0 ;;
    *) echo "flag lạ: $1 (xem --help)"; exit 2 ;;
  esac
done
[ -z "$DS" ] && { echo "Thiếu --dataset <thư-mục-bộ-đề>"; exit 2; }
DS="$(cd "$DS" && pwd)" || { echo "Không thấy thư mục bộ đề"; exit 2; }
LIMARG=""; [ -n "$LIMIT" ] && LIMARG="--limit $LIMIT"

# cổng chất lượng bộ đề trước khi tốn tiền gọi LLM
echo "[$(date +%T)] 0a. cổng chất lượng bộ đề..."
python3 "$HERE/validate_testset.py" "$DS" || {
  echo "✗ Bộ đề CHƯA ĐẠT cổng chất lượng — sửa xong hãy chạy eval."; exit 1; }

KPI_DIR="${KPI_DIR:-$REPO/source/eval/kpi}"
cd "$KPI_DIR" || { echo "Không thấy thư mục kpi: $KPI_DIR"; exit 1; }
[ -f .env.bench ] || { echo "Thiếu $KPI_DIR/.env.bench"; exit 1; }
set -a; . .env.bench; set +a; export DATA_ROOT="${DATA_ROOT:-/opt/secai/data}"

TS=$(date +%Y-%m-%d-%H%M%S); RUN=runs/${TS}-evalindep; RAW=$RUN/raw-logs; REP=$RUN/reports
mkdir -p "$RAW" "$REP"; LOG="$RUN/run_eval_indep.log"
exec > >(tee -a "$LOG") 2>&1
echo "[$(date +%T)] ===== EVAL BỘ ĐỀ ĐỘC LẬP · run=$RUN · ds=$DS · workers=$W $LIMARG ====="

echo "[$(date +%T)] 0b. preflight endpoints..."
for pair in "$LLM_BASE_URL/models|LLM" "$EMBED_URL/v1/models|embed" "$RERANK_URL/v1/models|rerank" "$QDRANT_URL/collections/$QDRANT_COLLECTION|qdrant"; do
  code=$(curl -s -o /dev/null -w "%{http_code}" -m 8 "${pair%%|*}" 2>/dev/null)
  echo "   ${pair##*|}: HTTP $code"
done

echo "[$(date +%T)] 1. answer-gen (workers=$W)..."
python3 scripts/bench_answers_concurrent.py --dataset "$DS" --out "$RAW/results.jsonl" --workers "$W" $LIMARG || exit 1

mkdir -p /tmp/eval_sast_indep && rm -f /tmp/eval_sast_indep/*.jsonl
python3 - "$DS" <<'PY'
import json, glob, os, sys
ds = sys.argv[1]
out = open('/tmp/eval_sast_indep/sast.jsonl', 'w', encoding='utf-8')
for f in glob.glob(os.path.join(ds, '*.jsonl')):
    if os.path.basename(f).startswith('_'):
        continue
    for l in open(f, encoding='utf-8'):
        if l.strip() and json.loads(l).get('capability') == 'sast':
            out.write(l)
out.close()
PY
for r in 2 3; do
  echo "[$(date +%T)]    SAST lượt $r (cho majority 2/3)..."
  python3 scripts/bench_answers_concurrent.py --dataset /tmp/eval_sast_indep --out "$RAW/results_sast_$r.jsonl" --workers 1
done

echo "[$(date +%T)] 2. answers (lam_answers.py — CÓ --dataset)..."
python3 "$HERE/lam_answers.py" --dataset "$DS" --results "$RAW/results.jsonl" --out "$RAW/answers.jsonl" || exit 1

echo "[$(date +%T)] 3. retrieval id-aware (EMB Hit@10 + RERANK P@1)..."
python3 scripts/retrieval_kpi.py --dataset "$DS" --out "$RAW" --contexts "$RAW/contexts.jsonl" $LIMARG
echo "[$(date +%T)] 4. llm_kpi (entity/relevance-judge/hallu)..."
python3 scripts/llm_kpi.py --answers "$RAW/answers.jsonl" --contexts "$RAW/contexts.jsonl" --out "$RAW" --no-ragas
echo "[$(date +%T)] 5. task_kpi (SAST majority 2/3 + exploit + remediation)..."
python3 scripts/task_kpi.py --answers "$RAW/answers.jsonl" --out "$RAW" --sast-runs "$RAW"/results_sast_2.jsonl "$RAW"/results_sast_3.jsonl
echo "[$(date +%T)] 6. perf..."
python3 scripts/perf_kpi.py --results "$RAW/results.jsonl" --out "$RAW" --concurrency 0
echo "[$(date +%T)] 7. sanitization..."
python3 sanitizer/gen_pii_dataset.py --n 500 >/dev/null 2>&1; python3 scripts/sanitization_kpi.py --out "$RAW"
if [ "$DO_FAITH" = "1" ]; then
  echo "[$(date +%T)] 8. faithfulness (gold-contexts, sample=$FAITH_SAMPLE)..."
  FAITH_GOLD_CONTEXTS=1 RAGAS_SAMPLE="$FAITH_SAMPLE" python3 scripts/faith_judge.py "$RAW" "$RAW"
fi
echo "[$(date +%T)] 9. build_reports..."
python3 scripts/build_reports.py --logs "$RAW" --reports "$REP" --run-dir "$RUN" --model "${ACC_MODEL:-model}" --date "$TS"
python3 scripts/make_deliverables.py --logs "$RAW" --reports "$REP"

echo "[$(date +%T)] 10. kiểm deliverable..."
miss=0
check() { if [ -s "$1" ]; then echo "   ✓ ${1#$RUN/}"; else echo "   ✗ THIẾU ${1#$RUN/}"; miss=$((miss+1)); fi; }
for f in ai-task-kpi-report.md embedding-report.md llm-kpi-report.md reranking-report.md \
         performance-report.md sanitization-report.md radar-data.json; do check "$REP/$f"; done
for f in auto-remediation.jsonl embedding-hit-rate-at-10.jsonl exploit-explanation.jsonl \
         llm-answer-relevance.jsonl llm-entity-precision.jsonl llm-hallucination.jsonl \
         reranking-precision-at-1.jsonl sast.jsonl kpi-summary.json run-manifest.json; do check "$RAW/$f"; done
check "$RUN/summary.md"

echo "[$(date +%T)] ===== XONG → $KPI_DIR/$RUN (thiếu $miss deliverable) ====="
echo
echo "So sánh với baseline đã công bố:"
echo "  python3 $REPO/KPI-VA-GEN-TEST/01-KPI/compare_kpi.py \\"
echo "      --a $KPI_DIR/$RAW --b $REPO/kpi_results/quality/raw-logs \\"
echo "      --md bao-cao-so-sanh.md --xlsx bao-cao-so-sanh.xlsx"
