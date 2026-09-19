# AIEngines / secai-prod

## Main project

**[`secai/secai-agent-gateway`](secai/secai-agent-gateway/README.md)** — the IAS v2 security-AI
gateway (FastAPI). This is the one production service in this repo; everything else here is
supporting infrastructure, data, or exploratory work for it. Start with its README for
architecture, endpoints, and how to run/deploy it.

## Auxiliary directories (under `secai/`, unless noted)

- **`deploy/`** (inside `secai-agent-gateway/`, formerly `secai-docker/`) — the docker-compose
  stack that stands up the gateway + Qdrant + Neo4j + Redis together; the LLM/embedding/
  reranker serving stays on vast.ai, called over HTTP.
- **`KPI-VA-GEN-TEST/`** — the 19-KPI benchmark harness (retrieval/generation/perf/models)
  that both the acceptance dossier and the gateway's KPI Studio (`/kpi-studio/` Live-run mode)
  shell out to.
- **`HO-SO-GOC-NGHIEM-THU-AI/`** — the official acceptance/hand-over evidence dossier (mirrors
  an external Drive folder); read-only bind-mounted into the gateway container for the
  `/showcase-*` endpoints. Not in git (too large) — see
  `secai-agent-gateway/docs/nghiem-thu/` for the consolidated paperwork that *is* tracked.
  Vietnamese doc names use "nghiệm thu" (acceptance testing / customer sign-off).
- **`secai-fe-devspace/`** — the frontend dev workspace that talks to the gateway.
  `secai-kali-executor/` — the sandboxed executor container backing `/ai/verify`, `/ai/tool`,
  `/ai/exec`. `secai-onebox/` — a self-contained one-VM deployment variant
  (serving+DB+gateway together, used for air-gapped demos).
  `secai-data/` — the Qdrant/Neo4j/exploitdb/PoC data volumes the gateway (and `deploy/`)
  bind-mount at runtime.
- **`FINETUNE/`, `DATA/`, `studio-llamafactory/`, `studio-promptfoo/`, `clearml-server/`,
  `KPI-VA-GEN-TEST/mlops/`** — model finetuning/eval/MLOps working trees feeding the same
  acceptance dossier; mostly large working data, not source you'd normally need to touch the
  gateway.
- **`secai-BK/`** — pre-reorg backup of older `secai/` content, kept for reference only.

## Data

The large data/model/vendor trees (`secai/secai-data/`, `secai-BK/`, `finetune_dataset/`,
`.venv/`, etc.) are **gitignored** — they live on this host (or on vast.ai for
LLM/embedding/reranker serving) but are not part of this git history. See this repo's
`.gitignore` and `secai-agent-gateway/README.md`'s "Deploy" section for exactly which external
paths a fresh deployment needs to bind-mount back in.
