# T7 independent Sol core review — bfe4e6b

**Spec/code verdict: FAIL. Quality/release verdict: BLOCKED.** This is a read-only review of the source and a locally mocked reproduction, not a live model assessment or integrated release test. No source, protected/legacy path, main branch, deployment, or external service was changed. The only checkout write is this requested report.

## Scope and provenance

- Base/main `ce9cd47ee1273018fec1c5cbab944a86d68d6ed5`; code `7131c80e289a83ced8813ef28008f5b6dd196db9`; reviewed head `bfe4e6b7a2eb3178434b3e08dbcdf58fe7410501`.
- Reviewed supplied diff `/home/ict/secai-multilang-core-wt/.superpowers/sdd/multilang-translate/review-ce9cd47..bfe4e6b.diff`, SHA-256 `1c27bfb1915d3409569748b2e40070c2269d2be9e43652ed7c8b19b471a0b59c`. Its `## Diff` payload exactly matches `git diff ce9cd47..bfe4e6b` (the package has a 26-line summary header). Sixteen added files only; no existing protected/legacy source changed. `git diff --check ce9cd47..bfe4e6b` passed.
- `pyproject.toml` SHA-256 `a026cffc0f3ff82cc93ff52b07f845b77aa74ab71597b02c11d309be854384ba`; untouched legacy `config.py` SHA-256 `e6b6e3119977f2fa2172b341b95b8ecd3c3483908bbb11a6234d304e9ac2358a`.
- Frozen contract, CORE-01..10 and INT/SEC gates were read from the criteria worktree. Reproductions used the author's `.venv/bin/python`, `PYTHONPATH` to this review checkout, `PYTHONDONTWRITEBYTECODE=1`, isolated `/tmp` paths, `env -i`, in-memory sessions, `httpx.MockTransport`, and injected fakes. No external network or live inference. A small seeded chunk round-trip probe checked 127 accepted generated cases with no mismatch; this is not exhaustive.

## Findings

Format: `ID | criterion/test | evidence | severity | suggested fix`. File references are relative to `secai/secai-agent-gateway` at the reviewed head.

| ID | Criterion/test | Evidence | Severity | Suggested fix |
|---|---|---|---|---|
| T7-01 | CORE-04, SEC-05: source/chunk/context tokens must bound actual use | `multilang/config.py:56-70` loads a hash-matched tokenizer but does not disable or reject its serialized truncation. A local WordLevel `tokenizer.json` with `max_length=8`, correctly SHA-pinned, counted 100 `hello` words as **8**. `multilang/service.py:103-114` accepted the request under an 8-token source cap and called the fake model once. The same undercount affects every chunk/context check. | **High** | Reject tokenizer configurations with truncation or explicitly disable truncation before counting; prove counts include the serving chat template and cannot be lower than actual request tokens. Test with a pinned tokenizer carrying truncation and an over-budget source. |
| T7-02 | CORE-03, CORE-08, SEC-02/04: invalid generated Unicode must fail in the JSON error envelope | `multilang/service.py:137-146` validates input Unicode only (`multilang/literals.py:72-76`), then accepts model output with U+202E bidi override and unpaired U+D800. Local fake returned either character: `translate()` accepted both. In a mocked ASGI POST, U+D800 caused `UnicodeEncodeError` while building `JSONResponse` in `api/multilang/routes.py:174-180`, escaping the defined 502 envelope; U+202E could be returned to clients. | **High** | Validate generated text before restore/response, including surrogate and dangerous bidi controls, and map invalid output to `OUTPUT_VALIDATION_FAILED` with the configured retry. Keep exact protected literal bytes intact. |
| T7-03 | CORE-07: mixed-script source alone is not confident mismatch | `multilang/service.py:42-58,115-117` returns `no` for Arabic/Russian/Korean whenever the script share is at most 75% after 12 letters. Valid mixed Arabic source `افحص إعدادات Apache Kubernetes الآن.` produced `SOURCE_LANGUAGE_MISMATCH` and **zero** model calls in a local fake run. The contract explicitly says mixed script alone must not cause a confident 422. | **Medium** | Treat mixed script as `unknown` unless an independent detector has strong contrary evidence; return `SOURCE_LANGUAGE_UNCERTAIN` and continue. Add Arabic/Russian/Korean technical prose with Latin names to tests. |
| T7-04 | CORE-08, SEC-01/06: internal prompt must not appear in successful output | `multilang/service.py:21,61-86` searches four leak patterns only. A fake returned the service's own `SYSTEM` instruction text with the placeholder-example clause omitted; `translate()` returned it as a successful French translation (292 characters, uncertainty warning). The source was ordinary Vietnamese prose. This is a deterministic bypass of the promised prompt-leak validator; no network or tool action occurred. | **High** | Reject exact/near-exact internal instruction fragments and use a non-disclosable canary in validation tests. Keep adversarial multilingual leak evaluation as an independent security gate; keyword matching alone cannot prove no leakage. |
| T7-05 | CORE-07: clear untranslated prose copy must fail | `multilang/service.py:71-86` detects only byte-near casefold equality after edge trimming. For French→Vietnamese, a fake returned the unchanged French sentence `Vérifiez le système avant de continuer.` with one doubled internal space. The service returned 200 semantics with `TARGET_LANGUAGE_UNCERTAIN`, although the prose was plainly copied. | **Medium** | Compare normalized prose for copy detection (whitespace/punctuation and protected literals handled separately); keep ambiguous language identity as a warning rather than treating a clear copy as valid. Add Latin→vi copy variants. |
| T7-06 | CORE-03, SEC-04: malformed provider response must map to `MODEL_UNAVAILABLE` | `multilang/provider.py:58-74` calls `data.get` before checking the JSON top-level type; its exception mapping omits `AttributeError`. A mocked HTTP 200 with JSON `[]` raised `AttributeError: 'list' object has no attribute 'get'` instead of 503, which `api/multilang/routes.py:181-184` does not catch. | **Medium** | Validate response shape before indexing and map malformed provider data to `MODEL_UNAVAILABLE`, without echoing response content. Test list/scalar/incorrect nested types. |
| T7-07 | CORE-10, SEC-05: invalid pinned tokenizer must fail closed as 503 | `multilang/config.py:61-69` catches `OSError`, `ValueError`, `ImportError`, but `Tokenizer.from_file` raised a plain `Exception` for a hash-matched local `{}` JSON tokenizer. `api/multilang/routes.py:149` would propagate it outside the defined error envelope. The file hash check cannot establish tokenizer validity. | **Medium** | Catch the tokenizer library's actual parse exception at this narrow boundary and return `None`/`MODEL_UNAVAILABLE`; test a hash-matched malformed tokenizer. |
| T7-08 | CORE-03/04: one retry for output validation | `multilang/provider.py:63-64` raises `OUTPUT_VALIDATION_FAILED` for returned `tool_calls`, but `multilang/service.py:125-151` retries only validation errors raised inside its inner restore/check block. A mocked first response with `tool_calls` and second valid response made **one** provider request and returned 502. No tool was executed; the issue is the specified validation-retry behavior and attempt accounting. | **Low** | Route provider output-validation errors through the same single retry policy while preserving the no-tool rejection; keep transport errors non-retryable. |

