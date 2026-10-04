# Shared GPU stage coordination for OCR and translation

Status: **design proposal only**. No GPU service is enabled or loaded by this
document. The CPU Hy-MT service and its diagnostic harness remain preserved,
but the stopped CPU quality run is not evidence for GPU readiness. This design
does not reopen, rename, or reset the OCR runtime's exhausted five-review gate.

## Intended outcome and fixed constraints

Anonymous v2 image/file jobs and opt-in v3 text translation should be able to
use GPU OCR and GPU Hy-MT on the same A100 without loading both new models at
once or changing the three incumbent GPU processes. File jobs retain their
durable queue, cancellation, result format, and per-block translation mapping.
The public v2/v3 APIs and private `/v1/ocr` and `/v1/translate` JSON contracts
stay unchanged. All new routes and model loading remain explicitly OFF by
default. A request timeout, disconnect, or HTTP 503 never proves a CUDA process
has ended or that its memory is available to the next model.

The reported snapshot had **8,772 MiB free** with three incumbent PIDs. The
proposed new-child budget is **6,144 MiB owned** with **2,048 MiB free reserve**;
the pre-load condition is therefore at least **8,192 MiB free** on the same
GPU UUID. This is a measurement rule, not a claim that the pending Hy-MT2-7B
Q4_K_M CUDA artifact fits. Its binary/model hashes, actual ownership PID,
peak allocation, prompt budget, EOS behavior, and unload latency still need
attestation. A shared GPU with unmanaged incumbent inference can change its
free memory between samples; no application lock can guarantee a hard VRAM
limit against those external processes. A dedicated GPU/partition or an
incumbent admission signal would be needed for that stronger guarantee.

## Existing code seams and why current locks are insufficient

| Path | Current behavior | GPU coordination implication |
| --- | --- | --- |
| `translation/jobs.py` `JobStore.submit/claim` | SQLite stores queued/running jobs, leases, cancellation, and an admission-to-terminal deadline. `max_running_jobs` counts jobs, not GPU calls. | Retain as the outer durable file/image queue. No schema change is needed for stage arbitration. |
| `translation/worker.py` `Worker.run_once` | Image jobs preprocess, call `ImageOcrPipeline.process`, then `translate_blocks`; file jobs skip OCR. `ocr_gate` wraps the whole image pipeline and `llm_gate` wraps each translation call. Both are process-local semaphores. | They may remain local load-shedding controls, but cannot protect v3 or another worker process. Do not hold a global GPU lease across the whole image pipeline. |
| `translation/ocr_client.py` `recognize_image` | Sends the private `/v1/ocr` request, with a configurable whole-request timeout and one retry on some HTTP/transport failures. `ImageOcrPipeline.process` may call `correct_ocr` afterward. | GPU OCR ownership begins and ends within recognition only. Revisit retries: replay only a broker-confirmed pre-admission busy response; a transport timeout may have left an active child. |
| `translation/ocr_correction.py` and `WorkerContext.complete` | Optional correction calls the incumbent Linch runner under `llm_gate`; then the image's blocks are translated through `TranslationAgent`. Correction is OFF by default. | Initial GPU profile keeps correction OFF. If enabled later, OCR must release and prove cleanup **before** correction; never reacquire the same GPU stage lock from inside a held OCR lease. Incumbent GPU activity also needs its own resource analysis. |
| `translation/specialist.py` `TranslationAgent` | Structured private `/v1/translate` call; v2 worker currently has no bounded retry loop for a pre-admission busy result. | Add a future job-deadline-bounded retry only for an explicit *not admitted* busy result. Do not replay an uncertain started generation. |
| `api/multilang/routes.py` and `multilang/service.py` | v3 has a 120-second whole-request bound, a process-local model semaphore, chunk calls, and direct `SpecialistLlm` transport. | Keep the local semaphore for load shedding. The GPU broker is authoritative across processes; v3 should fail fast with a retryable busy response if a safe stage cannot fit its remaining deadline. |
| `deploy/mt_cpu_service` | Reviewed CPU sidecar owns a pinned llama-server with an inherited `flock` descriptor, parent-death coupling, one generation slot, verified EOS, and reap-before-restart. It loads at lifespan startup and explicitly disables CUDA. | Reuse its *contract and proven ownership patterns*, not its CPU Q8 allowlist or persistent CUDA-disabled process. An opt-in GPU profile needs separate pinned Q4 evidence and per-stage unload proof. |
| `evals/translation_ocr/real_quality/qwen_ocr_supervisor.py` in the OCR review tree | Disposable CUDA child/request; `threading.Lock` is local to that supervisor. Its 110-second HTTP response can precede final child/GPU cleanup. It does not pass a shared lock FD into the child. | It is a read-only reference and remains **BLOCKED after five reviews**. Merely pointing v2 at it, adding a gateway semaphore, or wrapping its HTTP response with `flock` would not close abrupt-parent-death and cleanup races. |

