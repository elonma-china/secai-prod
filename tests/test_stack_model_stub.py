"""Model stub LLM + embedding + rerank CHỈ ĐỂ TEST (stack/acceptance/stubs/model_stub.py).

Chạy stub thật trong subprocess (`python -I`, cổng 0) rồi gọi bằng urllib và bằng chính client của gateway
(linch/OpenAI SDK, retrieval.embedding, prompt builder thật), nên hợp đồng không lệch với src/.
Khoá trong test là marker giả: chứng minh khoá và nội dung request không lọt ra log hay phản hồi.
"""
import asyncio
import json
import math
import os
import re
import select
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

STUB = Path(__file__).resolve().parents[1] / "stack/acceptance/stubs/model_stub.py"
KEY = "FAKE-STUB-KEY-MARKER-7f3a9c"
LLM, EMB, RR = "DuyTa/Cyber-F1-AWQ", "DuyTa/sec-embedding", "DuyTa/sec-rerank"
BODY_MARKER = "BODY-LEAK-MARKER-41d2"
SYSTEM_LOG4SHELL = ("<system_prompt>\nBạn là trợ lý ATTT. Ví dụ: cách vá Log4Shell? CVSS tối đa 10.0. "
                    "SYSTEM-PROMPT-MARKER-9b1e\n</system_prompt>")
KB_HITS = ("### Result 1 (score: 1.000)\n**Type:** core\n**CVE:** CVE-2021-44228\n**Title:** Apache Log4j2 JNDI\n\n"
           "Apache Log4j2 2.0-beta9 through 2.15.0 bị ảnh hưởng. From version 2.16.0 tính năng bị gỡ. KB-DATA-MARKER-c3")
CHAT_TOOLS = [{"type": "function", "function": {"name": "qdrant_search", "description": "TOOL-SCHEMA-MARKER-5e",
                                                 "parameters": {"type": "object", "properties": {}}}}]
FORBIDDEN_CANNED = ("10.0.0", "172.16.0", "Connection error", "ProviderError", "lỗi kết nối")
CVSS_NUM = re.compile(r"CVSS[^\n]{0,30}\d+\.\d")


class Server:
    def __init__(self, proc, url, err_path):
        self.proc, self.url, self.err_path = proc, url, err_path

    def stderr(self):
        return self.err_path.read_text(encoding="utf-8", errors="replace")


def _start(tmp_path, env_extra=None, key=KEY):
    env = {"PATH": os.environ["PATH"], **({"STUB_API_KEY": key} if key is not None else {}), **(env_extra or {})}
    err_path = tmp_path / f"stub-{len(list(tmp_path.glob('stub-*.err')))}.err"
    with open(err_path, "wb") as err:
        proc = subprocess.Popen([sys.executable, "-I", str(STUB), "--host", "127.0.0.1", "--port", "0"],
                                stdout=subprocess.PIPE, stderr=err, text=True, env=env)
    ready = select.select([proc.stdout], [], [], 15)[0]
    line = proc.stdout.readline() if ready else ""
    m = re.fullmatch(r"model_stub listening on http://127\.0\.0\.1:(\d+)\n", line)
    if not m:
        proc.kill()
        raise AssertionError(f"stub không lên: {line!r} {err_path.read_text(errors='replace')[-500:]}")
    assert int(m.group(1)) > 0   # --port 0 in ra cổng thật đã bind
    return Server(proc, f"http://127.0.0.1:{m.group(1)}", err_path)


def _stop(srv):
    srv.proc.terminate()
    try:
        srv.proc.wait(5)
    except subprocess.TimeoutExpired:
        srv.proc.kill()
        srv.proc.wait(5)
    srv.proc.stdout.close()


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    srv = _start(tmp_path_factory.mktemp("model-stub"))
    yield srv
    _stop(srv)


@pytest.fixture
def start(tmp_path):
    started = []

    def go(**kw):
        started.append(_start(tmp_path, **kw))
        return started[-1]
    yield go
    for s in started:
        _stop(s)


def call(base, method, path, body=None, key=KEY, raw=None):
    data = json.dumps(body, ensure_ascii=False).encode() if body is not None else raw
    req = urllib.request.Request(base + path, data=data, method=method)
    if key is not None:
        req.add_header("Authorization", f"Bearer {key}")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, resp.headers, resp.read()
    except urllib.error.HTTPError as e:
        with e:
            return e.code, e.headers, e.read()


