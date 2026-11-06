# Multilang assessment CPU checkpoint

Date: 2026-10-04 (Asia/Ho_Chi_Minh). Source base `ce9cd47ee1273018fec1c5cbab944a86d68d6ed5`; assessment worktree `feature/multilang-assessment`. This is an offline harness checkpoint, not a translation-quality acceptance result. The exact commit SHA is recorded in Git history after this report is committed.

| Criterion | Status | Evidence and limitation |
|---|---|---|
| AS-01/04 | NOT RUN | `evals/multilang/manifest.py` validates 18 fixed directions, pinned source revision/license, protected offsets, source-group split and cross-split duplicates. No licensed reference corpus has been obtained; [corpus preparation](corpus-preparation.md) is metadata only. |
| AS-02 | BLOCKED | [Preflight](preflight.md) captures incumbent DuyTa metadata; loaded revision is inferred from the sole cache snapshot, not an explicit serve revision. Inference and output limits are untested. |
| AS-03 | BLOCKED | `evals/multilang/run.py` requires explicit `--live`, matching inventory/reservation, OCR release, finite lease, request/rate/worst-case-cost bounds, and local lock. It checks the lease again after rate-limit sleep. All runner tests use HTTP mocks; OCR retains the GPU slot. |
| AS-05/06 | NOT RUN | Runner separates model-only, legacy-api and multilang-api. `sacrebleu==2.5.1` provides chrF++ and language-labelled BLEU diagnostics with coverage/signatures; no real outputs have been scored. No COMET/LID weights were loaded. |
| AS-07/08/09 | NOT RUN | Offline scorer keeps missing/error/empty rows in each direction/round denominator and separates rubric, off-target and exact protected-entity gates. It requires explicit mode/phase; repeated rounds preserve unique source-group count. Synthetic unit fixtures only, not the required 30×18×3 candidate requests. |
| AS-10/11 | BLOCKED | Paired source-group bootstrap uses a fixed seed and 10,000 resamples. Baseline non-inferiority margin, independent evaluator, TEST seal and final security/integration evidence are absent. The top-level release gate remains `BLOCKED`. |

The scorer treats the annotation JSONL as declared reviewer evidence, not proof of reviewer qualification or independence. A qualified blind evaluation and calibrated adjudication must establish that provenance before any quality PASS is accepted. The quality threshold is a sample rubric rate, never chrF++/BLEU. A per-direction gate produced by a mock or synthetic fixture is not a live acceptance result.

Verification:

- `.venv/bin/python -m pytest -q tests/multilang_assessment`: **43 passed**, 0 failed (0.27 s).
- `.venv/run-postchange.sh`: isolated `env -i` with paths listed in [preflight](preflight.md), `timeout 600s .venv/bin/python -m pytest -q --tb=short --timeout=10 --timeout-method=signal --junitxml=.venv/post-change.junit.xml`: **968 collected, 948 passed, 19 failed, 1 skipped** (152.01 s, exit 1). The raw output/JUnit stays ignored under `.venv`.
- [post-change.json](post-change.json) compares exact 19 failed node IDs against [baseline.json](baseline.json): `exact_baseline_failure_match=true`, `new_failure_ids=[]`, `resolved_baseline_failure_ids=[]`. The baseline had 925 collected: 905 passed, 19 failed, 1 skipped. The inherited failures are 13 bounded timeouts and 6 external data/tree dependencies. No source test is deselected.
- `git diff --check` and a sensitive-pattern scan over the scoped files found no whitespace error or secret-looking value. The baseline JUnit redacts two passing parametrized security-test names; all failed node IDs remain exact.

No GPU inference occurred. The corpus, reviewer and live quality gates remain **NOT RUN/BLOCKED**.
