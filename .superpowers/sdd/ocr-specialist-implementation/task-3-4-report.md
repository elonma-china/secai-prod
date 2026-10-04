# Task 3+4 CPU checkpoint

Status: scripted CPU adapter/composition implemented; real OCR backend integration pending Task 2. No live model, GPU, network service, production entrypoint switch, deployment, push, or merge was performed.

## Delivered

- Narrow `OcrBackend` Protocol and immutable image/task/result DTOs. The backend must implement scoped, durable submit/status/result/cancel; this checkpoint does not substitute a production queue.
- Isolated `DocumentRunner` with configured DuyTa provider check, fixed two-tool Linch registry, in-memory session/run stores, per-run `RunOptions(deps=...)`, three-turn and 120-second caps, one OCR submission per turn, honest pending/admission-failure answers, and bounded untrusted OCR previews.
- Additive OFF-by-default `create_document_app` and `/ai/v3/document-agent` contexts, turns, task status/result/cancel routes. Ingress bounds request/image sizes and pixels, rejects unknown/duplicate fields and spoofed image types, resolves context and task scopes off-loop, uses Task 1 durable turn reservation/replay, emits safe JSON/SSE events, and reaps scopes off-loop at lifespan startup and every 60 seconds.
- Separate `document_ocr` profile and prompt. Existing chat/translation registries are unchanged.

## Test evidence

- TDD red: tools import failed before `contracts.py`; runner import failed before `runner.py`; HTTP import failed before `document_app.py`; SSE token assertion failed before token emission; failed-admission assertion caught a false success answer before the deterministic failure response was added.
- Focused final command (from gateway root, with the same private paths and offline variables):

  `PYTHONPATH=src HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 GATEWAY_DB_PATH=/tmp/secai-doc-agent-tests-gw.db SESSION_DB_PATH=/tmp/secai-doc-agent-tests-sessions.db POC_CATALOG_DB=/tmp/secai-doc-agent-tests-poc.db POC_SOURCES_ROOT=/tmp/secai-doc-agent-tests-sources POC_ARTIFACT_STORE=/tmp/secai-doc-agent-tests-artifacts /tmp/secai-multilang-final-env/bin/python -m pytest -q tests/document_agent tests/test_projects_sessions.py`

  Result: **59 passed in 7.35s**.
- `PYTHONPATH=src /tmp/secai-multilang-final-env/bin/python -m compileall -q src/secai_gateway/document_agent src/secai_gateway/api/document_agent src/secai_gateway/document_app.py`: passed.
- `git diff --check`: passed before report/commit; rerun at commit.
- An exploratory Linch invocation without an explicit session store created `.linch/sessions.db`; that generated file was removed. `tests/document_agent/test_runner.py` passed **3 tests in 1.17s** afterward, and `test ! -e .linch/sessions.db` passed. The delivered runner explicitly uses `InMemorySessionStore` and `InMemoryRunStore`.

## Limits requiring integration review

- No concrete OCR queue/backend is present in this branch. The production feature remains OFF; enabling it requires an accepted Task 2 backend implementing the exact Protocol, shared admission/storage/rate limits, and live worker release behavior. Scripted tests prove the adapter and ASGI flow only.
- No live DuyTa call or OCR quality claim. The default configured model is not automatically treated as DuyTa; `DocumentRunner` checks the configured model identity when it builds the production provider.
- Full backend suite and v2/v3 file/translation regressions are deferred to the root integrated checkpoint, as requested. This task ran the focused document and legacy project/session tests only.
- The new ASGI composition does not itself configure a production access-log filter. Before staging, deployment must redact/disable header and body access logging for the new prefix. Default OFF means this route is not live yet.

## Independent review fix round 1

Four Important findings in `task-3-4-review.md` were addressed without live inference:

- A pinned SHA-256 tokenizer and chat template now render and count each complete Linch provider request, including tool schema and prior tool results, against the configured context window plus 1,024 output tokens. Missing/mismatched evidence fails closed. The production model identity is exactly `DuyTa/Cyber-F1-AWQ`. Linch filesystem tools were also disabled after a test exposed four extra advertised tools; both generations now advertise only OCR tools.
- `OcrBackend.submit(scope, attachment, peer, *, submission_key: str)` receives a stable 64-hex key derived from the context ID, HTTP idempotency key and exact-payload digest. A cancellation/retry ASGI test with a durable-key fake shows one admitted job and the same key across both submissions despite a new attachment ID. The accepted Task 2 backend must enforce this key transactionally with image/config identity; scripted tests do not prove cross-process exactly-once behavior.
- The 11 MiB multipart cap is checked before extending the request buffer, including one oversized ASGI chunk.
- Full OCR results are checked against a 10 MiB UTF-8 JSON response bound before constructing the HTTP response; the authorized result is complete within that supported bound. Oversized results return 413.

The optional `document_agent` dependency group declares Jinja2 and tokenizers for the enabled feature. Tests used the pinned offline tokenizer/template in `/home/ict/secai-multilang-benchmark-corpus/runtime-prep`; existing system Jinja2 3.1.2 and MarkupSafe 2.1.5 were copied to isolated `/tmp/secai-document-agent-test-deps`. No package was installed into the shared test environment and no network/model call occurred.

Final focused command, from gateway root:

`PYTHONPATH=/tmp/secai-document-agent-test-deps:src HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 GATEWAY_DB_PATH=/tmp/secai-doc-agent-tests-gw.db SESSION_DB_PATH=/tmp/secai-doc-agent-tests-sessions.db POC_CATALOG_DB=/tmp/secai-doc-agent-tests-poc.db POC_SOURCES_ROOT=/tmp/secai-doc-agent-tests-sources POC_ARTIFACT_STORE=/tmp/secai-doc-agent-tests-artifacts /tmp/secai-multilang-final-env/bin/python -m pytest -q tests/document_agent`

Final result: **30 passed in 4.58s**. `compileall -q` and `git diff --check` passed; `.linch/sessions.db` was absent. Full backend suite remains the integrated checkpoint's responsibility.