The old OCR child loads `Qwen/Qwen3-VL-2B-Instruct` into CUDA inside one
process; the supervisor's in-process lock is not a cross-service reservation.
The final OCR review's **OCR-R7 / FS-06** found a specific cleanup-gap branch:
after one verified owner sample, a later sample with `host_pid=None`,
`owned=0`, and only **1,000 MiB free** was accepted while the child remained
alive, then a later recovery could lead to HTTP 200. That violates the
continuous 2,048 MiB floor; later recovery cannot erase the observed breach.
The exact OCR candidate also lacks final live FS-06/08 evidence, and FS-07
remains blocked. The proposed broker must not import or activate that blocked
evaluation runtime as a shipping OCR backend. Any OCR code correction or reuse
requires an **explicit bounded reopening of the same OCR task and review
history**, including OCR-R7, not a new service name that evades the limit.

## Options

1. **Recommended: one opt-in GPU owner broker, one disposable child per
   stage.** One process owns a bounded stage queue and all new-model child
   PIDs. It exposes the existing `/v1/ocr` and `/v1/translate` envelopes on
   loopback with separate private credentials. Each stage loads one pinned
   model, generates once, reaps that child, and proves GPU cleanup before the
   next stage. The gateway still owns file jobs and text policy. This is
   slower than caching a model, but it is the smallest ownership rule that
   lets OCR take the next slot without a resident MT model retaining VRAM.
   The OCR leg remains disabled until the same OCR task is explicitly reopened
   and its blocked criteria pass.
2. **Two new services plus a lease daemon.** OCR and MT each talk to a third
   process for a shared lease. Every child must inherit the lease FD, and the
   daemon must survive or fail closed across crashes. This adds more protocols
   and ownership races while still requiring a replacement OCR runtime.
3. **Operational phase split for a diagnostic only.** Stop and prove unload of
   OCR before starting MT, and vice versa. This is useful for measured serial
   smoke runs, but cannot support mixed live image/file/v3 requests and is not
   the requested service behavior.

## Recommended broker behavior

The broker is the **only** endpoint configured for the two new GPU models.
Both v2 clients and v3's `SpecialistLlm` call its private routes; the legacy
DuyTa provider and its three incumbent processes stay on their current path.
The gateway's stage-admission adapter submits OCR recognition and MT chunk
calls to this **shared cross-process queue** with the remaining job/request
deadline. A second gateway-local queue would not make GPU ownership safer;
the broker is the authority, and local semaphores only limit caller load.
The broker takes one host-local `flock` at startup. Each spawned OCR or MT
child inherits the lock FD and uses a race-checked Linux parent-death signal
before model code runs. A replacement broker cannot load a second model while
an orphaned old child still holds the lock. A model child must be tied to the
exact pinned artifact and executable; process names, HTTP `/health`, and model
ID strings alone are insufficient ownership proof.

The stage state is `FREE_VERIFIED → LOADING → GENERATING → DRAINING →
FREE_VERIFIED`; any unproved child death, extra GPU PID, lost GPU sample, or
failed memory recovery enters `POISONED` and denies new GPU work. The broker
records the GPU UUID and incumbent PID baseline, checks no unexplained GPU
process has appeared, proves the new allocation belongs to its child, samples
owned MiB and free MiB during loading/generation, and enforces:

```text
owned_mib <= 6144
free_mib >= 2048 + max(0, 6144 - owned_mib)
```

While a child is alive, **every** GPU sample runs the owned/free check above;
`host_pid=None` and `owned=0` after a prior verified sample is not an
exception. A sample at 1,000 MiB free is an immediate breach and that request
can never return 200, even if free memory later recovers. Only after confirmed
child exit may a no-PID/zero-owned sample be treated as cleanup pending; the
broker still retains the lock, enforces the absolute 2,048 MiB floor, and
withholds success until the full pre-load reserve returns. This directly
covers OCR-R7 rather than carrying its cleanup-gap branch into a new wrapper.

Before another model starts, the previous child must have exited and the
observer must see zero owned allocation, no unexplained GPU PID, and at least
8,192 MiB free again. If an HTTP deadline expires first, the broker retains
the stage reservation while it kills/reaps only its own child and waits for
that GPU proof. It never kills an incumbent PID. If cleanup cannot be proved,
the broker stays poisoned until an operator resolves the condition; no
automatic alternate-model spawn or hidden source retry occurs.
The broker buffers a valid OCR or MT result and sends HTTP 200 only after that
stage's child has exited and its GPU cleanup proof has passed. A valid EOS or
decoded OCR text by itself is not enough to release the next stage.

The first functional version has **no warm model residency**: Hy-MT and OCR
each get one disposable child per generation/request. An OCR arrival can wait
for the active MT generation and cleanup, but cannot be held behind an idle
resident MT model. A finite FIFO stage queue and absolute deadlines include
waiting, model load, generation, and cleanup; cancelled **waiting** requests
leave the queue without killing an unrelated active child. If a stage cannot
fit its caller's remaining deadline, reject it before loading a model. File
jobs can retry only a proven pre-admission busy result under their durable
deadline. A future warm-MT optimization would be a separate reviewed change
with an immediate drain-on-OCR rule and measured latency benefit.

