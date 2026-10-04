# Report feature — execution state

## Current handoff — quality fixes completed, quality gate still FAIL

2026-10-04: integrated product `54cd3ad` contains the reviewed URL comparison, prompt grounding, scoped section edits and structure fixes. 61 report/session tests PASS on the integrated tree; independent code review PASS for scoped fixes (review commit `446c634`). Full suite remains incomplete; see `../../test-results/report-quality-fixes.md`.

Final frozen live run `report-mvp-quality-fix-03` used the identical original12 plus supplemental6 and actual DuyTa/Cyber-F1-AWQ. All18 generations/edits accepted and persisted, 18 reports/36 revisions; root independently verified content equality and DB/backup integrity. All earlier run artifacts are retained.

Independent offline LLM judgment (not human): original scan93.75%/96.88%, incident81.25%/81.25%, compliance62.50%/75.00% (generation/edit). Supplemental incident edits68.75%. Combined18 scores80.56%/80.56% do not override failing groups. No invented critical identifiers/numbers or cross-entity IP issue observed in final run, but known facts are still incorrectly labelled unavailable. Overall quality FAIL; no claim of full acceptance or deployment. Full report: `../../reports/report-mvp-quality-final-judgement.md`. Durable full reports and scores: `/home/ict/secai-report-evidence/INDEX.md`.

Remaining measured issues: preserve all supplied facts in relevant sections, distinguish known handling from unknown handling, follow shortening/rewrite instructions. Do not remove failed runs or relax rubric to claim pass. Feature remains opt-in/default OFF; no main merge/push/deployment.

## Historical stage

2026-10-04: MVP IMPLEMENTATION + INDEPENDENT REVIEW + ISOLATED INTEGRATION COMPLETE. Live content quality measured on 12 synthetic cases: FAIL for compliance under the per-template 70% threshold and factual hard gate. Latest user scope prioritizes quick usable functionality, sufficient tests and no perfectionism. User-authorized synthetic inference completed using existing DuyTa/Cyber-F1-AWQ serving; no rental or deployment performed.

## Live quality measurement — 2026-10-04

- Frozen manifest `883634a`, evaluated product `db071bd`; runner evidence commit `6ad34e5`, independent offline LLM judge (gpt-6-sol, not human) `aeabd8a`.
- Generation: scan 29/32 (90.62%), incident 26/32 (81.25%), compliance 19/32 (59.38%); overall 74/96 (77.08%).
- Planned edits: scan 30/32 (93.75%), incident 27/32 (84.38%), compliance 21/32 (65.62%); overall 78/96 (81.25%). Conditional edit score 78/88 (88.64%).
- Verdict FAIL: compliance below 70%; accepted compliance-noisy incorrectly associates CT-05 with CT-04's system/IP. One HTTP422 caused by URL terminal punctuation validator false positive stays zero in denominator. No product fixes or reruns during this measurement.
- 11 reports and 22 revisions durably stored. Parent independently checked all 22 contents against SQLite from a separate process; integrity_check OK. Readback/history checks pass. All accepted Markdown plus two rejected raw drafts saved.
- Durable run: `/home/ict/secai-report-evidence/report-mvp-883634a-01/`; `reports/INDEX.md`, `reports.db`, `reports.db.backup`, `results.json`, `manifest.json`, `judgement.json`, `quality-summary.md`. Do not delete as temporary output.
- Detailed judgement: `../../reports/report-mvp-quality-judgement.md`; run evidence: `../../reports/report-mvp-live-quality-evidence.md`.
- Small synthetic single-run sample; no customer acceptance, hash coverage or near-context long-input claim. Earlier pending sections below are historical.

## Final handoff

- Integrated code `2eff70f`; integration evidence/documentation `99abb73`, branch `integration/report-feature`, worktree `/home/ict/secai-report-integration-wt`.
- Backend head `f43aacd`, frontend head `864c1b8`; independent SOL recheck report `b625fe9` confirms R1–R6 resolved on candidate `d2e8bda`. Fresh ASTRA B then merged and verified exact reviewed code.
- Integrated checks:48 focused tests PASS, FE build PASS, diff check PASS. Full suite INCOMPLETE with six existing dependency/fixture failures, not passed.
- Browser smoke with real API/SQLite and visibly mocked LLM: form/save/generate/edit/history + Markdown draft import PASS. After fix, edited report retained original CVE/IP; temporary Vite and mock backend processes stopped.
- Reports: `integration-result.md`, `../../reviews/report-mvp-review.md`, `../../test-results/report-mvp-baseline.md`, `../../api/report-mvp.md`.
- Enable explicitly: backend REPORT_FEATURE_ENABLED=true and nonempty dedicated REPORT_API_KEY; frontend VITE_REPORT_FEATURE_ENABLED=true. Defaults remain OFF. Internal shared workspace only.
- Import/OCR merge simulations clean; multilingual FE has seven known add/add conflicts requiring coordination before combining WIP. Other branches/main and original workspace untouched. No push/main merge/deployment.
- Work finished for requested MVP implementation scope. Quality scoring is complete for the frozen synthetic dataset; compliance requires fixes and a new separately recorded run; advanced full-spec functionality remains deferred. One independent review/fix/recheck cycle; no unattended agents or GPU workloads required to keep this state.

## Current MVP authority (supersedes historical full-plan sequencing below)

