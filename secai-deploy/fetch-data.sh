#!/usr/bin/env bash
# Pulls the ~8G runtime data bundle (qdrant snapshot, neo4j dump, bm25/exploitdb/poc tarballs)
# from the private secai1999/secai-data HF dataset into ./data. Needs `hf auth login`
# (or an HF_TOKEN env var) beforehand — the dataset is private.
set -euo pipefail
cd "$(dirname "$0")"

REVISION="${SECAI_DATA_REVISION:-v1.0.0}"

command -v hf >/dev/null 2>&1 || {
  echo "[!] 'hf' CLI not found. Install with: pip install \"huggingface_hub[cli,hf_transfer]\""
  exit 1
}

# Enable the faster Rust transfer ONLY if the optional package is importable — forcing it when
# hf_transfer isn't installed makes `hf download` hard-fail instead of falling back.
if python3 -c 'import hf_transfer' 2>/dev/null; then
  export HF_HUB_ENABLE_HF_TRANSFER=1
fi

hf download secai1999/secai-data --repo-type dataset --revision "$REVISION" --local-dir ./data

echo
echo "DONE. Data staged in ./data — now run ./restore.sh"
