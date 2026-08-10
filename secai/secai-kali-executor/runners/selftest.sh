#!/usr/bin/env bash
# Xác nhận container "có đủ khả năng": mọi engine gọi được (exit 0 hoặc in ra version).
set -uo pipefail
echo "== SecAI Kali executor · selftest =="
ok=0; fail=0

strip_ansi() { sed -r 's/\x1b\[[0-9;]*[mGKHhl]//g'; }

check() {
  local name="$1"; shift
  local out rc line
  out="$("$@" 2>&1)"; rc=$?
  line="$(printf '%s\n' "$out" | strip_ansi | grep -m1 -E '[0-9]' | sed 's/^[[:space:]]*//' | cut -c1-70)"
  if [ "$rc" -eq 0 ] || [ -n "$line" ]; then
    printf '%-14s OK   %s\n' "$name" "$line"; ok=$((ok+1))
  else
    printf '%-14s FAIL (rc=%s)\n' "$name" "$rc"; fail=$((fail+1))
  fi
}

check nuclei     nuclei -version -duc
check msfconsole msfconsole --version
check pocsuite   pocsuite --version
check sqlmap     sqlmap --version
check nmap       nmap --version
check python3    python3 --version
echo "== ok=${ok} fail=${fail} =="
[ "$fail" -eq 0 ]