- Criteria checkpoint `1f0d611` (historical full criteria `de20361`), `docs/criteria/report-mvp.md` in criteria worktree.
- Scope: SQLite template storage; form and DOCX/MD/TXT draft import; template-driven generation; natural-language edits with report revisions; FE; focused tests/full regression evidence; independent review.
- Content quality >=70% separately measured; mock tests are not live quality evidence. Security, critical factual grounding and legacy compatibility are hard checks. No 70%-passing test-suite shortcut.
- Dedicated REPORT_API_KEY protects all new routes for an internal shared workspace; do not expose the global ADMIN_API_KEY to this UI. Multi-user/tenant auth is deferred, not claimed complete.
- Advanced lifecycle/catalog/LoRA/30x3 campaigns deferred. No need to wait for their historical gates to implement MVP.
- Backend SOL: `/home/ict/secai-report-core-wt`, `feature/report-core`, owns new backend + minimal app/config hook.
- Frontend SOL (reused preflight agent): `/home/ict/secai-report-fe-wt`, `feature/fe-devspace-report`, owns source snapshot/new UI only.
- Warden runs legacy local baseline in its own worktree in parallel. Root requested no shared virtualenv mutations or uncontrolled external model calls.
- Native agent thread limit encountered; reuse finished SOL as frontend coder. Independent review will use a fresh-context reviewer (fresh CLI if native thread limit persists).

### MVP checkpoint evidence

- Backend `b2213e1`: 41 focused report/session tests passed; full suite attempt interrupted, not PASS.
- FE `864c1b8`, source snapshots `419f90c`/`9a2aeef`: npm ci/build passed. Existing snapshot dependency advisories:1low/1moderate/1high; feature does not change package lock.
- Baseline Warden `909b0fd`: 925collected, distinct303pass/6fail/1skip completed;1interrupted and614without result. Six failures concern missing external Bughunt tree/unseeded PoC catalog. Full baseline INCOMPLETE.
- Review candidate `0a0dbdd` in review/report-mvp combines FE/backend. Fresh CLI SOL independent review in progress, no coder history;7new report tests and5selected old gateway tests pass at review time.
- Root browser smoke passed core form/generate/edit/history/MD draft import with real SQLite/API and MOCK LLM. See browser-smoke.md; no live quality score.
- First independent findings being fixed: encrypted DOCX unhandled exception; edit drops prior CVE facts. Review/fix cycle1, not yet final PASS.

## Authority and isolation

- Governing request: `../../plans/report-feature-user-spec.txt` (verbatim user attachment).
- Base: `main` and fetched `origin/main`, SHA `ce9cd47ee1273018fec1c5cbab944a86d68d6ed5`; no WIP branch used as base.
- Git root contains several projects. Original checkout and translation/OCR state files remain untouched.
- This worktree: `integration/report-feature`. Criteria: separate `chore/report-criteria` worktree.
- Root orchestrates only; SOL implements. Each writer receives a separate worktree and explicit ownership. Independent review/TEST/Vanguard/Astra B contexts required.
- Available concurrency: root plus 3 agents. GPU work must be serialized; Warden reviews checkpoints rather than monopolizing a slot.

## Agents

| Agent | Responsibility | Status |
|---|---|---|
| report_preflight / SOL | Read-only branch overlap, code ownership, auth, FE and serving survey | Running |
| report_criteria_astra_a / ASTRA | Per-task criteria, hard gates and evidence requirements | Running in criteria worktree |

## Pending user inputs (asked together once)

User clarification: templates must be stored in a database; users add report templates through the UI and edit generated reports with natural-language prompts. This authorizes report-edit prompts as a narrow addition to the initial predefined-prompt-only scope. Report edits must not silently change shared templates; propose revision history and preserve factual grounding. Database selection is delegated; actual template examples, trusted identity and GPU permissions remain unknown.

User confirmed BOTH form-based template authoring and file import. Supported initial import formats follow original scope: DOCX/MD/TXT. Imported content becomes a reviewable, editable draft before publication; a file upload is not publication approval.

Preflight documentation checkpoint: `59b8f9a` on `feature/report-assessment`, `docs/reports/report-preflight.md`. Contains snapshot of 26 attached branches, full committed overlap lists, auth/FE blockers and DB proposal. Documentation diff check passed; no test/benchmark run.

State files are namespaced under `docs/loop/report/` to avoid future merge collisions with the translation workstream; this is a path-only isolation adjustment to the requested STATE/LOG artifacts.

- Actual customer templates/reference reports and initial report/prompt catalog.
- Roles and trusted identity integration for template administration and report ownership.
- Dev instance, maximum Vast budget and approved sanitized/synthetic external payloads. No approval inferred from previous translation tasks.
- Active branches and common base overrides; Git discovery used in meantime.
- Defaults: Vietnamese, Markdown/structured JSON, DOCX/PDF export deferred.

## Gates and evidence

- Criteria must finish before any code/benchmark. Soft quality thresholds require baseline followed by user confirmation; hard thresholds remain as specified.
- A/B/C/D: NOT RUN. Adapter branch must not be created before Gate C prerequisites.
- Existing/new tests: NOT RUN. Historical other-task results are not this task's baseline.
- No main merge until final user confirmation. Integration only after required independent review/test/Vanguard passes.
- Review/fix counts: all 0/5; Vanguard returns 0/3. No cost incurred by this task on Vast.
- Overlap map pending preflight. Known FE discrepancy: tracked sibling FE has local deletions; nested FE is untracked. Do not overwrite or stage either.

## Next actions

1. Finish preflight and per-task criteria; preserve exact active-branch SHAs and overlap evidence.
2. Produce local architecture/API contract and assessment plan; identify unresolved high-risk identity choices.
3. Run isolated existing-suite baseline after criteria readiness; prepare assessment only when inputs permit. External inference remains blocked pending data/resource approval.
4. Follow user-specified ASSESS/gates → CODE → independent REVIEW → TEST → Vanguard → integration → API handoff.
