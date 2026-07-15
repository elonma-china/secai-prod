# secai-deploy

Deployment bundle for the SECAI agent gateway — the `secai-deploy/` subfolder of this repo is
all you need to run the backend on another machine. The gateway **image** lives on Docker Hub
(`secai1999/secai-gateway`, built from the source in the repo root) and the ~8G runtime **data**
(Qdrant snapshot, Neo4j dump, BM25 index, Exploit-DB, PoC catalog) lives in a private Hugging
Face dataset (`secai1999/secai-data`). This folder is just the glue that pulls both and wires up
the full stack (gateway + qdrant + neo4j + redis): compose file + fetch/restore scripts + config
template. The **AI models** (LLM / embedding / reranker) are NOT bundled — they run on a remote
GPU host (vast.ai); you point `.env` at their URLs.

## Prerequisites

- Docker + Docker Compose v2
- ~20GB free disk (bundle ~8G + restored DB volumes ~7G + image ~2G; you can delete
  `data/qdrant/cve_kb.snapshot` and `data/neo4j/neo4j.dump` after a successful restore to
  reclaim ~6.5G)
- `huggingface_hub` CLI: `pip install "huggingface_hub[cli,hf_transfer]"`
- Outbound network to: Docker Hub, Hugging Face, your LLM/embedding/reranker serving host,
  and `api.deepseek.com:443` if using the DeepSeek fallback
- Access to the private `secai1999/secai-gateway` image and `secai1999/secai-data` dataset
  (ask the repo owner to grant you access, then `docker login` / `hf auth login`)

## Deploy

```bash
git clone https://github.com/elonma-china/secai-prod.git && cd secai-prod/secai-deploy
cp .env.example .env               # fill in endpoints + secrets. Set a REAL NEO4J_PASSWORD
                                   # (not 'change-me') — it is baked into neo4j on first boot.
docker login                       # pull the private gateway image
hf auth login                      # pull the private dataset

./fetch-data.sh                    # → ./data  (~8G from secai1999/secai-data)
./restore.sh                       # loads neo4j dump into ./_state, recovers qdrant snapshot, starts qdrant/neo4j/redis
docker compose pull gateway
docker compose up -d gateway

# lock down the public port to known IPs — REQUIRED on a public-IP host (the gateway is
# unauthenticated). Edit ALLOW_IPS/ALLOW_CIDRS first; run AFTER `up` so the DOCKER-USER chain exists.
sudo ./apply_firewall.sh
```

## Verify

```bash
docker compose ps                                          # all 4 services healthy
curl -s -o /dev/null -w '%{http_code}\n' http://localhost:8131/health   # 200
curl -s http://127.0.0.1:6333/collections/cve_kb | head     # status green, ~654k points
```

Then exercise the gateway itself — a chat/retrieval query and an `/ai/exploit` query for a CVE
with a known PoC (confirms BM25 + PoC catalog + Qdrant + Neo4j + your LLM endpoint are all
wired correctly, not just that the container is "up").

## Notes

- `LLM_PROVIDER`/`QWEN_*`/`EMBEDDING_*`/`RERANKER_*` endpoints are **not baked into the
  image or dataset** — they point at a remote GPU serving host (e.g. vast.ai) whose IP/port
  can change between rentals. Re-check them in `.env` before each deploy.
- The gateway is intentionally unauthenticated (no API-key check on its own endpoints) — it's
  meant to run behind a firewall/VPN. `apply_firewall.sh` allowlists `:8131` in the **DOCKER-USER**
  iptables chain (Docker-published ports bypass the INPUT chain, so an INPUT allowlist would be a
  silent no-op). Edit the allowlist for this host's real frontend/dev IPs first, and run it AFTER
  `docker compose up` (the chain only exists once Docker is running). A reverse proxy with auth
  is an alternative.
- **Neo4j password is immutable after first boot.** `NEO4J_PASSWORD` is baked into the neo4j
  store on its first initialization. Changing it in `.env` later will NOT update neo4j — the
  gateway will then get 401s on every graph query. To change it you must stop the stack and
  delete `./_state/neo4j_data`, then re-run `./restore.sh`.
- **Verify the graph is non-empty** after deploy (a named-volume/bind-mount mismatch is an easy
  regression here): `docker compose exec neo4j cypher-shell -u neo4j -p "$NEO4J_PASSWORD" \
  "MATCH (n) RETURN count(n);"` should return ~491,776 — not 0.
- Qdrant/Neo4j/Redis ports are not published to the network (Qdrant's 6333 is bound to
  `127.0.0.1` only, for `restore.sh`'s own use) — only the gateway's `:8131` is exposed.
- To update to a new data snapshot later: bump `SECAI_DATA_REVISION`, re-run `./fetch-data.sh`
  + `./restore.sh` (the qdrant/neo4j recover steps are idempotent — check the running
  collection/database first if you need a hard refresh instead of an initial load).
