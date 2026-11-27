# Stack GHCR release bundle Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn `stack/` into a complete release package: an operator on another machine downloads one script with `curl`, runs `./secai install`, and gets a pinned, version-matched stack (gateway + Qdrant + Neo4j + Redis + Elasticsearch + exploit VMs) pulled from public GHCR, with a separately versioned data snapshot. Upgrade, rollback and data update are one command each.

**Architecture:** All work stays in the gateway repo, branch `feat/stack-ghcr` (worktree `/home/ict/AIEngines/secai/wt-stack-ghcr`), one PR, not merged by this task. Three new pieces sit on top of the existing `stack/` scripts (`bootstrap.sh`, `pack.sh`, `restore-data.sh`, `check.sh`, `eval-vast.sh`), which are reused, not rewritten:

1. `stack/publish.sh` (publisher, dev machine only): gates (full pytest on `git archive <ref>`, FULL build, import app, `/app/src` == archive, smoke container `*-smoke-<date>` on 127.0.0.1) → builds gateway + pentestcode-vm from the archive → resolves base-image digests → writes `secai-stack.json` → builds a `FROM scratch` bundle image holding `stack/` + manifest → refuses to push unless every target package is public → pushes. `publish-data` runs `pack.sh data`, scans the whole package for secrets, and pushes a `FROM scratch` data image. `promote` moves the `stable` tag of the bundle.
2. `stack/secai` (operator CLI, the file fetched with `curl`): pulls the bundle by version (or `stable`), extracts it with `docker create`/`docker cp`, asks only the mandatory settings (secrets by hidden prompt or env, never argv), pulls every image by digest, pulls and restores the data image into a per-version data root, `compose up`, `check.sh all`. `upgrade` auto-rolls back when `check.sh all` fails. `data update` restores the new data next to the old one and switches `DATA_ROOT`; the old data root stays for `data rollback`.
3. `stack/lib/secret_scan.py`: stdlib scanner over directories, `.tar.zst`/`.tar`/`.gz`, nested tars and Qdrant snapshots, plus an optional Qdrant payload scroll. Prints rule + location only, never the value.

**Tech Stack:** bash, Python 3 stdlib (`python3 -I`), Docker 29 with containerd image store and legacy builder (no buildx), compose v2.40, GHCR (user `elonma-china`, repo `elonma-china/secai-prod` public), pytest via `scripts/dev.sh test`.

**Spec:** Decisions fixed with the owner on 2026-11-27 (not to be changed by this task):
1. Public GHCR packages under `ghcr.io/elonma-china/`: `secai-agent-gateway`, `secai-pentestcode-vm`, `secai-stack` (bundle), `secai-data`. Base images (qdrant, neo4j, redis, elasticsearch) are pulled from their origin registries, pinned by digest in the manifest.
2. Data snapshot = separate public package `secai-data:<YYYY.MM.DD>` (`FROM scratch`, the `pack.sh data` output), published on demand by one manual command. The whole package content is scanned for secrets before push (private key, `ghp_`/`gho_`/`github_pat_`, `hf_`, `sk-`, `AKIA`, `.env` files, `password=`/`api_key=`). Any finding stops the push; only path + rule name are printed. GHCR layer limit ~10 GB (Qdrant snapshot ~4.85 GB).
3. Release set = core (gateway, qdrant, neo4j, redis, es) + profile `exploit` (vm-a, vm-b). Profiles translation/report/review stay in compose but are not built, pushed or enabled by the CLI.
4. Manual publish on the dev machine with gates; nothing is pushed if a gate fails.
5. Stack version `YYYY.MM.DD-<sha8>`; every project image of one release carries the same tag. The manifest lists every image with its digest (base images too) and the default data version. Data has its own date version.
6. Operator fetches one file: `curl -fsSL https://raw.githubusercontent.com/elonma-china/secai-prod/main/stack/secai -o secai`. The target needs no git.
7. Moving prod on this host to the package is a later, separate step; this task only writes the migration + rollback plan.
8. Final test on a clean Vast VM (≤ $0.30/h, ≤ 3 h), destroy only the test instance and prove it is gone.

Owner instruction 2026-11-27 (answer to the "make packages public" question): packages are still private. Do all code, tests, docs and dry-runs; do NOT push real images/data and do NOT rent a VM until all four packages are public. Before every real push check `gh api '/users/elonma-china/packages?package_type=container'`; if not all public, stop there, open the PR and report the pending steps.

