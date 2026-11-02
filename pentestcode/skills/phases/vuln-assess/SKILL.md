---
name: vuln-assess-phase
tags: [vuln_assess]
description: Vulnerability analysis — triage scanner output, verify manually, score with CVSS, mark false positives. Use when the current phase is VULN_ASSESS.
---

# Vulnerability Analysis — Scan, Verify, Score

Raw scanner output is 80% noise. This phase exists to VERIFY and PRIORITIZE, not to collect CVEs.

## Automated scan
```
nuclei -u TARGET -severity critical,high -t ~/nuclei-templates/ -json -o nuclei.json
# authenticated nuclei when creds exist (10x coverage): -H 'Authorization: Bearer ...'
nikto -h https://target -Format json -o nikto.json        # web misconfig baseline
```
- Authenticated scans > anonymous scans everywhere (Nessus/OpenVAS/Nuclei): login first, then scan.
- Parse into state with `nuclei_parse` — dedupes and links vulns to hosts automatically.

## Manual verification — MANDATORY before marking confirmed
Every scanner hit gets one of:
1. **Confirmed** — reproduce manually, capture request/response as evidence
2. **False positive** — record WHY (version patched, control in place); don't just delete
3. **Needs auth/context** — defer to exploitation phase with the required creds attached

Classic FP sources: version banners lying (custom builds), WAF returning 200 for everything, template regex matching error pages.

## CVSS scoring for prioritization
`CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H` = 9.8 Critical
- Prioritize by: exploitability (AV:N + no auth + public PoC) × impact, not by CVSS alone.
- A 9.8 unauthenticated RCE beats a 9.1 post-auth one. An exposure with active in-the-wild exploitation (CISA KEV) beats both.

## Correlation across findings
- 3 "medium" findings chaining to RCE = one "critical" attack path (`attack_path_suggest`).
- Info leaks (versions, stack traces, .git exposure) feed other findings — chain them, don't score them isolated.

## Gotchas
- Never report a vuln you could not reproduce — the retest will destroy client trust.
- Keep scanner timing/rate in evidence: a DoS-looking outage during scan ruins the report.
- Store evidence (PoC requests, screenshots) at confirmation time, not at reporting time.
