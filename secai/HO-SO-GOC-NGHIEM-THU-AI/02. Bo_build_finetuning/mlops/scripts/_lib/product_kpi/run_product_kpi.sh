#!/usr/bin/env bash
# GATE SẢN PHẨM hợp nhất — đo cả 3 loại model qua FULL PIPELINE, neo trần KPI hợp đồng ×0.95:
#   • retrieval (embedding Hit@10 id-aware + reranker P@1) — retrieval_kpi.py
#   • LLM-RAG (faithfulness/relevance/entity/hallucination) — llm_kpi + faith_judge (judge gpt-4o-mini)
#   • task (SAST/Exploit/Remediation accuracy) — task_kpi, grader tất định tự-sinh từ CVE mới
# Sinh answers 1 LẦN qua agent /ai/chat (mã SAST inline), rồi chấm cả 9 check của
# finetune_thresholds_product.yml.  Endpoint mặc định = PRODUCTION (secai-docker/.env); override qua
# env (EMBED_URL/RERANK_URL/QDRANT_URL/GW_BASE...) để đo CANDIDATE (stage 9).
#
# Usage: run_product_kpi.sh <dataset_dir> <out_dir> [gateway_base] [limit]
set -euo pipefail
DATASET="${1:?cần dataset_dir}"; OUT="${2:?cần out_dir}"; GW_BASE="${3:-http://localhost:8131}"; LIMIT="${4:-}"

PKG="/home/ict/AIEngines/secai/HO-SO-GOC-NGHIEM-THU-AI/02. Bo_build_finetuning"
GW="/home/ict/AIEngines/secai/secai-agent-gateway"
LIB="$PKG/mlops/scripts/_lib/product_kpi"
PY="$PKG/mlops/.venv-eval/bin/python"
DOCKER_ENV="/home/ict/AIEngines/secai/secai-docker/.env"
MLOPS_ENV="$HOME/.config/secai-mlops/mlops.env"
getenv() { grep -E "^$1=" "$DOCKER_ENV" | head -1 | cut -d= -f2-; }

# ── judge độc lập (gpt-4o-mini ổn định cho relevance/faithfulness — xem run_llm_kpi.sh) ──
set -a; . "$MLOPS_ENV"; set +a
: "${JUDGE_BASE_URL:?}"; : "${JUDGE_API_KEY:?}"
PRODUCT_JUDGE_MODEL="${PRODUCT_JUDGE_MODEL:-openai/gpt-4o-mini}"
export LLM_BASE_URL="$JUDGE_BASE_URL" LLM_MODEL="$PRODUCT_JUDGE_MODEL" LLM_API_KEY="$JUDGE_API_KEY"
export RAGAS_JUDGE_BASE_URL="$JUDGE_BASE_URL" RAGAS_JUDGE_MODEL="$PRODUCT_JUDGE_MODEL"
export RAGAS_JUDGE_API_KEY="$JUDGE_API_KEY" OPENAI_API_KEY="$JUDGE_API_KEY"
# ── endpoint truy hồi (mặc định production; override qua env cho candidate) ──
export EMBED_URL="${EMBED_URL:-$(getenv EMBEDDING_API_URL)}" EMBED_MODEL="${EMBED_MODEL:-$(getenv EMBEDDING_MODEL)}" EMBED_KEY="${EMBED_KEY:-$(getenv EMBEDDING_API_KEY)}"
export RERANK_URL="${RERANK_URL:-$(getenv RERANKER_API_URL)}" RERANK_MODEL="${RERANK_MODEL:-$(getenv RERANKER_MODEL)}" RERANK_KEY="${RERANK_KEY:-$(getenv RERANKER_API_KEY)}"
export QDRANT_URL="${QDRANT_URL:-http://localhost:6333}" QDRANT_COLLECTION="${QDRANT_COLLECTION:-$(getenv QDRANT_COLLECTION)}"
export BM25_LOCAL_MODEL_DIR="${BM25_LOCAL_MODEL_DIR:-/home/ict/AIEngines/secai/secai-data/_state/bm25_local_model}"
export FAITH_GOLD_CONTEXTS=1 RAGAS_SAMPLE="${RAGAS_SAMPLE:-2000}"
export PYTHONPATH="$LIB${PYTHONPATH:+:$PYTHONPATH}"

