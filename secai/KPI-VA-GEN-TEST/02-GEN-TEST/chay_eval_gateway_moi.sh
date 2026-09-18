#!/bin/bash
# ============================================================================
# chay_eval_gateway_moi.sh — Chạy ĐỦ 11 KPI chất lượng trên MỘT BỘ ĐỀ BẤT KỲ,
# nhắm vào backend HIỆN ĐANG TRIỂN KHAI: secai-agent-gateway (FastAPI, Docker,
# container secai-docker-gateway, mặc định http://127.0.0.1:8131,
# POST /ai/chat/stream SSE với body {"message": "..."}) — KHÔNG PHẢI backend
# opencode cũ (:4096, /session). Bản song sinh cho backend cũ là
# chay_eval_bo_moi.sh (giữ nguyên, dùng khi ai đó dựng lại opencode).
#
# KHÁC BIỆT DUY NHẤT so với chay_eval_bo_moi.sh: bước "1. answer-gen" và 2 lượt
# SAST majority-2/3 gọi adapters/bench_answers_gateway.py (thay vì
# scripts/bench_answers_concurrent.py) — cùng cờ --dataset/--out/--workers/
# --limit hệt bản gốc, adapter chỉ đổi cách gọi HTTP sang gateway mới. Mọi
# bước còn lại (lam_answers.py, retrieval_kpi.py, llm_kpi.py, task_kpi.py,
# perf_kpi.py, sanitization_kpi.py, faith_judge.py, build_reports.py,
# make_deliverables.py, kiểm deliverable, quy ước KPI_DIR/.env.bench,
# quy ước runs/<ts>-evalindep) GIỮ NGUYÊN không đổi.
#
# LƯU Ý VỀ RATE LIMIT của gateway (phát hiện thực tế 2026-08-26): gateway tự
# giới hạn 15 request/60s MỖI IP CLIENT trên /ai/chat(/stream)
# (src/secai_gateway/api/middleware.py:route_limit, state/ratelimit.py) — đây
# là cơ chế an toàn thật của production vì gateway còn phục vụ traffic khác.
# adapters/bench_answers_gateway.py và adapters/perf_load_gateway.py đã tự
# giới tốc ~12-13 req/60s (sliding-window dùng chung giữa các worker thread)
# + retry-with-backoff khi vẫn dính 429. R-PERF-CCU (~30 luồng đồng thời) và
# R-PERF-RPS (~0.6 RPS = 36 req/phút) của Group B VƯỢT giới hạn 15 req/60s này
# theo cấu trúc — không thể vừa tuân thủ rate limit vừa đạt 2 mục tiêu đó từ
# một IP nguồn. Xem KET-QUA/2026-08-26/ trong repo này để biết ví dụ đầy đủ
# một lần chạy 19-KPI thật (12/19 đạt ngưỡng hợp đồng thật) và cách xử lý.
#
# Dùng:
#   ./chay_eval_gateway_moi.sh --dataset ./testset-v2 [--workers 2] [--limit N] [--no-faith]
#
# Yêu cầu: KPI-VA-GEN-TEST/.env.bench đã điền — bao gồm các biến gateway mới
#   ACC_BASE=http://127.0.0.1:8131  ACC_PROVIDER=secai-gateway
#   ACC_MODEL=DuyTa/Cyber-F1-AWQ    ACC_AGENT=chat
#   (không còn OPENCODE_SERVER_USERNAME/PASSWORD, không còn ACC_AGENT=build)
# Kết quả: KPI-VA-GEN-TEST/runs/<NGÀY-GIỜ>-evalindep/{raw-logs,reports,summary.md}
# (KPI_DIR mặc định = thư mục KPI-VA-GEN-TEST; export KPI_DIR=<path khác> để đổi.)
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
    -h|--help)      sed -n '2,39p' "$0"; exit 0 ;;
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

KPI_DIR="${KPI_DIR:-$HERE/..}"
cd "$KPI_DIR" || { echo "Không thấy thư mục kpi: $KPI_DIR"; exit 1; }
[ -f .env.bench ] || { echo "Thiếu $KPI_DIR/.env.bench"; exit 1; }
set -a; . .env.bench; set +a; export DATA_ROOT="${DATA_ROOT:-/opt/secai/data}"

TS=$(date +%Y-%m-%d-%H%M%S); RUN=runs/${TS}-evalindep; RAW=$RUN/raw-logs; REP=$RUN/reports
mkdir -p "$RAW" "$REP"; LOG="$RUN/run_eval_indep.log"
ln -sfn "$(basename "$RUN")" runs/latest   # xem log luôn qua runs/latest/run_eval_indep.log, không cần tìm timestamp
exec > >(tee -a "$LOG") 2>&1
echo "[$(date +%T)] ===== EVAL BỘ ĐỀ ĐỘC LẬP (gateway) · run=$RUN · ds=$DS · workers=$W $LIMARG ====="

echo "[$(date +%T)] 0b. preflight: SMOKE gateway (bench đi qua gateway :8131) + qdrant..."
GW="${GATEWAY_BASE:-http://localhost:8131}"
hc=$(curl -s -o /dev/null -w "%{http_code}" -m 10 "$GW/health" 2>/dev/null); echo "   gateway /health: HTTP $hc"
sc=$(curl -s -o /dev/null -w "%{http_code}" -m 60 -X POST "$GW/ai/chat" -H "Content-Type: application/json" -d '{"user_id":"preflight","message":"CVE-2021-44228 la gi? Tra loi ngan."}' 2>/dev/null); echo "   gateway /ai/chat smoke: HTTP $sc"
qc=$(curl -s -o /dev/null -w "%{http_code}" -m 8 "$QDRANT_URL/collections/$QDRANT_COLLECTION" 2>/dev/null); echo "   qdrant: HTTP $qc"
[ "$sc" = "200" ] || { echo "   X Gateway KHONG phan hoi (HTTP $sc) -> DUNG, khong bench."; exit 1; }
echo "   OK gateway phan hoi -> vao bench."

echo "[$(date +%T)] 1. answer-gen qua gateway (workers=$W)..."
python3 "$HERE/adapters/bench_answers_gateway.py" --dataset "$DS" --out "$RAW/results.jsonl" --workers "$W" $LIMARG || exit 1

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
  echo "[$(date +%T)]    SAST lượt $r (cho majority 2/3, qua gateway)..."
  python3 "$HERE/adapters/bench_answers_gateway.py" --dataset /tmp/eval_sast_indep --out "$RAW/results_sast_$r.jsonl" --workers 1
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

echo "[$(date +%T)] ===== XONG (gateway) → $KPI_DIR/$RUN (thiếu $miss deliverable) ====="
echo
echo "So sánh với baseline đã công bố:"
echo "  python3 $REPO/KPI-VA-GEN-TEST/01-KPI/compare_kpi.py \\"
echo "      --a $KPI_DIR/$RAW --b $REPO/kpi_results/quality/raw-logs \\"
echo "      --md bao-cao-so-sanh.md --xlsx bao-cao-so-sanh.xlsx"
