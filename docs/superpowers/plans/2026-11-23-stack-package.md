# Gói triển khai `stack/` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Một thư mục `stack/` trong repo gateway dựng được toàn bộ SecAI (core, exploit-v2 2×12, dịch, báo cáo, review) trên máy mới chỉ từ `git clone` + gói dữ liệu + `.env`.

**Architecture:** Một `stack/compose.yml` (core luôn bật + profile `exploit`/`translation`/`report`/`review`), mọi service đọc chung một `.env`, nối nhau qua tên service trên bridge `secai-net`. exploit-v2 dùng image VM của PR #57 (2 container × 12 engine, mã nướng sẵn, không `docker.sock`). Script `bootstrap.sh` → `restore-data.sh` → `docker compose up` → `check.sh`.

**Tech Stack:** Docker Compose v2, bash, Python 3.12 (pytest, PyYAML, httpx), bun 1.3.14 (engine).

**Spec:** `docs/superpowers/specs/2026-11-23-stack-package-design.md`

## Global Constraints

- Làm trong worktree `/home/ict/AIEngines/secai/wt-stack-package`, nhánh `feat/stack-package`. Không commit lên `main`. Test chỉ qua `scripts/dev.sh test <file>`.
- **Không đụng prod**: không `up/down/restart/rm` project `secai-docker`, `secai-translation-release`, `secai-report-devspace`, `secai-review-release`; không đặt `container_name` trùng prod. Smoke dùng project `secai-stack-smoke`, tên `*-smoke-20261123`, cổng bind `127.0.0.1` hoặc IP LAN, không trùng 8131/6333/7474/7687/6379/18183/18185/18091.
- Kết quả `docker compose config` của `stack/compose.yml` không chứa `/home/ict`, `docker.sock`, `network_mode: host`, `host.docker.internal`.
- Image tag theo SHA: `GATEWAY_IMAGE=ghcr.io/elonma-china/secai-agent-gateway:main-<sha8>-<ngày>`, `VM_IMAGE=secai-pentestcode-vm:<sha8>-<ngày>`. Không `latest`.
- Tag DB cố định: `qdrant/qdrant:v1.10.1`, `neo4j:5.20-community`, `redis:7-alpine`, `docker.elastic.co/elasticsearch/elasticsearch:8.15.3`.
- Tên model request phải khớp serving; `.env.example` để trống tên model, `check.sh` fail nếu endpoint không phục vụ đúng tên (không cho rơi BM25-only).
- Bí mật chỉ trong `stack/.env` (mode 600, gitignored). `docker push` và smoke N=24 là bước **thủ công, chờ chủ dự án cho phép**.
- Đường dẫn dữ liệu trong container: `/srv/secai-data/{exploitdb,poc,poc-sources,exploitdb-mcp-server}`; mã engine tại `/opt/pentestcode`.
- Commit kết thúc bằng dòng `Co-Authored-By` theo cấu hình phiên.

## Review Focus

1. `.env` thiếu hoặc để trống endpoint/tên model → `check.sh` và `bootstrap.sh preflight` phải báo rõ biến nào thiếu, không để stack "xanh health" mà RAG hỏng (Task 6, 8).
2. Chạy `bootstrap.sh` lần hai → không được ghi đè secret/`credentials.json` đã có (Task 6).
3. Gói dữ liệu hỏng/thiếu file → `restore-data.sh` dừng trước khi nạp bất cứ thứ gì (kiểm `SHA256SUMS` trước) (Task 7).
4. Engine đặt mật khẩu nhưng gateway/healthcheck không gửi → mọi slot 401; và ngược lại mật khẩu rỗng phải chạy như cũ (Task 2).
5. Bật profile lẻ (chỉ `translation`, không `exploit`) → gateway vẫn khởi động, `IAS_SERVER_POOL` rỗng không làm crash (Task 3, 4).

---

### Task 1: Đưa image VM của PR #57 về `stack/images/pentestcode-vm`, build tái lập

**Files:**
- Cherry-pick: `3272fc8e`, `056ed5db`, `ec4dd404` (nhánh `feat/ev2-vm-containers`) lên `feat/stack-package`
- Move: `deploy/pentestcode-vm/{Dockerfile,supervisor.py,install-tools.sh,tools.txt}` → `stack/images/pentestcode-vm/`
- Delete: `deploy/pentestcode-vm/{compose.yml,vm.sh,firewall.sh,dev-gateway.override.yml}` và phần `DEV_COMPOSE_OVERRIDE` trong `scripts/dev.sh` do `ec4dd404` thêm
- Create: `stack/images/pentestcode-vm/build.sh`
- Modify: `pentestcode/.ias/ias.jsonc:108-110`, `src/secai_gateway/poc/exploit_v2_prompt.py:64,97`
- Test: `tests/test_stack_vm_image.py`

**Interfaces:**
- Produces: image `secai-pentestcode-vm:<sha8>-<ngày>`; engine nghe `0.0.0.0:<VM_PORT_BASE+i-1>`; env `VM_SLOTS`, `VM_PORT_BASE`, `VM_HOME_ROOT=/ias-pool-home`; `build.sh` in ra tag ở dòng cuối stdout.

