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
