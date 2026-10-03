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
