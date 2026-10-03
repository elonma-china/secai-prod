# Private OCR service on the existing Vast instance

The server binds to `127.0.0.1:18083` on the existing instance. The gateway
reaches it through an SSH local tunnel and sends a separate bearer token. Do
not publish the port. The model is the pinned
`PaddlePaddle/PaddleOCR-VL-1.6@c5630abae1d940eafe0697512a0325494b02ab42`
snapshot (Apache-2.0, approximately 1.0B total parameters). Its model file is
BF16 safetensors. Runtime uses the instance's pinned torch 2.11+cu130 and
transformers 5.13.1 from a separate virtual environment with system packages;
it does not modify their installation. `trust_remote_code=False` and offline
environment variables prevent code/weight fetch at inference time.

Before download, retain at least 20 GiB free under `/workspace`. Verify
snapshot SHA and each downloaded file checksum. Before loading, require 9216 MiB
free when OCR owns no GPU memory. After loading, require free memory at least
`max(2048, 9216 - owned_OCR_MiB)` before each inference. This charges the
already loaded OCR process once while reserving 2 GiB for incumbents. Query
the OCR host PID through `nvidia-smi`, including CUDA context and runtime memory.
Vast's container PID differs from the host GPU PID, so the service captures
the incumbent GPU PID set before CUDA initialization and requires exactly one
new process when its context is created; ambiguous changes fail closed;
reject when owned usage exceeds 7168 MiB or global free falls below 2048 MiB.
Start one uvicorn worker only. Keep the service token
outside git and outside process arguments; use an environment file readable
only by the service owner. Never print it in logs.

The wire API is `POST /v1/ocr` with bearer auth and JSON
`{"model":"PaddlePaddle/PaddleOCR-VL-1.6","image_base64":"..."}`. Success
returns `{"model":"...","blocks":[{"text":"...","bbox":null,"confidence":null}]}`.
`GET /health` reports the pinned revision and readiness without returning data.
The model produces a page text string; bounding boxes and confidence are null
because they are not available from this inference path.

To reproduce the service, copy `server.py` and `start.sh` to a directory on the
existing Vast instance, create a mode-0600 token file outside git, and invoke
`OCR_SERVICE_TOKEN_FILE=/path/to/token sh start.sh`. The script checks disk,
creates an isolated venv that reads but never modifies installed system packages,
downloads the pinned snapshot with `chat_template.jinja`, verifies the model
weight SHA-256, then starts one loopback worker in offline mode. Keep the process
under an operator-managed supervisor and record only its own PID for shutdown.