def chat_body(messages, **kw):
    return {"model": LLM, "messages": messages, **kw}


def answer(base, messages, **kw):
    code, _, raw = call(base, "POST", "/v1/chat/completions", chat_body(messages, **kw))
    assert code == 200, raw
    j = json.loads(raw)
    assert j["object"] == "chat.completion" and j["choices"][0]["finish_reason"] == "stop"
    return j["choices"][0]["message"]["content"]


def user(text):
    return {"role": "user", "content": [{"type": "text", "text": text}]}


def context_note(grounding=None):
    from secai_gateway.agent.runner import _ephemeral_context_text
    return _ephemeral_context_text("chat", grounding)


def kb_grounding(hits=KB_HITS):
    from secai_gateway.agent.hooks import CHAT_PRE_FETCH_MARKER
    from secai_gateway.api.routes import CVE_KB_CONTEXT_NOTE
    return CHAT_PRE_FETCH_MARKER + CVE_KB_CONTEXT_NOTE + hits


def parse_sse(raw):
    text = raw.decode("utf-8")
    assert text.endswith("\n\n")
    events = [e for e in text.split("\n\n") if e]
    assert all(e.startswith("data: ") and "\n" not in e for e in events), events
    assert events[-1] == "data: [DONE]"
    return [json.loads(e[len("data: "):]) for e in events[:-1]]


