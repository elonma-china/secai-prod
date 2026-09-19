# secai-agent-gateway

**IAS v2 security-AI gateway** — a private-network FastAPI service that gives the IAS
platform (ias.id.vn) LLM-backed security assistance: CVE/CWE chat, EN⇄VI security
translation, log/statistical report generation, and Exploit-DB/live-target PoC research —
plus a "nghiệm thu" (acceptance) showcase and a KPI benchmarking studio for operators.
It exposes IAS endpoints without normal API authentication; deploy it only behind a
VPN/private ingress or an IP-allowlisting reverse proxy. `X-API-Key` is required only for
the admin/raw-exec endpoints below.

## Architecture

```
FE / IAS platform
      │  HTTP (JSON or SSE)
      ▼
secai-agent-gateway (this repo, FastAPI/uvicorn, CPU-only, :8131)
      │                              │                        │
      ├─ LLM (chat completion)  ─────┤                        │
      ├─ embedding + reranker   ─────┤── HTTP, served on vast.ai GPU box(es)
      │                              │   (fine-tuned Cyber-F1 LLM + sec-embedding/
      │                              │    sec-rerank — see CLAUDE.md, do NOT deploy
      │                              │    the stock bge-m3/Qwen3-Reranker defaults)
      ├─ Qdrant  (hybrid dense+BM25 vector search, collection cve_kb_hybrid)
      ├─ Neo4j   (CVE↔CWE↔CAPEC↔Technique↔Tactic↔Product graph)
      ├─ Redis or SQLite (conversation/session memory — see "Memory" below)
      └─ Exploit-DB clone + PoC catalog/store (local disk, in-process — no separate service)
```

The gateway itself is a thin, stateless-ish CPU service: no GPU, no model weights in this
repo. Everything model-shaped (LLM, embedding, reranker) is called out over HTTP to serving
infrastructure on vast.ai; everything else the gateway needs to run (Qdrant, Neo4j, Redis)
is deployed alongside it. See [`deploy/`](deploy/) for the docker-compose stack that wires
all of this together (this folder is the *source*, not the deployment).

## Key endpoints

Every `POST` AI task has a plain JSON form and (mostly) an SSE `…/stream` form that streams
`tool_call` / `tool_result` / `token` events then one `final` event, ending with
`data: [DONE]`. Verified against `src/secai_gateway/api/routes.py` and
`src/secai_gateway/api/projects.py`.

**Chat / assistant**
- `POST /ai/chat` · `POST /chat_no_stream` · `POST /ai/chat/stream` — security assistant
  (CVE/CWE Q&A, remediation guidance); stateful per `user_id`.
- `POST /sessions/{sid}/chat` · `POST /sessions/{sid}/chat/stream` — same, scoped to a
  specific Project/Session (see below) instead of a bare `user_id`.

