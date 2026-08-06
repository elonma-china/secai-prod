#!/bin/bash
# Idempotent allowlist for the gateway's public port (:8131). Run as root before uvicorn
# binds (see secai-gateway.service ExecStartPre) so the port is never briefly wide open
# on restart. Safe to re-run: -C check before insert, -F flush-then-rebuild the sub-chain.
#
# 2026-07-27: opened to ALL sources (0.0.0.0/0) at user's explicit request, done knowingly —
# /ai/exploit has no auth (200 with no token) so this was the only access control on that
# endpoint; user was told this before confirming. Previous allowlist (FE GCP egress IPs +
# dev box + LAN), kept here so it's a one-line revert if this needs to be locked back down:
#   ALLOW_IPS=(35.236.157.253 35.236.183.207 34.81.181.92 35.221.221.96 58.187.40.253 127.0.0.1)
#   ALLOW_CIDRS=(192.168.1.0/24)
set -euo pipefail

CHAIN=SECAI_8131
PORT=8131
ALLOW_IPS=()
ALLOW_CIDRS=(0.0.0.0/0)

iptables -N "$CHAIN" 2>/dev/null || iptables -F "$CHAIN"

for ip in "${ALLOW_IPS[@]}" "${ALLOW_CIDRS[@]}"; do
    iptables -A "$CHAIN" -s "$ip" -j ACCEPT
done
iptables -A "$CHAIN" -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT
iptables -A "$CHAIN" -j DROP

iptables -C INPUT -p tcp --dport "$PORT" -j "$CHAIN" 2>/dev/null || \
iptables -I INPUT -p tcp --dport "$PORT" -j "$CHAIN"
