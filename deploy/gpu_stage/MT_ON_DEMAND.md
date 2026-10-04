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
