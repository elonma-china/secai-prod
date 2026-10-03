# Multilang final report — corrected dependency CPU checkpoint

2026-10-04. **CPU runtime checkpoint complete; assessment integration incomplete; release BLOCKED. Full suite not green.** This supersedes the prior report's dependency mismatch, pending browser, unmerged security, scanner NOT RUN and stale API provenance statuses.

Runtime/tests actually executed: **`929b19997116a2d7b193ebc44912ef1fdcaf0feb`**, tree `1bb7b6fdb9be3c25ab3d8d80e375a562ba34a5fc`. Product source remains identical to browser-executed43e5f22. Report SHA is a documentation-only descendant, recorded after commit in root scratch integration-correctdeps-checkpoint.md; it is not a newly executed test/browser SHA. Authorized security11ec898 and planning2c22262 merged cleanly; coref6b7834 and FE788da975 unchanged; assessment bf06bc4 and the owner’s later 8e53a00 **not merged**.

| Evidence | Result |
|---|---|
| Fresh base, prepared67-package environment | **925:905pass/19fail/1skip/0error** |
| Exact runtime929b199, same environment | **988:968pass/19fail/1skip/0error**, including17securityPASS |
| Disposable combined0097da3, same environment | **1095:1075pass/19fail/1skip/0error** |
| Complete ID/outcome comparisons | All925baseline,1014import,966OCR retained unchanged; zero new failures; prior984runtime/1091combined retained |
| Composed lifecycle/worker probes | **7/7PASS** with socket guards |
| Legacy fingerprints | 69paths/15schemas, en↔viJSON/SSE+invalid inputs identical base/runtimeOFF/ON/combinedOFF/ON |
| Correct dependency gap | **Closed**: olefile0.47, all17requirements and7metricpins satisfied;67packages=57unchanged+10extras; uv pip checkPASS |
| FE | Prior21tests/buildPASS reused by complete source/lock identity; no rebuild; audit remains prior same-lock author evidence |
| Root actualHTTP/browser | Passed at43e5f22 with fakeLLM; applies by verified source identity, no new-SHA execution claimed |
| Semgrep | Runtime14files/9units,7complete+2partial,0findings; assessmentbf06bc4 separate7files/6units,4complete+2partial,0findings. Compilation errors remain; not blanket securityPASS. CodeQLNOTRUN |
| API provenance | LowINT-RC-01 closed: opening core42f8d40→f6b7834 |

All full runs used `/tmp/secai-multilang-final-env/bin/python`, Python3.12.3, same600s overall/10s per-test command and private/offline paths, explicit correct snapshot PYTHONPATH and asserted import identity; no supplemental old package paths, timeout changes or deselection. Exact commands, every outcome, inherited19failure/1skip IDs, source/config/dependency hashes and before/after owner state: [integration-report.md](integration-report.md), [integration-evidence.json](integration-evidence.json). The fresh environment's baseline was measured, not assumed.

Combined SHA **`0097da34f5043a07812e5cf09385140312cc2eec`**, tree `bdf7efb04b8e938e3716c0e4c6555275010d635e`, incorporates current runtime plus committedimportbfab8eb/OCRff311c1 through previous disposableeb984d4. Source identical to previous simulation; inherited profiles.yaml/settings.py minimal unions retained, exact hunks recorded. **Not a clean file/OCR merge or product acceptance.** Both fileHigh defects still reproduce; ownerSTOP remains, HEADs/WIP unchanged. No file/OCR fixes.

Root [browser report](browser-http.md) covers actualHTTP/fakeLLM threepairs, legacyJSON/SSE, RTL/literal display, copyAPIstub and mobile width; live model quality/real tokenizer/OSclipboard remain untested, existing headerclip and health/projects warnings retained. Full23case finalcore security report merged; current17Vanguard cases pass, release security remains blocked. Partial Semgrep reports supersede oldNOTRUN wording without erasing failed-rule coverage. No product source changes, new model checkpoints, service or GPU calls, main merge, push or deployment.

