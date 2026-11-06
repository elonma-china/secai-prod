# Multilang final assessment CPU integration

2026-10-04. **Authorized CPU implementation/integration complete; full suites NOT GREEN; live quality and release BLOCKED.** Assessment is now integrated. This section supersedes earlier incomplete-assessment statuses, while retaining the historical results below. No product fix was made by this integrator.

## Exact accepted, tested and reported revisions

| Item | Exact SHA / evidence |
|---|---|
| Starting report | `ac777e5e6f688aa9745da147a3caf17a72bd040c`, clean |
| Accepted assessment | `f38801385321b07fd4bc210f5e2d751d1d727795`; independent round 5 PASS,152 tests plus independent protocol probes |
| Assessment merge | `1f31ef39abf6e8be6437c0e8bdd92011bd71da15`; exact accepted second parent, no conflict |
| Authorized rootdocs | `aa11407fee4fc06938c5767c10a7b6fcbddb9dc4`; reviews, final partial scan and resource closeout |
| **Runtime/full test SHA** | **`877e619514ed2a5ed7dcd5a2353c02b03ca295b4`**, tree `bd049b0f3469bcdf6a0d666586ce23adb9ab746e`; rootdocs second parent, clean merge |
| **Disposable combined test SHA** | **`e6856eda87bbb10668d06a3f2b71896216abb673`**, tree `2ce7eda9150b2aaf938c34e618e7651927391e81` |
| Combined parents | `0097da34f5043a07812e5cf09385140312cc2eec` + `877e619514ed2a5ed7dcd5a2353c02b03ca295b4`; clean update to inherited simulation |
| Refreshed origin/main | `ce9cd47ee1273018fec1c5cbab944a86d68d6ed5` |
| Import / OCR | `bfab8ebc96e25b23325fd72a1d3bf034e9aea315` / `ff311c12f97a1dd453495b0ff2b9ca269a49aba2` |
| Core / FE source | `f6b7834d5fb215075cb0cdbc448e3b538d93f7b3` / `788da975cf9eed0e0bf72089fd137e00fd8ad2b2`, unchanged |

The report commit is a documentation-only child of the runtime test SHA, with only integration-report.md, integration-evidence.json and final-report.md staged. It is **not a test/browser execution SHA**. Its exact SHA is recorded after commit in root scratch `/home/ict/secai-multilang-wt/.superpowers/sdd/multilang-translate/integration-assessment-final-checkpoint.md`; `git log -1 --format=%H -- docs/test-results/multilang/integration-report.md` from gateway resolves it. No squash/rebase, protected branch merge, main merge, push, deploy, service change or subagent.

## Full comparable regression

| Snapshot | Total | Passed | Failed | Skipped | Errors | Execution |
|---|---:|---:|---:|---:|---:|---|
| Fresh correctenv baseline ce9cd47, reused |925|905|19|1|0|Prior measured154.06s; no third baseline run |
| Runtime `877e619` |1140|1120|19|1|0|19 failed, 1120 passed, 1 skipped, 64 warnings in 162.57s (0:02:42); exit1 |
| Combined `e6856ed` |1247|1227|19|1|0|19 failed, 1227 passed, 1 skipped, 64 warnings in 172.34s (0:02:52); exit1 |
| Assessment cases within each full run |152|152|0|0|0|New execution; included in totals |
| Vanguard cases within each full run |17|17|0|0|0|New execution; included in totals |

All 925 fresh and archived baseline IDs/outcomes retained in both runs. All 988 prior runtime and 984 older runtime retained unchanged. Combined additionally retains all 1,014 import, 966 OCR, 1,095 prior combined and 1,091 older combined IDs/outcomes; all 1,140 current runtime cases also match combined. **No missing IDs, changed outcomes, new failure IDs, collection errors or deselections.** The 152 added assessment cases account exactly for the increase from 988→1,140 and 1,095→1,247. Full node maps and complete failure/skip messages and traceback text are committed in integration-evidence.json; raw full logs/JUnit and hashes are retained in `/tmp/multilang-assessment-final-evidence`.

Same `/tmp/secai-multilang-final-env/bin/python`, Python3.12.3, **67 packages = 57 baseline versions unchanged + 10 extras**. Verified all 17 combined requirements and seven assessment metric pins; `uv pip check` PASS. Metric lock at accepted f388013 is unchanged, SHA256 `7b2cef3aa3cefb5481f7e26b64b89784242bbed3b502c51e6613c50dc7006555`. No install/download/environment mutation or supplemental PYTHONPATH. Prior baseline source/tests and all 67 installed versions are identical, justifying baseline reuse.

Both full commands remain `timeout 600s /tmp/secai-multilang-final-env/bin/python -m pytest -q --tb=short --timeout=10 --timeout-method=signal --junitxml=<private-output>`, from their exact gateway checkout with explicit `PYTHONPATH=<exact-checkout>/src`. The reused launcher retains the same clean allowlisted private HOME/cache/HF/database/catalog/artifact paths, memory sessions, empty Redis, absent classifier, offline HF/Transformers, denied-loopback proxy and bounded DNS controls. Import identity and full package inventory asserted per run. No live/data/model call or model download was performed. Proxies are isolation guards, not comprehensive egress certification. No timeout adjustment or test suppression. Timings are test durations, not model latency/cost evidence.

Inherited 19 failures: one absent external Bughunt tree, five unavailable private PoC/catalog cases (one assertion + four StopIteration), 13 unchanged 10-second timeouts. One unavailable catalog skip remains. These are limitations, not harmlessness or overall PASS. Exact IDs:

