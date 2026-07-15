#!/bin/bash
# Idempotent allowlist for the gateway's public port (:8131) in the Docker Compose deployment.
#
# IMPORTANT: the gateway runs as a Docker-published port (docker-compose.yml: "8131:8131"), so
# inbound traffic is DNAT'd and evaluated in the FORWARD path (DOCKER-USER chain) — it NEVER
# hits the INPUT chain. Filtering INPUT (the bare-metal approach) would be a silent no-op and
# leave the intentionally-unauthenticated gateway world-open. So we filter in DOCKER-USER, which
# Docker processes first for all container traffic.
#
# Run as root AFTER `docker compose up -d gateway` (the DOCKER-USER chain only exists once Docker
# has created its iptables scaffolding). Safe to re-run: -C check before insert, -F flush-rebuild.
#
# >>> EDIT ALLOW_IPS / ALLOW_CIDRS for THIS deployment before running <<<
# (defaults are loopback + private LAN only — add your real frontend/dev IPs.)
set -euo pipefail

CHAIN=SECAI_8131
PORT=8131
ALLOW_IPS=(127.0.0.1)          # add your frontend + dev-testing public IPs here
ALLOW_CIDRS=(192.168.0.0/16)   # private LAN; tighten/remove as needed

# Build the allowlist sub-chain: allowed sources RETURN (fall through to Docker's ACCEPT),
# everything else destined for :8131 is DROPped.
iptables -N "$CHAIN" 2>/dev/null || iptables -F "$CHAIN"
iptables -A "$CHAIN" -m conntrack --ctstate ESTABLISHED,RELATED -j RETURN
for ip in "${ALLOW_IPS[@]}" "${ALLOW_CIDRS[@]}"; do
    iptables -A "$CHAIN" -s "$ip" -j RETURN
done
iptables -A "$CHAIN" -j DROP

# Hook it into DOCKER-USER for traffic destined to the gateway's container port. Note: this
# matches any forwarded packet with --dport 8131; on this host only the gateway uses that port.
iptables -C DOCKER-USER -p tcp --dport "$PORT" -j "$CHAIN" 2>/dev/null || \
iptables -I DOCKER-USER -p tcp --dport "$PORT" -j "$CHAIN"

echo "Applied $CHAIN allowlist on DOCKER-USER for :$PORT — allowed: ${ALLOW_IPS[*]} ${ALLOW_CIDRS[*]}"
