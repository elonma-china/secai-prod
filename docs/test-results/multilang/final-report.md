# Multilang final report — intermediate CPU runtime checkpoint

2026-10-04. **BLOCKED release; runtime integration checkpoint complete, assessment integration incomplete.** This report intentionally does not mark the whole feature complete.

Tested code SHA **`43e5f2233256a74b091c41b3f14b325f44161a6a`**, tree `002bb5b3def8b36efe5c331142fed957b6df7de7`. Report/evidence commit is a documentation-only descendant, recorded by exact SHA in root scratch checkpoint after committing. Do not substitute report commit for tested runtime SHA. Authorized parents: planningef1c990, coref6b7834 (round4 PASS), FE788da975 (round2 PASS/API docs), security4883e87 only. Refreshed main remainsce9cd47. Full provenance, hashes, commands, every retained baseline ID and exact19failure IDs: [integration-report.md](integration-report.md), [integration-evidence.json](integration-evidence.json).

| CPU evidence | Result |
|---|---|
| Exact runtime full suite | **984 cases:964pass,19fail,1skip,0error**; all925baseline IDs/outcomes retained; zero new failure IDs; **suite not green** |
| Disposable main+runtime+import+OCR | **1091 cases:1071pass,same19fail,1skip,0error**; all1014import and966OCR checkpoint IDs/outcomes retained |
| Composition/lifespan/standalone worker | **7/7** guarded private probes; v2/v3 flagsOFF/ON, GET/disabled POST, client startup/shutdown, actual worker handoff, optional tokenizer absent |
| Legacy comparison | 69paths/15schemas and both en↔vi JSON/SSE equal to base with wrapperOFF/ON and composedv2 |
| Frontend | Offline npmci;253 installed versions match lock;21tests/buildPASS; existing bundle warning |
| Dependency/security evidence | Prior same-lock FE audit0 reused (2026-10-03); no fresh advisory/CodeQL/Semgrep scan. Vanguard finalcore23selectedPASS report11ec898 read only, not merged |
| Final candidate real HTTP/browser | Root owns replay; exact candidate supplied promptly; pending root evidence, no integrator staging restart |

Disposable simulation SHA `eb984d4d1d689b1eabc7e4be9bb5b62671e9196c` contains temporary union of **inherited conflicts** in profiles.yaml (retain OCR correction profile) and translation/settings.py (retain import converter_image). It is not accepted product code, not merged into feature or protected branches, and **file+OCR is not a clean merge**. Both known file High findings still reproduce: terminal input/quota retention without worker, and Unicode-adjacent URL mutation. That task remains STOPPED/BLOCKED, not fixed or reopened. Supplemented installed olefile0.46 is below protected requirement>=0.47; combined dependency acceptance is blocked. Assessment baseline packages otherwise unchanged.

API guide matches inspected wire behavior but its opening core42f8d40 provenance is stale (Low; root/FE follow-up). Protected legacy source and old FE sibling unchanged. No mainmerge, push, deployment, language enablement, new model/checkpoint or sharedenvironment/.env mutation.

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

- Assessment round2 has six remaining findings, fix2 ongoing. Do not merge until independent acceptance and exactSHA authorization. Then rerun full final integratedsuite and affected composition/browser checks.
- Security finalcore report11ec898 records23selectedpasses at securitymerge4cb54d9; separately authorize any merge of that test/report delta. CPU replay does not clear release security, production trace/privacy, ingresswide quota, scanners, or actual serving identity.
- Root finalHTTP/browser smoke must reference runtime43e5f223 (or later newly tested codeSHA). Prior-SHA browser evidence remains historical, even though only the script branch changed afterward.
- Corpus licensing/reference independence, serving revision/tokenizer/template, qualified scoring and sealedTEST custody,18direction×3roundquality, en↔vi noninferiority/perf/cost margins and release decision remain blocked. GateC not entered. Both directions remainOFF for every new language.
- Counters: core3/5,FE1/5,assessment2/5ongoing,integration0/5productfixes,Vanguardreturns0/3. Auxiliarycheckpoint allowance shared3/3 exhausted, ours0; DuyTaonly. Other file taskSTOPPED after2rounds with2High unchanged.

**Future main merge requires user confirmation after remaining gates. Production deployment/enablement is a separate decision.** Nothing here grants either approval.