- [ ] **Step 1: Cherry-pick 3 commit**, giải xung đột bằng cách giữ `main` cho mọi file ngoài `deploy/pentestcode-vm/`, `docs/archive/test-results/exploit-v2-vm-20261123.md` và thay đổi `IAS_SANDBOX_MODE=inline` trong `pentestcode/`. Run: `git log --oneline origin/main..HEAD` → đúng 3 commit + commit spec/plan.

- [ ] **Step 2: Viết test fail** `tests/test_stack_vm_image.py`:

```python
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
VM = ROOT / "stack/images/pentestcode-vm"

def test_dockerfile_self_contained():
    df = (VM / "Dockerfile").read_text()
    assert "FROM oven/bun:1.3.14" in df          # không còn FROM image cục bộ
    assert "secai-pentestcode:1.3.14-tools" not in df
    assert "/home/ict" not in df and "docker-cli" not in df
    assert "COPY pentestcode/ /opt/pentestcode/" in df
    assert "bun install --frozen-lockfile" in df

def test_no_host_paths_in_engine_config_and_prompt():
    for rel in ("pentestcode/.ias/ias.jsonc", "src/secai_gateway/poc/exploit_v2_prompt.py"):
        assert "/home/ict" not in (ROOT / rel).read_text(), rel

def test_build_script_tags_by_sha():
    sh = (VM / "build.sh").read_text()
    assert "git archive" in sh and "secai-pentestcode-vm:" in sh and "latest" not in sh
```

- [ ] **Step 3:** Run `scripts/dev.sh test tests/test_stack_vm_image.py` → FAIL (file chưa có).

- [ ] **Step 4: Gộp Dockerfile.** Lấy phần cài công cụ từ `/home/ict/AIEngines/secai/secai-docker/Dockerfile.pentestcode` (apt list, `NUCLEI_VERSION=v3.11.1`, `HTTPX_VERSION=v1.7.1`, searchsploit) làm stage đầu `FROM oven/bun:1.3.14`, bỏ `docker-cli`; nối tiếp các lệnh của Dockerfile VM. `VM_SRC=/opt/pentestcode`, `WORKDIR /opt/pentestcode`, `NUCLEI_TEMPLATES=/srv/secai-data/poc-sources/nuclei-templates`. Ghim pip transitive: tạo `stack/images/pentestcode-vm/pip-constraints.txt` từ `docker run --rm secai-pentestcode-vm:056ed5db-20261123 pip freeze` (image đã đo), và `install-tools.sh` cài mọi gói pip với `-c pip-constraints.txt`.

- [ ] **Step 5: Sửa đường dẫn.** `ias.jsonc`: `"/srv/secai-data/exploitdb-mcp-server/build/index.js"` và `DB_PATH` tương ứng. `exploit_v2_prompt.py`: hai chuỗi `/home/ict/AIEngines/secai/secai-data/poc-sources` → `/srv/secai-data/poc-sources`.

- [ ] **Step 6: `build.sh [sha]`**: `git archive <sha> pentestcode stack/images/pentestcode-vm | tar -x -C <tmp>`; `docker build --build-arg GIT_SHA=<sha> -t secai-pentestcode-vm:<sha8>-$(date +%Y%m%d)`; in tag. Không push.

- [ ] **Step 7:** Run test → PASS. Run `stack/images/pentestcode-vm/build.sh HEAD` → image tạo được; `docker run --rm <tag> sh -c 'nuclei -version && bun --version && ls /opt/pentestcode/packages/opencode'` chạy sạch. Ghi kích thước image.

- [ ] **Step 8:** Run `scripts/dev.sh test tests/` cho các test exploit_v2 hiện có (prompt đổi đường dẫn) → PASS. Commit `feat(stack): image VM pentestcode tái lập, không đường dẫn host`.

---

### Task 2: Mật khẩu engine

**Files:**
- Modify: `src/secai_gateway/config.py` (thêm setting), `src/secai_gateway/poc/ias_client.py:24-36`, nơi tạo `IasClient` trong `src/secai_gateway/poc/exploit_v2.py`, `stack/images/pentestcode-vm/supervisor.py` (`healthy`)
- Test: `tests/test_ias_client_auth.py`

**Interfaces:**
- Produces: `Settings.ias_server_password: str = ""` (env `IAS_SERVER_PASSWORD`); `IasClient(base_url, *, timeout_s=600.0, client=None, password: str = "")`. Engine nhận `OPENCODE_SERVER_PASSWORD` (Basic auth, user `opencode`).

- [ ] **Step 1: Test fail:**

