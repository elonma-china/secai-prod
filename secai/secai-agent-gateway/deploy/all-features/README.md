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

Shared Devspace uses gateway 8131, report upstream 18183 and translation upstream
18185. Configure the last two through `REPORT_GATEWAY_PROXY` and
`TRANSLATION_GATEWAY_PROXY` or their local upstream marker files. Both v2 file/image
jobs and v3 text translation use the translation upstream; expert review and legacy
chat/report APIs use the gateway. The private translation Devspace on 5179 uses the
same routing settings after upgrading its static assets.

Readiness includes authenticated report/template reads and persisted revisions,
unauthorized review rejection, multilingual language capabilities, file/image route
availability, stable worker processes and actual inference smoke. `/health` alone
does not establish model or OCR readiness. Keep smoke source synthetic and preserve
report outputs. Model identities, GPU ownership and memory cleanup are checked on
the existing Vast instance independently of the Docker rollout.
