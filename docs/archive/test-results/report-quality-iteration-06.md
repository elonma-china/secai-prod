# Report quality iteration 06 — verification

2026-10-04. Final integrated product `f4d8ba2` includes source-first JSON prompts, bounded compliance-outcome checks, source control/IP attribution, duplicate event checks, scoped edit preservation and known-action/negation handling. Feature remains opt-in and no deployment/main merge/push was performed.

## Tests and independent code review

- Final integrated tree: `tests/test_report_v2.py tests/test_projects_sessions.py` **73 passed in 8.71s**, using existing import-worktree Python and task-local DB paths. No dependency or global environment mutation.
- Independent final reviewer: report tests **39/39 passed**, review commit `a9fa0fe`, including unknown vs confirmed source action counterexamples. See `../reviews/report-quality-fixes-review.md`.
- Bounded full suite on immediately preceding product `ab2f45c`: **104 passed, 1 skipped, 6 failed**, stopped at `--maxfail=6` in 71.47s. The six failures are the previously recorded missing Bughunt tree/PoC catalog fixtures; full suite is NOT PASS. Log retained at `/home/ict/secai-report-evidence/report-mvp-quality-fix-06/full-suite.log`; names and baseline are in `report-mvp-baseline.md`.

## Live inference and final-code replay

- Frozen manifest SHA256 `23b33f114e87513a4f434369d16b3369deab00d83bb9bef01a49471ea59b553f`, original12 and supplemental6 unchanged. DuyTa/Cyber-F1-AWQ, temperature0.2, max_tokens3000. All18 generations and18 edits accepted;39 model attempts (three rejected first drafts retained).
- Live product `3c53d42` (upstream `ab2f45c`) preceded the narrow negation fix. Final product `ed8925f` (upstream `a9641b3`, integrated `f4d8ba2`) was checked with offline full-router replay of all39 model candidates: identical model system/user messages, request statuses, accepted contents, attempt counts and persisted revisions in every case. Model calls during replay:0. This is not a claim that another live inference run occurred on the final SHA.
- Root verified api.py/validation.py/store.py byte-identical between integrated code and final replay checkout. A separate read-only process confirmed all36 live SQLite revision contents equal API outputs and database/backup integrity OK.
- Each live and replay run retains18 reports/36 revisions, Markdown, raw rejected drafts, source/prompt metadata, JSON results and SQLite backup. Root artifact index: `/home/ict/secai-report-evidence/INDEX.md`.

## Evaluation scope

Independent offline LLM judging uses the frozen70% per-template/operation rubric, with original12 and supplemental6 shown separately. Scores are synthetic single-run evidence, not customer acceptance or population accuracy. A separate audit documents earlier broad hard-gate interpretations while preserving every historical verdict and score: `../reviews/report-mvp-acceptance-audit.md`. Final independent judgement PASS:128/144 generation (88.89%),130/144 edit (90.28%), every original/supplemental template/operation ≥70% and frozen hard gates pass. Full detail: `../reports/report-mvp-quality-06-judgement.md`; the minimum group scores75%. Remaining soft errors are retained in that report.