```text
tests/test_bughunt_phase0.py::test_bugbounty_root_and_arsenal
tests/test_chat_poc_drilldown_regression.py::test_uc2a_search_log4shell_surfaces_log4j_scan_with_valid_ref
tests/test_chat_poc_drilldown_regression.py::test_uc2b_fetch_same_run_returns_real_grounded_source_code
tests/test_chat_poc_drilldown_regression.py::test_uc2c_ref_reused_in_a_different_turn_run_is_rejected
tests/test_chat_poc_drilldown_regression.py::test_uc2d_resear_in_new_run_yields_a_fresh_fetchable_ref
tests/test_chat_poc_drilldown_regression.py::test_uc4_fetch_budget_enforced_within_a_single_chat_run
tests/test_consolidate.py::test_exploit_endpoint_consolidated_tier
tests/test_exploit_metadata_grounding.py::test_exploit_endpoint_cwe_refs_empty_when_no_cve_record
tests/test_exploit_metadata_grounding.py::test_exploit_endpoint_populates_cwe_refs_from_cve_record
tests/test_exploit_metadata_grounding.py::test_exploit_stream_endpoint_populates_cwe_refs
tests/test_exploit_relevance_gate.py::test_exploit_freetext_at_threshold_still_tiers_real
tests/test_exploit_relevance_gate.py::test_exploit_genuine_freetext_no_cve_still_tiers_real
tests/test_exploit_relevance_gate.py::test_exploit_named_cve_bypasses_freetext_gate
tests/test_exploit_relevance_gate.py::test_exploit_offtopic_freetext_gated_to_not_found
tests/test_exploit_relevance_gate.py::test_exploit_vi_generic_hack_gated_even_with_a_passing_score
tests/test_gateway.py::test_exploit_chitchat_returns_cve_guide
tests/test_gateway.py::test_exploit_stream_section1_preview_arrives_before_final
tests/test_gateway.py::test_legacy_contract_paths_and_profiles
tests/test_gateway.py::test_streaming_endpoints
```

Skip: `tests/test_chat_poc_drilldown_regression.py::test_uc3_license_gated_or_unfetched_poc_returns_metadata_only_not_fabricated`.

## Assessment review, namespaces and ownership

Read full frozen assessment/core/FE/adapter/integration criteria, independent core/FE PASS, full Vanguard report, full earlier integration/final report and machine evidence, correctdeps/resource closeout, and independent assessment rounds 3–5. Round 3 reported 5 findings; round 4 closed the shared-slot lock,30×3 hard minima, signed phase/full count, pinned output directory, leaving one choice-level tool_call_id guard; round 5 closes that residual with six markers×three levels and 54 independently enumerated negative cases plus probes. No remaining finding in the accepted bounded patch; this does not certify live provenance/quality.

The integration delta contains 54 paths: assessment evals/multilang and tests/multilang_assessment, plus authorized documentation. Source/tests are Git-object identical to accepted f388013 in runtime and combined. No modified legacy/core/FE source, pyproject or lock; no protected import/OCR path overlap. Root STATE/LOG only arrived through the exact authorized rootdocs merge; this integrator did not edit them. No source fix was necessary or attempted.

Fresh socket-denied import/config/metric checks passed in both trees: assessment runner/scorer/evidence/manifest/metrics and runtime/API composition import from the intended snapshots; independent legacy Settings instance remains unchanged; all new flags default OFF; incumbent DuyTa ID unchanged; local chrF++/BLEU signatures valid and diagnostic quality remains NOT_ASSESSED. Full tests additionally exercise no-live dry-run behavior, signed leases and trusted evidence, shared locking, output pinning, failed-sample denominators, hard minima, holdout consumption, paired margins and protocol guards. Fixture signatures and 100-point identical-string metric probes are software tests, not model quality evidence.

## Refreshed composition, conflicts and unrelated WIP

Read-only fetch of origin/main and before/after HEAD/status/diff fingerprints recorded. Main+runtime, runtime+import, runtime+OCR merge-tree results clean. **Import+OCR still has actual conflicts** in `src/secai_gateway/prompts/profiles.yaml` and `src/secai_gateway/translation/settings.py`. The current disposable merge was clean because it inherits the prior reviewed simulation resolutions; no new resolution here.

Unchanged temporary unions: preserve OCR's ocr_correction profile (prompt: ocr_correction.md, tools: [], base: false, max_turns: 2) alongside import translate_v2; preserve import `converter_image: str = "secai-doc-converter:1"`. Exact conflict hunks remain in historical evidence; current raw merge-tree outputs and resolved blob/SHA256 equality are recorded. These simulation choices remain **not accepted file/OCR integration** and were never merged into protected/integration feature sources.

Import remains with untracked .venv-import/ and AGENTS.md; OCR and assessment each have untracked AGENTS.md. Root planning has untracked .playwright-cli/ and output/. Original AIEngines worktree retains 33 tracked sibling FE deletions and its other untracked artifacts. No owner WIP imported, modified or reverted. Before/after full statuses and tracked-diff fingerprints are committed; observed owner changes: none.

File owner remains **STOPPED after two reviews with two High findings**, no reopening or acceptance: R2-01 failed input/quota cleanup with worker/reaper down; R2-02 Unicode-adjacent URL/hash/version literal mutation. Earlier correctenv reproductions remain applicable by identical combined source/config/dependencies; **not rerun here**, and neither defect fixed.

## Reused evidence and scanner limits

Verified Git trees and all 203 prior runtime/FE/config file hashes plus 44 prior raw artifact hashes. Runtime/backend/deploy/declaration/locks/canonical FE/old sibling FE remain identical to 929b199 and actual HTTP/browser-executed 43e5f22. Combined runtime source/config remain identical to 0097da3. Thus the prior 7/7 socket-guarded composition probes, legacy 69 paths/15 schemas and en↔vi JSON/SSE/invalid fingerprints apply by identity, with unchanged digest `db5e5df0a891c91d9c372a67524fbc8ff16513f6d4a7f03640d31eb217f60ad2`. **Reused, not newly executed.** All affected assessment tests were newly executed in both full suites.

