#!/usr/bin/env bash
# Helper độc lập: tải 1 repo HF (production hoặc candidate) về máy GPU bất kỳ và load thử —
# dùng khi người vận hành muốn tự tay "tải về máy GPU" và kiểm tra model mới trước khi tin
# tưởng, KHÔNG phụ thuộc vào việc rent vast.ai của 08_rent_candidate_serving.sh.
#
# Dùng: ./download_and_smoke_test.sh <hf_repo_id> <local_dir> [embedding|reranker|llm]
set -euo pipefail
REPO="${1:?usage: download_and_smoke_test.sh <hf_repo_id> <local_dir> [embedding|reranker|llm]}"
LOCAL_DIR="${2:?usage: download_and_smoke_test.sh <hf_repo_id> <local_dir> [embedding|reranker|llm]}"
KIND="${3:-llm}"

# `huggingface-cli` bị khai tử ở huggingface_hub>=1.x (xác nhận qua smoke test thật
# 2026-09-18: chạy nhưng in "no longer works", exit 1) — dùng `hf` (cùng cú pháp).
echo "[download_and_smoke_test] hf download $REPO -> $LOCAL_DIR"
hf download "$REPO" --local-dir "$LOCAL_DIR"

case "$KIND" in
  embedding)
    python3 - "$LOCAL_DIR" <<'PY'
import sys
from sentence_transformers import SentenceTransformer
model = SentenceTransformer(sys.argv[1])
vec = model.encode(["Lỗ hổng Log4Shell là gì?"], normalize_embeddings=True)
print("OK — embedding dim:", vec.shape[1])
PY
    ;;
  reranker)
    python3 - "$LOCAL_DIR" <<'PY'
import sys, torch
from transformers import AutoModelForCausalLM, AutoTokenizer
tok = AutoTokenizer.from_pretrained(sys.argv[1])
model = AutoModelForCausalLM.from_pretrained(sys.argv[1], torch_dtype=torch.bfloat16, device_map="auto").eval()
msgs = [{"role": "user", "content": "<Query>: test\n<Document>: test"}]
ids = tok.apply_chat_template(msgs, tokenize=True, return_tensors="pt", add_generation_prompt=True).to(model.device)
with torch.inference_mode():
    out = model(ids).logits
print("OK — logits shape:", tuple(out.shape))
PY
    ;;
  llm|*)
    python3 - "$LOCAL_DIR" <<'PY'
import sys, torch
from transformers import AutoModelForCausalLM, AutoTokenizer
tok = AutoTokenizer.from_pretrained(sys.argv[1])
model = AutoModelForCausalLM.from_pretrained(sys.argv[1], torch_dtype=torch.bfloat16, device_map="auto").eval()
msgs = [{"role": "user", "content": "CVE-2021-44228 là lỗ hổng gì?"}]
ids = tok.apply_chat_template(msgs, tokenize=True, return_tensors="pt", add_generation_prompt=True, enable_thinking=False).to(model.device)
with torch.inference_mode():
    out = model.generate(ids, max_new_tokens=100)
print("OK —", tok.decode(out[0][ids.shape[1]:], skip_special_tokens=True))
PY
    ;;
esac
