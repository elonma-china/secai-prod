#!/usr/bin/env bash
# Preflight cho máy điều phối/train của pipeline mlops/ — cài dependency + xác nhận mọi thứ
# CẦN CÓ trước khi chạy run_pipeline.sh --execute. Không cài/khởi động gì trên máy serving GPU
# (đó là ephemeral vast.ai, tự cài qua image — xem 04. Huong_dan_cai_dat/HUONG-DAN-CAI-DAT.md).
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FT="$ROOT/../finetune"
QUANT="$ROOT/../quantization"
FAIL=0

say() { echo "[install] $*"; }
ok()  { echo "[install]  ✓ $*"; }
bad() { echo "[install]  ✗ $*" >&2; FAIL=1; }

say "== 1. Python packages (glue code — chỉ stdlib, không cần cài gì) =="
python3 -c "import json, urllib.request" && ok "python3 stdlib OK"

say "== 2. hf CLI + HF_TOKEN (quyền ghi lên repo production/candidate) =="
# huggingface_hub>=1.x khai tử `huggingface-cli` (xác nhận qua smoke test thật 2026-09-18:
# lệnh chạy nhưng in "no longer works" + exit 1) — CLI hiện tại tên `hf`, cùng cú pháp.
if command -v hf >/dev/null 2>&1; then
  ok "hf CLI có sẵn"
else
  say "cài: pip install -U huggingface_hub"
  pip install -U huggingface_hub && ok "đã cài huggingface_hub (kèm hf CLI)" || bad "cài huggingface_hub thất bại"
fi
if [ -n "${HF_TOKEN:-}" ]; then
  if hf auth whoami >/dev/null 2>&1; then
    ok "HF_TOKEN hợp lệ ($(hf auth whoami 2>/dev/null | head -1))"
  else
    bad "HF_TOKEN đặt rồi nhưng whoami thất bại — kiểm tra token còn hạn/đúng quyền write"
  fi
else
  bad "thiếu \$HF_TOKEN — export HF_TOKEN=<token có quyền write lên repo production/candidate> trước khi chạy --execute"
fi

say "== 3. vastai CLI + API key (rent instance requalify + production) =="
if command -v vastai >/dev/null 2>&1; then
  ok "vastai CLI có sẵn"
  if vastai --url https://cloud.vast.ai show user >/dev/null 2>&1; then
    ok "vastai API key hợp lệ"
  else
    bad "vastai show user thất bại — chạy: vastai set api-key <KEY> (console.vast.ai có thể 403, dùng --url https://cloud.vast.ai)"
  fi
else
  bad "thiếu vastai CLI — pip install vastai"
fi

say "== 4. GPU cho train/DPO (SFT LLM cần GPU 80GB+) =="
if command -v nvidia-smi >/dev/null 2>&1 && nvidia-smi >/dev/null 2>&1; then
  VRAM="$(nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits | head -1)"
  ok "GPU phát hiện — VRAM ${VRAM}MB $([ "${VRAM:-0}" -ge 80000 ] 2>/dev/null && echo '(đủ cho SFT LLM 96GB khuyến nghị, kiểm tra thêm nếu <96GB)')"
else
  bad "không thấy GPU (nvidia-smi) trên máy này — CHỈ chạy được stage 01-03 (drift check, không tốn GPU); train/DPO/quantize (--execute) cần chạy trên máy có GPU 80GB+"
fi

say "== 5. Python packages cho eval (giai đoạn 3/9 — CẦN cả ở dry-run, không cần GPU) =="
# Phát hiện thật 2026-09-18: chạy run_pipeline.sh mặc định (dry-run, chỉ 1->3) trên 1 máy điều
# phối sạch vẫn chết ở eval_embedding.py vì thiếu sentence-transformers — dễ nhầm là "stage 1-3
# không cần cài gì" (đúng cho GPU, sai cho python deps này).
python3 -c "import sentence_transformers, transformers, torch" 2>/dev/null \
  && ok "sentence-transformers/transformers/torch có sẵn" \
  || bad "thiếu sentence-transformers/transformers/torch — pip install torch --index-url https://download.pytorch.org/whl/cpu && pip install -r $FT/requirements-eval.txt (CPU đủ dùng, không cần GPU cho stage 1-3)"

say "== 6. Python packages cho train/DPO/quantize (chỉ máy chạy --execute) =="
python3 -c "import unsloth" 2>/dev/null && ok "unsloth có sẵn" || say "chưa cài — pip install -r $FT/requirements-train.txt (chỉ cần trên máy sẽ chạy --execute)"
python3 -c "import llmcompressor" 2>/dev/null && ok "llmcompressor có sẵn" || say "chưa cài — pip install -r $QUANT/requirements.txt (chỉ cần trên máy chạy 06_quantize_candidate.sh)"

say "== 7. Qdrant + gateway .env =="
python3 -c "
import sys
sys.path.insert(0, '$ROOT/scripts')
from _lib import common
c = common.load_config()
gw = common.read_env_file(common.resolve_path(c['gateway']['env_file']))
missing = [k for k in ('QWEN_BASE_URL', 'QWEN_API_KEY') if not gw.get(k)]
if missing:
    print('[install]  ✗ thiếu ' + ', '.join(missing) + ' trong ' + str(common.resolve_path(c['gateway']['env_file'])))
    raise SystemExit(1)
print('[install]  ✓ gateway .env đọc được QWEN_BASE_URL')
" || FAIL=1

echo
if [ "$FAIL" = "0" ]; then
  echo "[install] TẤT CẢ preflight OK — có thể chạy: python3 scripts/run_pipeline.sh (dry-run mặc định)"
else
  echo "[install] Có mục CHƯA đạt ở trên — sửa rồi chạy lại trước khi dùng --execute/--allow-deploy" >&2
fi
exit "$FAIL"
