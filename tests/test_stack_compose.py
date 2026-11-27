import pytest
import yaml
from pathlib import Path

COMPOSE = Path(__file__).resolve().parents[1] / "stack/compose.yml"


def load_compose():
    return yaml.safe_load(COMPOSE.read_text())


def services(profile=None):
    return {n: s for n, s in load_compose()["services"].items()
            if (profile in s.get("profiles", [])) or (profile is None and "profiles" not in s)}


def test_core_services_and_pinned_images():
    core = services()
    assert set(core) == {"gateway", "qdrant", "neo4j", "redis", "es"}
    # Tag gốc là mặc định; bản phát hành (secai/publish.sh) ghi <tag>@sha256:<digest> vào .env qua các biến này.
    assert core["qdrant"]["image"] == "${QDRANT_IMAGE:-qdrant/qdrant:v1.10.1}"
    assert core["neo4j"]["image"] == "${NEO4J_IMAGE:-neo4j:5.20-community}"
    assert core["redis"]["image"] == "${REDIS_IMAGE:-redis:7-alpine}"
    assert core["es"]["image"] == "${ES_IMAGE:-docker.elastic.co/elasticsearch/elasticsearch:8.15.3}"
    assert core["gateway"]["image"] == "${GATEWAY_IMAGE:?set GATEWAY_IMAGE}"


def test_no_host_coupling_anywhere():
    text = COMPOSE.read_text()
    for bad in ("/home/ict", "docker.sock", "host.docker.internal", "container_name", ":latest"):
        assert bad not in text, bad
    # network_mode chỉ được dùng để chung netns với translation-gateway (sidecar tunnel + worker), không host/none/container:.
    nm = {n: s["network_mode"] for n, s in load_compose()["services"].items() if "network_mode" in s}
    assert nm == {"specialist-tunnel": "service:translation-gateway", "translation-worker": "service:translation-gateway"}
    assert text.count("network_mode") == 2


def test_gateway_wiring():
    env = services()["gateway"]["environment"]
    assert env["QDRANT_URL"] == "http://qdrant:6333"
    assert env["NEO4J_URI"] == "bolt://neo4j:7687"
    assert env["REDIS_URL"] == "redis://redis:6379"
    assert env["ELASTICSEARCH_URL"] == "http://es:9200"
    assert not any(k.startswith("SHOWCASE_") and "UPSTREAM" in k and v for k, v in env.items())


def test_only_gateway_and_report_publish_ports():
    for name, s in load_compose()["services"].items():
        if name not in {"gateway", "report-backend", "review-frontend"}:
            assert "ports" not in s, name


def test_networks_and_volumes_declared():
    c = load_compose()
    assert {"secai-net", "poc-labs-internal"} <= set(c["networks"])
    assert {"gw-data", "redis-data", "es-data"} <= set(c["volumes"])
    for s in c["services"].values():
        if "network_mode" not in s:   # service chung netns thì không khai networks (thừa hưởng của translation-gateway)
            assert "secai-net" in s["networks"]


def test_env_files_not_required():
    for name, s in load_compose()["services"].items():
        for e in s.get("env_file", []):
            assert isinstance(e, dict) and e.get("required") is False, name