## Criterion disposition

| Criterion | Review status | Basis/limit |
|---|---|---|
| CORE-01 | PASS for inspected implementation | Canonical registry, 16 ordered pairs, strict request extras, master/per-language flags, alias and pair restrictions are present. No live flags enabled. |
| CORE-02 | PASS for inspected no-tool payload; loaded revision BLOCKED | Fixed `DuyTa/Cyber-F1-AWQ` model field and incumbent endpoint setting; only system/user messages, `stream=false`, no tool payload or fallback, returned tool calls rejected. Model ID is checked, but the actual serving checkpoint revision was not attested; no live quality claim is valid. |
| CORE-03 | **FAIL** | T7-02, T7-06, T7-08. Atomic success assembly and error schemas otherwise present. |
| CORE-04 | **FAIL** | T7-01. Byte/char/chunk/admission controls are implemented, but tokenizer undercount invalidates the token/context guarantee. Process-local limits are not cluster-global. |
| CORE-05 | PASS for inspected unit paths; quality pending | Occurrence map, nonce, protected-before-chunk order and strict token restore are present. The optional arbitrary proper-name detector is **not** a requirement; names can be annotated with protected markup. Corpus quality must count strict-order rejects. |
| CORE-06 | PARTIAL | CJK/Arabic boundary and rejoin paths inspected; 127 accepted local round-trips had no mismatch. Cancellation is implemented for queued/in-flight awaits, but real provider disconnect/rollback evidence is pending. |
| CORE-07 | **FAIL** | T7-03 and T7-05. Offline MSA/zh-Hans/register adjudication remains BLOCKED. |
| CORE-08 | **FAIL** | T7-02 and T7-04. Input bidi/surrogate guard and no-tool payload exist; output safety and prompt leak guard fail local fakes. |
| CORE-09 | BLOCKED for full gate | Dispatcher routes exact v3 prefix and forwards other scopes; no protected legacy source changed. Legacy file/OCR combined tree, real CORS/rollback, and full regression after final code SHA were not run here. |
| CORE-10 | **FAIL** | T7-07. Flags default OFF and request settings snapshot exist; verified tokenizer and loaded-revision evidence remain BLOCKED. |
| INT-01..06, SEC-01..09 | BLOCKED/NOT RUN as integrated gates | This review is on core SHA only; no file/OCR integration tree, independent full-suite replay on final SHA, scanner, canary log/history scan, multiworker ingress proof, or live provider trace. Security findings above also block a clean SEC gate. |
| REL-01 | BLOCKED | No 16-direction live quality, entity, off-target, baseline, cost or performance results. No production flag enablement. |

## Test evidence and limits

The author reports a **targeted 114 passed** on final code (`21` new + `93` legacy). The author's earlier full run, **before the final three CJK/validation tests**, was `923 passed, 19 failed, 1 skipped`; the 19 failed node IDs exactly matched the `ce9cd47` baseline under the author's isolated harness. This is inherited failure evidence, **not** a full-suite PASS or a final-SHA full-suite result. I did not blindly repeat either suite. The inherited failures were:

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

The inherited failures include an external `claude-bug-bounty`/PoC-catalog dependency and legacy 10-second test timeouts according to the author's checkpoint. The single skip was `tests/test_chat_poc_drilldown_regression.py::test_uc3_license_gated_or_unfetched_poc_returns_metadata_only_not_fabricated`. The integrated evaluator must rerun the final code and imported file/OCR tests with the same guards before making INT-03 claims. Missing live quality, loaded checkpoint revision, screenshot/FE smoke, and integration evidence remain **BLOCKED**, never unit PASS.
