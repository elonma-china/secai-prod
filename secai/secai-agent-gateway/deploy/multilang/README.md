# Isolated multilingual text API staging

This overlay is code and configuration guidance only. It does not change the existing
Docker stack or enable any language. Do not run a production cutover until the
assessment, independent security/integration review, and user release decision pass.

Use `secai_gateway.multilang_app:app` as the ASGI target in an isolated staging
process. With `MULTILANG_ENABLED=false` (the default), it returns the original
gateway app. With master ON, only exact `/ai/v3/translate` and its slash-prefixed
paths reach the isolated FastAPI surface; all other ASGI traffic and the original
lifespan go to the legacy app. Roll back by setting `MULTILANG_ENABLED=false` or
restoring `secai_gateway.app:app`, then restarting the staging process after active
requests have drained. No database migration is involved.

All settings are server-side environment variables. No request field or header may
select a model, adapter, URL, flag, or user identity. Do not copy production `.env`
or credentials into a checkout. The existing `LLM_PROVIDER=vllm`, `QWEN_BASE_URL`,
`QWEN_API_KEY`, and `QWEN_MODEL` configure the incumbent model endpoint. The wrapper
requires `QWEN_MODEL=DuyTa/Cyber-F1-AWQ` and rejects a returned model ID mismatch.
The ID alone does not attest the served checkpoint revision.

Before any flag is enabled, operators must independently verify the serving
checkpoint and matching tokenizer revision, chat template, context window, output
limit, quantization, and model licensing. Then inject these values from the same
inventory:

| Variable | Default | Purpose |
|---|---:|---|
| `MULTILANG_ENABLED` | `false` | Master route switch |
| `MULTILANG_ENABLE_{ZH,JA,KO,FR,DE,ES,RU,AR}` | `false` | Both directions of each language |
| `MULTILANG_TOKENIZER_JSON` | empty | Local pinned `tokenizer.json`; never downloaded by this app |
| `MULTILANG_TOKENIZER_SHA256` | empty | SHA-256 of that exact tokenizer file |
| `MULTILANG_CONTEXT_TOKENS` | `0` | Verified serving context limit |
| `MULTILANG_CHAT_TEMPLATE_OVERHEAD_TOKENS` | `0` | Verified upper bound for role/special tokens the server adds |
| `MULTILANG_MAX_TEXT_CHARS` | `24000` | Input code point cap |
| `MULTILANG_MAX_BODY_BYTES` | `262144` | Streaming JSON byte cap |
| `MULTILANG_MAX_SOURCE_TOKENS` | `8192` | Full original source cap |
| `MULTILANG_MAX_CHUNKS` | `16` | Per request chunk cap |
| `MULTILANG_CHUNK_INPUT_TOKENS` | `2048` | Per chunk source cap |
| `MULTILANG_MAX_OUTPUT_TOKENS` | `4096` | Reserved output tokens per call |
| `MULTILANG_MAX_RETRIES` | `1` | One output validation retry per chunk |
| `MULTILANG_REQUEST_TIMEOUT_SECONDS` | `120` | Whole request deadline |
| `MULTILANG_MAX_CONCURRENT_MODEL_CALLS` | `2` | Process call cap (up to 2) |
| `MULTILANG_MAX_ACTIVE_REQUESTS` | `8` | Process admission cap |
| `MULTILANG_RATE_LIMIT_PER_MINUTE` | `10` | Fixed window POST quota per peer IP |

If tokenizer/config evidence is missing or its hash differs, an enabled POST returns
`MODEL_UNAVAILABLE` before inference. The full prompt count includes system and
user content, reserved output, and the attested chat-template overhead. A process
quota is not a cluster-wide quota: staging with multiple workers requires ingress
limits or another shared quota before production release.

`GET /ai/v3/translate/languages` returns nine canonical languages, sixteen
directional pairs, effective flags, and limits. `POST /ai/v3/translate` accepts
only JSON `{ "text": "…", "source_lang": "vi", "target_lang": "zh-Hans" }`.
`zh` is a case-insensitive alias for `zh-Hans`; all other region variants, `en`,
`auto`, same-language, and foreign-to-foreign pairs are rejected. The new API is
anonymous and stateless. It neither reads nor writes session/ledger content.
Existing en↔vi, file, OCR, chat, and session paths remain on legacy handling.

These response examples illustrate the wire format; they are **not live model
evidence**:

```json
{"translated_text":"检查 CVE-2024-1234 和 192.0.2.1。","source_lang":"vi","target_lang":"zh-Hans","warnings":[],"metadata":{"request_id":"illustrative-1","chunks":1,"attempts":1}}
```

```json
{"translated_text":"Kiểm tra CVE-2024-1234 tại 192.0.2.1.","source_lang":"ja","target_lang":"vi","warnings":["SOURCE_LANGUAGE_UNCERTAIN"],"metadata":{"request_id":"illustrative-2","chunks":1,"attempts":1}}
```

```json
{"translated_text":"افحص CVE-2024-1234 و 192.0.2.1.","source_lang":"vi","target_lang":"ar","warnings":[],"metadata":{"request_id":"illustrative-3","chunks":1,"attempts":1}}
```

Failure codes and statuses follow `docs/api/multilang-contract.md` in the
integration branch. All v3 JSON responses use `Cache-Control: no-store`,
`X-Content-Type-Options: nosniff`, and a generated `X-Request-ID`; 429 also has
`Retry-After`. The source is data, including HTML-like text and URLs. Clients
must render returned text as a text node, not HTML. A short or mixed-script
translation can carry an uncertainty warning; it still needs human quality
adjudication. Strict literal order may reject a linguistically valid reorder.
