# Import file translation — independent code review, round 2

Date: 2026-10-04. **Exact candidate SHA:** `bfab8ebc96e25b23325fd72a1d3bf034e9aea315`. Frozen base: `ce9cd47ee1273018fec1c5cbab944a86d68d6ed5`. Scope: gateway backend, TXT/DOCX/DOC and anonymous `/ai/v2/translate`; OCR branch is not composed. No feature code or tests, main branch, production service, network scanner, GPU, or live model was changed or used.

**Code review verdict: FAIL. `CODE_REVIEW_READY_FOR_TEST: FAIL`.** Two reproducible High findings remain in deadline cleanup and technical literal preservation. This verdict concerns current code and REVIEW-due evidence. Independent TEST, Vanguard, OCR composition, integration and release remain separate pending gates; their mere future existence is not a code defect. Overall criterion/release acceptance is **BLOCKED**, with observed criterion violations marked **FAIL** below.

## Impact and round 1 disposition

The base-to-candidate diff adds the v2 router, queue/worker, extraction, token and translation modules, scoped prompt/profile, converter image, dependencies and tests. Shared production files are `src/secai_gateway/app.py` (router/store/access log filter), `api/middleware.py` (v2 rate/CORS), `agent/runner.py` (new profile date exclusion), `prompts/profiles.yaml`, `pyproject.toml`, and `uv.lock`. All gateway imports/startups see `app.py` and the lock; every HTTP request traverses middleware. The old `/ai/translate` router and prompt are not edited. `pyproject.toml` on the frozen base already required `linch>=1.2.1,<2`, whereas its lock contained 1.1.0; the candidate lock's 1.2.1 resolves this pre-existing mismatch. The read-only test venv reports Linch 1.2.1. The lock also adds multipart/Word/OLE/Lingua/regex dependencies; dependency compatibility and scanner evidence remain later gates.

| Prior finding | Current state | Evidence |
|---|---|---|
| R1-01 High, queued deadline stalls without worker | **Partially fixed; residual R2-01 High.** Reads, admissions, claims and reaper now terminalize overdue active rows, release IP quota and fence stale success. Input cleanup still depends on `reap()`; with the worker offline, a status read after the cleanup window leaves input and reserved storage indefinitely. | `translation/jobs.py:77-87,91-106,130,187,269-291`; passing `test_offline_worker_queued_deadline_releases_quota_and_expires_artifact` explicitly invokes `reap()`; independent private-store reproduction below. |
| R1-02 High, CJK CVE omitted and marker split | **Specific reproductions fixed; residual R2-02 High.** CJK-adjacent CVE and IP are matched, and a protected marker remains intact across CJK chunks. Other required literal classes still use Unicode `\b`, so CJK/Arabic-adjacent URLs, hashes and software versions are unprotected and can be corrupted without an integrity error. | `translation/technical_tokens.py:31-43`, `translation/service.py:76-105,141-167`; passing targeted tests; independent span and fake-provider reproductions below. |

## Findings

| ID | Criterion/test | Evidence (path, line, request/test command) | Severity | Recommendation |
|---|---|---|---|---|
| R2-01 | IMP-11/13; terminal input cleanup within 5 minutes, storage quota recovery while worker is offline | `translation/jobs.py:75-87` terminalizes overdue jobs on `get`; `jobs.py:269-291` is the only input unlink/zero-byte path; `jobs.py:155-159` sums every row retaining `input_path`. Private-store probe with `deadline=1s`, `artifact_cleanup=1s`, quota 16 bytes: 12-byte job admitted at time 100; `get(now=102)` returned `failed`; `get(now=108)` still found the file; a 5-byte admission at time 108 returned `STORAGE_UNAVAILABLE`. No worker or `reap()` was running. The author's cleanup test calls `reap()` itself, so it does not cover this state. Reproduction code/command below. | **High** | Make an independent web/store operation or supervised reaper enforce terminal input cleanup and quota release within the frozen window even when claim/worker is absent. Add a no-worker test that advances past cleanup, polls status, checks disk and admits under quota without manually calling `reap()`. |
| R2-02 | IMP-08/19; exact technical literal occurrences beside supported scripts | `translation/technical_tokens.py:34,38-43` still uses `\b` for URL, hash and software/version patterns. `literal_spans('中https://example.com/a中')`, `literal_spans('中abcdef1234567890中')`, `literal_spans('中OpenSSL 3.0.1中')`, and the Arabic-adjacent URL all returned `()`. A fake provider changed the URL in `中https://example.com/a中` to `https://evil.test/b`; `translate_blocks(...,'zh','vi')` returned success with the changed URL, because `service.py:160-165` compared two empty literal lists. CVE and IP adjacent cases now match, and the old 2,980-CJK + marker + 3,000-CJK chunk reproduction yielded one intact marker in one chunk. Reproduction command below. | **High** | Apply script-aware boundaries consistently to every required technical class, define URL termination at prose boundaries, and add occurrence/alignment tests for CJK/Arabic/Cyrillic adjacency plus fake-provider mutation rejection. |

