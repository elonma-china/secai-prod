#!/usr/bin/env bash
# Giai đoạn 4 — Tự động Finetune (SFT): đọc `finetune_targets` từ finetune-decision.json (giai
# đoạn 3), chỉ train đúng component bị fail. GỌI NGUYÊN các script/notebook train đã có sẵn
# (train_llm.py, train_embedding.py, train_reranker.sh — không viết lại logic train; bản Colab
# tương đương: notebooks/Qwen35_Cyber_F1_Colab.ipynb, BGE_M3_Colab.ipynb, Qwen3_Reranker_Colab.ipynb).
#
# Trước khi train mỗi component: ghép CVE mới (Giai đoạn 2, $EVAL_DIR) vào ĐUÔI file train tĩnh
# (build_incremental_train_data.py) rồi trỏ train_*.py/.sh vào file ghép đó — nếu KHÔNG làm bước
# này, "Tự động Finetune" sẽ chỉ train LẠI trên đúng dữ liệu cũ, không dạy model biết thêm gì về
# chính các CVE mới vừa gây suy giảm (xem docstring build_incremental_train_data.py).
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FT="$ROOT/../../finetune"

if [ $# -lt 3 ]; then
  echo "usage: 04_auto_finetune.sh <finetune-decision.json> <output_base_dir> <eval_dir_giai_doan_2>" >&2
  exit 64
fi
DECISION="$1"; OUT_BASE="$2"; EVAL_DIR="$3"

TARGETS="$(python3 -c "import json; print(' '.join(json.load(open('$DECISION')).get('finetune_targets', [])))")"
DECISION_VALUE="$(python3 -c "import json; print(json.load(open('$DECISION'))['decision'])")"
if [ -z "$TARGETS" ]; then
  echo "[04_auto_finetune] finetune_targets rỗng (decision=$DECISION_VALUE) — không có gì để train"
  exit 0
fi
echo "[04_auto_finetune] decision=$DECISION_VALUE, targets cần finetune lại: $TARGETS"
mkdir -p "$OUT_BASE/incremental-data"

# NỐI TIẾP model ĐANG SERVING (không train lại từ base gốc) — nhất quán cả 3 component:
#   LLM: config qwen35_cyber_f1.json:base_model = DuyTa/Cyber-F1 (checkpoint trước quantize).
#   Embedding/Reranker: base = repo serving dưới đây (mặc định; override qua env nếu cần).
SERVING_EMBED_BASE="${SERVING_EMBED_BASE:-DuyTa/sec-embedding}"
SERVING_RERANK_BASE="${SERVING_RERANK_BASE:-DuyTa/sec-rerank}"

build_incremental() {
  local component="$1" static_train="$2" out_train="$3"
  python3 "$ROOT/_lib/build_incremental_train_data.py" --component "$component" \
    --eval-dir "$EVAL_DIR" --static-train "$static_train" --out-train "$out_train"
}

for target in $TARGETS; do
  case "$target" in
    llm)
      LLM_TRAIN="$OUT_BASE/incremental-data/llm_train.jsonl"
      build_incremental llm "$FT/data/llm/train.jsonl" "$LLM_TRAIN"
      echo "[04_auto_finetune] SFT LLM — PYTHONPATH=src python3 scripts/train_llm.py"
      ( cd "$FT" && PYTHONPATH=src python3 scripts/train_llm.py --output-dir "$OUT_BASE/llm-sft" \
          --dataset-path "$LLM_TRAIN" ${LLM_TRAIN_ARGS:-} )
      ;;
    embedding)
      EMBED_TRAIN="$OUT_BASE/incremental-data/embedding_train.jsonl"
      build_incremental embedding "$FT/data/embedding/train.jsonl" "$EMBED_TRAIN"
      echo "[04_auto_finetune] SFT embedding (nối tiếp serving: $SERVING_EMBED_BASE) — python3 scripts/train_embedding.py"
      ( cd "$FT" && python3 scripts/train_embedding.py --output-dir "$OUT_BASE/embedding" \
          --base-model "$SERVING_EMBED_BASE" --train "$EMBED_TRAIN" ${EMBED_TRAIN_ARGS:-} )
      ;;
    reranker)
      RERANK_TRAIN="$OUT_BASE/incremental-data/reranker_train.jsonl"
      build_incremental reranker "$FT/data/reranker/train.jsonl" "$RERANK_TRAIN"
      echo "[04_auto_finetune] fine-tune reranker (nối tiếp serving: $SERVING_RERANK_BASE) — bash scripts/train_reranker.sh (SWIFT)"
      ( cd "$FT" && MODEL="$SERVING_RERANK_BASE" OUT="$OUT_BASE/reranker" TRAIN="$RERANK_TRAIN" bash scripts/train_reranker.sh )
      ;;
    *)
      echo "[04_auto_finetune] target không rõ: '$target' — bỏ qua" >&2
      ;;
  esac
done
echo "[04_auto_finetune] xong -> $OUT_BASE"