## Eighteen-direction quality matrix

All cells are live-quality status, not unit-test percentages. Required rubric≥70% **each direction each round**, off-target≤2%, protectedentities100%; at least30×18×3 scheduled candidate samples. No live sample run here; latency/throughput/token/cost metrics **NOT RUN/UNKNOWN**, no fabricated zero cost. en↔vi margins remain pending baseline; old routes preserved by CPU evidence only.

| Direction | Round1 quality | Round2 quality | Round3 quality | Entity/off-target/performance | Disposition |
|---|---|---|---|---|---|
| vi → en | NOT RUN | NOT RUN | NOT RUN | NOT RUN | Legacy unchanged; noninferiority pending |
| en → vi | NOT RUN | NOT RUN | NOT RUN | NOT RUN | Legacy unchanged; noninferiority pending |
| vi → zh-Hans | NOT RUN | NOT RUN | NOT RUN | NOT RUN | OFF; release BLOCKED |
| zh-Hans → vi | NOT RUN | NOT RUN | NOT RUN | NOT RUN | OFF; release BLOCKED |
| vi → ja | NOT RUN | NOT RUN | NOT RUN | NOT RUN | OFF; release BLOCKED |
| ja → vi | NOT RUN | NOT RUN | NOT RUN | NOT RUN | OFF; release BLOCKED |
| vi → ko | NOT RUN | NOT RUN | NOT RUN | NOT RUN | OFF; release BLOCKED |
| ko → vi | NOT RUN | NOT RUN | NOT RUN | NOT RUN | OFF; release BLOCKED |
| vi → fr | NOT RUN | NOT RUN | NOT RUN | NOT RUN | OFF; release BLOCKED |
| fr → vi | NOT RUN | NOT RUN | NOT RUN | NOT RUN | OFF; release BLOCKED |
| vi → de | NOT RUN | NOT RUN | NOT RUN | NOT RUN | OFF; release BLOCKED |
| de → vi | NOT RUN | NOT RUN | NOT RUN | NOT RUN | OFF; release BLOCKED |
| vi → es | NOT RUN | NOT RUN | NOT RUN | NOT RUN | OFF; release BLOCKED |
| es → vi | NOT RUN | NOT RUN | NOT RUN | NOT RUN | OFF; release BLOCKED |
| vi → ru | NOT RUN | NOT RUN | NOT RUN | NOT RUN | OFF; release BLOCKED |
| ru → vi | NOT RUN | NOT RUN | NOT RUN | NOT RUN | OFF; release BLOCKED |
| vi → ar | NOT RUN | NOT RUN | NOT RUN | NOT RUN | OFF; release BLOCKED |
| ar → vi | NOT RUN | NOT RUN | NOT RUN | NOT RUN | OFF; release BLOCKED |

## Remaining gates and handoff

Assessment round 3 failed with five findings at bf06bc4. Read-only closeout observed fix cycle 3 committed at `8e53a00a8adf710dd630196de8341a2585e5d97a`; it remains outside this integration pending independent acceptance and authorization. Root continues independent review; only later explicit authorization of an accepted exactSHA permits its merge, followed by a **separate final integrated full suite** and affected evidence replay. No assessment test count or acceptance is claimed here.

Live quality/corpus/reference licensing, qualified independent scoring and sealedTEST custody, serving revision/tokenizer/template/context,18direction×3roundquality, en↔vi noninferiority and performance/cost margins, production trace/egress and cluster-wide limits remain BLOCKED. GateC not entered. Every new language staysOFF; DuyTaonly. Counters core3/5,FE1/5,assessment3/5,integration0/5productfixes,Vanguardreturns0/3; sharedauxiliary3/3 exhausted,ours0. Otherfile taskSTOPPED with2High.

Future main merge requires user confirmation after the remaining gates; deployment and enablement are separate decisions.