These are observed contract violations, not missing-evidence labels. The frozen contract requires input cleanup within five minutes of terminal state and 100% exact preservation of URLs, hashes and software versions, including mixed scripts.

## Tests, artifacts and legacy regression

The focused suite was run independently from **this** worktree with `PYTHONPATH=$PWD/src`, private `/tmp/secai-review-import-r2` state, memory sessions, empty Redis URL and the read-only interpreter `/home/ict/secai-translate-import-wt/.venv-import/bin/python` (venv prefix verified; executable symlink resolves to `/usr/bin/python3.12`). Result: **137 passed in 10.48s**, exit 0. This includes the real local DOC converter smoke and standalone worker-process tests, but not a complete attack/resource matrix. `git diff --check ce9cd47ee1273018fec1c5cbab944a86d68d6ed5 HEAD` exited 0. An initial isolated ad hoc repro without the private PoC environment encountered inherited `/srv/exploitdb` permission denial during import; the corrected private-env repro ran. This was a harness issue, not a test failure.

The author full-run raw JUnit `/tmp/secai-import-suite/junit-r1-fix.xml` was parsed by exact `(classname, name)` identities and compared with frozen-base `docs/test-results/translate-baseline.full-timeout.junit.xml`: candidate **1,014 collected, 994 passed, 19 failed, 1 skipped, 0 errors**; base **925 collected, 905 passed, 19 failed, 1 skipped, 0 errors**. All 925 base IDs remain, 89 IDs are new, no base ID changed status, and the failure-ID and skip-ID symmetric differences are empty; no new ID failed. The archived earlier candidate `/tmp/secai-import-suite/junit-final.xml` has 999 cases, 979 passed, the same 19 failures and one skip. The full candidate run exited 1; it is **not** a suite PASS. The baseline report attributes one failure to a missing external Bughunt tree, five to absent isolated PoC catalog data, and 13 to 10-second per-test timeouts; it does not prove those 19 are harmless product behavior. I inspected the raw XML and baseline harness account, but did not rerun the 1,014-case suite independently.

Author legacy snapshots `/tmp/secai-import-suite/{baseline,candidate_off,candidate_on}.json` are byte-identical SHA-256 `1103705b22c0f1da9f6f2ba3e7047002beaba525f349894f8815a576af12688c`. I reran `tests/legacy_translate_probe.py` from this worktree with v2 disabled and private state; its output has the same hash. The probe covers `/ai/translate` success/bad-target/empty-input JSON and `/ai/translate/stream` SSE with a deterministic fake runner. Fixed-`PYTHONHASHSEED=0` archived legacy OpenAPI captures are byte-identical SHA-256 `5462691737c35c527313e9a8391cf833176642ccbaf3ae89cf1dc20a0aa75943`; a fresh candidate `app.openapi()` capture, excluding v2 paths, equals the archived candidate capture: 69 old paths, 15 schemas, including operation IDs. The fixed seed controls pre-existing multi-method route ordering. These comparisons support narrow legacy schema/translation compatibility; they do not cover both chat routes JSON/SSE, live model behavior or all legacy validation.

## Criterion status on this SHA

`PASS` below would require **all** frozen assertions on the final composition SHA. Local unit success is cited as partial evidence; it does not turn a criterion PASS. `FAIL` means an observed violation. `BLOCKED` means required evidence is absent. The REVIEW-due assertion status is explicit in the final column; later TEST/Vanguard work remains pending independently.

