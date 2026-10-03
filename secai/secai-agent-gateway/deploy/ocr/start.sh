#!/usr/bin/env sh
# Run from the copied deploy/ocr directory on the existing Vast instance.
# OCR_SERVICE_TOKEN_FILE must name an existing, owner-readable secret file.
set -eu

: "${OCR_SERVICE_TOKEN_FILE:?set path to existing token file}"
: "${OCR_RUNTIME_ROOT:=/workspace/secai-translation-ocr}"

if [ ! -r "$OCR_SERVICE_TOKEN_FILE" ]; then
  echo 'OCR token file is unreadable' >&2
  exit 1
fi

mkdir -p "$OCR_RUNTIME_ROOT/model" "$OCR_RUNTIME_ROOT/runtime"
OCR_RUNTIME_ROOT="$OCR_RUNTIME_ROOT" python3 - <<'PY'
import os, shutil
free = shutil.disk_usage(os.environ['OCR_RUNTIME_ROOT']).free
if free < 20 * 1024**3:
    raise SystemExit('OCR disk reserve below 20 GiB')
PY

if [ ! -x "$OCR_RUNTIME_ROOT/runtime/venv/bin/python" ]; then
  python3 -m venv --system-site-packages "$OCR_RUNTIME_ROOT/runtime/venv"
fi

# Download happens only here, before the service enters offline inference mode.
OCR_RUNTIME_ROOT="$OCR_RUNTIME_ROOT" "$OCR_RUNTIME_ROOT/runtime/venv/bin/python" - <<'PY'
import hashlib, os
from pathlib import Path
from huggingface_hub import snapshot_download
root = Path(os.environ['OCR_RUNTIME_ROOT'])
revision = 'c5630abae1d940eafe0697512a0325494b02ab42'
snapshot_download(repo_id='PaddlePaddle/PaddleOCR-VL-1.6', revision=revision,
                  local_dir=root / 'model', max_workers=2,
                  allow_patterns=['*.json', '*.safetensors', '*.txt', '*.model',
                                  '*.jinja', '*.md', 'LICENSE*'])
hasher = hashlib.sha256()
with (root / 'model/model.safetensors').open('rb') as weights:
    for chunk in iter(lambda: weights.read(4 * 1024 * 1024), b''):
        hasher.update(chunk)
digest = hasher.hexdigest()
if digest != '85a479d506a11e724e7285d395c551be69f41dbc16b6342d3cacfb189aed71db':
    raise SystemExit('OCR model checksum mismatch')
PY

export OCR_MODEL_PATH="$OCR_RUNTIME_ROOT/model"
export OCR_SERVICE_TOKEN="$(cat "$OCR_SERVICE_TOKEN_FILE")"
export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1
cd "$(dirname "$0")"
exec "$OCR_RUNTIME_ROOT/runtime/venv/bin/python" -m uvicorn server:app \
  --host 127.0.0.1 --port 18083 --workers 1 --no-access-log
