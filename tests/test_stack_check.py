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
code, body, *rc = json.load(open(os.environ["STUB_ROUTES"])).get(key, [404, '{"error":"no route %s"}' % key])
sys.stdout.write(body + "\\n" + str(code))
sys.stdout.flush()
sys.exit(rc[0] if rc else 0)   # rc 28 = curl quá thời gian (write-out vẫn in mã + phần thân đã nhận)
'''
DOCKER_STUB = '''#!/bin/sh
printf '%s\\n' "$*" >> "$DOCKER_LOG"
case " $* " in *" exec "*) for a in "$@"; do last=$a; done; svc=$(printf ' %s ' "$*" | sed -E 's/.* -T ([^ ]+) .*/\\1/'); case " ${DOCKER_EXEC_FAIL:-} " in *" $svc:$last "*) echo "stub: connection refused" >&2; exit 1 ;; esac; exit 0 ;; *" ps "*) cat "$DOCKER_PS" ;; *" config "*) [ -z "${DOCKER_CONFIG_OUT:-}" ] || { cat "$DOCKER_CONFIG_OUT"; exit 0; }; echo "stub docker: config không được stub" >&2; exit 9 ;; *) echo "stub docker: chỉ cho phép ps" >&2; exit 9 ;; esac
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


def _run(tmp_path, sub, routes=None, env=None, ps=None, stack=None, ps_raw=None, docker_config=None):
    b = _stub(tmp_path, "curl", CURL_STUB)
    _stub(tmp_path, "docker", DOCKER_STUB)
    (tmp_path / "routes.json").write_text(json.dumps(routes or {}))
    (tmp_path / "ps.json").write_text(ps_raw if ps_raw is not None else "\n".join(json.dumps(r) for r in (ps or [])))
    if docker_config is not None:
        (tmp_path / "dcfg.out").write_text(docker_config)
        env = {**(env or {}), "DOCKER_CONFIG_OUT": str(tmp_path / "dcfg.out")}
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
def _health(tmp_path, routes=None, ps=None, ps_raw=None, **env):
    return _run(tmp_path, "health", {**HEALTH_ROUTES, **(routes or {})}, {"GATEWAY_URL": GW, **env}, ps if ps is not None else _ps(), ps_raw=ps_raw)


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


# ================= fix round 1 =================
def _parse_cfg(cfg):
    """Tệp cấu hình curl stdin -> (method, url, headers, body dict|None); giải escape \\x như curl."""
    un = lambda t: __import__("re").sub(r"\\(.)", r"\1", t)
    g = lambda k: [un(m) for m in __import__("re").findall(r'^' + k + r' = "(.*)"$', cfg, __import__("re").M)]
    data = g("data")
    return g("request")[0], g("url")[0], g("header"), (json.loads(data[0]) if data else None)


def _calls(tmp_path):
    return [_parse_cfg(json.loads(l)["cfg"]) for l in (tmp_path / "curl.log").read_text().splitlines()]


# ---- IMPORTANT 1: parser lỗi không bao giờ thành pass ----
@pytest.mark.parametrize("out", ["this is not json", "[1]", "{}", '{"services": 5}', ""])
def test_config_bad_render_is_parser_error_not_ok(tmp_path, out):
    r = _run(tmp_path, "config", docker_config=out)   # docker stub: rc 0 + đầu ra rác cho `config`
    assert r.returncode != 0 and "parser error" in r.stderr and "config: OK" not in r.stdout and "ok: config" not in r.stdout


@pytest.mark.parametrize("body", ["[1]", "not json", '"str"', "null"])
def test_health_non_object_json_is_parser_error(tmp_path, body):
    r = _health(tmp_path, {f"GET {GW}/health": [200, body]})
    assert r.returncode != 0 and "/health" in r.stderr and "parser error" in r.stderr
    assert "ok: health: /health" not in r.stdout


def test_health_sessions_non_object_is_parser_error(tmp_path):
    r = _health(tmp_path, {f"POST {GW}/sessions": [200, "[1]"]})
    assert r.returncode != 0 and "parser error" in r.stderr and "POST /sessions" in r.stderr


