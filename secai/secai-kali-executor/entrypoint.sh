#!/usr/bin/env bash
# Dispatcher trong container. Mỗi lệnh engine bị bọc `timeout` (doc §5: giới hạn thời gian).
# KHÔNG chứa logic quyết định — chỉ chạy đúng engine với slot đã cho rồi trả output thô.
set -uo pipefail

LIB=/usr/local/lib/executor
T="${EXEC_TIMEOUT:-120}"

run() { exec timeout --signal=KILL "${T}s" "$@"; }

cmd="${1:-selftest}"
shift || true

case "$cmd" in
  selftest)  exec "$LIB/selftest.sh" ;;
  tools)     exec "$LIB/tools.sh" ;;
  nuclei)    run "$LIB/run-nuclei.sh"   "$@" ;;
  msf-check) run "$LIB/run-msf.sh"      "$@" ;;
  pocsuite)  run "$LIB/run-pocsuite.sh" "$@" ;;
  searchsploit) run searchsploit "$@" ;;
  raw)       run "$@" ;;                        # passthrough để debug
  *) echo "unknown command: $cmd (dùng: selftest|nuclei|msf-check|pocsuite|raw)" >&2; exit 2 ;;
esac
