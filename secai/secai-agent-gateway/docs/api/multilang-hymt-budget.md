# v3 Hy-MT2 specialist token budget

The v3 specialist keeps the existing private three-field request
`{text, source_lang, target_lang}`. The Hy-MT2-7B profile is opt-in; the default
specialist profile remains MADLAD/T5, and the incumbent and legacy en↔vi
routes are unchanged. The sidecar selects and attests its own pinned GGUF and
binary. This v3 profile verifies the local tokenizer and prompt-count recipe;
it does not claim to attest the remote child process through its two-field
response.

For a staged 7B sidecar, configure these v3 settings together:

| Environment key | Required value |
| --- | --- |
| `MULTILANG_BACKEND` | `specialist` |
| `MULTILANG_SPECIALIST_PROFILE` | `hymt2_7b_gguf` |
| `MULTILANG_SPECIALIST_MODEL` | `tencent/Hy-MT2-7B` |
| `MULTILANG_SPECIALIST_TOKENIZER_JSON` | absolute path to pinned `tokenizer.json` |
| `MULTILANG_SPECIALIST_TOKENIZER_SHA256` | `4185389a69284fdd3ebf737c6120bbeee36cbed9763151fa5bac5986a39e05d4` |
| `MULTILANG_SPECIALIST_TOKENIZER_CONFIG_JSON` | absolute path to pinned `tokenizer_config.json` |
| `MULTILANG_SPECIALIST_TOKENIZER_CONFIG_SHA256` | `1fffdb809549222938f4a732645dd984c7c0b2973d592c2d01e05425274317da` |
| `MULTILANG_SPECIALIST_TOKENIZER_REVISION` | official base `9b0eb4e8f001def3e5ff6469a0ac96fdb39ec223` |
| `MULTILANG_SPECIALIST_CHAT_TEMPLATE_SHA256` | GGUF metadata template `788ac16c5d7bfefc28655928ad524c8f378a44cb24d24fb125d6a5859b167677` |
| `MULTILANG_SPECIALIST_CONTEXT_TOKENS` | `4096` |
| `MULTILANG_SPECIALIST_CHUNK_INPUT_TOKENS` | recommended `896`, at most `1024` |
| `MULTILANG_SPECIALIST_MAX_OUTPUT_TOKENS` | `512` |

Set the existing specialist private base URL, API key and enable flags as
usual. A missing or mismatched profile, model, token file, config file, digest,
revision, template digest, or budget makes `verified_specialist_counter()`
return no counter; the v3 route responds `MODEL_UNAVAILABLE` before provider
inference. Do not copy the 16 MB tokenizer into git or substitute a T5
tokenizer or an arbitrary context size. MADLAD's old 512-token encoder/256-token
decoder limits remain enforced when its profile is selected.

Hy-MT uses the official target names, including **Simplified Chinese** and
**Modern Standard Arabic**. Its pinned GGUF template for one user message
renders `<|startoftext|>{translation instruction and masked chunk}<|extra_0|>`.
The local tokenizer encodes that entire rendered string with
`add_special_tokens=False`; the BOS is already present. Serialized truncation
and padding are rejected. Each chunk must fit 1024 actual prompt tokens and
reserve all 512 output tokens inside the configured 4096-token autoregressive
context. The output text check reserves one terminal EOG token. Hy-MT never
uses MADLAD's `<2xx>` prefix or separate encoder/decoder accounting.

The pinned public `tokenizer.json` has no truncation or padding. For a public
`Xin chào`→Simplified Chinese probe, the actual sidecar's `/apply-template`
rendered the string above and `/tokenize` returned 30 token IDs, equal to the
local tokenizer's IDs (first `127958`, last `127962`). Those were two read-only
calls to the staged CPU model server; no `/completion` or model generation was
used. The focused test checks the same local 30-token count when the public
tokenizer is staged on the test host. This evidence covers token counting,
not translation quality or production readiness.
