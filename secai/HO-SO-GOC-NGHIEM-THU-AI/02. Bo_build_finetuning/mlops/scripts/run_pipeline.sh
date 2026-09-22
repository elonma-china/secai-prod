#!/usr/bin/env bash
# Orchestrator — gọi tuần tự Giai đoạn 1 -> 7 (script 01..10). Vòng đời:
#   CVE mới (01) -> Synthetic Data Generation (02) -> Drift Detection trên production (03)
#   -> [nếu FINETUNE_REQUIRED] Auto Finetune/DPO (04,05) -> Quantize (06) -> Push HF candidate
#   (07) -> Requalify trên GPU thật (08,09) -> Promote hoặc Hold (10).
#
# Mặc định DRY-RUN: chỉ chạy 01->03 (đọc Qdrant + đo model đang serving — không tốn GPU-giờ,
# không rent vast.ai, không đụng HF/production). Cần --execute để train/push-candidate/rent-tạm
# thật; cần thêm --allow-deploy (tách riêng khỏi --execute) để bước 10 được ghi đè HF production
# và rent/redeploy instance production.
#   --mock: chạy ĐỦ 10 stage để kiểm ORCHESTRATION, KHÔNG tốn GPU/không đụng production (stage
#     GPU được stub, LLM dùng endpoint stand-in, stage 7 chỉ preview). Xem khối MOCK MODE bên dưới.
#
# Mỗi giai đoạn ghi log riêng vào runs/<id>/logs/<NN>.log và cập nhật runs/<id>/status.json qua
# _lib/pipeline_status.py — để trigger_api + trang nghiệm thu hiển thị tiến độ/log THẬT theo từng
# giai đoạn (không giả lập).
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MLOPS_ROOT="$ROOT/.."

# Stage 1-3 cần eval venv (sentence-transformers/transformers) — tự thêm vào PATH nếu có, để
# chạy được cả dưới systemd timer lẫn trigger_api (không phụ thuộc shell tương tác đã activate).
if [ -x "$MLOPS_ROOT/.venv-eval/bin/python3" ]; then
  PATH="$MLOPS_ROOT/.venv-eval/bin:$PATH"
  export PATH
fi

PSTATUS() { python3 "$ROOT/_lib/pipeline_status.py" "$@"; }
# Ghi log giai đoạn ĐÃ che secret (API key serving/Bearer/hf_/sk- token — vd bước 06b/07 in ra
# key serving) trước khi xuống đĩa + ra trigger_api.log. Đặt SAU stage trong pipe nên
# ${PIPESTATUS[0]} vẫn là exit code của stage.
tee_stage() { python3 "$ROOT/_lib/redact.py" | tee "$@"; }  # "$@" để hỗ trợ cả `tee_stage -a file`

EXECUTE=0; ALLOW_DEPLOY=0; RUN_ID=""; MOCK=0
while [ $# -gt 0 ]; do
  case "$1" in
    --execute)      EXECUTE=1; shift;;
    --allow-deploy) ALLOW_DEPLOY=1; shift;;
    --mock)         MOCK=1; EXECUTE=1; shift;;
    --run-id)       RUN_ID="$2"; shift 2;;
    -h|--help) sed -n '2,12p' "$0"; exit 0;;
    *) echo "tham số lạ: $1 (chấp nhận --execute, --allow-deploy, --mock, --run-id <id>)" >&2; exit 64;;
  esac
done

