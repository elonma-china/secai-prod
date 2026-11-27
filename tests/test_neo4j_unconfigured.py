"""NEO4J_PASSWORD không có mặc định trong code: thiếu thì Neo4j coi như chưa cấu hình.

Tính năng đồ thị phải degrade êm (rỗng/lỗi mềm, không request nào tới Neo4j, không exception),
/health báo neo4j ok=false kèm lý do."""
from __future__ import annotations

import httpx
import pytest
from linch.tools.base import ToolContext

from secai_gateway.api import health_deps as hd
from secai_gateway.config import Settings
from secai_gateway.retrieval import kb_context, neo4j, qdrant


@pytest.fixture
def unconfigured(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Neo4j chưa cấu hình; mọi request HTTP tới Neo4j (cổng 7474) bị ghi lại để test chứng minh không có."""
    monkeypatch.setattr(neo4j, "NEO4J_PASSWORD", "")
    calls: list[str] = []
    real = httpx.AsyncClient

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.port == 7474 or "/tx/commit" in request.url.path:
            calls.append(str(request.url))
            return httpx.Response(401, json={"errors": [{"code": "Neo.ClientError.Security.Unauthorized"}]})
        if "/collections/" in request.url.path:
            if request.url.path.endswith("/points/scroll"):
                return httpx.Response(200, json={"result": {"points": []}})
            return httpx.Response(200, json={"result": {"status": "green", "points_count": 3}})
        return httpx.Response(404)

    def fake_client(*args, **kwargs):
        kwargs = dict(kwargs)
        kwargs["transport"] = httpx.MockTransport(handler)
        return real(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", fake_client)
    return calls


def test_settings_default_has_no_password(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NEO4J_PASSWORD", raising=False)
    assert Settings(_env_file=None).neo4j_password == ""


@pytest.mark.asyncio
async def test_graph_lookups_degrade_without_request(unconfigured: list[str]) -> None:
    assert neo4j.neo4j_configured() is False
    assert await neo4j.cve_exists_in_kb("CVE-2024-1234") is False
    assert await neo4j.cve_cwe_refs("CVE-2024-1234") == []
    res = await neo4j._neo4j_search("CVE-2024-1234", ToolContext(cwd=".", session_id="t", run_id="t", session_store=None))
    assert res.is_error and "not configured" in res.content and "qdrant_search" in res.content
    async with httpx.AsyncClient() as client:
        assert await qdrant._graph_reverse_candidates(client, {"cwe_id": ["CWE-79"]}) == {}
        assert await kb_context._graph_context(client, "CVE-2024-1234") == []
    assert unconfigured == []


@pytest.mark.asyncio
async def test_cve_context_without_graph_is_not_a_service_error(unconfigured: list[str]) -> None:
    out = await kb_context.get_cve_context("CVE-2024-1234")
    assert out["status"] == "not_found"  # Qdrant trả rỗng, đồ thị vắng mặt: không phải "error"
    assert unconfigured == []


@pytest.mark.asyncio
async def test_health_reports_neo4j_not_configured(unconfigured: list[str]) -> None:
    class S:
        embedding_api_url = ""
        embedding_api_key = ""
        embedding_model = "m"
        reranker_api_url = ""
        reranker_api_key = ""
        reranker_model = "m"
        qdrant_url = "http://qdrant:6333"
        qdrant_collection = "cve_kb_hybrid"
        neo4j_database = "neo4j"
        showcase_kb_sync_log = "/nonexistent/sync.log"

    extra = await hd.collect(S())
    row = extra["databases"]["neo4j"]
    assert row["ok"] is False and row["detail"] == "chưa cấu hình NEO4J_PASSWORD"
    assert extra["databases"]["qdrant"]["ok"] is True
    assert "neo4j: chưa cấu hình NEO4J_PASSWORD" in hd.problems_from(extra)
    assert unconfigured == []


@pytest.mark.asyncio
async def test_configured_password_still_queries(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(neo4j, "NEO4J_PASSWORD", "fake-test-password")
    seen: list[str] = []
    real = httpx.AsyncClient

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers.get("Authorization", ""))
        return httpx.Response(200, json={"errors": [], "results": [{"columns": ["n"], "data": [{"row": [1]}]}]})

    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **k: real(*a, **{**k, "transport": httpx.MockTransport(handler)}))
    assert await neo4j.cve_exists_in_kb("CVE-2024-1234") is True
    assert seen and seen[0].startswith("Basic ")


def test_repo_deploy_files_have_no_neo4j_password_default() -> None:
    import yaml
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    compose = (root / "deploy" / "docker-compose.yml").read_text(encoding="utf-8")
    assert "NEO4J_PASSWORD:-" not in compose and "${NEO4J_PASSWORD:?" in compose
    helm = root / "deploy" / "helm" / "secai-gateway"
    assert yaml.safe_load((helm / "values.yaml").read_text(encoding="utf-8"))["neo4j"]["password"] == ""
    assert "required" in (helm / "templates" / "neo4j.yaml").read_text(encoding="utf-8")