Prior FE 21 tests/build PASS, 253 installed lock matches, lock SHA256 `9ef40829cd3c6b2c2d18c342deda83209e36dd3c348bb0059fa863b9bcc9eb1f` retained; no rebuild. Root actual HTTP/browser at 43e5f22 covered fake LLM three pairs, legacy JSON/SSE, CORS, Arabic RTL/literal LTR, copy API stub, mobile width. No new browser claim; OS clipboard, live LLM, real tokenizer remain untested; existing header clipping/projects-session 404/health-restart warning and bundle warning retained. Prior same-lock author npm audit 0 is not a fresh advisory audit. Root staging is closed per resource-closeout.md/staging-cleanup.json; no service action here.

Runtime Semgrep source scope 14 files/9 units: 7 complete + 2 partial, zero findings. Final accepted assessment [scan](../../reviews/multilang/static-analysis-assessment-f388013/report.md):7 Python files/6 units, 4 complete + 2 partial, zero findings. All recorded source hashes match the current integrated sources. Elttam 12 + Apiiro 4 rule compilation errors remain in each affected scan; Pro unavailable, CodeQL NOT RUN. No scanner rerun or blanket security PASS. Assessment final scan supersedes prior bf06bc4/8e53a00 scope for current code only. Vanguard 23 selected final-core PASS remains prior independent evidence; current 17 Vanguard cases PASS freshly in each full suite.

## Handoff and remaining blockers

**CPU implementation/checkpoint complete and ready for final Warden. Live release remains BLOCKED.** All 18 directions × 3 rounds of quality NOT RUN; rubric ≥70% each direction/round, off-target≤2%, protected entities 100% unmeasured. Licensed independent corpus/reference, calibrated qualified evaluator/adjudication, source group splits and sealed holdout/tester trust/custody remain pending. Gate A needs live serving revision/tokenizer/template/context/license/engine/quant attestation and baseline/resource lease; Gate B ablations NOT RUN; Gate C NOT ENTERED, neither PASS nor N/A; Gate D BLOCKED. en↔vi frozen noninferiority and performance/cost margins pending; latency/throughput/token/cost UNKNOWN/NOT RUN, rental accrual unknown. Production trace privacy/egress/real provider and cluster-wide quota/rollback evidence pending. The 19 inherited failures + one skip and file STOP with two High findings remain. Partial static analysis does not clear security release.

All language flags OFF; DuyTa unchanged; no alternative translator,auxiliary checkpoint,live/data/model/GPU calls or downloads. Counters core 3/5, FE 1/5, assessment 4/5, integration 0/5 product fixes, Vanguard returns 0/3; shared auxiliary 3/3 exhausted, ours 0. Known actual preflight output credential incident remains **unresolved; no rotation or deletion claimed**; no values included. Root-owned staging closed, other resources untouched, no cost-zero claim. No root STATE/LOG edits, main merge,push,deploy or enablement; future main merge/rollout are separate user decisions.

Machine evidence: [integration-evidence.json](integration-evidence.json). Final [report](final-report.md) retains all 18 NOT RUN direction rows. No unconditional release PASS.

---

## Historical corrected-dependency checkpoint at reportac777e5

# Multilang CPU runtime integration — corrected dependency checkpoint

2026-10-04. **CPU runtime checkpoint complete; assessment integration incomplete; release BLOCKED.** This section supersedes the earlier dependency, security-merge, browser, scanner and API-provenance statuses retained below. The full suite is **not green**.

## Exact snapshots and authorization

| Item | Exact revision |
|---|---|
| Fresh baseline | `ce9cd47ee1273018fec1c5cbab944a86d68d6ed5` |
| Runtime plus reviewed tests actually run | **`929b19997116a2d7b193ebc44912ef1fdcaf0feb`**, tree `1bb7b6fdb9be3c25ab3d8d80e375a562ba34a5fc` |
| Product source/browser identity | `43e5f2233256a74b091c41b3f14b325f44161a6a`; backend source, deploy files, Python declaration/lock, complete canonical FE and old FE sibling identical |
| Security authorized / clean merge | `11ec898bd712ba1add2bf56c0ac4f9918c5b1bf8` / `b9e9515fe28feabba8b339dbf3b1c18d7a78ec9a` |
| Planning evidence authorized / clean merge | `2c222625aa169f5678a73f0ca05233fd9f63fc4f` / `929b19997116a2d7b193ebc44912ef1fdcaf0feb` |
| Reviewed core / FE | `f6b7834d5fb215075cb0cdbc448e3b538d93f7b3` / `788da975cf9eed0e0bf72089fd137e00fd8ad2b2` |
| Disposable combined actually run | **`0097da34f5043a07812e5cf09385140312cc2eec`**, tree `bdf7efb04b8e938e3716c0e4c6555275010d635e` |
| Import / OCR committed parents | `bfab8ebc96e25b23325fd72a1d3bf034e9aea315` / `ff311c12f97a1dd453495b0ff2b9ca269a49aba2` |
| Assessment excluded | `bf06bc4311a8c2487abce8bf7612c04025eb9d56`; round 3 FAIL with 5 findings; fix cycle 3 committed at closeout as 8e53a00, still excluded pending independent acceptance and authorization |