# ── MOCK MODE ─────────────────────────────────────────────────────────────────
# --mock: chạy ĐỦ 10 stage để kiểm ORCHESTRATION end-to-end mà KHÔNG tốn GPU/không đụng production:
#   stage 1-2-3 chạy THẬT (LLM-under-test = endpoint stand-in), stage 4/4b/5 STUB (tạo artifact
#   giả, không train), stage 6 stub push (chỉ log), stage 6b stub rent (ghi endpoint stand-in),
#   stage 6c requalify THẬT trên SUBSET nhỏ của benchmark (LLM stand-in), stage 7 promote/hold ở
#   chế độ PREVIEW (không --allow-deploy → không ghi HF prod / không redeploy gateway).
# Endpoint stand-in mặc định = OpenRouter (JUDGE_*); đưa MOCK_LLM_* nếu muốn tách model-under-test.
if [ "$MOCK" = "1" ]; then
  ALLOW_DEPLOY=0   # mock KHÔNG bao giờ deploy thật
  MOCK_LLM_URL="${MOCK_LLM_URL:-${JUDGE_BASE_URL:-https://openrouter.ai/api/v1}}"
  MOCK_LLM_MODEL="${MOCK_LLM_MODEL:-deepseek/deepseek-v3.2}"  # under-test ≠ judge (deepseek-chat-v3.1)
  MOCK_LLM_KEY="${MOCK_LLM_KEY:-${JUDGE_API_KEY:-}}"
  MOCK_BENCH_N="${MOCK_BENCH_N:-8}"          # số dòng benchmark lấy cho requalify (nhỏ cho nhanh)
  MOCK_CVE_N="${MOCK_CVE_N:-8}"              # số CVE mẫu cho stage 1
  # shellcheck source=_lib/gate_eval_common.sh
  source "$ROOT/_lib/gate_eval_common.sh"   # cần run_gate_eval cho mock stage 3 (chạy dưới bash: BASH_SOURCE OK)
fi

RUN_STARTED_AT="$(python3 -c 'import datetime; print(datetime.datetime.now(datetime.timezone.utc).isoformat())')"
[ -n "$RUN_ID" ] || RUN_ID="$(date -u +%Y%m%d-%H%M%S)"
RUN_DIR="$MLOPS_ROOT/runs/$RUN_ID"
LOG_DIR="$RUN_DIR/logs"
STATUS="$RUN_DIR/status.json"
mkdir -p "$LOG_DIR"

MODE="dry"; [ "$EXECUTE" = "1" ] && MODE="execute"; [ "$ALLOW_DEPLOY" = "1" ] && MODE="deploy"; [ "$MOCK" = "1" ] && MODE="mock"
PSTATUS init --file "$STATUS" --run-id "$RUN_ID" --mode "$MODE" --stages \
  "01:Thu thập CVE mới|02:Tổng hợp bộ eval (Synthetic Data)|03:Phát hiện suy giảm (production)|04:Tự động Finetune (SFT)|04b:DPO (LLM)|05:Quantize AWQ|06:Push candidate lên HF|06b:Requalify - thuê GPU candidate|06c:Requalify - drift-check candidate|07:Promote hoặc Hold"

echo "[run_pipeline] run_id=$RUN_ID  execute=$EXECUTE  allow_deploy=$ALLOW_DEPLOY  dir=$RUN_DIR"

# Đánh dấu các stage sẽ không chạy ở chế độ dry là "pending" -> để nguyên; khi kết thúc sớm sẽ
# set skipped cho phần còn lại qua skip_rest().
skip_rest() {
  # đánh dấu skipped cho mọi stage còn pending
  python3 - "$STATUS" <<'PY'
import json, sys
p = sys.argv[1]
d = json.load(open(p, encoding="utf-8"))
for s in d["stages"]:
    if s["state"] == "pending":
        s["state"] = "skipped"
open(p, "w", encoding="utf-8").write(json.dumps(d, ensure_ascii=False, indent=2) + "\n")
PY
}

fail_and_exit() {  # <stage_id> <rc> <message>
  local sid="$1" rc="$2" msg="$3"
  PSTATUS set --file "$STATUS" --stage "$sid" --state failed --exit-code "$rc" --note "$msg"
  skip_rest
  PSTATUS overall --file "$STATUS" --state failed
  echo "[run_pipeline] LỖI ở Giai đoạn $sid (rc=$rc): $msg" >&2
  exit "$rc"
}

