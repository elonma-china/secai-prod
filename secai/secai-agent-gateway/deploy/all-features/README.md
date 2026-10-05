# Full feature release

The same image contains the current report, file/OCR translation, multilingual and
expert review source, the static Devspace build and the isolated report entrypoint.
Gateway, workers and report service keep their existing commands, private settings
and database/volume mounts. No model is served inside these CPU containers.

Build the normal gateway Dockerfile for a new base dependency set. For a source-only
release, pin the existing verified gateway image as `GATEWAY_IMAGE`; confirm imports
and `pip check` in the candidate. Assemble a private build directory with only
`src/`, `pyproject.toml`, `devspace/dist`, `devspace/serve_static.py` and
`report_runtime.py` from `deploy/report/runtime.py`. Copy this Dockerfile there:

```sh
VITE_REPORT_FEATURE_ENABLED=true VITE_TRANSLATE_V2_FILES_ENABLED=true \
  VITE_TRANSLATE_V2_IMAGES_ENABLED=true npm run build # from secai-fe-devspace
docker build --build-arg GATEWAY_IMAGE=<immutable-gateway-image> \
  --build-arg SOURCE_REVISION=<main-commit> \
  -t secai-agent-gateway:all-features-<main-commit> <private-build-directory>
```

Host release state and rollback profiles live outside Git at
`/home/ict/secai-releases/all-features-20261004`, mode 0700, with private JSON mode
0600. Save the exact current Compose profiles and immutable images before switching.
Update only the explicitly owned gateway, file worker, translation gateway/worker/
frontend and report services, using `up -d --no-deps --no-build`. Preserve named
volumes and bind mounts, and never run `down -v`. Check container ownership and
deployment locks again immediately before applying. Roll back only containers still
owned by this release, to the saved profiles and images; keep current databases.

All client APIs now use gateway **8131**. The primary entrypoint is
`secai_gateway.unified_app:app`: legacy chat/session/project/exploit/review stay
local; `/ai/v2/reports` forwards to `REPORT_UPSTREAM_URL`, and both
`/ai/v2/translate` and `/ai/v3/translate` (including stream) forward to
`TRANSLATION_UPSTREAM_URL`. Auth headers, request bodies and streaming responses
are preserved. Report and translation stores/workers remain in their services.

Current private upstreams: report `http://secai-report-backend:8131` on the existing
report Docker network; translation `http://host.docker.internal:18185`. Attach the
primary gateway to the external report network and preserve host-gateway mapping.
Devspace `GATEWAY`, `REPORT_GATEWAY_PROXY`, `TRANSLATION_GATEWAY_PROXY` and local
upstream marker files all point to `http://127.0.0.1:8131`. Client documentation
must use 8131, never the internal upstream ports.

The English/stream rollout snapshots and rollback profiles are stored privately at
`/home/ict/secai-releases/translation-v3-en-stream-20261005`. V3 now has 18
translation directions; `MULTILANG_ENABLE_EN=true` opts in to English/Vietnamese.
The live Hy-MT source chunk budget is 128 tokens to leave room for Vietnamese
output within the serving output cap. SSE emits validated chunks, then final;
a later chunk failure emits error without a successful final.

Readiness includes authenticated report/template reads and persisted revisions,
unauthorized review rejection, multilingual language capabilities, file/image route
availability, stable worker processes and actual inference smoke. `/health` alone
does not establish model or OCR readiness. Keep smoke source synthetic and preserve
report outputs. Model identities, GPU ownership and memory cleanup are checked on
the existing Vast instance independently of the Docker rollout.

Devspace reports now connect automatically: the browser sends `X-Report-Workspace: shared`, and the static proxy loads the workspace key from private `REPORT_KEY_FILE` (host default `.report-api-key.local`). Docker mounts `/run/devspace/report-api.key` read-only. This uses the existing configured Tailnet/loopback connection guard; the Cloudflare loopback shortcut is explicitly enabled for internal shared-admin Devspace. The backend still requires `X-API-Key`. Never bake the key into the image or frontend bundle. Frontend grid/preview rollout and rollback profiles: `/home/ict/secai-releases/translation-file-preview-20261005`.

Report export rollout: report backend and frontend use immutable image `sha256:ac510772daebc9b37afe7d49005dfa023b6bea3aa66d07b156cdf7abc816b0ae`. Renderers require python-docx, markdown-it-py and reportlab; PDF embeds DejaVu regular/bold fonts carried with their license. The standard Dockerfile installs fonts-dejavu-core. Profiles, snapshots and the pre-rollout SQLite backup remain private in `/home/ict/secai-releases/report-export-20261005`. The shared gateway still forwards exports through 8131; workers/model services are unchanged.
