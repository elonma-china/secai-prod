# v3 private translation specialist adapter

The v3 translation backend defaults to `MULTILANG_BACKEND=incumbent` and keeps the existing DuyTa provider and validation path. An isolated deployment can select `MULTILANG_BACKEND=specialist` after the specialist runtime and tokenizer are verified. This switch affects only `/ai/v3/translate`; legacy chat, SSE, and translation routes keep their existing orchestration.

Required specialist settings (all use the `MULTILANG_` prefix):

| Setting | Meaning |
| --- | --- |
| `BACKEND=specialist` | Explicit opt-in. |
| `SPECIALIST_BASE_URL` | Loopback HTTP or a private IP over HTTPS, with a port and no path/query/credentials. |
| `SPECIALIST_API_KEY` | Bearer secret for the private service. |
| `SPECIALIST_MODEL` | Exact model identity returned by the service, including revision. Candidate: `google/madlad400-3b-mt@fa184c675da0b5c9e1c8694fccd4e12e2d422094`. |
| `SPECIALIST_TOKENIZER_JSON` / `SPECIALIST_TOKENIZER_SHA256` | Local tokenizer JSON and pinned SHA-256. Candidate tokenizer SHA-256: `a2799ccc696b752ba00c34f58726bfe253a04921ceb6cfc620400f560474790b`. File absence, checksum mismatch, truncation config, or parser failure closes the inference gate. |
| `SPECIALIST_CONTEXT_TOKENS` | Attested encoder ceiling, at most 512 for this candidate. |
| `SPECIALIST_CHUNK_INPUT_TOKENS` | Split budget (default 256); the full target prefix and EOS must still fit the encoder ceiling. |
| `SPECIALIST_MAX_OUTPUT_TOKENS` | Attested decoder ceiling (default 256); output is counted with the pinned tokenizer before acceptance. |
| `SPECIALIST_TIMEOUT_SECONDS` | Per-call timeout (default 60); request deadline still applies. |

The gateway sends a private `POST /v1/translate` with exactly `{ "text": masked_chunk, "source_lang": source, "target_lang": target }`. Language codes are canonical per request and `zh-Hans` becomes `zh` at this wire boundary. The adapter never infers the pair from prompt prose or mutable process state. A successful response has exactly `{ "translated_text": string, "model": exact_identity }`. Non-200, invalid or oversized JSON, or a model mismatch fails closed. Request bodies are capped at 128 KiB, responses at 256 KiB, and a chunk at 6000 characters. The gateway keeps literal protection, chunking, restoration, output checks, JSON envelope, and cancellation.

**Server completeness contract:** HTTP 200 is permitted only when generation ended with the model's terminal EOS token `</s>` and returned complete nonempty text. A max-token cutoff without EOS, cancellation, or partial generation must be non-200. The adapter maps a verified 200 to `stop`; the two-field response has no finish-reason field to inspect independently. The CPU candidate uses the source string `<2target> ` + masked chunk (for Simplified Chinese `<2zh> `), tokenized with `T5TokenizerFast.encode(add_special_tokens=True)`, including encoder EOS. The gateway's pinned tokenizer JSON counts that same complete string. MADLAD is encoder-decoder, so encoder limit 512 and decoder limit 256 are checked separately. Runtime must use the same tokenizer and bounds; deployment remains gated until that equivalence and EOS behavior are measured.

This adapter is a transport and safety integration. It provides no claim that the candidate meets the required semantic quality threshold of more than 70% for each direction, or that a CPU service is available in production. Activation and release remain subject to separate quality and runtime evidence.