finish_ok() {  # kết thúc sớm nhưng KHÔNG phải lỗi (dry-run dừng, không đủ CVE, không cần finetune)
  skip_rest
  PSTATUS overall --file "$STATUS" --state done
  exit 0
}

# ── Giai đoạn 1: CVE mới kể từ lần finetune trước ──────────────────────────────
PSTATUS set --file "$STATUS" --stage 01 --state running
if [ "$MOCK" = "1" ]; then
  # Mock: lấy mẫu CVE THẬT từ Qdrant với watermark 30 ngày gần đây rồi cap $MOCK_CVE_N dòng (đủ để
  # chạy 2->6c, không cần cả nghìn). Không phụ thuộc watermark trong state.
  {
    WM="$(python3 -c 'import datetime; print((datetime.datetime.now(datetime.timezone.utc)-datetime.timedelta(days=30)).isoformat())')"
    python3 "$ROOT/01_collect_new_cves.py" --out "$RUN_DIR/new_cves_full.csv" --watermark "$WM" || true
    { head -1 "$RUN_DIR/new_cves_full.csv"; tail -n +2 "$RUN_DIR/new_cves_full.csv" | head -"$MOCK_CVE_N"; } > "$RUN_DIR/new_cves.csv"
    echo "[run_pipeline][MOCK] lấy $(( $(wc -l < "$RUN_DIR/new_cves.csv") - 1 )) CVE mẫu (watermark $WM)"
  } 2>&1 | tee_stage "$LOG_DIR/01.log"
  RC=0
  [ "$(( $(wc -l < "$RUN_DIR/new_cves.csv") - 1 ))" -ge 1 ] || fail_and_exit 01 1 "MOCK: không lấy được CVE mẫu nào từ Qdrant"
else
python3 "$ROOT/01_collect_new_cves.py" --out "$RUN_DIR/new_cves.csv" 2>&1 | tee_stage "$LOG_DIR/01.log"
RC=${PIPESTATUS[0]}
fi
if [ "$RC" = "2" ]; then
  PSTATUS set --file "$STATUS" --stage 01 --state done --exit-code 2 --note "không đủ CVE mới — dừng chu kỳ (bình thường)"
  echo "[run_pipeline] không đủ CVE mới — dừng chu kỳ này (bình thường, không phải lỗi)"
  finish_ok
elif [ "$RC" != "0" ]; then
  fail_and_exit 01 "$RC" "01_collect_new_cves.py"
fi
PSTATUS set --file "$STATUS" --stage 01 --state done --exit-code 0

# ── Giai đoạn 2: Synthetic Data Generation ─────────────────────────────────────
PSTATUS set --file "$STATUS" --stage 02 --state running
bash "$ROOT/02_build_eval_set.sh" "$RUN_DIR/new_cves.csv" "$RUN_DIR/genset" 2>&1 | tee_stage "$LOG_DIR/02.log"
RC=${PIPESTATUS[0]}
if [ "$RC" = "2" ]; then
  PSTATUS set --file "$STATUS" --stage 02 --state done --exit-code 2 --note "gen_testset.py không sinh câu nào — dừng chu kỳ"
  echo "[run_pipeline] gen_testset.py không sinh được câu nào — dừng chu kỳ này"
  finish_ok
elif [ "$RC" != "0" ]; then
  fail_and_exit 02 "$RC" "02_build_eval_set.sh"
fi
PSTATUS set --file "$STATUS" --stage 02 --state done --exit-code 0
# Bộ synthetic CVE mới: dùng cho PHÁT HIỆN SUY GIẢM (stage 3, đo production — production chưa train
# trên nó) + BỔ SUNG TRAIN (stage 4/DPO). KHÔNG dùng để requalify/gate (stage 6c chấm trên
# BENCHMARK cố định — candidate không train trên đó → chống rò rỉ train/test + bắt regression).
EVAL_DIR="$RUN_DIR/genset/adapted"

