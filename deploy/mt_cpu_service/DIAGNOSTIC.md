# Isolated CPU file and v3 DEV diagnostic

This recipe is for the reviewed 7B Hy-MT CPU sidecar on the diagnostic branch.
It does not enable either public API by default. Wait until the separate raw
candidate benchmark has finished and its owner has released the one-model CPU
slot. Use a private host with enough RAM for the pinned model. No OCR service,
GPU service, shared Docker stack, or production database is part of this run.

The sidecar and gateway must use the **same** model name
`tencent/Hy-MT2-7B`, local GGUF, llama-server binary, and private bearer token.
Check the pinned hashes in [README.md](README.md) before launch. The 7B
candidate uses the official EOS/EOT metadata overrides embedded in the sidecar;
do not launch a second standalone llama-server. The sidecar acquires its lock
before loading the model and keeps it until its owned child exits.

## Offline preparation (safe to run before the model slot is released)

Run from this repository root using the isolated environment. The frozen DEV
manifest is identified by SHA-256
`ad1e0fd7f8068da49bee2255e64a8019ec5a2a840b0347e9403153c612a0f64e`.
The command creates 18 DOCX files with 30 distinct aligned source groups in
each direction and an exact block-to-sample mapping. Keep the work directory
private: it will later contain capability job IDs and translated text.

```sh
umask 077
export DIAG_PYTHON=/tmp/secai-multilang-final-env/bin/python
export PYTHONPATH="$PWD/src:$PWD"
export DIAG_WORK=/absolute/private/path/to/cpu-file-dev
export DIAG_STATE=/absolute/private/path/to/cpu-file-state
mkdir -p "$DIAG_STATE"
export GATEWAY_DB_PATH="$DIAG_STATE/gateway.sqlite3"
export SESSION_DB_PATH="$DIAG_STATE/sessions.sqlite3"
export POC_CATALOG_DB="$DIAG_STATE/poc.sqlite3"
export EXPLOITDB_ROOT="$DIAG_STATE/exploitdb"
export POC_SOURCES_ROOT="$DIAG_STATE/poc-sources"
export POC_ARTIFACT_STORE="$DIAG_STATE/poc-artifacts"
export INPUT_CLASSIFIER_WEIGHTS_PATH="$DIAG_STATE/classifier.json"
export SESSION_STORE_BACKEND=memory
export REDIS_URL=
export HF_HUB_OFFLINE=1
export TRANSFORMERS_OFFLINE=1
"$DIAG_PYTHON" -m deploy.mt_cpu_service.diagnostic prepare \
  --dev-manifest /home/ict/secai-multilang-benchmark-corpus/dev/flores101-dev-sample60-manifest.jsonl \
  --work-dir "$DIAG_WORK"
```

## Staging process profile (only after the CPU slot is released)

Use three separate foreground processes under one private operator account.
Set all writable paths to a fresh private directory, bind both HTTP listeners
to loopback, and use one uvicorn worker each. Keep shell history and logs free
of the token. Read the token file into the environment of **only** the gateway
and translation worker; the sidecar reads the same file itself. Do not copy a
shared `.env` into this checkout.

