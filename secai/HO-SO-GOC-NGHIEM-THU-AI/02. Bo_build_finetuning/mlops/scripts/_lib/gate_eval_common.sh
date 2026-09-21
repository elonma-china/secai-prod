#!/usr/bin/env bash
# Hàm dùng chung giữa 03_run_drift_check.sh (đo model PRODUCTION) và
# 09_run_drift_check_candidate.sh (đo model CANDIDATE) — tránh chép lại logic gọi
# eval_embedding.py/eval_reranker.py/eval_llm_qa.py/check_finetune_thresholds.py hai lần.
#
# Nguồn: dùng "source" từ script gọi, không tự chạy độc lập.
LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FT_FOR_GATE_EVAL="$LIB_DIR/../../../finetune"

# Chỉ chạy LLM predictions + judge trên 1 tập llm_test -> <report_dir>/llm-qa.json. Tách riêng để
# stage 4b (DPO) xây được report trên TẬP TRAIN (không phải eval) — tránh rò rỉ train/test.
run_llm_report() {
  local eval_dir="$1" report_dir="$2" llm_url="$3" llm_key="$4" llm_model="$5"
  : "${JUDGE_BASE_URL:?cần export JUDGE_BASE_URL}"; : "${JUDGE_MODEL:?cần export JUDGE_MODEL}"
  : "${JUDGE_API_KEY:?cần export JUDGE_API_KEY}"
  [ -n "$llm_url" ] || { echo "[gate_eval] thiếu LLM URL" >&2; return 1; }
  mkdir -p "$report_dir"
  echo "[gate_eval] gọi LLM ($llm_model @ $llm_url) lấy predictions -> $report_dir"
  python3 "$LIB_DIR/call_llm_predictions.py" --test "$eval_dir/llm_test.jsonl" \
    --llm-url "$llm_url" --llm-model "$llm_model" --api-key "$llm_key" \
    --output "$report_dir/llm-predictions.jsonl"
  echo "[gate_eval] eval LLM QA (judge độc lập: $JUDGE_MODEL)"
  JUDGE_API_KEY="$JUDGE_API_KEY" python3 "$FT_FOR_GATE_EVAL/scripts/eval_llm_qa.py" \
    --predictions "$report_dir/llm-predictions.jsonl" \
    --test "$eval_dir/llm_test.jsonl" --judge-url "$JUDGE_BASE_URL" --judge-model "$JUDGE_MODEL" \
    --api-key-env JUDGE_API_KEY --output "$report_dir/llm-qa.json"
}

run_gate_eval() {
  local eval_dir="$1" report_dir="$2" embed_repo="$3" rerank_repo="$4" \
        llm_url="$5" llm_key="$6" llm_model="$7"

  : "${JUDGE_BASE_URL:?cần export JUDGE_BASE_URL — judge PHẢI độc lập với model đang đo}"
  : "${JUDGE_MODEL:?cần export JUDGE_MODEL}"
  : "${JUDGE_API_KEY:?cần export JUDGE_API_KEY — key của endpoint judge (vd. OpenRouter/DeepSeek)}"
  [ -n "$llm_url" ] || { echo "[gate_eval] thiếu LLM URL" >&2; return 1; }

  mkdir -p "$report_dir/models"

  echo "[gate_eval] tải model để đo: $embed_repo, $rerank_repo"
  # `huggingface-cli` bị khai tử ở huggingface_hub>=1.x (phát hiện qua smoke test thật
  # 2026-09-18) — CLI hiện tại là `hf`, cùng cú pháp `download <repo> --local-dir <dir>`.
  hf download "$embed_repo" --local-dir "$report_dir/models/embedding" >/dev/null
  hf download "$rerank_repo" --local-dir "$report_dir/models/reranker" >/dev/null

  echo "[gate_eval] eval embedding"
  python3 "$FT_FOR_GATE_EVAL/scripts/eval_embedding.py" --model "$report_dir/models/embedding" \
    --test "$eval_dir/embedding.jsonl" --output "$report_dir/embedding.json"

  echo "[gate_eval] eval reranker"
  python3 "$FT_FOR_GATE_EVAL/scripts/eval_reranker.py" --model "$report_dir/models/reranker" \
    --test "$eval_dir/reranker.jsonl" --output "$report_dir/reranker.json"

  run_llm_report "$eval_dir" "$report_dir" "$llm_url" "$llm_key" "$llm_model"

  echo "[gate_eval] chạy quality gate (check_finetune_thresholds.py)${GATE_POLICY:+ policy=$GATE_POLICY}"
  # GATE_POLICY (tuỳ chọn): stage 3 (detection) đặt = finetune_thresholds_detect.yml (ngưỡng hợp
  # bộ synthetic nhỏ gold-yếu); stage 6c (requalify) để TRỐNG -> dùng finetune_thresholds.yml mặc
  # định (KPI hợp đồng đầy đủ trên benchmark).
  python3 "$FT_FOR_GATE_EVAL/scripts/check_finetune_thresholds.py" \
    ${GATE_POLICY:+--policy "$GATE_POLICY"} \
    --report embedding="$report_dir/embedding.json" \
    --report reranker="$report_dir/reranker.json" \
    --report llm="$report_dir/llm-qa.json" \
    --output "$report_dir/finetune-decision.json"

  cat "$report_dir/finetune-decision.json"
}