# ── Giai đoạn 3: Phát hiện suy giảm (đo model đang serving) ────────────────────
PSTATUS set --file "$STATUS" --stage 03 --state running
if [ "$MOCK" = "1" ]; then
  # Mock: đo bằng LLM-under-test stand-in (production thật có thể đang down). Chạy run_gate_eval
  # THẬT (embed/rerank tải về + eval local, LLM predictions qua stand-in, judge thật) rồi ÉP
  # FINETUNE_REQUIRED để demo tiếp 4->10.
  {
    run_gate_eval "$EVAL_DIR" "$RUN_DIR/report-production" \
      "DuyTa/sec-embedding" "DuyTa/sec-rerank" \
      "$MOCK_LLM_URL" "$MOCK_LLM_KEY" "$MOCK_LLM_MODEL" || true
    python3 -c "
import json, pathlib
p = pathlib.Path('$RUN_DIR/report-production/finetune-decision.json')
d = json.loads(p.read_text()) if p.exists() else {'schema_version':1,'checks':[]}
d['decision'] = 'FINETUNE_REQUIRED'; d['finetune_targets'] = ['embedding','reranker','llm']
d['mock_forced'] = True
p.write_text(json.dumps(d, ensure_ascii=False, indent=2))
print('[run_pipeline][MOCK] ép decision=FINETUNE_REQUIRED (embedding,reranker,llm) để demo 4->10')
"
  } 2>&1 | tee_stage "$LOG_DIR/03.log"
  RC=0
elif [ "${PRODUCT_GATE:-1}" = "1" ]; then
# PRODUCT-KPI gate (mặc định): đo production qua FULL PIPELINE trên bộ CVE mới (raw genset), neo
# TRẦN KPI HỢP ĐỒNG ×0.95. Đúng thang hợp đồng (retrieval hybrid id-aware + LLM-RAG + task SAST/
# exploit/remediation). Đặt PRODUCT_GATE=0 để quay lại 03 cũ (standalone dense-only/bare).
bash "$ROOT/03_run_drift_check_product.sh" "$RUN_DIR/genset" "$RUN_DIR/report-production" "${GATEWAY_BASE:-http://localhost:8131}" 2>&1 | tee_stage "$LOG_DIR/03.log"
RC=${PIPESTATUS[0]}
else
bash "$ROOT/03_run_drift_check.sh" "$EVAL_DIR" "$RUN_DIR/report-production" 2>&1 | tee_stage "$LOG_DIR/03.log"
RC=${PIPESTATUS[0]}
fi
[ "$RC" = "0" ] || fail_and_exit 03 "$RC" "03_run_drift_check.sh"
DECISION_VALUE="$(python3 -c "import json;print(json.load(open('$RUN_DIR/report-production/finetune-decision.json'))['decision'])")"
PSTATUS set --file "$STATUS" --stage 03 --state done --exit-code 0 --note "decision=$DECISION_VALUE"
echo "[run_pipeline] Giai đoạn 3 xong: decision=$DECISION_VALUE"

if [ "$DECISION_VALUE" != "FINETUNE_REQUIRED" ]; then
  echo "[run_pipeline] model đang serving vẫn đạt ngưỡng trên CVE mới ($DECISION_VALUE) — KHÔNG cần finetune, giữ nguyên serving. Kết thúc chu kỳ."
  finish_ok
fi
if [ "$EXECUTE" != "1" ]; then
  echo "[run_pipeline] decision=FINETUNE_REQUIRED nhưng thiếu --execute — DRY-RUN dừng ở đây."
  echo "[run_pipeline] chạy lại với --execute để: train (04,05) -> quantize (06) -> push candidate (07) -> requalify trên GPU thật (08,09) -> promote/hold (10)."
  finish_ok
fi

