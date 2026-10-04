# Multilang final report — assessment-integrated CPU checkpoint

2026-10-04. **CPU implementation and authorized final integration complete; full suites NOT GREEN; live quality/release BLOCKED.** Final independent Warden permits CPU implementation handoff; see [Warden verdict](../../reviews/multilang/warden-final.md). Assessment round 5 independently passed and is now integrated.

The report commit is a documentation-only descendant recorded after commit in root scratch integration-assessment-final-checkpoint.md. Resolve it with `git log -1 --format=%H -- docs/test-results/multilang/integration-report.md` from the gateway directory. It is not the executed test or browser SHA.

- **Runtime/test SHA:** `877e619514ed2a5ed7dcd5a2353c02b03ca295b4`, tree `bd049b0f3469bcdf6a0d666586ce23adb9ab746e`.
- **Disposable combined test SHA:** `e6856eda87bbb10668d06a3f2b71896216abb673`, tree `2ce7eda9150b2aaf938c34e618e7651927391e81`.
- Accepted assessment `f38801385321b07fd4bc210f5e2d751d1d727795` merged via `1f31ef39abf6e8be6437c0e8bdd92011bd71da15`; authorized rootdocs `aa11407fee4fc06938c5767c10a7b6fcbddb9dc4` merged via the runtime/test SHA. Both clean non-squash merges. No new product fix; core, FE and protected sources unchanged.

| Evidence | Result |
|---|---|
| Previous fresh correct-environment baseline, reused | 925 cases: 905 passed / 19 failed / 1 skipped / 0 errors |
| New runtime full suite | **1,140 cases: 1,120 passed / 19 failed / 1 skipped / 0 errors**, exit 1 |
| New combined full suite | **1,247 cases: 1,227 passed / 19 failed / 1 skipped / 0 errors**, exit 1 |
| Assessment / Vanguard within each new full suite | **152/152 and 17/17 PASS**, included above |
| Case preservation | All 925 baseline, 1,014 import, 966 OCR, 988 prior runtime and 1,095 prior combined cases retained unchanged; zero new failures or missing IDs |
| Dependencies | 67 packages = 57 unchanged baseline versions + 10 extras; 17 requirements, seven metric pins and `uv pip check` PASS |
| New import/config/metric coexistence probes | PASS in both exact snapshots under a denied-socket guard; flags OFF |
| Reused composition and legacy evidence | Seven probes passed; 69 paths/15 schemas and JSON/SSE fingerprints identical by verified source/dependency identity; not rerun |
| Reused FE/browser | 21 tests/build PASS; actual HTTP/browser executed at 43e5f22 with fake LLM; complete source/lock identity verified, no new execution |
| Partial Semgrep | Runtime: 14 files, nine units, seven complete/two partial. Final assessment: seven files, six units, four complete/two partial. Zero findings; rule errors retained; CodeQL NOT RUN |

Both suites used the same private/offline `/tmp/secai-multilang-final-env/bin/python`, explicit snapshot PYTHONPATH and original 600-second overall/10-second per-test command. No deselection, timeout changes or third baseline run. Exact commands, all case outcomes, full failures and skip details: [integration-report.md](integration-report.md), [integration-evidence.json](integration-evidence.json). Existing failures remain one external Bughunt dependency, five unavailable private PoC/catalog cases and 13 timeouts; one catalog skip remains.

Refreshed main ce9cd47, import bfab8eb, OCR ff311c1 and owner WIP are unchanged. Main/runtime, runtime/import and runtime/OCR merge-trees are clean. **File+OCR still conflicts in profiles.yaml and translation/settings.py.** The disposable combined tree inherits the same minimal unions from 0097da3; this is not accepted file/OCR integration. File owner remains STOPPED with two High findings: expired failed-job input/quota cleanup and Unicode-adjacent literal mutation. Earlier reproductions are reused through identity, not newly run. The original worktree's 33 sibling FE deletions and other untracked files remain untouched.

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


## Remaining gates, resources and handoff

Gate A still needs live baseline, serving revision/tokenizer/template/context/license/engine/quantization attestation, a coordinated resource lease and cost quota. Licensed independent corpus/references, calibrated qualified evaluation/adjudication and sealed holdout/tester custody remain pending. Gate B DEV ablations are NOT RUN; Gate C is NOT ENTERED (not N/A without Gate B evidence); Gate D is BLOCKED. Required ≥70% rubric per direction/round, off-target ≤2%, entity preservation 100% and 30×18×3 samples remain unmeasured. en↔vi noninferiority and performance/cost margins are pending; latency, tokens, throughput and cost are UNKNOWN/NOT RUN.

Production trace privacy, egress, real-provider behavior, multiworker quotas, rollback evidence and incomplete scanner coverage remain release gaps. The 19 baseline failures and two file High findings prevent unconditional acceptance. Browser evidence uses fake providers; real tokenizer/LLM and OS clipboard remain untested. Existing mobile header clipping and recorded health/projects/session warnings remain limitations.

The preflight credential-output incident remains **unresolved; no rotation or deletion claimed**. Root-owned staging ports 5177/5178/5180 and browser were closed per [resource closeout](resource-closeout.md); other resources remain untouched. No live/model/data/GPU calls, downloads, service changes or new auxiliary trials. Rental cost is unknown. All flags OFF; DuyTa unchanged. Counters: core 3/5, FE 1/5, assessment 4/5, integration 0/5, Vanguard returns 0/3; shared auxiliary allowance 3/3 exhausted, ours zero.

This integrator did not edit root STATE/LOG; their exact rootdocs history was authorized. No main merge, push, deployment or language enablement. The requested CPU implementation is ready for handoff while live gates remain BLOCKED. Final Warden follows; future main merge and rollout are separate user decisions.
