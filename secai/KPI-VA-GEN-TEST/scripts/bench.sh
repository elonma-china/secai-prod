#!/bin/bash
# bench.sh — Bench KPI AI turnkey, endpoint-driven, output gom 1 folder timestamped.
#
# Cấp URL 3 service (LLM/embed/rerank) + Qdrant + backend qua .env.bench rồi chạy:
#     cp eval/kpi/.env.bench.example eval/kpi/.env.bench   # điền URL
#     bash eval/kpi/scripts/bench.sh                       # full 1000 mẫu
#     bash eval/kpi/scripts/bench.sh --limit 5             # smoke nhanh
#
# Output: eval/kpi/runs/<YYYY-MM-DD-HHMMSS>/{raw-logs,reports,summary.md}
# (symlink runs/latest trỏ run mới nhất). KHÔNG dùng SSH; mọi script gọi endpoint qua URL.
#
# Phases: 0 answers(full agent) · 1 retrieval · 2 LLM-KB · 3 perf · 4 task · 5 sanitization ·
#         6 faithfulness(RAGAS, skip mềm) · 7 reports+deliverables.
set -o pipefail

KPI_DIR="$(cd "$(dirname "$0")/.." && pwd)"
EVAL_DIR="$(cd "$KPI_DIR/.." && pwd)"
REPO_ROOT="$(cd "$EVAL_DIR/.." && pwd)"
SCRIPTS="$KPI_DIR/scripts"

# ── args ──
LIMIT=""; RESUME="--resume"; ENVF="$KPI_DIR/.env.bench"
while [ $# -gt 0 ]; do
  case "$1" in
    --limit) LIMIT="--limit $2"; shift 2;;
    --no-resume) RESUME=""; shift;;
    --env) ENVF="$2"; shift 2;;
    -h|--help) grep '^# ' "$0" | sed 's/^# //'; exit 0;;
    *) echo "arg lạ: $1 (xem --help)"; exit 2;;
  esac
done

# ── load .env.bench ──
[ -f "$ENVF" ] || { echo "Thiếu $ENVF — chạy: cp $KPI_DIR/.env.bench.example $ENVF rồi điền URL."; exit 1; }
set -a; . "$ENVF"; set +a
: "${QDRANT_COLLECTION:=cve_kb}"
: "${DATA_ROOT:=$REPO_ROOT/data}"
export DATA_ROOT

TS="$(date +%Y-%m-%d-%H%M%S)"
RUN="$KPI_DIR/runs/$TS"; RAW="$RUN/raw-logs"; REP="$RUN/reports"
mkdir -p "$RAW" "$REP"
ln -sfn "$RUN" "$KPI_DIR/runs/latest"

echo "============================================================"
echo " Bench KPI AI — run $TS"
echo "   LLM      ${LLM_BASE_URL:-?}  ($LLM_MODEL)"
echo "   EMBED    ${EMBED_URL:-?}"
echo "   RERANK   ${RERANK_URL:-?}"
echo "   QDRANT   ${QDRANT_URL:-?}/$QDRANT_COLLECTION"
echo "   BACKEND  ${ACC_BASE:-?}  (provider=${ACC_PROVIDER:-?} model=${ACC_MODEL:-?})"
echo "   DATA     $DATA_ROOT"
echo "   OUT      $RUN"
echo "============================================================"

# ── preflight ──
ck() {  # $1 name  $2 url  $3 fatal(1/0)  $4 want(optional: mã HTTP chính xác, vd 200)
  code=$(curl -s -o /dev/null -w "%{http_code}" --max-time 8 "$2" 2>/dev/null)
  want="${4:-}"
  if [ "$code" = "000" ] || { [ -n "$want" ] && [ "$code" != "$want" ]; }; then
    reason="không reach"; [ "$code" != "000" ] && reason="HTTP $code (cần $want)"
    echo "  [FAIL] $1  $2  ($reason)"
    [ "${3:-1}" = "1" ] && PF=1
    return 1
  fi
  echo "  [ok]   $1  $2  ($code)"
}
echo "== Preflight =="
PF=0
ck "LLM    " "${LLM_BASE_URL%/}/models" 1
ck "EMBED  " "${EMBED_URL%/}/v1/embeddings" 1     # GET→405 vẫn tính 'reachable'
ck "RERANK " "${RERANK_URL%/}/v1/rerank" 1
ck "QDRANT " "${QDRANT_URL%/}/collections/$QDRANT_COLLECTION" 1 200   # 200 = collection cve_kb tồn tại (404 = chưa seed)
ck "BACKEND" "${ACC_BASE%/}/" 1
if [ -f "$DATA_ROOT/knowledge_base/bm25_cve_kb/vocab.index.json" ]; then
  echo "  [ok]   BM25    $DATA_ROOT/knowledge_base/bm25_cve_kb"
else
  echo "  [FAIL] BM25    thiếu $DATA_ROOT/knowledge_base/bm25_cve_kb (retrieval sẽ dense-only)"; PF=1
