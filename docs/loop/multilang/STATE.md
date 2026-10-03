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
- Assessment: /home/ict/secai-multilang-assessment-wt, feature/multilang-assessment; @8e53a00a8adf710dd630196de8341a2585e5d97a; review4FAIL1Medium, four prior findings closed; fix4 active.
- Integration: /home/ict/secai-multilang-integration-wt, integration/multilang-translate; Fresh Astra correct-dependency replay CLI19729 active; runtime929b19997116a2d7b193ebc44912ef1fdcaf0feb, assessment excluded pending review5.
- Protected import current observed: bfab8ebc96e25b23325fd72a1d3bf034e9aea315; OCR: ff311c12f97a1dd453495b0ff2b9ca269a49aba2. Their source/state/log read-only. Recheck before integration.
- Pre-existing protected-branch cross-conflicts observed at365e9e0/99cd0a2: profiles.yaml and translation/settings.py. Not caused by our code; refresh current snapshots at integration.

## Evidence and counters
| Task | Current status | Fix cycles | Vanguard returns |
|---|---|---:|---:|
| T0 preflight | CPUbaseline905pass19fail1skip0error; exact failureIDs in assessment baseline.json | 0/5 | 0/3 |
| T1 criteria | CRITERIA_READY, six documents committedb94e5ea | 0/5 | 0/3 |
| T2 assessment | Review4FAIL1Medium residual protocol marker; findings9→6→5→1, fix4 active | 4/5 | 0/3 |
| T3/T4 core | f6b7834 spec/codePASS round4,17independentcasespass; author139targetedpass | 3/5 | 0/3 |
| T5 FE | 21test/build/audit0; code reviewed, FE-03 CDN finding fixed and re-reviewPASS | 1/5 | 0/3 |
| T6 adapter | Conditional Gate C, not created or run | 0/5 | 0/3 |
| T7 integration tests | Runtime replay968pass19samefail1skip; combined0097da3 1075pass19samefail1skip; assessment excluded | 0/5 | 0/3 |
| T8 Vanguard | Finalcore11ec898:23pass,0new actionablefindings; live/scanners/composedreleaseBLOCKED | 0/5 | 0/3 |
| T9 AstraB integration | Runtime929b199 tested correct deps; combined replay complete; assessment excluded pending review5; no main merge | 0/5 | 0/3 |
| T10 API/finaldocs | API docs788da97 complete; finalreport pending | 0/5 | 0/3 |

FE browser: T3 mock plus real HTTP/fakeLLM Playwright evidence, final runtime43e5f22 replay on vi→zh-Hans,ja→vi,vi→ar with CVE/IP; inspected desktop/mobile RTL screenshots, exact copy API stub (not OS permission). Source identical929b199. See browser-http.md. No model-quality claim.

Static analysis: runtime43e5f22 OSS Semgrep1.179.0,9units7complete2partial,14files0findings. Assessmentbf06bc4 six units4complete2partial,7files0findings; replay8e53a00 complete: six units4complete2partial,7files0findings. Third-party rule compile errors recorded; not blanket security PASS. Dependency gap closed in /tmp/secai-multilang-final-env,67compatiblepackages incl olefile0.47,17declaredrequirements+metriclockverified,baseline57versions unchanged.

Serving inventory: authenticated modelID confirms DuyTa/Cyber-F1-AWQ; sole cached snapshot7e7e6718ce268d2b00adc6e3ea43fcd33452f516 with qwen3_5_moe/compressed-tensors. Process has no --revision, so cache-backed inference is not attestation of loadedrevision. Matching config endpoints read from existing secai-docker settings in memory; no .env copied. Linch1.2.1 in isolatedenv; sharedvenv untouched.

## Current orchestration
- Native new-thread limit reached; independent reviewers/warden launched as fresh Codex CLI sessions with specified Sol model and separate reviewcheckouts. No review waiver. Read-only bwrap first failed beforeinspection; retry uses existing unrestricted environment with strict read-only task instructions.
- Core reviewclosedPASS; Vanguardfinalcore replaycomplete23pass; Vanguard CPUcheckpointcomplete; assessmentreview4FAIL1Medium, fix4 active; FE docs complete.
- Next: finish independent code reviews/fixes, Vanguard CPU/security tests, fresh Astra B integration with exact-SHA fullsuite and protectedbranch simulations; API docs. Keep all languageflagsOFF. Mainmerge requires later user confirmation per spec.
- Pending release blockers: verified servingtokenizer/template/revision, licensed/reference corpus+independent scoring/holdout,18-direction/3round quality, margins/perf, security/integration gates evidence.

No quality percentage, production readiness, main merge, push or deployment has been claimed. No new language enabled in production.

Other import task STOPPED/BLOCKED by its Warden after two reviewrounds with2High unchanged. Do not reopen/fix its source. Combined simulation may record known defects; no acceptance claim for file/OCR.
