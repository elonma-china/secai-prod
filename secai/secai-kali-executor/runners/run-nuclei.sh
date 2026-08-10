#!/usr/bin/env bash
# nuclei verify: chạy 1 template vào target, xuất JSONL (verdict có cấu trúc — doc §6).
#   -duc/-disable-update-check: không tự cập nhật (egress bị hạn chế)
#   -ni: tắt interactsh (OOB cần server ngoài → không dùng trong sandbox egress-hẹp)
set -euo pipefail
tpl=""; target=""
while [ $# -gt 0 ]; do
  case "$1" in
    --template) tpl="$2"; shift 2 ;;
    --target)   target="$2"; shift 2 ;;
    --) shift; break ;;
    *) break ;;
  esac
done
[ -n "$tpl" ] && [ -n "$target" ] || { echo "usage: nuclei --template <path> --target <url> [extra nuclei args]" >&2; exit 2; }
exec nuclei -t "$tpl" -u "$target" -jsonl -duc -disable-update-check -ni "$@"