Facts measured on 2026-11-27:
- Placeholders `:init` (10 kB, FROM scratch) pushed for the four packages at 12:40Z; all four are private (anonymous token endpoint → 401).
- Docker 29.1.3, containerd snapshotter, no buildx. For a single-manifest image built locally, `.Id` equals the digest GHCR reports after push (verified on the four placeholders).
- Base images now (registry HEAD): `qdrant/qdrant:v1.10.1` index `sha256:6a34a799…`, `neo4j:5.20-community` `sha256:52d3dec8…`, `redis:7-alpine` moved to `sha256:7f7d30b7…` (local prod copy is older), `elasticsearch:8.15.3` `sha256:01c17320…`.
- Full pytest on an archive takes ~15 min (3315 passed on 2dcff27c). Gateway FULL build is cached on this host (~35 s); cold ~8 min. pentestcode-vm build 3–12 min, ~800 MB pushed.
- `scripts/release-prod.sh gates` cannot be reused: it only targets `origin/main`, needs prod running, and exits 0 without testing when prod already runs that sha. The publisher copies the gate bodies.
- Free disk `/` 73 GB. `pack.sh data` on prod Qdrant creates then deletes a ~4.9 GB snapshot; avoid 03:00–05:00 (autoscan writes Neo4j).
- Gateway contract for model stubs (from `src/`): streaming chat completions via the OpenAI SDK, judge JSON for the injection cascade, embeddings dim 1024 (`{"data":[{"embedding":[...]}]}`), rerank `{"results":[{"index","relevance_score"}]}`. The check.sh RAG query uses the exact-CVE fast path (Qdrant scroll on `cve_id`, no embedding/rerank). Static prompts contain "Log4Shell"/"10.0" but never "2.15.0"/"2.16.0", so an LLM stub must echo only the KB context block.

## Global Constraints

