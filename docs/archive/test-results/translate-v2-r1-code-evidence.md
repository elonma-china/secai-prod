# Translation v2 R1 code evidence

Evidence collected 2026-10-04 from `feature/import-file-translate`. This is author-side review evidence, not independent functional, security, quality, or release acceptance. All probes used private `/tmp/secai-import-suite` state and a local fake runner unless explicitly noted.

## R1-01 and R1-02 regressions

- `tests/test_translate_v2_jobs.py::test_offline_worker_queued_deadline_releases_quota_and_expires_artifact` admits at a frozen time with no claim, then asserts status failure, IP quota release, input cleanup and 410 tombstone. `test_stalled_running_job_deadline_fences_worker_and_releases_ip` checks the same deadline after claim and prevents stale publish.
- `tests/test_translate_v2_job_access.py::test_offline_worker_overdue_job_status_and_result` checks HTTP 200 failed status and 409 result while no worker is running.
- `tests/test_translate_v2_service.py::test_cjk_chunk_boundary_never_splits_protected_marker` checks that a CJK-adjacent inline technical literal has one intact placeholder in one chunk and that a fake provider which drops broken marker fragments can round-trip it. `tests/test_translate_v2_ocr_tokens.py` contains SOL-2's adjacent-script literal regressions, cherry-picked unchanged from `75f105f5c3f0f458f6bc9f8def19f2a1c068bb3c`.
- Focused related run: `137 passed in 10.34s` for `tests/test_translate_v2_*.py tests/test_translate_*.py`, using the isolated import venv and private test DB paths.
- Full hermetic run: `994 passed, 19 failed, 1 skipped, 0 errors` across 1,014 collected cases in 160.92s. JUnit: `/tmp/secai-import-suite/junit-r1-fix.xml`. Against the frozen base's 925 cases, all 925 IDs remain, 89 IDs were added, and the 19 failure IDs and one skip match exactly; no base node changed status and no new failure ID appeared. The suite exit code is **1** because the 19 inherited failures remain. This evidence does not certify those failures as harmless or claim whole-suite PASS.

## Legacy compatibility probe

`tests/legacy_translate_probe.py` runs the same deterministic fake runner against `/ai/translate` success, bad-target and empty-input JSON plus `/ai/translate/stream` SSE. Run it with `PYTHONPATH=src` from the base checkout and import checkout. Use `SESSION_STORE_BACKEND=memory`, `REDIS_URL=''`, private `POC_CATALOG_DB`, `GATEWAY_DB_PATH`, `SESSION_DB_PATH`; in the import checkout run once with `TRANSLATE_V2_ENABLED=false` and once with `true`, private `TRANSLATE_V2_DB_PATH` and `TRANSLATE_V2_ARTIFACT_DIR`. Each invocation printed JSON to its own file under `/tmp/secai-import-suite`.

All three snapshots are byte-identical, SHA-256 `1103705b22c0f1da9f6f2ba3e7047002beaba525f349894f8815a576af12688c`. Statuses: success 200, invalid target 400, empty input 400, SSE 200 with token and final events. This probe tests only the translation legacy path, not all legacy routes or live model behavior.

OpenAPI from `app.openapi()` was captured in separate base and import Python processes with `PYTHONHASHSEED=0`, the same import venv, `PYTHONPATH=src`, memory sessions and private database paths. Candidate v2 paths were excluded; all 69 legacy paths and 15 schemas compare **raw equal**, including `operationId`. Both JSON files have SHA-256 `5462691737c35c527313e9a8391cf833176642ccbaf3ae89cf1dc20a0aa75943`. Without fixed hash seed, the pre-existing showcase multi-method route emitted different duplicate operation IDs due to method-set ordering; no field was removed in the fixed-seed comparison. Raw captures: `/tmp/secai-import-suite/openapi-base-seed0.json`, `/tmp/secai-import-suite/openapi-candidate-seed0.json`.

## Runtime prerequisite probes

- `tests/test_translate_v2_doc_converter.py` and `tests/test_translate_v2_worker_process.py`: **3 passed in 4.85s** on this host. The DOC smoke ran the bounded networkless Docker converter against `tests/fixtures/translate_v2/basic.doc`; fixture SHA-256 `83a64dda77680368b4c1e0ecc637b4abf1b7f0196e932f730f09b14f10de026d`; local image ID `sha256:2f28a7994784f8af26930ae37d3827421af31757cdd7c2d5ff701e3bc6469d78`. The worker test starts a separate process with in-memory sessions and a private DB, processes an identity job and scans private storage for synthetic input. Neither test touches the production stack.
- Earlier author-only LLM canaries used an SSH loopback tunnel to the existing Vast `DuyTa/Cyber-F1-AWQ` endpoint and private in-memory worker session. Synthetic vi→zh, ar→vi and vi→ja examples returned script-appropriate text with their CVE literal intact; the private DB scan found no synthetic content. The tunnel was stopped. These canaries predate the latest source-wrapper change, have no independent reference score, and do **not** satisfy 18-direction quality or prompt-safety gates. No new live LLM call was made during R1 fixes while SOL-2 held the GPU slot.

The full 62-case ×3, bilingual proposition review, complete API/resource/security matrix, and OCR composition remain downstream independent gates. This file does not assign those criteria PASS.
