#!/usr/bin/env bash
# Phase 2 — đo KPI tầng LLM qua FULL RAG PIPELINE (agent secAI /ai/chat) + judge độc lập, neo trần
# HĐ ×0.95. Tái dùng kpi_bench llm_kpi.py (entity/hallucination/relevance) + faith_judge.py
# (faithfulness RAGAS-style, gold-context). Xuất llm.json (schema gate) rồi chấm policy product.
#
# Usage: run_llm_kpi.sh <dataset_dir> <out_dir> [gateway_base] [limit]
set -euo pipefail
DATASET="${1:?cần dataset_dir}"; OUT="${2:?cần out_dir}"; GW_BASE="${3:-http://localhost:8131}"; LIMIT="${4:-}"

PKG="/home/ict/AIEngines/secai/HO-SO-GOC-NGHIEM-THU-AI/02. Bo_build_finetuning"
GW="/home/ict/AIEngines/secai/secai-agent-gateway"
LIBDIR="$PKG/mlops/scripts/_lib/product_kpi"
PY="$PKG/mlops/.venv-eval/bin/python"
DOCKER_ENV="/home/ict/AIEngines/secai/secai-docker/.env"
MLOPS_ENV="$HOME/.config/secai-mlops/mlops.env"

getenv() { grep -E "^$1=" "$DOCKER_ENV" | head -1 | cut -d= -f2-; }
# ── judge độc lập (OpenRouter deepseek từ mlops.env) — KHÔNG phải model đang đo ──
set -a; . "$MLOPS_ENV"; set +a
: "${JUDGE_BASE_URL:?}"; : "${JUDGE_API_KEY:?}"
# Judge cho relevance/faithfulness PHẢI mạnh: deepseek-chat-v3.1 chấm relevance ERRATIC (0.0 oan
# ~35% câu tốt — calib 2026-09-20). gpt-4o-mini (judge hợp đồng gốc) ổn định, khớp nghiệm thu.
PRODUCT_JUDGE_MODEL="${PRODUCT_JUDGE_MODEL:-openai/gpt-4o-mini}"
export LLM_BASE_URL="$JUDGE_BASE_URL" LLM_MODEL="$PRODUCT_JUDGE_MODEL" LLM_API_KEY="$JUDGE_API_KEY"   # llm_kpi relevance
export RAGAS_JUDGE_BASE_URL="$JUDGE_BASE_URL" RAGAS_JUDGE_MODEL="$PRODUCT_JUDGE_MODEL"                # faith_judge
export RAGAS_JUDGE_API_KEY="$JUDGE_API_KEY" OPENAI_API_KEY="$JUDGE_API_KEY"
# ── endpoint truy hồi/embed (để llm_kpi grounding KB + faith gold-context) ──
export EMBED_URL="${EMBED_URL:-$(getenv EMBEDDING_API_URL)}" EMBED_MODEL="${EMBED_MODEL:-$(getenv EMBEDDING_MODEL)}"
export RERANK_URL="${RERANK_URL:-$(getenv RERANKER_API_URL)}" RERANK_MODEL="${RERANK_MODEL:-$(getenv RERANKER_MODEL)}"
export QDRANT_URL="${QDRANT_URL:-http://localhost:6333}" QDRANT_COLLECTION="${QDRANT_COLLECTION:-$(getenv QDRANT_COLLECTION)}"
export FAITH_GOLD_CONTEXTS=1                 # faithfulness dùng chunk gold thật (chuẩn RAGAS, bỏ nhiễu retrieval)
export RAGAS_SAMPLE="${RAGAS_SAMPLE:-1000}"  # không cắt mẫu (bộ drift nhỏ)
export PYTHONPATH="$LIBDIR${PYTHONPATH:+:$PYTHONPATH}"

RAW="$OUT/raw"; mkdir -p "$RAW"
echo "[llm-kpi] agent=$GW_BASE/ai/chat · judge=$JUDGE_MODEL · qdrant=$QDRANT_URL/$QDRANT_COLLECTION ${LIMIT:+limit=$LIMIT}"

echo "[llm-kpi] 1/4 sinh answers qua FULL AGENT (/ai/chat)"
LIMARG=(); [ -n "$LIMIT" ] && LIMARG=(--limit "$LIMIT")
"$PY" "$LIBDIR/gen_agent_answers.py" --dataset "$DATASET" --base "$GW_BASE" \
  --out "$RAW/answers-all.jsonl" --workers 6 "${LIMARG[@]}"
# LLM-KB metrics (faithfulness/relevance/entity/hallucination) đo trên RAG-QA: BỎ trap (test TỪ CHỐI,
# không phải relevance) + BỎ record cần FILE (SAST cần đính kèm qua /session — thuộc Phase 3, /ai/chat
# không nhận file). Không lọc -> relevance tụt oan (calib 2026-09-20: 0.66 vs 0.98 sau lọc).
"$PY" - "$RAW/answers-all.jsonl" "$RAW/answers.jsonl" <<'PYEOF'
import json, sys
inp, out = sys.argv[1:3]; k = t = 0
with open(out, "w", encoding="utf-8") as o:
    for l in open(inp, encoding="utf-8"):
        if not l.strip():
            continue
        a = json.loads(l); t += 1
        if "trap" in (a.get("tags") or []) or a.get("files"):
            continue
        o.write(l); k += 1
print(f"[llm-kpi] RAG-QA subset (bỏ trap+file) = {k}/{t}")
PYEOF

echo "[llm-kpi] 2/4 llm_kpi.py (entity / hallucination / relevance)"
"$PY" "$GW/kpi_bench/scripts/llm_kpi.py" --no-ragas \
  --answers "$RAW/answers.jsonl" --contexts "$RAW/contexts.jsonl" --out "$RAW"

echo "[llm-kpi] 3/4 faith_judge.py (faithfulness, gold-context)"
"$PY" "$GW/kpi_bench/scripts/faith_judge.py" "$RAW" "$RAW"

echo "[llm-kpi] 4/4 adapt -> llm.json + chấm gate"
"$PY" "$LIBDIR/adapt_llm_report.py" --llm-summary "$RAW/llm-kpi-summary.json" \
  --faith-summary "$RAW/faithfulness-summary.json" --out "$OUT/llm.json"
"$PY" "$PKG/finetune/scripts/check_finetune_thresholds.py" \
  --policy "$PKG/finetune/configs/finetune_thresholds_product.yml" \
  --report llm="$OUT/llm.json" --output "$OUT/llm-decision.json"
