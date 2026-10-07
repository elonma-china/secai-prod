#!/usr/bin/env bash
# Build the Devspace FE into the canary dist only (never secai-fe-devspace/dist, which :5173 serves).
set -euo pipefail
. "$(dirname "${BASH_SOURCE[0]}")/common.sh"
OUT="$AB_FE_DIR/dist"
case "$OUT" in /tmp/ab-canary*) ;; *) echo "refusing to build outside /tmp/ab-canary*" >&2; exit 1 ;; esac
mkdir -p "$AB_FE_DIR" "$AB_LOGS"
cd "$AB_ROOT/secai-fe-devspace"
# Vite also reads secai-fe-devspace/.env.local; the three flags below override it explicitly.
VITE_REPORT_FEATURE_ENABLED=true \
VITE_TRANSLATE_V2_FILES_ENABLED=true \
VITE_TRANSLATE_V2_IMAGES_ENABLED=true \
VITE_GATEWAY_PROXY="http://127.0.0.1:$AB_GW_PORT" \
  npx vite build --outDir "$OUT" --emptyOutDir 2>&1 | tee "$AB_LOGS/build-fe.log" | tail -n 8
test -f "$OUT/index.html"
echo "built $OUT"