| ID | Criterion status | REVIEW-due evidence and disposition | Remaining stage/evidence |
|---|---|---|---|
| IMP-01 | **BLOCKED** | Exact JUnit node comparison, deterministic legacy translate JSON/SSE and raw OpenAPI comparisons above support no detected legacy regression in those paths. Full chat JSON/SSE and ownership/validation comparisons are not demonstrated. | REVIEW evidence incomplete; later integrated replay. |
| IMP-02 | **BLOCKED** | `api/translate_v2/routes.py:139-153` admits only TXT/DOCX/DOC extensions/magic; `documents.py:149-169,293-305` performs deeper checks; focused tests pass. Full multipart spoof/corrupt/empty/timing matrix and fixture hashes absent. | REVIEW boundary evidence incomplete; TEST corpus pending. |
| IMP-03 | **BLOCKED** | `documents.py:54-85` strict BOM/UTF-8/CP1258 path and focused unit cases pass. Byte-provenance goldens and full ambiguous/binary matrix absent. | REVIEW evidence incomplete; TEST fixed corpus pending. |
| IMP-04 | **BLOCKED** | `documents.py:149-290` covers body, tables, sections, headers/footers and footnotes; selected synthetic tests pass. Annotated DOCX/DOC occurrence/order goldens and full converter coverage absent. | REVIEW evidence incomplete; TEST corpus pending. |
| IMP-05 | **BLOCKED** | `documents.py:88-130,319-353` bounds ZIP/XML and converter argv; DOC smoke passed on local synthetic fixture. Malicious fixture set, outbound observation and timeout/kill/cleanup records absent. | REVIEW security/isolation evidence incomplete; Vanguard attack pass pending. |
| IMP-06 | **BLOCKED** | `routes.py:90-145` streams request bytes with limits and `worker.py:117-130` offloads extraction. Exact below/equal/above, missing/lying length, disconnect, RSS and status latency evidence absent. | REVIEW boundary evidence incomplete; TEST load pending. |
| IMP-07 | **BLOCKED** | `service.py:107-170` sequential block translation and chunk cap; focused tests pass. Full block coverage, model token-budget proof, completion/order and provider failure matrix absent. | REVIEW evidence incomplete; TEST corpus pending. |
| IMP-08 | **FAIL** | R2-02 proves an unprotected URL can be changed and published successfully. | Full occurrence/alignment and live token gate also pending TEST. |
| IMP-09 | **BLOCKED** | `profiles.yaml:59-63` lists `tools: []`, `runner.py:223-232` applies tool override, worker uses in-memory sessions; process identity smoke passed. Actual provider payload capture, injection and private worker ledger/log canary scan for a translated job absent. | REVIEW security evidence incomplete; TEST/Vanguard runtime probes pending. |
| IMP-10 | **BLOCKED** | `jobs.py:160` uses `secrets.token_hex(16)`; anonymous routes and no-store tested; archived probe covers possession flow. Complete two-client/log-proxy canary inventory and expiry privacy absent. | REVIEW evidence incomplete; Vanguard logs pending. |
| IMP-11 | **FAIL** | R2-01 violates terminal cleanup when worker/reaper is offline. Deadline status, IP quota release and stale finish fencing pass targeted tests. | Two-process kill/restart and cancel timing still pending. |
| IMP-12 | **BLOCKED** | Transactional idempotency/tombstone tests pass. Complete frozen-clock cross-process active/terminal/expired/reuse matrix absent. | REVIEW concurrency evidence incomplete. |
| IMP-13 | **BLOCKED** | Transactional queue/IP/rate paths have unit tests; R2-01 shows retained bytes can block quota under worker outage. Multi-web-process 60th/61st request, proxy trust and cleanup-quota evidence absent. | REVIEW concurrency evidence incomplete; TEST load pending. |
| IMP-14 | **BLOCKED** | V2 default off and storage-failure isolation are tested; DOC converter and OCR availability are separate checks. Complete optional dependency/startup and safe-error matrix absent. | REVIEW evidence incomplete. |
| IMP-15 | **BLOCKED** | Fixed 62-case ×3 manifest, 186 live rows and proposition rubric absent. | Independent TEST gate, not itself a code defect. |
| IMP-16 | **BLOCKED** | No staging p50/p95, RSS, throughput, deadline or incumbent comparison. | Independent TEST resource gate. |
| IMP-17 | **BLOCKED** | This report is independent review of exact import SHA only; no tester/Vanguard/composition/integration/scanner completion. | Accumulating downstream gate; not a circular prerequisite for starting TEST once REVIEW passes. |
| IMP-18 | **BLOCKED** | `routes.py:22,126-131` supports ten codes and rejects explicit non-vi cross-pairs; focused direction tests pass. Full auto/error matrix and 18 live direction rows/rubric absent. | REVIEW matrix evidence incomplete; TEST language gate pending. |
| IMP-19 | **FAIL** | R2-02 reproduces adjacent-script URL/hash/version omission despite fixed CVE/IP and marker chunk tests. | Full CJK/Arabic/bidi/combining and live script gate pending. |
| IMP-20 | **BLOCKED** | No independent qualified bilingual or licensed public-reference adequacy evidence for all 18 directions. | Independent TEST language review gate. |

