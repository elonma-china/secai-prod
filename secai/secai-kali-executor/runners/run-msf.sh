#!/usr/bin/env bash
# metasploit check: chạy `check` (verify, KHÔNG exploit) cho 1 module vào target.
#   -q quiet · -n không dùng database · -x kịch bản resource
# Verdict lấy từ dòng "[+] ... appears vulnerable" / "[*] ... not ... / cannot be checked".
set -euo pipefail
module=""; target=""; rport=""; extra=""
while [ $# -gt 0 ]; do
  case "$1" in
    --module) module="$2"; shift 2 ;;
    --target) target="$2"; shift 2 ;;
    --rport)  rport="$2";  shift 2 ;;
    --set)    extra="${extra}; set $2"; shift 2 ;;   # vd: --set "SSL true"
    --) shift; break ;;
    *) break ;;
  esac
done
[ -n "$module" ] && [ -n "$target" ] || { echo "usage: msf-check --module <fullname> --target <host> [--rport N] [--set 'K V']" >&2; exit 2; }
script="use ${module}; set RHOSTS ${target}"
[ -n "$rport" ] && script="${script}; set RPORT ${rport}"
script="${script}${extra}; check; exit"
exec msfconsole -q -n -x "$script"
