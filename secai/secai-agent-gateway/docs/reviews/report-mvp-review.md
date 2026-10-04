# Independent REPORT MVP review

**Verdict: FAIL (implementation hard gates); live content quality: PENDING.** Reviewed `9a2aeef..0a0dbdd` on `review/report-mvp` (frontend `864c1b8`, backend `0a0dbdd`). The baseline frontend snapshot `419f90c` and environment example `9a2aeef` predate this feature review. No model or external service was called.

| ID | Criterion | Evidence / repro | Severity | Minimal fix |
| --- | --- | --- | --- | --- |
| R1 | MVP-06 malformed imports must not return 500 | `src/secai_gateway/report_v2/import_file.py:28-39`: `ZipFile.read()` can raise `RuntimeError` for an encrypted DOCX; the handler omits it. An encrypted-bit DOCX sent to `/templates/import` with a valid key returned **500** in a TestClient probe. | **High** | Catch encrypted/unsupported ZIP read errors and return 422; add the malformed-file regression case. |
| R2 | MVP-03 revisions preserve critical facts | `src/secai_gateway/report_v2/api.py:155-163`, `validation.py:20-37`: edit validation checks required headings and that output literals occur in the original source, but never checks that critical entities from the prior revision remain. A draft replacing both sections containing `CVE-2026-1234` with “Chưa có thông tin” passed validation and was saved as revision 2 in a store probe. | **High** | Compare critical entities in the previous revision with the candidate edit, or explicitly reject their unexplained removal; test the edit path. |
| R3 | MVP-03 revision prompt provenance | `src/secai_gateway/report_v2/store.py:32-35,121-142`: revisions retain only content and time. The edit instruction from `api.py:148-163` is never saved, so revision history cannot show what prompt produced a revision. | Medium | Persist the edit instruction with each revision and return it in revision history. |
| R4 | MVP-02 invalid output retry | `src/secai_gateway/report_v2/api.py:135,160-163`: generation and edit call the model once; a structurally invalid or unverified draft immediately returns 422. The required bounded retry path is absent. | Medium | Retry validation failure a small fixed number of times, then return a clear error without persisting the invalid draft. |
| R5 | MVP-04 reject bad key before parsing request data | `src/secai_gateway/report_v2/api.py:44-50,106-110`: `_auth` is a route dependency, which FastAPI runs after body parsing. An unauthenticated malformed JSON `/generate` request returned 422 instead of 401; multipart upload data is likewise parsed before the key check. Valid requests to all eight new routes returned 401 without a key. | Medium | Check the report key before route body parsing, at middleware/ASGI entry for this prefix; preserve the disabled-route behavior. |
| R6 | MVP-08 positive edit test | `tests/test_report_v2.py:25-26,47-67`: the fake model always returns the same text, so the happy-path edit test proves a revision increments but does not prove the instruction changed report content. | Medium | Make the edit fake produce a distinct valid response and assert the new content, original content, and prompt provenance. |

## Verified behavior and limits

- New report tests: `PYTHONPATH=src .../.venv-import/bin/python -m pytest -q -p no:cacheprovider tests/test_report_v2.py` with PoC/gateway/report paths set to a temporary directory: **7 passed**. The original gateway `.venv` could not collect these tests because `python-multipart` is absent there; it is declared in `pyproject.toml` and available in the separate preexisting `.venv-import` used for the passing run. No dependency was installed or changed.
- Focused legacy gateway checks: `tests/test_gateway.py -k 'health_status_pure or prefetch_prompt_appendix or consolidated_prompt_appendix'`: **5 passed, 16 deselected**. The legacy contract test `test_legacy_contract_paths_and_profiles` timed out after 18 seconds; full legacy regression remains unverified. No external calls were intentionally made.
- Frontend: `npm run build` from a temporary source copy linked to the preexisting frontend `node_modules`: **passed**. This emitted only the existing Vite large-chunk warning. The new FE contract matches route paths and payloads; the key stays in component memory and is sent in `X-API-Key`, not in URLs or browser storage. Markdown uses the existing DOMPurify/react-markdown renderer without raw HTML rendering.
- API/store inspection and probes confirmed feature default OFF, a separate report key, SQLite persistence, optimistic version/revision checks, and no invalid model text saved by the existing tests. DOCX/MD/TXT import creates an editable draft; the FE exposes save, generate, open, edit, and revision history. The probe used fake model output and temp DB paths only.
- **Content threshold 70%: PENDING.** There is no live inference corpus, model run, or independent quality scoring in this review. Passing mocked tests and a build do not establish content quality or factual accuracy.

