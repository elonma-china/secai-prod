# Private real-page OCR runtime

This isolated service uses `Qwen/Qwen3-VL-2B-Instruct` revision
`89644892e4d85e24eaac8bacfd4f463576704203` and the frozen
`conditional` prompt. It serves only Vast loopback port 18083 behind a bearer
token and an optional private SSH tunnel. No public API or translation backend
contract changes are made here.

For shared MT/OCR GPU admission, the supervisor acquires the same
`/workspace/secai-gpu-specialists/admission.lock` as the on-demand MT owner
before capturing its GPU baseline. The existing OCR child inherits that lock
descriptor; the supervisor releases its descriptor only after child death and
the existing GPU cleanup check. Set `OCR_SHARED_GPU_LOCK_PATH` only when both
services use the same mounted lock inode. A busy lock returns OCR 503 without
loading a model or changing the baseline. Under that lock, OCR also rejects
the guarded MT owner's poison marker or any `evidence/gpu-run-*` directory
without a valid `clean_exit_attested: true` summary; operator clearance is
required before either service resumes after unproved cleanup.

Required source files are `qwen_ocr_supervisor.py`, `qwen_ocr_child.py`,
`qwen_server_real_pages_bounded.py`, `start_real_pages_bounded.sh`, and the
tracked GPU helper `deploy/ocr/server.py`. Run the startup script in its
checkout location, or copy all five files into one private runtime directory.
The script checks the pinned model revision and weights SHA-256, 20 GiB disk
reserve, `nvidia-smi`, and exact installed versions before binding. Its Python
environment is the isolated owned `runtime/venv`; startup refuses version
mismatch rather than changing shared packages. Tested packages: torch
2.11.0+cu130, Transformers 5.13.1, FastAPI 0.136.3, Pydantic 2.13.4,
Pillow 12.3.0, Uvicorn 0.51.0, huggingface-hub 1.23.0, safetensors 0.8.0,
accelerate 1.14.0.

The CPU-only supervisor spawns exactly one disposable GPU OCR child per
request. It holds the single-flight admission lock until the child is dead
and GPU memory has returned to the preload reserve. Its GPU monitor starts
before child load and continues through cleanup. It checks a stable device
UUID and incumbent set, unique new GPU host PID corroborated by the child
**before any allocation is attributed to OCR**. A PID seen during the brief
CUDA-context/publication race remains unidentified and receives zero OCR
allocation credit, so the full 8192 MiB preload reserve applies; missing
proof after one second fails closed. Verified owned memory stays at or below
6144 MiB, and free memory at least
`2048 + max(0, 6144-owned)` MiB. Missing or ambiguous measurements fail
closed. On disconnect, breach or 100-second wall deadline it kills only the
subprocess it spawned. If cleanup or unrelated GPU use prevents reserve
restoration, HTTP returns an error by 110 seconds while admission remains
locked until a background reconciliation observes safe conditions. Cooperative
child stopping and a fourfold repeated-token guard provide additional
protection; partial output is never successful OCR.
An independent event-loop watchdog kills the child at the 100-second wall
deadline even if a GPU measurement is in progress. The HTTP waiter remains
bounded to 110 seconds; any later cleanup keeps admission closed.
An HTTP 200 also requires a freshly verified child host PID and a positive
measured owned-memory peak. A fast child whose GPU use escapes sampling fails
closed. The private trace emits a separate `disconnect_observed` event; a
generic release event alone is not cancellation evidence.
After the verified GPU PID disappears, driver free-memory reporting may lag
context teardown. This transition emits `gpu_cleanup_pending` and keeps the
single-flight lock. Every cleanup-pending sample must still have at least
2048 MiB free, including while the child remains alive; an observed breach
fails the request even if free memory later recovers. HTTP success waits for
both child exit and a new sample
meeting the full 8192 MiB reserve. A remaining or unexpected GPU PID is still
checked against the active resource limits and fails closed if attribution is
ambiguous.

Historical frozen 40-page evidence remains 18 attempted, 12 HTTP successes,
6 deadline failures, 102 not run. Diagnostic replays confirmed hallucinated
loops on `mdp-ar-03` and `mdp-zh-03`. A 2 MP image cap recovered `mdp-ja-02`
once but failed both Arabic and Chinese difficult pages. A manually rotated
and cropped `mdp-zh-03` ended in 13.18 seconds but had CER 43.36%; manually
rectified `mdp-ar-03` still timed out at 100.03 seconds. These manual
transforms were diagnostic only and are not production preprocessing. The
quality recovery option budget is exhausted; FS-07 remains blocked. A 200
response, repeated-token stop, or tiny-image smoke is not real-document
quality acceptance.

Focused checks: `test_bounded_runtime.py` and `test_ocr_supervisor.py` in the
Vast Python environment; `probe_disconnect.py <public-image>
<private-token-file> <private-trace-file>` against the owned loopback service.
The probe uses a fresh request ID, waits for one generated token, disconnects,
checks lock release, then requires a valid subsequent OCR response. It never
prints OCR text or secrets. Live traces record latency, sampled peak owned
memory and minimum free memory, not request content.
