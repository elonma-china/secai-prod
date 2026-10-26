# Import file translation — independent code review, round 1

Date: 2026-10-04. Candidate `88a8ea8709532ace977db405c6d3400dd9d0eb4e` against frozen base `ce9cd47ee1273018fec1c5cbab944a86d68d6ed5`. Review scope is the gateway backend in this worktree. No feature code, tests, root state/log, main branch, deployed service, GPU, or live model was changed or used.

**Code review verdict: FAIL.** The two reproduced behavior defects below violate the frozen job and technical token contracts. This is an independent code review, not the later independent tester/Vanguard/integration or release decision. Most corpus and operational criteria remain **BLOCKED** for missing evidence, even where unit tests pass.

## Impact map and regression comparison

The diff adds `api/translate_v2/routes.py`, `translation/{documents,jobs,service,settings,technical_tokens,types,worker}.py`, the DOC converter image/scripts, one new `translate_v2` profile/prompt, contract/criteria/plan documents, and tests. Shared production changes are `app.py` (v2 router/store and access filter), `api/middleware.py` (v2 rate/CORS handling), `agent/runner.py` (date-context exclusion for new profiles), `prompts/profiles.yaml`, `pyproject.toml`, and `uv.lock`. The existing `/ai/translate` router and prompt are not edited. Blast radius: every application import/startup sees `app.py` and the revised lock; all HTTP requests traverse middleware; `runner.py` date logic is profile-scoped. The 74 added collected tests span 11 test modules, including one existing prompt test.

`pyproject.toml` already required `linch>=1.2.1,<2` on the frozen base, while its lock resolved 1.1.0; candidate resolves 1.2.1. That correction is necessary for lock consistency, but it is a shared runtime dependency change and merits the downstream compatibility/dependency gate. The read-only interpreter used here reports Linch 1.2.1. The candidate lock also includes the newly declared multipart, Word, OLE, Lingua, and regex dependencies. No network dependency scan was run.

Archived full JUnit comparison was done by exact `(classname, test name)` identity, not pass counts: `/tmp/secai-import-suite/junit-final.xml` has 999 cases, 979 passed, 19 failed, 1 skipped, 0 errors; base `.../docs/test-results/translate-baseline.full-timeout.junit.xml` has 925 cases, 905 passed, 19 failed, 1 skipped, 0 errors. All 925 base node IDs occur in candidate, there are 74 new IDs, and the **failure-ID symmetric difference is empty**. This supports no new failure *in that harness*. It does not make the existing 19 failures pass: one relies on a missing external Bughunt tree, five on an absent isolated PoC catalog entry, and 13 hit the 10-second test timeout. The baseline report does not establish that all would pass with real dependencies. The natural skip is identical. The author-reported focused 122 passes were not independently reproduced; the 10 v2 modules selected here yielded 73 passes. `git diff --check` passed.

The first focused invocation inherited an ambient `/srv/exploitdb` path and stopped at collection with 9 permission errors. A second invocation with `env -i` removed the user site packages and stopped at collection with `httpx` missing. Those are **harness errors, not product failures**. The final focused invocation supplied private `/tmp/secai-review-import` database/artifact paths, `PYTHONPATH=$PWD/src`, memory sessions, and empty Redis URL; it ran only this worktree's tests and passed 73/73 in 9.39 seconds. Interpreter: `/home/ict/secai-translate-import-wt/.venv-import/bin/python` (symlink resolves to `/usr/bin/python3.12`, venv prefix remains the import worktree). It was used read-only. No packages were installed.

## Reproduced findings

