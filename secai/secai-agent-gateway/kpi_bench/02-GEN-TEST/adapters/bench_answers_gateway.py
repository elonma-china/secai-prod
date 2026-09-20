#!/usr/bin/env python3
"""bench_answers_gateway.py — Drop-in thay thế bench_answers_concurrent.py, gọi thẳng
LIVE secai-agent-gateway (SSE) thay vì opencode /session API cũ.

Giữ NGUYÊN cấu trúc bench_answers_concurrent.py: argparse flags, resume-by-id đọc
--out cũ, ThreadPoolExecutor concurrency, progress in mỗi 25 câu (rate/ETA/error-count),
ghi JSONL có Lock. CHỈ thay phần gọi HTTP bên trong answer_one()/work(): POST
/session + /session/{id}/message (opencode) → POST /ai/chat/stream (gateway, SSE).

=== GATEWAY API CONTRACT (verified live) ===
Base URL mặc định: http://127.0.0.1:8131 (Docker container secai-docker-gateway).
Endpoint: POST /ai/chat/stream — SSE (text/event-stream).
Request body: {"message": "<câu hỏi, có thể kèm nội dung file>"}  — KHÔNG gửi user_id
(hoặc gửi "anonymous") để mỗi request có session RIÊNG (fresh), tránh context của câu
trước bị mang sang câu sau (routes.py:_chat_session_id).

HARD GUARD: security.py:require_public_input() trả HTTP 422 nếu message khớp pattern
private-key PEM / sk-|pk-|api- token 16+ ký tự / password|passwd|authorization|bearer
theo sau bởi :/= + token / AWS AKIA key / 13-19 chữ số liên tiếp. Các câu SAST/remediation
nhúng mã nguồn lỗi thật (vd CWE-798 chứa SECRET_KEY = "hardcoded-secret-do-not-use") CÓ
THỂ dính bẫy này — bắt riêng từng câu (urllib.error.HTTPError code 422), ghi vào
record["error"] = "HTTP422:blocked_by_require_public_input", đếm running counter, và
TIẾP TỤC (không để 1 câu bị chặn giết cả run). Counter này tự nó là 1 finding cần báo cáo.

SSE frames: "event: NAME\ndata: JSON\n\n", kết thúc bằng dòng "data: [DONE]\n\n" trần.
  - tool_call   {"id","name","input","summary"} → gom event["name"] vào "tools".
    Đây là CÁCH DUY NHẤT biết tool nào thực sự được gọi (khác /chat_no_stream, chỉ liệt
    kê tool CÓ SẴN cho profile chứ không phải tool ĐÃ GỌI — do đó KHÔNG dùng /chat_no_stream).
  - tool_result {"id","name","is_error","duration_ms","result"} — "result" là nội dung
    tool trả về, ĐÃ ĐƯỢC gateway đưa vào SSE để UI hiển thị citation (xem
    src/secai_gateway/api/sse.py:66-78: "Include the retrieved content so the UI can
    render citations (sources)", cắt 4000 ký tự). Với tool truy hồi KB (qdrant_search/
    neo4j_search — xem agent/factory.py RequireKbSearchBeforeSynthesisHook), "result" CHÍNH
    LÀ context RAG thật đã được feed cho LLM sinh câu trả lời → 2026-09-20: gom các
    tool_result này (lọc theo _CONTEXT_TOOL_NAMES) vào rec["retrieved_contexts"] và xuất
    riêng ra <out-dir>/real-contexts.jsonl (id → contexts) để faith_judge.py chấm
    faithfulness đúng chuẩn RAGAS (context = cái gateway ĐÃ retrieve, không phải gold
    re-fetch). Trước 2026-09-20 field "result" bị bỏ qua hoàn toàn (chỉ gom tên tool).
  - token       {"delta"} — 1 chunk text stream. Ghi lại wall-clock của token ĐẦU TIÊN
    trừ thời điểm bắt đầu request = ttftMs. Nối các delta thành draft best-effort.
  - regenerate  {"hook","reason"} — hook bác bản draft đang stream, bắt model làm lại
    toàn bộ. Khi gặp event này PHẢI reset buffer token-delta đã gom về "" — nếu không,
    draft bị bác và bản regenerate đúng sẽ nối liền không có ranh giới (gotcha đã ghi
    chú ngay trong source gateway, api/sse.py).
  - error       {"error"} — lỗi cứng; stream vẫn tiếp tục tới "final" với fallback message.
    Đánh dấu degraded nếu gặp, nhưng vẫn ghi final text.
  - final       {"content","answer","message","metadata"} — text CHÍNH THỨC cuối cùng
    (event["content"] == event["answer"]). LUÔN dùng field này cho "text" (không dùng
    token-buffer tự gom, chỉ là best-effort). metadata có thể có "input_classification":
    {"label","action","blocked":bool,"pii":bool,"scores","latency_ms"} — ghi vào field
    "input_classification" khi có; blocked:true nghĩa là classifier model-based đã từ
    chối cho agent trả lời (fail-open theo thiết kế nên phải HIẾM — cờ mọi lần xảy ra,
    đây là dữ liệu chẩn đoán quý — 1 câu SAST/exploit hợp lệ bị coi nhầm là adversarial
    là 1 finding thật).
  Stream kết thúc ở dòng literal "data: [DONE]\n\n".

HTTP client: urllib.request (stdlib) — đúng idiom sẵn có của bench_answers_concurrent.py
— đọc response từng dòng, gom theo frame SSE ngăn cách bởi dòng trống. Timeout mỗi câu
240-300s (theo quy ước codebase này).

RATE LIMIT (route_limit() strip "/stream" nên /ai/chat/stream tính vào bucket DEFAULT
15 req/60s theo (client_ip, route), fixed-window, không có Retry-After): mọi worker thread
CHUNG 1 client IP nên phải gate TOÀN CỤC bằng 1 rate limiter dùng chung (_RateLimiter bên
dưới, sliding-window với margin an toàn dưới 15/60s) TRƯỚC mỗi lần gọi urlopen — đây là cơ
chế CHÍNH để không đụng limit. Nếu vẫn dính 429 (race với traffic khác trên cùng gateway),
retry có backoff, giới hạn số lần thử — KHÔNG retry mù vô hạn.

Usage:
  python3 bench_answers_gateway.py --dataset /path/to/testset-dir \
      --out /path/to/results.jsonl --workers 4 [--limit N]

Biến môi trường:
  GATEWAY_BASE   (default http://127.0.0.1:8131)
  GATEWAY_TIMEOUT (default 260, giây)
  REPO_ROOT      (default /home/ict/secai-88) — gốc để resolve "files" tương đối
  GATEWAY_RATE_LIMIT_MAX    (default 80, số request tối đa mỗi cửa sổ — margin dưới 90/60s
                             hiện hành cho /ai/chat kể từ 2026-08-27; đổi nếu route_limit() đổi lại)
  GATEWAY_RATE_LIMIT_PERIOD (default 62, giây mỗi cửa sổ — margin trên 60)
"""
import argparse
import collections
import glob
import json
import os
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Lock

