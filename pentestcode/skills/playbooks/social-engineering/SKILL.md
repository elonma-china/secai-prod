---
name: playbook-social-engineering
description: Social engineering assessment — phishing campaigns, vishing, physical tests, GoPhish setup. Load when the engagement scope explicitly includes social engineering or physical testing.
tags: [vuln_assess]
---

# Social Engineering — Phishing, Vishing, Physical

HARD GATE: this playbook requires the RoE to EXPLICITLY name social engineering/physical testing, the exact target population size, and data-handling rules. If the scope is silent → DO NOT run, ask the operator to confirm authorization.

## Phishing (GoPhish)
1. **Pretext selection** from OSINT (recon phase): org tools, HR processes, recent events (reorg, new VPN, survey, invoice).
2. **Infrastructure**: own domain similar to org's, SPF/DKIM/DMARC properly set (deliverability), dedicated sending IPs warmed up, URL shorteners avoided (flagged).
3. **Payload tiers** (escalate only if authorized): credential harvest page (clone org login, HTTPS, valid cert) → macro doc → link to landing.
4. **Campaign metrics**: sent / opened / clicked / submitted / creds validated. Report EACH — click-through is the real risk indicator, not submissions.
5. **Timing**: Tue-Thu 09:00-11:00 local typically highest engagement; avoid month-end (finance busy, but BEC tests may target it deliberately — RoE call).

## Vishing
- Same pretext as the phishing campaign; call targets who clicked but didn't submit ("IT follow-up").
- OTP/2FA social-engineering: "read me the code you just received" is the highest-impact finding possible — proves full account takeover.
- Record calls ONLY if RoE allows (consent laws vary by jurisdiction — check two-party consent).

## Physical
- Tailgating at badge-controlled entrances, badge cloning (Proxmark), lock picking, dumpster diving, USB drops.
- Escort rules and safety: never confrontation, abort word agreed, no bypassing locks that damage property.
- Environmental findings count: whiteboards, unlocked screens, unattended reception, printed creds.

## Reporting angle
- Map each success to impact: "phished creds → mailbox access → BEC risk" with ATT&CK T1566/T1534/T1556.
- Compare results by department — one department at 40% submission rate = targeted training recommendation.
- NEVER store harvested real credentials beyond the engagement; hash/redact in report, deliver for reset.

## GoPhish quick start
```
gophish serve            # admin panel, default :3333
# landing page: clone org login (view-source → save), strip JS noise
# email template: org branding + tracking pixel
# sending profile: SMTP creds per RoE (dedicated domain, not client's)
```
Results CSV → engagement state as findings, not spreadsheet-only.
