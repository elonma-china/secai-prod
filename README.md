# secai-agent-gateway

IAS v2 security-AI gateway — a private-network FastAPI service that gives the IAS platform
(ias.id.vn) LLM-backed security assistance: CVE/CWE chat, EN⇄VI security translation,
multilingual text/file/image translation, persisted template reports, expert review, and
Exploit-DB / live-target PoC research. It also serves an acceptance ("nghiệm thu") showcase
and a KPI benchmarking studio for operators.

The gateway is CPU-only and stateless-ish: it holds no model weights. All model services
(LLM, embedding, reranker, OCR, specialist MT) are called over HTTP to GPU serving
infrastructure; the datastores it depends on (Qdrant, Neo4j, Redis) are deployed alongside it.

**Access control.** Legacy IAS endpoints assume private-network access — deploy behind a
VPN / private ingress or an IP-allowlisting reverse proxy. Report v2 requires its own
`X-API-Key`; expert review requires Bearer credentials with reviewer/admin permissions;
admin and raw-exec endpoints require a separate admin key.

## Architecture

```
FE / IAS platform
      │  HTTP (JSON or SSE)
      ▼
secai-agent-gateway (FastAPI / uvicorn, CPU-only, :8131)
      │
      ├─ LLM (chat completion)        ─┐
      ├─ embedding + reranker          ├─ HTTP to GPU serving (vast.ai)
      ├─ OCR + specialist MT          ─┘
      │
      ├─ Qdrant   hybrid dense + BM25 vector search (collection cve_kb_hybrid)
      ├─ Neo4j    CVE ↔ CWE ↔ CAPEC ↔ Technique ↔ Tactic ↔ Product graph
      ├─ Redis / SQLite   conversation & session memory
      └─ Exploit-DB clone + PoC catalog/store (local disk, in-process)
```

Model names requested by the gateway must match what the serving layer exposes
(fine-tuned `Cyber-F1` LLM, `sec-embedding`, `sec-rerank`); substituting stock
embedding/reranker models causes a model-name mismatch that degrades retrieval. See
[`deploy/`](deploy/) for the docker-compose stack that wires the gateway to its datastores
and serving endpoints.

## Key endpoints

Every `POST` AI task has a plain JSON form and (mostly) an SSE `…/stream` form that emits
`tool_call` / `tool_result` / `token` events followed by one `final` event and `data: [DONE]`.
Interactive API docs are at `/docs` (Swagger) and `/redoc`.

### Chat / assistant
- `POST /ai/chat` · `POST /chat_no_stream` · `POST /ai/chat/stream` — security assistant
  (CVE/CWE Q&A, remediation guidance), stateful per `user_id`.
- `POST /sessions/{sid}/chat` · `POST /sessions/{sid}/chat/stream` — same, scoped to a
  Project/Session instead of a bare `user_id`.

### Project & session management
ChatGPT-style sidebar backing store; ownership must match the caller's `user_id`.
- `POST /projects` · `GET /projects` · `GET /projects/{pid}` · `PATCH /projects/{pid}` ·
  `DELETE /projects/{pid}`
- `POST /projects/{pid}/sessions` · `GET /projects/{pid}/sessions`
- `POST /sessions` · `GET /sessions` · `GET /sessions/{sid}` · `PATCH /sessions/{sid}` ·
  `DELETE /sessions/{sid}` · `GET /sessions/{sid}/messages`

### Translate / report
- `POST /ai/translate` · `POST /ai/translate/stream` — EN⇄VI security translation with
  literal masking (accounts/passwords pass through verbatim) and long-input chunking.
- `POST /ai/generate-report` · `POST /ai/generate-report/stream` — log/data blob → Markdown
  report.
- `POST /ai/generate-statistical-report` · `…/stream` — period report as
  `report_title` / `summary` / `detail`.

### Exploit / PoC research
Exploit-DB-grounded and read-only unless an engine is explicitly enabled.
- `POST /ai/exploit` · `POST /ai/exploit/stream` — agentic PoC research grounded in the
  Exploit-DB corpus; returns an unexecuted `draft_not_executed` candidate.
- `POST /ai/exploit/run` — deterministic (non-LLM) PoC execution via the pocsuite3 engine.
  Disabled unless `POC_EXPLOIT_ENABLED` + `POC_EXPLOIT_API_URL` are set (404 otherwise).
