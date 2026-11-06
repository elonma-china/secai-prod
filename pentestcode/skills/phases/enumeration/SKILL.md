---
name: enumeration-phase
tags: [enumeration]
description: Scanning and enumeration — port/service scan, version detection, web content discovery, tech stack fingerprint. Use when the current phase is ENUMERATION.
---

# Scanning & Enumeration

Goal: every host in scope fully mapped — ports, versions, banners, hidden surfaces. Feed results into engagement state (`state_update`), not just terminal scrollback.

## Port scan — tunable speed vs coverage
```
# Fast baseline, all ports, no discovery (live hosts known)
masscan -p1-65535 --rate 1000 -oJ masscan.json TARGET
# Full service detection on discovered ports
nmap -sS -sV -sC -p- -oA nmap/full TARGET          # slow but thorough
nmap -sS -sV -p <ports> --open -oA nmap/svc TARGET  # after masscan
```
- Never run default-rate `-p-` against fragile/OT targets — negotiate rate in RoE.
- `-Pn` when ICMP is filtered; combine `-sn` on the whole CIDR first to find live hosts cheaply.
- UDP top-100 minimum: `nmap -sU --top-ports 100 -sV -O` — SNMP/DNS/mDNS/NTP/LDAP often missed.

## Service enrichment
- **NSE**: `--script vuln,default` for triage; dangerous scripts (`brute`, `dos`, `exploit`) ONLY with explicit authorization.
- **SMB**: `nxc smb HOST` (shares, signing, OS, null session) → often instant finding (signing disabled = relay).
- **SNMP**: `snmpwalk -v2c -c public HOST .1.3.6.1.2.1` → network layout, VLANs, VPN peers.
- **TLS**: `testssl.sh HOST` — old TLS, weak ciphers, expired/expiring certs, heartbleed.

## Web surface discovery
```
# Content + vhosts + params
ffuf -u https://t/FUZZ -w raft-medium-directories -mc 200,204,301,302,401,403
ffuf -u https://t/ -H "Host: FUZZ.t" -w subdomains.txt      # vhost fuzzing
arjun -u https://t/page                                        # hidden params
```
- Status-code triage: 401/403 → bypass tests (`X-Original-URL`, method override); 301 to login = app surface.
- Mirror interesting dirs with `wget --mirror` or `katana` (crawl + JS link extraction) — JS files hold API routes and often tokens.
- API discovery: `/swagger`, `/openapi.json`, `/graphql`, `/.well-known/`, `/api/v{1..10}/`.

## Tech stack
`whatweb -v URL` / `httpx -td` → CMS-specific scans (wpscan), framework CVE lookup, then version-match against Nuclei templates.

## Gotchas
- Scan results without version detail are wasted — always `-sV`. A raw open port is not a finding.
- Load `nuclei` DURING this phase, not later: framework RCEs (Log4j, Spring4Shell, CVE-2021-*) don't show up in dir-fuzz.
- Deduplicate into engagement state via `nmap_parse` / `nuclei_parse` tools — the exploit phase queries `state_query`, it does not re-scan.