# ── Giai đoạn 4: Tự động Finetune (SFT) ────────────────────────────────────────
PSTATUS set --file "$STATUS" --stage 04 --state running
if [ "$MOCK" = "1" ]; then
  {
    echo "[run_pipeline][MOCK] STUB SFT — không train GPU; tạo artifact giả cho embedding/reranker/llm-sft"
    for d in embedding reranker llm-sft; do
      mkdir -p "$RUN_DIR/finetune-out/$d"; echo "MOCK adapter ($d) $(date -u +%FT%TZ)" > "$RUN_DIR/finetune-out/$d/MOCK_ARTIFACT.txt"
    done
  } 2>&1 | tee_stage "$LOG_DIR/04.log"
  RC=0
else
bash "$ROOT/04_auto_finetune.sh" "$RUN_DIR/report-production/finetune-decision.json" "$RUN_DIR/finetune-out" "$EVAL_DIR" 2>&1 | tee_stage "$LOG_DIR/04.log"
RC=${PIPESTATUS[0]}
fi
[ "$RC" = "0" ] || fail_and_exit 04 "$RC" "04_auto_finetune.sh"
PSTATUS set --file "$STATUS" --stage 04 --state done --exit-code 0

TARGETS="$(python3 -c "import json;print(' '.join(json.load(open('$RUN_DIR/report-production/finetune-decision.json'))['finetune_targets']))")"
declare -A ARTIFACT_DIR

# ── Giai đoạn 4b: DPO tiếp trên checkpoint SFT (chỉ khi có target llm) ──────────
if printf '%s\n' $TARGETS | grep -qx llm; then
  PSTATUS set --file "$STATUS" --stage 04b --state running
  LLM_ADAPTER_DIR="$RUN_DIR/finetune-out/llm-sft"
  if [ "$MOCK" = "1" ]; then
    { echo "[run_pipeline][MOCK] STUB DPO + Quantize — không train/quantize GPU; tạo AWQ giả"
      mkdir -p "$RUN_DIR/finetune-out/llm-dpo/model" "$RUN_DIR/quant-out/awq"
      echo "MOCK DPO" > "$RUN_DIR/finetune-out/llm-dpo/model/MOCK_ARTIFACT.txt"
      echo "MOCK AWQ" > "$RUN_DIR/quant-out/awq/MOCK_ARTIFACT.txt"
    } 2>&1 | tee_stage "$LOG_DIR/04b.log"
    cp "$LOG_DIR/04b.log" "$LOG_DIR/05.log" 2>/dev/null || true
    PSTATUS set --file "$STATUS" --stage 04b --state done --exit-code 0 --note "MOCK stub"
    PSTATUS set --file "$STATUS" --stage 05 --state done --exit-code 0 --note "MOCK stub"
    ARTIFACT_DIR[llm]="$RUN_DIR/quant-out/awq"
  else
  # DPO xây cặp từ report drift production (stage 3, trên synthetic CVE mới): chosen = gold, rejected
  # = câu production trả SAI. Không rò rỉ vào gate vì requalify (6c) chấm trên BENCHMARK riêng.
  python3 "$ROOT/05_dpo_llm.py" \
    --llm-qa-report "$RUN_DIR/report-production/llm-qa.json" \
    --test "$EVAL_DIR/llm_test.jsonl" \
    --base-model "$LLM_ADAPTER_DIR" \
    --output-dir "$RUN_DIR/finetune-out/llm-dpo" 2>&1 | tee_stage "$LOG_DIR/04b.log"
  DPO_RC=${PIPESTATUS[0]}
  if [ "$DPO_RC" = "0" ]; then
    echo "[run_pipeline] DPO chạy xong -> dùng checkpoint DPO làm bản LLM cuối"
    LLM_ADAPTER_DIR="$RUN_DIR/finetune-out/llm-dpo/model"
    PSTATUS set --file "$STATUS" --stage 04b --state done --exit-code 0
  elif [ "$DPO_RC" = "2" ]; then
    echo "[run_pipeline] không đủ cặp preference cho DPO — dùng thẳng checkpoint SFT"
    PSTATUS set --file "$STATUS" --stage 04b --state done --exit-code 2 --note "không đủ cặp preference — dùng SFT"
  else
    fail_and_exit 04b "$DPO_RC" "05_dpo_llm.py"
  fi

  # ── Giai đoạn 5: Quantize AWQ ────────────────────────────────────────────────
  PSTATUS set --file "$STATUS" --stage 05 --state running
  bash "$ROOT/06_quantize_candidate.sh" "$LLM_ADAPTER_DIR" "$RUN_DIR/quant-out" 2>&1 | tee_stage "$LOG_DIR/05.log"
  RC=${PIPESTATUS[0]}
  [ "$RC" = "0" ] || fail_and_exit 05 "$RC" "06_quantize_candidate.sh"
  PSTATUS set --file "$STATUS" --stage 05 --state done --exit-code 0
  ARTIFACT_DIR[llm]="$RUN_DIR/quant-out/awq"
  fi
