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
snapshot SHA and each downloaded file checksum. Load and each inference require
at least 9216 MiB free GPU memory. This deliberately rejects requests if the
incumbent models leave less headroom. The OCR process is limited to 7168 MiB
peak reserved GPU memory. Start one uvicorn worker only. Keep the service token
outside git and outside process arguments; use an environment file readable
only by the service owner. Never print it in logs.

The wire API is `POST /v1/ocr` with bearer auth and JSON
`{"model":"PaddlePaddle/PaddleOCR-VL-1.6","image_base64":"..."}`. Success
returns `{"model":"...","blocks":[{"text":"...","bbox":null,"confidence":null}]}`.
`GET /health` reports the pinned revision and readiness without returning data.
The model produces a page text string; bounding boxes and confidence are null
because they are not available from this inference path.
