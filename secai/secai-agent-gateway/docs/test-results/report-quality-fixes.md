# Report quality fixes — validation

Date: 2026-10-04. Product commits `96d6d3f`, `f750391`, `2b2b3e8`; integrated as `6cb390b`, `c9353b5`, `54cd3ad` on `integration/report-feature`. No main merge, push or deployment.

- Regression tests reproduced URL punctuation/case semantics, scoped edit preservation, overlapping section names, extra peer-level headings, retained negative observations and conflicting counts before fixes.
- Coder focused report/session suite: 61 passed. Independent reviewer report suite: 27 passed. Root reran report/session suite on integrated tree: 61 passed in 6.77s.
- Root's first invocation stopped during collection (2 errors) because default PoC DB tried `/srv/exploitdb`; rerun used task-specific `/tmp/secai-report-final-check` DB paths. No product/config changes were used to bypass tests.
- Coder full suite was bounded at 120 seconds and exited 124 around 22%, with six previously recorded external Bughunt/PoC fixture failures. No full-suite PASS claim. Independent gateway-only run was also interrupted after 32 passing tests. Failure names and environmental baseline are in `report-mvp-baseline.md`.
- Independent scoped code review PASS after fixing its URL and section-selection findings; see `../reviews/report-quality-fixes-review.md`. Semantic guards are narrow and do not prove arbitrary prose grounding.
- Final frozen live run: 18 generated reports + 18 revisions, 37 model attempts, DuyTa/Cyber-F1-AWQ. Original 12 cases/templates unchanged; six supplemental cases frozen before fixes. Code files api.py/validation.py/store.py byte-identical between evaluated checkout and integrated tree.
- Root independently opened final SQLite read-only in a separate process: all 36 revision contents exactly match accepted API outputs, 18 reports/36 revisions, database and backup integrity_check OK.
- Persistent artifact index: `/home/ict/secai-report-evidence/INDEX.md`. Final run: `/home/ict/secai-report-evidence/report-mvp-quality-fix-03/`. Earlier runs remain intact, including failures and raw retries.
- Semantic quality: FAIL under the frozen per-cohort/template criteria. Original compliance generation 62.50%; supplemental incident edits 68.75%; false absence of known source facts remains. Combined score 116/144 (80.56%) for both generation and edits does not override failing groups. See ../reports/report-mvp-quality-final-judgement.md.
