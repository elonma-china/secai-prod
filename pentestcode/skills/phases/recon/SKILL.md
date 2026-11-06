---
name: recon-phase
tags: [recon]
description: Reconnaissance phase — passive OSINT then active enumeration. Use when the current phase is RECON. Triggers - OSINT, subdomain, whois, certificate transparency, shodan, google dork, footprint.
---

# Reconnaissance — Passive FIRST, then Active

Rule: exhaust PASSIVE before touching the target. Passive leaves no logs on target infra; active does.

## Passive
- **DNS/subdomains**: `subfinder -d target.com -silent`, `amass enum -passive -d target.com`, `assetfinder --subs-only target.com`
- **Cert transparency**: `curl -s "https://crt.sh/?q=%25.target.com&output=json" | jq -r '.[].name_value' | sort -u` — often finds forgotten subdomains + internal hostnames in CN fields
- **WHOIS/RDAP**: registrar, name servers, historical contacts (who.is, rdap.org)
- **Net blocks**: `whois -h whois.radb.net -- '-i origin ASXXXX'` → prefix list, then validate with `asnmap`
- **Shodan/Censys**: exposed services, banners, SSL certs (Shodan cert search pivots to sibling hosts on same org), leaked `.env`/`.git` indexes
- **Google dorks**: `site:target.com filetype:xlsx`, `site:target.com inurl:admin`, `intitle:"index of" site:target.com`, `site:target.com ext:env | ext:sql | ext:bak`
- **Code leaks**: GitHub/GitLab search `org:target`, `target.com password`, secret scanning (trufflehog/gitleaks on found repos)
- **Social**: LinkedIn (org chart, tech stack, employees for phishing/spray), job ads (tech in use), Wayback Machine for dead endpoints still live internally

## Active (in-scope domains/IPs ONLY)
- **DNS brute + zone transfer**: `dig axfr target.com @NS`, `gobuster dns -d target.com -w wordlist`
- **Fingerprint**: `httpx -l subs.txt -status-code -title -tech-detect -favicon-hash` — favicon hash pivots to `Shodan http.favicon.hash:<mmh3>`
- **ASN/IP expansion**: resolve all found hosts → new IPs → re-check every IP against scope with `scope_check`
- **WAF detect**: `wafw00f https://target.com` — affects scan tuning later

## Gotchas
- Subdomain takeover check immediately: dangling CNAME (npm/github/azure/s3) → `subjack`/`nuclei -t takeovers/`. It's recon AND a finding.
- A cert in CT logs is NOT proof the host is in scope. Validate + `scope_check` before any active probe.
- Record everything into the engagement state (`state_update`): hosts, domains, org relationships. Later phases consume this — a host found in recon but never scanned is the classic miss.
- Diff CT logs against live DNS: dead names may still resolve on INTERNAL resolvers (VPN/recon from inside).