BASE = os.environ.get("GATEWAY_BASE", "http://127.0.0.1:8131").rstrip("/")
TIMEOUT = float(os.environ.get("GATEWAY_TIMEOUT", "260"))
REPO_ROOT = Path(os.environ.get("REPO_ROOT", "/home/ict/secai-88"))


class _RateLimiter:
    """Sliding-window client-side rate limiter, dùng CHUNG cho mọi worker thread.

    Gateway giới hạn 15 req/60s theo (client_ip, route) — mọi thread ở đây đều bắn
    từ CÙNG 1 client IP nên đây phải là 1 limiter TOÀN CỤC (không phải per-thread),
    và cửa sổ/ngưỡng phải nhỏ hơn ngưỡng thật (mặc định 13/62s) để chừa margin cho
    fixed-window đếm lệch giờ ở phía server.
    """

    def __init__(self, max_calls, period):
        self.max_calls = max_calls
        self.period = period
        self._calls = collections.deque()
        self._lock = threading.Lock()

    def acquire(self):
        while True:
            with self._lock:
                now = time.time()
                while self._calls and now - self._calls[0] >= self.period:
                    self._calls.popleft()
                if len(self._calls) < self.max_calls:
                    self._calls.append(now)
                    return
                wait = self.period - (now - self._calls[0]) + 0.05
            time.sleep(max(wait, 0.05))


_RATE_LIMIT_MAX = int(os.environ.get("GATEWAY_RATE_LIMIT_MAX", "80"))
_RATE_LIMIT_PERIOD = float(os.environ.get("GATEWAY_RATE_LIMIT_PERIOD", "62"))
_rate_limiter = _RateLimiter(_RATE_LIMIT_MAX, _RATE_LIMIT_PERIOD)
_MAX_429_RETRIES = int(os.environ.get("GATEWAY_MAX_429_RETRIES", "3"))