```python
import base64, httpx, pytest
from secai_gateway.poc import ias_client
from secai_gateway.poc.ias_client import IasClient

_orig = httpx.AsyncClient

async def _auth_header_sent(monkeypatch, password):
    seen = {}
    def handler(req):
        seen["auth"] = req.headers.get("authorization")
        return httpx.Response(200, json={"id": "s1"})
    # IasClient tự tạo AsyncClient: chèn MockTransport, giữ nguyên mọi tham số nó truyền (kể cả auth).
    monkeypatch.setattr(ias_client.httpx, "AsyncClient",
                        lambda **kw: _orig(transport=httpx.MockTransport(handler), **kw))
    await IasClient("http://vm-a:5101", password=password).create_session()
    return seen["auth"]

@pytest.mark.asyncio
async def test_password_sent_as_basic_auth(monkeypatch):
    assert await _auth_header_sent(monkeypatch, "s3cret") == \
        "Basic " + base64.b64encode(b"opencode:s3cret").decode()

@pytest.mark.asyncio
async def test_empty_password_sends_no_auth(monkeypatch):
    assert await _auth_header_sent(monkeypatch, "") is None
```

- [ ] **Step 2:** Run `scripts/dev.sh test tests/test_ias_client_auth.py` → FAIL (`unexpected keyword 'password'`).
- [ ] **Step 3:** Thêm `password` vào `IasClient.__init__`; `_http()` truyền `auth=("opencode", password)` khi khác rỗng. Mọi chỗ tạo `IasClient` truyền `settings.ias_server_password`. `supervisor.py::healthy` gửi header Basic khi `OPENCODE_SERVER_PASSWORD` có giá trị.
- [ ] **Step 4:** Run test → PASS; chạy lại toàn bộ test có `ias_client`/`exploit_v2` → PASS.
- [ ] **Step 5:** Commit `feat(exploit-v2): mật khẩu engine (IAS_SERVER_PASSWORD), mặc định tắt`.

---

### Task 3: `stack/compose.yml` — core

**Files:**
- Create: `stack/compose.yml`, `stack/.gitignore` (`.env`, `secrets/`)
- Test: `tests/test_stack_compose.py`

**Interfaces:**
- Produces: services `gateway`, `qdrant`, `neo4j`, `redis`, `es` (không profile); network `secai-net`, `poc-labs-internal`; volumes `gw-data`, `redis-data`, `es-data`; biến `.env`: `GATEWAY_IMAGE`, `GATEWAY_HOST_PORT` (mặc định 8131), `GATEWAY_BIND` (mặc định `0.0.0.0`), `DATA_ROOT`, `COMPOSE_PROJECT_NAME` (mặc định `secai-stack`).
- Hàm test dùng chung: `load_compose() -> dict` (PyYAML đọc `stack/compose.yml`), `services(profile: str | None) -> dict`.

- [ ] **Step 1: Test fail:**

```python
import re, yaml
from pathlib import Path
COMPOSE = Path(__file__).resolve().parents[1] / "stack/compose.yml"

def load_compose(): return yaml.safe_load(COMPOSE.read_text())
def services(profile=None):
    return {n: s for n, s in load_compose()["services"].items()
            if (profile in s.get("profiles", [])) or (profile is None and "profiles" not in s)}

def test_core_services_and_pinned_images():
    core = services()
    assert set(core) == {"gateway", "qdrant", "neo4j", "redis", "es"}
    assert core["qdrant"]["image"] == "qdrant/qdrant:v1.10.1"
    assert core["neo4j"]["image"] == "neo4j:5.20-community"
    assert core["redis"]["image"] == "redis:7-alpine"
    assert core["es"]["image"].endswith("elasticsearch:8.15.3")
    assert core["gateway"]["image"] == "${GATEWAY_IMAGE:?set GATEWAY_IMAGE}"

def test_no_host_coupling_anywhere():
    text = COMPOSE.read_text()
    for bad in ("/home/ict", "docker.sock", "network_mode", "host.docker.internal", "container_name", ":latest"):
        assert bad not in text, bad

def test_gateway_wiring():
    env = services()["gateway"]["environment"]
    assert env["QDRANT_URL"] == "http://qdrant:6333"
    assert env["NEO4J_URI"] == "bolt://neo4j:7687"
    assert env["REDIS_URL"] == "redis://redis:6379"
    assert env["ELASTICSEARCH_URL"] == "http://es:9200"
    assert not any(k.startswith("SHOWCASE_") and "UPSTREAM" in k and v for k, v in env.items())

def test_only_gateway_and_report_publish_ports():
    for name, s in load_compose()["services"].items():
        if name not in {"gateway", "report-backend"}:
            assert "ports" not in s, name
```

