#!/usr/bin/env bash
# Phase 1 — đo KPI TRUY HỒI qua pipeline SẢN PHẨM (hybrid dense+BM25 native Qdrant + id-aware +
# rerank), tái dùng gateway kpi_bench/scripts/retrieval_kpi.py, trỏ endpoint theo env. Xuất
# embedding.json + reranker.json (schema gate) rồi chấm với finetune_thresholds_product.yml.
#
# Usage: run_retrieval_kpi.sh <dataset_dir> <out_dir> [limit]
#   dataset_dir: thư mục chứa acceptance_*.jsonl ({id,input,capability,metadata.cve_id})
set -euo pipefail
DATASET="${1:?cần dataset_dir}"; OUT="${2:?cần out_dir}"; LIMIT="${3:-}"

PKG="/home/ict/AIEngines/secai/HO-SO-GOC-NGHIEM-THU-AI/02. Bo_build_finetuning"
GW="/home/ict/AIEngines/secai/secai-agent-gateway"
LIBDIR="$PKG/mlops/scripts/_lib/product_kpi"
PY="$PKG/mlops/.venv-eval/bin/python"
DOCKER_ENV="/home/ict/AIEngines/secai/secai-docker/.env"

# ── endpoint (mặc định = PRODUCTION đọc từ secai-docker/.env; có thể override qua env sẵn có) ──
getenv() { grep -E "^$1=" "$DOCKER_ENV" | head -1 | cut -d= -f2-; }
export EMBED_URL="${EMBED_URL:-$(getenv EMBEDDING_API_URL)}"
export EMBED_KEY="${EMBED_KEY:-$(getenv EMBEDDING_API_KEY)}"
export EMBED_MODEL="${EMBED_MODEL:-$(getenv EMBEDDING_MODEL)}"
export RERANK_URL="${RERANK_URL:-$(getenv RERANKER_API_URL)}"
export RERANK_KEY="${RERANK_KEY:-$(getenv RERANKER_API_KEY)}"
export RERANK_MODEL="${RERANK_MODEL:-$(getenv RERANKER_MODEL)}"
export QDRANT_URL="${QDRANT_URL:-http://localhost:6333}"
export QDRANT_COLLECTION="${QDRANT_COLLECTION:-$(getenv QDRANT_COLLECTION)}"
export BM25_LOCAL_MODEL_DIR="${BM25_LOCAL_MODEL_DIR:-/home/ict/AIEngines/secai/secai-data/_state/bm25_local_model}"
export PYTHONPATH="$LIBDIR${PYTHONPATH:+:$PYTHONPATH}"   # để retrieval_kpi.py `import config` thấy shim

mkdir -p "$OUT/raw"
echo "[product-kpi] embed=$EMBED_URL rerank=$RERANK_URL qdrant=$QDRANT_URL/$QDRANT_COLLECTION"
echo "[product-kpi] dataset=$DATASET ${LIMIT:+limit=$LIMIT}"

LIMARG=(); [ -n "$LIMIT" ] && LIMARG=(--limit "$LIMIT")
"$PY" "$GW/kpi_bench/scripts/retrieval_kpi.py" \
  --dataset "$DATASET" --out "$OUT/raw" --contexts "$OUT/raw/contexts.jsonl" \
  --workers 16 "${LIMARG[@]}"

echo "[product-kpi] adapt -> embedding.json + reranker.json"
"$PY" "$LIBDIR/adapt_retrieval_report.py" --summary "$OUT/raw/retrieval-summary.json" --out-dir "$OUT"

echo "[product-kpi] chấm gate (finetune_thresholds_product.yml)"
"$PY" "$PKG/finetune/scripts/check_finetune_thresholds.py" \
  --policy "$PKG/finetune/configs/finetune_thresholds_product.yml" \
  --report embedding="$OUT/embedding.json" --report reranker="$OUT/reranker.json" \
  --output "$OUT/product-decision.json"
