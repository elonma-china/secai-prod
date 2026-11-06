---
name: playbook-methodology
description: Engagement-level methodology reference — PTES/OWASP/NIST/OSSTMM mapping, rules of engagement, legal boundaries, evidence handling, cleanup. Load at engagement START or when scope/authorization questions arise.
tags: [scope_check]
---

# Methodology & Rules of Engagement

## Framework map (pick per engagement type)
| Framework | Use when |
|---|---|
| **PTES** | Full-scope pentest — the 7-phase spine (intel → recon → mapping → vuln → exploit → post-ex → report) |
| **OWASP Testing Guide** | Web/API focus — test cases per vuln class |
| **NIST SP 800-115** | Client requires a recognized US standard / structured technical assessment |
| **OSSTMM** | Metrics-driven assessment, need auditable coverage percentages |
| **MITRE ATT&CK** | Map findings to real adversary TTPs — report impact as "an attacker would do X" |

## BEFORE any action — non-negotiable
1. **Written authorization** exists, signed by someone who can authorize the target. Verbal = no test.
2. **Scope**: exact IPs/domains/ranges, exclusions, test window, allowed techniques. Feed to `scope_check` — every tool invocation checks against it.
3. **Rules of engagement**: scan rates, DoS limits, social-engineering allowed?, physical allowed?, data handling clause, emergency contact.
4. **Third parties**: cloud shared-responsibility boundaries, subsidiaries, partners' infra — out of scope unless explicitly named.
5. Legal frame: client contract + local law (Convention Budapest / national cybercrime law) supersedes any methodology.

## During the test
- Log every action (command, timestamp, target) — your log is the defense if something breaks.
- Rate-limit scanners and brute force; assume any outage is YOURS until proven otherwise.
- Collect the minimum data needed to prove impact; encrypt findings at rest; never exfil real customer data to personal storage.
- Compromise of an out-of-scope system, even accidental: stop, secure it, disclose to client immediately.
- Out-of-band callbacks: use your own infrastructure (interactsh/ollin), never third-party domains.

## AFTER the test — cleanup
- Remove: shells/backdoors, dropped files, created accounts, cron/persistence, SSH keys, test data injected.
- Verify cleanup: re-check every host touched; note anything that cannot be removed safely (and why).
- Revoke/deliver credentials found — do NOT reuse them post-engagement.

## Report shape (PTES-aligned)
1. Executive summary (business language, no vuln names)
2. Findings: severity (CVSS + exploitability), evidence, step-by-step reproduction, remediation
3. Attack narrative: how the kill chain connected findings (ATT&CK-mapped)
4. Coverage & methodology (what was and was NOT tested)
5. Remediation roadmap prioritized by risk, not by tool order

## Retest
Fixes → retest ONLY the affected findings → upgrade/downgrade status in engagement state (`state_update`) → short addendum report.