def test_health_deep_block_wrong_shapes_fail_not_crash(tmp_path):
    h = {"llm": True, "serving": [1], "databases": "x"}
    r = _health(tmp_path, {f"GET {GW}/health": [200, json.dumps(h)]})
    assert r.returncode != 0 and "BM25" in r.stderr and "Traceback" not in r.stderr
    h = {"llm": True, "serving": {"embedding": 1, "reranker": None}, "databases": {"qdrant": "x", "neo4j": []}}
    r = _health(tmp_path, {f"GET {GW}/health": [200, json.dumps(h)]})
    assert r.returncode != 0 and "embedding" in r.stderr and "qdrant" in r.stderr.lower()


def test_health_sse_non_dict_frame_is_ignored_and_final_still_judged(tmp_path):
    sse = "event: token\ndata: [1]\n\nevent: final\ndata: " + json.dumps({"text": CHAT}) + "\n\nevent: x\ndata: 5\n\ndata: [DONE]\n\n"
    assert _health(tmp_path, {f"POST {GW}/sessions/s1/chat/stream": [200, sse]}).returncode == 0
    only_bad = "event: final\ndata: [1]\n\nevent: token\ndata: \"s\"\n\ndata: [DONE]\n\n"
    r = _health(tmp_path, {f"POST {GW}/sessions/s1/chat/stream": [200, only_bad]})
    assert r.returncode != 0 and "thiếu dấu hiệu" in r.stderr and "Traceback" not in r.stderr


@pytest.mark.parametrize("raw", ["Error: boom not json", "[1]", '{"Service":"gateway"}\n[2]', "[\n"])
def test_health_ps_bad_output_is_parser_error(tmp_path, raw):
    r = _health(tmp_path, ps_raw=raw)
    assert r.returncode != 0 and "parser error" in r.stderr and "docker compose ps" in r.stderr
    assert "service chạy tốt" not in r.stdout


def test_health_ps_empty_means_services_missing(tmp_path):
    r = _health(tmp_path, ps_raw="")
    assert r.returncode != 0 and "gateway chưa chạy" in r.stderr


def test_models_ok_body_wrong_shape_is_parser_error(tmp_path):
    r = _run(tmp_path, "models", {**OK_ROUTES, f"GET {LLM}/models": [200, "[1]"]}, MODEL_ENV)
    assert r.returncode != 0 and "parser error" in r.stderr and "LLM" in r.stderr
    r = _run(tmp_path, "models", {**OK_ROUTES, f"POST {EMB}/v1/embeddings": [200, "not json"]}, MODEL_ENV)
    assert r.returncode != 0 and "parser error" in r.stderr and "embedding" in r.stderr


def test_missing_tools_fail_up_front(tmp_path):
    bash = shutil.which("bash")
    empty = tmp_path / "empty"
    empty.mkdir()
    for sub in ("models", "health", "all", "config"):
        r = subprocess.run([bash, str(STACK / "check.sh"), sub], text=True, capture_output=True, env={"PATH": str(empty), "STACK_ENV": "/dev/null"})
        assert r.returncode != 0 and ("python3" in r.stderr), (sub, r.stderr)
    only_py = tmp_path / "onlypy"
    only_py.mkdir()
    (only_py / "python3").symlink_to(shutil.which("python3"))
    r = subprocess.run([bash, str(STACK / "check.sh"), "models"], text=True, capture_output=True, env={"PATH": str(only_py), "STACK_ENV": "/dev/null"})
    assert r.returncode != 0 and "curl" in r.stderr