`JobStore` remains the outer queue: CPU DOCX/image validation and image
preprocessing do not reserve GPU. Only OCR recognition is an OCR stage. The
OCR lease is released with cleanup proof before optional correction and before
`translate_blocks` starts MT stages. Each text chunk is its own MT stage; the
broker does not keep the MT child alive between chunks in the first version.
Holding a global lease around
`ImageOcrPipeline.process` would deadlock or block translation/correction and
is explicitly excluded. `ocr_gate`, `llm_gate`, and v3's semaphore are only
local backpressure, never cross-process safety proofs.

An explicit pre-admission `BUSY` means **no generation started** and may be
retried within the durable job deadline. An HTTP timeout, disconnect, 5xx, or
unknown response means admission may have happened: the gateway fails that
stage unless it can query the broker by a private idempotency key and prove a
terminal result. The broker continues cleanup after client cancellation.
The existing OCR client's broad retry-on-transport path must be narrowed for
this backend. v3 has a 120-second whole-request ceiling, so it should reject
quickly while an OCR stage cannot fit, rather than wait past its deadline.
The v2 OCR and MT client timeouts need measured queue/load bounds and must
remain below each job's finite admission-to-terminal deadline; the current
120-second defaults are not enough to promise an OCR handoff after a full
105-second MT generation plus model switching and OCR inference.

## Smallest implementation sequence after explicit approval

1. Add an isolated, default-OFF `deploy/gpu_stage/` owner with one flock,
   bounded queue, pinned child launcher, GPU observer, and the existing
   private `/v1/translate` contract. Start with **MT only** after the Q4 CUDA
   artifact and 6,144/2,048 MiB measurements are accepted; OCR stays OFF.
   Keep the reviewed CPU sidecar untouched as a separate fallback artifact,
   not an automatic model fallback in a request.
2. Ask the user to reopen the **same** OCR task for a bounded OCR-R7 fix,
   FS-06/08 live evidence, and the shared-child ownership integration. This
   does not erase five failed reviews or FS-07's blocked quality status.
   Only after its own review passes should the broker expose `/v1/ocr` backed
   by a pinned OCR child that inherits the same lock and death coupling.
3. Wire the v2 `ocr_client.py` and `specialist.py` plus v3
   `multilang/provider.py` to that broker under explicit settings. Give
   `WorkerContext.translate` and OCR recognition a finite retry for *known
   pre-admission busy only*; retain JobStore's durable job and cancellation
   semantics. The v3 route uses its existing 120-second bound and returns
   promptly if the stage cannot fit. Do not change the public request/result
   schemas or edit incumbent serving processes.

This sequence can make GPU MT functional first, while accurately reporting
mixed OCR+MT service as blocked until the OCR gate is reopened and passed.

## Opt-in activation and proof required before implementation release

Keep `TRANSLATE_V2_ENABLED`, `TRANSLATE_V2_OCR_ENABLED`, and
`MULTILANG_ENABLED` OFF by default. A separate GPU broker profile pins the
official Hy-MT2-7B Q4_K_M artifact and CUDA llama-server binary, the OCR
model/revision, GPU UUID, token files, context/output budgets, and the 6,144/
2,048 MiB limits. The pending Q4 prep worker's artifact and live VRAM proof
must be reviewed before filling those pins. The CPU Q8 allowlist must not be
relabeled as a GPU artifact. The old OCR supervisor and CPU sidecar must not be
run as competing services on that GPU; their code and review history remain
preserved, unchanged.

CPU-only regression tests should use fake GPU snapshots and tiny owned child
processes to cover simultaneous OCR/MT/v3 admissions, queue cancellation,
MT-to-OCR handoff, no nested lease during correction, EOS/timeout cleanup,
broker SIGKILL with inherited lock, stale/foreign GPU PID, over-cap or missing
samples, and poisoned recovery. The specific OCR-R7 regression must inject a
verified owner sample followed by `host_pid=None`, `owned=0`, `free=1000`
while the child is **still alive**, and assert no HTTP 200 even after a later
high-free sample. A later explicitly authorized live smoke must
verify the actual pinned Q4 model and OCR child separately, then interleave an
image job, a file block, and v3 while recording GPU UUID/PIDs/free/owned MiB,
latency, child exit, and response envelopes. A green HTTP status alone is not
memory or quality evidence. The OCR task requires an explicit bounded reopen
and review of its exact failed criteria; this proposal does not reset its five
prior reviews or authorize a renamed OCR replacement.

Until the new OCR ownership path and Q4 CUDA evidence pass review, the honest
operational state is **GPU mixed-service release blocked**. A serial offline
diagnostic can still be planned after explicit model-slot authorization, but
it must not be described as the shared-queue implementation.