# Tool nào trả về context RAG THẬT (KB retrieval) — dùng để lọc tool_result events khi gom
# rec["retrieved_contexts"]. Khớp agent/factory.py: RequireKbSearchBeforeSynthesisHook(tools=
# ("qdrant_search", "neo4j_search")). Override bằng env nếu tên tool đổi (comma-separated).
_CONTEXT_TOOL_NAMES = {
    t.strip() for t in os.environ.get(
        "FAITH_CONTEXT_TOOL_NAMES", "qdrant_search,neo4j_search").split(",") if t.strip()
}

_LANG_BY_EXT = {
    ".py": "python", ".js": "javascript", ".ts": "typescript", ".jsx": "jsx",
    ".tsx": "tsx", ".java": "java", ".go": "go", ".rb": "ruby", ".php": "php",
    ".c": "c", ".cpp": "cpp", ".cc": "cpp", ".h": "c", ".hpp": "cpp",
    ".cs": "csharp", ".sh": "bash", ".yml": "yaml", ".yaml": "yaml",
    ".json": "json", ".sql": "sql", ".rs": "rust", ".kt": "kotlin",
    ".swift": "swift", ".xml": "xml", ".html": "html", ".env": "",
}


def _resolve_file(path_str, dataset_dir):
    """Resolve 1 đường dẫn file trong sample['files'] thành Path tồn tại, hoặc None.

    Ưu tiên: 1) tuyệt đối / relative-to-cwd sẵn có, 2) relative tới REPO_ROOT
    (quy ước gen_testset.py), 3) relative tới --dataset và các thư mục cha của nó.
    """
    candidates = []
    p = Path(path_str)
    if p.is_absolute():
        candidates.append(p)
    else:
        candidates.append(Path.cwd() / p)
        candidates.append(REPO_ROOT / p)
        ds = Path(dataset_dir).resolve()
        parents = [ds] + list(ds.parents)
        for parent in parents:
            candidates.append(parent / p)
    for c in candidates:
        if c.is_file():
            return c
    return None


def _build_message(sample, dataset_dir):
    """Ghép input câu hỏi + nội dung file (nếu có) thành message text gửi gateway."""
    text = sample.get("input", "")
    files = sample.get("files") or []
    if not files:
        return text
    blocks = []
    for fp in files:
        resolved = _resolve_file(fp, dataset_dir)
        if resolved is None:
            print(f"  [WARN] không tìm thấy file '{fp}' (id={sample.get('id')}) — gửi câu hỏi KHÔNG kèm file này", flush=True)
            continue
        try:
            content = resolved.read_text(encoding="utf-8", errors="replace")
        except Exception as e:  # noqa: BLE001
            print(f"  [WARN] lỗi đọc file '{fp}' (id={sample.get('id')}): {e} — bỏ qua file này", flush=True)
            continue
        ext = resolved.suffix.lower()
        lang = _LANG_BY_EXT.get(ext, "")
        filename = resolved.name
        blocks.append(f"\n\n--- Nội dung file `{filename}` ---\n```{lang}\n{content}\n```")
    return text + "".join(blocks)


def _iter_sse(resp):
    """Đọc HTTP response theo dòng, gom thành frame SSE (event/data) ngăn bởi dòng trống.
    Yield (event_name, payload_dict) cho mỗi frame; dừng khi gặp "data: [DONE]"."""
    event_name = None
    data_lines = []
    for raw in resp:
        line = raw.decode("utf-8", errors="replace").rstrip("\n").rstrip("\r")
        if line == "":
            if data_lines:
                data_str = "\n".join(data_lines)
                if data_str.strip() == "[DONE]":
                    return
                try:
                    payload = json.loads(data_str)
                except json.JSONDecodeError:
                    payload = {}
                yield (event_name or "message", payload)
            event_name = None
            data_lines = []
            continue
        if line.startswith("event:"):
            event_name = line[len("event:"):].strip()
        elif line.startswith("data:"):
            data_lines.append(line[len("data:"):].lstrip(" "))
    # response ended without trailing blank line — flush any pending frame
    if data_lines:
        data_str = "\n".join(data_lines)
        if data_str.strip() != "[DONE]":
            try:
                payload = json.loads(data_str)
            except json.JSONDecodeError:
                payload = {}
            yield (event_name or "message", payload)


