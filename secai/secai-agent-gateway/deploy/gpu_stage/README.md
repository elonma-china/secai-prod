# Private GPU stage broker (CPU-verified MVP)

`deploy.gpu_stage.broker:create_gpu_stage_app` is an ASGI factory for one
loopback process. It is **off by default**. The broker owns the same advisory
`admission.lock` as the guarded Q4 diagnostic and must replace that guard,
never run beside it. OCR and translation requests enter one finite FIFO. Each
request starts one pinned child, and the next child cannot start until that
child is reaped and a fresh GPU sample shows at least 8192 MiB free. Parent
death leaves the flock inherited by the child until it dies. The broker
poisons itself if cleanup or ownership cannot be proved.

The MT child uses the pinned `Hy-MT2-7B Q4_K_M` llama-server recipe from
`gpu_specialist_server.py`, with the separately reviewed **4096** context
profile, full 33/33 layer offload, at most 1024 input tokens and 512 output
tokens. Its private route is `POST /v1/translate` with exactly
`text`, `source_lang`, `target_lang`, and an exact
`{translated_text, model}` success response. The OCR child uses the frozen
`Qwen/Qwen3-VL-2B-Instruct` conditional transcription code and returns exact
`{model, blocks}`. Both require separate private Bearer tokens and a loopback
client. No translation or OCR text enters logs.

Activation configuration (operator-owned private runtime only):

| Variable | Meaning |
| --- | --- |
| `GPU_STAGE_BROKER_ENABLED=1` | Explicit broker factory gate |
| `GPU_STAGE_MT_ENABLED=1` | Optional MT route; default off |
| `GPU_STAGE_OCR_ENABLED=1` | Optional OCR route; default off |
| `GPU_STAGE_MT_TOKEN_FILE` | Mode-0600 private MT token file, required if MT enabled |
| `GPU_STAGE_OCR_TOKEN_FILE` | Mode-0600 private OCR token file, required if OCR enabled |
| `GPU_STAGE_OCR_PYTHON` / `GPU_STAGE_OCR_PYTHON_SHA256` | Absolute isolated OCR Python and its exact SHA-256 |
| `GPU_STAGE_OCR_MODEL_PATH` | Pinned local OCR snapshot; offline only |
| `GPU_STAGE_OCR_WEIGHT_PATH` | Its `model.safetensors`, checked against the reviewed digest |
| `GPU_STAGE_OCR_PID_DIR` | Private writable directory for host PID proof files |
| `GPU_STAGE_LOG_DIR` | Private writable directory for MT readiness logs |

The fixed source digests for the OCR child, bounded generator and GPU helper
are checked before every stage. The model weight digest and official revision
are recorded in the reviewed OCR runtime source. Do not enable OCR until its
runtime dependencies, local snapshot, and unresolved live quality gate FS-07
are separately qualified. This broker has CPU fake-child tests only; it has
**not** run a live GPU stage. The prior guarded diagnostic and this broker
must be sequenced by the GPU owner. Never launch extra workers or replicas.
