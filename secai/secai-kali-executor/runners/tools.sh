#!/usr/bin/env bash
# Liệt kê kho công cụ đã cài (thông tin, KHÔNG gate như selftest).
set -uo pipefail
show() {
  if p="$(command -v "$1" 2>/dev/null)"; then printf '  \342\234\223 %-18s %s\n' "$1" "$p"
  else printf '  \342\234\227 %-18s (thi\341\272\277u)\n' "$1"; fi
}
echo "== Verify engines =="
for t in nuclei msfconsole msfvenom pocsuite sqlmap searchsploit; do show "$t"; done
echo "== Recon / web =="
for t in nmap masscan nikto whatweb wafw00f gobuster dirb ffuf wfuzz dnsrecon nbtscan onesixtyone; do show "$t"; done
echo "== Brute / crack =="
for t in hydra medusa john hashcat hashid; do show "$t"; done
echo "== SMB / AD / net =="
for t in smbclient rpcclient enum4linux-ng ldapsearch responder snmpwalk \
         secretsdump.py psexec.py wmiexec.py GetNPUsers.py GetUserSPNs.py ntlmrelayx.py \
         nxc netexec; do show "$t"; done
echo "== Net utils =="
for t in ncat nc socat tcpdump dig whois python3 git curl; do show "$t"; done
