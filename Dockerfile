# secai-agent-gateway — IAS v2 FastAPI/uvicorn gateway (port 8131).
#
# No GPU: this service is a thin gateway that calls OUT over HTTP to the LLM,
# embedding/reranker, Qdrant, Neo4j and Redis — so a slim CPU base is correct.
#
# NOTE: written for Docker's LEGACY builder (this host has Docker 29 without
# buildx/BuildKit) — no `RUN --mount`, no heredocs, no `COPY --link`.
#
#   Build:  docker build -t secai-agent-gateway:0.2.0 .
#   Run:    docker run -d -p 8131:8131 --env-file .env \
#             -v secai-gw-data:/app/data secai-agent-gateway:0.2.0
#   (or just: docker compose up --build)

# ---------- builder: resolve deps into an isolated venv ----------
FROM python:3.12-slim AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# Only what the hatchling build backend needs to install the package + its deps.
COPY pyproject.toml ./
COPY src ./src

RUN python -m venv /opt/venv \
 && /opt/venv/bin/pip install --upgrade pip \
 && /opt/venv/bin/pip install ".[redis]"

# ---------- runtime: slim, non-root, source copied verbatim ----------
FROM python:3.12-slim AS runtime

# PYTHONPATH=/app/src makes the copied source (with prompts/*.md + profiles.yaml
# sitting next to the code) win over the site-packages copy, so data-file lookups
# via __file__ always resolve. Env defaults are in-container sane; override the
# externally-facing ones at runtime with --env-file .env.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/opt/venv/bin:$PATH" \
    PYTHONPATH="/app/src" \
    GATEWAY_DB_PATH=/app/data/gateway.db \
    SEARCHSPLOIT_BIN=/app/bin/searchsploit \
    EXPLOITDB_ROOT=/srv/exploitdb \
    BM25_DIR=/srv/bm25/bm25_cve_kb \
    POC_CATALOG_DB=/srv/poc/catalog.db \
    POC_SOURCES_ROOT=/srv/poc-sources \
    POC_ARTIFACT_STORE=/srv/poc-store

# bash: the vendored searchsploit is a bash script. tini: correct PID-1 signals.
RUN apt-get update \
 && apt-get install -y --no-install-recommends bash tini \
 && rm -rf /var/lib/apt/lists/*

RUN useradd --system --create-home --uid 10001 app

WORKDIR /app

# Third-party dependencies, pre-installed in the builder venv.
COPY --from=builder /opt/venv /opt/venv

# Application source + the vendored searchsploit helper.
COPY src ./src
COPY bin ./bin

# Vendored KPI bench code (KPI Studio "Live" run, groups B/C — see kpi_run.py) — was
# previously bind-mounted from a sibling KPI-VA-GEN-TEST/ dir; now baked in so the image
# is self-contained/portable. bash + python3 (already installed above) run it as-is.
COPY kpi_bench ./kpi_bench

# Writable state dir + mountpoints for the secai-data bundle, owned by the non-root user.
# /srv/poc is bind-mounted read-write at runtime: PocCatalog opens sqlite with
# `PRAGMA journal_mode=WAL`, which needs to create catalog.db-wal/-shm next to catalog.db.
RUN mkdir -p /app/data /srv/exploitdb /srv/bm25 /srv/poc /srv/poc-sources /srv/poc-store \
 && chmod +x /app/bin/searchsploit \
 && chown -R app:app /app /srv/exploitdb /srv/bm25 /srv/poc /srv/poc-sources /srv/poc-store

USER app

EXPOSE 8131

# App-is-up probe: /health returns 200 even when LLM/Redis are "degraded".
HEALTHCHECK --interval=30s --timeout=6s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8131/health',timeout=5).status==200 else 1)"

ENTRYPOINT ["tini", "--"]
CMD ["uvicorn", "secai_gateway.app:app", "--host", "0.0.0.0", "--port", "8131"]