def answer_one(s, dataset_dir):
    qid = s["id"]
    rec = {"id": qid, "capability": s.get("capability"), "bucket": s.get("bucket"),
           "difficulty": s.get("difficulty"), "input": s.get("input"),
           "text": "", "tools": [], "ttftMs": None, "totalMs": None, "error": None,
           "input_classification": None,
           "retrieved_contexts": []}  # tool_result["result"] thật từ qdrant_search/neo4j_search
    message = _build_message(s, dataset_dir)
    body = json.dumps({"message": message}).encode("utf-8")
    t0 = time.time()
    attempt = 0
    retries_429 = 0
    while True:
        attempt += 1
        req = urllib.request.Request(
            BASE + "/ai/chat/stream", data=body,
            headers={"Content-Type": "application/json"}, method="POST",
        )
        # Self-pace TRƯỚC mỗi lần gọi (kể cả lần retry) — đây là cơ chế CHÍNH giữ dưới
        # ngưỡng 15 req/60s của gateway, không phải retry-sau-khi-dính-429.
        _rate_limiter.acquire()
        token_buf = []
        ttft_recorded = False
        got_error_event = False
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                for event_name, payload in _iter_sse(resp):
                    if event_name == "tool_call":
                        name = payload.get("name")
                        if name:
                            rec["tools"].append(name)
                    elif event_name == "tool_result":
                        # "result" là context RAG THẬT gateway đã feed cho LLM (sse.py:66-78)
                        # — chỉ giữ tool truy hồi KB, bỏ qua tool khác (poc_search, web_fetch...).
                        name = payload.get("name")
                        result = payload.get("result")
                        if name in _CONTEXT_TOOL_NAMES and result:
                            rec["retrieved_contexts"].append(result)
                    elif event_name == "token":
                        if not ttft_recorded:
                            rec["ttftMs"] = int((time.time() - t0) * 1000)
                            ttft_recorded = True
                        delta = payload.get("delta", "")
                        if delta:
                            token_buf.append(delta)
                    elif event_name == "regenerate":
                        # draft bị bác bỏ toàn bộ, model làm lại từ đầu — xoá buffer token-delta
                        # để không nối rác của draft cũ với bản regenerate đúng.
                        token_buf = []
                    elif event_name == "error":
                        got_error_event = True
                    elif event_name == "final":
                        final_text = payload.get("content")
                        if final_text is None:
                            final_text = payload.get("answer") or payload.get("message") or "".join(token_buf)
                        rec["text"] = final_text
                        meta = payload.get("metadata") or {}
                        ic = meta.get("input_classification")
                        if ic is not None:
                            rec["input_classification"] = ic
            rec["totalMs"] = int((time.time() - t0) * 1000)
            if got_error_event and not rec["error"]:
                rec["error"] = "SSE_error_event_seen_degraded"
            if not rec["text"] and token_buf:
                # phòng khi thiếu event "final" hẳn — fallback về draft gom được
                rec["text"] = "".join(token_buf)
            rec["retries_429"] = retries_429
            return rec
        except urllib.error.HTTPError as e:
            rec["totalMs"] = int((time.time() - t0) * 1000)
            if e.code == 422:
                # 422 nghĩa là "chặn câu này" (require_public_input) — KHÔNG phải "chậm
                # lại", giữ nguyên hành vi cũ: không retry, trả lỗi ngay.
                rec["error"] = "HTTP422:blocked_by_require_public_input"
                rec["retries_429"] = retries_429
                return rec
            if e.code == 429 and attempt <= _MAX_429_RETRIES:
                # Không nên xảy ra nhờ _rate_limiter, nhưng nếu vẫn dính (traffic khác
                # cùng gateway) thì backoff có giới hạn rồi thử lại — không retry mù.
                retries_429 += 1
                time.sleep(3.0 * attempt)
                rec["tools"] = []  # reset — attempt trước có thể đã gom dở dang
                rec["retrieved_contexts"] = []  # reset cùng lý do
                continue
            if e.code == 429:
                # Hết số lần retry cho phép, vẫn 429 — gắn nhãn riêng để phân biệt với
                # lỗi HTTP chung, và để work() đếm riêng "429-final-failures".
                rec["error"] = "HTTP429:rate_limited_after_retries"
                rec["retries_429"] = retries_429
                return rec
            body_txt = ""
            try:
                body_txt = e.read().decode("utf-8", errors="replace")[:500]
            except Exception:  # noqa: BLE001
                pass
            rec["error"] = f"HTTPError:{e.code}:{body_txt}"
            rec["retries_429"] = retries_429
            return rec
        except Exception as e:  # noqa: BLE001
            rec["totalMs"] = int((time.time() - t0) * 1000)
            rec["error"] = f"{type(e).__name__}: {e}"
            rec["retries_429"] = retries_429
            return rec


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", default="/opt/secai/datasets-acceptance")
    ap.add_argument("--out", default="/opt/secai/kpi/raw-logs/results.jsonl")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--real-contexts-out", default=None,
                     help="đường dẫn real-contexts.jsonl (id -> contexts THẬT gom từ tool_result "
                          "qdrant_search/neo4j_search). Mặc định: cùng thư mục --out, tên "
                          "'real-contexts.jsonl' — faith_judge.py tự tìm file này theo quy ước đó.")
    args = ap.parse_args()
    real_ctx_path = args.real_contexts_out or str(
        Path(args.out).resolve().with_name("real-contexts.jsonl"))

    qs = []
    for f in sorted(glob.glob(os.path.join(args.dataset, "*.jsonl"))):
        if os.path.basename(f).startswith("_"):
            continue  # bỏ tệp phụ trợ (_meta/, _pool.jsonl…)
        for line in open(f, encoding="utf-8"):
            if line.strip():
                qs.append(json.loads(line))
    if args.limit:
        qs = qs[: args.limit]

    done = set()
    if os.path.exists(args.out):
        for line in open(args.out, encoding="utf-8"):
            try:
                done.add(json.loads(line)["id"])
            except Exception:
                pass
    todo = [s for s in qs if s["id"] not in done]
    print(f"== answers gateway: {len(todo)} câu (skip {len(done)} done) · workers={args.workers} · base={BASE} ==", flush=True)

    lock = Lock()
    os.makedirs(os.path.dirname(os.path.abspath(args.out)) or ".", exist_ok=True)
    out = open(args.out, "a", encoding="utf-8")
    os.makedirs(os.path.dirname(os.path.abspath(real_ctx_path)) or ".", exist_ok=True)
    real_ctx_out = open(real_ctx_path, "a", encoding="utf-8")
    n = [0]
    nerr = [0]
    n422 = [0]
    nblocked = [0]
    n429ok = [0]     # câu bị 429 nhưng retry thành công (không phải lỗi cuối cùng)
    n429fail = [0]   # câu vẫn 429 sau khi hết số lần retry — error="HTTP429:rate_limited_after_retries"
    nctx = [0]       # câu có >=1 context THẬT gom được từ tool_result (qdrant_search/neo4j_search)
    t0 = time.time()

    def work(s):
        r = answer_one(s, args.dataset)
        with lock:
            out.write(json.dumps(r, ensure_ascii=False) + "\n")
            out.flush()
            # real-contexts.jsonl: id -> contexts THẬT (RAGAS-compliant) — faith_judge.py
            # tự tìm file này trong cùng thư mục answers.jsonl/results.jsonl.
            real_ctx_out.write(json.dumps(
                {"id": r["id"], "contexts": r.get("retrieved_contexts") or []}, ensure_ascii=False) + "\n")
            real_ctx_out.flush()
            n[0] += 1
            if r["error"]:
                nerr[0] += 1
                if r["error"] == "HTTP422:blocked_by_require_public_input":
                    n422[0] += 1
                elif r["error"] == "HTTP429:rate_limited_after_retries":
                    n429fail[0] += 1
            elif r.get("retries_429", 0) > 0:
                n429ok[0] += 1
            if r.get("retrieved_contexts"):
                nctx[0] += 1
            ic = r.get("input_classification")
            if isinstance(ic, dict) and ic.get("blocked"):
                nblocked[0] += 1
            if n[0] % 25 == 0 or n[0] == len(todo):
                rate = n[0] / max(1e-9, time.time() - t0)
                eta = (len(todo) - n[0]) / max(1e-9, rate)
                print(f"  {n[0]}/{len(todo)} · {rate:.2f} q/s · err={nerr[0]} (422={n422[0]}) · 429_retried_ok={n429ok[0]} 429_final_fail={n429fail[0]} · classifier_blocked={nblocked[0]} · real_ctx_captured={nctx[0]} · ETA {eta/60:.1f}m", flush=True)
        return r

    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        list(ex.map(work, todo))
    out.close()
    real_ctx_out.close()
    print(f"== XONG: {n[0]} câu · {nerr[0]} lỗi (422={n422[0]}) · 429_retried_ok={n429ok[0]} 429_final_fail={n429fail[0]} · classifier_blocked={nblocked[0]} · real_ctx_captured={nctx[0]}/{n[0]} → {real_ctx_path} · {(time.time()-t0)/60:.1f} phút ==", flush=True)


if __name__ == "__main__":
    main()