def test_config_validates_without_dotenv(tmp_path):
    import os, shutil, subprocess
    import pytest
    if not shutil.which("docker"):
        pytest.skip("docker CLI not available")
    if subprocess.run(["docker", "compose", "version"], capture_output=True).returncode != 0:
        pytest.skip("docker compose plugin not available")
    assert not (COMPOSE.parent / ".env").exists(), "stack/.env must not exist for this test"
    env = {**os.environ, "GATEWAY_IMAGE": "x", "DATA_ROOT": str(tmp_path), "NEO4J_PASSWORD": "x"}
    r = subprocess.run(["docker", "compose", "-f", str(COMPOSE), "config", "-q"],
                       env=env, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


def test_no_default_secrets_or_unguarded_data_root():
    text = COMPOSE.read_text()
    assert "NEO4J_PASSWORD:-" not in text  # không mặc định literal
    assert "NEO4J_PASSWORD:?" in text
    assert text.count("${DATA_ROOT") == text.count("${DATA_ROOT:?"), "every DATA_ROOT must use the :? form"


def test_exploit_profile_two_vms_hardened():
    vms = services("exploit")
    assert set(vms) == {"vm-a", "vm-b"}
    for name, port in (("vm-a", "5101"), ("vm-b", "5201")):
        s = vms[name]
        # `:?` làm `config` lỗi khi profile tắt (compose nội suy cả service ngoài profile) -> mặc định là tag MISSING.
        assert s["image"] == "${VM_IMAGE:-secai-pentestcode-vm:MISSING-set-VM_IMAGE}"
        assert s["cap_drop"] == ["ALL"]
        assert set(s["cap_add"]) == {"CHOWN", "DAC_OVERRIDE", "FOWNER", "SETUID", "SETGID", "KILL"}
        assert "no-new-privileges:true" in s["security_opt"]
        assert s["mem_limit"] == "${VM_MEM_LIMIT:-48g}"
        assert s["pids_limit"] == "${VM_PIDS_LIMIT:-16384}"
        assert s["environment"]["VM_PORT_BASE"] == port
        assert s["environment"]["VM_SLOTS"] == "${VM_SLOTS:-12}"
        assert s["environment"]["OPENCODE_SERVER_PASSWORD"] == "${IAS_SERVER_PASSWORD:-}"
        assert s["environment"]["ORCA_LOCAL_BASE_URL"] == "${LLM_BASE_URL:-${QWEN_BASE_URL:-}}"   # LLM_* chuẩn, QWEN_* cũ
        assert set(s["networks"]) == {"secai-net", "poc-labs-internal"}
        assert "ports" not in s
        assert all(":ro" in v or v.startswith("pool-home-") for v in s["volumes"])
        data = {v.split(":")[-2] for v in s["volumes"] if ":ro" in v}
        assert data == {f"/srv/secai-data/{d}" for d in ("exploitdb", "poc", "poc-sources", "exploitdb-mcp-server")}
        assert "OPENCODE_SERVER_PASSWORD" in s["healthcheck"]["test"][1]
    assert any(v.startswith("pool-home-a:/ias-pool-home:") or v == "pool-home-a:/ias-pool-home" for v in vms["vm-a"]["volumes"])
    assert any(v == "pool-home-b:/ias-pool-home" for v in vms["vm-b"]["volumes"])


def test_gateway_pool_wiring_without_depends_on_vm():
    gw = services()["gateway"]
    assert gw["environment"]["IAS_SERVER_POOL"] == "${IAS_SERVER_POOL:-}"
    assert gw["environment"]["IAS_POOL_GPU_COUNT"] == "${IAS_POOL_GPU_COUNT:-0}"
    assert gw["environment"]["IAS_SERVER_PASSWORD"] == "${IAS_SERVER_PASSWORD:-}"
    assert "pool-home-a:/ias-pool-home-a" in gw["volumes"] and "pool-home-b:/ias-pool-home-b" in gw["volumes"]
    assert not {"vm-a", "vm-b"} & set(gw["depends_on"])
    assert {"pool-home-a", "pool-home-b"} <= set(load_compose()["volumes"])


def test_config_validates_with_and_without_exploit_profile(tmp_path):
    import os, shutil, subprocess
    import pytest
    if not shutil.which("docker") or subprocess.run(["docker", "compose", "version"], capture_output=True).returncode != 0:
        pytest.skip("docker compose not available")
    env = {"PATH": os.environ["PATH"], "GATEWAY_IMAGE": "x", "DATA_ROOT": str(tmp_path), "NEO4J_PASSWORD": "x"}
    for extra in ([], ["--profile", "exploit"]):
        r = subprocess.run(["docker", "compose", "-f", str(COMPOSE), *extra, "config", "-q"],
                           env=env, capture_output=True, text=True)
        assert r.returncode == 0, (extra, r.stderr)


def test_translation_profile_on_bridge():
    t = services("translation")
    assert set(t) == {"translation-gateway", "translation-worker", "translation-converter", "specialist-tunnel"}
    for n in ("translation-gateway", "translation-worker"):
        assert t[n]["image"] == "${GATEWAY_IMAGE:?set GATEWAY_IMAGE}"
        e = t[n]["environment"]
        assert e["TRANSLATE_V2_CONVERTER_URL"] == "http://translation-converter:18091"
        assert e["QDRANT_URL"] == "http://qdrant:6333"
        assert not {"QWEN_BASE_URL", "LLM_BASE_URL", "EMBEDDING_API_URL"} & set(e)   # chỉ từ .env chung
        assert not [k for k in e if k.startswith(("SHOWCASE_", "REWARD_"))]
        assert e["MULTILANG_SPECIALIST_TOKENIZER_JSON"] == "/app/translation-tokenizer/tokenizer.json"
        assert e["MULTILANG_SPECIALIST_CONTEXT_TOKENS"] == "4096"
        assert e["TRANSLATE_V2_CONVERTER_TOKEN"] == "${TRANSLATE_V2_CONVERTER_TOKEN:-}"
        assert any(v.endswith(":/app/translation-tokenizer:ro") for v in t[n]["volumes"])
        assert "translation-data:/app/data" in t[n]["volumes"]
    assert t["translation-gateway"]["command"][:2] == ["uvicorn", "secai_gateway.multilang_app:app"]
    assert t["translation-gateway"]["command"][-1] == "18185"
    assert "18185" in t["translation-gateway"]["healthcheck"]["test"][1]
    assert "8131" not in t["translation-gateway"]["healthcheck"]["test"][1]
    # Bọc bởi netns_watch: translation-gateway khởi động lại thì netns cũ chỉ còn lo -> worker thoát để Docker nối lại.
    assert t["translation-worker"]["command"] == ["python", "-I", "/opt/stack/netns_watch.py",
                                                   "python", "-m", "secai_gateway.translation.worker"]
    assert "./lib/netns_watch.py:/opt/stack/netns_watch.py:ro" in t["translation-worker"]["volumes"]
    c = t["translation-converter"]
    assert c["image"] == "${CONVERTER_IMAGE:-secai-doc-convert:MISSING-set-CONVERTER_IMAGE}"
    assert c["environment"]["CONVERTER_TOKEN"] == "${TRANSLATE_V2_CONVERTER_TOKEN:-}"
    assert c["environment"]["CONVERTER_PORT"] == "18091" and c["environment"]["CONVERTER_HOST"] == "0.0.0.0"
    assert c["cap_drop"] == ["ALL"] and "ports" not in c
    g = services()["gateway"]["environment"]
    assert g["TRANSLATION_UPSTREAM_URL"] == "${TRANSLATION_UPSTREAM_URL:-}"
    assert g["REVIEW_ENABLED"] == "${REVIEW_ENABLED:-false}"
    assert g["REVIEW_CREDENTIALS_FILE"] == "/app/review-credentials.json"
    assert g["REVIEW_DB_PATH"] == "/app/review-data/reviews.db"
    gv = services()["gateway"]["volumes"]
    assert "./secrets/review-credentials.json:/app/review-credentials.json:ro" in gv
    assert "review-data:/app/review-data" in gv


def test_no_plain_depends_on_profiled_services():
    c = load_compose()["services"]
    profiled = {n for n, s in c.items() if "profiles" in s}
    for n, s in c.items():
        if "profiles" in s:
            continue
        assert not profiled & set(s.get("depends_on", {})), n


def test_report_and_review_profiles():
    r = services("report")["report-backend"]
    assert r["image"] == "${GATEWAY_IMAGE:?set GATEWAY_IMAGE}"
    assert r["environment"]["REPORT_FEATURE_ENABLED"] == "true"
    assert r["environment"]["REPORT_DB_PATH"] == "/srv/reports/reports.db"
    assert r["environment"]["REPORT_API_KEY"] == "${REPORT_API_KEY:-}"
    assert "report-data:/srv/reports" in r["volumes"]
    assert r["ports"] == ["${REPORT_BIND:-127.0.0.1}:${REPORT_HOST_PORT:-18183}:8131"]
    assert r["command"] == ["uvicorn", "secai_gateway.app:app", "--host", "0.0.0.0", "--port", "8131"]
    v = services("review")["review-frontend"]
    assert v["environment"]["VITE_GATEWAY_PROXY"] == "http://gateway:8131"
    assert v["command"] == ["python", "/app/review-ui/serve_static.py"]
    assert v["image"] == "${REVIEW_UI_IMAGE:-secai-review-ui:MISSING-set-REVIEW_UI_IMAGE}"
    assert v["ports"] == ["${REVIEW_BIND:-127.0.0.1}:${REVIEW_HOST_PORT:-5174}:5174"]
    assert {"translation-data", "report-data", "review-data"} <= set(load_compose()["volumes"])


def test_config_validates_every_profile_combination(tmp_path):
    import os, shutil, subprocess
    import pytest
    if not shutil.which("docker") or subprocess.run(["docker", "compose", "version"], capture_output=True).returncode != 0:
        pytest.skip("docker compose not available")
    env = {"PATH": os.environ["PATH"], "GATEWAY_IMAGE": "x", "DATA_ROOT": str(tmp_path), "NEO4J_PASSWORD": "x"}
    combos = [[], *[["--profile", p] for p in ("exploit", "translation", "report", "review")],
              ["--profile", "exploit", "--profile", "translation", "--profile", "report", "--profile", "review"]]
    for extra in combos:
        r = subprocess.run(["docker", "compose", "-f", str(COMPOSE), *extra, "config", "-q"],
                           env=env, capture_output=True, text=True)
        assert r.returncode == 0, (extra, r.stderr)


def test_vm_healthcheck_uses_ias_user_and_has_no_password_branch():
    for s in services("exploit").values():
        cmd = s["healthcheck"]["test"][1]
        auth, plain = cmd.split("; else ")
        assert 'curl -fsS -u "ias:$$OPENCODE_SERVER_PASSWORD" http://127.0.0.1:$$VM_PORT_BASE/global/health' in auth
        assert "-u" not in plain and plain.startswith("curl -fsS http://127.0.0.1:$$VM_PORT_BASE/global/health")
        assert "opencode:" not in cmd


def test_shared_gid_for_gateway_and_vms():
    assert "${VM_SHARED_GID:-10100}" in services()["gateway"]["group_add"]
    for s in services("exploit").values():
        assert s["environment"]["VM_SHARED_GID"] == "${VM_SHARED_GID:-10100}"


def test_review_only_on_gateway():
    # .env chung có REVIEW_ENABLED=true (init đặt khi bật review); service khác không mount review-credentials.json
    # nên phải ghi đè false, nếu không chúng chết lúc khởi động (load_credentials ValueError) — thấy ở smoke 2026-11-23.
    c = load_compose()["services"]
    for n, s in c.items():
        if n != "gateway" and s.get("image") == "${GATEWAY_IMAGE:?set GATEWAY_IMAGE}":
            assert s["environment"].get("REVIEW_ENABLED") == "false", n


def test_worker_does_not_inherit_http_healthcheck():
    # Image gateway có HEALTHCHECK gọi 127.0.0.1:8131; worker không mở cổng nào nên luôn unhealthy trên bridge
    # (prod chạy host network nên probe trúng gateway prod — xanh giả). Smoke 2026-11-23.
    w = services("translation")["translation-worker"]
    assert w["healthcheck"] == {"disable": True}


def test_specialist_tunnel_sidecar_shares_translation_netns():
    # specialist.py/provider.py chỉ nhận http loopback (giữ chặn SSRF, không sửa src/): sidecar ở chung netns với
    # translation-gateway nên 127.0.0.1:18868/18083 trong gateway và worker là đầu tunnel.
    t = services("translation")
    s = t["specialist-tunnel"]
    assert s["network_mode"] == "service:translation-gateway"
    assert "networks" not in s and "ports" not in s and "env_file" not in s   # không nhận cả .env (khoá API)
    assert s["image"] == "${TUNNEL_IMAGE:-secai-specialist-tunnel:MISSING-set-TUNNEL_IMAGE}"
    assert s["cap_drop"] == ["ALL"] and "cap_add" not in s
    assert "no-new-privileges:true" in s["security_opt"]
    assert s["read_only"] is True and "privileged" not in s
    assert s["user"] == "${TUNNEL_UID:-10002}"
    assert s["volumes"] == ["${TUNNEL_SECRETS_DIR:-./secrets/tunnel}:/run/tunnel:ro"]
    assert not any("docker.sock" in v for v in s["volumes"])
    env = s["environment"]
    assert set(env) == {"TUNNEL_MODE", "TUNNEL_SSH_HOST", "TUNNEL_SSH_PORT", "TUNNEL_SSH_USER",
                        "TUNNEL_REMOTE_SPECIALIST_PORT", "TUNNEL_REMOTE_OCR_PORT",
                        "TUNNEL_DIRECT_SPECIALIST", "TUNNEL_DIRECT_OCR", "TUNNEL_DIRECT_ALLOW_TEST"}
    assert env["TUNNEL_MODE"] == "${TUNNEL_MODE:-ssh}"
    w = t["translation-worker"]
    assert w["network_mode"] == "service:translation-gateway" and "networks" not in w
    for n in ("specialist-tunnel", "translation-worker"):
        # gateway dịch bị tạo lại/khởi động lại qua compose thì netns cũ mất: compose phải khởi động lại cả hai.
        assert t[n]["depends_on"]["translation-gateway"] == {"condition": "service_started", "restart": True}, n


@pytest.mark.parametrize("env,want", [
    ({"QWEN_BASE_URL": "http://old/v1", "QWEN_MODEL": "old", "QWEN_API_KEY": "ko", "QWEN_CONTEXT_WINDOW": "4096"},
     ("http://old/v1", "old", "ko", "4096")),
    ({"LLM_BASE_URL": "http://new/v1", "LLM_MODEL": "new", "LLM_API_KEY": "kn"}, ("http://new/v1", "new", "kn", "131072")),
    ({"LLM_BASE_URL": "http://new/v1", "QWEN_BASE_URL": "http://old/v1", "LLM_MODEL": "new", "QWEN_MODEL": "old",
      "LLM_API_KEY": "kn", "QWEN_API_KEY": "ko", "LLM_CONTEXT_WINDOW": "65536", "QWEN_CONTEXT_WINDOW": "4096"},
     ("http://new/v1", "new", "kn", "65536")),
])
def test_vm_llm_env_canonical_with_legacy_fallback(tmp_path, env, want):
    import json, os, shutil, subprocess
    if not shutil.which("docker") or subprocess.run(["docker", "compose", "version"], capture_output=True).returncode != 0:
        pytest.skip("docker compose not available")
    e = {"PATH": os.environ["PATH"], "GATEWAY_IMAGE": "x:1", "DATA_ROOT": str(tmp_path), "NEO4J_PASSWORD": "x", **env}
    r = subprocess.run(["docker", "compose", "-f", str(COMPOSE), "--profile", "exploit", "config", "--format", "json"],
                       env=e, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    v = json.loads(r.stdout)["services"]["vm-a"]["environment"]
    assert (v["ORCA_LOCAL_BASE_URL"], v["ORCA_LOCAL_MODEL_ID"], v["ORCA_LOCAL_API_KEY"], v["ORCA_LOCAL_CONTEXT"]) == want


def test_readme():
    import re
    stack = COMPOSE.parent
    readme = stack / "README.md"
    assert readme.exists()
    t = readme.read_text()
    for var in set(re.findall(r"\$\{(\w+):\?", COMPOSE.read_text())):
        assert var in t, var
    for script in ("bootstrap.sh", "pack.sh", "restore-data.sh", "check.sh", "eval-vast.sh", "destroy-eval.sh"):
        assert (stack / script).exists() and script in t, script
    for profile in ("exploit", "translation", "report", "review"):
        assert f"`{profile}`" in t, profile
    assert "## Chưa kiểm" in t
    for s in ("bootstrap.sh init", "restore-data.sh", "docker compose up -d", "check.sh", "Rollback", "docker push"):
        assert s in t, s
    assert "/home/ict" not in t
    for pat in (r"\b[0-9a-f]{32,}\b", r"\bsk-[A-Za-z0-9]{16,}", r"\bghp_\w{20,}", r"-----BEGIN", r"\bAKIA[0-9A-Z]{12,}"):
        assert not re.search(pat, t), pat
    # deploy/ cũ phải trỏ về stack/ nhưng không đổi hành vi
    root = stack.parent
    for f in ("deploy/docker-compose.yml", "deploy/up.sh", "deploy/README.md"):
        assert "DEPRECATED" in (root / f).read_text()[:1500], f