**Project & session management** (ChatGPT-style sidebar backing store — ownership must
match the caller's `user_id`)
- `POST /projects` · `GET /projects` · `GET /projects/{pid}` · `PATCH /projects/{pid}` ·
  `DELETE /projects/{pid}`
- `POST /projects/{pid}/sessions` · `GET /projects/{pid}/sessions`
- `POST /sessions` · `GET /sessions` · `GET /sessions/{sid}` · `PATCH /sessions/{sid}` ·
  `DELETE /sessions/{sid}` · `GET /sessions/{sid}/messages`

**Translate / report**
- `POST /ai/translate` · `POST /ai/translate/stream` — EN⇄VI security translation, with
  literal masking (accounts/passwords/etc. pass through verbatim, no 422) and chunking for
  long input.
- `POST /ai/generate-report` · `POST /ai/generate-report/stream` — turn a log/data blob
  into a Markdown report.
- `POST /ai/generate-statistical-report` · `…/stream` — period report as
  `report_title`/`summary`/`detail`.

**Exploit / PoC research** (Exploit-DB-grounded, read-only unless explicitly enabled)
- `POST /ai/exploit` · `POST /ai/exploit/stream` — agentic PoC research grounded in the
  Exploit-DB corpus; returns an unexecuted `draft_not_executed` candidate unless a live
  engine is enabled below.
- `POST /ai/exploit/run` — deterministic (non-LLM) PoC execution via the pocsuite3 engine.
  Off by default (`POC_EXPLOIT_ENABLED` + `POC_EXPLOIT_API_URL`), 404 otherwise.
- `GET  /ai/exploit-advance/fields` — form spec for the multi-step live-exploit request.
- `POST /ai/exploit-advance/stream` · `POST /ai/exploit-advance` · `POST
  /ai/exploit-advance/reply` — multi-step exploitation of one CVE against a real target,
  streaming `step`/`log`/`final`/`question` events (the `question` event is a
  human-in-the-loop pause — resume via `/reply`). Fail-closed: needs the pocsuite engine or
  `LANE1_ENABLED`, plus the target to be on the allowlist (`VERIFY_ALLOWED_TARGETS` /
  `VERIFY_ALLOW_ALL`).
- `POST /ai/verify` — verify-only PoC-against-target check via secai-kali-executor. Off by
  default (`VERIFY_ENABLED`), 404 otherwise.
- `POST /ai/bughunt/stream` — target-driven (not CVE-driven) ReAct recon/vuln-scan hunt +
  deterministic validation gate.
- `POST /ai/tool` — scoped pentest tool (nmap/searchsploit/gobuster/sqlmap/hydra) in the
  Kali executor. Off by default (`VERIFY_ENABLED`).
- `POST /ai/exec` — raw command execution in a privileged Kali container. Off by default
  (`EXEC_ENABLED`) **and** always requires `X-API-Key == ADMIN_API_KEY` even when on — the
  most dangerous surface in the gateway.

**Ops**
- `GET  /health` — LLM reachability, session backend (Redis/SQLite) status, searchsploit/
  Exploit-DB commit, and the input-classifier's live fail-open rate.
- `POST /admin/refresh-models` — needs `X-API-Key`.
- Interactive API docs at `/docs` (Swagger) and `/redoc`.

**Showcase & KPI Studio** — see the dedicated section below; routed under `/showcase-api/*`
plus two static SPAs mounted at `/showcase-demo/` and `/kpi-studio/`.

## Sanitization

Every request is filtered on the way **in** and every answer on the way **out**.

**Input — redaction gate.** `require_public_input` (`security.py`) rejects prompts carrying
secrets/PII (private keys, `sk_/AKIA/…` tokens, `password:`/`bearer …`, long card-like digit
runs) with `422` — only public or pre-redacted input reaches the model.

**Input — model-based classification** (`src/secai_gateway/sanitize/`). Before the agent runs,
each chat prompt is embedded with the deployed `sec-embedding` model and a tiny **numpy** head
assigns a combined taxonomy — intent `security | chitchat | offtopic` (softmax) plus independent
`injection` (prompt-injection/jailbreak) and `pii` sigmoids — then `gate.decide` acts:

- `injection` → **blocked** with a safe refusal (never reaches the LLM),
- `offtopic` → deterministic `<scope_policy>` refusal (configurable off — production currently
  runs with `INPUT_CLASSIFIER_BLOCK_OFFTOPIC=false` to avoid over-refusing meta/system
  questions),
- `chitchat` → pass (the LLM handles greetings), `pii` → soft flag (audit/redact), else **pass**.

The head is **mixed** (intent = 1-hidden-layer MLP, injection/pii = class-balanced logistic),
runs numpy-only (no torch at serving) and ships as `sanitize/assets/head.npz`. It is trained
offline against the same embedding space (`scripts/train_sanitize_head.py`, data
`evals/sanitize/dataset.jsonl` built by `scripts/build_sanitize_dataset.py`). **Fail-open by
construction**: a missing/untrained head, an `EMBEDDING_MODEL` mismatch, a dimension mismatch,
or any embedding error passes the prompt through — the classifier can only *add* a block, never
take chat down. `/health`'s `input_sanitize` field surfaces the live fail-open (`unknown`) rate
so a silently-disabled guard doesn't hide behind a green health check. Toggle and tune via
`INPUT_CLASSIFIER_*` env vars (enable, per-action block flags, per-label thresholds, embed
timeout).

On the held-out eval split every class scores F1 ≥ 0.98 (macro-F1 ≈ 0.986, injection recall
1.0). Full methodology and metrics: `sanitize/assets/metrics.json` and
`docs/input-sanitization-classifier.md` / `docs/input-classifier-improvement-plan.md`.

**Output — language + anti-XSS.** `sanitize_output_language` (`security.py`) runs on every
answer: it drops leaked reasoning/meta-planning and internal tool-call echoes, strips CJK
(VI/EN-only), then **defangs active content** (`neutralize_active_content`) so a frontend that
renders the answer cannot execute it — dangerous URL schemes get a zero-width break, HTML tags
are angle-escaped, event handlers are broken. This applies to chat, translate, report, exploit,
the SSE token stream and the injected PoC. It is **defense-in-depth**; the frontend must still
sanitize on render (DOMPurify, block `javascript:` links) as the primary fix.

## Memory

Conversation memory is per-session, keyed by an opaque `session_id`/`project_id` (see the
Project/Session endpoints above) or by a bare `user_id` for the legacy `/ai/chat` path.
Production defaults to a **durable SQLite** store (`SESSION_STORE_BACKEND=sqlite`,
`SESSION_DB_PATH`, survives container restarts) built on Linch's session-store interface
(`src/secai_gateway/state/session_store.py`); set `SESSION_STORE_BACKEND=redis` for the
older Redis-backed 24h-TTL behavior, or leave both unset for an in-memory store (dev/test).
Long chats stay under the context window via Linch's proactive compaction
(`CompactionLadder`, keep-recent-10) with a prompt-cacheable prefix.

## Knowledge base

- **qdrant_search** — hybrid dense (fine-tuned `sec-embedding`) + BM25 retrieval over the
  `cve_kb_hybrid` collection, RRF-fused and reranked (`sec-rerank`). CVE/CWE/CAPEC/ATT&CK
  IDs in the query are auto-detected and pinned.
- **neo4j_search** — graph traversal (CVE↔CWE↔CAPEC↔Technique↔Tactic↔Product), plus
  product entry-points and count/statistics queries.

## PoC / Exploit-DB

The Exploit-DB research path (`/ai/exploit*`) runs **in-process** as a plain tool — no
separate service. The Exploit-DB clone is read-only; by itself it never executes artifacts,
targets, mirrors, updates, or runs Nmap — `/ai/exploit` alone can only return an unexecuted
`draft_not_executed` candidate. Actually running something against a real target requires one
of the explicitly-gated engines above (`/ai/exploit/run`, `/ai/exploit-advance*`, `/ai/verify`,
`/ai/tool`, `/ai/exec`), each fail-closed behind its own enable flag and (for anything
target-facing) an allowlist.

**Section 1 (the fetched PoC) is placed deterministically and shown in full.** During its
agentic turn the model sees each fetched artifact capped to a context budget
(`_MAX_CODE_CHARS`/`_MAX_SET_CHARS` in `poc/backend.py`), so long chats stay under the window.
But the PoC rendered into the response's Section 1 — the `description`/`exploit_guide` text the
UI displays — is **re-read from the content store at injection time (after the model runs) and
shown untruncated, as the whole repo**: a repo tree plus one path-headed block per file, entry
first, including sibling files the fetch budget skipped. Full source is only ever the real
fetched artifact bytes for license-cleared sources (never model-invented), gated by the same
license + path-escape rules as `poc_fetch`.

## Showcase + KPI Studio

Two operator/demo surfaces are mounted same-origin so no CORS or separate deploy is needed:

- **`/showcase-demo/`** — the "nghiệm thu" (acceptance) dashboard: finetune dossier
  (dashboards/logs/checkpoints), model info, KB sync status, sanitize-classifier live demo,
  source dossier browser, the 1000-prompt test set, deploy/Helm info, evidence files, and a
  retraining-automation tab. All backed by `/showcase-api/*` routes that read **read-only**
  from the "hồ sơ gốc" (official evidence dossier, mounted from `HO-SO-GOC-NGHIEM-THU-AI/` —
  see "Deploy" below) rather than recomputing anything — the numbers shown must match what was
  handed to the customer. Full API reference: `docs/nghiem-thu-showcase/`.
- **`/kpi-studio/`** — a standalone KPI benchmarking app (same `/showcase-api/kpi-*` backend,
  own sidebar shell) with two modes:
  - **Demo replay** (`/showcase-api/kpi-bench/*`) — replays the frozen, official KPI run
    already recorded in the dossier (19+1 KPIs, formulas/thresholds/pass-fail), at a simulated
    pace, for a live-feeling walkthrough with zero cost/risk.
  - **Live run** (`/showcase-api/kpi-studio/*` catalog/dataset/service-status +
    `/showcase-api/kpi-run/*`) — actually shells out to the real bench scripts
    (`KPI-VA-GEN-TEST/02-GEN-TEST/bench_kpi.py` for groups B/C, `.../scripts/bench.sh` for
    group A) against the live LLM/embedding/reranker/Qdrant backends, streams raw
    stdout/stderr over SSE, and reads back the real result files. Only one live run is allowed
    gateway-wide at a time (it costs real inference money and writes to shared `runs/`
    output). Requires the `KPI-VA-GEN-TEST/` working tree mounted read-only — see "Deploy".

## Deploy

**This repo is the source** (development / building the gateway image). To stand up the full
backend — gateway + Qdrant + Neo4j + Redis, wired together — use the sibling
**[`deploy/`](deploy/)** directory (a docker-compose stack, moved here from the former sibling
`secai-docker/`). It builds this repo's `Dockerfile`, brings up the databases on an internal
`secai-net` network, and points the gateway at the LLM/embedding/reranker endpoints served on
vast.ai over plain HTTP (no GPU in this stack). See `deploy/README.md` for the up/down/health
commands.

**Model variant matters.** The embedding/reranker serving on vast.ai must be started with the
fine-tuned `DuyTa/sec-embedding` / `DuyTa/sec-rerank` variants, **not** the stock
`bge-m3`/`Qwen3-Reranker` defaults — a name mismatch fails embed/rerank calls with 404 (silent
to `/health`) and RAG quietly degrades to BM25-only. See this repo's `CLAUDE.md` for the exact
deploy flags and the post-deploy curl check.

**External data prerequisites — not in this repo, must be bind-mounted at deploy time:**
- **`HO-SO-GOC-NGHIEM-THU-AI/`** (the official acceptance/evidence dossier — finetune
  datasets/logs/dashboards, KPI results, build sources) — required for every `/showcase-api/*`
  route under "Showcase" above (`SHOWCASE_*_DIR` env vars). Mirrors an external Drive folder;
  see `docs/nghiem-thu/` for what's copied into this repo's docs vs. what stays external.
- **Exploit-DB clone / PoC catalog+store** (`EXPLOITDB_ROOT`, `POC_CATALOG_DB`,
  `POC_ARTIFACT_STORE`, `POC_SOURCES_ROOT`) — required for `/ai/exploit*`.
- **`KPI-VA-GEN-TEST/`** working tree + a BM25 index — required only for **KPI Studio Live
  run** (Demo replay and everything else works without it).

None of the above ship in this git repo; they're bind-mounted from the host (or another
volume) by `deploy/docker-compose.yml`. A checkout of just this repo gets you a working chat/
translate/report/exploit-research gateway against Qdrant+Neo4j+Redis, but the showcase/KPI-
Studio-live and PoC-execution surfaces need those external trees present.

