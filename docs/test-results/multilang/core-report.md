# Multilang core CPU checkpoint — 2026-10-04

**Disposition: implementation checkpoint only; release BLOCKED.** No live inference,
Vast/GPU access, tokenizer download, production flag change, deployment, push, or
main merge occurred. All new language flags remain OFF by default. The independent
review, integrated full suite, serving inventory, 16-direction quality assessment,
and release decision remain outstanding.

## Revisions and environment

- Base/main: `ce9cd47ee1273018fec1c5cbab944a86d68d6ed5`.
- Core code/test/deploy documentation commit: `7131c80e289a83ced8813ef28008f5b6dd196db9` (report is a later scoped commit).
- `pyproject.toml` SHA-256: `a026cffc0f3ff82cc93ff52b07f845b77aa74ab71597b02c11d309be854384ba`.
- Legacy `src/secai_gateway/config.py` SHA-256: `e6b6e3119977f2fa2172b341b95b8ecd3c3483908bbb11a6234d304e9ac2358a`.
- Isolated `.venv`: Python 3.12.3, Linch 1.2.1, FastAPI 0.142.2, Pydantic 2.13.5,
  httpx 0.28.1, tokenizers 0.23.2, pytest 9.1.1, pytest-timeout 2.4.0,
  redis 8.0.1 for optional legacy session fixtures. No shared venv was modified.
- Test environment used `env -i`, private writable paths beneath `.venv/baseline-home`,
  `PYTHONPATH` to this worktree's `src`, offline Hugging Face flags, loopback-deny
  HTTP proxies, in-memory sessions, and a 10-second per-test signal timeout.
  External model calls were mocked. No `.env` or credential file was copied.

## Scope and evidence

The commit adds only `src/secai_gateway/multilang/**`,
`src/secai_gateway/api/multilang/**`, `src/secai_gateway/multilang_app.py`,
`tests/multilang/**`, and `deploy/multilang/README.md`. The v3 app has a fixed
DuyTa model ID and endpoint from existing server settings, an isolated no-tool
HTTP port, a pinned local tokenizer/hash gate, literal masking, sentence chunks,
bounded admission/calls, JSON errors, and an ASGI dispatcher that forwards legacy
traffic and lifespan. It has no session or ledger writes.

| Evidence | Result |
|---|---|
| `pytest -q tests/multilang tests/test_translate_validation.py tests/test_translate_mask.py tests/test_translate_glossary.py tests/test_text_chunk.py tests/test_projects_sessions.py --timeout=10 --timeout-method=signal` | **114 passed** on final code commit; 21 new and 93 legacy tests |
| `pytest -q --timeout=10 --timeout-method=signal --junitxml=.venv/baseline-home/core-full.xml` | **923 passed, 19 failed, 1 skipped** on the preceding working tree, before final CJK sentence-boundary and validation tests; all 19 failure node IDs exactly matched the 19 baseline failures (`new=[]`, `missing=[]`) |
| Baseline on `ce9cd47` from independent preflight | **905 passed, 19 failed, 1 skipped** under the same isolated guard pattern |
| `git diff --cached --check` before code commit | Passed |

The final commit has **not** had a full-suite rerun; root integration owns that
verification. The full-suite failure count is not a suite PASS. Six inherited
failures need the external `claude-bug-bounty` tree or real PoC catalog, and
thirteen legacy exploit/stream tests reached the 10-second timeout. The skipped
node was
`tests/test_chat_poc_drilldown_regression.py::test_uc3_license_gated_or_unfetched_poc_returns_metadata_only_not_fabricated`.

Inherited failure node IDs (identical to baseline):

```text
tests/test_bughunt_phase0.py::test_bugbounty_root_and_arsenal
tests/test_chat_poc_drilldown_regression.py::test_uc2a_search_log4shell_surfaces_log4j_scan_with_valid_ref
tests/test_chat_poc_drilldown_regression.py::test_uc2b_fetch_same_run_returns_real_grounded_source_code
tests/test_chat_poc_drilldown_regression.py::test_uc2c_ref_reused_in_a_different_turn_run_is_rejected
tests/test_chat_poc_drilldown_regression.py::test_uc2d_resear_in_new_run_yields_a_fresh_fetchable_ref
tests/test_chat_poc_drilldown_regression.py::test_uc4_fetch_budget_enforced_within_a_single_chat_run
tests/test_consolidate.py::test_exploit_endpoint_consolidated_tier
tests/test_exploit_metadata_grounding.py::test_exploit_endpoint_populates_cwe_refs_from_cve_record
tests/test_exploit_metadata_grounding.py::test_exploit_endpoint_cwe_refs_empty_when_no_cve_record
tests/test_exploit_metadata_grounding.py::test_exploit_stream_endpoint_populates_cwe_refs
tests/test_exploit_relevance_gate.py::test_exploit_offtopic_freetext_gated_to_not_found
tests/test_exploit_relevance_gate.py::test_exploit_freetext_at_threshold_still_tiers_real
tests/test_exploit_relevance_gate.py::test_exploit_named_cve_bypasses_freetext_gate
tests/test_exploit_relevance_gate.py::test_exploit_vi_generic_hack_gated_even_with_a_passing_score
tests/test_exploit_relevance_gate.py::test_exploit_genuine_freetext_no_cve_still_tiers_real
tests/test_gateway.py::test_legacy_contract_paths_and_profiles
tests/test_gateway.py::test_streaming_endpoints
tests/test_gateway.py::test_exploit_chitchat_returns_cve_guide
tests/test_gateway.py::test_exploit_stream_section1_preview_arrives_before_final
```

## Criterion disposition

| IDs | Checkpoint status and limit |
|---|---|
| CORE-01, CORE-02, CORE-03, CORE-09 | CPU contract tests PASS for registry/flags, master OFF 404, no-tool payload, returned tool-call rejection, atomic JSON/errors, CORS/legacy routing, and lifespan. Independent integration review remains NOT RUN. |
| CORE-04, CORE-06, CORE-10 | CPU tests PASS for body/character/source/chunk/context guards, deadline/cancel, admission/quota, CJK/Arabic sentence splitting, and request flag snapshot. Verified serving tokenizer/template/context evidence and multiworker ingress quota remain BLOCKED/NOT RUN. |
| CORE-05 | Occurrence/order, nested code, CVE/CWE/IP next to non-Latin script, user placeholder, and strict restore tests PASS. Arbitrary proper-name recognition is not guaranteed; source authors can use literal code markup. Quality corpus still needs adjudication of strict-order rejects. |
| CORE-07, CORE-08 | CPU tests PASS for conservative script evidence, copy/off-target rejection, dangerous bidi/surrogate rejection, Arabic joiners, and safe error envelopes. Human review of zh-Hans/MSA/register, multilingual prompt injection, log scans, and quality gates remains NOT RUN. |
| INT-01..06, SEC-01..09, REL-01 | This code commit alone does not satisfy integration, independent security, or release gates. Legacy baseline is inherited with 19 failures; file/OCR integrated tree and live quality are pending. |
| DOC-01 | Staging/rollback, identity, limits, errors, and illustrative vi→zh, ja→vi, vi→ar examples are in `deploy/multilang/README.md`; examples are not live evidence. |

The observed cached snapshot
`7e7e6718ce268d2b00adc6e3ea43fcd33452f516` and its compressed-tensors
metadata are **not** proof of the revision loaded by serving. The wrapper checks
the request/response model ID but cannot attest that loaded revision. Until the
inventory confirms the matching tokenizer, chat template overhead and context
limit, enabled POSTs fail closed before model calls. No quality percentage is
claimed from mock tests.