else
  PSTATUS set --file "$STATUS" --stage 04b --state skipped --note "không có target llm"
  PSTATUS set --file "$STATUS" --stage 05 --state skipped --note "không có target llm"
fi
for target in $TARGETS; do
  case "$target" in
    embedding) ARTIFACT_DIR[embedding]="$RUN_DIR/finetune-out/embedding";;
    reranker)  ARTIFACT_DIR[reranker]="$RUN_DIR/finetune-out/reranker";;
  esac
done

# ── Giai đoạn 6: Push candidate lên HF ──────────────────────────────────────────
# MANIFEST khai Ở SHELL CHA (không phải trong khối `{ } | tee_stage` — pipe tạo subshell nên biến
# gán bên trong sẽ KHÔNG thấy được ở stage 07). Stage 07 (promote) cần "$MANIFEST".
MANIFEST="$RUN_DIR/artifacts-manifest.json"
MANIFEST_KV="$RUN_DIR/artifacts-manifest.kv"
PSTATUS set --file "$STATUS" --stage 06 --state running
{
  : > "$MANIFEST_KV"
  for comp in "${!ARTIFACT_DIR[@]}"; do
    echo "$comp=${ARTIFACT_DIR[$comp]}" >> "$MANIFEST_KV"
  done
  python3 -c "
import json
d = {}
with open('$MANIFEST_KV') as f:
    for line in f:
        line = line.strip()
        if line:
            k, v = line.split('=', 1)
            d[k] = v
json.dump(d, open('$MANIFEST', 'w'), ensure_ascii=False, indent=2)
"
  for comp in "${!ARTIFACT_DIR[@]}"; do
    if [ "$MOCK" = "1" ]; then
      echo "[run_pipeline][MOCK] STUB push HF — sẽ đẩy '$comp' (${ARTIFACT_DIR[$comp]}) lên repo candidate (bỏ qua trong mock)"
    else
      python3 "$ROOT/07_push_candidate_to_hf.py" --local-dir "${ARTIFACT_DIR[$comp]}" --component "$comp" --target candidate
    fi
  done
} 2>&1 | tee_stage "$LOG_DIR/06.log"
RC=${PIPESTATUS[0]}
[ "$RC" = "0" ] || fail_and_exit 06 "$RC" "07_push_candidate_to_hf.py"
PSTATUS set --file "$STATUS" --stage 06 --state done --exit-code 0

# ── Giai đoạn 6b/6c: Requalify trên GPU thật ────────────────────────────────────
PSTATUS set --file "$STATUS" --stage 06b --state running
if [ "$MOCK" = "1" ]; then
  {
    echo "[run_pipeline][MOCK] STUB rent — không thuê vast; ghi endpoint stand-in ($MOCK_LLM_MODEL @ $MOCK_LLM_URL)"
    python3 -c "
import json
json.dump({'instance_id':'mock','llm_url':'$MOCK_LLM_URL','embed_url':'mock','rerank_url':'mock',
          'api_key':'$MOCK_LLM_KEY','llm_model':'$MOCK_LLM_MODEL',
          'embed_model':'DuyTa/sec-embedding','rerank_model':'DuyTa/sec-rerank',
          'label':'mock','down_script':'/bin/echo'},
         open('$RUN_DIR/endpoint-candidate.json','w'), ensure_ascii=False, indent=2)
"
  } 2>&1 | tee_stage "$LOG_DIR/06b.log"
  RC=0
