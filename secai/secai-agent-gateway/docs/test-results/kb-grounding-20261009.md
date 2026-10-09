# KB grounding regression results — 2026-10-09

This run checks the exploit endpoint's KB-grounding boundary with deterministic fixtures. It covers the `found`, `not_found`, `error`, and `insufficient` retrieval outcomes, request isolation, catalog evidence handling, and JSON/SSE response contracts.

## Result

**197 passed, 1 skipped** in 6.54 seconds (final related-suite rerun including hook-exhaustion API tests).

Command:

```sh
POC_CATALOG_DB=/tmp/secai-kb-test-catalog.db \
PYTHONPATH=src .venv/bin/python -m pytest -q \
  tests/test_poc_evaluation_rubric.py \
  tests/test_exploit_kb_grounding.py \
  tests/test_exploit_metadata_grounding.py \
  tests/test_exploit_relevance_gate.py \
  tests/test_exploit_lab_rubric.py \
  tests/test_kb_context.py \
  tests/test_require_kb_search_hook.py \
  tests/test_prefetch_request_state.py \
  tests/test_exploit_kb_guard.py \
  tests/test_consolidate.py \
  tests/test_gateway.py
```

The skipped test was `test_external_exploit_stream_has_section1_when_real_source_exists`. It is an opt-in smoke test that requires `RUN_POC_CATALOG_EXTERNAL_SMOKE=1` and a populated external PoC catalog.

## Use cases covered

- An exact `found` result reaches the runner as `grounding_context`; the captured user prompt stays unchanged and contains no KB text. CWE references come from the lookup result.
- `not_found`, retrieval errors, and `insufficient` results with no catalog evidence return without calling the model. JSON and SSE retain their final response contracts and do not expose a fabricated draft marker.
- A bare legacy CVE-existence result cannot upgrade an `insufficient` technical context into permission to synthesize.
- A KB lookup exception is handled as unavailable evidence and does not expose the exception detail or call the model.
- Requests by the same user remain isolated sequentially and concurrently, even when stale values were seeded in the legacy session map. A found request cannot authorize generation for a later insufficient request.
- A temporary SQLite catalog and content-addressed source fixture exercise the real prefetch and injection path for JSON and SSE. The response uses the fixture bytes and preserves its SHA-256 and canonical URL; the candidate is marked `draft_not_executed`. A separate metadata-only advisory case may ground labeled synthesis and does not invent an artifact hash or verified status.
- SSE emits one authoritative final payload and `[DONE]`; the real-code preview precedes the final. Model partial draft text is checked across all emitted frames. A direct cancellation of the response iterator cancels the runner and restores request-local state.
- Retrieval tests cover exact identity, insufficient records, linked context, error handling, source fallbacks, output bounds, and concurrent backend requests. Hook tests cover required-search enforcement and rejection of metadata-only tool results.

`tests/test_exploit_lab_rubric.py` is included in the command, but it tests only the local scoring math for the exploit-lab rubric (0–100 scores and A/B summaries). It does **not** launch a lab, execute a PoC, test exploitability, or validate a real vulnerability.

## Limits

KB API tests use mocked Qdrant/Neo4j transport results. They establish route and retrieval behavior for those fixtures, not live service health or the quality of deployed KB data. The catalog artifact is a harmless test-owned fixture used to verify byte selection and provenance; it is not a real exploit and was not executed. Iterator cancellation checks cleanup at the `StreamingResponse` iterator boundary; it does not emulate a browser disconnect through a live network connection.

The 197-test result above is the related KB/API/hook/state group. A separate completed full-suite run is recorded below.

## Review of the requested files

| File | Result | Checked behavior |
| --- | --- | --- |
| `poc/prefetch.py` | Pass in regression scope | Six evidence stores isolated per request; cleanup, concurrent same-session requests and cancellation covered. |
| `agent/hooks.py` | Pass in regression scope | Successful usable tool output required; metadata-only and empty formatted rows rejected; exhaustion returns `HookResult.mutate`. |
| `api/routes.py` | Pass in regression scope | Catalog and exact DB context prefetched concurrently; no model call for unavailable grounding; ephemeral prompt context; authoritative SSE final and cleanup. |
| `tests/test_require_kb_search_hook.py` | Pass | Retry boundary, per-run isolation and actual Qdrant/Neo4j formatter cases. |
| `tests/test_poc_evaluation_rubric.py` | Pass | Hermetic route behavior across grounded, missing, failed and insufficient KB outcomes. |

| Requested scenario | Verified behavior |
| --- | --- |
| A — Catalog has source code | Actual fixture source bytes and provenance are retained in JSON/SSE; status remains unexecuted. |
| B — No catalog PoC, exact CVE exists in KB | Usable CVE description is passed as ephemeral grounding context. Linked CWE/CAPEC/ATT&CK context is labeled general knowledge. |
| C — CVE absent everywhere | Deterministic not-found response; model is not called and no draft is emitted. |
| D — Model fails to search before exhausting retries | Hook replaces final text with a safe refusal; injection keeps candidate `not_generated`. |

Retrieved metadata is filtered structurally; this is not a semantic proof that every accepted description is sufficient to write a working PoC. Exact lookups run in parallel and output/reference counts are bounded. Supplemental knowledge comes from the configured Qdrant collection; creating another collection does not automatically enable cross-collection retrieval.

Syntax verification used the three-file `uv run python -m py_compile` command requested by the user. This checks Python compilation, not static type correctness. The explicitly requested two-test-file command also passed: **37 tests**, including the additional formatter regressions.

An unrelated full-suite dependency issue was also corrected in test fixtures: three Bughunt workflow tests called HTTP verification and CVE sweep without mocks. They now mock those services explicitly. The complete phase0 module passed with DNS blocked: **13 passed, 1 skipped**. Production Bughunt code was unchanged.

## Complete repository suite

Command:

```sh
/home/ict/.local/bin/uv run python -m pytest -q -rs -o faulthandler_timeout=60 --durations=15
```

Result: **2244 passed, 6 skipped, 7 xfailed, 66 warnings in 376.94 seconds**; process exit code **0**. `git diff --check` also returned exit code 0. The earlier partial full-suite attempts were superseded after source/fixture updates and are not counted as completed runs.

Skipped tests:

| Test | Reason |
| --- | --- |
| `test_bughunt_phase0.py::test_bugbounty_external_clone_and_arsenal_smoke` | `RUN_BUGBOUNTY_EXTERNAL_SMOKE` unset; requires real external clone/arsenal. |
| `test_chat_poc_drilldown_regression.py::test_external_log4shell_catalog_has_grounded_log4j_scan_artifact` | `RUN_POC_CATALOG_EXTERNAL_SMOKE` unset; requires populated external catalog. |
| `test_gateway.py::test_external_exploit_stream_has_section1_when_real_source_exists` | Same opt-in external catalog requirement. |
| `test_reward_qwen.py::test_pairwise_gradient_and_real_tiny_qwen_scalar_roundtrip` | Optional reward dependencies cannot import `huggingface_hub.is_offline_mode`. |
| `test_reward_qwen.py::test_offline_training_evaluation_and_artifact_roundtrip[head]` | Same optional reward dependency incompatibility. |
| `test_reward_qwen.py::test_offline_training_evaluation_and_artifact_roundtrip[lora]` | Same optional reward dependency incompatibility. |

The full suite has no failing tests, but skipped and expected-failure cases are not evidence of verified behavior. No package versions were changed to address unrelated optional reward dependencies. Compilation success does not establish static type correctness, and passing regression tests does not establish successful exploitation by generated code. The SSE route buffers model partial text until the authoritative final, while retaining real-source preview events.