- `GET /ai/exploit-advance/fields` — form spec for the multi-step live-exploit request.
- `POST /ai/exploit-advance/stream` · `POST /ai/exploit-advance` · `POST /ai/exploit-advance/reply`
  — multi-step exploitation of one CVE against a real target, streaming
  `step` / `log` / `final` / `question` events (`question` is a human-in-the-loop pause,
  resumed via `/reply`). Requires the pocsuite engine or `LANE1_ENABLED` and the target on the
  allowlist (`VERIFY_ALLOWED_TARGETS` / `VERIFY_ALLOW_ALL`).
- `POST /ai/verify` — verify-only PoC-against-target check via secai-kali-executor. Disabled
  unless `VERIFY_ENABLED` (404 otherwise).
- `POST /ai/bughunt/stream` — target-driven ReAct recon/vuln-scan with a deterministic
  validation gate.
- `POST /ai/tool` — scoped pentest tool (nmap/searchsploit/gobuster/sqlmap/hydra) in the Kali
  executor. Disabled unless `VERIFY_ENABLED`.
- `POST /ai/exec` — raw command execution in a privileged Kali container. Disabled unless
  `EXEC_ENABLED`, and always requires `X-API-Key == ADMIN_API_KEY`.

### Report v2, file/OCR translation, expert review

| Feature | API | Storage / entrypoint |
| --- | --- | --- |
| Report templates | `GET/POST /ai/v2/reports/templates`, `PATCH /templates/{id}`, `POST /templates/import` | `REPORT_FEATURE_ENABLED`, `REPORT_API_KEY`, SQLite `REPORT_DB_PATH` |
| Persisted reports | `POST /ai/v2/reports/generate`, `GET /ai/v2/reports/{id}`, `POST /{id}/edit`, `GET /{id}/revisions` | content + revisions persisted; edit checks expected revision |
| File/image translation | `POST /ai/v2/translate/files`, `POST /ai/v2/translate/images`, job status/result routes | dedicated worker + durable job/output storage; see [contract](docs/api/translate-v2-contract.md) |
| Multilingual text | `POST /ai/v3/translate`, `GET /ai/v3/translate/languages` | `secai_gateway.multilang_app:app`, `MULTILANG_ENABLED` + per-pair flags |
| Expert review | `/review-api/me`, campaigns, assignments, judgments, export | `REVIEW_ENABLED`, SQLite `REVIEW_DB_PATH`, Bearer credentials from `REVIEW_CREDENTIALS_FILE` |

Report v2 uses a shared API key in this MVP; tenant isolation and advanced publication are
out of scope. V3 translation exposes its own schema at `/ai/v3/translate/openapi.json` (not
listed in the legacy `/openapi.json`). Legacy report routes remain available.

### Ops
- `GET /health` — LLM reachability, session backend (Redis/SQLite) status, searchsploit /
  Exploit-DB commit, and the input-classifier fail-open rate.