The report commit is a documentation-only descendant of tested snapshot `929b19997116a2d7b193ebc44912ef1fdcaf0feb`. It contains this report, final-report.md, integration-evidence.json and the authorized one-line API provenance correction. Its exact SHA is recorded in root scratch `integration-correctdeps-checkpoint.md` after committing; retrieve with `git log -1 --format=%H -- docs/test-results/multilang/integration-report.md` from the gateway directory. Do not label the report SHA as the test or browser execution SHA. All prior reports, complete evidence.json, frozen criteria/contract, root STATE, handoff notes, full final-core security report and current round3 assessment feedback were read. No source fix or assessment merge was made.

## Comparable full regression with correct dependencies

| Snapshot | Total | Passed | Failed | Skipped | Errors | Duration / exit |
|---|---:|---:|---:|---:|---:|---|
| Fresh base in prepared environment | 925 | 905 | 19 | 1 | 0 | 154.06s / 1 |
| Runtime `929b199` | 988 | 968 | 19 | 1 | 0 | 159.19s / 1 |
| Combined `0097da3` | 1095 | 1075 | 19 | 1 | 0 | 168.78s / 1 |
| Separate composed probes | 7 | 7 | 0 | 0 | 0 | 9.28s / 0 |

Every one of the **925 fresh baseline IDs and outcomes** is retained in runtime and combined; fresh baseline also matches all925 archived assessment baseline IDs/outcomes. All984 prior runtime and1091 prior combined IDs/outcomes remain. All **1014 archived import** and **966 archived OCR** IDs/outcomes remain in combined. No missing IDs, changed outcomes, new failure IDs, collection errors or deselections. Runtime adds46 core and **17 security cases**; all17 pass (four more than the earlier13). Exact outcome maps and comparisons are in integration-evidence.json; no failure was hidden by timeout adjustment.

Interpreter `/tmp/secai-multilang-final-env/bin/python`, Python3.12.3, **67 distributions**. Verified all57 core-baseline packages unchanged, no removals, ten additions: colorama0.4.6, lingua-language-detector2.0.2, lxml6.1.3, **olefile0.47**, portalocker4.4.0, python-docx1.2.0, python-multipart0.0.20, regex2026.9.29, sacrebleu2.5.1, tabulate0.10.0. All17 declared combined project/dev requirements satisfy constraints; all7 assessmentbf06bc4 metric-lock pins match. Preparation installed that lock with `--require-hashes`; current independent inventory/constraints check and `uv pip check` pass. Lock SHA256 `7b2cef3aa3cefb5481f7e26b64b89784242bbed3b502c51e6613c50dc7006555`. No further package download or environment mutation was needed in this continuation. The former olefile0.46 mismatch is **closed**, with original failed setup and compatibility-only results retained as historical evidence.

All three full runs use the **same prepared interpreter and command**, no supplement or editable package: `timeout 600s /tmp/secai-multilang-final-env/bin/python -m pytest -q --tb=short --timeout=10 --timeout-method=signal --junitxml=<private-output>`. Working directory and explicit `PYTHONPATH=<exact-snapshot>/src` select base/runtime/combined; actual imported module path and the complete67-package inventory were asserted separately for each. Identical clean environment apart from snapshot/private paths: private HOME/cache/HF/databases/catalog/artifacts, memory sessions, empty Redis, unavailable classifier, offline HF/Transformers, disabled bytecode, loopback-deny proxies and bounded DNS. Proxies are isolation guards, not exhaustive egress certification. No live service/model/GPU run. Timings are test execution times, not performance benchmarks.

Exact inherited failures (all remain release limitations):

```text
tests/test_bughunt_phase0.py::test_bugbounty_root_and_arsenal
tests/test_chat_poc_drilldown_regression.py::test_uc2a_search_log4shell_surfaces_log4j_scan_with_valid_ref
tests/test_chat_poc_drilldown_regression.py::test_uc2b_fetch_same_run_returns_real_grounded_source_code
tests/test_chat_poc_drilldown_regression.py::test_uc2c_ref_reused_in_a_different_turn_run_is_rejected
tests/test_chat_poc_drilldown_regression.py::test_uc2d_resear_in_new_run_yields_a_fresh_fetchable_ref
tests/test_chat_poc_drilldown_regression.py::test_uc4_fetch_budget_enforced_within_a_single_chat_run
tests/test_consolidate.py::test_exploit_endpoint_consolidated_tier
tests/test_exploit_metadata_grounding.py::test_exploit_endpoint_cwe_refs_empty_when_no_cve_record
tests/test_exploit_metadata_grounding.py::test_exploit_endpoint_populates_cwe_refs_from_cve_record
tests/test_exploit_metadata_grounding.py::test_exploit_stream_endpoint_populates_cwe_refs
tests/test_exploit_relevance_gate.py::test_exploit_freetext_at_threshold_still_tiers_real
tests/test_exploit_relevance_gate.py::test_exploit_genuine_freetext_no_cve_still_tiers_real
tests/test_exploit_relevance_gate.py::test_exploit_named_cve_bypasses_freetext_gate
tests/test_exploit_relevance_gate.py::test_exploit_offtopic_freetext_gated_to_not_found
tests/test_exploit_relevance_gate.py::test_exploit_vi_generic_hack_gated_even_with_a_passing_score
tests/test_gateway.py::test_exploit_chitchat_returns_cve_guide
tests/test_gateway.py::test_exploit_stream_section1_preview_arrives_before_final
tests/test_gateway.py::test_legacy_contract_paths_and_profiles
tests/test_gateway.py::test_streaming_endpoints
```

Exact skip: `tests/test_chat_poc_drilldown_regression.py::test_uc3_license_gated_or_unfetched_poc_returns_metadata_only_not_fabricated`. Prior attribution: one external Bughunt dependency, five unavailable isolated PoC-data cases and13 ten-second timeouts; their unchanged IDs do not make them harmless or passing.

## Actual composition and owner boundaries