# ---- MINOR 2: nội dung cấu hình curl gửi đi ----
def test_models_sends_expected_headers_and_bodies(tmp_path):
    assert _run(tmp_path, "models", OK_ROUTES, MODEL_ENV).returncode == 0
    calls = {(m, u): (h, b) for m, u, h, b in _calls(tmp_path)}
    bearer = f"Authorization: Bearer {FAKE}"
    h, b = calls[("GET", f"{LLM}/models")]
    assert bearer in h and b is None
    h, b = calls[("POST", f"{EMB}/v1/embeddings")]
    assert bearer in h and "Content-Type: application/json" in h
    assert b == {"model": "DuyTa/sec-embedding", "input": "healthcheck"}
    h, b = calls[("POST", f"{RR}/v1/rerank")]
    assert bearer in h and "Content-Type: application/json" in h
    assert b == {"model": "DuyTa/sec-rerank", "query": "healthcheck", "documents": ["healthcheck"], "top_n": 1}


def test_health_sends_json_bodies_and_no_auth(tmp_path):
    assert _health(tmp_path).returncode == 0
    calls = _calls(tmp_path)
    sess = next(c for c in calls if c[1].endswith("/sessions"))
    chat = next(c for c in calls if c[1].endswith("/chat/stream"))
    assert "Content-Type: application/json" in sess[2] and sess[3]["user_id"].startswith("check-")
    assert chat[3]["message"].startswith("CVE-2021-44228 là lỗ hổng gì?") and chat[3]["user_id"] == sess[3]["user_id"]
    assert all(not any(x.startswith("Authorization") for x in c[2]) for c in calls)


def test_real_curl_delivers_key_byte_exact(tmp_path):
    """curl THẬT + http.server loopback: khoá có `"`, `\\`, `#` đến nơi nguyên vẹn qua `curl -K -`."""
    if shutil.which("curl") is None:
        pytest.skip("curl không có")
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer
    seen = []

    class H(BaseHTTPRequestHandler):
        def _send(self, obj):
            b = json.dumps(obj).encode()
            self.send_response(200); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)

        def do_GET(self):
            seen.append(("GET", self.path, self.headers.get("Authorization"), None)); self._send({"data": [{"id": "qwen-m"}]})

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
            seen.append(("POST", self.path, self.headers.get("Authorization"), body))
            self._send({"data": [{"embedding": [0.1]}]} if self.path.endswith("embeddings") else {"results": [{"index": 0}]})

        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    key = 'ab"c\\d#e f'
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    env = {**os.environ, "STACK_ENV": "/dev/null", **{k: v for k, v in MODEL_ENV.items()},
           "QWEN_BASE_URL": base + "/v1", "EMBEDDING_API_URL": base, "RERANKER_API_URL": base,
           "QWEN_API_KEY": key, "EMBEDDING_API_KEY": key, "RERANKER_API_KEY": key}
    try:
        r = subprocess.run(["bash", str(STACK / "check.sh"), "models"], text=True, capture_output=True, env=env, timeout=60)
    finally:
        srv.shutdown()
    assert r.returncode == 0, r.stderr
    assert [s[2] for s in seen] == [f"Bearer {key}"] * 3 and len(seen) == 3
    assert seen[1][3]["model"] == "DuyTa/sec-embedding" and seen[2][3]["documents"] == ["healthcheck"]


# ---- MINOR 3: âm tính ----
@pytest.mark.parametrize("code", [401, 403])
def test_models_key_rejected_all_three(tmp_path, code):
    routes = {k: [code, '{"error":"unauthorized"}'] for k in OK_ROUTES}
    r = _run(tmp_path, "models", routes, MODEL_ENV)
    assert r.returncode != 0
    for var in ("QWEN_API_KEY", "EMBEDDING_API_KEY", "RERANKER_API_KEY"):
        assert var in r.stderr and f"HTTP {code}" in r.stderr
    _no_leak(tmp_path, r)


def test_models_200_with_error_json_fails(tmp_path):
    bad = [200, '{"error": {"message": "internal failure"}}']
    r = _run(tmp_path, "models", {**OK_ROUTES, f"POST {EMB}/v1/embeddings": bad, f"POST {RR}/v1/rerank": bad}, MODEL_ENV)
    assert r.returncode != 0 and "EMBEDDING_MODEL" in r.stderr and "RERANKER_MODEL" in r.stderr and "ok: models: embedding" not in r.stdout


