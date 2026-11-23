"""stack/check.sh env|config|models|health|all (Task 8a). curl và docker được stub qua PATH: không gọi mạng thật,
không `up`; chỉ `config` dùng docker compose thật (offline) và bị skip nếu thiếu docker."""
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

STACK = Path(__file__).resolve().parents[1] / "stack"
FAKE = "sk-FAKE-SECRET-9f3a7c"   # không được xuất hiện ở stdout/stderr/argv

CURL_STUB = '''#!/usr/bin/env python3
import json, os, re, sys
cfg = sys.stdin.read()
open(os.environ["STUB_LOG"], "a").write(json.dumps({"argv": sys.argv[1:], "cfg": cfg}) + "\\n")
g = lambda k: (re.search(r'^' + k + r' = "(.*)"$', cfg, re.M) or [None, ""])[1]
key = (g("request") or "GET") + " " + g("url")
code, body = json.load(open(os.environ["STUB_ROUTES"])).get(key, [404, '{"error":"no route %s"}' % key])
sys.stdout.write(body + "\\n" + str(code))
'''
DOCKER_STUB = '''#!/bin/sh
echo "$@" >> "$DOCKER_LOG"
case " $* " in *" ps "*) cat "$DOCKER_PS" ;; *) echo "stub docker: chỉ cho phép ps" >&2; exit 9 ;; esac
'''

LLM, EMB, RR, GW = "http://llm/v1", "http://emb", "http://rr", "http://gw:8131"
CHAT = ("Log4Shell (CVE-2021-44228) là lỗ hổng RCE trong Apache Log4j2, CVSS 10.0. Bản vá: nâng lên 2.16.0 trở lên. " * 8)
OK_ROUTES = {
    f"GET {LLM}/models": [200, json.dumps({"data": [{"id": "qwen-m"}]})],
    f"POST {EMB}/v1/embeddings": [200, json.dumps({"data": [{"embedding": [0.1, 0.2]}]})],
    f"POST {RR}/v1/rerank": [200, json.dumps({"results": [{"index": 0, "relevance_score": 0.9}]})],
}
DEEP = {"status": "ok", "llm": True, "redis": "connected",
        "serving": {"embedding": {"ok": True}, "reranker": {"ok": True}},
        "databases": {"qdrant": {"ok": True, "points": 1000}, "neo4j": {"ok": True}}}
SSE = 'event: token\ndata: {"delta": "x"}\n\nevent: final\ndata: ' + json.dumps({"text": CHAT}) + "\n\ndata: [DONE]\n\n"
HEALTH_ROUTES = {
    f"GET {GW}/health": [200, json.dumps(DEEP)],
    f"POST {GW}/sessions": [200, json.dumps({"session_id": "s1"})],
    f"POST {GW}/sessions/s1/chat/stream": [200, SSE],
}
CORE = ["gateway", "qdrant", "neo4j", "redis", "es"]
MODEL_ENV = {"QWEN_BASE_URL": LLM, "QWEN_MODEL": "qwen-m", "QWEN_API_KEY": FAKE,
             "EMBEDDING_API_URL": EMB, "EMBEDDING_MODEL": "DuyTa/sec-embedding", "EMBEDDING_API_KEY": FAKE,
             "RERANKER_API_URL": RR, "RERANKER_MODEL": "DuyTa/sec-rerank", "RERANKER_API_KEY": FAKE}


def _stub(tmp_path, name, text):
    b = tmp_path / "bin"
    b.mkdir(exist_ok=True)
    (b / name).write_text(text)
    (b / name).chmod(0o755)
    return b


def _run(tmp_path, sub, routes=None, env=None, ps=None, stack=None):
    b = _stub(tmp_path, "curl", CURL_STUB)
    _stub(tmp_path, "docker", DOCKER_STUB)
    (tmp_path / "routes.json").write_text(json.dumps(routes or {}))
    (tmp_path / "ps.json").write_text("\n".join(json.dumps(r) for r in (ps or [])))
    e = {**os.environ, "PATH": f"{b}:{os.environ['PATH']}", "STACK_ENV": "/dev/null", "STUB_LOG": str(tmp_path / "curl.log"),
         "STUB_ROUTES": str(tmp_path / "routes.json"), "DOCKER_LOG": str(tmp_path / "docker.log"),
         "DOCKER_PS": str(tmp_path / "ps.json"), "COMPOSE_PROFILES": "", **(env or {})}
    return subprocess.run(["bash", str((stack or STACK) / "check.sh"), *sub.split()], text=True, capture_output=True, env=e, timeout=120)


def _no_leak(tmp_path, r):
    assert FAKE not in r.stdout and FAKE not in r.stderr
    log = tmp_path / "curl.log"
    if log.exists():
        assert all(FAKE not in json.dumps(json.loads(l)["argv"]) for l in log.read_text().splitlines())   # khoá không lên argv/ps


def _ps(running=CORE, **override):
    return [{"Service": s, "State": "running", "Health": "healthy", **override.get(s, {})} for s in running]


