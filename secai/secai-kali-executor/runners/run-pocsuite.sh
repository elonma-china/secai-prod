#!/usr/bin/env bash
# pocsuite3 verify: nạp 1 PoC (POCBase) và chạy --verify vào target (doc §4).
# Mặc định verify-only; attack/shell KHÔNG mở ở runner này (phải authz riêng — doc §5).
set -euo pipefail
poc=""; target=""
while [ $# -gt 0 ]; do
  case "$1" in
    --poc)    poc="$2"; shift 2 ;;
    --target) target="$2"; shift 2 ;;
    --) shift; break ;;
    *) break ;;
  esac
done
[ -n "$poc" ] && [ -n "$target" ] || { echo "usage: pocsuite --poc <path> --target <url> [extra pocsuite args]" >&2; exit 2; }
exec pocsuite -r "$poc" -u "$target" --verify "$@"
