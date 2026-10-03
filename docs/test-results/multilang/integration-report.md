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