## Dev setup

```bash
# 1. install (editable) with uv — creates/uses .venv
VIRTUAL_ENV=.venv uv pip install -e '.[dev]'
#    or standard pip:  python3 -m pip install -e '.[dev]'

# 2. configure — all settings are read from .env (pydantic-settings), see
#    src/secai_gateway/config.py for the full list (LLM_PROVIDER, QDRANT_URL, NEO4J_*,
#    REDIS_URL/SESSION_STORE_BACKEND, EMBEDDING/RERANKER_API_URL, EXPLOITDB_ROOT,
#    ALLOWED_ORIGINS, ADMIN_API_KEY, INPUT_CLASSIFIER_*, AIRGAP, …)
cp .env.example .env   # if present, else copy from deploy/.env as a starting point

# 3. serve
uvicorn secai_gateway.app:app --host 127.0.0.1 --port 8080
```

Check it's up: `curl -s http://127.0.0.1:8080/health`. Run the tests with `pytest` (hermetic —
no live services needed; ~54 test files under `tests/`).

## Layout

```
src/secai_gateway/
  config.py        # Settings (pydantic-settings) — all env in one place
  app.py            # create_app(): middleware + routes
  api/              # schemas · sse · messages · middleware · routes · projects · showcase/
  agent/            # profiles (from prompts/) · providers · factory · runner
  retrieval/        # qdrant · filters · bm25 · embedding · neo4j · cypher
  poc/              # service · models · tools · advance (multi-step exploit) · verify
  sanitize/          # input-classifier head + assets
  state/            # session_store (SQLite/Redis) · ledger · ratelimit
  prompts/          # profiles.yaml + base_system.md + per-profile .md
demo/
  showcase/         # static SPA for /showcase-demo/ (per-tab .js under tabs/)
  kpi-studio/       # static SPA for /kpi-studio/
```

System prompts live in `src/secai_gateway/prompts/` — edit the `.md` files (and
`profiles.yaml` for tools/turn caps) without touching Python.

## Docs

Full documentation lives under [`docs/`](docs/):
- `docs/api-guide.md`, `docs/poc-api-guide.md`, `docs/api-userguide-exploitDB.md`,
  `docs/session-project-api-guide.md` — API references.
- `docs/exploit-advance-*.md`, `docs/poc-verify-architecture.md` — live-exploit/verify design.
- `docs/input-sanitization-classifier.md`, `docs/input-classifier-improvement-plan.md`,
  `docs/model-classification-*.md` — the input-classifier design and its evolution.
- `docs/bao-cao-thong-minh-sua-template.md` — statistical-report backend design notes.
- `docs/nghiem-thu-showcase/` — the `/showcase-api/*` design + API reference (start at
  `00-TONG-QUAN.md`).
- `docs/nghiem-thu/` — consolidated acceptance/hand-over paperwork (workbooks, review
  reports); see `docs/nghiem-thu/INDEX.md`.