fi
if [ -n "${EMBED_MODEL:-}" ] && [ -f "$DATA_ROOT/knowledge_base/bm25_cve_kb/embedding_model.json" ]; then
  idx_model=$(tr -d ' \n' < "$DATA_ROOT/knowledge_base/bm25_cve_kb/embedding_model.json")
  case "$idx_model" in
    *"$EMBED_MODEL"*) : ;;
    *) echo "  [WARN] EMBED_MODEL=$EMBED_MODEL khác model index ($idx_model) → recall có thể sai" ;;
  esac
fi
[ "$PF" = "1" ] && { echo; echo "Preflight FAIL — sửa .env.bench/hạ tầng rồi chạy lại."; exit 1; }

step() { echo; echo ">> $*"; }

# ── Phase 0: answers (full agent secAI) ──
step "Phase 0/7 — sinh đáp án (full agent secAI qua backend)"
python3 "$EVAL_DIR/scripts/acceptance_runner.py" \
  --dataset "$EVAL_DIR/datasets-acceptance" --out "$RAW" --run-id "$TS" \
  --base "$ACC_BASE" --provider "${ACC_PROVIDER:-secai-llm}" --model "${ACC_MODEL:-$LLM_MODEL}" \
  --agent "${ACC_AGENT:-build}" $RESUME $LIMIT --restart-cmd "" \
  || { echo "answer-gen lỗi — kiểm tra backend/auth/provider."; exit 1; }
python3 "$SCRIPTS/gen_answers.py" --results "$RAW/results.jsonl" --out "$RAW/answers.jsonl" \
  || { echo "gen_answers lỗi."; exit 1; }

# ── Phase 1: retrieval (Hit@10 / P@1) ──
step "Phase 1/7 — retrieval (Hit@10 / P@1)"
python3 "$SCRIPTS/retrieval_kpi.py" --out "$RAW" --contexts "$RAW/contexts.jsonl" $LIMIT \
  || echo "  (retrieval lỗi — kiểm tra Qdrant/embed/rerank/BM25)"

# ── Phase 2: LLM-KB (entity / hallucination / answer-relevance) ──
step "Phase 2/7 — LLM-KB (entity/hallucination/relevance)"
python3 "$SCRIPTS/llm_kpi.py" --no-ragas --answers "$RAW/answers.jsonl" --contexts "$RAW/contexts.jsonl" --out "$RAW" \
  || echo "  (llm-kb lỗi — kiểm tra embed/Qdrant)"

# ── Phase 3: hiệu năng ──
step "Phase 3/7 — hiệu năng (TTFT/TPS/response + throughput)"
python3 "$SCRIPTS/perf_kpi.py" --results "$RAW/results.jsonl" --out "$RAW" --concurrency 0 \
  || echo "  (perf lỗi)"

# ── Phase 4: task accuracy ──
step "Phase 4/7 — task (SAST/exploit/remediation)"
python3 "$SCRIPTS/task_kpi.py" --answers "$RAW/answers.jsonl" --out "$RAW" \
  || echo "  (task lỗi)"

# ── Phase 5: sanitization (500 mẫu) ──
step "Phase 5/7 — sanitization PII/Secret (500 mẫu)"
python3 "$KPI_DIR/sanitizer/gen_pii_dataset.py" --n 500
python3 "$SCRIPTS/sanitization_kpi.py" --out "$RAW" \
  || echo "  (sanitization lỗi)"

# ── Phase 6: faithfulness (RAGAS) — skip mềm nếu thiếu judge key ──
step "Phase 6/7 — faithfulness (RAGAS)"
if [ -n "${RAGAS_JUDGE_API_KEY:-}" ]; then
  python3 "$SCRIPTS/llm_kpi.py" --only-faith --answers "$RAW/answers.jsonl" --contexts "$RAW/contexts.jsonl" --out "$RAW" \
    || echo "  (faithfulness lỗi — kiểm tra ragas + judge key)"
else
  echo "  (skip: chưa set RAGAS_JUDGE_API_KEY trong .env.bench)"
fi

# ── Phase 7: reports + deliverables ──
step "Phase 7/7 — build reports + deliverables"
python3 "$SCRIPTS/build_reports.py" --logs "$RAW" --reports "$REP" --run-dir "$RUN" --model "$LLM_MODEL" --date "$TS" \
  || { echo "build_reports lỗi."; exit 1; }

echo
echo "============================================================"
echo " XONG. Kết quả run: $RUN"
echo "   summary:   $RUN/summary.md"
echo "   reports:   $REP  (radar.png, *-report.md, improvement-plan.md, FN/FP analysis, failed-samples.xlsx)"
echo "   raw-logs:  $RAW  (*.jsonl, kpi-summary.json/.xlsx, run-manifest.json)"
echo "   symlink:   $KPI_DIR/runs/latest -> $TS"
echo "============================================================"