Refreshed merge-tree: main+runtime, runtime+import and runtime+OCR clean; **import+OCR still conflicts independently** in the same two source files. The disposable update is a clean merge of runtime929b199 into previous simulationeb984d4, with exact parents `eb984d4d1d689b1eabc7e4be9bb5b62671e9196c 929b19997116a2d7b193ebc44912ef1fdcaf0feb`. Its entire backend source and dependency declaration equal previouseb984d4; no new resolution or runtime code was introduced. It contains committed snapshots only, no owner WIP.

| Inherited conflict hunk | Preserved minimal union |
|---|---|
| profiles.yaml lines59–67 of conflicted tree, after legacy translate and before translate_v2 | Retain OCR `ocr_correction` mapping: prompt ocr_correction.md, tools[], base false, max_turns2; retain import translate_v2 |
| translation/settings.py lines23–26 of conflicted tree, after worker_db_path | Retain import `converter_image: str = "secai-doc-converter:1"` |

Raw merge tree and exact numbered conflict text, resolved blob IDs and SHA256 hashes are recorded in integration-evidence.json. These remain **temporary simulation choices**, not accepted product integration, not a clean file/OCR merge, and not merged into feature/protected branches. Importbfab8eb and OCRff311c1 HEADs and WIP match before/after: import untracked `.venv-import/` and AGENTS.md; OCR untracked AGENTS.md. Main unchangedce9cd47. Assessment HEAD advanced from bf06bc4 with five modified test/source files to `8e53a00a8adf710dd630196de8341a2585e5d97a` with only untracked AGENTS.md. These owner changes were observed read-only and not merged; the assessment scan still covers bf06bc4 only.

Owner task remains **STOPPED/BLOCKED** after two review rounds with two High. Correct-dependency repros still show R2-01 expired failed-job input retained and subsequent admission STORAGE_UNAVAILABLE with no worker/reaper; R2-02 CJK-adjacent URL has no protected span and fake-provider mutation is accepted. No fix, reopen or additional owner review.

Seven reused socket-guarded probes pass against actual combined factory: masterOFF/ON × v2OFF/ON, capabilities and disabled bidirectionalPOST, v2 storage and OpenAPI boundaries, separate legacy/v3 HTTP-client lifecycle and legacy lifespan forwarding, real standalone worker/API/job/result handoff under outer factoryOFF/ON, optional tokenizer absent startup. Native imported file/OCR tests, including networkless DOC conversion, run in full suite. Worker remains separately supervised; web lifespan does not start it.

Fresh fixed-PYTHONHASHSEED0 fingerprints match across base, runtimeOFF/ON and combinedOFF/ON: **69 legacy paths /15 schemas**, both en↔vi JSON/SSE and invalid/empty input behavior. SHA256 remains `db5e5df0a891c91d9c372a67524fbc8ff16513f6d4a7f03640d31eb217f60ad2`. Fake legacy runner only; not all live behavior.

## Browser, frontend, security scans and API documentation

Root's [actual HTTP/browser replay](browser-http.md) **executed at43e5f22**: 9languages/16pairs, exact CORS allowed/denied, vi→zh-Hans/ja→vi/vi→ar200 preserving CVE/IP, disabledfr403 and extra model400, legacy en↔vi JSON/SSE200. Browser awaited returned text; ArabicRTL/technicalLTR, exact copyAPI-stub text and390px mobile no page overflow passed; screenshots inspected by root. Backend used fakeLLM/token counter/legacy runner and denied outbound sockets. Existing header/navigation mobile clipping, expected projects/session404 and one restart-time health connection refusal are recorded. OS clipboard permissions and live model quality untested.

Product/FE/lock identity to43e5f22 is verified by Git object IDs and SHA256 manifests. Thus browser evidence **applies by verified source identity**, not a claim that929b199 was newly browser-executed. No repeated staging/browser or unnecessary FE rebuild. Earlier **21tests/buildPASS**,253 installed lock matches, lock SHA256 `9ef40829cd3c6b2c2d18c342deda83209e36dd3c348bb0059fa863b9bcc9eb1f` remain applicable; bundle warning and prior author npm audit0 dated2026-10-03 retain their limits. No fresh advisory audit.

Authorized security11ec898 full report: **23 selected final-core CPU cases passed, zero new actionable findings**, security release BLOCKED. Current runtime full suite additionally confirms all17 Vanguard cases and baseline legacy cases with correct dependencies.

New committed [runtime Semgrep report](../../reviews/multilang/static-analysis/report.md) **supersedes older scanner NOT RUN**: Semgrep OSS1.179.0,14 allowlisted files (11Python/1TS/2TSX),9 units,7 complete and2 partial,0findings. All14 source hashes verified against current identical runtime; no rerun claimed. Elttam12 and Apiiro4 compilation errors remain, including one relevant JS/TS obfuscation rule; **partial scan, not blanket security PASS**. Pro unavailable; CodeQL NOT RUN. [Assessment scan](../../reviews/multilang/static-analysis-assessment/report.md) is separate evidence for **bf06bc4 only**,7Python files/6units,4complete+2partial/0findings; source hashes verified against that committed snapshot, not current owner WIP. It does not approve assessment or its fixes. Full errors, scope, revisions, unit results and raw checksums remain committed.

**INT-RC-01 (Low / DOC-01) CLOSED**: API guide opening core reference corrected42f8d40→f6b7834, the sole new documentation fix. No wire/runtime change.

## Remaining gates

