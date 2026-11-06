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

## Follow-up: four report review defects

Branch: `integration/report-feature`, patch on `17c7c61` (uncommitted).

- Added four regression tests; before the patch all four failed for the reviewed reasons.
- Fixed numbered section matching, ordered-list ordinal handling, source facts inside code fences, and explicit whole-report edit scope. Negative controls still reject invented body quantities and cross-control IP reassignment; negated whole-report instructions preserve scoped behavior.
- Focused verification: `PYTHONDONTWRITEBYTECODE=1 POC_CATALOG_DB=/tmp/secai-report-review-fix-catalog.db PYTHONPATH=src /home/ict/secai-translate-import-wt/.venv-import/bin/python -m pytest -q -p no:cacheprovider tests/test_report_v2.py tests/test_projects_sessions.py` → **77 passed in 7.96s**. The numbered-template API test covers import, create, generate, edit and readback of both saved revisions.
- Read-only validation replay against `/home/ict/secai-report-evidence/report-mvp-quality-fix-06/reports.db`: **36 saved revisions accepted, 0 failures, 0 model calls**. This checks compatibility of saved outputs, not new model quality; original evidence remains unchanged.
- Broader backend run with the same environment, `timeout 180 ... -m pytest -q -p no:cacheprovider --maxfail=6`, stopped after **104 passed, 1 skipped, 6 failed in 72.27s**. Not a full-suite PASS. Log: `/tmp/secai-report-review-fix-fullsuite.log`.
- Failing tests: `tests/test_bughunt_phase0.py::test_bugbounty_root_and_arsenal` (missing `/home/ict/AIEngines/secai/claude-bug-bounty`), and `tests/test_chat_poc_drilldown_regression.py::{test_uc2a_search_log4shell_surfaces_log4j_scan_with_valid_ref,test_uc2b_fetch_same_run_returns_real_grounded_source_code,test_uc2c_ref_reused_in_a_different_turn_run_is_rejected,test_uc2d_resear_in_new_run_yields_a_fresh_fetchable_ref,test_uc4_fetch_budget_enforced_within_a_single_chat_run}` (real catalog lacks required Log4Shell/log4j-scan data).
- No model calls, deployment, commit or merge performed for this patch.
