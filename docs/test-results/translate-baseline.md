# Translate baseline test result

Date: 2026-10-03. Checkout: `ce9cd47ee1273018fec1c5cbab944a86d68d6ed5` (detached worktree). **Full suite completed with failures: 905 passed, 19 failed, 1 skipped, 0 errors; 925 collected and accounted for.**

## Environment

- Interpreter (read-only): `/home/ict/AIEngines/secai/secai-agent-gateway/.venv/bin/python` (Python 3.12.3, pytest 9.1.1). No install or mutation of that venv. `pytest-timeout==2.4.0` was installed with `uv pip install --target /tmp/secai-translate-baseline-ce9cd47/pytest-plugins --no-deps pytest-timeout`; this target directory is first on `PYTHONPATH` for the completed run.
- `PYTHONPATH=/home/ict/secai-translate-baseline-wt/secai/secai-agent-gateway/src`; import check resolved `secai_gateway.__file__` to that worktree's `src/secai_gateway/__init__.py`.
- The import check and the completed full-suite run used `env -i`. The completed run set `HOME`, `XDG_CACHE_HOME`, `HF_HOME` to `/tmp/secai-translate-baseline-ce9cd47`; set offline Hugging Face flags; redirected gateway, session, PoC catalog/source/artifact paths into that `/tmp` tree; and set `SESSION_STORE_BACKEND=memory`, empty `REDIS_URL`, and a nonexistent classifier head. HTTP(S)/ALL proxy variables pointed to unused loopback port `127.0.0.1:9`; `RES_OPTIONS='attempts:1 timeout:1'` bounded DNS retries. These are network guards for the baseline run, not product changes. Earlier diagnostic runs inherited unrelated ambient variables. The worktree has no `.env`. No production service, live eval, or KPI run was invoked.
- `tests/conftest.py` forces memory sessions and disables Redis but sets `AIRGAP=false` for network-feature tests; this does not guarantee that every test mocks every external request. The sandbox could not create a network namespace (`unshare -Urn` and `bwrap --unshare-net` failed with permission errors).

## Runs and counts

| Run | Command (with environment above) | Result |
| --- | --- | --- |
| **Completed full suite** | `timeout 1200s python -m pytest -q --tb=short --timeout=10 --timeout-method=signal --junitxml=docs/test-results/translate-baseline.full-timeout.junit.xml` | **905 passed, 19 failed, 1 skipped, 0 errors; 925 total in 153.24 s, exit 1.** Per-test timeouts are failures in JUnit, not skipped tests. This is the reproducible baseline to use for a post-change comparison with the same environment and options. |
| Full suite | `python -m pytest -vv -o faulthandler_timeout=25 --junitxml=docs/test-results/translate-baseline.junit.xml` | Interrupted after ~70 s at `test_stream_bughunt_workflow_yields_phases_then_result`: 28 passed, 0 failed, 0 skipped, 0 errors **among completed tests**. No complete-suite result. |
| Isolated blocker | `timeout 30s python -m pytest -vv -o faulthandler_timeout=10 tests/test_bughunt_phase0.py::test_stream_bughunt_workflow_yields_phases_then_result` | Timed out (exit 124), no completed result. |
| Remaining subset | `python -m pytest -q -o faulthandler_timeout=25 --deselect=tests/test_bughunt_phase0.py::test_stream_bughunt_workflow_yields_phases_then_result --junitxml=docs/test-results/translate-baseline.subset.junit.xml` | Interrupted after ~70 s at another async test: 232 passed, 6 failed, 1 skipped, 1 explicitly deselected, 0 reported errors **among completed tests**. 685 of 925 were not completed in this run. No complete-subset result. |
| Translation tests | `python -m pytest -q tests/test_translate_validation.py tests/test_translate_mask.py tests/test_translate_glossary.py -k 'not nonexistent' --junitxml=docs/test-results/translate-baseline.translate.junit.xml` | 49 passed, 0 failed, 0 skipped, 0 errors (exit 0). All 49 tests in the three files were selected. |

The first full run's initial quiet invocation was also interrupted at the same Bughunt test; it showed 41 passed and 1 failed after cancellation progressed to later tests, so the `-vv` run above is the clearer diagnostic evidence. The 49 translation tests overlap with the completed full-suite scope and must not be added to its counts.

## Baseline failures and blockers

1. `tests/test_bughunt_phase0.py::test_bugbounty_root_and_arsenal` failed because `/home/ict/AIEngines/secai/claude-bug-bounty` is absent. This is an external tree dependency.
2. Five tests in `tests/test_chat_poc_drilldown_regression.py` failed because the isolated PoC catalog lacks the expected real `log4j-scan` entry: `test_uc2a_search_log4shell_surfaces_log4j_scan_with_valid_ref`, `test_uc2b_fetch_same_run_returns_real_grounded_source_code`, `test_uc2c_ref_reused_in_a_different_turn_run_is_rejected`, `test_uc2d_resear_in_new_run_yields_a_fresh_fetchable_ref`, and `test_uc4_fetch_budget_enforced_within_a_single_chat_run`.
3. Thirteen endpoint tests failed at the 10-second per-test limit (`Failed: Timeout (>10.0s) from pytest-timeout`): one in `test_consolidate.py` (`test_exploit_endpoint_consolidated_tier`); three in `test_exploit_metadata_grounding.py` (the three `test_exploit_*endpoint*` tests); five in `test_exploit_relevance_gate.py` (the five `test_exploit_*` API cases); and four in `test_gateway.py` (`test_legacy_contract_paths_and_profiles`, `test_streaming_endpoints`, `test_exploit_chitchat_returns_cve_guide`, `test_exploit_stream_section1_preview_arrives_before_final`). JUnit names every timeout; this run does not establish whether any would pass with live dependencies or a longer limit.
4. The earlier diagnostic run hung in `test_stream_bughunt_workflow_yields_phases_then_result`, with a worker thread in `socket.getaddrinfo`; the isolated test timed out at 30 seconds. Under the completed run's loopback proxy and DNS retry settings, that test passed.
5. Natural skip: `tests/test_chat_poc_drilldown_regression.py::test_uc3_license_gated_or_unfetched_poc_returns_metadata_only_not_fabricated`.

No product or test source was changed, and no failing or hanging test was marked as passing.

## Artifacts

- Completed full run: `translate-baseline.full-timeout.stdout.log`, `translate-baseline.full-timeout.junit.xml`, `translate-baseline.full-timeout.exitcode`.
- Full interrupted diagnostic run: `translate-baseline.stdout.log`, `translate-baseline.junit.xml`, `translate-baseline.exitcode`.
- Subset interrupted run: `translate-baseline.subset.stdout.log`, `translate-baseline.subset.junit.xml`, `translate-baseline.subset.exitcode`.
- Isolated timeout: `bughunt-isolated.log`.
- Translation run: `translate-baseline.translate.stdout.log`, `translate-baseline.translate.junit.xml`, `translate-baseline.translate.exitcode`.
- Collection list: `translate-baseline.collection.log`.
