# Stack package follow-ups Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the four gaps found after the real-data packtest of PR #65: BM25 needs HuggingFace, the package ships 8 GB of `.git` plus sources nothing reads at runtime, Neo4j is a 5-month-old dump, and the package carries no Docker images.

**Architecture:** All work stays in the gateway repo, branch `feat/stack-package` (worktree `/home/ict/AIEngines/secai/wt-stack-package`), and goes into PR #65. BM25 stopwords ship inside `src/`. One shared exclude list drives `pack.sh`, the packtest rsync and the packtest size estimate. Neo4j moves from `neo4j-admin dump` to a read-only paged HTTP export (`stack/neo4j-export.py`) into CSVs. `restore-data.sh` loads them with `neo4j-admin database import full`, applies the schema and checks counts. `pack.sh images` saves every image the `.env` points to, and `bootstrap.sh load-images` loads it. Proof is a rented-VM packtest.

**Tech Stack:** bash, Python 3 stdlib only for stack scripts (`urllib`, `csv`, `gzip`), pytest via `scripts/dev.sh test`, Docker / compose v2.40, neo4j:5.20-community (APOC present on prod, not required), fastembed 0.9.1.

**Spec:** Decisions agreed with the user on 2026-11-24 (grilling session), recorded here because no separate spec doc exists:
1. Target machine has Internet but HuggingFace/Docker Hub may be blocked. Data and models must work offline; images ship in the package.
2. Target is a read-only copy, refreshed by re-packing on the dev machine. The package drops `.git`, `trickest-cve`, `PoC-in-GitHub` and `_manual-untracked-backup-*`. It keeps `priority/` because 3 team-canonical PoCs point at `poc-sources/priority/`.
3. Neo4j in the package must be from the same day as the Qdrant snapshot. Export online by paged read-only queries to CSV, then `neo4j-admin import` on the target. Never stop, restart or reconfigure the prod Neo4j container.
4. BM25 reads its model dir from env `BM25_LOCAL_MODEL_DIR`. The 936-byte `english.txt` ships in the image.
5. `pack.sh images` saves every image the `.env` points to, and bootstrap loads them.
6. Fix the packtest size estimate.
7. Re-verify on a rented Vast VM (≤ $0.30/h, ≤ 3 h, destroy after and prove it is gone).
8. Everything goes into PR #65. No merge, no prod release, no GHCR push.

Facts measured on 2026-11-24:
- Prod Neo4j has 587,393 nodes, all single-label: CVE 412,189; Product 173,647; CWE 896; CAPEC 443; Technique 177; Tactic 41.
- It has 5,414,825 relationships: MAPS_TO_CAPEC 2,497,639; AFFECTS 1,584,504; EXPLOITS_TECHNIQUE 692,646; HAS_WEAKNESS 393,850; FOLLOWS_TACTIC 246,091; PART_OF 95.
- Node ids run 0..587,392. No relationship has properties.
- Properties are scalar only (string, float, boolean).
- There are 6 uniqueness constraints (e.g. `CVE.id`, `Product.name`) and only the default LOOKUP indexes otherwise.
- `UNWIND range(lo,hi) AS i MATCH (a) WHERE id(a)=i` seeks by id, which is fast.
- Prod autoscan writes to Neo4j around 03:00–04:45 local time.
- `neo4j-admin database import full` in 5.20 community has no `--schema` option, so constraints must be created after the import.

## Global Constraints

