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
if shutil.disk_usage(os.environ['OCR_RUNTIME_ROOT']).free < 30 * 1024**3:
    raise SystemExit('OCR disk reserve cannot cover model download')
PY

if [ ! -x "$OCR_RUNTIME_ROOT/runtime/venv/bin/python" ]; then
  python3 -m venv --system-site-packages "$OCR_RUNTIME_ROOT/runtime/venv"
fi

command -v nvidia-smi >/dev/null 2>&1 || { echo 'nvidia-smi unavailable' >&2; exit 1; }
"$OCR_RUNTIME_ROOT/runtime/venv/bin/python" - <<'PY'
from importlib.metadata import version
expected = {
    'torch': '2.11.0+cu130', 'transformers': '5.13.1',
    'fastapi': '0.136.3', 'pydantic': '2.13.4',
    'Pillow': '12.3.0', 'uvicorn': '0.51.0',
    'huggingface-hub': '1.23.0', 'safetensors': '0.8.0',
    'accelerate': '1.14.0',
}
for package, pinned in expected.items():
    actual = version(package)
    if actual != pinned:
        raise SystemExit(f'OCR runtime dependency mismatch: {package}')
PY

OCR_RUNTIME_ROOT="$OCR_RUNTIME_ROOT" "$OCR_RUNTIME_ROOT/runtime/venv/bin/python" - <<'PY'
import hashlib, os, shutil
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
if shutil.disk_usage(root).free < 20 * 1024**3:
    raise SystemExit('OCR disk reserve below 20 GiB after download')
PY

export OCR_MODEL_ID='Qwen/Qwen3-VL-2B-Instruct'
export OCR_MODEL_PATH="$OCR_RUNTIME_ROOT/qwen3vl-model"
export OCR_MAX_PEAK_MIB="${OCR_MAX_PEAK_MIB:-6144}"
export OCR_SERVICE_TOKEN="$(cat "$OCR_SERVICE_TOKEN_FILE")"
export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1
OCR_SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
if [ -f "$OCR_SCRIPT_DIR/server.py" ]; then
  OCR_HELPER_DIR="$OCR_SCRIPT_DIR"
else
  OCR_HELPER_DIR="$(CDPATH= cd -- "$OCR_SCRIPT_DIR/../../../deploy/ocr" && pwd)"
fi
export PYTHONPATH="$OCR_HELPER_DIR${PYTHONPATH:+:$PYTHONPATH}"
cd "$(dirname "$0")"
exec "$OCR_RUNTIME_ROOT/runtime/venv/bin/python" -m uvicorn qwen_ocr_supervisor:app \
  --host 127.0.0.1 --port 18083 --workers 1 --no-access-log
