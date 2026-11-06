# Diagnostic file translation/OCR integration

Date: 2026-10-04. This isolated `integration/multilang-file-ocr-diagnostic` worktree starts at reviewed UI commit `b33416b` and merges backend stability commit `bef2e05`. It is a diagnostic composition, not a merge to main, a deployed change, or OCR/model-quality acceptance. No real model, OCR service, GPU, or private endpoint was called by this merge verification.

## Conflict resolution

| Path | Resolution |
|---|---|
| `src/secai_gateway/agent/runner.py` | Keep the `translation_orchestrator` specialist tool result as the return value, alongside backend strict `ErrorEvent` to `ProviderError` conversion for OCR correction and legacy `translate_v2` profile calls. Strict handling remains opt-in to the worker runner. |
| `src/secai_gateway/translation/worker.py` | Keep file translation through `TranslationAgent` and the backend's full-pipeline OCR cancellation/deadline supervisor, correction retry cap, strict runner errors, and disabled hidden provider retries. Use the same bounded child-task supervisor for specialist calls. Construct a Linch runner only when OCR correction is enabled. |
| `src/secai_gateway/translation/ocr_correction.py` | Preserve raw fallback for invalid JSON/proposed edits only. Provider, deadline and cancellation failures propagate to terminal job failure/cancellation. |
| `tests/test_translate_v2_worker.py` | Keep specialist file-job and language-pair assertions; port backend retry/deadline/actual-wire assertions from the obsolete Linch file translation path to the live optional OCR correction path. Preserve retry budgets (503/disconnect: two sends; 401: one) and cancel/trickle checks. |

`tests/test_multilang_file_ocr_merge.py` covers the composed path: bounded specialist timeout and child cancellation, one retry after a transient specialist error, the same HTTP retry/cancel path through `httpx.MockTransport`, no Linch construction when OCR correction is off, and the DuyTa orchestrator receiving its specialist tool result with strict OCR errors enabled. Existing `tests/test_translation_specialist.py` additionally covers the specialist HTTP contract and the file worker's specialist route.

## Verification

Commands run from this gateway directory with `POC_CATALOG_DB=/tmp/secai-multilang-diag-poc.sqlite3` and `PYTHONPATH=src` using `/tmp/secai-multilang-final-env/bin/python`:

- `python -m pytest -q tests/test_multilang_file_ocr_merge.py tests/test_translate_v2_worker.py tests/test_translate_v2_ocr_correction.py tests/test_translation_specialist.py tests/test_translate_v2_ocr_client.py`: **66 passed in 7.66 s** before the final two HTTP-wire regressions; **68 passed in 8.77 s** after they were added.
- `python -m pytest -q` over the 21 existing translation v2, multifile and new integration test modules: **338 passed in 69.32 s**.
- Full repository `python -m pytest -q` on the frozen product-code tree, before the final two test-only HTTP-wire regressions: **exit 1, 1,443 passed, 7 failed, 1 skipped, 64 warnings in 549.55 s**. The seven failures are exactly:
  - `tests/test_bughunt_phase0.py::test_bugbounty_root_and_arsenal`
  - `tests/test_chat_poc_drilldown_regression.py::{test_uc2a_search_log4shell_surfaces_log4j_scan_with_valid_ref,test_uc2b_fetch_same_run_returns_real_grounded_source_code,test_uc2c_ref_reused_in_a_different_turn_run_is_rejected,test_uc2d_resear_in_new_run_yields_a_fresh_fetchable_ref,test_uc4_fetch_budget_enforced_within_a_single_chat_run}`
  - `tests/test_gateway.py::test_exploit_stream_section1_preview_arrives_before_final`

  The Bughunt root `/home/ict/AIEngines/secai/claude-bug-bounty` does not exist in this workspace. The other six require PoC catalog evidence absent from the isolated catalog. These match the seven IDs documented by the backend owner, but this composition's count and outcome are reported independently. The two final tests are the only files added after that full-suite run; they passed in the subsequent focused run.
- `git diff --check`: passed before staging.

The backend owner's prior 1,158-pass/7-fail baseline belongs to a different tree and is not a pass claim for this composition. Translation semantic quality for the 18 directions and OCR quality are separate acceptance gates. A separate algorithm check on the pre-merge file translation code found that trimming each of five long-text chunks can lose four internal word separators; this remains a follow-up product defect and is deliberately not folded into this merge resolution.