Assessment round 3 failed with five findings at bf06bc4; owner fix cycle 3 advanced to `8e53a00a8adf710dd630196de8341a2585e5d97a` during this checkpoint and is still excluded pending independent acceptance; its later independently accepted, explicitly authorized merge needs a **separate final integrated SHA and full suite**. This checkpoint completes CPU runtime work while those fixes continue. All18direction×3roundlive quality cells stay NOT RUN; corpus/reference licensing, independent qualified scoring/sealed custody, serving revision/tokenizer/template/context attestation, en↔vi noninferiority and perf/cost, production traces/egress/multiworker limits and release decision remain blocked. GateC not entered. Flags defaultOFF; DuyTa only; auxiliary ours0/shared3of3 exhausted. Counters core3/5,FE1/5,assessment3/5,integration0/5productfixes,Vanguardreturns0/3; none reset. No mainmerge/push/deploy/protected-source edits/spawns.

Machine evidence: [integration-evidence.json](integration-evidence.json). Raw logs, full JUnit, dependency manifests, exact commands and probes: `/tmp/multilang-correctdeps-evidence`. Historical records below are superseded where this checkpoint explicitly updates them; no historic run was rewritten as a new result.

---

## Historical checkpoint at report0263286 (preserved)

# Multilang CPU runtime integration checkpoint

2026-10-04 local / 2026-10-03 UTC. **Intermediate runtime checkpoint complete; release BLOCKED.** Assessment is excluded pending independent acceptance. This is not whole-feature completion, a green full suite, a clean file/OCR merge, or permission to enable flags/main-merge/deploy.

## Exact revisions and review authorization

| Item | Exact SHA / disposition |
|---|---|
| Refreshed origin/main and base | `ce9cd47ee1273018fec1c5cbab944a86d68d6ed5`; fetched read-only before integration and at closeout |
| Planning authorized | `ef1c990310dd886f1a8ebe18796e18f6134880a6` |
| Core authorized | `f6b7834d5fb215075cb0cdbc448e3b538d93f7b3`; independent round 4 scoped PASS, 17 repros |
| FE authorized | `788da975cf9eed0e0bf72089fd137e00fd8ad2b2`; round 2 PASS plus API docs; canonical nested allowlisted snapshot intentional |
| Security authorized and merged | `4883e87d5680862e2296aed7a8254e6addb6fbb5`; 13 independent test cases, historical 19-case old-core CPU report |
| **Runtime code candidate tested** | **`43e5f2233256a74b091c41b3f14b325f44161a6a`** |
| Runtime candidate tree | `002bb5b3def8b36efe5c331142fed957b6df7de7` |
| Report commit | Documentation/evidence-only descendant of the code candidate; obtain exact report commit with `git log -1 --format=%H -- docs/test-results/multilang/integration-report.md`. Root scratch checkpoint and delivery record its exact SHA after commit. Report commit is not the tested code SHA. |
| Assessment excluded | Observed `368afcd7e9ee02c3b9ad850ef4229bdc45cc6d9f`; independent round 2 six remaining; fix cycle 2 ongoing |
| Security final-core report READ ONLY | `11ec898bd712ba1add2bf56c0ac4f9918c5b1bf8`; tested security merge `4cb54d9de5e0eae088d339a860146465a8b37234`, 23 selected passes; not authorized for merge and not merged |
| Protected import | `bfab8ebc96e25b23325fd72a1d3bf034e9aea315` |
| Protected OCR | `ff311c12f97a1dd453495b0ff2b9ca269a49aba2` |
| Disposable combined candidate | `eb984d4d1d689b1eabc7e4be9bb5b62671e9196c`; tree `0cfdb23329a78abb8d6726d560288b512ef7c4d9` |

Read root AGENTS, frozen criteria/contract, root STATE and full independent core rounds 1–4 (including detailed first report), assessment rounds 1–2, FE rounds 1–2, Warden checkpoints, and both full Vanguard reports/plans. Review gates updated before accepting their exact commits; no coder summary substituted for review. Root STATE/LOG ownership preserved; these reports and scratch checkpoint carry this integrator's updates.

Four clean authorized merges only: planning → core → FE → security. No conflict resolution or new product code on integration branch. All base-tracked legacy source files are unchanged; diff under `src/` consists solely of the 11 new multilang files. Old sibling `secai/secai-fe-devspace` unchanged. No assessment or later unapproved report/test commit was silently merged. No protected branch, runtime environment, `.env`, service, main branch, or remote push changed.

## Full regression and retained test IDs

| Run | Total | Passed | Failed | Skipped | Errors | Result |
|---|---:|---:|---:|---:|---:|---|
| Assessment baseline at base | 925 | 905 | 19 | 1 | 0 | Frozen baseline, not green |
| Exact runtime `43e5f22` | 984 | 964 | 19 | 1 | 0 | Exit 1; 156.59s; all 925 baseline IDs/outcomes retained; +46 core, +13 security; zero new failure IDs |
| Combined with exact assessment deps initially | — | — | — | — | 1 collection error | Missing `olefile`; retained as failed setup evidence, no deselection |
| Disposable combined with private dependency supplement | 1091 | 1071 | 19 | 1 | 0 | Exit 1; 166.82s; all 925 baseline IDs/outcomes retained; zero new failure IDs |
| Additional guarded composition probes | 7 | 7 | 0 | 0 | 0 | 8.62s; separate from full-suite count |

All **1,014 import checkpoint IDs** and all **966 OCR checkpoint IDs** also remain in the combined suite, with no changed outcome, against their archived full JUnit. No collection selection/deselection was used in either full run. `integration-evidence.json` records every baseline ID, all new IDs, exact failures/skips, dependency inventory, config/file hashes and original command. Baseline source: assessment `baseline.json` and `.venv/baseline.raw.junit.xml`; protected comparison: `/tmp/secai-import-suite/junit-r1-fix.xml` and `/home/ict/secai-ocr-evidence/final-pytest-349df3c/full.junit.xml`.

