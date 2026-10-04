# Final offline judgement: Vietnamese security reports

**Verdict: FAIL.** The independent judge was **gpt-6-sol**, an offline LLM, not a human reviewer. The generator was **DuyTa/Cyber-F1-AWQ**. All 18 synthetic cases completed with accepted generation and edit responses; no target model calls were made for this judgement.

The rule is **≥70% for each template and operation**, applied to original and supplemental cohorts separately, plus a factual/structure/persistence hard gate. Original compliance generation is **20/32 (62.50%)**, so the threshold fails. The factual hard gate also **FAILS**; the score cannot override it.

| Cohort | Template | Generation | Planned edit | Threshold |
| --- | --- | ---: | ---: | --- |
| Original 12 | Scan | 30/32 (93.75%) | 31/32 (96.88%) | PASS |
| Original 12 | Incident | 26/32 (81.25%) | 26/32 (81.25%) | PASS |
| Original 12 | Compliance | 20/32 (62.50%) | 24/32 (75.00%) | FAIL |
| Original 12 | Overall | 76/96 (79.17%) | 81/96 (84.38%) | PASS |
| Supplemental 6 | Scan | 13/16 (81.25%) | 12/16 (75.00%) | PASS |
| Supplemental 6 | Incident | 12/16 (75.00%) | 11/16 (68.75%) | FAIL |
| Supplemental 6 | Compliance | 15/16 (93.75%) | 12/16 (75.00%) | PASS |
| Supplemental 6 | Overall | 40/48 (83.33%) | 35/48 (72.92%) | PASS |
| Combined 18 | Scan | 43/48 (89.58%) | 43/48 (89.58%) | PASS |
| Combined 18 | Incident | 38/48 (79.17%) | 37/48 (77.08%) | PASS |
| Combined 18 | Compliance | 35/48 (72.92%) | 36/48 (75.00%) | PASS |
| Combined 18 | Overall | 116/144 (80.56%) | 116/144 (80.56%) | PASS |

Every cohort has **all planned generations and edits accepted**: 12/12 and 12/12 for the original cohort, 6/6 and 6/6 for supplemental, 18/18 and 18/18 combined. Thus conditional edit denominators equal planned denominators: **81/96**, **35/48**, and **116/144**, respectively. Failed generations and skipped edits would score zero in planned denominators; there were none in this run.

## Material defects

- **False absence of known facts:** `compliance-complete` generation marks all sections unknown despite supplied scope, evidence, statuses, and count; its edit recovers statuses but leaves scope/evidence unknown. `compliance-missing` similarly marks known APP-LAB-2 scope/IP and backup-record context unknown. `scan-url-punctuation` has a correct source URL but falsely leaves its summary unknown.
- **Confirmed handling presented as unknown:** `incident-conflicting_long` and `incident-2-vs-3` both say “Chưa có thông tin” in Xử lý although the source confirms isolation. Their conflicting times or 2-vs-3 account figures otherwise remain distinct; the edit to `incident-2-vs-3` also grows rather than shortens the impact text.
- **Unmet edit instructions:** `incident-complete` does not shorten the timeline; `compliance-cross-control-ip` leaves Kết luận as two unchanged bullet fragments instead of two sentences; `compliance-section-only-edit` leaves its requested conclusion rewrite unchanged. `scan-url-punctuation` expands Đề xuất from one sentence to two bullets despite the request to make it shorter. Unchanged edits were credited where the original already met the request, including `scan-complete` and `scan-cross-host-ip`.
- **Omitted source context:** `compliance-section-only-edit` omits the total of two controls and evaluation date in both versions. `incident-missing`, `incident-conflicting_long`, `incident-2-vs-3`, and `incident-section-only-edit` omit their event identifier and IP. The latter does preserve 08:15/08:45, one affected account, and unknown data-loss status.
- **Entity and uncertainty checks:** Both cross-host IP assignments are correct. `compliance-cross-control-ip` keeps CT-11 with APP-LAB-11/203.0.113.51 and CT-12 with DB-LAB-11/203.0.113.52. `compliance-noisy` does not attach CT-04’s IP to CT-05. The 2-versus-3 account dispute is not replaced with a settled count. No invented CVE, IP, URL, hash, or numeric value was found.

All 36 accepted outputs have the three required level-two headings in order; document titles were allowed. All accepted content matched saved readback; edited reports retained revisions 1 and 2 with the original preserved. The two explicit section-only edit cases kept their non-target sections byte-identical. These checks pass, but the factual gate failures above determine the hard-gate verdict.

## Comparison and provenance

The original 12 cases match the baseline manifest case values exactly. This judgement gives original generation **76/96** and edit **81/96**, versus the [published baseline summary](/home/ict/secai-report-evidence/report-mvp-883634a-01/quality-summary.md) at **74/96** and **78/96**. Original compliance scores are **20/32** and **24/32**, versus baseline **19/32** and **21/32**. The baseline had 11/12 accepted generations and 11/12 edits; this run has 12/12 of each. Score changes also reflect separate single-pass LLM judging variation and must not be read as measured population improvement.

Frozen manifest commit: `9dde667`; runner commit: `711c732`; actual product code commit from run metadata: `96a9c16e26c049cb90c80470ca0e30fd8b354b37`. Manifest SHA-256: `23b33f114e87513a4f434369d16b3369deab00d83bb9bef01a49471ea59b553f`. The [run manifest](/home/ict/secai-report-evidence/report-mvp-quality-fix-03/manifest.json), [results](/home/ict/secai-report-evidence/report-mvp-quality-fix-03/results.json), and [saved report index](/home/ict/secai-report-evidence/report-mvp-quality-fix-03/reports/INDEX.md) provide read-only evidence. [Per-case scores and hard issues](/home/ict/secai-report-quality-review-final-wt/evals/report_mvp/judgement-final.json) are in JSON.

This small synthetic dataset provides no evidence of population accuracy or customer acceptance. The judgement was not a human review. Year 2099 and synthetic identifiers were treated as valid source facts.
