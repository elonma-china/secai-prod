#!/usr/bin/env sh
# Run only on the existing Vast instance; one private OCR worker.
set -eu

: "${OCR_SERVICE_TOKEN_FILE:?set path to existing token file}"
: "${OCR_RUNTIME_ROOT:=/workspace/secai-translation-ocr}"

if [ ! -r "$OCR_SERVICE_TOKEN_FILE" ]; then
  echo 'OCR token file is unreadable' >&2
  exit 1
fi

mkdir -p "$OCR_RUNTIME_ROOT/qwen3vl-model" "$OCR_RUNTIME_ROOT/runtime"
OCR_RUNTIME_ROOT="$OCR_RUNTIME_ROOT" python3 - <<'PY'
import os, shutil
if shutil.disk_usage(os.environ['OCR_RUNTIME_ROOT']).free < 20 * 1024**3:
    raise SystemExit('OCR disk reserve below 20 GiB')
PY

if [ ! -x "$OCR_RUNTIME_ROOT/runtime/venv/bin/python" ]; then
  python3 -m venv --system-site-packages "$OCR_RUNTIME_ROOT/runtime/venv"
fi

OCR_RUNTIME_ROOT="$OCR_RUNTIME_ROOT" "$OCR_RUNTIME_ROOT/runtime/venv/bin/python" - <<'PY'
import hashlib, os
from pathlib import Path
from huggingface_hub import snapshot_download
root = Path(os.environ['OCR_RUNTIME_ROOT'])
snapshot_download(repo_id='Qwen/Qwen3-VL-2B-Instruct',
                  revision='89644892e4d85e24eaac8bacfd4f463576704203',
                  local_dir=root / 'qwen3vl-model', max_workers=2,
                  allow_patterns=['*.json', '*.safetensors', '*.txt', '*.model',
                                  '*.jinja', 'LICENSE*'])
hasher = hashlib.sha256()
with (root / 'qwen3vl-model/model.safetensors').open('rb') as weights:
    for chunk in iter(lambda: weights.read(4 * 1024 * 1024), b''):
        hasher.update(chunk)
if hasher.hexdigest() != '7de1838c87a5349b016c26a1c3f7d2bc400a3d485f95ef39a7059ffd734977a0':
    raise SystemExit('OCR model checksum mismatch')
PY

export OCR_MODEL_ID='Qwen/Qwen3-VL-2B-Instruct'
export OCR_MODEL_PATH="$OCR_RUNTIME_ROOT/qwen3vl-model"
export OCR_SERVICE_TOKEN="$(cat "$OCR_SERVICE_TOKEN_FILE")"
export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1
cd "$(dirname "$0")"
exec "$OCR_RUNTIME_ROOT/runtime/venv/bin/python" -m uvicorn qwen_server:app \
  --host 127.0.0.1 --port 18083 --workers 1 --no-access-log