def test_models_200_empty_data_fails(tmp_path):
    routes = {**OK_ROUTES, f"GET {LLM}/models": [200, '{"data": []}'], f"POST {EMB}/v1/embeddings": [200, '{"data": []}'],
              f"POST {RR}/v1/rerank": [200, '{"results": []}']}
    r = _run(tmp_path, "models", routes, MODEL_ENV)
    assert r.returncode != 0 and r.stderr.count("FAIL") == 3
    assert "QWEN_MODEL" in r.stderr and "DuyTa/sec-embedding" in r.stderr and "DuyTa/sec-rerank" in r.stderr


@needs_docker
def test_all_stops_at_config_stage_when_env_passes(tmp_path):
    d = _stack_copy(tmp_path)
    (d / "bootstrap.sh").write_text("#!/usr/bin/env bash\necho preflight stub OK >&2\n")   # env qua
    c = d / "compose.yml"
    c.write_text(c.read_text().replace("  qdrant:\n    image: qdrant/qdrant:v1.10.1\n", "  qdrant:\n    image: qdrant/qdrant:v1.10.1\n    container_name: fixed\n", 1))
    r = subprocess.run(["bash", str(d / "check.sh"), "all"], text=True, capture_output=True,
                       env={**os.environ, "STACK_DIR": str(d), "STACK_ENV": "/dev/null", "NEO4J_PASSWORD": FAKE})
    assert r.returncode == 1
    assert "== env ==" in r.stdout and "== config ==" in r.stdout and "== models ==" not in r.stdout
    assert "giai đoạn 'config' trượt" in r.stderr and FAKE not in r.stdout + r.stderr


# ---- MINOR 5: curl quá thời gian giữa luồng SSE ----
def test_chat_stream_timeout_reports_timeout_not_missing_markers(tmp_path):
    partial = 'event: token\ndata: {"delta": "Log4"}\n\n'
    r = _health(tmp_path, {f"POST {GW}/sessions/s1/chat/stream": [200, partial, 28]}, CHECK_CHAT_TIMEOUT="7")
    assert r.returncode != 0 and "quá thời gian 7 s" in r.stderr and "thiếu dấu hiệu" not in r.stderr


def test_models_and_health_timeout_rc28(tmp_path):
    r = _run(tmp_path, "models", {**OK_ROUTES, f"POST {EMB}/v1/embeddings": [200, "", 28]}, MODEL_ENV)
    assert r.returncode != 0 and "quá thời gian" in r.stderr and "EMBEDDING_MODEL" in r.stderr
    r = _health(tmp_path, {f"GET {GW}/health": [200, "{}", 28]})
    assert r.returncode != 0 and "quá thời gian" in r.stderr


# ---- URL trong thông báo không lộ user:pass@ hay query ----
def test_failure_messages_strip_url_credentials_and_query(tmp_path):
    base = "http://u:PWSECRET@llm/v1?tok=QSECRET"
    r = _run(tmp_path, "models", {f"GET {base}/models": ["000", ""]}, {**MODEL_ENV, "QWEN_BASE_URL": base})
    assert r.returncode != 0 and "llm/v1" in r.stderr and "PWSECRET" not in r.stderr and "QSECRET" not in r.stderr
    r = _health(tmp_path, {f"GET http://u:PWSECRET@gw:8131/health": [500, ""]}, GATEWAY_URL="http://u:PWSECRET@gw:8131")
    assert r.returncode != 0 and "gw:8131" in r.stderr and "PWSECRET" not in r.stderr


