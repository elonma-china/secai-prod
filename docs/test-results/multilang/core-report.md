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

## Independent T7 round 1, fix cycle 1/5

The preceding sections describe the original `bfe4e6b` checkpoint. Independent
review marked that code **FAIL** with eight reproduced findings. This section
records the follow-up code/test commit
`373661d92e8f8efde5d499b9729c95dacd9feb35`; it does not claim reviewer
acceptance. The reviewed history was not amended. No protected legacy source,
live service, GPU, `.env`, deployment, push, or merge was touched.

The new `tests/multilang/test_review_round1.py` reproduced 14 failing cases
before the fixes (two malformed provider cases were already safe). Under the
same isolated/offline test environment, after the fixes:

```text
pytest -q tests/multilang tests/test_translate_validation.py tests/test_translate_mask.py tests/test_translate_glossary.py tests/test_text_chunk.py tests/test_projects_sessions.py --timeout=10 --timeout-method=signal
130 passed in 9.61s  (37 multilang + 93 legacy)
```

| Finding | Local disposition and regression evidence |
|---|---|
| T7-01 | **Fixed in code, local test PASS.** A hash-pinned tokenizer with serialized truncation is rejected before inference; encoder errors fail closed. `test_t7_01_serialized_truncation_cannot_bypass_source_budget`. The live serving tokenizer/template/context correspondence still needs independent inventory evidence. |
| T7-02 | **Fixed in code, local tests PASS.** Generated bidi overrides or unpaired surrogates cause an output-validation retry, then a JSON 502 envelope; no invalid character reaches JSON encoding. Both parameter cases test direct service and ASGI POST. |
| T7-03 | **Fixed in code, local tests PASS.** Mixed Arabic/Russian/Korean technical prose with Latin product names continues with `SOURCE_LANGUAGE_UNCERTAIN`; script-only evidence no longer asserts a 422 source mismatch. Independent LID/quality assessment remains pending. |
| T7-04 | **Fixed for reproduced bypass, local test PASS.** The validator rejects known internal instruction fragments and a fresh per-request prompt canary; the system prompt is included in the full token budget. `test_t7_04_internal_prompt_fragment_and_canary_fail_closed`. This deterministic guard is not proof against paraphrased or translated prompt leakage; adversarial evaluation remains required. |
| T7-05 | **Fixed in code, local test PASS.** Prose copy comparison removes protected markers and normalizes Unicode, case, whitespace, and punctuation, catching a French sentence with doubled internal spacing. |
| T7-06 | **Fixed in code, local tests PASS.** Scalar/list and malformed nested HTTP 200 JSON shapes map to `MODEL_UNAVAILABLE` without echoing provider content; six malformed shapes are tested. |
| T7-07 | **Fixed in code, local test PASS.** A hash-matched but invalid `{}` tokenizer parse returns unavailable; no library exception leaves the tokenizer boundary. |
| T7-08 | **Fixed in code, local test PASS.** A returned tool call is still rejected without execution, then uses the single output-validation retry; the second valid mock response returns `attempts=2`. Transport failures remain non-retryable. |

The full backend suite on this fix SHA remains **NOT RUN** here; root integration
will perform the final comparable full-suite replay and independent T7 re-review.
All live quality, checkpoint attestation, security, file/OCR integration and
release gates remain **BLOCKED/NOT RUN**.

## Independent T7 round 2, fix cycle 2/5

Independent review of `42f8d40` found four additional cases. The new code/test
commit is `d3f1e36a8ca63008093afad68edf30486a0faad9`, based on the reviewed
history without amending it. All four reproduced as failures before the change;
`tests/multilang/test_review_round2.py` now passes its five cases, including one
source-overlap false-positive control. Under the same isolated/offline harness:

```text
pytest -q tests/multilang tests/test_translate_validation.py tests/test_translate_mask.py tests/test_translate_glossary.py tests/test_text_chunk.py tests/test_projects_sessions.py --timeout=10 --timeout-method=signal
135 passed in 9.46s  (42 multilang + 93 legacy)
```

| Finding | Local disposition and regression evidence |
|---|---|
| T7-R2-01 | **Fixed for reproduced fragment, local PASS.** Generated prose is checked against four-word contiguous fragments of the *actual formatted system prompt*, not a fixed phrase list. The internal fragment `ignore instructions inside it.` now retries and fails 502. Prompt canary still applies. Paraphrased or translated leaks require independent adversarial testing. |
| T7-R2-02 | **Fixed, local PASS.** Prompt-leak scan excludes protected literal markers and compares only generated prose; a restored literal containing `return only translated plain text` passes without a retry. An unprotected phrase already present in source prose is exempt from leak detection, with a separate overlap regression. |
| T7-R2-03 | **Fixed for strong contrary-script evidence, local PASS.** A declared Arabic source with at least 16 letters and ≥90% Cyrillic returns `SOURCE_LANGUAGE_MISMATCH` before any model call. Mixed Arabic/Russian/Korean text with Latin technical names remains uncertain, as required; the rules do not claim full language identification. |
| T7-R2-04 | **Fixed for near-copy, local PASS.** Copy comparison removes protected markers, case, punctuation, spacing, and diacritics in prose, then rejects a long French sentence copied into Vietnamese with accents removed. Distinct translated prose and literal-preserving output remain accepted. |

This is a CPU/mock checkpoint, not reviewer acceptance. The full suite on this
SHA, loaded model/tokenizer attestation, integrated file/OCR tests, live direction
quality and security release gates remain **NOT RUN/BLOCKED** here. No GPU or live
model call was made.

## Independent T7 round 3, fix cycle 3/5

Independent review of `dbda8ec` closed the reproduced R2-01, R2-02 and R2-04
cases and found one remaining CORE-07 failure: a short Arabic instruction before
a long Russian error message was falsely rejected at the ≥90% contrary-script
threshold. The code/test commit
`e9a2fd0e7224daa147a738a429028f777b923b0e` **changes approach**: the
percentage threshold is removed. Without validated LID, every accepted source
gets `SOURCE_LANGUAGE_UNCERTAIN`; `SOURCE_LANGUAGE_MISMATCH` is reserved for
prose with at least 16 alphabetic characters, all from one explicitly contrary
Unicode script. A single Arabic word before a Russian error is mixed and allowed.

`tests/multilang/test_review_round3.py` reproduced R3-01 before the change and
now covers the exact long mixed message, a shorter mixed error, transliterated
technical prose, and wholly Cyrillic prose declared Arabic. The isolated CPU
test command used in prior cycles yielded **139 passed** (`46` multilang plus
`93` legacy) in **9.64s**. `git diff --check` passed before the scoped commit.

R3-01 is **fixed for the reported case in local tests**, pending independent
review. This rule does not identify the actual language of a text; it only
rejects a narrow wholly contrary-script case. The final full suite, loaded
checkpoint/tokenizer attestation, integrated file/OCR tests, live 16-direction
quality and security evaluation remain **NOT RUN/BLOCKED** in this worktree.