- [ ] **Step 2:** Run `scripts/dev.sh test tests/test_stack_compose.py` → FAIL.
- [ ] **Step 3: Viết `stack/compose.yml` phần core.** Lấy healthcheck/ulimit/heap từ `/home/ict/AIEngines/secai/secai-docker/docker-compose.yml` (qdrant, neo4j, redis, es). Gateway: `command: ["uvicorn","secai_gateway.unified_app:app","--host","0.0.0.0","--port","8131"]`, `env_file: .env`, `environment` ghim như test + `GATEWAY_DB_PATH=/app/data/gateway.db`, `SESSION_STORE_BACKEND=sqlite`, `SESSION_DB_PATH=/app/data/sessions.db`, `EXPLOITDB_ROOT=/srv/exploitdb`, `POC_CATALOG_DB=/srv/poc/catalog.db`, `POC_ARTIFACT_STORE=/srv/poc-store`, `POC_SOURCES_ROOT=/srv/poc-sources`, `REWARD_SERVICE_URL=""`, `SHOWCASE_RETRAINING_UPSTREAM=""`, `SHOWCASE_TENSORBOARD_UPSTREAM=""`, `REVIEW_ENABLED=${REVIEW_ENABLED:-false}`. Volumes: `gw-data:/app/data`, `${DATA_ROOT}/exploitdb:/srv/exploitdb:ro`, `${DATA_ROOT}/poc:/srv/poc`, `${DATA_ROOT}/poc/store:/srv/poc-store`, `${DATA_ROOT}/poc-sources:/srv/poc-sources:ro`, qdrant `${DATA_ROOT}/_state/qdrant_storage:/qdrant/storage`, neo4j `${DATA_ROOT}/_state/neo4j_data:/data`. Ports: `"${GATEWAY_BIND:-0.0.0.0}:${GATEWAY_HOST_PORT:-8131}:8131"`. `depends_on` qdrant/neo4j/redis `service_healthy`. Network `poc-labs-internal`: `external: ${POC_LABS_EXTERNAL:-false}`, tên qua `POC_LABS_NETWORK`.
- [ ] **Step 4:** Run test → PASS. Run `GATEWAY_IMAGE=x DATA_ROOT=/tmp docker compose -f stack/compose.yml config -q` → exit 0.
- [ ] **Step 5:** Commit `feat(stack): compose core`.

---

### Task 4: Profile `exploit` (2×12) và chuỗi pool

**Files:**
- Modify: `stack/compose.yml`
- Create: `stack/lib/pool.sh`
- Test: `tests/test_stack_compose.py` (thêm), `tests/test_stack_scripts.py` (mới)

**Interfaces:**
- Produces: services `vm-a` (`VM_PORT_BASE=5101`), `vm-b` (`5201`), profile `exploit`; volumes `pool-home-a`, `pool-home-b` mount vào VM tại `/ias-pool-home` và vào gateway tại `/ias-pool-home-a`, `/ias-pool-home-b`. `stack/lib/pool.sh <slots>` in ra `IAS_SERVER_POOL` dạng `http://vm-a:5101|/ias-pool-home-a/slot1,...` (2×slots mục, không dấu phẩy cuối).

- [ ] **Step 1: Test fail** (thêm vào `test_stack_compose.py`):

```python
def test_exploit_profile_two_vms_hardened():
    vms = services("exploit")
    assert set(vms) == {"vm-a", "vm-b"}
    for name, port in (("vm-a", "5101"), ("vm-b", "5201")):
        s = vms[name]
        assert s["image"] == "${VM_IMAGE:?set VM_IMAGE}"
        assert s["cap_drop"] == ["ALL"]
        assert "no-new-privileges:true" in s["security_opt"]
        assert s["environment"]["VM_PORT_BASE"] == port
        assert s["environment"]["VM_SLOTS"] == "${VM_SLOTS:-12}"
        assert s["environment"]["OPENCODE_SERVER_PASSWORD"] == "${IAS_SERVER_PASSWORD:-}"
        assert set(s["networks"]) == {"secai-net", "poc-labs-internal"}
        assert all(":ro" in v or v.startswith("pool-home-") for v in s["volumes"])
    gw = services()["gateway"]
    assert any(v.startswith("pool-home-a:/ias-pool-home-a") for v in gw["volumes"])
```

`tests/test_stack_scripts.py`:

```python
import subprocess
from pathlib import Path
STACK = Path(__file__).resolve().parents[1] / "stack"

def run(*a, **kw): return subprocess.run(a, text=True, capture_output=True, **kw)

def test_pool_string_24_slots():
    out = run("bash", str(STACK / "lib/pool.sh"), "12").stdout.strip()
    items = out.split(",")
    assert len(items) == 24
    assert items[0] == "http://vm-a:5101|/ias-pool-home-a/slot1"
    assert items[11] == "http://vm-a:5112|/ias-pool-home-a/slot12"
    assert items[12] == "http://vm-b:5201|/ias-pool-home-b/slot1"
    assert items[23] == "http://vm-b:5212|/ias-pool-home-b/slot12"
```