- `POST /admin/refresh-models` — requires `X-API-Key`.
- `/showcase-api/*` plus static SPAs at `/showcase-demo/` and `/kpi-studio/` (see
  [Showcase + KPI Studio](#showcase--kpi-studio)).

## Sanitization

Every request is filtered on the way in and every answer on the way out.

**Input — redaction gate.** `require_public_input` (`security.py`) rejects prompts carrying
secrets/PII (private keys, `sk_`/`AKIA` tokens, `password:` / `bearer …`, long card-like digit
runs) with `422`; only public or pre-redacted input reaches the model.

**Input — model-based classification** (`src/secai_gateway/sanitize/`). Each chat prompt is
embedded with the deployed `sec-embedding` model and a small numpy head assigns intent
(`security | chitchat | offtopic`, softmax) plus independent `injection` and `pii` sigmoids;
`gate.decide` then acts:
- `injection` → blocked with a safe refusal (never reaches the LLM);
- `offtopic` → deterministic `<scope_policy>` refusal (configurable; production runs with
  `INPUT_CLASSIFIER_BLOCK_OFFTOPIC=false`);
- `chitchat` → pass; `pii` → soft flag (audit/redact); otherwise pass.

The head runs numpy-only (no torch at serving) and ships as `sanitize/assets/head.npz`,
trained offline against the same embedding space (`scripts/train_sanitize_head.py`). It is
**fail-open**: a missing/untrained head, an embedding-model or dimension mismatch, or any
embedding error passes the prompt through, so the classifier can only add a block, never take
chat down. `/health` surfaces the live fail-open rate. Toggle and tune via `INPUT_CLASSIFIER_*`
env vars (enable, per-action block flags, per-label thresholds, embed timeout). Methodology:
`docs/input-sanitization-classifier.md`.

**Output — language + anti-XSS.** Legacy IAS answers pass through `sanitize_output_language`
(`security.py`): it drops leaked reasoning/meta-planning and tool-call echoes, strips CJK
(VI/EN only), then defangs active content (`neutralize_active_content`) — dangerous URL schemes
get a zero-width break, HTML tags are angle-escaped, event handlers are broken. This is
defense-in-depth; the frontend must still sanitize on render (DOMPurify, block `javascript:`
links). V3 multilingual translation uses separate literal/language validation and must not run
through the legacy VI/EN filter.

## Memory

Conversation memory is per-session, keyed by an opaque `session_id` / `project_id` or by a
bare `user_id` for the legacy `/ai/chat` path. The default store is durable SQLite
(`SESSION_STORE_BACKEND=sqlite`, `SESSION_DB_PATH`), built on Linch's session-store interface
(`src/secai_gateway/state/session_store.py`). Set `SESSION_STORE_BACKEND=redis` for the
Redis-backed 24h-TTL store, or leave both unset for an in-memory store (dev/test). Long chats
stay under the context window via Linch's proactive compaction (`CompactionLadder`,
keep-recent-10) behind a prompt-cacheable prefix.

## Knowledge base

- **qdrant_search** — hybrid dense (`sec-embedding`) + BM25 retrieval over `cve_kb_hybrid`,
  RRF-fused and reranked (`sec-rerank`). CVE/CWE/CAPEC/ATT&CK IDs in the query are auto-detected
  and pinned.
- **Knowledge chunks** (OWASP Cheat Sheets, ASVS 5.0, CWE/CAPEC/ATT&CK mitigations, OWASP
  Top 10 / CWE Top 25; `kind` = `cheatsheet` / `requirement` / `mitigation` / `taxonomy`) share
  the collection but are scoped: `qdrant_search` defaults to `scope="entity"` and never returns
  them; `scope="knowledge"` returns only them (minus chunks flagged `contains_attack_example`).
  Chat uses them only when `CHAT_KNOWLEDGE_GROUNDING_ENABLED=true` (default `false`): id-less
  concept / how-to-fix questions route to `concept_kb` with a cited reference block, and a
  "fix CVE-X" question also gets the mitigations of that CVE's CWEs. Ingest/verify/rollback:
  `secai/scripts/knowledge/`.
- **Chat pre-fetch for open-ended CVE questions** (`CHAT_CVE_SEARCH_PREFETCH_ENABLED`, default
  on): questions about a named vulnerability with no id are searched server-side; PoC questions
  get a de-duplicated catalog summary (and the real artifact on request); CWE/CAPEC/ATT&CK ids
  come with their official names.
- **CVE discovery + serving latency** (all default off): `CHAT_DISCOVERY_ENABLED` routes
  "which <product> flaw …?" questions to CVE search with vendor synonyms and a KEV-only pass
  (`retrieval/discovery.py`); `EMBEDDING_KEEPALIVE_ENABLED` reuses embedding/rerank connections;
  `EMBEDDING_CACHE_TTL_S` caches the vector of a prompt already embedded by the classifier;
  `CHAT_SEMANTIC_ROUTING_ENABLED` (requires the knowledge flag) lets the databases decide which
  one a question needs (thresholds `KNOWLEDGE_CONFIRM_MIN_RERANK`, `SEMANTIC_ROUTE_*`).
- **neo4j_search** — graph traversal (CVE ↔ CWE ↔ CAPEC ↔ Technique ↔ Tactic ↔ Product), plus
  product entry-points and count/statistics queries.

## PoC / Exploit-DB

The Exploit-DB research path (`/ai/exploit*`) runs in-process as a plain tool — no separate
service. The Exploit-DB clone is read-only; on its own it never executes artifacts, hits
targets, or runs Nmap, so `/ai/exploit` can only return an unexecuted `draft_not_executed`
candidate. Running something against a real target requires one of the gated engines
(`/ai/exploit/run`, `/ai/exploit-advance*`, `/ai/verify`, `/ai/tool`, `/ai/exec`), each
fail-closed behind its own enable flag and, for anything target-facing, an allowlist.

Section 1 of the response (the fetched PoC) is placed deterministically. During the agentic
turn the model sees each artifact capped to a context budget (`_MAX_CODE_CHARS` /
`_MAX_SET_CHARS` in `poc/backend.py`); the PoC rendered into the response is re-read from the
content store after the model runs and shown untruncated (repo tree plus one path-headed block
per file). Full source is only ever the real fetched artifact bytes for license-cleared
sources, gated by the same license and path-escape rules as `poc_fetch`.

## Showcase + KPI Studio

Two operator/demo surfaces are mounted same-origin (no CORS or separate deploy):

- **`/showcase-demo/`** — the acceptance ("nghiệm thu") dashboard: finetune dossier, model
  info, KB sync status, sanitize-classifier demo, source dossier browser, the 1000-prompt test
  set, deploy/Helm info, evidence files, and a retraining-automation tab. Backed by
  `/showcase-api/*` routes that read read-only from the official evidence dossier
  (`HO-SO-GOC-NGHIEM-THU-AI/`, bind-mounted — see [Deploy](#deploy)) rather than recomputing.
  Reference: `docs/nghiem-thu-showcase/`.
- **`/kpi-studio/`** — a standalone KPI benchmarking app (same `/showcase-api/kpi-*` backend)
  with two modes:
  - **Demo replay** (`/showcase-api/kpi-bench/*`) — replays the frozen official KPI run
    recorded in the dossier (19+1 KPIs with formulas/thresholds/pass-fail) at a simulated pace.
  - **Live run** (`/showcase-api/kpi-studio/*` + `/showcase-api/kpi-run/*`) — shells out to the
    real bench scripts against the live LLM/embedding/reranker/Qdrant backends, streams
    stdout/stderr over SSE, and reads back the result files. One live run is allowed gateway-wide
    at a time. Requires the `KPI-VA-GEN-TEST/` working tree mounted read-only.

## Deploy

This repository is the source for building the gateway image. To stand up the full backend —
gateway + Qdrant + Neo4j + Redis — use [`deploy/`](deploy/), a docker-compose stack that builds
this repo's `Dockerfile`, brings up the datastores on an internal `secai-net` network, and
points the gateway at the LLM/embedding/reranker endpoints over HTTP. See `deploy/README.md`
for up/down/health commands.

**Five-model serving.** `secai-serving-vast/` is a separate Git repository. The rental script
defaults to `DuyTa/Cyber-F1-AWQ`, `DuyTa/sec-embedding`, `DuyTa/sec-rerank`,
`Qwen/Qwen3-VL-2B-Instruct`, and specialist `tencent/Hy-MT2-7B` (Q4_K_M GGUF). Offline plans
(no rental):

```sh
# from secai-serving-vast/
./serving-up-cyber-f1.sh --dry-run --gpus 1
./serving-up-cyber-f1.sh --dry-run --gpus 2 --specialist-gpu 1
./serving-up-cyber-f1.sh --dry-run --gpus 4 --specialist-gpu 3
```

Supported hardware is A100 80GB, 1–4 GPUs. Each GPU runs one resident LLM/embedding/rerank
trio behind nginx. OCR and specialist MT share one selected GPU and load sequentially on
demand, reached over SSH on private loopback ports with separate tokens; the specialist GPU
needs ≥ 8192 MiB free. Startup validates the GPU count and authenticated resident inference per
replica and through nginx, then runs an OCR/MT smoke before reporting success. Existing-instance
upgrades use `serving-specialists-install.sh` and reuse the current environment/artifacts. Exact
flags: `secai-serving-vast/vast/gpu_specialists/README.md`.

**Full-feature Docker and Devspace.** [deploy/all-features/README.md](deploy/all-features/README.md)
covers the source-overlay image and the service-preserving rollout. Gateway, job workers, and
report backend keep their own entrypoints, private settings, and persistent mounts; use existing
deployment locks and verify ownership before changing a shared service.

Devspace frontend build (from `secai-fe-devspace/`):

```sh
npm ci
VITE_REPORT_FEATURE_ENABLED=true VITE_TRANSLATE_V2_FILES_ENABLED=true \
  VITE_TRANSLATE_V2_IMAGES_ENABLED=true npm run build
```

The deployed static proxy uses gateway 8131, report 18183, and translation 18185. Set
`REPORT_GATEWAY_PROXY` / `TRANSLATION_GATEWAY_PROXY` or local `.report-upstream` /
`.translation-upstream` files. The host Devspace runs on 5173; the translation frontend on 5179.
Rollback restores saved immutable images/profiles and frontend assets while keeping
report/job/review databases. Keep active `.env`, `.env.local`, `.env.pentest`, and deployment
env files; preserve example files; keys, rollback profiles, and data backups stay outside Git.

**External data prerequisites** (not in this repo; bind-mounted at deploy time):
- `HO-SO-GOC-NGHIEM-THU-AI/` — the official acceptance/evidence dossier; required by every
  `/showcase-api/*` route (`SHOWCASE_*_DIR`).
- Exploit-DB clone / PoC catalog + store (`EXPLOITDB_ROOT`, `POC_CATALOG_DB`,
  `POC_ARTIFACT_STORE`, `POC_SOURCES_ROOT`) — required by `/ai/exploit*`.
- `KPI-VA-GEN-TEST/` working tree + a BM25 index — required only by KPI Studio Live run.

A checkout of just this repo yields a working chat/translate/report/exploit-research gateway
against Qdrant + Neo4j + Redis; the showcase, KPI-Studio-live, and PoC-execution surfaces need
the external trees above present.

## Dev setup

```bash
# 1. install (editable) with uv — creates/uses .venv
VIRTUAL_ENV=.venv uv pip install -e '.[dev]'
#    or: python3 -m pip install -e '.[dev]'

# 2. configure — all settings are read from .env (pydantic-settings); see config.py for the
#    full list (LLM_PROVIDER, QDRANT_URL, NEO4J_*, REDIS_URL / SESSION_STORE_BACKEND,
#    EMBEDDING/RERANKER_API_URL, EXPLOITDB_ROOT, ALLOWED_ORIGINS, ADMIN_API_KEY,
#    INPUT_CLASSIFIER_*, AIRGAP, …)
cp .env.example .env   # or copy deploy/.env as a starting point

# 3. serve
uvicorn secai_gateway.app:app --host 127.0.0.1 --port 8080
```

Check it is up with `curl -s http://127.0.0.1:8080/health`. Run `pytest` for the test suite;
mock external services — some Bughunt/PoC tests require external fixtures.

## Layout

```
src/secai_gateway/
  config.py     # Settings (pydantic-settings) — all env in one place
  app.py        # create_app(): middleware + routes
  api/          # schemas · sse · messages · middleware · routes · projects · showcase/
  agent/        # profiles (from prompts/) · providers · factory · runner
  retrieval/    # qdrant · filters · bm25 · embedding · neo4j · cypher
  poc/          # service · models · tools · advance (multi-step exploit) · verify
  sanitize/     # input-classifier head + assets
  state/        # session_store (SQLite/Redis) · ledger · ratelimit
  prompts/      # profiles.yaml + base_system.md + per-profile .md
demo/
  showcase/     # static SPA for /showcase-demo/
  kpi-studio/   # static SPA for /kpi-studio/
```

System prompts live in `src/secai_gateway/prompts/` — edit the `.md` files (and `profiles.yaml`
for tools/turn caps) without touching Python. `data/` and `logs/` are local, outside Git;
`secai-serving-vast/` has its own Git repository.

## Docs

Full documentation is under [`docs/`](docs/); start with the [index](docs/README.md).
- `docs/api-guide.md`, `docs/poc-api-guide.md`, `docs/api-userguide-exploitDB.md`,
  `docs/session-project-api-guide.md` — API references.
- `docs/exploit-advance-*.md`, `docs/poc-verify-architecture.md` — live-exploit / verify design.
- `docs/input-sanitization-classifier.md`, `docs/model-classification-*.md` — input-classifier
  design.
- `docs/nghiem-thu-showcase/` — `/showcase-api/*` design + API reference (start at
  `00-TONG-QUAN.md`).
- `docs/nghiem-thu/` — acceptance/hand-over paperwork (start at `docs/nghiem-thu/INDEX.md`).