```sh
umask 077
export PYTHONPATH="$PWD/src:$PWD"
export DIAG_STATE=/absolute/private/path/to/cpu-file-state
mkdir -p "$DIAG_STATE"
export MT_CPU_CANDIDATE=7b-q8
export MT_CPU_MODEL_PATH=/home/ict/secai-multilang-cpu-runtime/hymt2-7b-q8/HY-MT2-7B-Q8_0.gguf
export MT_CPU_LLAMA_SERVER_PATH=/home/ict/secai-multilang-cpu-runtime/llama.cpp/build/bin/llama-server
export MT_CPU_TOKEN_FILE=/absolute/private/path/to/translation-token
export MT_CPU_LOCK_PATH="$DIAG_STATE/model-owner.lock"
export MT_CPU_SERVER_PORT=18865
export MT_CPU_THREADS=16

export GATEWAY_DB_PATH="$DIAG_STATE/gateway.sqlite3"
export SESSION_DB_PATH="$DIAG_STATE/sessions.sqlite3"
export POC_CATALOG_DB="$DIAG_STATE/poc.sqlite3"
export EXPLOITDB_ROOT="$DIAG_STATE/exploitdb"
export POC_SOURCES_ROOT="$DIAG_STATE/poc-sources"
export POC_ARTIFACT_STORE="$DIAG_STATE/poc-artifacts"
export INPUT_CLASSIFIER_WEIGHTS_PATH="$DIAG_STATE/classifier.json"
export SESSION_STORE_BACKEND=memory
export REDIS_URL=
export HF_HUB_OFFLINE=1
export TRANSFORMERS_OFFLINE=1
export TRANSLATE_V2_DB_PATH="$DIAG_STATE/jobs.sqlite3"
export TRANSLATE_V2_ARTIFACT_DIR="$DIAG_STATE/artifacts"
export TRANSLATE_V2_WORKER_DB_PATH="$DIAG_STATE/worker.sqlite3"
export TRANSLATE_V2_WORKER_POC_DB_PATH="$DIAG_STATE/worker-poc.sqlite3"
export TRANSLATE_V2_OCR_ENABLED=false
export TRANSLATE_V2_OCR_CORRECTION_SUPPORTED=false
export TRANSLATE_V2_MAX_RUNNING_JOBS=2
export TRANSLATE_V2_MAX_LLM_CALLS=1
export TRANSLATE_V2_SPECIALIST_CONTEXT_TOKENS=4096
export TRANSLATE_V2_SPECIALIST_TIMEOUT_SECONDS=120
export TRANSLATE_V2_LLM_TIMEOUT_SECONDS=120
export TRANSLATE_V2_JOB_DEADLINE_SECONDS=7200
export TRANSLATE_V2_SPECIALIST_BASE_URL=http://127.0.0.1:18864
export TRANSLATE_V2_SPECIALIST_MODEL=tencent/Hy-MT2-7B
export TRANSLATE_V2_ENABLED=false
export MULTILANG_ENABLED=false
```

`7200` seconds is a finite **DEV diagnostic** job deadline from admission,
including queue wait; it is not a production latency SLO. The private sidecar
still has a 105-second absolute deadline per block and the v2 client a
120-second timeout. A block that cannot finish in that bound fails the job; do
not silently extend the sidecar's deadline or substitute partial output.

After verifying the selected model is no longer in use elsewhere, start the
sidecar in the first terminal:

```sh
"$DIAG_PYTHON" -m uvicorn deploy.mt_cpu_service.service:create_app --factory \
  --host 127.0.0.1 --port 18864 --workers 1 --no-access-log
```

In each gateway/worker terminal, inherit the profile above and provision the
same token **without putting it in argv or logs**:

```sh
IFS= read -r TRANSLATE_V2_SPECIALIST_API_KEY < "$MT_CPU_TOKEN_FILE"
export TRANSLATE_V2_SPECIALIST_API_KEY
export TRANSLATE_V2_ENABLED=true
```

Start the gateway and worker separately:

```sh
"$DIAG_PYTHON" -m uvicorn secai_gateway.multilang_app:app \
  --host 127.0.0.1 --port 18181 --workers 1 --no-access-log
"$DIAG_PYTHON" deploy/translate-v2/worker.py
```

Only the translation worker consumes queued jobs. `max_running_jobs=2` allows
two durable jobs, while `max_llm_calls=1` serializes their specialist calls.
Do not start another worker or sidecar instance for this profile. Verify v2
capability and the sidecar's health before admitting the batch; a successful
health response alone is not a translation-quality result.

## Actual file API run and mapping

The live action has an explicit `--allow-live` gate. It uses loopback HTTP,
disables proxy environment variables and redirects, admits no more than two
jobs at once, and records idempotency keys in a private manifest. Keep the
gateway and worker running until all jobs are terminal. The batch timeout is
7500 seconds per file, just above the server's 7200-second job deadline.