Both full runs used the assessment `.venv/bin/python` read-only, **Python 3.12.3 / pytest 9.1.1 / pytest-timeout 2.4.0 / pytest-asyncio 1.4.0 / Linch 1.2.1 / redis 8.0.1**, correct checkout working directory and explicit `PYTHONPATH=<checkout>/src`; import identity asserted before pytest. The measured installation has **64 distributions**, recorded individually; the brief's “75” corresponds to assessment's scoped test count, not an assumed package inventory. Environment mirrors `.venv/run-postchange.sh`: clean environment, private HOME/cache/HF/data/catalog/artifact paths, memory sessions, empty Redis, offline HF/Transformers, unavailable classifier, denied-loopback proxies and bounded DNS attempts. Bytecode disabled additionally. No shared runtime/package/.env mutation. Test command: `timeout 600s <assessment-python> -m pytest -q --tb=short --timeout=10 --timeout-method=signal --junitxml=<private-output>`. No live translation, external-service evaluation or package download. Proxies are isolation guards, not an exhaustive egress security certification.

Combined supplement reuses only installed **olefile 0.46**, **python-multipart 0.0.32**, **python-docx 1.2.0**, **lingua-language-detector 2.0.2** via private read-only symlink paths appended to PYTHONPATH, retaining all baseline package versions. Lingua is the inherited import CPU dependency, not a new translator or new downloaded auxiliary checkpoint. **Important limitation: installed olefile 0.46 is below the protected declaration `>=0.47,<1`; combined results are compatibility exploration, not acceptance of the declared dependency environment.** No network install was needed/attempted. Exact package locations and versions recorded. Installed Pillow 12.3.0 and lxml 6.1.3 came from assessment environment.

Exact retained 19 failure IDs (baseline attributes one to external Bughunt, five to missing isolated PoC data, thirteen to 10-second timeout; this does not establish harmlessness):

```text
tests/test_bughunt_phase0.py::test_bugbounty_root_and_arsenal
tests/test_chat_poc_drilldown_regression.py::test_uc2a_search_log4shell_surfaces_log4j_scan_with_valid_ref
tests/test_chat_poc_drilldown_regression.py::test_uc2b_fetch_same_run_returns_real_grounded_source_code
tests/test_chat_poc_drilldown_regression.py::test_uc2c_ref_reused_in_a_different_turn_run_is_rejected
tests/test_chat_poc_drilldown_regression.py::test_uc2d_resear_in_new_run_yields_a_fresh_fetchable_ref
tests/test_chat_poc_drilldown_regression.py::test_uc4_fetch_budget_enforced_within_a_single_chat_run
tests/test_consolidate.py::test_exploit_endpoint_consolidated_tier
tests/test_exploit_metadata_grounding.py::test_exploit_endpoint_cwe_refs_empty_when_no_cve_record
tests/test_exploit_metadata_grounding.py::test_exploit_endpoint_populates_cwe_refs_from_cve_record
tests/test_exploit_metadata_grounding.py::test_exploit_stream_endpoint_populates_cwe_refs
tests/test_exploit_relevance_gate.py::test_exploit_freetext_at_threshold_still_tiers_real
tests/test_exploit_relevance_gate.py::test_exploit_genuine_freetext_no_cve_still_tiers_real
tests/test_exploit_relevance_gate.py::test_exploit_named_cve_bypasses_freetext_gate
tests/test_exploit_relevance_gate.py::test_exploit_offtopic_freetext_gated_to_not_found
tests/test_exploit_relevance_gate.py::test_exploit_vi_generic_hack_gated_even_with_a_passing_score
tests/test_gateway.py::test_exploit_chitchat_returns_cve_guide
tests/test_gateway.py::test_exploit_stream_section1_preview_arrives_before_final
tests/test_gateway.py::test_legacy_contract_paths_and_profiles
tests/test_gateway.py::test_streaming_endpoints
```

Exact retained skip: `tests/test_chat_poc_drilldown_regression.py::test_uc3_license_gated_or_unfetched_poc_returns_metadata_only_not_fabricated`.

## Actual composition, conflicts and inherited defects

`git merge-tree --write-tree` results: main+ours clean, ours+import clean, ours+OCR clean. **Import+OCR conflicts independently of multilang**, same two source paths as the prior overlap report. Raw merge-tree outputs and hashes retained in private evidence. Disposable detached worktree `/tmp/secai-multilang-combined-runtime` includes committed main (ancestor), runtime candidate, import and OCR; no owner WIP imported.

| Conflict / exact hunk | Temporary resolution and provenance |
|---|---|
| `src/secai_gateway/prompts/profiles.yaml`, after legacy translate / before translate_v2, ~line 58 | Keep OCR's entire additive `ocr_correction` mapping (`prompt: ocr_correction.md`, `tools: []`, `base: false`, `max_turns: 2`) and import's existing `translate_v2` mapping. No competing value overwritten. `ocr_correction.py:98` calls profile `ocr_correction`; worker.py allows both; imported OCR correction tests assert this profile. |
| `src/secai_gateway/translation/settings.py`, after worker_db_path, ~line 23 | Keep import's `converter_image: str = "secai-doc-converter:1"`; OCR side lacks this one field and otherwise matches. `documents.py:312,339` reads it for image inspect and networkless converter invocation; imported real DOC converter smoke passed. |

Only conflict markers removed to produce this minimal union, only in the disposable tree. No source invention, no conflict resolution merged back into feature or protected branches. These temporary choices are **not accepted product integration**. Committed simulation exists solely to give tested composition an exact SHA/tree. No cleanmerge claim for file+OCR.

Owner WIP rechecked at closeout: import only untracked `.venv-import/` and gateway `AGENTS.md`; OCR only untracked gateway `AGENTS.md`; protected HEADs unchanged. Read `/home/ict/AIEngines/secai/secai-agent-gateway/docs/loop/STATE.md`: task **STOPPED/BLOCKED**, two rounds with two High findings. Existing problems remain **unresolved**, independently reproduced on combined tree:

