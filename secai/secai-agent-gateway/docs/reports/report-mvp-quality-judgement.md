# Offline quality judgment: Vietnamese security reports

**Verdict: FAIL** under the frozen ≥70% per-template and per-operation rule. This is an **independent offline LLM judgment by gpt-6-sol**, not a human review. The generator was **DuyTa/Cyber-F1-AWQ**; this judge did not call it. The 12-case manifest is synthetic and is not customer acceptance evidence.

| Template | Generation, planned 4 | Edit, planned 4 | Result |
| --- | ---: | ---: | --- |
| Scan | 29/32 (90.62%) | 30/32 (93.75%) | PASS |
| Incident | 26/32 (81.25%) | 27/32 (84.38%) | PASS |
| Compliance | 19/32 (59.38%) | 21/32 (65.62%) | FAIL |
| **Overall** | **74/96 (77.08%)** | **78/96 (81.25%)** | **FAIL** |

Each accepted generation and edit was judged on coverage, semantic accuracy, clarity and style, each 0–2. The planned denominators include `compliance-complete`: generation returned HTTP 422 after two rejected drafts, and its edit was skipped. Both score 0/8. Thus 11/12 generations and 11/12 planned edits were accepted. Conditional on the 11 successful generations, edits scored 78/88 (88.64%); the compliance conditional score was 21/24 (87.5%). These conditional figures do not change the end-to-end verdict.

All 22 accepted outputs have the required three section headings in order. All 22 accepted response texts matched their readbacks, and the 11 edited reports retained the original revision. One edit (`scan-complete`) was unchanged: its recommendation was already one sentence, so the no-op meets the length request, but its vague recommendation still loses coverage credit.

The separate factual hard gate **fails** for both versions of `compliance-noisy`: the scope associates CT-05 with DB-LAB-3 (203.0.113.33), although the source associates that system and IP only with CT-04. No other unsupported critical CVE, IP, URL, hash or important numeric claim was found in the accepted reports. The supplied year 2099, CVEs and documentation IPs were treated as valid synthetic source facts.

Representative content errors:

- `incident-complete` edit drops the source-backed observation that no data loss was seen and does not shorten its timeline.
- `incident-conflicting_long` generation and edit retain uncertainty but omit the source's specific disagreement between **2 and 3 accounts**.
- `incident-noisy` generation says handling is unknown despite listing the blocked session; its edit corrects this.
- `compliance-conflicting_long` calls conflicting evidence “no data” in the conclusion. Both records and the absent final approval remain visible, but the distinction matters.

The exact per-case scores, reasons and hard issues are in [judgement.json](/home/ict/secai-report-quality-review-wt/evals/report_mvp/judgement.json). Full saved original and edited reports, plus the two rejected raw drafts, remain in the [reports index](/home/ict/secai-report-evidence/report-mvp-883634a-01/reports/INDEX.md). Source results remain in [results.json](/home/ict/secai-report-evidence/report-mvp-883634a-01/results.json); the frozen [manifest](/home/ict/secai-report-quality-wt/secai/secai-agent-gateway/evals/report_mvp/manifest.json) has SHA-256 `3356be9e1a9fdf690fe001a3d9aa718b4f8a8181e5a6ef7b83fc7f4b904ced3e` (manifest commit `883634a6b155d0516f9ee2605e484ccf258312ae`; source commit `db071bd1ecbcfa6c46fd47512c71208865fd61f3`).

Recorded endpoint latency, with linear quantiles at index `(n−1)×p`: generation p50 **1,475.6 ms**, p95 **2,376.8 ms** (12 terminal requests, including the failed request); edit p50 **1,389.1 ms**, p95 **1,955.1 ms** (11 completed edits). Token and cost counts were unavailable. This small synthetic set supports no precision, recall, confidence or population claim; it has no hash example and no near-context long input.