RAW="$OUT/raw"; mkdir -p "$RAW"
echo "[product-kpi] agent=$GW_BASE · judge=$PRODUCT_JUDGE_MODEL · embed=$EMBED_URL · qdrant=$QDRANT_URL/$QDRANT_COLLECTION ${LIMIT:+limit=$LIMIT}"

echo "[product-kpi] 1/6 answers qua FULL AGENT (/ai/chat, SAST inline mã)"
LIMARG=(); [ -n "$LIMIT" ] && LIMARG=(--limit "$LIMIT")
"$PY" "$LIB/gen_agent_answers.py" --dataset "$DATASET" --base "$GW_BASE" --out "$RAW/answers-all.jsonl" --workers 6 "${LIMARG[@]}"
# subset RAG-QA cho LLM-KB (bỏ trap + file); task_kpi dùng answers-all (đủ sast/exploit/remediation)
"$PY" - "$RAW/answers-all.jsonl" "$RAW/answers.jsonl" <<'PYEOF'
import json, sys
inp, out = sys.argv[1:3]; k = t = 0
with open(out, "w", encoding="utf-8") as o:
    for l in open(inp, encoding="utf-8"):
        if not l.strip(): continue
        a = json.loads(l); t += 1
        if "trap" in (a.get("tags") or []) or a.get("files"): continue
        o.write(l); k += 1
print(f"[product-kpi] RAG-QA subset (bỏ trap+file) = {k}/{t}")
PYEOF

echo "[product-kpi] 2/6 retrieval (Hit@10 + P@1) + contexts"
"$PY" "$GW/kpi_bench/scripts/retrieval_kpi.py" --dataset "$DATASET" --out "$RAW" --contexts "$RAW/contexts.jsonl" --workers 16 "${LIMARG[@]}"

echo "[product-kpi] 3/6 LLM-KB (entity/hallucination/relevance)"
"$PY" "$GW/kpi_bench/scripts/llm_kpi.py" --no-ragas --answers "$RAW/answers.jsonl" --contexts "$RAW/contexts.jsonl" --out "$RAW"

echo "[product-kpi] 4/6 faithfulness (gold-context)"
"$PY" "$GW/kpi_bench/scripts/faith_judge.py" "$RAW" "$RAW"

echo "[product-kpi] 5/6 task (SAST/Exploit/Remediation)"
"$PY" "$GW/kpi_bench/scripts/task_kpi.py" --answers "$RAW/answers-all.jsonl" --out "$RAW"

echo "[product-kpi] 6/6 adapt 4 report + chấm gate (9 check)"
"$PY" "$LIB/adapt_retrieval_report.py" --summary "$RAW/retrieval-summary.json" --out-dir "$OUT"
"$PY" "$LIB/adapt_llm_report.py" --llm-summary "$RAW/llm-kpi-summary.json" --faith-summary "$RAW/faithfulness-summary.json" --out "$OUT/llm.json"
"$PY" "$LIB/adapt_task_report.py" --task-summary "$RAW/task-summary.json" --out-dir "$OUT"
# SUFFICIENCY: chỉ chấm report có đủ mẫu (examples >= MIN_SAMPLES) — report mỏng bị BỎ -> check
# not_evaluated (DEFER), tránh false-trigger do 1 câu trượt trên mẫu bé (vd exploit n=5). Kỳ quá
# mỏng -> tất cả defer -> INSUFFICIENT_EVIDENCE -> không retrain.
MIN_SAMPLES="${MIN_SAMPLES:-15}"
REPORT_ARGS=()
for name in embedding reranker llm sast exploit remediation; do
  f="$OUT/$name.json"; [ -f "$f" ] || continue
  n="$("$PY" -c "import json;print(int(json.load(open('$f')).get('examples',0) or 0))")"
  if [ "$n" -ge "$MIN_SAMPLES" ]; then REPORT_ARGS+=(--report "$name=$f")
  else echo "[product-kpi] DEFER $name (examples=$n < MIN_SAMPLES=$MIN_SAMPLES) — không chấm"; fi
done
"$PY" "$PKG/finetune/scripts/check_finetune_thresholds.py" \
  --policy "$PKG/finetune/configs/finetune_thresholds_product.yml" \
  "${REPORT_ARGS[@]}" --output "$OUT/product-decision.json"
