#!/usr/bin/env python3
"""trace_one.py — Chạy 1 câu hỏi và in TOÀN BỘ chuỗi suy luận ReAct của agent secAI.

Mục đích (nghiệm thu): chứng minh mô hình LLM trả lời một mẫu CVE theo *từng bước* —
gọi công cụ nào, tham số gì, KB trả về gì — rồi mới đưa ra câu trả lời cuối. Khác với
acceptance_runner.py (chỉ lấy text + danh sách tool), script này giữ NGUYÊN thứ tự các
"part" của message trợ lý để tái dựng chuỗi:

    SUY LUẬN -> GỌI qdrant_search -> KẾT QUẢ -> GỌI neo4j_search -> KẾT QUẢ -> TRẢ LỜI

Nguồn dữ liệu: chính API opencode :4096 (KHÔNG bịa) —
  POST /session
  POST /session/{id}/prompt_async   (trả ngay, không mắc kẹt ở response streaming)
  GET  /session/{id}/message        (poll các part đã lưu, đúng thứ tự)

Mô hình dùng: mặc định lấy default của backend từ GET /config (hiện tại là
runpod-secai/Qwen/Qwen3-30B-A3B trên GPU box) — in kèm trong artifact để chứng minh
"mô hình nào đã trả lời". enable_thinking=false nên không có part 'reasoning' riêng;
bằng chứng suy luận-từng-bước chính là chuỗi tool-call ReAct + text xen kẽ.

Chỉ dùng stdlib (urllib) để chạy được cả trên VPS Python trần (không pip/httpx).
Auth: đọc OPENCODE_SERVER_USERNAME / OPENCODE_SERVER_PASSWORD từ env (không in ra).

Ví dụ:
  # sourced creds sẵn trong env:
  python3 kpi/scripts/trace_one.py --id acc_dd_0001
  python3 kpi/scripts/trace_one.py --question "CVE-2021-44228 là gì?"
  # hoặc chạy qua wrapper tự nạp creds từ container:
  bash kpi/scripts/trace_one.sh --id acc_dd_0001
"""
import argparse
import base64
import json
import os
import socket
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
ANSWERS = REPO / "kpi" / "raw-logs" / "answers.jsonl"
DATASET_DIRS = [REPO / "datasets-acceptance", REPO / "acceptance" / "datasets"]
# Fallback nếu /config không đọc được (đúng với OPENCODE_CONFIG_CONTENT hiện hành):
DEFAULT_MODEL_REF = "runpod-secai/Qwen/Qwen3-30B-A3B"

MIME = {"py": "text/x-python", "js": "text/javascript", "ts": "text/x-typescript",
        "java": "text/x-java", "php": "text/x-php", "go": "text/x-go", "rb": "text/x-ruby",
        "c": "text/x-c", "txt": "text/plain", "json": "application/json"}


class HttpError(Exception):
    def __init__(self, status, body=""):
        super().__init__(f"HTTP {status}: {body[:200]}")
        self.status = status
        self.body = body