else
bash "$ROOT/08_rent_candidate_serving.sh" "$RUN_DIR/report-production/finetune-decision.json" "$RUN_DIR/endpoint-candidate.json" 2>&1 | tee_stage "$LOG_DIR/06b.log"
RC=${PIPESTATUS[0]}
fi
if [ "$RC" != "0" ]; then
  PSTATUS set --file "$STATUS" --stage 06b --state failed --exit-code "$RC" --note "instance candidate có thể vẫn TÍNH TIỀN, kiểm tra tay"
  skip_rest; PSTATUS overall --file "$STATUS" --state failed
  echo "[run_pipeline] LỖI ở Giai đoạn 6b (rc=$RC) — instance candidate có thể vẫn đang TÍNH TIỀN, kiểm tra tay" >&2
  exit "$RC"
fi
PSTATUS set --file "$STATUS" --stage 06b --state done --exit-code 0

PSTATUS set --file "$STATUS" --stage 06c --state running
# Requalify trên BENCHMARK CỐ ĐỊNH (không phải synthetic CVE mới): candidate KHÔNG train trên đó
# → đo tổng quát hoá thật + bắt regression, không rò rỉ train/test. Dựng thư mục benchmark-eval
# với đúng 3 tên file mà run_gate_eval cần, trỏ tới bộ test cố định khai trong pipeline.yml.
BENCH_DIR="$RUN_DIR/benchmark-eval"
mkdir -p "$BENCH_DIR"
MOCK_BENCH_N_ARG=0; [ "$MOCK" = "1" ] && MOCK_BENCH_N_ARG="$MOCK_BENCH_N"
python3 -c "
import sys, os
sys.path.insert(0, '$ROOT')
from _lib import common
b = common.load_config()['benchmark']
pairs = {'embedding.jsonl': b['embedding_test'], 'reranker.jsonl': b['reranker_test'], 'llm_test.jsonl': b['llm_test']}
n = int('$MOCK_BENCH_N_ARG')  # >0 = mock: lấy subset n dòng đầu; 0 = symlink full
for name, rel in pairs.items():
    src = str(common.resolve_path(rel)); dst = os.path.join('$BENCH_DIR', name)
    if not os.path.isfile(src): raise SystemExit(f'thiếu benchmark: {src}')
    if os.path.islink(dst) or os.path.exists(dst): os.remove(dst)
    if n > 0:
        with open(src, encoding='utf-8') as f: head = [l for _, l in zip(range(n), f)]
        open(dst, 'w', encoding='utf-8').writelines(head)
    else:
        os.symlink(src, dst)
print('[run_pipeline] benchmark-eval -> ' + ', '.join(pairs) + (f' (MOCK subset {n} dòng)' if n>0 else ' (full)'))
" 2>&1 | tee_stage "$LOG_DIR/06c.log"
if [ "${PIPESTATUS[0]}" != "0" ]; then fail_and_exit 06c 1 "dựng benchmark-eval thất bại (thiếu file benchmark?)"; fi
if [ "$MOCK" != "1" ] && [ "${PRODUCT_GATE:-1}" = "1" ]; then
  # Requalify PRODUCT-KPI trên BENCHMARK ACCEPTANCE cố định (leak-free: candidate KHÔNG train trên
  # bộ hand-curated này), đo candidate qua FULL PIPELINE, neo TRẦN KPI HỢP ĐỒNG. Cần gateway trỏ
  # LLM candidate (CANDIDATE_GATEWAY_BASE) cho /ai/chat full-RAG. Đặt PRODUCT_GATE=0 để dùng
  # requalify benchmark cũ (09_run_drift_check_candidate.sh, standalone — không cần candidate gateway).
  ACC_BENCH="${ACCEPTANCE_BENCHMARK_DIR:-$(cd "$ROOT/../../../03. Kpi_results/Model/Datasets" 2>/dev/null && pwd || true)}"
  : "${CANDIDATE_GATEWAY_BASE:?PRODUCT_GATE requalify cần CANDIDATE_GATEWAY_BASE (gateway /ai/chat trỏ LLM candidate). Hoặc đặt PRODUCT_GATE=0 dùng requalify benchmark cũ.}"
  [ -d "$ACC_BENCH" ] || fail_and_exit 06c 1 "không thấy ACCEPTANCE_BENCHMARK_DIR ($ACC_BENCH) cho requalify product-KPI"
  bash "$ROOT/09_run_drift_check_candidate_product.sh" "$ACC_BENCH" "$RUN_DIR/report-candidate" "$RUN_DIR/endpoint-candidate.json" "$CANDIDATE_GATEWAY_BASE" 2>&1 | tee_stage -a "$LOG_DIR/06c.log"
  RC=${PIPESTATUS[0]}
