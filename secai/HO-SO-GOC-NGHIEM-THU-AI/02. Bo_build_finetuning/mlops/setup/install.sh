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

say "== 1b. venv điều phối .venv-eval (host chạy stage 1-3/9 product-gate + trigger_api) =="
# BẮT BUỘC: run_pipeline.sh, product-KPI gate (run_product_kpi.sh: PY=.venv-eval/bin/python) và
# trigger_api/run.sh đều dùng .venv-eval. venv KHÔNG relocatable + bị gitignore → máy mới phải tự tạo.
VENV="$ROOT/.venv-eval"
if [ -x "$VENV/bin/python" ] && "$VENV/bin/python" -c "import sentence_transformers, fastapi, httpx, yaml" 2>/dev/null; then
  ok ".venv-eval đã có + đủ deps ($VENV)"
else
  say "tạo/cập nhật .venv-eval + cài deps điều phối (eval + fastapi/uvicorn/httpx/pyyaml)…"
  python3 -m venv "$VENV" \
    && "$VENV/bin/pip" install -q -U pip \
    && "$VENV/bin/pip" install -q torch --index-url https://download.pytorch.org/whl/cpu \
    && "$VENV/bin/pip" install -q -r "$FT/requirements-eval.txt" \
    && "$VENV/bin/pip" install -q fastapi "uvicorn[standard]" httpx pyyaml \
    && ok "đã tạo .venv-eval + cài deps điều phối" \
    || bad "tạo/cài .venv-eval thất bại — cần cho stage 1-3/9 (product gate) + trigger_api"
fi
EVAL_PY="$VENV/bin/python"; [ -x "$EVAL_PY" ] || EVAL_PY="python3"

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
# Kiểm TRONG .venv-eval (nơi run_pipeline/product-gate thực sự chạy), không phải python hệ thống.
"$EVAL_PY" -c "import sentence_transformers, transformers, torch" 2>/dev/null \
  && ok "sentence-transformers/transformers/torch có trong .venv-eval" \
  || bad "thiếu deps eval trong .venv-eval — chạy lại mục 1b (tạo .venv-eval) đã sửa ở trên"

say "== 6. Python packages cho train/DPO/quantize (chỉ máy chạy --execute) =="
python3 -c "import unsloth" 2>/dev/null && ok "unsloth có sẵn" || say "chưa cài — pip install -r $FT/requirements-train.txt (chỉ cần trên máy sẽ chạy --execute)"
python3 -c "import llmcompressor" 2>/dev/null && ok "llmcompressor có sẵn" || say "chưa cài — pip install -r $QUANT/requirements.txt (chỉ cần trên máy chạy 06_quantize_candidate.sh)"

say "== 7. Stack phục vụ (product-gate MẶC ĐỊNH gọi inference THẬT — cần stack đang chạy) =="
# Product-KPI gate (PRODUCT_GATE=1, mặc định) đọc endpoint embed/rerank/qdrant từ secai-docker/.env
# và gọi gateway /ai/chat — KHÁC nhánh cũ đọc secai-agent-gateway/.env + QWEN_* (đã stale).
python3 -c "
import sys
sys.path.insert(0, '$ROOT/scripts')
from _lib import common
c = common.load_config()
dk = common.resolve_path(c['gateway']['docker_env_file'])
env = common.read_env_file(dk)
missing = [k for k in ('EMBEDDING_API_URL', 'RERANKER_API_URL', 'QDRANT_URL') if not env.get(k)]
if missing:
    print('[install]  ✗ thiếu ' + ', '.join(missing) + ' trong ' + str(dk))
    raise SystemExit(1)
print('[install]  ✓ ' + str(dk) + ' có EMBEDDING/RERANKER/QDRANT URL')
" || FAIL=1
HEALTH="$(python3 -c "import sys;sys.path.insert(0,'$ROOT/scripts');from _lib import common;print(common.load_config()['gateway'].get('default_health_url',''))" 2>/dev/null)"
if [ -n "$HEALTH" ]; then
  if curl -fsS -m 6 "$HEALTH" >/dev/null 2>&1; then
    ok "gateway sống ($HEALTH) — product gate gọi được /ai/chat + embed/rerank"
  else
    bad "gateway KHÔNG phản hồi ($HEALTH) — product gate (mặc định) cần gateway :8131 + serving + qdrant ĐANG CHẠY, KỂ CẢ dry-run. Bật stack (secai-docker) trước; hoặc đặt PRODUCT_GATE=0 để dùng gate benchmark cũ."
  fi
fi

say "== 8. Judge độc lập (JUDGE_* — chấm faithfulness/relevance ở stage 3/9) =="
if [ -n "${JUDGE_BASE_URL:-}" ] && [ -n "${JUDGE_API_KEY:-}" ]; then
  if curl -fsS -m 8 -H "Authorization: Bearer ${JUDGE_API_KEY}" "${JUDGE_BASE_URL%/}/models" >/dev/null 2>&1; then
    ok "judge reachable (${JUDGE_BASE_URL}) — model=${JUDGE_MODEL:-<chưa đặt JUDGE_MODEL>}"
  else
    bad "JUDGE_BASE_URL/API_KEY đặt rồi nhưng /models không phản hồi — kiểm tra key/credit/mạng trước khi chạy stage 3/9"
  fi
  [ -n "${JUDGE_MODEL:-}" ] || bad "thiếu \$JUDGE_MODEL — đặt model NON-REASONING (vd deepseek/deepseek-chat-v3.1) để gate benchmark ~1000 mẫu không quá chậm"
else
  bad "thiếu \$JUDGE_BASE_URL/\$JUDGE_API_KEY — judge PHẢI độc lập với model đang đo; xem mlops.env.example"
fi

echo
if [ "$FAIL" = "0" ]; then
  echo "[install] TẤT CẢ preflight OK — có thể chạy: python3 scripts/run_pipeline.sh (dry-run mặc định)"
else
  echo "[install] Có mục CHƯA đạt ở trên — sửa rồi chạy lại trước khi dùng --execute/--allow-deploy" >&2
fi
exit "$FAIL"
