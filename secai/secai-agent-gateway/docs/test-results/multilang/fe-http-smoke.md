# FE HTTP staging smoke (deterministic fake provider)

Status: **PASS for the listed real HTTP requests** on 2026-10-04. This verifies the local route, validation, service, chunk, response, and CORS path with injected fakes. It does **not** measure translation quality or readiness of the live model. Browser FE-to-backend bridge testing is separately owned by T3 and is not claimed here.

## Setup

- Core checkout: `/home/ict/secai-multilang-core-wt/secai/secai-agent-gateway`, immutable tested commit `bfe4e6b7a2eb3178434b3e08dbcdf58fe7410501`.
- FE checkout before this report: `a7832804f33efe6d32b2730e4f238e0719c9278c`.
- Disposable scripts: `/tmp/secai-multilang-http-smoke/server.py` and `/tmp/secai-multilang-http-smoke/smoke_client.py` (outside the repository). The server imports the actual core `multilang_app.create_app`, mounts the actual v3 API, and injects a deterministic `LlmPort` and legacy runner. It binds only `100.74.146.12:5180` for the private Tailscale FE browser bridge. Exact allowed browser origin: `http://ict-gpu.tail5d5146.ts.net:5178`.
- The process was launched with a clean `env -i`, isolated writable DB/catalog paths under `/tmp/secai-multilang-http-smoke/data`, `SESSION_STORE_BACKEND=memory`, `AIRGAP=true`, and no dotenv in the staging directory. `sys.addaudithook` rejects every `socket.connect` by the server process. The fake provider cannot call external model or network services. No production deploy, Docker, Vast, or real inference was used.
- v3 flags: master on; zh-Hans, ja, and ar on; other new languages off. The fake model emits fixed target-language fixture prose and returns any service-protected nonce markers unchanged. The injected token counter is `len`, so this smoke does **not** validate the pinned tokenizer's count or model context budget. Legacy vi↔en uses an injected fake runner, not v3.

## Requests and results

Command: `/home/ict/secai-multilang-core-wt/secai/secai-agent-gateway/.venv/bin/python /tmp/secai-multilang-http-smoke/smoke_client.py` from the staging directory. Exit code **0**.

| Check | Observed result |
| --- | --- |
| `GET /health` | 200, `degraded` as expected without real serving. |
| `GET /ai/v3/translate/languages` | 200; 9 languages, 16 pairs; only zh-Hans, ja, ar enabled. |
| CORS preflight | 200 with `Access-Control-Allow-Origin` for the exact FE origin; unrelated origin was not reflected. |
| `POST /ai/v3/translate` vi→zh-Hans | 200 JSON, one chunk/attempt; `CVE-2024-1234` and `192.0.2.1` each preserved once. Warnings: `SOURCE_LANGUAGE_UNCERTAIN`, `TARGET_LANGUAGE_UNCERTAIN`. |
| `POST /ai/v3/translate` ja→vi | 200 JSON, one chunk/attempt; both literals preserved once. Warning: `TARGET_LANGUAGE_UNCERTAIN`. |
| `POST /ai/v3/translate` vi→ar | 200 JSON, one chunk/attempt; both literals preserved once. Warning: `SOURCE_LANGUAGE_UNCERTAIN`. |
| v3 vi→fr with flag off | 403 `LANGUAGE_DISABLED`. |
| v3 request with extra `model` field | 400 `INVALID_REQUEST`. |
| Legacy `/ai/translate` and `/ai/translate/stream` vi→en and en→vi | JSON 200 and SSE 200 with token and final frames in both directions, using the fake legacy runner. |

For each successful v3 translation, the assertions also checked JSON content type, `Cache-Control: no-store`, `X-Content-Type-Options: nosniff`, exact-origin CORS header, response field set, canonical language pair, and `X-Request-ID` matching `metadata.request_id`.

Input fixtures: vi `Kiểm tra CVE-2024-1234 và 192.0.2.1.`; ja `このセキュリティ報告では CVE-2024-1234 と 192.0.2.1 を確認してください。`; legacy vi `Xin chào thế giới.` and en `Hello world.`. The fake responses are deliberately deterministic; language-detector warnings above are recorded rather than treated as model quality evidence.

The staging listener was confirmed on `100.74.146.12:5180`. It is temporary and will stop when its local process ends. No screenshot or live-model quality result is claimed.