- [ ] **Step 2:** Run cả hai file test → FAIL.
- [ ] **Step 3:** Thêm `x-vm` anchor vào compose (cap_add `[CHOWN, DAC_OVERRIDE, FOWNER, SETUID, SETGID, KILL]`, `mem_limit: ${VM_MEM_LIMIT:-48g}`, `pids_limit: ${VM_PIDS_LIMIT:-16384}`, env `ORCA_LOCAL_*` từ `QWEN_*` như compose PR #57, healthcheck `curl -fsS -u opencode:$$OPENCODE_SERVER_PASSWORD http://127.0.0.1:<port>/global/health`, mount `${DATA_ROOT}/{exploitdb,poc,poc-sources,exploitdb-mcp-server}:/srv/secai-data/...:ro`). Gateway nhận `IAS_SERVER_POOL: ${IAS_SERVER_POOL:-}` và `IAS_SERVER_PASSWORD`; **không** `depends_on` VM (Review Focus 5).
- [ ] **Step 4:** Viết `stack/lib/pool.sh` (vòng lặp như `vm.sh pool` cũ, host = tên service).
- [ ] **Step 5:** Run test → PASS; `docker compose --profile exploit config -q` → exit 0. Kiểm `ias_pool._parse_pool("")` với pool rỗng không raise: thêm test `test_empty_pool_does_not_crash` gọi `pool_size("", "")` nếu chưa có test tương đương.
- [ ] **Step 6:** Commit `feat(stack): profile exploit 2x12 trên bridge`.

---

### Task 5: Profile `translation`, `report`, `review` và hai image phụ

**Files:**
- Modify: `stack/compose.yml`
- Create: `stack/images/converter/{Dockerfile,server.py,convert.sh}`, `stack/images/review-ui/{Dockerfile,static/…}`
- Test: `tests/test_stack_compose.py` (thêm)

**Interfaces:**
- Produces: services `translation-gateway` (`uvicorn secai_gateway.multilang_app:app --port 18185`), `translation-worker` (`python -m secai_gateway.translation.worker`), `translation-converter` (:18091), `report-backend` (`uvicorn secai_gateway.app:app --port 8131`, publish `${REPORT_BIND:-127.0.0.1}:${REPORT_HOST_PORT:-18183}:8131`), `review-frontend` (`python /app/review-ui/serve_static.py`). Volumes `translation-data`, `report-data`. Biến `.env`: `CONVERTER_IMAGE`, `REVIEW_UI_IMAGE`, `TRANSLATE_V2_OCR_BASE_URL`, `TRANSLATE_V2_SPECIALIST_BASE_URL`, `MULTILANG_SPECIALIST_BASE_URL`, `TOKENIZER_DIR`.

- [ ] **Step 1: Lấy nguồn không có trong git (chỉ đọc, không dừng container):** `docker cp secai-translation-converter:/opt/server.py stack/images/converter/server.py`; `docker cp secai-review-frontend:/app/review-ui stack/images/review-ui/static`. Đọc cả hai, xác nhận không chứa token/khoá (grep `token|key|secret` → chỉ tên biến env). Nếu có giá trị bí mật: dừng, báo chủ dự án.
- [ ] **Step 2: Test fail:**

```python
def test_translation_profile_on_bridge():
    t = services("translation")
    assert set(t) == {"translation-gateway", "translation-worker", "translation-converter"}
    for n in ("translation-gateway", "translation-worker"):
        assert t[n]["image"] == "${GATEWAY_IMAGE:?set GATEWAY_IMAGE}"
        e = t[n]["environment"]
        assert e["TRANSLATE_V2_CONVERTER_URL"] == "http://translation-converter:18091"
        assert e["QDRANT_URL"] == "http://qdrant:6333"
        assert "QWEN_BASE_URL" not in e and "EMBEDDING_API_URL" not in e   # chỉ từ .env chung
    assert services()["gateway"]["environment"]["TRANSLATION_UPSTREAM_URL"] == "${TRANSLATION_UPSTREAM_URL:-}"

def test_report_and_review_profiles():
    r = services("report")["report-backend"]
    assert r["environment"]["REPORT_FEATURE_ENABLED"] == "true"
    assert r["environment"]["REPORT_DB_PATH"] == "/srv/reports/reports.db"
    assert "report-data:/srv/reports" in r["volumes"]
    v = services("review")["review-frontend"]
    assert v["environment"]["VITE_GATEWAY_PROXY"] == "http://gateway:8131"
```

- [ ] **Step 3:** Run → FAIL.
- [ ] **Step 4:** Viết ba profile. Env dịch: chép **tên và giá trị không bí mật** từ `/home/ict/secai-releases/main-033b2aa3-20261123/translation-main.json` (các `MULTILANG_*`, `TRANSLATE_V2_*` giới hạn/concurrency), bỏ mọi `SHOWCASE_*`, bỏ endpoint model (đến từ `env_file: .env`), URL nội bộ theo tên service; giá trị bí mật (`*_API_KEY`, `*_TOKEN`) thành `${VAR}` đọc từ `.env`. Healthcheck dịch sửa cho đúng cổng 18185 (bản prod đang kiểm nhầm 8131). `.env` đặt `TRANSLATION_UPSTREAM_URL=http://translation-gateway:18185` khi bật profile.
- [ ] **Step 5:** Converter Dockerfile: nền `ubuntu:24.04@sha256:a853f94d…` như `deploy/translate-v2/Dockerfile.converter` + python venv tại `/opt/v` + `server.py`; `CONVERTER_HOST=0.0.0.0`, `CONVERTER_PORT=18091`, `USER 65534`. Review-ui Dockerfile: `FROM python:3.12-slim`, `COPY static/ /app/review-ui/`, `USER 65534`.
- [ ] **Step 6:** Run test → PASS; `docker compose --profile translation --profile report --profile review config -q` → exit 0; build hai image phụ, mỗi cái `docker run --rm` khởi động và trả HTTP ở cổng của nó.
- [ ] **Step 7:** Commit `feat(stack): profile dịch, báo cáo, review`.

---

### Task 6: `.env.example` và `bootstrap.sh`

**Files:**
- Create: `stack/.env.example`, `stack/bootstrap.sh`
- Test: `tests/test_stack_scripts.py` (thêm)

**Interfaces:**
- Produces: `bootstrap.sh init` (tạo `.env` từ `.env.example` nếu chưa có, sinh secret, sinh `secrets/review-credentials.json`, tính `IAS_SERVER_POOL` bằng `lib/pool.sh`), `bootstrap.sh preflight` (exit ≠ 0 + in tên biến thiếu). Biến `STACK_DIR` để test trỏ thư mục tạm.
- Secret tự sinh (32 byte hex): `NEO4J_PASSWORD`, `ADMIN_API_KEY`, `LOGS_UI_PASSWORD`, `LOGS_UI_SESSION_SECRET`, `REPORT_API_KEY`, `IAS_SERVER_PASSWORD`, `TRANSLATE_V2_CONVERTER_TOKEN`. Nhập tay (preflight bắt buộc): `QWEN_BASE_URL`, `QWEN_API_KEY`, `QWEN_MODEL`, `EMBEDDING_API_URL`, `EMBEDDING_MODEL`, `RERANKER_API_URL`, `RERANKER_MODEL`, `GATEWAY_IMAGE`, `DATA_ROOT`; thêm `VM_IMAGE` nếu `COMPOSE_PROFILES` chứa `exploit`; thêm `TRANSLATE_V2_SPECIALIST_BASE_URL` nếu chứa `translation`.

- [ ] **Step 1: Test fail:**

```python
import os, shutil, stat

def _stack(tmp_path):
    d = tmp_path / "stack"; shutil.copytree(STACK, d, ignore=shutil.ignore_patterns(".env", "secrets"))
    return d

def test_init_generates_secrets_once(tmp_path):
    d = _stack(tmp_path); env = {**os.environ, "STACK_DIR": str(d)}
    assert run("bash", str(d / "bootstrap.sh"), "init", env=env).returncode == 0
    first = (d / ".env").read_text()
    assert stat.S_IMODE((d / ".env").stat().st_mode) == 0o600
    for k in ("NEO4J_PASSWORD", "ADMIN_API_KEY", "IAS_SERVER_PASSWORD", "REPORT_API_KEY"):
        v = [l for l in first.splitlines() if l.startswith(k + "=")][0].split("=", 1)[1]
        assert len(v) == 64, k
    assert (d / "secrets/review-credentials.json").exists()
    assert run("bash", str(d / "bootstrap.sh"), "init", env=env).returncode == 0
    assert (d / ".env").read_text() == first            # lần 2 không ghi đè

def test_preflight_names_missing_vars(tmp_path):
    d = _stack(tmp_path); env = {**os.environ, "STACK_DIR": str(d)}
    run("bash", str(d / "bootstrap.sh"), "init", env=env)
    r = run("bash", str(d / "bootstrap.sh"), "preflight", env=env)
    assert r.returncode != 0
    for k in ("QWEN_BASE_URL", "EMBEDDING_MODEL", "RERANKER_MODEL", "GATEWAY_IMAGE", "DATA_ROOT"):
        assert k in r.stderr

def test_env_example_has_no_secret_values():
    for line in (STACK / ".env.example").read_text().splitlines():
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            if any(t in k for t in ("KEY", "PASSWORD", "SECRET", "TOKEN")):
                assert v == "", k
```

- [ ] **Step 2:** Run → FAIL.
- [ ] **Step 3:** Viết `.env.example` theo 5 khối có tiêu đề (`# ── ENDPOINTS`, `SECRETS`, `PATHS & IMAGES`, `POOL`, `TRANSLATION`), mỗi biến một dòng chú thích. Mặc định: `COMPOSE_PROFILES=exploit,translation,report,review`, `VM_SLOTS=12`, `QDRANT_COLLECTION=cve_kb_hybrid`, `AIRGAP=true`, `VERIFY_ENABLED=false`, `EXEC_ENABLED=false`, `RATE_LIMIT_ENABLED=false`, `QWEN_CONTEXT_WINDOW=131072`.
- [ ] **Step 4:** Viết `bootstrap.sh`. Định dạng `credentials.json`: đọc `src/secai_gateway/api/review_auth.py` để sinh đúng schema (hash SHA-256 của token, `actor_id`, `role`); token thô in **một lần** ra stdout, không lưu.
- [ ] **Step 5:** Run test → PASS. Commit `feat(stack): .env.example và bootstrap`.

---

### Task 7: `pack.sh` và `restore-data.sh`

**Files:**
- Create: `stack/pack.sh`, `stack/restore-data.sh`
- Test: `tests/test_stack_scripts.py` (thêm; `docker` thay bằng stub trên PATH như `tests/test_wt_prune.py`)

**Interfaces:**
- `pack.sh image [sha]` → build gateway từ `git archive`, tag `ghcr.io/elonma-china/secai-agent-gateway:main-<sha8>-<ngày>`, in tag; **không push** (in lệnh `docker push` để chạy tay).
- `pack.sh data <src_root> <out_dir>` → `out_dir/{qdrant/cve_kb_hybrid.snapshot, neo4j/neo4j.dump, exploitdb.tar.zst, poc.tar.zst, poc-sources.tar.zst, exploitdb-mcp-server.tar.zst, MANIFEST.md, SHA256SUMS}`. Snapshot Qdrant lấy qua API `POST /collections/cve_kb_hybrid/snapshots` (không copy `_state`); dump Neo4j bằng `neo4j-admin database dump` trên **bản sao** store hoặc container tạm chỉ đọc, không dừng `secai-docker-neo4j`.
- `restore-data.sh <pkg_dir>` → kiểm `sha256sum -c SHA256SUMS` **trước**; rồi giải nén vào `$DATA_ROOT`, nạp dump Neo4j (bỏ qua nếu store đã có), khôi phục snapshot Qdrant. Idempotent.

- [ ] **Step 1: Test fail:**

```python
def test_restore_refuses_corrupt_package(tmp_path):
    pkg = tmp_path / "pkg"; pkg.mkdir(); (pkg / "poc.tar.zst").write_bytes(b"x")
    (pkg / "SHA256SUMS").write_text("0" * 64 + "  poc.tar.zst\n")
    data = tmp_path / "data"
    r = run("bash", str(STACK / "restore-data.sh"), str(pkg), env={**os.environ, "DATA_ROOT": str(data)})
    assert r.returncode != 0 and "SHA256" in r.stderr
    assert not data.exists() or not any(data.iterdir())      # chưa nạp gì

def test_restore_requires_manifest_files(tmp_path):
    pkg = tmp_path / "pkg"; pkg.mkdir(); (pkg / "SHA256SUMS").write_text("")
    r = run("bash", str(STACK / "restore-data.sh"), str(pkg), env={**os.environ, "DATA_ROOT": str(tmp_path / "d")})
    assert r.returncode != 0 and "qdrant" in r.stderr

def test_pack_image_never_pushes(tmp_path):
    binp = tmp_path / "bin"; binp.mkdir(); log = tmp_path / "docker.log"
    (binp / "docker").write_text(f'#!/bin/sh\necho "$@" >> {log}\n'); (binp / "docker").chmod(0o755)
    r = run("bash", str(STACK / "pack.sh"), "image", "HEAD", env={**os.environ, "PATH": f"{binp}:{os.environ['PATH']}"})
    assert r.returncode == 0
    calls = log.read_text()
    assert "build" in calls and "push" not in calls
    assert "ghcr.io/elonma-china/secai-agent-gateway:main-" in r.stdout
```

- [ ] **Step 2:** Run → FAIL.
- [ ] **Step 3:** Viết hai script (mở rộng logic `/home/ict/AIEngines/secai/secai-data/restore.sh`).
- [ ] **Step 4:** Run test → PASS. Thử thật với **tập con nhỏ**: `pack.sh data` trỏ một thư mục giả gồm `exploitdb` + một snapshot Qdrant nhỏ tạo từ collection thử trong container `qdrant-smoke-20261123`; `restore-data.sh` vào thư mục tạm; chạy lần hai → không lỗi, không đổi gì. Không đóng gói 49 GB thật ở bước này (đĩa còn 69 GB).
- [ ] **Step 5:** Commit `feat(stack): pack và restore dữ liệu có checksum`.

---

### Task 8: `check.sh` và smoke trên máy này

**Files:**
- Create: `stack/check.sh`
- Test: `tests/test_stack_scripts.py` (thêm); kết quả smoke ghi `docs/archive/test-results/stack-package-smoke-20261123.md`

**Interfaces:**
- `check.sh config` → chạy `docker compose config -q` cho: không profile, từng profile lẻ, tất cả; grep kết quả không có 4 chuỗi cấm ở Global Constraints.
- `check.sh models` → `GET $QWEN_BASE_URL/models`, embedding, rerank (kèm key); fail nếu `QWEN_MODEL`/`EMBEDDING_MODEL`/`RERANKER_MODEL` không có trong danh sách phục vụ.
- `check.sh health` → health từng service đang chạy + một truy vấn RAG thật qua gateway, fail nếu phản hồi báo BM25-only/không có hit Qdrant.

- [ ] **Step 1: Test fail** (stub `curl` trả danh sách model):

```python
def test_models_check_fails_on_name_mismatch(tmp_path):
    binp = tmp_path / "bin"; binp.mkdir()
    (binp / "curl").write_text('#!/bin/sh\necho \'{"data":[{"id":"BAAI/bge-m3"}]}\'\n'); (binp / "curl").chmod(0o755)
    env = {**os.environ, "PATH": f"{binp}:{os.environ['PATH']}", "QWEN_BASE_URL": "http://x/v1", "QWEN_MODEL": "m",
           "EMBEDDING_API_URL": "http://x", "EMBEDDING_MODEL": "DuyTa/sec-embedding",
           "RERANKER_API_URL": "http://x", "RERANKER_MODEL": "DuyTa/sec-rerank", "STACK_ENV": "/dev/null"}
    r = run("bash", str(STACK / "check.sh"), "models", env=env)
    assert r.returncode != 0 and "DuyTa/sec-embedding" in r.stderr
```

- [ ] **Step 2:** Run → FAIL. **Step 3:** Viết `check.sh`. **Step 4:** Run test → PASS; `stack/check.sh config` → PASS.
- [ ] **Step 5: Smoke core + dịch + báo cáo + review** trên project `secai-stack-smoke`: `.env` riêng (`GATEWAY_HOST_PORT` và `REPORT_HOST_PORT` lấy cổng trống ≥ 21000, bind IP LAN), `DATA_ROOT` = thư mục tạm `~/secai-stack-smoke-data`: `exploitdb`, `poc-sources`, `exploitdb-mcp-server` là **symlink** tới bản trong `/home/ict/AIEngines/secai/` (compose mount chúng `:ro` nên không ghi được vào dữ liệu prod); `poc` là **bản copy** (1,7 GB, vì mount ghi được cho `catalog.db` WAL); `_state` riêng và trống, Qdrant nạp tập con (Task 7). Tuyệt đối không trỏ `DATA_ROOT` thẳng vào `secai-data` của prod. Chạy `bootstrap.sh init && preflight`, `docker compose up -d`, `check.sh models && check.sh health`, một lần dịch văn bản synthetic, một lần tạo báo cáo synthetic. Ghi kết quả. `docker compose -p secai-stack-smoke down` (giữ volume tới khi xong Step 6).
- [ ] **Step 6: Smoke exploit-v2 N=24 — CHỜ CHỦ DỰ ÁN CHỌN KHUNG ~40 PHÚT VẮNG.** Bật profile `exploit` trong project smoke (`POC_LABS_EXTERNAL=true`, `POC_LABS_NETWORK=poc-labs-internal`), dừng tạm cặp `secai-ev2-vm-eval-a/b` nếu chủ dự án đồng ý (RAM). Chạy `local/loadtest-results/20261123-N24-vm-r3/prod_ramp.py` trỏ gateway smoke, lab nginx-ui 192.168.96.21, N=24 × 3, nghỉ 60 s; rồi chạy lại PoC trả về theo đúng cách đo cũ. **Đạt:** exploit ok ≥ 71/72 **và** PoC chạy lại ra bằng chứng ≥ 27/72. **Không đạt:** không sửa ngưỡng; ghi số, quay về phương án host-network theo spec mục 7 và hỏi chủ dự án.
- [ ] **Step 7:** `docker compose -p secai-stack-smoke down -v`; xác nhận `docker ps -a | grep smoke` rỗng và 30 container prod vẫn healthy. Commit báo cáo smoke.

---

### Task 9: Tài liệu và dọn bản cũ

**Files:**
- Create: `stack/README.md`
- Modify: `deploy/README.md`, `deploy/docker-compose.yml`, `deploy/up.sh` (đầu file: khối "DEPRECATED → dùng `stack/`"), `AGENTS.md`, `README.md` (mục triển khai trỏ `stack/README.md`), `docs/README.md`
- Test: `tests/test_stack_compose.py` (thêm)

- [ ] **Step 1: Test fail:**

```python
def test_readme_lists_the_five_steps_and_limits():
    t = (COMPOSE.parent / "README.md").read_text()
    for s in ("bootstrap.sh init", "restore-data.sh", "docker compose up -d", "check.sh", "Rollback",
              "chưa kiểm", "docker push"):
        assert s in t, s
```

- [ ] **Step 2:** Run → FAIL. **Step 3:** Viết `stack/README.md` một trang: yêu cầu máy (Docker Compose v2, RAM cho 2×`VM_MEM_LIMIT`, internet lúc build VM), sơ đồ thư mục, 5 bước dựng, bảng profile, bảng biến bắt buộc, cập nhật phiên bản (đổi `GATEWAY_IMAGE`/`VM_IMAGE` rồi `up -d`), rollback (đổi lại tag, giữ volume `report-data`), mục "Chưa kiểm" (máy đích thật, gói dữ liệu đầy đủ, Docker Hub bị chặn), ghi chú `docker push` thủ công. Thêm khối deprecated vào các file cũ, không xoá.
- [ ] **Step 4:** Run toàn bộ `scripts/dev.sh test tests/test_stack_compose.py tests/test_stack_scripts.py tests/test_stack_vm_image.py tests/test_ias_client_auth.py` → PASS; `git diff --check` sạch.
- [ ] **Step 5:** Commit `docs(stack): README và đánh dấu deploy/ cũ`. **Dừng**: push nhánh và mở PR chỉ khi chủ dự án cho phép; PR ghi rõ nó thay thế phần đóng gói của PR #57.
