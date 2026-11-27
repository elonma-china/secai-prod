#!/usr/bin/env python3
"""CHỈ ĐỂ TEST. Stub LLM + embedding + rerank (API kiểu OpenAI) cho máy test không có model thật.

Chứng minh ĐƯỜNG ỐNG (gateway gọi đúng URL, khoá, tên model; SSE đúng dạng; dữ liệu KB đi tới câu trả lời),
KHÔNG chứng minh chất lượng model. Không bao giờ dùng thay model thật trên prod.
  chat/completions  chọn câu trả lời theo loại request: judge, báo cáo template, dịch (bảng từ cố định), chat.
                    Chat chỉ trích khối ngữ cảnh KB gateway gửi kèm, không nhắc lại system prompt hay tool schema
                    (prompt tĩnh có sẵn vài marker mà check.sh tìm, nhắc lại sẽ thành đạt giả).
  embeddings        vector tất định từ sha256, STUB_EMBED_DIM chiều, chuẩn hoá L2.
  rerank            điểm = tỉ lệ từ của query có trong tài liệu, nhân 0.98.
Chạy: python3 -I model_stub.py [--host 127.0.0.1] [--port 18000]   (--port 0: tự chọn cổng, dòng đầu in cổng thật)
  STUB_API_KEY          khoá Bearer (mặc định stub-test-key-not-secret: khoá giả, không phải bí mật)
  STUB_LLM_MODEL        mặc định DuyTa/Cyber-F1-AWQ   (= LLM_MODEL trong .env)
  STUB_EMBEDDING_MODEL  mặc định DuyTa/sec-embedding  (= EMBEDDING_MODEL)
  STUB_RERANK_MODEL     mặc định DuyTa/sec-rerank     (= RERANKER_MODEL)
  STUB_EMBED_DIM        mặc định 1024 (phải bằng kích thước vector dense trong Qdrant)
Sai tên model so với vai trò → 404, để check.sh models vẫn bắt được cấu hình lệch. Log mỗi request một dòng ra
stderr (method, path, mã trả về); không bao giờ ghi body hay khoá. Hợp đồng đối chiếu từ src/secai_gateway:
agent/runner.py, agent/hooks.py, api/routes.py, api/messages.py, sanitize/cascade.py, retrieval/embedding.py,
api/health_deps.py. Chỉ stdlib.
"""
import argparse
import hashlib
import hmac
import json
import math
import os
import re
import signal
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

DEFAULT_KEY = "stub-test-key-not-secret"
MAX_BODY = 64 * 1024 * 1024
JUDGE_JSON = '{"injection": false, "scope": "in_scope", "security": true, "security_requests": [], "reason": "stub"}'
HEADINGS_EMPTY = '{"headings": []}'
REPORT_TITLE = "BÁO CÁO ĐÁNH GIÁ AN TOÀN THÔNG TIN"
SECTION_TEXT = "Nội dung mẫu do model stub sinh (CHỈ ĐỂ TEST, không phải kết quả phân tích thật)."

# Dấu hiệu trong prompt của gateway. src/ đổi thì sửa ở đây và tests/test_stack_model_stub.py.
JUDGE_SYSTEM = "<injection_judge_profile>"                    # prompts/injection_judge.md
JUDGE_USER = "Điểm nghi ngờ sơ bộ"                             # sanitize/cascade.py build_judge_prompt
HEADINGS_ASK = '{"headings": ['                                # api/routes.py: dò đầu mục trong tệp template
REPORT_MARK = "=== YÊU CẦU / Ý TƯỞNG CỦA NGƯỜI DÙNG ==="     # api/routes.py: báo cáo tự do (không tệp)
REPORT_END = "\n\n=== DỮ LIỆU QUÉT"
SECTIONS_MARK = "=== CÁC ĐẦU MỤC TRONG TEMPLATE BÁO CÁO ==="  # api/routes.py: báo cáo theo tệp template
TR_OPEN = '<untrusted_retrieved_content source="translate_input">'   # api/messages.py + security.py
TR_CLOSE = "</untrusted_retrieved_content>"
NOTE_OPEN = "[GHI CHÚ NGỮ CẢNH HỆ THỐNG"                     # agent/runner.py _ephemeral_context_text
NOTE_CLOSE = "\n[HẾT GHI CHÚ NGỮ CẢNH"
KB_MARK = "[Ngữ cảnh tra cứu từ KB nội bộ"                   # agent/hooks.py CHAT_PRE_FETCH_MARKER

