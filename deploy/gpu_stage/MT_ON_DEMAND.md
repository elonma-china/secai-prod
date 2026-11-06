# On-demand GPU translation with the existing OCR service

`deploy.gpu_stage.mt_on_demand:create_mt_stage_app` is the **MT-only** private
ASGI factory. It has no OCR route and does not start a model at startup. Both
file translation and v3 text clients can call its exact three-field
`POST /v1/translate` contract. The existing OCR service remains responsible
for OCR and keeps its existing `POST /v1/ocr` contract.

Enable the factory only on the single Vast host that has the pinned Q4 model,
llama-server and OCR runtime. Set `GPU_MT_ON_DEMAND_ENABLED=1` and
`GPU_STAGE_MT_TOKEN_FILE` to a private token file (no group/other permissions),
then launch one Uvicorn worker on loopback. `GPU_STAGE_LOG_DIR` selects a
private writable readiness-log directory. The gateway's v2 and v3 specialist
base URLs should point to this loopback service through the controlled private
transport. Keep the previous long-lived diagnostic guard stopped; it uses the
same listener port and lock.

Each translated request waits within its **105-second absolute deadline** for
`/workspace/secai-gpu-specialists/admission.lock`. A live OCR child inherits
that same lock from its existing supervisor. Once MT claims it, one pinned
Hy-MT2-7B Q4_K_M child starts with the separately reviewed 4096-context
recipe, private llama-server API key, full 33/33 offload and bounded
1024-input/512-output token policy. MT releases its lock only after child death
and a fresh sample showing the full 8192 MiB preload reserve. Two requests
total may be active/queued in this process; additional requests receive BUSY.
Disconnect or timeout never implies that remote generation has stopped: the
internal stage retains its slot until cleanup finishes. Incomplete stage
evidence under the guard's `evidence/gpu-run-broker-*` namespace vetoes later
MT and diagnostic-guard starts across process restart. An operator must prove
the child/GPU cleanup before clearing such evidence.

The OCR supervisor shared-lock change is a separate additive scheduling patch;
both patches must be present and the lock paths must resolve to the same inode.
This branch has CPU fake-child tests only. It has **not** activated OCR or MT
on a GPU, and OCR quality qualification is outside this scheduling change.

Failure semantics at preload: `503 GPU_RESERVE` raised before any child exists
(free VRAM below the 8192 MiB reserve) is transient and retryable. It does
**not** latch the queue, leaves no evidence directory, and the next request
re-checks the GPU. `GPU_OWNER`, `GPU_SAMPLE`, `GPU_CLEANUP`, `GPU_BUDGET` and
a `GPU_RESERVE` observed while a stage is running or after it is reaped still
poison the queue until the process is restarted after cleanup proof. The
reserve is sampled once before the model-file attestation (a cheap gate, so a
sustained shortage does not rehash multi-GB files) and again after it. Every
stage failure also emits one warning line, `gpu stage failure code=<CODE>
poison=<bool>`, with the code and poison flag only, to stderr; confirm on
deploy that it reaches `mt.log`.

## Optional warm lease (`GPU_MT_WARM_IDLE_SECONDS`)

Default `0` keeps the behaviour above: one child per request. Set it to a small
value (suggested `20`, max 120) to keep the pinned child **and the shared GPU
flock** alive after a request, so consecutive calls (a file is split into many)
skip the spawn. The lease runs inside one `owner.run`, so sampling, the
8192 MiB preload reserve, attestation, owner proof, poison and evidence vetoes
are unchanged and happen once per child. Each job still obeys its own 105 s
deadline. The child is torn down through `owner.close()` on idle expiry, on any
job failure, timeout or disconnect (generation may still be running, so it is
never reused), on `GPU_MT_WARM_MAX_SECONDS` (default 600) and on app shutdown.
At most 2 requests are in flight; more get BUSY. Poisoned queue rejects new work.

Trade-off: while warm, the flock is held, so OCR on the same GPU waits up to the
idle window (plus teardown). OCR cannot signal this process; keep the window
short. Another MT stage queued behind the lease, `request_release()` or
shutdown closes the lease as soon as it is idle.

Attestation digests (binary, GGUF, libraries) are cached in-process keyed by
path, size, mtime, inode and ctime: the first spawn hashes fully, any stat
change re-hashes. A restart always re-hashes.
