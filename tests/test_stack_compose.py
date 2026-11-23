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