```sh
"$DIAG_PYTHON" -m deploy.mt_cpu_service.diagnostic run \
  --work-dir "$DIAG_WORK" --base-url http://127.0.0.1:18181 --allow-live
```

If interrupted, use `resume` with the same work directory and base URL. Use
`cancel` only when deliberately abandoning the remaining jobs. Once all 18
jobs have succeeded, export exactly 540 **actual API result blocks**:

```sh
"$DIAG_PYTHON" -m deploy.mt_cpu_service.diagnostic map-results --work-dir "$DIAG_WORK"
```

The mapper checks the frozen DEV hash, all 18 job IDs/statuses, 30 ordered
paragraphs per result, source-text hashes, and nonempty translated blocks.
`candidate-blocks.jsonl` is diagnostic model output, not a gold reference or
release-quality verdict. Preserve failure rows and latency evidence; do not
replace them with raw-text benchmark outputs. Follow the separate evaluation
and blinded-review procedure before any quality claim.

## V3 sequencing

The reviewed Hy-MT v3 budget profile is present but remains OFF. When the file
batch has ended, set **all** of the following in the isolated gateway process
and restart it. See [the full budget contract](../../docs/api/multilang-hymt-budget.md)
for the exact prompt accounting. A partial selection such as context 4096
without `hymt2_7b_gguf` is rejected during settings validation.

```sh
export MULTILANG_BACKEND=specialist
export MULTILANG_SPECIALIST_PROFILE=hymt2_7b_gguf
export MULTILANG_SPECIALIST_BASE_URL=http://127.0.0.1:18864
export MULTILANG_SPECIALIST_MODEL=tencent/Hy-MT2-7B
export MULTILANG_SPECIALIST_TOKENIZER_JSON=/home/ict/secai-multilang-cpu-runtime/hymt2-7b-tokenizer/tokenizer.json
export MULTILANG_SPECIALIST_TOKENIZER_SHA256=4185389a69284fdd3ebf737c6120bbeee36cbed9763151fa5bac5986a39e05d4
export MULTILANG_SPECIALIST_TOKENIZER_CONFIG_JSON=/home/ict/secai-multilang-cpu-runtime/hymt2-7b-tokenizer/tokenizer_config.json
export MULTILANG_SPECIALIST_TOKENIZER_CONFIG_SHA256=1fffdb809549222938f4a732645dd984c7c0b2973d592c2d01e05425274317da
export MULTILANG_SPECIALIST_TOKENIZER_REVISION=9b0eb4e8f001def3e5ff6469a0ac96fdb39ec223
export MULTILANG_SPECIALIST_CHAT_TEMPLATE_SHA256=788ac16c5d7bfefc28655928ad524c8f378a44cb24d24fb125d6a5859b167677
export MULTILANG_SPECIALIST_CONTEXT_TOKENS=4096
export MULTILANG_SPECIALIST_CHUNK_INPUT_TOKENS=896
export MULTILANG_SPECIALIST_MAX_OUTPUT_TOKENS=512
export MULTILANG_MAX_CONCURRENT_MODEL_CALLS=1
export MULTILANG_ENABLED=true
export MULTILANG_ENABLE_JA=true
export MULTILANG_ENABLE_ZH=true
export MULTILANG_ENABLE_AR=true
```

Inject `MULTILANG_SPECIALIST_API_KEY` from the **same** private token file into
the gateway process without echoing it or putting it in argv. Four public-DEV
probes plus one synthetic CVE/IP literal probe run sequentially through the
exact v3 API and save responses in the private work directory. They test the
real masked/prompted route, not file translation quality. No other v3
language flags are needed for these probes.

```sh
"$DIAG_PYTHON" -m deploy.mt_cpu_service.diagnostic v3-smoke \
  --dev-manifest /home/ict/secai-multilang-benchmark-corpus/dev/flores101-dev-sample60-manifest.jsonl \
  --work-dir "$DIAG_WORK" --base-url http://127.0.0.1:18181 --allow-live
```
