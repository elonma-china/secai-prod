# Private Hy-MT CPU translation service

This sidecar implements the existing v2 `TranslationAgent` and v3 `SpecialistLlm`
`POST /v1/translate` contract. It is a separate CPU process; the gateway still
owns orchestration, masking, validation, and file/OCR job lifecycle. Importing
`service.py` does not load a model. The app factory loads one pinned GGUF only
when its FastAPI lifespan starts. No service is enabled by this commit.

The private request has exactly `text`, `source_lang`, and `target_lang`. Codes
are `vi`, `en`, `zh`, `ja`, `ko`, `fr`, `de`, `es`, `ru`, `ar`; `zh` means Simplified
Chinese and `ar` means Modern Standard Arabic. The direction must include `vi`
or be an identity request. An identity request returns the text unchanged.
HTTP 200 has exactly `translated_text` and `model`, where `model` matches the
configured gateway specialist model. Other responses have a generic error
code, never a partial translation.

## Staging configuration

Select one candidate **after** its quality review. Run exactly one worker on a
loopback HTTP listener (for example port 18864); the child llama-server uses a
different loopback port (default 18865). This is a deployment recipe, not an
instruction to start it during tests:

```sh
export MT_CPU_CANDIDATE=7b-q8
export MT_CPU_MODEL_PATH=/absolute/path/to/HY-MT2-7B-Q8_0.gguf
export MT_CPU_LLAMA_SERVER_PATH=/absolute/path/to/pinned/llama-server
export MT_CPU_TOKEN_FILE=/absolute/private/path/to/translation-token
export MT_CPU_LOCK_PATH=/absolute/private/path/to/translation-owner.lock
export MT_CPU_SERVER_PORT=18865
export MT_CPU_THREADS=16
python -m uvicorn deploy.mt_cpu_service.service:create_app --factory \
  --host 127.0.0.1 --port 18864 --workers 1 --no-access-log
```

The token file must be readable only by its owner (`0600`) and contain a
32–256-character URL-safe secret (`A–Z`, `a–z`, digits, `_`, `-`), optionally
followed by one newline. Provision the **same value** as the v2/v3 specialist
API key in the corresponding gateway configuration without putting it in an
argv, URL, source file, or logs. Set the gateway specialist base URL to the
sidecar's loopback address and its expected model to
`tencent/Hy-MT2-7B` or `tencent/Hy-MT2-1.8B` according to the selected
candidate. The service also rejects non-loopback clients and HTTP redirects;
its own HTTPX client ignores proxy environment variables. The owned child
receives the token **file path** through `--api-key-file`, so another local
process cannot bypass the sidecar's generation slot by calling the child port.
The token value never appears in the child argv.

At startup, the sidecar takes an exclusive local lock, hashes the complete
model and binary, then checks GGUF BOS, raw EOS, and chat-template SHA before
spawning only its own `llama-server`. The pinned binary is llama.cpp commit
`11fe02151f79c41d0d4af7da708755d73b9c0da6` with SHA-256
`e8253f2a2ad34cc36b4a2b874a6bf19c45ba867b89223e8a627f1ca0083db7b7`.
The candidate allowlist pins both model hashes and revisions in `service.py`.
Keep the lock file on a local filesystem shared by all attempted instances.
The pinned child inherits the lock descriptor, and a small Linux exec helper
sets a race-checked parent-death signal before launching it. If the sidecar
parent dies abruptly, the old child keeps the lock until it exits; another
sidecar cannot spawn a competing model. Startup accepts `/health` only after
the owned PID is proved to hold the expected loopback listening socket.

For 7B, the original GGUF has `eos_token_id=3`, but token 3 represents a
literal dollar sign in the pinned model. The official base configuration uses
EOS `127960` and EOT `127967`. The pinned child command applies both metadata
overrides; this was checked by forced-token probes in
`/home/ict/secai-multilang-benchmark-evidence/cpu-hymt2-7b-q8/smoke18-eog-corrected/eog-probe.json`
(SHA-256 `d5dc06fed5c803663d9d9eedfa8944324b2ecc1894266fee509ad03b89841eb0`).
The service accepts only `stop_type=eos`, `truncated=false`, nonempty text,
the exact pinned child model alias, and a final generated token in
`{127960,127967}`. For 1.8B the pinned raw and accepted EOS is `120020`.
Any changed model/binary/metadata requires a reviewed allowlist update.

The child is fixed to `--n-gpu-layers 0`, one generation slot, context 4096,
at most 1024 actual formatted prompt tokens, and at most 512 generated tokens.
The 105-second request deadline includes body receipt, queueing, tokenization,
and completion, including any needed child restart. At most one request may
wait behind the active generation. A queued request that disconnects or times
out immediately frees its waiting place without terminating the active
request's child.
The sidecar terminates and waits for **its own** child after a timeout,
disconnect, or uncertain upstream state. If it cannot prove the child exited,
it keeps the generation reservation poisoned and refuses further requests.
It never treats a client HTTP timeout as evidence that generation stopped.
After a successful reap, the next request may lazily restart the **same** pinned
model under the same exclusive process lock and generation slot. Restart
reattests the artifact, gets at most one attempt for that request, and must
finish within its remaining absolute deadline. Repeated startup failures return
503 and use exponential backoff capped at 30 seconds; a request during backoff
does not spawn another child. There is no automatic source-text retry or model
fallback. The lock is released only during sidecar shutdown after child death
has been proved.

Run the isolated mock suite without starting a model:

```sh
python -m pytest -q tests/test_mt_cpu_service.py
```

This commit provides a callable service and process-safety tests only. It does
not establish translation quality, production readiness, cross-service CPU
fairness, or a live v2/v3 smoke result. Those require the selected candidate's
separate benchmark and a staged gateway integration.