class Client:
    """urllib tối giản + basic auth, đọc trọn body (chịu được response giữ lâu của opencode)."""

    def __init__(self, base_url, timeout):
        self.base = base_url.rstrip("/")
        self.timeout = timeout
        self.headers = {}
        pw = os.environ.get("OPENCODE_SERVER_PASSWORD")
        if pw:
            user = os.environ.get("OPENCODE_SERVER_USERNAME", "Fable5")
            token = base64.b64encode(f"{user}:{pw}".encode()).decode()
            self.headers["Authorization"] = "Basic " + token

    def _req(self, method, path, body=None):
        url = self.base + path
        data = json.dumps(body).encode() if body is not None else None
        hdrs = dict(self.headers)
        if data is not None:
            hdrs["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                return r.status, r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode("utf-8", "replace")

    def get_json(self, path):
        status, text = self._req("GET", path)
        if status >= 400:
            raise HttpError(status, text)
        return json.loads(text)

    def post_json(self, path, body):
        status, text = self._req("POST", path, body)
        if status >= 400:
            raise HttpError(status, text)
        return json.loads(text)

    def post_empty(self, path, body):
        status, text = self._req("POST", path, body)
        if status >= 400:
            raise HttpError(status, text)

    def abort(self, session_id):
        try:
            self.post_json(f"/session/{session_id}/abort", {})
        except Exception:
            pass


# ── Nguồn câu hỏi ─────────────────────────────────────────────────────────────

def load_sample_by_id(sample_id):
    """Tìm sample theo id trong answers.jsonl trước, rồi tới các dataset gốc."""
    if ANSWERS.exists():
        for line in ANSWERS.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            rec = json.loads(line)
            if rec.get("id") == sample_id:
                return {"id": rec["id"], "question": rec.get("question", ""),
                        "files": rec.get("files", []) or [], "gold": rec.get("gold", {}),
                        "expected": rec.get("expected", {})}
    for d in DATASET_DIRS:
        if not d.exists():
            continue
        for fp in sorted(d.glob("*.jsonl")):
            for line in fp.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                s = json.loads(line)
                if s.get("id") == sample_id:
                    meta = s.get("metadata") or {}
                    return {"id": s["id"], "question": s.get("input", ""),
                            "files": s.get("files", []) or [],
                            "gold": {"cve_id": meta.get("cve_id"), "cwe": meta.get("cwe", []),
                                     "severity": meta.get("severity")},
                            "expected": s.get("expected", {})}
    return None


def resolve_question(args):
    if args.question:
        return {"id": "adhoc", "question": args.question, "files": [], "gold": {}, "expected": {}}
    s = load_sample_by_id(args.id)
    if not s:
        raise SystemExit(f"[LỖI] Không tìm thấy id '{args.id}' trong answers.jsonl/dataset. "
                         f"Dùng --question để hỏi trực tiếp.")
    return s


# ── Model resolution ──────────────────────────────────────────────────────────

def fetch_config(http):
    """Lấy /config (default model + endpoint mỗi provider). Trả {} nếu lỗi."""
    try:
        return http.get_json("/config")
    except Exception:
        return {}


def provider_endpoints(cfg):
    """{providerID: baseURL} — để chứng minh endpoint phục vụ (không lộ apiKey)."""
    out = {}
    prov = cfg.get("provider", {}) if isinstance(cfg, dict) else {}
    if isinstance(prov, dict):
        for pid, pv in prov.items():
            if isinstance(pv, dict):
                out[pid] = (pv.get("options") or {}).get("baseURL")
    return out


def resolve_model(args, cfg):
    """provider/modelID: ưu tiên --provider/--model; nếu không, lấy default từ /config."""
    if args.provider and args.model:
        return args.provider, args.model, "cli"
    ref = cfg.get("model") if isinstance(cfg, dict) else None
    if not ref:
        ref = DEFAULT_MODEL_REF
    provider, _, model = ref.partition("/")  # model id có thể chứa '/'
    return provider, model, ("/config" if ref != DEFAULT_MODEL_REF else "fallback")


# ── Gửi 1 lượt + lấy trace ────────────────────────────────────────────────────

def make_id(prefix):
    return f"{prefix}_{format(int(time.time() * 1000), 'x')}{os.urandom(5).hex()}"


def build_parts(sample):
    parts = [{"type": "text", "text": sample["question"]}]
    for f in sample.get("files", []) or []:
        ap = Path(f) if os.path.isabs(f) else (REPO / f)
        if not ap.exists():
            continue
        mime = MIME.get(ap.suffix.lstrip(".").lower(), "text/plain")
        b64 = base64.b64encode(ap.read_bytes()).decode()
        parts.append({"type": "file", "mime": mime, "filename": ap.name,
                      "url": f"data:{mime};base64,{b64}"})
    return parts


def run_turn(http, sample, provider, model, agent):
    sid = http.post_json("/session", {"agent": agent,
                                      "model": {"providerID": provider, "id": model}})["id"]
    t0 = time.time()
    http.post_empty(f"/session/{sid}/prompt_async", {
        "messageID": make_id("msg"), "agent": agent,
        "model": {"providerID": provider, "modelID": model},
        "parts": build_parts(sample)})

    deadline = t0 + http.timeout
    all_msgs = []
    while time.time() < deadline:
        all_msgs = http.get_json(f"/session/{sid}/message")
        assistants = [m for m in all_msgs if (m.get("info") or {}).get("role") == "assistant"]
        terminal = next((m for m in reversed(assistants)
                         if (m.get("info") or {}).get("finish") not in (None, "tool-calls")
                         or (m.get("info") or {}).get("error")), None)
        if terminal:
            # Một số provider tiếp tục sinh message dù đã finish=stop. Dừng session ngay
            # sau terminal message đầu tiên để trace không chờ đến timeout/runaway loop.
            http.abort(sid)
            return sid, terminal, all_msgs, round((time.time() - t0) * 1000)

        status = http.get_json("/session/status").get(sid, {"type": "idle"})
        if status.get("type") == "idle" and assistants:
            return sid, assistants[-1], all_msgs, round((time.time() - t0) * 1000)
        time.sleep(0.5)

    http.abort(sid)
    raise socket.timeout(f"session {sid} chưa có terminal message sau {http.timeout:g}s")


def assistant_stream(all_msgs):
    """Trả (ordered_parts, last_info) — parts của các message role=assistant theo thứ tự."""
    ordered = []
    last_info = {}
    for m in all_msgs:
        info = m.get("info", {})
        if info.get("role") != "assistant":
            continue
        last_info = info
        for p in m.get("parts", []):
            ordered.append(p)
    return ordered, last_info


# ── Render ────────────────────────────────────────────────────────────────────

def _fmt_dur(state):
    tm = state.get("time", {}) if isinstance(state, dict) else {}
    if tm.get("start") and tm.get("end"):
        return f"{tm['end'] - tm['start']}ms"
    return "?"


def _trunc(text, limit):
    if text is None:
        return ""
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n     [... {len(text) - limit} ký tự bị cắt — xem messages.json]"


def detect_anomalies(finish, tool_seq, step, answer, err):
    """Cờ chẩn đoán cho nghiệm thu: runaway tool-loop, lặp vô hạn, cắt do token."""
    warns = []
    if finish == "length":
        warns.append("Câu trả lời bị CẮT do chạm giới hạn output token (finish=length) — "
                     "dấu hiệu mô hình LẶP không dừng.")
    counts = {}
    for t in tool_seq:
        counts[t] = counts.get(t, 0) + 1
    over = [f"{k}×{v}" for k, v in counts.items()
            if (k == "neo4j_search" and v > 1) or (k == "qdrant_search" and v > 2) or v > 3]
    if over:
        warns.append(f"Nghi ngờ RUNAWAY TOOL-LOOP: {', '.join(over)} "
                     f"(build.md: qdrant≤2/thực thể, neo4j≤1/câu).")
    if step >= 6:
        warns.append(f"Số bước cao ({step}) cho 1 câu hỏi — chi phí + độ trễ lớn.")
    words = answer.split()
    if len(words) > 200 and len(set(words)) / len(words) < 0.15:
        warns.append(f"ANSWER LẶP nghiêm trọng: chỉ {len(set(words))} từ duy nhất / {len(words)} từ "
                     f"(tỉ lệ {len(set(words)) / len(words):.1%}).")
    if err:
        warns.append("Backend trả về error cho lượt này.")
    return warns


def render_trace(sample, provider, model, model_src, info, parts, total_ms, max_output, endpoints):
    out = []
    w = out.append
    gold = sample.get("gold") or {}
    ans_prov = info.get("providerID")
    ans_model = info.get("modelID")
    ans_endpoint = (endpoints or {}).get(ans_prov)
    finish = info.get("finish")
    w("=" * 78)
    w("TRACE SUY LUẬN TỪNG BƯỚC — AGENT secAI (ATTT)")
    w("=" * 78)
    w(f"ID mẫu       : {sample['id']}")
    w(f"Câu hỏi      : {sample['question']}")
    if gold.get("cve_id"):
        w(f"Gold CVE     : {gold.get('cve_id')}  | CWE {gold.get('cwe')}  | severity {gold.get('severity')}")
    w(f"Mô hình yêu cầu : {provider}/{model}   (nguồn: {model_src})")
    if ans_prov:
        w(f"Mô hình trả lời : {ans_prov}/{ans_model}   <-- BẰNG CHỨNG model")
        if ans_endpoint:
            w(f"Endpoint phục vụ: {ans_endpoint}   <-- BẰNG CHỨNG serving (self-hosted, không phải API ngoài)")
    w("-" * 78)

    step = 0
    tool_seq = []
    text_parts = []
    for p in parts:
        t = p.get("type")
        if t == "step-start":
            step += 1
            w("")
            w(f"── BƯỚC {step} ──────────────────────────────────────────────────────────")
        elif t == "reasoning":
            txt = (p.get("text") or "").strip()
            if txt:
                w("[SUY LUẬN]")
                w("  " + _trunc(txt, max_output).replace("\n", "\n  "))
        elif t == "tool":
            state = p.get("state", {}) or {}
            name = p.get("tool", "?")
            status = state.get("status", "?")
            tool_seq.append(name)
            inp = state.get("input")
            inp_s = json.dumps(inp, ensure_ascii=False) if inp is not None else "{}"
            w(f"[CÔNG CỤ] {name}  ({status}, {_fmt_dur(state)})")
            w(f"  tham số: {_trunc(inp_s, 400)}")
            if status == "completed":
                res = state.get("output", "") or ""
                w(f"  kết quả ({len(res)} bytes):")
                w("    " + _trunc(res, max_output).replace("\n", "\n    "))
            elif status == "error":
                w(f"  LỖI: {_trunc(state.get('error', ''), 500)}")
        elif t == "text":
            if p.get("ignored"):
                continue
            txt = (p.get("text") or "").strip()
            if txt:
                text_parts.append(txt)
                w("[VĂN BẢN MÔ HÌNH]")
                w("  " + _trunc(txt, max_output).replace("\n", "\n  "))

    answer = "\n\n".join(text_parts).strip()
    w("")
    w("=" * 78)
    w("TRẢ LỜI CUỐI CÙNG")
    w("=" * 78)
    if not answer:
        w("(không có text trả lời)")
    elif len(answer) > 4000:
        w(answer[:4000] + f"\n[... {len(answer) - 4000} ký tự bị cắt trong bản đọc — xem answer.txt bản đầy đủ]")
    else:
        w(answer)

    # ── Tóm tắt / metrics ─────────────────────────────────────────────────────
    completed_tools = [p.get("tool") for p in parts
                       if p.get("type") == "tool" and (p.get("state") or {}).get("status") == "completed"]
    uniq = sorted(set(completed_tools))
    tokens = info.get("tokens") or {}
    err = info.get("error")
    w("")
    w("-" * 78)
    w("TÓM TẮT")
    w(f"  Số bước (step-start)   : {step}")
    w(f"  Chuỗi gọi công cụ      : {' -> '.join(tool_seq) if tool_seq else '(không gọi công cụ)'}")
    w(f"  Công cụ hoàn tất (uniq): {', '.join(uniq) if uniq else '-'}")
    w(f"  Độ dài trả lời         : {len(answer)} ký tự / {len(answer.split())} từ")
    if tokens:
        w(f"  Tokens                 : in={tokens.get('input')} out={tokens.get('output')} "
          f"reasoning={tokens.get('reasoning')}")
    if info.get("cost") is not None:
        w(f"  Cost                   : {info.get('cost')}")
    if finish:
        w(f"  Finish reason          : {finish}")
    w(f"  Thời gian lượt (wall)  : {total_ms} ms")
    if err:
        e = err if isinstance(err, str) else (err.get("data", {}) or {}).get("message") or err.get("name") or json.dumps(err)
        w(f"  LỖI                    : {e}")
    w("-" * 78)

    # ── Chẩn đoán bất thường (phát hiện runaway-loop / lặp / cắt token) ────────
    warnings = detect_anomalies(finish, tool_seq, step, answer, err)
    if warnings:
        w("CẢNH BÁO / CHẨN ĐOÁN")
        for wm in warnings:
            w(f"  [!] {wm}")
        w("-" * 78)

    meta = {"id": sample["id"], "question": sample["question"], "gold": gold,
            "model_requested": f"{provider}/{model}", "model_source": model_src,
            "model_answered": f"{ans_prov}/{ans_model}" if ans_prov else None,
            "serving_endpoint": ans_endpoint, "finish": finish,
            "tool_sequence": tool_seq, "tools_completed": uniq, "steps": step,
            "answer_chars": len(answer), "answer_words": len(answer.split()),
            "tokens": tokens, "cost": info.get("cost"), "total_ms": total_ms,
            "error": bool(err), "warnings": warnings}
    return "\n".join(out), answer, meta


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(description="Trace từng bước 1 câu hỏi CVE qua backend secAI :4096")
    src = ap.add_mutually_exclusive_group()
    src.add_argument("--id", default="acc_dd_0001", help="id mẫu trong answers.jsonl/dataset")
    src.add_argument("--question", default=None, help="hỏi trực tiếp (bỏ qua --id)")
    ap.add_argument("--base", default=os.environ.get("ACC_BASE", "http://localhost:4096"))
    ap.add_argument("--provider", default=os.environ.get("ACC_PROVIDER"))
    ap.add_argument("--model", default=os.environ.get("ACC_MODEL"))
    ap.add_argument("--agent", default=os.environ.get("ACC_AGENT", "build"))
    ap.add_argument("--timeout", type=float, default=float(os.environ.get("ACC_TIMEOUT", "300")))
    ap.add_argument("--max-output", type=int, default=1500, help="giới hạn ký tự hiển thị mỗi output")
    ap.add_argument("--out", default=str(REPO / "kpi" / "reports" / "trace"))
    args = ap.parse_args()

    http = Client(args.base, args.timeout)
    sample = resolve_question(args)
    cfg = fetch_config(http)
    endpoints = provider_endpoints(cfg)
    provider, model, model_src = resolve_model(args, cfg)

    print(f"[trace] backend={args.base}  model={provider}/{model} ({model_src})  agent={args.agent}")
    print(f"[trace] Đang gửi câu hỏi (có thể mất tới {int(args.timeout)}s cho model 30B + nhiều tool)...\n",
          flush=True)

    try:
        sid, resp, all_msgs, total_ms = run_turn(http, sample, provider, model, args.agent)
    except HttpError as e:
        if e.status in (401, 403):
            raise SystemExit(f"[LỖI AUTH {e.status}] Chưa có creds. Chạy qua wrapper "
                             f"kpi/scripts/trace_one.sh, hoặc export OPENCODE_SERVER_PASSWORD trước.")
        raise SystemExit(f"[LỖI HTTP] {e}")
    except (socket.timeout, urllib.error.URLError) as e:
        raise SystemExit(f"[LỖI KẾT NỐI] {e} — backend down? kiểm tra 'docker ps'.")

    info = resp.get("info", {}) if isinstance(resp, dict) else {}
    parts, last_info = assistant_stream(all_msgs)
    if not info:
        info = last_info

    trace_txt, answer, meta = render_trace(sample, provider, model, model_src, info, parts,
                                           total_ms, args.max_output, endpoints)
    print(trace_txt)

    # ── Lưu artifact ──────────────────────────────────────────────────────────
    ts = time.strftime("%Y%m%d-%H%M%S")
    run_dir = Path(args.out) / f"{sample['id']}-{ts}"
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "trace.txt").write_text(trace_txt + "\n", encoding="utf-8")
    (run_dir / "messages.json").write_text(json.dumps(all_msgs, ensure_ascii=False, indent=2),
                                           encoding="utf-8")
    (run_dir / "answer.txt").write_text(answer + "\n", encoding="utf-8")
    meta["session_id"] = sid
    meta["backend"] = args.base
    meta["timestamp"] = ts
    (run_dir / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2),
                                       encoding="utf-8")
    print(f"\n[trace] Đã lưu:")
    print(f"  {run_dir / 'trace.txt'}      (bằng chứng đọc được cho người)")
    print(f"  {run_dir / 'messages.json'}  (part thô, đầy đủ — ground truth)")
    print(f"  {run_dir / 'answer.txt'}     (chỉ câu trả lời)")
    print(f"  {run_dir / 'meta.json'}      (model đã trả lời, chuỗi tool, metrics)")


if __name__ == "__main__":
    main()
