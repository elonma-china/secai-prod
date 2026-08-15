# secai-agent-gateway

Private-network IAS v1 gateway backed by [Linch](https://pypi.org/project/linch/). It
exposes the legacy IAS endpoints without normal API authentication; deploy it only behind
a VPN/private ingress or an IP-allowlisting reverse proxy. `X-API-Key` is required only
for `/admin/refresh-models`.

The LLM backend is selected by `LLM_PROVIDER` (`deepseek` → DeepSeek `deepseek-v4-flash`,
or `vllm` → local Qwen). Chat and PoC research are the only agentic profiles;
translate/report/statistical profiles never receive tools.

## Deploying to another machine

This repo is the **source** (for development / building the image). To stand up the full backend
on another machine — pull the prebuilt image + the ~8G Qdrant/Neo4j/BM25/PoC data and wire up the
whole stack — use the separate deployment repo **[elonma-china/secai-deploy](https://github.com/elonma-china/secai-deploy)**
and follow its README. Cloning THIS repo and running the root `docker-compose.yml` alone is NOT
enough for deployment: it builds the gateway only, with no databases and no data.

## Run (development, from source)

```bash
# 1. install (editable). Standard:
python3 -m pip install -e '.[dev]'
#    or with uv:  VIRTUAL_ENV=.venv uv pip install -e '.[dev]'

# 2. configure — all settings are read from .env (pydantic-settings)
cp .env.example .env        # set LLM_PROVIDER, DEEPSEEK_API_KEY, QDRANT_URL,
                            # NEO4J_*, REDIS_URL, EMBEDDING/RERANKER_API_URL,
                            # EXPLOITDB_ROOT, ALLOWED_ORIGINS, ADMIN_API_KEY

# 3. serve
uvicorn secai_gateway.app:app --host 127.0.0.1 --port 8080
```

Check it's up: `curl -s http://127.0.0.1:8080/health` (reports LLM, Redis, searchsploit).
Run the tests with `pytest` (hermetic — no live services needed).

## Endpoints

Each AI task has a JSON form and an SSE `…/stream` form (streams `tool_call` /
`tool_result` / `token` events then one `final` event, ending with `data: [DONE]`):

- `POST /ai/chat` · `POST /chat_no_stream` · `POST /ai/chat/stream` — security assistant (CVE/CWE Q&A, remediation); stateful per `user_id`
- `POST /ai/translate` · `POST /ai/translate/stream` — EN⇄VI security translation
- `POST /ai/generate-report` · `POST /ai/generate-report/stream` — analyse a log/data blob into a Markdown report
- `POST /ai/generate-statistical-report` · `…/stream` — period report as `report_title`/`summary`/`detail`
- `POST /ai/exploit` · `POST /ai/exploit/stream` — Exploit-DB-grounded PoC research
- `GET  /health` · `POST /admin/refresh-models` (needs `X-API-Key`)
- Interactive API docs at `/docs` (Swagger) and `/redoc`

## Memory

Conversation memory is **unified in Redis** via Linch's own session store
(`src/secai_gateway/state/session_store.py`, keys `linch:sess:{id}`, 24h TTL). Chat sends
only the new message with a stable per-user session id; Linch loads prior turns and applies
**proactive compaction** (`CompactionLadder`, keep-recent-10) so long chats stay under the
window with a prompt-cacheable prefix. Without `REDIS_URL` it falls back to an in-memory
store (dev/test).

## Knowledge base

- **qdrant_search** — hybrid dense (bge-m3) + BM25 retrieval over the `cve_kb` collection,
  RRF-fused and reranked. CVE/CWE/CAPEC/ATT&CK IDs in the query are auto-detected and pinned.
- **neo4j_search** — graph traversal (CVE↔CWE↔CAPEC↔Technique↔Tactic↔Product), plus
  product entry-points and count/statistics queries.

## PoC / Exploit-DB

Runs **in-process** as a plain tool — no separate service. The Exploit-DB clone is
read-only; it never executes artifacts, targets, mirrors, updates, or runs Nmap. `/ai/exploit`
can return an unexecuted `draft_not_executed` candidate only.

**Section 1 (the fetched PoC) is placed deterministically and shown in full.** During its
agentic turn the model sees each fetched artifact capped to a context budget
(`_MAX_CODE_CHARS`/`_MAX_SET_CHARS` in `poc/backend.py`), so long chats stay under the window.
But the PoC rendered into the response's Section 1 — the `description`/`exploit_guide` text the
UI displays — is **re-read from the content store at injection time (after the model runs) and
shown untruncated, as the whole repo**: a repo tree plus one path-headed block per file, entry
first, including sibling files the fetch budget skipped. This keeps the model context bounded
while the displayed PoC is complete — no extra endpoint, no second round-trip
(`_fullen_evidence_for_display` in `api/routes.py`, budget `_MAX_DISPLAY_CHARS`). Full source is
only ever the real fetched artifact bytes for license-cleared sources (never model-invented),
gated by the same license + path-escape rules as `poc_fetch`; the gateway is private-network
only, so serve it accordingly.

## Layout

```
src/secai_gateway/
  config.py        # Settings (pydantic-settings) — all env in one place
  app.py           # create_app(): middleware + routes
  api/             # schemas · sse · messages · middleware · routes
  agent/           # profiles (from prompts/) · providers · factory · runner
  retrieval/       # qdrant · filters · bm25 · embedding · neo4j · cypher
  poc/             # service · models · tools
  state/           # session_store (Redis) · ledger · ratelimit
  prompts/         # profiles.yaml + base_system.md + per-profile .md
```

System prompts live in `src/secai_gateway/prompts/` — edit the `.md` files (and
`profiles.yaml` for tools/turn caps) without touching Python.
