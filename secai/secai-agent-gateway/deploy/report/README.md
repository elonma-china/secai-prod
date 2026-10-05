# Report backend Docker deployment

The report backend retains its private loopback port 18183. Client APIs and
Devspace use the common gateway on **8131**, whose report upstream reaches this
service on its Docker network. Set frontend REPORT_GATEWAY_PROXY and
TRANSLATION_GATEWAY_PROXY to `http://127.0.0.1:8131`; upstream marker files use
the same address. Keep credentials out of URLs and the frontend bundle.

The internal Devspace workspace proxy supplies the report API key from its private
key file, as documented in `../all-features/README.md`. Direct backend requests
still require X-API-Key.

Report export supports TXT, DOCX and PDF via
`GET /ai/v2/reports/{id}/export?format=pdf&revision=N`. PDF needs reportlab and
DejaVu Sans regular/bold fonts at `/usr/share/fonts/truetype/dejavu/`. The standard
Dockerfile installs fonts-dejavu-core; source-overlay releases must also carry
these fonts and their license. Export does not run the model or modify the DB.

Build reproducibly from the gateway root:

```sh
docker build -t secai-agent-gateway:reports-core .
docker build --build-arg GATEWAY_IMAGE=secai-agent-gateway:reports-core \
  -f deploy/report/Dockerfile -t secai-report-backend:20261004 .
```

The deployed image was built as a report overlay on the existing gateway image to preserve its dependencies and behavior. Its immutable release context and image metadata are retained under `/home/ict/secai-report-devspace/docker-release-20261004-180339`; the final report image is `secai-report-backend:20261004`.

The existing host deployment uses `/home/ict/secai-report-devspace/docker-report/compose.json`, equivalent to `compose.yaml` here. Keep private model settings and the report API key in `report.env` (0600), outside image build context. Configure `REPORT_FEATURE_ENABLED=true`, `REPORT_DB_PATH=/srv/reports/reports.db`, `GATEWAY_DB_PATH=/app/data/gateway.db`, `POC_CATALOG_DB=/app/data/catalog.db`, `EXPLOITDB_ROOT=/app/data/exploitdb`, `SESSION_STORE_BACKEND=memory`, `REDIS_URL=` and the existing LLM settings. The data directories must be writable by container UID 10001; the current deployment grants that UID access with filesystem ACLs.

The versioned `switch.py` is the deployed host-specific helper `/home/ict/secai-report-devspace/report-docker.py`. It controls only the report Compose project and the previous `secai-report-devspace.service` fallback:

```sh
/home/ict/secai-report-devspace/report-docker.py apply
/home/ict/secai-report-devspace/report-docker.py rollback
```

Apply creates a SQLite backup, stops the fallback service, starts the report container and verifies authenticated readiness. Failure automatically restores the fallback. Rollback stops only the report container and enables/starts the previous systemd service at the same port. Both use the same original DB, so code rollback preserves reports created after deployment. Successful apply disables the fallback service to avoid port collisions after reboot. An operation lock serializes report switches; a container owned by another Compose project is rejected.

Actual create/edit/readback, rollback and Docker reapply were verified with the configured model and synthetic input. Both old and new reports retained two revisions. Deployment evidence, DB backups and private configuration remain outside Git at `/home/ict/secai-report-devspace/docker-report/`. The backend unit/session suite has 77 passing tests; the wider suite remains blocked by external Bughunt/PoC fixtures, as recorded in `docs/test-results/report-quality-fixes.md`.
