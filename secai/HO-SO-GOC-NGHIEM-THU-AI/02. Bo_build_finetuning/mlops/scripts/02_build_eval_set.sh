#!/usr/bin/env bash
# Giai đoạn 2 — Synthetic Data Generation (hoàn chỉnh, không phải wrapper sơ sài):
#   CSV CVE mới (bước 01) --> gen_testset.py --source customer --enrich --exclude-pool
#   --> validate không rỗng --> adapt_eval_set.py --> embedding.jsonl/reranker.jsonl/llm_test.jsonl
# đúng schema mà eval_embedding.py/eval_reranker.py/eval_llm_qa.py (gate chính thức) cần.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
GEN_TESTSET="$ROOT/../../../../KPI-VA-GEN-TEST/02-GEN-TEST/gen_testset.py"

if [ $# -lt 2 ]; then
  echo "usage: 02_build_eval_set.sh <cve_csv> <out_dir>" >&2
  exit 64
fi
CVE_CSV="$1"; OUT_DIR="$2"

[ -f "$GEN_TESTSET" ] || { echo "[02_build_eval_set] thiếu $GEN_TESTSET — kiểm tra repo" >&2; exit 1; }

read -r DEFAULT_QDRANT_URL DEFAULT_QDRANT_COLLECTION < <(python3 -c "
import sys
sys.path.insert(0, '$ROOT')
from _lib import common
c = common.load_config()['qdrant']
print(c['default_url'], c['default_collection'])
")
export QDRANT_URL="${QDRANT_URL:-$DEFAULT_QDRANT_URL}"
export QDRANT_COLLECTION="${QDRANT_COLLECTION:-$DEFAULT_QDRANT_COLLECTION}"

N=$(( $(wc -l < "$CVE_CSV") - 1 ))
if [ "$N" -le 0 ]; then
  echo "[02_build_eval_set] CSV rỗng (0 CVE) — không có gì để tổng hợp" >&2
  exit 2
fi
echo "[02_build_eval_set] $N CVE mới -> gen_testset.py --source customer --enrich --exclude-pool (Qdrant: $QDRANT_URL/$QDRANT_COLLECTION)"

mkdir -p "$OUT_DIR"
python3 "$GEN_TESTSET" --source customer --in "$CVE_CSV" --enrich --exclude-pool --out "$OUT_DIR"

TOTAL=0
for f in "$OUT_DIR"/acceptance_*.jsonl; do
  [ -f "$f" ] || continue
  TOTAL=$(( TOTAL + $(wc -l < "$f") ))
done
if [ "$TOTAL" -eq 0 ]; then
  echo "[02_build_eval_set] gen_testset.py không sinh được câu nào (0 CVE grounded trong KB) — dừng" >&2
  exit 2
fi
echo "[02_build_eval_set] $TOTAL câu tổng hợp"

python3 "$ROOT/_lib/adapt_eval_set.py" --gen-dir "$OUT_DIR" --out-dir "$OUT_DIR/adapted"
echo "[02_build_eval_set] xong -> $OUT_DIR/adapted/{embedding,reranker,llm_test}.jsonl (synthetic CVE mới)"
echo "[02_build_eval_set]   dùng cho: phát hiện suy giảm (stage 3, đo production) + train (stage 4/DPO)."
echo "[02_build_eval_set]   requalify/promote (stage 6c) chấm trên BENCHMARK cố định — KHÔNG dùng bộ này (chống leakage)."