- Never `docker stop/rm/restart` a prod container. Never write to prod Qdrant or Neo4j. The only Neo4j access is read-only Cypher.
- Throwaway containers are named `*-smoke-<YYYYMMDD>`, `packtest-*` or `secai-restore-neo4j-<pid>`. They never reuse a prod name.
- Secrets (Neo4j password, Qdrant key, Vast key) never appear in argv, logs, committed files or test output. Pass them through env inherited by the child, `docker run -e VAR` without a value, or 0600 files.
- Tests run only through `scripts/dev.sh test <paths>` from the worktree.
- Commit trailer: `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- Stack scripts use Python 3 stdlib only and must run with `python3 -I`.
- No `/home/ict` in `stack/README.md` (`test_readme` enforces it).
- `bm25.py` behaviour on prod must not change. The bundled `english.txt` must be byte-identical (sha256 `649e2341238138974f7fc014ba2c3655dc334605136791a9d1918a41fca86143`).
- Rental: ≤ $0.30/h, ≤ 3 h, KVM "Ubuntu 22.04 VM" template as before. Destroy only the recorded test instance; prod instance 62088958 is already gone and stays denylisted.

## Review Focus

1. Prod Neo4j is written while the export runs (autoscan, ingest). The export must fail (exit 3, no `counts.json`), never ship a graph whose counts differ from before/after. Test is in Task 3.
2. A restore dies after `neo4j-admin import` but before schema and count verification. A rerun must refuse with a clear "incomplete previous restore" message instead of skipping because the store exists. Test is in Task 4.
3. String properties with comma, double quote, newline, unicode and the empty string `""` must round-trip exactly. A missing property must stay missing, not become `""`. Tests are in Task 3 (CSV encoding) and Task 4 (real round-trip on a seeded graph).
4. An image the `.env` names is missing locally, or is a `MISSING` placeholder. `pack.sh images` must fail before writing anything and list every bad ref. Test is in Task 5.
5. Secrets must not leak through argv or output in the exporter, restore and packtest. Tests are in Tasks 3, 4 and 6: the fake `docker`/`curl` argv logs and captured output must not contain the test password.

---

### Task 1: BM25 stopwords bundled in `src/`, dir overridable by env

**Files:**
- Create: `src/secai_gateway/retrieval/bm25_model/english.txt` (copy of `/home/ict/AIEngines/secai/secai-data/_state/bm25_local_model/english.txt`, byte-identical)
- Modify: `src/secai_gateway/config.py` (setting next to `bm25_model`, line ~70)
- Modify: `src/secai_gateway/retrieval/bm25.py:25-28,41-42`
- Test: `tests/test_bm25_local_model.py` (new)

**Interfaces:**
- Produces: `Settings.bm25_local_model_dir: str = ""` (env `BM25_LOCAL_MODEL_DIR`). Empty means the bundled dir `Path(bm25.__file__).parent / "bm25_model"`. Task 6 relies on the bundled dir working inside the gateway image with `--network none`.

- [ ] **Step 1: Write failing tests** in `tests/test_bm25_local_model.py`. Monkeypatch `fastembed.SparseTextEmbedding` with a recorder and reset `bm25._bm25_state["sparse_model"] = None` between tests.
  - `test_default_uses_bundled_dir`: with no env, `specific_model_path` equals the bundled dir, and that dir contains `english.txt` with the sha256 above.
  - `test_env_overrides_dir`: with `BM25_LOCAL_MODEL_DIR=<tmp dir containing english.txt>` (re-read settings the way other tests in `tests/` reset `get_settings`), `specific_model_path == str(tmp)`.
  - `test_missing_dir_falls_back_to_download`: with the env pointing at a non-existent dir, `specific_model_path` is not passed. This keeps today's behaviour.
- [ ] **Step 2:** `scripts/dev.sh test tests/test_bm25_local_model.py`. Expected: FAIL.
- [ ] **Step 3:** Add the setting. In `bm25.py` resolve the dir at load time: `_settings.bm25_local_model_dir or <bundled dir>`. Delete the `/home/ict/...` constant and keep the existing `os.path.isdir` check. Copy `english.txt`.
- [ ] **Step 4:** Run the tests. Expected: PASS. Also run `scripts/dev.sh test tests/ -k bm25` (existing BM25 tests stay green).
- [ ] **Step 5: Offline proof, no build needed.** Run `docker run --rm --network none -e HF_HUB_OFFLINE=1 -v "$PWD/src:/app/src:ro" --entrypoint python secai-agent-gateway:main-033b2aa3-20261123 -c "from secai_gateway.retrieval import bm25; m=bm25._load_sparse_model(); print('ok', len(list(m.query_embed(['apache struts rce']))[0].indices))"`. Expected: `ok <n>` with n > 0.
- [ ] **Step 6: Commit** `fix(bm25): stopwords BM25 đóng trong src, đổi thư mục bằng BM25_LOCAL_MODEL_DIR (chạy offline)`.

### Task 2: One exclude list for `poc-sources`

**Files:**
- Create: `stack/lib/poc-sources-excludes.sh`
- Modify: `stack/pack.sh` (the `tar -C "$src" -cf - poc-sources` line and MANIFEST)
- Modify: `stack/eval-vast.sh:603` (`du` estimate) and `:625` (rsync of `poc-sources`)
- Test: `tests/test_stack_scripts.py`, `tests/test_stack_packtest.py`

**Interfaces:**
- Produces: sourcing `stack/lib/poc-sources-excludes.sh` defines `POC_SOURCES_SKIP=(trickest-cve PoC-in-GitHub '_manual-untracked-backup-*')`, the top-level dirs under `poc-sources/`. `.git` is excluded at every depth by each consumer. Each consumer builds its own flags from this array; the array is the only place the names appear.

- [ ] **Step 1: Failing tests.**
  - `test_pack_data_excludes_poc_sources_skip`: fixture `poc-sources/` has `nuclei-templates/a.yaml`, `nuclei-templates/.git/HEAD`, `trickest-cve/x.md`, `PoC-in-GitHub/y.json`, `_manual-untracked-backup-2026-09-02/z`, `priority/Nginx-Rift/poc.py`. Listing `poc-sources.tar.zst` gives exactly `a.yaml` and `priority/Nginx-Rift/poc.py`.
  - `test_pack_manifest_lists_skipped_sources`: MANIFEST.md names the three skipped dirs and `.git`.
  - In `test_stack_packtest.py`: the recorded rsync argv for `poc-sources` carries `--exclude` for `.git` and each skip name. The `du` estimate (fixture sizes) excludes them, so a 1 MB `.git` plus a 1 MB `trickest-cve` do not count.
- [ ] **Step 2:** Run both files. Expected: the new tests FAIL.
- [ ] **Step 3:** Implement. `pack.sh` and `eval-vast.sh` source the lib via their own dir (`$REPO/stack/lib/...` and `$HERE/lib/...`).
- [ ] **Step 4:** Run both files. Expected: PASS.
- [ ] **Step 5: Commit** `feat(stack): gói bỏ .git, trickest-cve, PoC-in-GitHub, backup tay khỏi poc-sources (một danh sách dùng chung)`.

### Task 3: `stack/neo4j-export.py`, a read-only paged export to import CSVs

**Files:**
- Create: `stack/neo4j-export.py`
- Test: `tests/test_stack_neo4j_export.py` (fake Neo4j HTTP server: stdlib `http.server` in a thread that answers `/db/neo4j/tx/commit` by matching statement substrings)

**Interfaces:**
- Produces CLI: `python3 -I stack/neo4j-export.py [--node-page 20000] [--rel-page 5000] OUT_DIR`.
  - Env: `NEO4J_URL` (default `http://127.0.0.1:7474`), `NEO4J_USER` (default `neo4j`), `NEO4J_PASSWORD` (unset means no auth header), `NEO4J_DATABASE` (default `neo4j`).
  - `OUT_DIR` must not exist or must be empty.
  - Exit 0 on success; 2 on unsupported data (multi-label node, list/map property, label or type not matching `^[A-Za-z_][A-Za-z0-9_]*$`, conflicting property types other than int+float); 3 on count drift; 1 on HTTP/other errors. On any non-zero exit `counts.json` is not written.
