# Multilang — STATE

Updated2026-10-04. Phase: CPU_IMPLEMENTED / INDEPENDENT_REVIEW. User authorized execution; this is not quality acceptance or release approval.

## Binding decisions
- Runtime orchestrator and translator: deployed DuyTa only. No alternative translator/fallback/postediting model.
- New16 vi↔zh-Hans/ja/ko/fr/de/es/ru/ar; old en↔vi unchanged. ArabicMSA, main base.
- Quality sample pass rate≥70% EACH direction EACH round; off-target≤2%, protected entities100%; live evidence NOT RUN. en↔vi noninferiority/performance margins pending baseline.
- Auxiliary models max3 TOTAL (OCR/LID/QE), sequential. Other OCR owner reports3used (allowance exhausted); this task loaded0. No restart/unload/change to their services.
- Existing Vast rental confirmed. No additional rental authorized. OCR owner reports own GPU job/tunnels stopped at17:50UTC; no new auxiliary model allowed. Live inference still requires verified lease/serving/corpus; no live run authorized by this status update. CPU work proceeds flagsOFF.
- New v3 JSON-only text API, anonymous/stateless, separate namespace/composition; no file/OCR or legacy source edits. Frozen contract: docs/api/multilang-contract.md; criteria: docs/criteria/multilang-*.md.

## Worktrees and revisions
- Planning: /home/ict/secai-multilang-wt, feature/multilang-translate; plan docs/plans/multilang-translate.md; source attachment copied verbatim.
- Base main snapshot: ce9cd47ee1273018fec1c5cbab944a86d68d6ed5.
- Core: /home/ict/secai-multilang-core-wt, feature/multilang-core @f6b7834d5fb215075cb0cdbc448e3b538d93f7b3.
- FE: /home/ict/secai-multilang-fe-wt, feature/fe-devspace-multilang @788da975cf9eed0e0bf72089fd137e00fd8ad2b2; canonical nested snapshot baseline8b41f62, no sibling deletion.
- Assessment: /home/ict/secai-multilang-assessment-wt, feature/multilang-assessment; @368afcd7e9ee02c3b9ad850ef4229bdc45cc6d9f; independentround2active.
- Integration: /home/ict/secai-multilang-integration-wt, integration/multilang-translate; Fresh AstraB CLI18640 active: reviewedruntimecore/FE/securitytests/docs integration, assessmentexcludedpendingreview.
- Protected import current observed: bfab8ebc96e25b23325fd72a1d3bf034e9aea315; OCR: ff311c12f97a1dd453495b0ff2b9ca269a49aba2. Their source/state/log read-only. Recheck before integration.
- Pre-existing protected-branch cross-conflicts observed at365e9e0/99cd0a2: profiles.yaml and translation/settings.py. Not caused by our code; refresh current snapshots at integration.

## Evidence and counters
| Task | Current status | Fix cycles | Vanguard returns |
|---|---|---:|---:|
| T0 preflight | CPUbaseline905pass19fail1skip0error; exact failureIDs in assessment baseline.json | 0/5 | 0/3 |
| T1 criteria | CRITERIA_READY, six documents committedb94e5ea | 0/5 | 0/3 |
| T2 assessment | Round2changesrequested6remaining; fix2active fordurablelease/custody/provenance/margin | 2/5 | 0/3 |
| T3/T4 core | f6b7834 spec/codePASS round4,17independentcasespass; author139targetedpass | 3/5 | 0/3 |
| T5 FE | 21test/build/audit0; code reviewed, FE-03 CDN finding fixed and re-reviewPASS | 1/5 | 0/3 |
| T6 adapter | Conditional Gate C, not created or run | 0/5 | 0/3 |
| T7 integration tests | NOT RUN on combined finaltree | 0/5 | 0/3 |
| T8 Vanguard | Finalcore11ec898:23pass,0new actionablefindings; live/scanners/composedreleaseBLOCKED | 0/5 | 0/3 |
| T9 AstraB integration | CPUruntime checkpoint running, assessmentexcluded; no main merge | 0/5 | 0/3 |
| T10 API/finaldocs | API docs788da97 complete; finalreport pending | 0/5 | 0/3 |

FE browser: mocked T3 checks recorded separately; real HTTP/fakeLLM Playwright smoke on corebfe4e6b passes vi→zh-Hans,ja→vi,vi→ar,RTL/literal/copy,desktop/mobile screenshot checks. Screenshots inspected; repeat finalSHA needed. See browser-http.md. No genuine model quality claim.

Serving inventory: authenticated modelID confirms DuyTa/Cyber-F1-AWQ; sole cached snapshot7e7e6718ce268d2b00adc6e3ea43fcd33452f516 with qwen3_5_moe/compressed-tensors. Process has no --revision, so cache-backed inference is not attestation of loadedrevision. Matching config endpoints read from existing secai-docker settings in memory; no .env copied. Linch1.2.1 in isolatedenv; sharedvenv untouched.

## Current orchestration
- Native new-thread limit reached; independent reviewers/warden launched as fresh Codex CLI sessions with specified Sol model and separate reviewcheckouts. No review waiver. Read-only bwrap first failed beforeinspection; retry uses existing unrestricted environment with strict read-only task instructions.
- Core reviewclosedPASS; Vanguardfinalcore replaycomplete23pass; Vanguard CPUcheckpointcomplete; assessmentfix2active; FE docscomplete.
- Next: finish independent code reviews/fixes, Vanguard CPU/security tests, fresh Astra B integration with exact-SHA fullsuite and protectedbranch simulations; API docs. Keep all languageflagsOFF. Mainmerge requires later user confirmation per spec.
- Pending release blockers: verified servingtokenizer/template/revision, licensed/reference corpus+independent scoring/holdout,18-direction/3round quality, margins/perf, security/integration gates evidence.

No quality percentage, production readiness, main merge, push or deployment has been claimed. No new language enabled in production.

2026-10-04 update: core42f8d40 fixes T7-01..08 (130 targetedpass), independent round2 running; core1/5. Assessmentf31a86c review R-01..09 FAIL, fix1/5 active. FE d7c7eab realHTTP/fakeLLM smokePASS; API docs underway. Browser fallback Playwright explicitly permitted after T3 unavailable. No liveGPU/mainmerge/deploy.

Other import task STOPPED/BLOCKED by its Warden after two reviewrounds with2High unchanged. Do not reopen/fix its source. Combined simulation may record known defects; no acceptance claim for file/OCR.