# ---- usage ----
def test_usage_on_bad_subcommand(tmp_path):
    for sub in ("bogus", ""):
        r = _run(tmp_path, sub)
        assert r.returncode == 2 and "usage" in r.stderr and "models" in r.stderr, sub


# ---- models ----
def test_models_ok(tmp_path):
    r = _run(tmp_path, "models", OK_ROUTES, MODEL_ENV)
    assert r.returncode == 0, r.stderr
    _no_leak(tmp_path, r)
    assert any(FAKE in json.loads(l)["cfg"] for l in (tmp_path / "curl.log").read_text().splitlines())  # khoá có gửi, qua stdin config


def test_models_brief_stub_mismatch_names_embedding(tmp_path):
    routes = {k: [200, '{"data":[{"id":"BAAI/bge-m3"}]}'] for k in OK_ROUTES}
    r = _run(tmp_path, "models", routes, MODEL_ENV)
    assert r.returncode != 0 and "DuyTa/sec-embedding" in r.stderr and "DuyTa/sec-rerank" in r.stderr
    _no_leak(tmp_path, r)


def test_models_llm_mismatch_lists_expected_and_served(tmp_path):
    routes = {**OK_ROUTES, f"GET {LLM}/models": [200, json.dumps({"data": [{"id": "other-a"}, {"id": "other-b"}]})]}
    r = _run(tmp_path, "models", routes, MODEL_ENV)
    assert r.returncode != 0
    assert "QWEN_MODEL" in r.stderr and "qwen-m" in r.stderr and "other-a" in r.stderr and "other-b" in r.stderr
    assert "EMBEDDING_MODEL" not in r.stderr   # chỉ báo thứ sai


@pytest.mark.parametrize("route,var,model", [(f"POST {EMB}/v1/embeddings", "EMBEDDING_MODEL", "DuyTa/sec-embedding"),
                                             (f"POST {RR}/v1/rerank", "RERANKER_MODEL", "DuyTa/sec-rerank")])
def test_models_404_is_mismatch(tmp_path, route, var, model):
    r = _run(tmp_path, "models", {**OK_ROUTES, route: [404, '{"detail":"model not found"}']}, MODEL_ENV)
    assert r.returncode != 0 and var in r.stderr and model in r.stderr and "404" in r.stderr
    _no_leak(tmp_path, r)


def test_models_missing_key_fails_clearly(tmp_path):
    env = {**MODEL_ENV, "EMBEDDING_API_KEY": ""}
    r = _run(tmp_path, "models", OK_ROUTES, env)
    assert r.returncode != 0 and "EMBEDDING_API_KEY" in r.stderr
    _no_leak(tmp_path, r)


def test_models_unreachable(tmp_path):
    r = _run(tmp_path, "models", {**OK_ROUTES, f"GET {LLM}/models": ["000", ""]}, MODEL_ENV)
    assert r.returncode != 0 and "QWEN_BASE_URL" in r.stderr


# ---- config ----
def _stack_copy(tmp_path):
    d = tmp_path / "stk" / "stack"
    (d / "lib").mkdir(parents=True)
    for f in ("compose.yml", "bootstrap.sh", "check.sh", ".env.example"):
        if (STACK / f).exists():
            shutil.copy(STACK / f, d / f)
    shutil.copy(STACK / "lib/pool.sh", d / "lib/pool.sh")
    return d


needs_docker = pytest.mark.skipif(shutil.which("docker") is None or subprocess.run(["docker", "compose", "version"], capture_output=True).returncode != 0,
                                  reason="docker compose không có")


def _config(tmp_path, d, **env):
    # docker thật (offline): PATH không stub docker.
    e = {**os.environ, "STACK_DIR": str(d), "NEO4J_PASSWORD": FAKE, **env}
    return subprocess.run(["bash", str(d / "check.sh"), "config"], text=True, capture_output=True, env=e, timeout=180)


@needs_docker
def test_config_passes_real_compose_all_six_combinations(tmp_path):
    d = _stack_copy(tmp_path)
    r = _config(tmp_path, d)
    assert r.returncode == 0, r.stderr
    assert r.stdout.count("ok: config") == 6, r.stdout
    assert FAKE not in r.stdout + r.stderr


@needs_docker
def test_config_allows_home_path_in_bind_sources(tmp_path):
    d = _stack_copy(tmp_path)
    r = _config(tmp_path, d, DATA_ROOT="/home/ict/data")
    assert r.returncode == 0, r.stderr


@needs_docker
def test_config_flags_rendered_container_name(tmp_path):
    d = _stack_copy(tmp_path)
    c = d / "compose.yml"
    c.write_text(c.read_text().replace("  qdrant:\n    image: qdrant/qdrant:v1.10.1\n", "  qdrant:\n    image: qdrant/qdrant:v1.10.1\n    container_name: fixed\n", 1))
    r = _config(tmp_path, d)
    assert r.returncode != 0 and "container_name" in r.stderr and "FAIL" in r.stderr