- Produces files in `OUT_DIR` (Task 4 consumes exactly these names):
  - `nodes-<Label>.header.csv`: `:ID,<key>:<type>,...,:LABEL`, with types `string|long|double|boolean`, int+float giving `double`, keys sorted.
  - `nodes-<Label>.csv.gz`: no header. `:ID` = internal `id(n)`; `:LABEL` = the label.
  - `rels-<TYPE>.header.csv`: `:START_ID,:END_ID,:TYPE`, plus typed property columns if any exist.
  - `rels-<TYPE>.csv.gz`.
  - `schema.cypher`: one statement per line ending in `;`. Sources are `SHOW CONSTRAINTS YIELD createStatement`, plus `SHOW INDEXES YIELD createStatement, owningConstraint, type WHERE owningConstraint IS NULL AND type <> 'LOOKUP'`.
  - `counts.json`: `{"nodes": {Label: n}, "relationships": {TYPE: n}, "exported_at": "<UTC ISO>"}`.
- CSV encoding: a string is always quoted (`"` doubled), so `""` is the empty string. A missing property is an empty unquoted field. Booleans are written `true`/`false`. Floats use `repr`.
- Algorithm (the part the tests don't pin):
  1. Counts before: per label `MATCH (n) RETURN labels(n), count(*)`, per type `MATCH ()-[r]->() RETURN type(r), count(*)`.
  2. Keys per label `MATCH (n:L) UNWIND keys(n) AS k RETURN DISTINCT k` (same for types). Max id from `MATCH (n) RETURN max(id(n))`.
  3. Nodes: for each page `[lo, lo+node_page)` run `UNWIND range($lo,$hi) AS i MATCH (n) WHERE id(n)=i RETURN id(n), labels(n), properties(n)`, write rows to the label file and infer types as you go.
  4. Relationships: for each page of start ids run `UNWIND range($lo,$hi) AS i MATCH (a) WHERE id(a)=i MATCH (a)-[r]->(b) RETURN id(a), id(b), type(r), properties(r)`.
  5. Write headers last, because types are only known after streaming.
  6. Counts after must equal counts before and the exported row counts. Otherwise exit 3 and print the diff.
  7. Print progress to stderr: one line per 10 % of id range.

- [ ] **Step 1: Failing tests** (fake server holds a small graph: 2 labels, 2 rel types, an id gap, plus a CVE with `cvss_score` float, `is_in_kev` bool, `title` containing `a,"b"\nc`, `ü`, `""`, and one node missing `severity`):
  - `test_export_writes_import_files`: file set and headers are exact (`:ID,cvss_score:double,id:string,is_in_kev:boolean,severity:string,title:string,:LABEL`); `counts.json` equals the graph.
  - `test_csv_encoding_round_trip`: decompress, then `csv.reader` returns the exact original strings. The empty string is `""` and the missing property is an empty unquoted field (check the raw bytes).
  - `test_count_drift_exits_3`: the fake returns +1 CVE on the second count. Expected: exit 3, no `counts.json`, stderr names the CVE label.
  - `test_multilabel_and_list_property_exit_2` (parametrized).
  - `test_int_and_float_mix_is_double`.
  - `test_schema_cypher_from_show_constraints`: fake createStatements end up one per line with `;`; LOOKUP indexes are excluded.
  - `test_password_not_in_output`: with `NEO4J_PASSWORD=pw-SECRET-123`, the fake sees an `Authorization: Basic` header, and the string is absent from stdout, stderr and every output file.
  - `test_out_dir_not_empty_refused`.
- [ ] **Step 2:** `scripts/dev.sh test tests/test_stack_neo4j_export.py`. Expected: FAIL (no script).
- [ ] **Step 3:** Implement `stack/neo4j-export.py` (stdlib only, `urllib.request`, `gzip`, `csv`).
- [ ] **Step 4:** Run the tests. Expected: PASS.
- [ ] **Step 5: Commit** `feat(stack): neo4j-export.py xuất đồ thị online chỉ đọc theo trang ra CSV import, kiểm số đếm trước/sau`.

### Task 4: `pack.sh` and `restore-data.sh` switch from Neo4j dump to export/import

**Files:**
- Modify: `stack/pack.sh`.
  - Options: drop `--neo4j-dir`, `--neo4j-dump`, `NEO4J_STORE_DIR` and `NEO4J_IMAGE`. Add `--neo4j-url URL` (default `${NEO4J_URL:-http://127.0.0.1:7474}`, runs the exporter into `$out/neo4j`) and `--neo4j-export DIR` (copies a pre-made export; it must contain `counts.json`, `schema.cypher` and ≥1 `nodes-*.header.csv`).
  - `SHA256SUMS` covers every `neo4j/*` file.
  - MANIFEST shows node and relationship totals from `counts.json`.
  - Header comment says to avoid 03:00–05:00 (autoscan).
- Modify: `stack/restore-data.sh`.
  - Required files become `neo4j/counts.json` and `neo4j/schema.cypher`, plus whatever `nodes-*`/`rels-*` the package has. Each must have a SHA256SUMS line.
  - The Neo4j step is rewritten (below).
- Test: `tests/test_stack_scripts.py` (update existing pack/restore tests that used `neo4j.dump`; fake `docker` records argv and env-var names)

**Interfaces:**
- Consumes: Task 3 file names, and `counts.json` with keys `nodes` and `relationships`.
- Restore Neo4j step, run only when `$DATA_ROOT/_state/neo4j_data/databases/neo4j/neostore` is absent:
  1. Precheck at step 1, before touching `DATA_ROOT`: `NEO4J_PASSWORD` comes from env, else the last `NEO4J_PASSWORD=` line of `$(dirname "$0")/.env`. If it is empty, die.
  2. Write marker `_state/neo4j_data/.secai-restore-started`.
  3. Throwaway `docker run --rm --entrypoint bash -v data:/data -v "$PKG/neo4j:/import:ro" $NEO4J_IMAGE -c "neo4j-admin database import full neo4j --id-type=integer --multiline-fields=true --nodes=L=/import/nodes-L.header.csv,/import/nodes-L.csv.gz ... --relationships=T=... && chown -R 7474:7474 /data"`. Build the args from the file names.
  4. Start `secai-restore-neo4j-$$` with `-e NEO4J_AUTH` (value only in env), `/data` mounted and `schema.cypher` mounted read-only. A trap always removes it. Wait ≤ 300 s for `cypher-shell 'RETURN 1'`, with the password passed via `docker exec -e NEO4J_PASSWORD`. Apply `schema.cypher` with `cypher-shell -f`, then `CALL db.awaitIndexes(900)`.
  5. Count per label and per type and compare with `counts.json`. On a match print `==> neo4j: counts match (<N> nodes, <M> relationships)` and write `.secai-restore-complete`. On a mismatch, die with the diff.
  - If the store exists and `.secai-restore-started` exists without `.secai-restore-complete`, die with: `incomplete previous Neo4j restore: remove $DATA_ROOT/_state/neo4j_data and rerun`. If the store exists without markers (pre-existing install), skip as today.
  - Task 6 greps for `neo4j: counts match`.

- [ ] **Step 1: Failing tests** (fake `docker`; a fake `cypher-shell` output is supplied through the fake `docker exec`):
  - `test_pack_neo4j_export_dir_copied_and_summed`.
  - `test_pack_neo4j_url_runs_exporter`: the exporter is replaced through a PATH-injected stub or env override documented in the test. `NEO4J_PASSWORD` reaches it via env, never argv.
  - `test_pack_rejects_old_neo4j_flags`: `--neo4j-dump` gives the usage error.
  - `test_restore_builds_import_args_from_files`: the docker argv contains `--nodes=CVE=/import/nodes-CVE.header.csv,/import/nodes-CVE.csv.gz` and `--relationships=AFFECTS=...`.
  - `test_restore_counts_match_writes_complete_marker`.
  - `test_restore_counts_mismatch_dies`.
  - `test_restore_incomplete_marker_refuses_rerun`.
  - `test_restore_without_password_dies_before_touching_data_root`.
  - `test_restore_password_not_in_argv`: the fake docker argv log lacks `pw-SECRET-123`, and `NEO4J_AUTH` is passed as `-e NEO4J_AUTH` with no `=`.
  - Update or remove the existing tests that assert `neo4j.dump`.
- [ ] **Step 2:** `scripts/dev.sh test tests/test_stack_scripts.py`. Expected: the new tests FAIL.
- [ ] **Step 3:** Implement `pack.sh` and `restore-data.sh`.
- [ ] **Step 4:** Run `scripts/dev.sh test tests/test_stack_scripts.py tests/test_stack_neo4j_export.py`. Expected: PASS.
- [ ] **Step 5: Real round-trip on a seeded graph (local, throwaway).**
  - Start `neo4j-roundtrip-src-smoke-20261124` (neo4j:5.20-community, `NEO4J_AUTH` from a random env value, port bound to 127.0.0.1 only).
  - Seed it with the Review Focus #3 values, a uniqueness constraint and 3 labels.
  - Run `neo4j-export.py`, then `restore-data.sh` against a scratch `DATA_ROOT` under `local/stack-eval/roundtrip/` with a minimal package built by `pack.sh data --neo4j-export`. Use a minimal fixture `src_root` for the other trees.
  - Query the restored DB. Expected: every property is equal, including `""` and the missing property, and `SHOW CONSTRAINTS` lists the constraint.
  - Remove both containers and the scratch dir.
- [ ] **Step 6: Real prod-size round-trip (read-only on prod).**
  - Run `neo4j-export.py` against prod `http://127.0.0.1:7474`, outside 03:00–05:00, with the password read from the prod gateway `.env` into env only.
  - Import into a scratch `DATA_ROOT` via `restore-data.sh` on a package with only the neo4j part plus tiny fixtures. Record export seconds, import seconds, export size and the counts line.
  - Expected: counts match 587,393 / 5,414,825, or the current numbers if autoscan added some, and the exporter's before/after check passed.
  - Delete the scratch data afterwards and keep only the numbers for Task 7.
- [ ] **Step 7: Commit** `feat(stack): Neo4j trong gói = export CSV cùng ngày; restore import + schema + so số đếm, chặn restore dở`.

### Task 5: `pack.sh images` and `bootstrap.sh load-images`

**Files:**
- Modify: `stack/pack.sh` (new mode `images`)
- Modify: `stack/bootstrap.sh` (new subcommand `load-images`; header comment: `init`/`preflight` stay read-only, `load-images` only runs `docker load`)
- Test: `tests/test_stack_scripts.py` (pack images), `tests/test_stack_check.py` or the existing bootstrap test file (load-images)

**Interfaces:**
- `pack.sh images <env_file> <out_dir>`:
  - Image list is `docker compose --env-file <env_file> -f "$REPO/stack/compose.yml" config --images | sort -u`. The profiles are the env file's `COMPOSE_PROFILES`.
  - Fail before writing if any ref contains `MISSING` or `docker image inspect` fails. List all bad refs.
  - `$out_dir/images` must not exist.
  - Writes `$out_dir/images/images.tar.zst` (`docker save … | zstd -T0`), `images.txt` (`<ref> <sha256:id>` per line) and `SHA256SUMS`. Remove the partial dir on failure.
- `bootstrap.sh load-images <images_dir>`:
  - `sha256sum -c --strict` first.
  - If every ref in `images.txt` already exists with the same id, print `all images present` and skip.
  - Otherwise `zstd -dc images.tar.zst | docker load`, then re-check every ref and id. Exit non-zero naming any missing or mismatched ref.
- Task 6 consumes both commands exactly as above.

- [ ] **Step 1: Failing tests** with fake `docker` (`compose … config --images`, `image inspect`, `save`, `load`) and real `zstd`:
  - `test_pack_images_writes_tar_list_sums`.
  - `test_pack_images_missing_or_placeholder_fails_before_writing`: two bad refs are both listed, and `out/images` does not exist afterwards.
  - `test_pack_images_refuses_existing_out`.
  - `test_load_images_skips_when_present`: no `docker load` call.
  - `test_load_images_loads_and_verifies`.
  - `test_load_images_id_mismatch_after_load_fails`.
  - `test_load_images_bad_checksum_fails_before_load`.
- [ ] **Step 2:** Run. Expected: FAIL.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** Run the changed test files. Expected: PASS.
- [ ] **Step 5: Commit** `feat(stack): pack.sh images đóng mọi image .env trỏ tới; bootstrap.sh load-images nạp và kiểm id`.

### Task 6: Packtest uses the new pieces

**Files:**
- Modify: `stack/eval-vast.sh` (`cmd_packtest`, the remote pack and restore heredocs, and the header comment listing `PACKTEST_*` vars)
- Test: `tests/test_stack_packtest.py`

**Interfaces:**
- Consumes: Task 2 lib, Task 3 CLI, Task 4 `--neo4j-export` plus the `neo4j: counts match` line, Task 5 commands, and Task 1 bundled BM25.
- New or changed env:
  - `PACKTEST_NEO4J_URL` (default `http://127.0.0.1:7474`).
  - `PACKTEST_NEO4J_PASSWORD_FILE` (required, 0600, owned by the current user, same check as `PACKTEST_QDRANT_KEY_FILE`).
  - `PACKTEST_IMAGES_ENV` (optional env file for `pack.sh images`).
  - `PACKTEST_NEO4J_NODES` is removed.
  - `PACKTEST_VM_MIN_FREE_GB` default becomes 100.
- Flow changes:
  - **a.** The local precheck no longer needs `neo4j/neo4j.dump`.
  - **a2.** Before the bandwidth gate, run the exporter locally into `$PT/neo4j-export`. The password goes via env to that child only, and the exporter runs under `tmo`. If `PACKTEST_IMAGES_ENV` is set, run `pack.sh images "$PACKTEST_IMAGES_ENV" "$PT/images-pkg"`.
  - **b.** The size estimate is `du` of the trees with the Task 2 excludes, plus the export dir, plus `images-pkg`.
  - **c.** rsync `poc-sources` with the Task 2 excludes. rsync the export dir to `/srv/src-root/neo4j-export/` and `images-pkg/images/` to `/srv/images-pkg/images/`.
  - **e.** Remote pack uses `--neo4j-export "$SRC/neo4j-export"`.
  - **f.** Remote restore passes a random `NEO4J_PASSWORD` (`openssl rand -hex 16`, env only). Checks:
    - `neo4j_counts`: pass if `restore.log` contains `neo4j: counts match`, and the totals equal the export's `counts.json`. This replaces the `NEO4J_AUTH=none` count container.
    - `images_load`: `bootstrap.sh load-images /srv/images-pkg/images`.
    - `bm25_offline`: `docker run --rm --network none -e HF_HUB_OFFLINE=1 --entrypoint python <GATEWAY_IMAGE ref from the images env> -c "<load bm25._load_sparse_model, embed one query, print n>"`.
    - `images_load` and `bm25_offline` are `not_verified` when `PACKTEST_IMAGES_ENV` is unset, and become required checks for the verdict when it is set.
  - `facts.py` source/restored comparison stays. It now matches because the rsync and the pack use the same excludes.
- On a PACKTEST_IMAGES_ENV-enabled run, the provision step also needs `zstd` (already there) and Docker (already there).

- [ ] **Step 1: Failing tests** (extend the existing fake ssh/rsync harness in `test_stack_packtest.py`):
  - The exporter is invoked with the password in env only. Assert on the recorded argv and env names, and that `pw-SECRET-123` is absent from logs and argv.
  - A missing or loose-perm `PACKTEST_NEO4J_PASSWORD_FILE` makes it die before anything is created.
  - The rsync argv carries the excludes and the export/images dirs.
  - The remote pack command line has `--neo4j-export` and no `--neo4j-dump`.
  - Verdict: with `PACKTEST_IMAGES_ENV` set, a missing `bm25_offline` pass gives verdict fail. Without it, the check is `not_verified` and the verdict is unaffected.
  - The estimate includes the images and export sizes.
  - Update any existing test that references `neo4j.dump` or `PACKTEST_NEO4J_NODES`.
- [ ] **Step 2:** `scripts/dev.sh test tests/test_stack_packtest.py tests/test_stack_eval_vast.py`. Expected: the new tests FAIL.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** Run the same command. Expected: PASS. Then run the full stack suite: `scripts/dev.sh test tests/test_stack_*.py tests/test_bm25_local_model.py`. Expected: all PASS.
- [ ] **Step 5: Commit** `feat(stack): packtest export Neo4j cục bộ, mang image, kiểm BM25 offline và số đếm Neo4j; ước tính theo danh sách loại trừ`.

### Task 7: Docs, then a whole-branch review

**Files:**
- Modify: `stack/README.md`:
  - The data package section: what is excluded and why; Neo4j export/import; avoid 03:00–05:00.
  - `pack.sh images` and `bootstrap.sh load-images` in the install steps.
  - `BM25_LOCAL_MODEL_DIR`.
  - "Chưa kiểm": drop the items Task 8 proves; keep the rest.
- Modify: `stack/.env.example` (commented `BM25_LOCAL_MODEL_DIR=`).

- [ ] **Step 1:** Write the docs, including the Task 4 Step 6 numbers.
- [ ] **Step 2:** `scripts/dev.sh test tests/test_stack_*.py tests/test_bm25_local_model.py`. Expected: PASS (includes `test_readme`).
- [ ] **Step 3: Commit** `docs(stack): README gói không .git, Neo4j export/import, image, BM25 offline`.
- [ ] **Step 4:** Whole-branch review (fresh reviewer) of `git diff 3f082cb4..HEAD`. Fix every Critical/Important finding, then re-run Step 2.

### Task 8: Rented-VM packtest on real data, then report

**Files:**
- Create: `docs/archive/test-results/stack-package-followups-packtest-20261124.md` (force-add, since the path is gitignored)
- Local only (git-ignored): `local/stack-eval/images.env`, `local/stack-eval/neo4j.pw` (0600)

- [ ] **Step 1: Local prep.**
  - Build the gateway image from HEAD with `stack/pack.sh image` and note the tag.
  - Build the tunnel image from `stack/images/tunnel`.
  - Write `local/stack-eval/images.env` (copy of `eval.env`) with `COMPOSE_PROFILES=exploit,translation,report,review` and every `*_IMAGE` set to a local tag: the gateway just built, `secai-pentestcode-vm:056ed5db-20261123`, `secai-doc-convert-svc:1`, review-ui from `secai-review-ui-smoke:20261123` or a fresh build, and the tunnel just built.
  - Check that `pack.sh images` fails fast if any tag is missing.
  - Write `neo4j.pw` from the prod gateway `.env` (0600; never print it).
  - Confirm local free disk ≥ 60 GB and that the time is not 03:00–05:00.
- [ ] **Step 2: Rent.**
  - `EVAL_MODE=packtest EVAL_DISK_GB=200 stack/eval-vast.sh up` with the same offer filters as the previous packtest (KVM Ubuntu VM, ≤ $0.30/h, bandwidth floor via `PACKTEST_MIN_MBPS` if needed).
  - Then `PACKTEST_NEO4J_PASSWORD_FILE=… PACKTEST_IMAGES_ENV=… stack/eval-vast.sh packtest`, then `collect`, then `down`.
- [ ] **Step 3: Prove cleanup.**
  - `show instance <ID>` gives `instances: null`, and the account has 0 instances and 0 timers.
  - No orphan proxy or tunnel is left.
  - Prod Qdrant still has its two old snapshots (2026-08-23, 2026-09-04) and the same point count.
  - Prod Neo4j counts are unchanged except for autoscan growth.
- [ ] **Step 4: Report.** Write the results doc:
  - verdict and every check (pass, fail, not_verified)
  - package size by part, against the previous 13.7 GB
  - pack, export, rsync, restore and image load times
  - Neo4j counts
  - BM25 offline result
  - cost
  
  Update `stack/README.md` "Chưa kiểm". Run `scripts/dev.sh test tests/test_stack_*.py`. Commit `docs(stack): packtest lần 2 (gói gọn, Neo4j cùng ngày, image, BM25 offline)` and `git push origin feat/stack-package`.
- [ ] **Step 5:** Update memory `stack-package-pr65.md` with what is now proven and what is still unverified.
