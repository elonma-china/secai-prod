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


def test_networks_and_volumes_declared():
    c = load_compose()
    assert {"secai-net", "poc-labs-internal"} <= set(c["networks"])
    assert {"gw-data", "redis-data", "es-data"} <= set(c["volumes"])
    for s in c["services"].values():
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
    assert "secai2026" not in text
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
        assert s["environment"]["ORCA_LOCAL_BASE_URL"] == "${QWEN_BASE_URL:-}"
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