else
  bash "$ROOT/09_run_drift_check_candidate.sh" "$BENCH_DIR" "$RUN_DIR/report-candidate" "$RUN_DIR/endpoint-candidate.json" 2>&1 | tee_stage -a "$LOG_DIR/06c.log"
  RC=${PIPESTATUS[0]}
fi
if [ "$RC" != "0" ]; then
  echo "[run_pipeline] LỖI ở Giai đoạn 6c (rc=$RC) — không đo được candidate, coi như KHÔNG đạt (an toàn: không promote khi thiếu bằng chứng)" >&2
  DOWN_SCRIPT="$(python3 -c "import json;print(json.load(open('$RUN_DIR/endpoint-candidate.json'))['down_script'])")"
  INSTANCE_ID="$(python3 -c "import json;print(json.load(open('$RUN_DIR/endpoint-candidate.json'))['instance_id'])")"
  "$DOWN_SCRIPT" "$INSTANCE_ID" || echo "[run_pipeline] CẢNH BÁO: huỷ instance $INSTANCE_ID thất bại — huỷ tay: $DOWN_SCRIPT $INSTANCE_ID" >&2
  PSTATUS set --file "$STATUS" --stage 06c --state failed --exit-code "$RC" --note "không đo được candidate — đã huỷ instance, không promote"
  skip_rest; PSTATUS overall --file "$STATUS" --state failed
  exit "$RC"
fi
PSTATUS set --file "$STATUS" --stage 06c --state done --exit-code 0

# ── Giai đoạn 7: Promote hoặc Hold ──────────────────────────────────────────────
PSTATUS set --file "$STATUS" --stage 07 --state running
PROMOTE_ARGS=("$RUN_DIR/report-candidate/finetune-decision.json" "$RUN_DIR/endpoint-candidate.json" "$MANIFEST" "$RUN_STARTED_AT")
[ "$ALLOW_DEPLOY" = "1" ] && PROMOTE_ARGS+=(--allow-deploy)
bash "$ROOT/10_promote_or_hold.sh" "${PROMOTE_ARGS[@]}" 2>&1 | tee_stage "$LOG_DIR/07.log"
RC=${PIPESTATUS[0]}
echo "[run_pipeline] xong, run_id=$RUN_ID, 10_promote_or_hold.sh rc=$RC (3=hold/giữ nguyên serving, đúng thiết kế — không phải lỗi)"
if [ "$RC" = "3" ]; then
  PSTATUS set --file "$STATUS" --stage 07 --state done --exit-code 3 --note "HOLD — candidate không đạt, giữ nguyên serving hiện tại"
  PSTATUS overall --file "$STATUS" --state done
  exit 0
elif [ "$RC" = "0" ]; then
  PSTATUS set --file "$STATUS" --stage 07 --state done --exit-code 0 --note "PROMOTE — model mới đã deploy"
  PSTATUS overall --file "$STATUS" --state done
  exit 0
else
  fail_and_exit 07 "$RC" "10_promote_or_hold.sh"
fi