The MVP should be re-reviewed after R1 and R2 are fixed and the explicit provenance/retry/auth criteria are addressed. Advanced publish, immutable audit, adapters, and tenant isolation remain deferred under `docs/criteria/report-mvp.md`.

## Fix recheck — 2026-10-04

**Verdict: PASS for implementation fix review; live content quality 70%: PENDING.** Exact candidate tested: `d2e8bdad8fd1e4ee6fb8c1279d30d598bf14f6c4`. This short independent pass examined only product fixes in `0a0dbdd..d2e8bda` (cherry-picked fix commits `e84a2d9`, `e823903`, `d2e8bda`). The original FAIL above remains historical evidence.

| ID | Status | Recheck evidence |
| --- | --- | --- |
| R1 | Resolved | Import catches encrypted/unsupported ZIP read errors. Reused the encrypted-bit DOCX HTTP probe: **422**, no 500. Regression test passes. |
| R2 | Resolved | Edit validation receives the prior content and requires its critical identifiers to remain. Reused the dropped-CVE HTTP probe: **422 after two attempts**, current revision remains 1. CVE/IP/URL/hash preservation tests pass. |
| R3 | Resolved | SQLite revisions now persist and return `instruction`, with migration for the previous table schema. A successful edit probe retained the original revision and returned its edit instruction in revision 2; migration test passes. |
| R4 | Resolved | Generation and edit retry validation failure once, then return 422 without persisting an invalid draft. Successful retry tests pass; the dropped-CVE probe independently confirmed the fixed two-attempt bound. |
| R5 | Resolved | `ReportRoute` checks the key before invoking FastAPI's body parser. Reused malformed JSON and multipart requests without a key: both **401**. Early-auth regression test passes. |
| R6 | Resolved | Happy-path test now asserts distinct edited content, preserved original content, and the revision instruction. The independent positive-edit probe confirmed all three. |

Independent targeted verification from `secai/secai-agent-gateway`:

```sh
probe_dir=$(mktemp -d /tmp/secai-report-recheck.XXXXXX)
POC_CATALOG_DB="$probe_dir/poc.db" EXPLOITDB_ROOT="$probe_dir" \
GATEWAY_DB_PATH="$probe_dir/gateway.db" REPORT_DB_PATH="$probe_dir/report.db" \
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src \
/home/ict/secai-translate-import-wt/.venv-import/bin/python -m pytest \
  -q -p no:cacheprovider tests/test_report_v2.py tests/test_projects_sessions.py
```

Result: **48 passed in 6.65s**. `git diff --check 0a0dbdd..d2e8bda` also passed. No introduced regression was found in the inspected fixes. No product code or shared dependencies were changed by the reviewer; probes used temporary paths and fake model output.

This verdict covers the implementation fixes and targeted tests, **not full regression or model quality**. The prior incomplete legacy checks and root's documented six known baseline failures remain outside this recheck; slow legacy tests were not rerun. Root reports browser smoke of form/save/generate/edit/history and Markdown draft import against the real API/SQLite with a visibly labeled fake LLM; that is separate evidence, not an independent browser run by this reviewer. No model calls were made. Content scoring remains unmeasured and **PENDING**, without changing this implementation PASS.