# ================= Task 8d: sidecar specialist-tunnel =================
@needs_docker
@pytest.mark.parametrize("svc,mode", [("qdrant", "host"), ("specialist-tunnel", "host"), ("review-frontend", "service:translation-gateway")])
def test_config_flags_network_mode_outside_tunnel_rule(tmp_path, svc, mode):
    # Chỉ specialist-tunnel và translation-worker được chung netns với translation-gateway; host/khác service luôn cấm.
    d = _stack_copy(tmp_path)
    c = d / "compose.yml"
    t = c.read_text()
    if svc == "specialist-tunnel":
        t = t.replace("    network_mode: service:translation-gateway\n    depends_on:\n      translation-gateway: { condition: service_started, restart: true }\n    # Chỉ biến",
                      "    network_mode: host\n    # Chỉ biến", 1)
    else:
        anchor = {"qdrant": "  qdrant:\n    image: qdrant/qdrant:v1.10.1\n",
                  "review-frontend": "  review-frontend:\n"}[svc]
        t = t.replace(anchor, anchor + f"    network_mode: {mode}\n", 1)
        if svc == "qdrant":
            t = t.replace("    networks: [secai-net]\n    healthcheck:\n      test: [\"CMD-SHELL\", \"bash -c 'echo > /dev/tcp/localhost/6333'", "    healthcheck:\n      test: [\"CMD-SHELL\", \"bash -c 'echo > /dev/tcp/localhost/6333'", 1)
        else:
            t = t.replace("      VITE_GATEWAY_PROXY: http://gateway:8131\n    ports:\n      - \"${REVIEW_BIND:-127.0.0.1}:${REVIEW_HOST_PORT:-5174}:5174\"\n    networks: [secai-net]\n",
                          "      VITE_GATEWAY_PROXY: http://gateway:8131\n", 1)
    assert t != c.read_text()
    c.write_text(t)
    r = _config(tmp_path, d)
    assert r.returncode != 0 and "network_mode" in r.stderr and "FAIL" in r.stderr, r.stdout + r.stderr


TUNNEL_PS = CORE + ["translation-gateway", "translation-worker", "translation-converter", "specialist-tunnel"]


def _docker_calls(tmp_path):
    return (tmp_path / "docker.log").read_text().splitlines()


def test_health_tunnel_forward_checked_in_gateway_and_worker_netns(tmp_path):
    r = _health(tmp_path, ps=_ps(TUNNEL_PS), COMPOSE_PROFILES="translation", TUNNEL_MODE="ssh",
                TRANSLATE_V2_OCR_BASE_URL="http://127.0.0.1:18083")
    assert r.returncode == 0, r.stderr
    ex = [l for l in _docker_calls(tmp_path) if " exec " in f" {l} "]
    for svc in ("translation-gateway", "translation-worker"):
        for port in ("18868", "18083"):
            assert any(f"-T {svc} " in l and l.endswith(port) for l in ex), (svc, port, ex)
    assert "specialist-tunnel" in r.stdout


def test_health_tunnel_not_forwarding_fails_naming_port_and_service(tmp_path):
    r = _health(tmp_path, ps=_ps(TUNNEL_PS), COMPOSE_PROFILES="translation", TUNNEL_MODE="ssh",
                DOCKER_EXEC_FAIL="translation-worker:18868")
    assert r.returncode != 0
    assert "18868" in r.stderr and "translation-worker" in r.stderr and "tunnel" in r.stderr


def test_health_ocr_not_probed_when_ocr_url_is_not_loopback(tmp_path):
    r = _health(tmp_path, ps=_ps(TUNNEL_PS), COMPOSE_PROFILES="translation", TUNNEL_MODE="direct",
                TRANSLATE_V2_OCR_BASE_URL="http://10.0.0.7:18083")
    assert r.returncode == 0, r.stderr
    ex = [l for l in _docker_calls(tmp_path) if " exec " in f" {l} "]
    assert ex and not any(l.endswith("18083") for l in ex)


def test_health_tunnel_off_skips_probe_and_sidecar(tmp_path):
    r = _health(tmp_path, ps=_ps(TUNNEL_PS[:-1]), COMPOSE_PROFILES="translation", TUNNEL_MODE="off")
    assert r.returncode == 0, r.stderr
    assert not [l for l in _docker_calls(tmp_path) if " exec " in f" {l} "]


def test_health_tunnel_sidecar_required_when_on(tmp_path):
    r = _health(tmp_path, ps=_ps(TUNNEL_PS[:-1]), COMPOSE_PROFILES="translation", TUNNEL_MODE="ssh")
    assert r.returncode != 0 and "specialist-tunnel" in r.stderr