WORDS = [  # chép từ specialist_stub.py; dài trước ngắn; không đụng token kỹ thuật (Apache Log4j2, CVE-..., phiên bản)
    ("security advisory", "khuyến cáo bảo mật"), ("remote code execution", "thực thi mã từ xa"),
    ("affected versions", "các phiên bản bị ảnh hưởng"), ("arbitrary code", "mã tùy ý"),
    ("vulnerability", "lỗ hổng"), ("unauthenticated", "chưa xác thực"), ("attacker", "kẻ tấn công"),
    ("server", "máy chủ"), ("allows", "cho phép"), ("upgrade", "nâng cấp"), ("mitigation", "biện pháp giảm thiểu"),
    ("detection", "phát hiện"), ("or later", "hoặc mới hơn"), ("in", "trong"), ("to", "để"),
]
_RX = re.compile(r"\b(" + "|".join(re.escape(e) for e, _ in WORDS) + r")\b", re.I)
_MAP = {e: v for e, v in WORDS}
_PLACEHOLDER = re.compile(r"(⟦[^⟧]*⟧)")
_NUMBERED = re.compile(r"\d+\.\s*[^,\n]+")
_WORD = re.compile(r"\w+")


class BadRequest(Exception):
    pass


def text_of(content):
    """Nội dung message: chuỗi, hoặc list part → nối text của part type=text."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(p["text"] for p in content
                         if isinstance(p, dict) and p.get("type") == "text" and isinstance(p.get("text"), str))
    return ""


def count_words(text):
    return len(text.split())


def translate(text):
    # chỉ thay từ ở phần chữ; mọi ⟦…⟧ giữ nguyên, đúng một lần như đầu vào
    parts = _PLACEHOLDER.split(text)
    return "".join(p if i % 2 else _RX.sub(lambda m: _MAP[m.group(0).lower()], p) for i, p in enumerate(parts))


def section(heading, pos):
    m = re.match(r"(\d+)\.", heading)
    return {"heading": heading, "content": f"{m.group(1) if m else pos}.1. {SECTION_TEXT}"}


def numbered(block):
    return [h.strip() for h in _NUMBERED.findall(block) if h.strip()]


def report_answer(text):
    block = text.split(REPORT_MARK, 1)[1].split(REPORT_END, 1)[0]
    heads = numbered(block) or ["1. Nội dung báo cáo"]
    return json.dumps({"document_title": REPORT_TITLE, "include_letterhead": False, "include_salutation": False,
                       "include_signature": False, "sections": [section(h, i) for i, h in enumerate(heads, 1)]},
                      ensure_ascii=False)


def sections_answer(text):
    block = text.split(SECTIONS_MARK, 1)[1].lstrip("\n").split("\n\n", 1)[0]
    heads = []
    for line in (x.strip() for x in block.splitlines()):
        if line:
            heads.extend(numbered(line) if re.match(r"\d+\.", line) else [line])
    return json.dumps({"sections": [section(h, i) for i, h in enumerate(heads, 1)]}, ensure_ascii=False)


def translate_answer(text):
    start = text.index(TR_OPEN) + len(TR_OPEN)
    end = text.rfind(TR_CLOSE)
    body = text[start:end] if end >= start else text[start:]
    if body.startswith("\n"):
        body = body[1:]
    if body.endswith("\n"):
        body = body[:-1]
    return translate(body) or "[stub-llm]"


def chat_answer(users):
    notes = [u for u in users if u.startswith(NOTE_OPEN)]
    note = notes[-1] if notes else ""
    if KB_MARK in note:
        kb = note.split(KB_MARK, 1)[1]
        end = kb.rfind(NOTE_CLOSE)
        kb = kb[:end] if end >= 0 else kb
        close = kb.find("]")   # hết lời dặn của gateway ngay sau marker; phần sau là dữ liệu tra được
        kb = (kb[close + 1:] if close >= 0 else kb).strip()
        if kb:
            return "[stub-llm] Trích ngữ cảnh KB:\n\n" + kb
    questions = [u.strip() for u in users if not u.startswith(NOTE_OPEN)]
    q = questions[-1] if questions else ""
    if len(q) > 500:
        q = q[:500] + "…"
    return (f"[stub-llm] Model stub (CHỈ ĐỂ TEST) đã nhận câu hỏi: «{q}». "
            "Không có ngữ cảnh KB đi kèm nên stub không trả lời nội dung.")


def choose_answer(messages):
    msgs = [m for m in messages if isinstance(m, dict)]
    system = "\n".join(text_of(m.get("content")) for m in msgs if m.get("role") == "system")
    users = [text_of(m.get("content")) for m in msgs if m.get("role") == "user"]
    if JUDGE_SYSTEM in system or any(u.lstrip().startswith(JUDGE_USER) for u in users):
        return JUDGE_JSON
    for mark, build in ((HEADINGS_ASK, lambda u: HEADINGS_EMPTY), (REPORT_MARK, report_answer),
                        (SECTIONS_MARK, sections_answer), (TR_OPEN, translate_answer)):
        hits = [u for u in users if mark in u]
        if hits:
            return build(hits[-1])
    return chat_answer(users)


def embed(text, dim):
    vals, block = [], 0
    while len(vals) < dim:
        vals.extend(b / 127.5 - 1 for b in hashlib.sha256(f"{block}:{text}".encode("utf-8", "surrogatepass")).digest())
        block += 1
    vals = vals[:dim]
    norm = math.sqrt(sum(x * x for x in vals))   # b/127.5-1 không bao giờ bằng 0 nên norm > 0
    return [x / norm for x in vals]


def words(text):
    return set(_WORD.findall(text.lower()))


class Config:
    def __init__(self, key, llm, emb, rerank, dim):
        self.key = key.encode("utf-8", "surrogateescape")
        self.llm, self.emb, self.rerank, self.dim = llm, emb, rerank, dim

    def models(self):
        return {"object": "list", "data": [
            {"id": self.llm, "object": "model", "created": 0, "owned_by": "stub", "max_model_len": 131072}]}

    def chat(self, body):
        messages = body.get("messages")
        if not isinstance(messages, list):
            raise BadRequest("messages phải là list")
        text = choose_answer(messages)   # có tools vẫn trả văn bản thường, không tool_calls
        created = int(time.time())
        prompt_tokens = max(1, sum(count_words(text_of(m.get("content"))) for m in messages if isinstance(m, dict)))
        completion_tokens = max(1, count_words(text))
        usage = {"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens,
                 "total_tokens": prompt_tokens + completion_tokens}
        if body.get("stream") is not True:
            return "application/json", json.dumps({
                "id": "chatcmpl-stub", "object": "chat.completion", "created": created, "model": self.llm,
                "choices": [{"index": 0, "message": {"role": "assistant", "content": text}, "finish_reason": "stop"}],
                "usage": usage}, ensure_ascii=False).encode()
        base = {"id": "chatcmpl-stub", "object": "chat.completion.chunk", "created": created, "model": self.llm}
        chunks = [{**base, "choices": [{"index": 0, "delta": {"role": "assistant", "content": ""}, "finish_reason": None}]},
                  {**base, "choices": [{"index": 0, "delta": {"content": text}, "finish_reason": None}]},
                  {**base, "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}]
        opts = body.get("stream_options")
        if isinstance(opts, dict) and opts.get("include_usage"):
            chunks.append({**base, "choices": [], "usage": usage})
        # ensure_ascii: mỗi chunk đúng một dòng ASCII, không ký tự xuống dòng lạ nào lọt vào luồng SSE
        sse = "".join(f"data: {json.dumps(c)}\n\n" for c in chunks) + "data: [DONE]\n\n"
        return "text/event-stream; charset=utf-8", sse.encode()

    def embeddings(self, body):
        inp = body.get("input")
        texts = [inp] if isinstance(inp, str) else inp
        if not isinstance(texts, list) or not texts or not all(isinstance(t, str) for t in texts):
            raise BadRequest("input phải là chuỗi hoặc list chuỗi khác rỗng")
        n = max(1, sum(count_words(t) for t in texts))
        return "application/json", json.dumps({
            "object": "list", "model": self.emb,
            "data": [{"object": "embedding", "index": i, "embedding": embed(t, self.dim)} for i, t in enumerate(texts)],
            "usage": {"prompt_tokens": n, "total_tokens": n}}).encode()

    def rerank_(self, body):
        query, docs, top_n = body.get("query"), body.get("documents"), body.get("top_n")
        if not isinstance(query, str) or not isinstance(docs, list):
            raise BadRequest("cần query (chuỗi) và documents (list)")
        texts = []
        for d in docs:
            t = d.get("text") if isinstance(d, dict) else d
            if not isinstance(t, str):
                raise BadRequest("documents phải là chuỗi hoặc {\"text\": chuỗi}")
            texts.append(t)
        if top_n is not None and (isinstance(top_n, bool) or not isinstance(top_n, int)):
            raise BadRequest("top_n phải là số nguyên")
        q = words(query)
        scored = sorted(((len(q & words(t)) / max(1, len(q)) * 0.98, i, t) for i, t in enumerate(texts)),
                        key=lambda s: (-s[0], s[1]))
        if top_n and top_n > 0:
            scored = scored[:top_n]
        n = max(1, count_words(query) + sum(count_words(t) for t in texts))
        return "application/json", json.dumps({
            "id": "rerank-stub", "model": self.rerank, "usage": {"total_tokens": n},
            "results": [{"index": i, "relevance_score": s, "document": {"text": t}} for s, i, t in scored]},
            ensure_ascii=False).encode()


def make_handler(cfg):
    routes = (("/chat/completions", cfg.llm, cfg.chat), ("/embeddings", cfg.emb, cfg.embeddings),
              ("/rerank", cfg.rerank, cfg.rerank_))

    class Handler(BaseHTTPRequestHandler):
        # HTTP/1.0: mỗi response đóng kết nối; mọi response vẫn có Content-Length (kể cả SSE)
        server_version = "model_stub"
        sys_version = ""
        norm_path = "-"

        def _send(self, code, raw, ctype="application/json"):
            self.send_response(code)
            self.send_header("Content-Type", ctype if "charset" in ctype else ctype + "; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            if ctype.startswith("text/event-stream"):
                self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            self.wfile.write(raw)

        def _json(self, code, obj):
            self._send(code, json.dumps(obj, ensure_ascii=False).encode())

        def _error(self, code, message):
            self._json(code, {"error": {"message": message}})

        def _authed(self):
            scheme, _, token = self.headers.get("Authorization", "").partition(" ")
            return scheme.lower() == "bearer" and hmac.compare_digest(
                token.strip().encode("utf-8", "surrogateescape"), cfg.key)

        def _read_body(self):
            # luôn đọc hết body trước khi trả lời (kể cả 401): đóng socket còn dữ liệu chưa đọc sẽ RST mất response
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                return None
            if length < 0 or length > MAX_BODY:
                return None
            return self.rfile.read(length)

        def _dispatch(self, method):
            self.norm_path = re.sub("/+", "/", urlsplit(self.path).path).rstrip("/") or "/"
            path = self.norm_path
            raw = self._read_body() if method == "POST" else b""
            if method == "GET" and path == "/health":
                return self._json(200, {"ok": True})
            if not self._authed():
                return self._error(401, "thiếu hoặc sai Bearer token")
            if method == "GET":
                return self._json(200, cfg.models()) if path.endswith("/models") else self._error(404, "not found")
            route = next((r for r in routes if path.endswith(r[0])), None)
            if route is None:
                return self._error(404, "not found")
            try:
                body = json.loads(raw) if raw is not None else None
            except ValueError:
                body = None
            if not isinstance(body, dict):
                return self._error(400, "body phải là JSON object")
            model = body.get("model")
            if model != route[1]:
                shown = model if isinstance(model, str) else ("" if model is None else json.dumps(model))
                return self._error(404, f"model '{shown[:200]}' not found")
            try:
                ctype, out = route[2](body)
            except BadRequest as e:
                return self._error(400, str(e))
            self._send(200, out, ctype)

        def _guarded(self, method):
            try:
                self._dispatch(method)
            except (BrokenPipeError, ConnectionResetError):
                pass
            except Exception as e:  # noqa: BLE001 — chỉ ghi tên lỗi, không ghi nội dung request
                sys.stderr.write(f"model_stub lỗi nội bộ: {type(e).__name__}\n")
                try:
                    self._error(500, "stub internal error")
                except OSError:
                    pass

        def do_GET(self):
            self._guarded("GET")

        def do_POST(self):
            self._guarded("POST")

        def log_request(self, code="-", size="-"):
            code = getattr(code, "value", code)
            sys.stderr.write(f"{time.strftime('%Y-%m-%dT%H:%M:%S')} model_stub {self.command or '-'} {self.norm_path} {code}\n")
            sys.stderr.flush()

        def log_message(self, fmt, *args):
            pass   # log_request đã ghi method/path/mã; không ghi dòng request thô hay header

    return Handler


def env_dim():
    raw = os.environ.get("STUB_EMBED_DIM", "1024")
    try:
        dim = int(raw)
    except ValueError:
        dim = 0
    if not 1 <= dim <= 65536:
        sys.stderr.write("model_stub: STUB_EMBED_DIM phải là số nguyên 1..65536\n")
        sys.exit(2)
    return dim


def main():
    ap = argparse.ArgumentParser(
        prog="model_stub.py",
        description="CHỈ ĐỂ TEST: stub LLM + embedding + rerank kiểu OpenAI; chứng minh đường ống, không chứng minh chất lượng model.")
    ap.add_argument("--host", default="127.0.0.1", help="địa chỉ nghe (mặc định 127.0.0.1)")
    ap.add_argument("--port", type=int, default=18000, help="cổng (mặc định 18000; 0 = tự chọn, in cổng thật)")
    args = ap.parse_args()
    cfg = Config(os.environ.get("STUB_API_KEY") or DEFAULT_KEY,
                 os.environ.get("STUB_LLM_MODEL") or "DuyTa/Cyber-F1-AWQ",
                 os.environ.get("STUB_EMBEDDING_MODEL") or "DuyTa/sec-embedding",
                 os.environ.get("STUB_RERANK_MODEL") or "DuyTa/sec-rerank",
                 env_dim())
    try:
        server = ThreadingHTTPServer((args.host, args.port), make_handler(cfg))
    except OSError as e:
        sys.stderr.write(f"model_stub: không mở được {args.host}:{args.port}: {e.strerror or e}\n")
        sys.exit(1)
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    host, port = server.server_address[:2]
    print(f"model_stub listening on http://{host}:{port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
