#!/bin/bash
# Idempotent allowlist for the gateway's public port (:8131). Run as root before uvicorn
# binds (see secai-gateway.service ExecStartPre) so the port is never briefly wide open
# on restart. Safe to re-run: -C check before insert, -F flush-then-rebuild the sub-chain.
#
# Allowed sources (from gateway.log traffic analysis, 2026-07-15):
#   35.221.221.96   - the real GCP frontend client (bc.googleusercontent.com), full IAS
#                     product traffic (/ai/chat, /ai/exploit, /ai/translate, /ai/generate-*)
#   58.187.40.253   - developer's own manual testing box (confirmed by user)
#   127.0.0.1       - local health checks
#   192.168.1.0/24  - LAN
# Everything else on :8131 is dropped. Recon/scan traffic observed from Viettel dynamic-IP
# ranges hitting only /, /docs, /favicon.ico is exactly what this blocks.
set -euo pipefail

CHAIN=SECAI_8131
PORT=8131
ALLOW_IPS=(35.221.221.96 58.187.40.253 127.0.0.1)
ALLOW_CIDRS=(192.168.1.0/24)

iptables -N "$CHAIN" 2>/dev/null || iptables -F "$CHAIN"

for ip in "${ALLOW_IPS[@]}" "${ALLOW_CIDRS[@]}"; do
    iptables -A "$CHAIN" -s "$ip" -j ACCEPT
done
iptables -A "$CHAIN" -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT
iptables -A "$CHAIN" -j DROP

iptables -C INPUT -p tcp --dport "$PORT" -j "$CHAIN" 2>/dev/null || \
iptables -I INPUT -p tcp --dport "$PORT" -j "$CHAIN"
