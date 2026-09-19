# deploy/ — consolidated Docker stack for secai-agent-gateway

2026-09-19: this directory vendors what used to live in the sibling `secai-docker/`
project (kept there as rollback/history — see its own README). The main change is
that the gateway image is now **self-contained**: KPI bench code
(`KPI-VA-GEN-TEST/`, runnable subset) is baked into the image at
`/app/kpi_bench` via the Dockerfile (`COPY kpi_bench ./kpi_bench`), so the
container no longer needs a host bind-mount of that directory to run KPI Studio
"Live" groups B/C.

## Components (5 containers)

| Service | Image | Role | Host port |
|---|---|---|---|
| `secai-docker-gateway` | `secai-agent-gateway:0.6.0-consolidated` (built from `..`, i.e. this repo) | BE FastAPI (IAS gateway) + baked-in KPI bench | `${GATEWAY_HOST_PORT:-8140}` → 8131 |
| `secai-docker-qdrant` | `qdrant/qdrant:v1.10.1` | Vector store (collection `cve_kb_hybrid`) | 6333 / 6334 |
| `secai-docker-neo4j` | `neo4j:5.20-community` | Knowledge graph | 7474 / 7687 |
| `secai-docker-redis` | `redis:7-alpine` | Conversation history (ephemeral 24h, legacy path) | 6379 |
| `secai-docker-es` | `elasticsearch:8.15.3` | Activity log store for `/logs` (internal only) | 127.0.0.1:9200 |

Data (KB/exploitdb/PoC/showcase dossier) still uses **absolute-path bind-mounts**
into the existing host dirs (`${SECAI_DATA_ROOT}`, `HO-SO-GOC-NGHIEM-THU-AI/`,
`/home/ict/secai-88/source/data`) — unchanged, location-independent regardless of
where this `deploy/` directory sits inside the repo.

## ⚠ Container/project names are intentionally identical to `secai-docker/`

`docker-compose.yml` here keeps `name: secai-docker` and the same
`container_name:`/named-volume values as `secai-docker/docker-compose.yml`. This is
on purpose: this file is meant to eventually **replace** (cut over, not run
alongside) the sibling stack — same container names + same named volumes
(`secai-gw-data`, `secai-redis-data`) means a future `up -d` from here upgrades
the running stack in place (keeps sessions/sqlite data), the same as any other
image-tag bump.

**Consequence:** do NOT run `up`/`down`/`restart` from this file until the
orchestrator has decided to cut over — it targets the exact same container names
as whatever is live. `build` is always safe (never touches running containers).

## Build (safe, does not touch the running stack)

```bash
cd /home/ict/AIEngines/secai/secai-agent-gateway
cp /home/ict/AIEngines/secai/secai-docker/.env deploy/.env   # real secrets, gitignored
docker compose -f deploy/docker-compose.yml --env-file deploy/.env build gateway
docker images secai-agent-gateway:0.6.0-consolidated
```

## Smoke-test on a throwaway port (never 8131)

Run a temporary, distinctly-named container against the new image on a spare
host port (e.g. 8135), with the same `.env` + the mounts this compose file
defines (skip the KPI-VA-GEN-TEST mount — it's baked in now). Verify:

```bash
curl -s localhost:8135/health
curl -s localhost:8135/kpi-studio/
curl -s localhost:8135/showcase-api/kpi-studio/catalog | jq '.kpis | length'   # 19
curl -s localhost:8135/showcase-api/kpi-run/status
curl -sN "localhost:8135/showcase-api/kpi-run/stream?scope=group&group=C"     # SSE: line…, done
```

Then `docker rm -f` the temp container — never leave it running.

## Cutover (not part of this consolidation pass — orchestrator decision)

```bash
docker compose -f deploy/docker-compose.yml --env-file deploy/.env up -d gateway
```

This recreates `secai-docker-gateway` in place with the new image. Roll back by
switching `image:` back to `secai-agent-gateway:0.5.1-kpi-live` and re-running
`up -d gateway`, or by falling back to `secai-docker/docker-compose.yml` (still
present, untouched).

## Notes carried over from `secai-docker/`

- **Model stays on vast**: `QWEN_BASE_URL` / `EMBEDDING_API_URL` /
  `RERANKER_API_URL` in `.env` point at the vast.ai serving instance. This stack
  does not serve models locally.
- **KPI Studio group A** (11 quality KPIs, `chay_eval_gateway_moi.sh`) still only
  runs on the HOST, not inside this container: it needs a writable `runs/` dir
  and a real `.env.bench` with judge/API keys, neither of which is baked into the
  image (secrets + run-churn are deliberately excluded — see
  `kpi_bench/.gitignore`). This is unchanged from the prior
  `secai-docker/docker-compose.yml` behavior (group A was already host-only
  there too). Groups B and C (throughput + chatflow perf) run fully inside the
  container now.
- `.env` (and this dir's own `.env` copy) contain secrets → gitignored.