def test_prompt_markers_match_gateway_source(monkeypatch):
    """Stub nhận loại request qua dấu hiệu trong prompt của gateway; src/ đổi dấu hiệu thì test này đỏ trước."""
    import importlib.util

    import secai_gateway
    from secai_gateway.agent.hooks import CHAT_PRE_FETCH_MARKER
    from secai_gateway.security import wrap_untrusted_content
    monkeypatch.setattr(sys, "dont_write_bytecode", True)

    def load(name, path):
        spec = importlib.util.spec_from_file_location(name, path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    m = load("model_stub_under_test", STUB)
    assert m.WORDS == load("specialist_stub_under_test", STUB.parent / "specialist_stub.py").WORDS   # bảng từ chép, không import
    src = Path(secai_gateway.__file__).parent
    routes = (src / "api/routes.py").read_text(encoding="utf-8")
    assert m.KB_MARK == CHAT_PRE_FETCH_MARKER
    assert wrap_untrusted_content("x", source="translate_input") == f"{m.TR_OPEN}\nx\n{m.TR_CLOSE}"
    for mark in (m.HEADINGS_ASK, m.REPORT_MARK, m.SECTIONS_MARK, m.REPORT_END.strip()):
        assert mark in routes
    assert m.JUDGE_SYSTEM in (src / "prompts/injection_judge.md").read_text(encoding="utf-8")
    note = context_note(kb_grounding())
    assert note.startswith(m.NOTE_OPEN) and m.NOTE_CLOSE in note and m.KB_MARK in note


# ── vòng đời, health, models, xác thực ──

def test_health_needs_no_key_and_listening_line(server):
    code, headers, raw = call(server.url, "GET", "/health", key=None)
    assert code == 200 and json.loads(raw) == {"ok": True}
    assert headers["Content-Length"] == str(len(raw))


def test_models_lists_only_configured_llm(server):
    for path in ("/v1/models", "//v1//models", "/v1/models/"):
        code, headers, raw = call(server.url, "GET", path)
        assert code == 200, path
        assert json.loads(raw) == {"object": "list", "data": [
            {"id": LLM, "object": "model", "created": 0, "owned_by": "stub", "max_model_len": 131072}]}
        assert headers["Content-Length"] == str(len(raw))


@pytest.mark.parametrize("method,path,body", [
    ("GET", "/v1/models", None),
    ("POST", "/v1/chat/completions", {"model": LLM, "messages": [{"role": "user", "content": "hi"}]}),
    ("POST", "/v1/embeddings", {"model": EMB, "input": "x"}),
    ("POST", "/v1/rerank", {"model": RR, "query": "x", "documents": ["x"]}),
])
def test_requests_without_or_with_wrong_key_get_401(server, method, path, body):
    for key in (None, "sai-khoa", KEY + "x", ""):
        code, _, raw = call(server.url, method, path, body, key=key)
        assert code == 401, (path, key)
        assert KEY not in raw.decode() and "sai-khoa" not in raw.decode()
    req = urllib.request.Request(server.url + path, method=method,
                                 data=json.dumps(body).encode() if body else None,
                                 headers={"Authorization": f"Basic {KEY}"})
    with pytest.raises(urllib.error.HTTPError) as ei:
        urllib.request.urlopen(req, timeout=10)
    assert ei.value.code == 401
    ei.value.close()


def test_default_key_when_env_unset(start):
    srv = start(key=None)
    assert call(srv.url, "GET", "/v1/models", key="stub-test-key-not-secret")[0] == 200
    assert call(srv.url, "GET", "/v1/models", key=KEY)[0] == 401


@pytest.mark.parametrize("path,body", [
    ("/v1/chat/completions", {"model": EMB, "messages": [{"role": "user", "content": "hi"}]}),
    ("/v1/embeddings", {"model": LLM, "input": "x"}),
    ("/v1/rerank", {"model": EMB, "query": "x", "documents": ["x"]}),
    ("/v1/embeddings", {"input": "x"}),
])
def test_wrong_model_for_role_is_404(server, path, body):
    code, _, raw = call(server.url, "POST", path, body)
    assert code == 404
    assert json.loads(raw) == {"error": {"message": f"model '{body.get('model', '')}' not found"}}


def test_model_names_come_from_env(start):
    srv = start(env_extra={"STUB_LLM_MODEL": "llm-x", "STUB_EMBEDDING_MODEL": "emb-x", "STUB_RERANK_MODEL": "rr-x",
                           "STUB_EMBED_DIM": "8"})
    assert json.loads(call(srv.url, "GET", "/v1/models")[2])["data"][0]["id"] == "llm-x"
    assert call(srv.url, "POST", "/v1/chat/completions", chat_body([user("hi")]))[0] == 404
    assert call(srv.url, "POST", "/v1/chat/completions", {"model": "llm-x", "messages": [user("hi")]})[0] == 200
    code, _, raw = call(srv.url, "POST", "/v1/embeddings", {"model": "emb-x", "input": "x"})
    assert code == 200 and len(json.loads(raw)["data"][0]["embedding"]) == 8
    assert call(srv.url, "POST", "/v1/rerank", {"model": "rr-x", "query": "a", "documents": ["a"]})[0] == 200


def test_bad_requests(server):
    assert call(server.url, "POST", "/v1/chat/completions", raw=b"{not json")[0] == 400
    assert call(server.url, "POST", "/v1/chat/completions", raw=b"[1, 2]")[0] == 400
    assert call(server.url, "POST", "/v1/chat/completions", {"model": LLM, "messages": "x"})[0] == 400
    assert call(server.url, "POST", "/v1/embeddings", {"model": EMB, "input": [1, 2]})[0] == 400
    assert call(server.url, "POST", "/v1/rerank", {"model": RR, "query": "x", "documents": "x"})[0] == 400
    assert call(server.url, "POST", "/v1/unknown", {"model": LLM})[0] == 404
    assert call(server.url, "GET", "/v1/unknown")[0] == 404


@pytest.mark.parametrize("value", ["abc", "0", "-3"])
def test_invalid_embed_dim_refuses_to_start(value):
    r = subprocess.run([sys.executable, "-I", str(STUB), "--port", "0"], capture_output=True, text=True, timeout=15,
                       env={"PATH": os.environ["PATH"], "STUB_EMBED_DIM": value})
    assert r.returncode == 2 and "STUB_EMBED_DIM" in r.stderr and "Traceback" not in r.stderr


def test_help_shows_defaults():
    r = subprocess.run([sys.executable, "-I", str(STUB), "--help"], capture_output=True, text=True, timeout=15,
                       env={"PATH": os.environ["PATH"]})
    assert r.returncode == 0 and "127.0.0.1" in r.stdout and "18000" in r.stdout and "CHỈ ĐỂ TEST" in r.stdout


# ── embeddings, rerank ──

def _embed(base, inp, model=EMB):
    code, _, raw = call(base, "POST", "/v1/embeddings", {"model": model, "input": inp})
    assert code == 200, raw
    return json.loads(raw)


def test_embeddings_deterministic_unit_norm_1024(server):
    a = _embed(server.url, "healthcheck")
    assert a["object"] == "list" and a["model"] == EMB and a["usage"]["prompt_tokens"] >= 1
    (item,) = a["data"]
    vec = item["embedding"]
    assert item["object"] == "embedding" and item["index"] == 0 and len(vec) == 1024
    assert abs(math.sqrt(sum(x * x for x in vec)) - 1.0) < 1e-9
    assert _embed(server.url, "healthcheck")["data"][0]["embedding"] == vec   # tất định
    pair = _embed(server.url, ["healthcheck", "CVE-2021-44228"])["data"]
    assert [d["index"] for d in pair] == [0, 1] and pair[0]["embedding"] == vec
    assert pair[1]["embedding"] != vec and len(pair[1]["embedding"]) == 1024


def test_embedding_matches_spec_formula(server):
    import hashlib
    raw = [b / 127.5 - 1 for blk in range(32) for b in hashlib.sha256(f"{blk}:abc".encode()).digest()][:1024]
    n = math.sqrt(sum(x * x for x in raw))
    got = _embed(server.url, "abc")["data"][0]["embedding"]
    assert max(abs(g - x / n) for g, x in zip(got, raw)) < 1e-12


def test_rerank_orders_by_overlap_and_honours_top_n(server):
    docs = ["không liên quan gì", {"text": "Apache Log4j2 remote code"}, "log4j2 apache"]
    code, _, raw = call(server.url, "POST", "/v1/rerank", {"model": RR, "query": "Apache Log4j2 RCE", "documents": docs})
    assert code == 200
    res = json.loads(raw)["results"]
    assert [r["index"] for r in res] == [1, 2, 0]
    assert res[0]["relevance_score"] == pytest.approx(2 / 3 * 0.98) and res[2]["relevance_score"] == 0
    assert res[0]["document"] == {"text": "Apache Log4j2 remote code"} and res[1]["document"] == {"text": "log4j2 apache"}
    top = json.loads(call(server.url, "POST", "/v1/rerank",
                          {"model": RR, "query": "Apache Log4j2 RCE", "documents": docs, "top_n": 1})[2])["results"]
    assert [r["index"] for r in top] == [1]
    one = json.loads(call(server.url, "POST", "/v1/rerank", {"model": RR, "query": "healthcheck",
                                                              "documents": ["healthcheck"], "top_n": 1})[2])
    assert one["results"] == [{"index": 0, "relevance_score": pytest.approx(0.98), "document": {"text": "healthcheck"}}]
    empty = json.loads(call(server.url, "POST", "/v1/rerank", {"model": RR, "query": "x", "documents": []})[2])
    assert empty["results"] == []


def test_gateway_embedding_and_rerank_clients(server, monkeypatch):
    import httpx
    from secai_gateway.retrieval import embedding as emb
    monkeypatch.setattr(emb, "EMBEDDING_URL", server.url + "/")   # gateway không bỏ '/' cuối: stub phải gộp '//'
    monkeypatch.setattr(emb, "RERANKER_URL", server.url)
    monkeypatch.setattr(emb, "EMBEDDING_MODEL", EMB)
    monkeypatch.setattr(emb, "RERANKER_MODEL", RR)
    monkeypatch.setattr(emb, "EMBEDDING_API_KEY", KEY)
    monkeypatch.setattr(emb, "RERANKER_API_KEY", KEY)

    async def run():
        async with httpx.AsyncClient() as c:
            return await emb.embed(c, "CVE-2021-44228"), await emb.rerank(c, "log4j2 apache", ["x y", "apache log4j2"], 20)
    vec, res = asyncio.run(run())
    assert len(vec) == 1024 and [r["index"] for r in res] == [1, 0] and res[0]["relevance_score"] == pytest.approx(0.98)


# ── chat/completions ──

def test_chat_non_stream_shape_and_plain_text_with_tools(server):
    code, headers, raw = call(server.url, "POST", "/v1/chat/completions",
                              chat_body([{"role": "system", "content": SYSTEM_LOG4SHELL}, user("Xin chào")],
                                        tools=CHAT_TOOLS, parallel_tool_calls=True))
    assert code == 200 and headers["Content-Length"] == str(len(raw))
    j = json.loads(raw)
    assert j["object"] == "chat.completion" and j["model"] == LLM and isinstance(j["created"], int)
    (choice,) = j["choices"]
    assert choice["finish_reason"] == "stop" and choice["message"]["role"] == "assistant"
    assert "tool_calls" not in choice["message"]
    assert choice["message"]["content"].startswith("[stub-llm]") and "Xin chào" in choice["message"]["content"]
    u = j["usage"]
    assert u["prompt_tokens"] > 0 and u["completion_tokens"] > 0 and u["total_tokens"] == u["prompt_tokens"] + u["completion_tokens"]


def test_chat_stream_sse_chunks(server):
    code, headers, raw = call(server.url, "POST", "/v1/chat/completions",
                              chat_body([user("Xin chào")], stream=True, stream_options={"include_usage": True},
                                        tools=CHAT_TOOLS))
    assert code == 200 and headers["Content-Type"].startswith("text/event-stream")
    chunks = parse_sse(raw)
    assert len(chunks) == 4 and all("error" not in c for c in chunks)
    assert all(c["object"] == "chat.completion.chunk" and c["model"] == LLM for c in chunks)
    for c in chunks[:3]:
        (ch,) = c["choices"]
        assert "delta" in ch and "finish_reason" in ch and ch["index"] == 0
    assert chunks[0]["choices"][0]["delta"] == {"role": "assistant", "content": ""}
    assert chunks[0]["choices"][0]["finish_reason"] is None
    content = chunks[1]["choices"][0]["delta"]["content"]
    assert content.startswith("[stub-llm]") and chunks[1]["choices"][0]["finish_reason"] is None
    assert chunks[2]["choices"][0] == {"index": 0, "delta": {}, "finish_reason": "stop"}
    assert chunks[3]["choices"] == [] and chunks[3]["usage"]["completion_tokens"] > 0
    # không include_usage → không có chunk usage
    no_usage = parse_sse(call(server.url, "POST", "/v1/chat/completions", chat_body([user("Xin chào")], stream=True))[2])
    assert len(no_usage) == 3 and no_usage[-1]["choices"][0]["finish_reason"] == "stop"


def test_gateway_provider_parses_stream(server):
    vllm = pytest.importorskip("linch.providers.vllm")
    from linch.types import Message, ProviderRequest, SystemBlock, TextBlock
    provider = vllm.VLLMProvider(vllm.VLLMProviderOptions(
        api_key=KEY, base_url=server.url + "/v1", parallel_tool_calls=True,
        extra_body={"chat_template_kwargs": {"enable_thinking": False}, "temperature": 0.7, "top_k": 20}))
    req = ProviderRequest(
        model=LLM, system=[SystemBlock(text=SYSTEM_LOG4SHELL)],
        tools=[{"name": "qdrant_search", "description": "TOOL-SCHEMA-MARKER-5e", "input_schema": {"type": "object", "properties": {}}}],
        messages=[Message(role="user", content=[TextBlock(text="CVE-2021-44228 là lỗ hổng gì?")]),
                  Message(role="user", content=[TextBlock(text=context_note(kb_grounding()))])],
        max_output_tokens=16384)

    async def run():
        try:
            return [e async for e in provider.stream(req)]
        finally:
            await provider.aclose()
    events = asyncio.run(run())
    text = "".join(e["text"] for e in events if e["type"] == "text_delta")
    assert events[-1]["type"] == "message_end" and events[-1]["stop_reason"] == "end_turn"
    assert events[-1]["usage"].input_tokens > 0 and events[-1]["usage"].output_tokens > 0
    assert not any(e["type"].startswith("tool_use") for e in events)
    assert "2.16.0" in text and "KB-DATA-MARKER-c3" in text
    assert "Log4Shell" not in text and "SYSTEM-PROMPT-MARKER" not in text and "TOOL-SCHEMA-MARKER" not in text


def test_kb_echo_contains_only_kb_block(server):
    from secai_gateway.agent.profiles import PROFILES, compose_system_prompt
    real_system = compose_system_prompt(PROFILES["chat"])
    assert "Log4Shell" in real_system and "10.0" in real_system   # đúng rủi ro: prompt tĩnh chứa marker của check.sh
    old = context_note(kb_grounding("OLD-KB-MARKER 2.15.0"))
    for system in (SYSTEM_LOG4SHELL, real_system):
        msgs = [{"role": "system", "content": system}, user("CVE-2021-44228 là gì?"), {"role": "assistant", "content": "cũ"},
                user(old), user("CVE-2021-44228 là lỗ hổng gì? Nêu sản phẩm bị ảnh hưởng, điểm CVSS và cách khắc phục."),
                user(context_note(kb_grounding()))]
        text = answer(server.url, msgs, tools=CHAT_TOOLS)
        assert text.startswith("[stub-llm] Trích ngữ cảnh KB:\n\n")
        assert "2.16.0" in text and "KB-DATA-MARKER-c3" in text and "### Result 1" in text
        assert "Log4Shell" not in text and "10.0" not in text and "SYSTEM-PROMPT-MARKER" not in text
        assert "TOOL-SCHEMA-MARKER" not in text and "OLD-KB-MARKER" not in text
        for leaked in ("GHI CHÚ NGỮ CẢNH", "HẾT GHI CHÚ", "Hôm nay là", "PHẢI dùng khối", "Nêu sản phẩm bị ảnh hưởng"):
            assert leaked not in text


def test_kb_not_found_block_is_echoed(server):
    text = answer(server.url, [user("CVE-2099-99999 là lỗ hổng gì?"),
                               user(context_note(kb_grounding("Không tìm thấy kết quả trong knowledge base.")))])
    assert "Không tìm thấy kết quả trong knowledge base." in text and not CVSS_NUM.search(text)


def test_chat_without_kb_quotes_real_question_only(server):
    text = answer(server.url, [{"role": "system", "content": SYSTEM_LOG4SHELL}, user(f"SSRF là gì? {BODY_MARKER}"),
                               user(context_note(None))], tools=CHAT_TOOLS)
    assert text.startswith("[stub-llm]") and f"SSRF là gì? {BODY_MARKER}" in text
    assert "Log4Shell" not in text and "SYSTEM-PROMPT-MARKER" not in text and "Hôm nay là" not in text
    plain = answer(server.url, [{"role": "user", "content": "Câu hỏi dạng chuỗi"}])
    assert "Câu hỏi dạng chuỗi" in plain


def test_injection_judge_returns_parseable_json(server):
    from secai_gateway.agent.profiles import PROFILES, compose_system_prompt
    from secai_gateway.sanitize.cascade import build_judge_prompt
    expected = {"injection": False, "scope": "in_scope", "security": True, "security_requests": [], "reason": "stub"}
    by_system = answer(server.url, [{"role": "system", "content": compose_system_prompt(PROFILES["injection_judge"])},
                                    user("tin nhắn bất kỳ"), user(context_note(None))])
    by_user = answer(server.url, [user(build_judge_prompt("CVE-2021-44228 là gì?", 0.42, history="câu trước")),
                                  user(context_note(None))])
    assert json.loads(by_system) == expected and json.loads(by_user) == expected
    assert by_user == '{"injection": false, "scope": "in_scope", "security": true, "security_requests": [], "reason": "stub"}'


def test_translate_applies_word_map_and_keeps_placeholders(server):
    from secai_gateway.agent.profiles import PROFILES, compose_system_prompt
    from secai_gateway.api.literal_mask import restore_checked
    from secai_gateway.api.messages import build_translate_prompt
    src = ("A remote code execution vulnerability in Apache Log4j2 allows an unauthenticated attacker to run "
           "arbitrary code on the server. Default credentials are admin / P@ssw0rd123.")
    prompt, literals = build_translate_prompt(src, "en", "vi")
    assert literals and "⟦L0⟧" in prompt
    out = answer(server.url, [{"role": "system", "content": compose_system_prompt(PROFILES["translate"])}, user(prompt)])
    assert out.count("⟦L0⟧") == 1 and "untrusted_retrieved_content" not in out and "Dịch văn bản sau" not in out
    assert "lỗ hổng" in out and "máy chủ" in out and "kẻ tấn công" in out and "Apache Log4j2" in out
    final = restore_checked(out, literals)   # đúng kiểm tra toàn vẹn của /ai/translate
    assert "admin / P@ssw0rd123" in final


def test_template_report_freeform_json(server):
    prompt = ("Bạn đóng vai trò ...\n\n=== YÊU CẦU / Ý TƯỞNG CỦA NGƯỜI DÙNG ===\n1. Tổng quan, 2. Chi tiết\n\n"
              "=== DỮ LIỆU QUÉT BẢO MẬT (SCAN DATA) ===\n{\"x\": \"3. Không phải đầu mục\"}\n\nNHIỆM VỤ ...\n"
              "   5. Kết luận và Đề xuất hành động tiếp theo")
    out = answer(server.url, [{"role": "system", "content": "<system_prompt>report</system_prompt>"}, user(prompt),
                              user(context_note(None))])
    j = json.loads(out)
    assert j["document_title"] == "BÁO CÁO ĐÁNH GIÁ AN TOÀN THÔNG TIN"
    assert [s["heading"] for s in j["sections"]] == ["1. Tổng quan", "2. Chi tiết"]
    assert j["sections"][0]["content"].startswith("1.1.") and j["sections"][1]["content"].startswith("2.1.")
    assert j["include_letterhead"] is False and j["include_signature"] is False and "```" not in out


def test_template_sections_json_and_headings_detection(server):
    prompt = ("...\n\n=== DỮ LIỆU QUÉT BẢO MẬT (SCAN DATA) ===\n{}\n\n=== CÁC ĐẦU MỤC TRONG TEMPLATE BÁO CÁO ===\n"
              "1. Thời gian\n2. Tổng quan\nPhụ lục\n\nNHIỆM VỤ CỦA BẠN:\n3. không phải đầu mục")
    j = json.loads(answer(server.url, [user(prompt), user(context_note(None))]))
    assert set(j) == {"sections"}
    assert [s["heading"] for s in j["sections"]] == ["1. Thời gian", "2. Tổng quan", "Phụ lục"]
    assert all(s["content"] for s in j["sections"])
    det = answer(server.url, [user('Đoạn [0]: ...\n\nTrả về DUY NHẤT một khối JSON dạng {"headings": [index1, index2, ...]}')])
    assert json.loads(det) == {"headings": []}


# ── không rò rỉ, không chuỗi gây đạt/trượt giả ──

def test_canned_texts_have_no_false_markers(server):
    outs = [
        answer(server.url, [user("hỏi gì đó"), user(context_note(None))]),
        answer(server.url, [user("Điểm nghi ngờ sơ bộ (bộ phân loại tự động): 0.500")]),
        answer(server.url, [user('{"headings": [')]),
        answer(server.url, [user("=== YÊU CẦU / Ý TƯỞNG CỦA NGƯỜI DÙNG ===\nBáo cáo chung\n\n=== DỮ LIỆU QUÉT")]),
        answer(server.url, [user("=== CÁC ĐẦU MỤC TRONG TEMPLATE BÁO CÁO ===\n1. A\n\n")]),
        answer(server.url, [user('<untrusted_retrieved_content source="translate_input">\nthe server\n</untrusted_retrieved_content>')]),
        answer(server.url, []),
    ]
    source = STUB.read_text(encoding="utf-8")
    for text in [*outs, source]:
        for bad in FORBIDDEN_CANNED:
            assert bad not in text
        assert not CVSS_NUM.search(text)
    assert all("Log4Shell" not in o and "2.16.0" not in o and "2.15.0" not in o for o in outs)


def test_logs_one_line_per_request_without_bodies_or_keys(server):
    before = server.stderr()
    answer(server.url, [user(f"câu hỏi {BODY_MARKER}")])
    call(server.url, "GET", "/v1/models", key="khoa-sai-LEAK-77")
    call(server.url, "GET", "/health", key=None)
    for _ in range(50):
        new = server.stderr()[len(before):]
        if new.count("\n") >= 3:
            break
        time.sleep(0.05)
    lines = new.splitlines()
    assert len(lines) == 3, lines
    assert re.search(r"\bPOST /v1/chat/completions 200$", lines[0])
    assert re.search(r"\bGET /v1/models 401$", lines[1]) and re.search(r"\bGET /health 200$", lines[2])
    log = server.stderr()
    for secret in (KEY, BODY_MARKER, "khoa-sai-LEAK-77", "Bearer", "Authorization"):
        assert secret not in log