- **R2-01 High**: expired failed job still retained input after cleanup window with worker/reaper down; new admission returned STORAGE_UNAVAILABLE.
- **R2-02 High**: CJK-adjacent URL had no protected span; fake provider mutation of that URL was accepted.

No fix, reopening, new review round or file/OCR quality acceptance. These confirmations are observed defects, not passing security tests.

## Runtime / lifecycle evidence

Seven socket-guarded private probes exercise the real combined factory, not a replacement legacy app: master OFF/ON × v2 OFF/ON, capabilities GET/disabled bidirectional POST, legacy and new OpenAPI boundaries, separate legacy/v3 HTTP clients initialized and closed, legacy lifespan forwarding, v2 store availability, and new routes unavailable with missing optional tokenizers. Replayed imported standalone worker subprocess/API/job/result assertions through the outer factory for master OFF and ON; same-language synthetic TXT processing invokes no model. Worker is independently supervised, **not started by web lifespan**; forwarding must preserve its API/store handoff, which these probes exercise. Native imported OCR/unit and networkless DOC converter tests were included in full run. Original core/Vanguard tests exercise new quota/CORS, cancellation, admission, request isolation and fake-provider branches; no claim of production ingress-wide quotas or live provider behavior.

Additional deterministic fresh subprocess comparison with PYTHONHASHSEED=0: all **69 legacy OpenAPI paths / 15 schemas**, vi→en and en→vi JSON and SSE responses, empty/bad target validation exactly equal between base, runtime OFF/ON, and combined OFF/ON. Serialized schema+response SHA256 `db5e5df0a891c91d9c372a67524fbc8ff16513f6d4a7f03640d31eb217f60ad2`. A fake legacy runner was injected only in test memory. This is narrow behavioral evidence, not all chat/live behavior.

## Frontend, API docs and dependency evidence

Canonical FE path `secai/secai-agent-gateway/secai-fe-devspace`. Node **24.15.0**, npm **11.12.1**; `npm ci --offline --no-audit --no-fund` installed 253 packages, then `npm test` **21/21 PASS**, `npm run build` **PASS**. Existing bundle warning (~612kB) and whatwg-encoding deprecation remain. Actual installed package versions compared to every lock entry: **253 matching, zero mismatches, zero missing required dependencies**; omitted platform-specific optional packages recorded. Lock SHA256 `9ef40829cd3c6b2c2d18c342deda83209e36dd3c348bb0059fa863b9bcc9eb1f`. DOMPurify3.4.16, Vite6.4.3, esbuild0.25.12, Vitest4.1.11 match reviewed lock.

Supply-chain plan: no new dependency versions/model downloads; verify lock/inventory offline and reuse FE author's same-lock **2026-10-03 npm audit0**. **No fresh advisory-network audit, Semgrep or CodeQL scan performed; no fabricated security-clear statement.** Existing audit freshness is bounded to that report/date, not a new scan.

Read API docs on authorized FE commit and runtime. Request schema, languages, errors, no-store headers, defaults, fixed DuyTa, tokenizer evidence gate, identity and restart semantics match inspected implementation. **INT-RC-01 | DOC-01 | API guide opening line still references core42f8d40 instead of finalf6b7834 | Low provenance mismatch | FE/root should refresh documented exact core SHA in a separately authorized docs change.** No runtime contract mismatch reproduced. No owner API doc edited here.

Root owns actual HTTP/browser final-candidate smoke and temporary staging restart. Candidate and exact path were promptly published to `/home/ict/secai-multilang-wt/.superpowers/sdd/multilang-translate/integration-runtime-checkpoint.md`. This integrator did not restart staging. Earlier browser reports are prior-SHA fake-LLM evidence; **final-candidate browser replay pending root** at this checkpoint. Browser evidence cannot certify live quality.

## Remaining gates, counters and release

Core fix cycles **3/5**, FE **1/5**, assessment **2/5 ongoing**, integration **0/5 product fixes**, Vanguard returns **0/3**. Other file task two review rounds / one fix cycle and unchanged two High remains stopped. No counter reset. Auxiliary checkpoints **shared3/3 exhausted, multilang0**; no new auxiliary checkpoint, alternative runtime, GPU session or Vast rental. DuyTa only; all default language flags OFF. CPU mock flags only in private tests.

Assessment independent acceptance and explicit merge authorization remain pending; after acceptance, **a new final integrated SHA and full-suite run are still required**. Security final-core report was read in full (23 selected passes, no new actionable finding) but only original authorized4883e87 merged. Later security test/report merge requires explicit authorization. Runtime snapshot remains stable for root browser work.

Release remains BLOCKED by quality/corpus/reference licensing, qualified independent adjudication/sealed custody, loaded checkpoint/tokenizer/template/context attestation, 18 directions ×3 rounds, en↔vi noninferiority and perf/cost thresholds, production trace/egress and multiworker/security evidence, final assessment-integrated regression/browser gates; composed file/OCR additionally has two unresolved High and dependency mismatch. No live percentages, cost, model quality, native review or blanket security PASS claimed. Gate C not entered; not declared N/A without Gate B evidence. See final-report.md for the 18-direction NOT RUN matrix. User future mainmerge confirmation and a separate rollout decision remain required; this checkpoint performs neither.

Machine-readable evidence: [integration-evidence.json](integration-evidence.json). Raw synthetic test logs/JUnit/probes remain `/tmp/multilang-integration-evidence`; no secrets, shared runtime cache or raw private user data copied. Hashes recorded for reproducibility. `git diff --check` passed before reporting.