## Release gate ledger

| Gate | State | Required next evidence |
|---|---|---|
| Import CODE REVIEW → TEST | **FAIL** | Fix R2-01/R2-02 and re-review exact SHA; complete REVIEW-due boundary/security/regression evidence listed above. |
| Independent TEST | **PENDING** | 62-case ×3 file corpus, 18 directions, token/proposition rubric, real converter/provider HTTP and resource measurements. |
| Vanguard | **PENDING** | Independent security, parser, upload, logging, race and dependency scan evidence on final SHA. |
| OCR composition/integration | **PENDING** | Compose `feature/image-ocr-translate`, record exact composition SHA and rerun affected file/image/full-pipeline gates. |
| Release | **BLOCKED** | All mandatory criteria PASS, zero unresolved High/Critical findings, later gates and operational evidence complete. No merge/push/production action in this review. |

## Reproduction commands

From this worktree's `secai/secai-agent-gateway` directory, use the read-only import venv and private paths:

```sh
EXPLOITDB_ROOT=/tmp/secai-review-import-r2/exploitdb POC_CATALOG_DB=/tmp/secai-review-import-r2/poc-catalog.db POC_SOURCES_ROOT=/tmp/secai-review-import-r2/poc-sources POC_ARTIFACT_STORE=/tmp/secai-review-import-r2/poc-store GATEWAY_DB_PATH=/tmp/secai-review-import-r2/gateway.db SESSION_DB_PATH=/tmp/secai-review-import-r2/sessions.db SESSION_STORE_BACKEND=memory REDIS_URL='' PYTHONPATH="$PWD/src" /home/ict/secai-translate-import-wt/.venv-import/bin/python -m pytest -q tests/test_translate_v2_boundary.py tests/test_translate_v2_doc_converter.py tests/test_translate_v2_documents.py tests/test_translate_v2_job_access.py tests/test_translate_v2_jobs.py tests/test_translate_v2_language.py tests/test_translate_v2_ocr_tokens.py tests/test_translate_v2_service.py tests/test_translate_v2_worker.py tests/test_translate_v2_worker_process.py tests/test_translate_*.py
git diff --check ce9cd47ee1273018fec1c5cbab944a86d68d6ed5 HEAD
```

For R2-01, use the same environment prefix, then run this isolated Python probe:

```python
from pathlib import Path
from tempfile import TemporaryDirectory
from secai_gateway.translation.jobs import JobStore
from secai_gateway.translation.settings import TranslationSettings
from secai_gateway.translation.types import TranslationError

with TemporaryDirectory(prefix="secai-r2-repro-") as root:
    settings = TranslationSettings(db_path=f"{root}/jobs.db", artifact_dir=f"{root}/artifacts",
                                   job_deadline_seconds=1, artifact_cleanup_seconds=1,
                                   storage_quota_bytes=16, _env_file=None)
    store = JobStore(settings)
    first = store.admit("file", b"123456789012", ".txt", "en", "vi", "peer", None, now=100)
    path = Path(first["input_path"])
    assert store.get(first["job_id"], now=102)["status"] == "failed"
    assert store.get(first["job_id"], now=108)["status"] == "failed"
    assert path.exists()  # expected absent after cleanup window
    try:
        store.admit("file", b"12345", ".txt", "en", "vi", "peer", None, now=108)
    except TranslationError as exc:
        assert exc.code == "STORAGE_UNAVAILABLE"
```

For R2-02, `literal_spans()` returns empty for `中https://example.com/a中`, `中abcdef1234567890中`, `中OpenSSL 3.0.1中` and `نصhttps://example.com/aنص`. In the same private environment, a fake `ProcessingContext.complete()` that returns the source chunk with `https://example.com/a` replaced by `https://evil.test/b` made `translate_blocks((DocumentBlock(id="b000001", kind="paragraph", order=0, text="中https://example.com/a中", metadata={}),), "zh", "vi", context=...)` succeed and publish `中https://evil.test/b中`. The old round 1 marker probe now passes: three chunks, one whole marker in the middle chunk, none split.
