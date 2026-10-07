#!/usr/bin/env bash
# Stop only the processes this harness started (PIDs in $AB_CANARY_DIR/pids). Idempotent.
# Usage: down.sh [--purge]   (--purge also deletes $AB_CANARY_DIR, including DB, tokens and dist)
set -u
. "$(dirname "${BASH_SOURCE[0]}")/common.sh"

stop_one() { # name marker
  local pid; pid="$(ab_pid_of "$1")"
  if [ -n "$pid" ] && ab_alive "$pid" "$2"; then
    kill "$pid" 2>/dev/null
    for _ in $(seq 1 50); do kill -0 "$pid" 2>/dev/null || break; sleep 0.1; done
    kill -0 "$pid" 2>/dev/null && kill -9 "$pid" 2>/dev/null
    echo "stopped $1 (pid $pid)"
  else
    echo "$1 not running"
  fi
}

stop_one static "$AB_FE_DIR/serve_static.py"
stop_one gateway "$AB_APP"
rm -f "$AB_PIDS"

if [ "${1:-}" = "--purge" ]; then
  case "$AB_CANARY_DIR" in
    /tmp/ab-canary*) rm -rf -- "$AB_CANARY_DIR"; echo "purged $AB_CANARY_DIR" ;;
    *) echo "refusing to purge non-/tmp/ab-canary* directory" >&2; exit 1 ;;
  esac
fi
exit 0