| ID | Criterion/test | Evidence (path, line, reproduction) | Severity | Recommendation |
|---|---|---|---|---|
| R1-01 | IMP-11, queue deadline includes wait; eventual terminal state | `translation/jobs.py:75-93` returns queued rows without checking `deadline`; `jobs.py:173-196` evaluates a queued deadline only in `claim()`. With a private temp store and `job_deadline_seconds=1`, `admit(..., now=100)` followed by `get(job_id, now=102)` returned `queued`. If the standalone worker is absent or stopped, the API can report queued indefinitely and keep input bytes/active IP quota; `reap()` only handles terminal/expired rows (`jobs.py:254-275`). | **High** | Enforce overdue queued-job terminalization through a path that runs independently of claims (for example transactional read/reaper handling), then test worker-offline status, result, quota release, and artifact expiry. |
| R1-02 | IMP-08/IMP-19, technical token protection in CJK and chunk integrity | `translation/technical_tokens.py:35` uses Unicode `\b` around CVE/CWE; `literal_spans('中CVE-2024-1234中')` returned `()`, so this valid adjacent token is sent as prose. Independently, `translation/service.py:76-103` first tokenizes an entire nonspace CJK run as one piece and then slices it by grapheme. For 2,980 CJK characters, an inline-code CVE literal, then 3,000 CJK characters, `protect_literals()` produces one placeholder, but `_chunks(...,3000)` returned marker counts `[(1,0),(0,1),(0,0)]`: the placeholder is split between model calls. A model cannot be required to return halves as one exact marker. | **High** | Recognize technical literals adjacent to CJK without relying on Unicode word boundaries. Split text around protected marker matches before grapheme chunking, never inside a marker. Add both reproductions as regression cases, including an actual fake model that demonstrates restoration failure. |

The repro snippets were executed with this worktree's `src` on `PYTHONPATH` and only private temporary paths. They did not call a model or network service.

## Criterion disposition

`PASS` requires all evidence in `docs/criteria/import-file-translate.md` on the exact final composition SHA. `FAIL` records an observed violation. `BLOCKED` records missing evidence, not an inferred defect. The import branch's image route is present, but the OCR counterpart branch is not composed here; image and full pipeline acceptance remain pending.

| ID | Status | Evidence and remaining requirement |
|---|---|---|
| IMP-01 | **BLOCKED** | Exact JUnit node comparison above supports no added failures. Shared diff is small, but normalized legacy OpenAPI and complete HTTP JSON/SSE comparison were not produced; the inherited 19 failures remain unresolved. |
| IMP-02 | **BLOCKED** | `routes.py:139-153` restricts extensions and signatures; `documents.py:149-169,293-305` validates deeper Word structure. Focused tests cover examples, but the specified full multipart fixture matrix/goldens is absent. |
| IMP-03 | **BLOCKED** | `documents.py:54-85` implements strict BOM/UTF-8 and heuristic CP1258 with warning; selected unit tests pass. Byte-provenance goldens and complete ambiguity/boundary matrix are absent. |
| IMP-04 | **BLOCKED** | `documents.py:149-290` extracts body, cells, headers/footers and footnotes. Annotated DOCX/DOC occurrence/order goldens across the fixed corpus are absent. |
| IMP-05 | **BLOCKED** | `documents.py:88-129,319-353` checks ZIP/XML and runs a bounded networkless Docker converter. Unit assertions pass; no real converter isolation/outbound/timeout/cleanup evidence was run here. `secai-doc-converter:1` existence was reported, not treated as a completed probe. |
| IMP-06 | **BLOCKED** | `routes.py:90-145` limits streamed request/file bytes and multipart fields; extraction runs in threads. Exact below/equal/above and disconnect, RSS, cleanup, event-loop latency evidence is missing. |
| IMP-07 | **BLOCKED** | `service.py:107-170` translates blocks sequentially and limits chunk count; no full corpus block coverage, model token-budget measurement, or out-of-order/provider failure evidence. R1-02 risks a valid long CJK input. |
| IMP-08 | **FAIL** | R1-02 reproduces failure to protect a CJK-adjacent CVE and splits a protected marker across chunks, contrary to technical span handling. Full token occurrence and live tests also remain pending. |
| IMP-09 | **BLOCKED** | `profiles.yaml` declares `translate_v2` tool-less/base false; `runner.py` only adds this profile to the date exclusion. Real provider payload/tool registry, prompt injection, and private worker ledger/session canary evidence is absent. |
| IMP-10 | **BLOCKED** | `jobs.py:146` uses `secrets.token_hex(16)` and routes need no auth; `app.py:24-29` filters Uvicorn access entries. Two-client tests pass; relevant proxy/application log canary inventory and expiry privacy evidence are missing. Possession-by-ID is the approved policy, not a defect. |
| IMP-11 | **FAIL** | R1-01 violates deadline-in-queue behavior. Claim fencing and cancel paths have unit coverage, but full two-process/restart tests are still missing. |
| IMP-12 | **BLOCKED** | `jobs.py:115-136,160-171,254-275` transactionally stores key hashes and handles tombstones. Frozen-clock cross-process concurrent replay/reuse evidence is incomplete. |
| IMP-13 | **BLOCKED** | `jobs.py:95-105,137-145` has transactional IP/queue/quota checks; middleware scopes v2. Cross-process web and actual 60th/61st lookup plus proxy-trust tests remain. |
| IMP-14 | **BLOCKED** | `app.py:87-95` defaults v2 off and catches store startup failures; route errors are shaped and DOC/OCR dependencies are separately checked. Complete dependency failure matrix and legacy startup comparisons remain. |
| IMP-15 | **BLOCKED** | No fixed 62-case manifest, 186 live rows, bilingual proposition rubric, or three shuffled rounds supplied. This is a later tester gate, not a code bug by itself. |
| IMP-16 | **BLOCKED** | Staging p50/p95, RSS, throughput, no-OOM and incumbent comparison measurements are absent. No GPU/live service was invoked. |
| IMP-17 | **BLOCKED** | Independent tester, Vanguard, composition-SHA integration, scans, and release docs are downstream gates; this review cannot certify them. |
| IMP-18 | **BLOCKED** | `routes.py:22,126-131` exposes 10 codes and rejects explicit non-vi cross-pairs; unit admission tests pass. All 18 directions, auto detection errors, identity, and qualified live per-direction rubric are missing. |
| IMP-19 | **FAIL** | R1-02 reproduces CJK-adjacent literal omission and placeholder splitting in a no-space CJK run. Full Arabic/Cyrillic/combining and live script evidence remains pending. |
| IMP-20 | **BLOCKED** | No independent language-qualified adequacy review/reference for all 18 directions is present. |