- Never `docker run/stop/rm/restart` a prod container; prod Qdrant/Neo4j are read-only. Throwaway containers: `secai-stack-gateway-smoke-<YYYYMMDD>`, `secai-restore-*-<pid>`, `secai-bundle-*`/`secai-data-extract-*`.
- Secrets (gh token, LLM/embedding/rerank keys, Neo4j password) never appear in argv, logs, committed files or test output. gh token → `docker login --password-stdin` with a temporary `DOCKER_CONFIG` removed by trap; registry calls pass the bearer through `curl -K -` on stdin.
- Push only through `stack/publish.sh`, only after the public check passes. No push from tests, no push in `--dry-run`.
- Tests run only through `scripts/dev.sh test <paths>`; new tests must pass in a `git archive` tree (no `.git`): use temp git repos, never the real checkout.
- Files owned by this task: `stack/**`, `tests/test_stack_*.py`, `AGENTS.md` (only the `stack/`/push lines), this plan, `docs/archive/test-results/stack-ghcr-*.md`. Do not touch `README.md`, `CLAUDE.md`, `docs/README.md`, `scripts/**`, `deploy/services.json`, `Dockerfile`, `src/`.
- Stack scripts: Python 3 stdlib only, `python3 -I`. No `/home/ict` in `stack/README.md` or `stack/compose.yml`.
- Commit trailer: `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- Rental: ≤ $0.30/h, ≤ 3 h via `stack/eval-vast.sh` (denylist, deadline timers); destroy only the recorded instance; no real keys on the VM (model stubs only).

## Review Focus

1. A gate fails, a package is private, the secret scan finds anything, or a pushed digest differs from the local `.Id`: nothing (more) is pushed. Tests in Task 4/5.
2. `upgrade` whose `check.sh all` fails must put the previous release back (files, `.env` image pins, running containers) and exit non-zero. `data update` failing must switch `DATA_ROOT` back and keep both data roots. Tests in Task 3.
3. Secrets typed at install never reach argv/`ps`/logs; `.env` stays 0600 through every rewrite. Tests in Task 3.
4. The bundle tag (`stable` or a version) is resolved to a digest once, and the extracted manifest's `version` must equal the requested version. Every image is pulled as `<ref>@<digest>`; `status` proves running containers use exactly those refs.
5. The scanner never prints a matched value, recurses into `.tar.zst`, nested tars and the Qdrant snapshot, and fails closed (exit 2) on any read/decompress error. Tests in Task 1.

---

### Task 1: Secret scanner `stack/lib/secret_scan.py`

**Files:** Create `stack/lib/secret_scan.py`, `tests/test_stack_secret_scan.py`.

- [ ] Rules: `private-key`, `github-token` (`gh[pousr]_`, `github_pat_`), `hf-token`, `sk-key`, `aws-access-key` (`AKIA`/`ASIA`), `env-file` (basename `.env` or `.env.*` except `.example/.sample/.template/.dist`), `password-assign` / `api-key-assign` (`password=`/`passwd=`/`api_key=`/`apikey=`/`api-key=`, case-insensitive, non-empty literal value; values starting with `$ % { < (` are expressions, not literals).
- [ ] Inputs: dirs (no symlink follow), files, `.tar.zst` (via `zstd -dcq`), `.tar`, `.tar.gz/.tgz`, `.gz`, nested tars (by name or `ustar` magic), Qdrant `*.snapshot` (tar). Chunked bytes scan with overlap; text → `path:line`, binary → `path@offset`; archive members as `outer!member`.
- [ ] Optional `--qdrant-url URL --qdrant-collection NAME`: scroll all payloads (no vectors), location `qdrant:<coll>#<id>`.
- [ ] `--allow FILE` (`<rule> <location-glob>` lines) counts allowed findings separately. Exit 0 clean, 1 findings, 2 read/decompress error. `--summary-json OUT`.
- [ ] Tests: every rule positive/negative, nested `.tar.zst` → tar → file, gz, binary offset, value never in stdout/stderr/summary, error → exit 2, allowlist.

### Task 2: Model stub `stack/acceptance/stubs/model_stub.py`

**Files:** Create `stack/acceptance/stubs/model_stub.py`, `tests/test_stack_model_stub.py`; update `stack/ACCEPTANCE.md` (stub section).

- [ ] One stdlib `ThreadingHTTPServer`: `GET /v1/models`, `POST /v1/chat/completions` (stream SSE and non-stream), `POST /v1/embeddings` (deterministic sha256 vectors, dim 1024, L2-normalised), `POST /v1/rerank` (word overlap score), `GET /health`. Bearer must equal `stub-test-key-not-secret`; wrong model name → 404 (so `check.sh models` still catches mis-wiring).
- [ ] Chat rules: injection judge → judge JSON; template report → section JSON; translate → word map on the `untrusted_retrieved_content` text keeping `⟦Ln⟧`; chat → echo only the `[Ngữ cảnh tra cứu từ KB nội bộ …]` block, never the system prompt.
- [ ] Tests: run the server on 127.0.0.1:0, drive with `urllib` + the `openai`-style SSE parser; echo never contains system-prompt markers.

### Task 3: Operator CLI `stack/secai`

**Files:** Create `stack/secai`, `tests/test_stack_secai.py`. Modify `stack/compose.yml` (`${QDRANT_IMAGE:-…}`, `${NEO4J_IMAGE:-…}`, `${REDIS_IMAGE:-…}`, `${ES_IMAGE:-…}`), `stack/.env.example` (those keys + `SECAI_VERSION`, `SECAI_DATA_VERSION`), `tests/test_stack_compose.py`, `tests/test_stack_check.py` (anchors).

Layout under `SECAI_HOME` (default `/opt/secai` as root, else `~/secai`): `stack/` (active bundle files + `.env` + `secrets/` + `tokenizer/`), `releases/<version>/` (pristine bundles, current + previous), `data/<data_version>/` (one `DATA_ROOT` per data version), `downloads/`, `state.json`, `env-backups/`.

- [ ] `install [VERSION]`: host checks (docker daemon, compose v2, zstd, sha256sum, python3, curl, `vm.max_map_count` (set it when root), free disk) → resolve bundle → activate → `.env` from `.env.example`, `COMPOSE_PROFILES=exploit`, `bootstrap.sh init`, prompt/env the mandatory keys, write image pins/`DATA_ROOT`/versions → pull images by digest → data fetch + restore (neo4j via `restore-data.sh`, Qdrant via a temporary `secai-restore-qdrant-<pid>` on `127.0.0.1:<random>`, point count == MANIFEST) → chown `poc` via the gateway image → `bootstrap.sh preflight` → `compose up -d` → wait healthy → `check.sh all` → `state.json`.
- [ ] `status [--json]`: versions, bundle digest, services, `/health` `git_sha` vs manifest, running `.Config.Image` vs pinned refs (`images_match`).
- [ ] `upgrade [VERSION]` (exit 0 ok, 1 nothing changed, 3 rolled back, 4 rollback failed), `rollback`, `data update [DATA_VERSION]`, `data rollback`, `check`, `logs [svc]`, `up`, `down` (never `-v`).
- [ ] Tests with a python fake `docker` (registry JSON: ref → digest + filesystem dir), fake bundle built from real `stack/` with recording fakes for `check.sh`/`restore-data.sh`: install happy path, secrets not in argv, `.env` 0600, version mismatch refused, upgrade + auto-rollback, rollback, data update + revert, lock.

### Task 4: Publisher `stack/publish.sh`

**Files:** Create `stack/publish.sh`, `tests/test_stack_publish.py`.

- [ ] `publish [--ref REF] [--data-version V] [--stable] [--dry-run]`: version `$(date +%Y.%m.%d)-<sha8>`, work dir `~/secai-releases/stack-<version>/` (0700), disk ≥ 45 GB, gates (pytest rc 0 + summary, FULL build with `GIT_SHA`/labels, import, `/app/src` == fresh `git archive <sha> src`, smoke `secai-stack-gateway-smoke-<YYYYMMDD>` mirroring release-prod L139-171), VM image from `git archive <sha> pentestcode stack/images/pentestcode-vm`, base digests by anonymous registry HEAD, manifest, bundle build after secret scan of the bundle tree. Push only after: not dry-run, all target packages public, login ok. Push gateway → vm → verify registry digest == local `.Id` → bundle. `NotFound: content digest` → seed the blob (`SECAI_SEED_BLOB`, read-only use of secai-serving's `docker/seed_blob.py`) and retry once.
- [ ] `publish-data [--version V] [--src-root D] [--mcp-dir D] [--from-pkg D] [--dry-run]`: refuse 03:00–05:00 and < 45 GB, `pack.sh data` (Neo4j password from env/0600 file), `secret_scan.py` on the whole package (+ Qdrant payload scroll), layer sizes < 9.5 GB, `FROM scratch` image (one COPY per group), push after the public check.
- [ ] `promote <version>`: pull bundle `<version>`, tag `stable`, push.
- [ ] Tests: temp git repo, fake python/docker/gh/curl; gate failure → no build/push; private package → no login/push; dry-run → no login/push; token only on stdin; scan finding → no push; digest mismatch → bundle not pushed.

### Task 5: VM test path

**Files:** Modify `stack/eval-vast.sh` (`EVAL_MODE=ghcr`: provision Docker only; subcommand `ghcrtest`), create `stack/acceptance/ghcr-vm-test.sh`, tests in `tests/test_stack_eval_vast.py`.

- [ ] `ghcr-vm-test.sh` runs on the VM as root: start `model_stub.py` on the docker0 address, `curl` `stack/secai` from raw GitHub at the branch SHA, `install V1` (stub keys via env), `status`, `check`, `acceptance/run-api.sh` (UI-POC-*, UI-TR-2/3 skipped with reasons), `upgrade V2`, `rollback`, `data update D2`, results + `SHA256SUMS` in `/opt/secai-stack/results` for `collect`.

### Task 6: Docs

- [ ] `stack/README.md`: new top section **"Người vận hành: 3 lệnh"** (Vietnamese), publisher section, update the "no push" sentence; keep existing detail below.
- [ ] `AGENTS.md`: the `stack/` line now allows GHCR publish only via `stack/publish.sh`.
- [ ] Prod migration + rollback plan (later step, not executed).
- [ ] `docs/archive/test-results/stack-ghcr-<date>.md`: what was published (tags/digests), VM run, costs, what is proven (pipeline, version match, data counts) and not (model quality).

### Task 7: Real release and VM test (only after all four packages are public)

- [ ] `publish-data` → `publish` (V1) → second commit → `publish` (V2) → data tag D2 → VM: `ghcrtest` → `collect` → `down` → prove gone → `promote` V1/V2 to `stable` after a passing VM run.
- [ ] Full suite `scripts/dev.sh test`, `bash -n`, `git diff --check`; push; PR (not draft, not merged).

## Prod migration plan (not executed in this task)

Written in `stack/README.md` → "Chuyển prod sang gói" once the package is stable: install the bundle side by side under a new compose project bound to 127.0.0.1 on another port, `data update` from the latest `secai-data`, compare `/health` and acceptance with prod, then switch the public port; rollback = stop the new project and restart the old `secai-docker` compose (its containers/images/volumes are untouched).