@needs_docker
def test_config_flags_latest_in_rendered_image_and_names_combination(tmp_path):
    d = _stack_copy(tmp_path)
    r = _config(tmp_path, d, GATEWAY_IMAGE="img:latest")
    assert r.returncode != 0 and ":latest" in r.stderr and "profile" in r.stderr
    assert FAKE not in r.stdout + r.stderr


@needs_docker
def test_config_flags_source_text_home_path(tmp_path):
    d = _stack_copy(tmp_path)
    c = d / "compose.yml"
    c.write_text(c.read_text() + "\n# /home/ict/secai/x\n")
    r = _config(tmp_path, d)
    assert r.returncode != 0 and "/home/ict" in r.stderr and "compose.yml" in r.stderr


@needs_docker
def test_config_real_env_value_beats_dummy(tmp_path):
    d = _stack_copy(tmp_path)
    (d / ".env").write_text("GATEWAY_IMAGE=real:latest\n")
    r = _config(tmp_path, d)   # .env có :latest; dummy không được che nó
    assert r.returncode != 0 and ":latest" in r.stderr


# ---- health ----
def _health(tmp_path, routes=None, ps=None, **env):
    return _run(tmp_path, "health", {**HEALTH_ROUTES, **(routes or {})}, {"GATEWAY_URL": GW, **env}, ps if ps is not None else _ps())


def test_health_ok(tmp_path):
    r = _health(tmp_path)
    assert r.returncode == 0, r.stderr
    assert "up" not in (tmp_path / "docker.log").read_text().split()   # chỉ ps, không up


def test_health_chat_missing_markers_fails(tmp_path):
    bad = 'event: final\ndata: ' + json.dumps({"text": "Xin lỗi, tôi không biết. " * 40}) + "\n\ndata: [DONE]\n\n"
    r = _health(tmp_path, {f"POST {GW}/sessions/s1/chat/stream": [200, bad]})
    assert r.returncode != 0 and "Log4" in r.stderr


def test_health_chat_provider_error_fails(tmp_path):
    bad = 'event: final\ndata: ' + json.dumps({"text": CHAT + " ProviderError"}) + "\n\ndata: [DONE]\n\n"
    r = _health(tmp_path, {f"POST {GW}/sessions/s1/chat/stream": [200, bad]})
    assert r.returncode != 0 and "ProviderError" in r.stderr


def test_health_bm25_only_signal_fails(tmp_path):
    h = json.loads(json.dumps(DEEP))
    h["serving"]["embedding"] = {"ok": False, "detail": "HTTP 404 (model 'x' có thể sai tên — RAG sẽ rơi về BM25-only)"}
    r = _health(tmp_path, {f"GET {GW}/health": [200, json.dumps(h)]})
    assert r.returncode != 0 and "embedding" in r.stderr and "BM25" in r.stderr


def test_health_empty_qdrant_fails(tmp_path):
    h = json.loads(json.dumps(DEEP))
    h["databases"]["qdrant"]["points"] = 0
    r = _health(tmp_path, {f"GET {GW}/health": [200, json.dumps(h)]})
    assert r.returncode != 0 and "qdrant" in r.stderr.lower()


def test_health_without_deep_block_fails(tmp_path):
    r = _health(tmp_path, {f"GET {GW}/health": [200, json.dumps({"status": "ok", "llm": True})]})
    assert r.returncode != 0 and "BM25" in r.stderr


def test_health_http_500_fails(tmp_path):
    r = _health(tmp_path, {f"GET {GW}/health": [500, "boom"]})
    assert r.returncode != 0 and "/health" in r.stderr and "500" in r.stderr


def test_health_service_unhealthy_and_missing_profile_service(tmp_path):
    r = _health(tmp_path, ps=_ps(es={"Health": "unhealthy"}), COMPOSE_PROFILES="exploit")
    assert r.returncode != 0
    assert "es" in r.stderr and "unhealthy" in r.stderr and "vm-a" in r.stderr and "vm-b" in r.stderr


def test_health_service_ok_for_active_profiles(tmp_path):
    r = _health(tmp_path, ps=_ps(CORE + ["vm-a", "vm-b", "report-backend"]), COMPOSE_PROFILES="exploit, report")
    assert r.returncode == 0, r.stderr


# ---- env / all ----
def test_env_wraps_preflight(tmp_path):
    d = _stack_copy(tmp_path)   # không có .env
    r = subprocess.run(["bash", str(d / "check.sh"), "env"], text=True, capture_output=True, env={**os.environ, "STACK_DIR": str(d), "STACK_ENV": ""})
    assert r.returncode != 0 and "init" in r.stderr


def test_all_stops_at_first_failing_stage(tmp_path):
    d = _stack_copy(tmp_path)
    r = subprocess.run(["bash", str(d / "check.sh"), "all"], text=True, capture_output=True, env={**os.environ, "STACK_DIR": str(d), "STACK_ENV": ""})
    assert r.returncode != 0 and "env" in r.stderr
    assert "ok: config" not in r.stdout and "models" not in r.stdout   # không chạy tiếp