## Release gates still pending

| Gate | State | Required next evidence |
|---|---|---|
| Code review | **FAIL** | Resolve R1-01 and R1-02, then review the corrected exact SHA. |
| Independent TEST | **PENDING** | Run fixed import corpus and multilingual proposition/token rubric independently on final SHA. |
| Vanguard | **PENDING** | Upload, archive, parser, resource, logging, concurrency and injection assessment on final SHA. |
| OCR composition/integration | **PENDING** | Compose `feature/image-ocr-translate`, rerun file/image/full-pipeline gates and record composition SHA. |
| Release | **BLOCKED** | All mandatory criteria PASS on final composition, scans and operational evidence complete; no main merge or production action authorized by this review. |

## Commands and artifact references

Focused command (run from this gateway directory; all test files are from this worktree):

```sh
EXPLOITDB_ROOT=/tmp/secai-review-import/exploitdb POC_CATALOG_DB=/tmp/secai-review-import/poc-catalog.db POC_SOURCES_ROOT=/tmp/secai-review-import/poc-sources POC_ARTIFACT_STORE=/tmp/secai-review-import/poc-store GATEWAY_DB_PATH=/tmp/secai-review-import/gateway.db SESSION_DB_PATH=/tmp/secai-review-import/sessions.db SESSION_STORE_BACKEND=memory REDIS_URL='' PYTHONPATH="$PWD/src" /home/ict/secai-translate-import-wt/.venv-import/bin/python -m pytest -q tests/test_translate_v2_boundary.py tests/test_translate_v2_doc_converter.py tests/test_translate_v2_documents.py tests/test_translate_v2_job_access.py tests/test_translate_v2_jobs.py tests/test_translate_v2_language.py tests/test_translate_v2_ocr_tokens.py tests/test_translate_v2_service.py tests/test_translate_v2_worker.py tests/test_translate_v2_worker_process.py
git diff --check ce9cd47ee1273018fec1c5cbab944a86d68d6ed5 88a8ea8709532ace977db405c6d3400dd9d0eb4e
```

Full historical candidate JUnit: `/tmp/secai-import-suite/junit-final.xml`; exact baseline JUnit/report: `/home/ict/secai-translate-baseline-wt/secai/secai-agent-gateway/docs/test-results/translate-baseline.full-timeout.junit.xml` and `translate-baseline.md`. This reviewer inspected the raw XML, collection identities, and baseline harness description; it did not rerun the full 999-test suite. The existing 19 baseline failures are not all known product defects and are not certified as harmless.
