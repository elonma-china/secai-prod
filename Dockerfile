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

# ---------- deps: third-party dependencies only (cached until pyproject.toml changes) ----------
# Only pyproject.toml is copied here, so editing src/ does NOT invalidate this (slow, ~6 min)
# layer. The dependency list is read from [project].dependencies + the `redis` extra, which
# resolves exactly like `pip install ".[redis]"` did, minus the package itself.
FROM python:3.12-slim AS deps

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

COPY pyproject.toml ./

RUN python -c "import tomllib; p = tomllib.load(open('pyproject.toml', 'rb'))['project']; print('\n'.join(p['dependencies'] + p['optional-dependencies']['redis']))" > /tmp/requirements.txt \
 && python -m venv /opt/venv \
 && /opt/venv/bin/pip install --upgrade pip \
 && /opt/venv/bin/pip install -r /tmp/requirements.txt

# ---------- wheel: build the secai-agent-gateway package itself (cheap, reruns on src edits) ----------
FROM python:3.12-slim AS wheel

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

COPY pyproject.toml ./
COPY src ./src

RUN pip wheel --no-deps --wheel-dir /wheels .

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
 && apt-get install -y --no-install-recommends bash tini fonts-dejavu-core libreoffice-writer \
 && rm -rf /var/lib/apt/lists/*

RUN useradd --system --create-home --uid 10001 app

WORKDIR /app

# Layer order (legacy builder): everything that does NOT depend on src/ comes first, so a
# source-only edit re-runs just the package install + `COPY src` + the build-arg tail
# (each re-run step costs a few seconds of layer commit on this host even when trivial).

EXPOSE 8131

# App-is-up probe: /health returns 200 even when LLM/Redis are "degraded".
HEALTHCHECK --interval=30s --timeout=6s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8131/health',timeout=5).status==200 else 1)"

ENTRYPOINT ["tini", "--"]
CMD ["uvicorn", "secai_gateway.app:app", "--host", "0.0.0.0", "--port", "8131"]

# Third-party dependencies, pre-installed in the deps venv (~440 MB, cached across
# source-only rebuilds).
COPY --from=deps /opt/venv /opt/venv

# The vendored searchsploit helper.
COPY bin ./bin

# Vendored KPI bench code (KPI Studio "Live" run, groups B/C — see kpi_run.py) — was
# previously bind-mounted from a sibling KPI-VA-GEN-TEST/ dir; now baked in so the image
# is self-contained/portable. bash + python3 (already installed above) run it as-is.
COPY kpi_bench ./kpi_bench

# Writable state dir + mountpoints for the secai-data bundle, owned by the non-root user.
# /srv/poc is bind-mounted read-write at runtime: PocCatalog opens sqlite with
# `PRAGMA journal_mode=WAL`, which needs to create catalog.db-wal/-shm next to catalog.db.
# /srv/reports + /app/review-data: named volumes of stack/compose.yml inherit this ownership (otherwise root-only, app cannot write).
RUN mkdir -p /app/data /app/review-data /srv/reports /srv/exploitdb /srv/bm25 /srv/poc /srv/poc-sources /srv/poc-store \
 && chmod +x /app/bin/searchsploit \
 && chown -R app:app /app /srv/exploitdb /srv/bm25 /srv/poc /srv/poc-sources /srv/poc-store /srv/reports

# --- from here on, layers depend on src/ ---

# The package itself (dist-info metadata + site-packages copy, as before), installed with
# --no-deps on top of the cached venv: the only venv change on a source edit.
COPY --from=wheel /wheels /tmp/wheels
RUN /opt/venv/bin/pip install --no-deps --no-cache-dir --disable-pip-version-check /tmp/wheels/*.whl \
 && rm -rf /tmp/wheels

# Application source (PYTHONPATH=/app/src), owned by the non-root user as before.
COPY --chown=app:app src ./src

USER app

# Build provenance, surfaced by /health (git_branch/git_sha). Declared last so changing them
# only invalidates these layers. Pass at build time:
#   docker build --build-arg GIT_SHA=$(git rev-parse --short HEAD) \
#                --build-arg GIT_BRANCH=$(git branch --show-current) ...
ARG GIT_SHA=""
ARG GIT_BRANCH=""
ENV GIT_SHA=${GIT_SHA} \
    GIT_BRANCH=${GIT_BRANCH}
