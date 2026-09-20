#!/usr/bin/env python3
"""Faithfulness judge trực tiếp (gpt-4o-mini) — RAGAS-style, stdlib only.
Merge answers + contexts theo id → mỗi mẫu hỏi judge: bao nhiêu claim được context hậu thuẫn.
score = supported/total. Output faithfulness-summary.json + llm-faithfulness.jsonl.

2026-09-20 — ADDITIVE (không đổi số cũ, xem MEMORY/kpi-method-audit.md fix (5) cho R-KPI-FAITH98):
  - SỐ CŨ giữ nguyên 100%: mean_faithfulness_pct/n (gold-refetch context khi FAITH_GOLD_CONTEXTS=1,
    parse-fail loại khỏi mẫu số) — logic KHÔNG đổi, chỉ thêm nhãn context_source cho minh bạch.
  - MỚI #1 — n_total/n_scored/n_parse_fail + mean_faithfulness_standard_pct: parse-fail được
    ĐẾM vào mẫu số (tính là 0/unsupported) thay vì biến mất khỏi trung bình như số cũ.
  - MỚI #2 — mean_faithfulness_retrieved_pct/n_retrieved: nếu bench_answers_gateway.py (đã vá
    2026-09-20) gom được context THẬT từ tool_result SSE (qdrant_search/neo4j_search) vào
    <ADIR>/real-contexts.jsonl, chấm faithfulness RIÊNG trên context đó — đây mới là số ĐÚNG
    CHUẨN RAGAS (context = cái gateway đã retrieve, không phải oracle gold re-fetch). Nếu
    real-contexts.jsonl không có/không có context nào (gateway "chat" profile hiện pre-fetch
    KB grounding Ở TẦNG CODE cho câu có CVE/CWE/CAPEC/ATT&CK-id — routes.py:_ground_chat_prompt,
    commit 69553fe — và KHÔNG emit qua SSE nên tool_result rỗng cho case đó, xem finding trong
    báo cáo agent) → field này là None + context_source_retrieved nêu rõ TODO, KHÔNG âm thầm
    gắn nhãn RAGAS cho số gold-refetch cũ.
"""
import json, os, re, sys, time, urllib.request, statistics
from concurrent.futures import ThreadPoolExecutor
from threading import Lock

KEY = os.environ["OPENAI_API_KEY"]
MODEL = os.environ.get("RAGAS_JUDGE_MODEL", "gpt-4o-mini")
BASE = os.environ.get("RAGAS_JUDGE_BASE_URL", "https://api.openai.com/v1").rstrip("/")
N = int(os.environ.get("RAGAS_SAMPLE", "250"))
ADIR = sys.argv[1] if len(sys.argv) > 1 else "/tmp/faith"
OUT = sys.argv[2] if len(sys.argv) > 2 else "/tmp/faith"
os.makedirs(OUT, exist_ok=True)
# Gold-contexts: lấy chunk THẬT của CVE gold từ Qdrant (chuẩn RAGAS ground-truth, bỏ nhiễu retrieval).
# Bật bằng FAITH_GOLD_CONTEXTS=1; cần QDRANT_URL (loopback VPS). Câu không có CVE gold → fallback contexts.jsonl.
GOLD = os.environ.get("FAITH_GOLD_CONTEXTS", "") in ("1", "true", "yes")
QDRANT = os.environ.get("QDRANT_URL", "http://localhost:6333").rstrip("/")
COLL = os.environ.get("QDRANT_COLLECTION", "cve_kb")


def gold_chunks(cve, k=8):
    body = {"filter": {"must": [{"key": "cve_id", "match": {"value": cve}}]}, "limit": k, "with_payload": True}
    r = urllib.request.Request(f"{QDRANT}/collections/{COLL}/points/scroll", data=json.dumps(body).encode(),
                               headers={"Content-Type": "application/json"}, method="POST")
    d = json.loads(urllib.request.urlopen(r, timeout=30).read())
    return [p.get("payload", {}).get("text", "")[:1200] for p in d.get("result", {}).get("points", [])]


ctx_by = {}
if os.path.exists(ADIR + "/contexts.jsonl"):
    for l in open(ADIR + "/contexts.jsonl"):
        d = json.loads(l); ctx_by[d["id"]] = d.get("contexts") or []

# MỚI (additive) — context THẬT gateway đã retrieve (tool_result SSE qdrant_search/neo4j_search),
# gom bởi bench_answers_gateway.py (đã vá 2026-09-20) → <ADIR>/real-contexts.jsonl. Đây là nguồn
# context ĐÚNG CHUẨN RAGAS; ưu tiên hơn gold-refetch/contexts.jsonl cho mean_faithfulness_retrieved_pct
# (số RIÊNG, không thay số cũ). File không tồn tại/rỗng → real_ctx_by rỗng, xử lý ở dưới.
REAL_CTX_PATH = ADIR + "/real-contexts.jsonl"
real_ctx_by = {}
if os.path.exists(REAL_CTX_PATH):
    for l in open(REAL_CTX_PATH):
        if not l.strip():
            continue
        d = json.loads(l)
        c = d.get("contexts") or []
        if c:
            real_ctx_by[d["id"]] = c

items = []
items_retrieved = []  # MỚI (additive) — song song với items, chỉ id có context THẬT
for l in open(ADIR + "/answers.jsonl"):
    a = json.loads(l)
    if not (a.get("answer") or "").strip():
        continue
    # ── SỐ CŨ — GIỮ NGUYÊN logic chọn context (gold-refetch nếu GOLD, else contexts.jsonl) ──
    c = None
    if GOLD:
        gcve = (a.get("gold") or {}).get("cve_id")
        if gcve:
            try:
                c = gold_chunks(gcve)
            except Exception:
                c = None
    if not c:
        c = ctx_by.get(a["id"])
    if c:
        items.append({"id": a["id"], "q": a["question"], "ans": a["answer"], "ctx": c})
    # ── MỚI (additive) — context THẬT nếu bắt được ──
    rc = real_ctx_by.get(a["id"])
    if rc:
        items_retrieved.append({"id": a["id"], "q": a["question"], "ans": a["answer"], "ctx": rc})
items = items[:N]
items_retrieved = items_retrieved[:N]
print("faithfulness: %d mẫu (gold/simulated context, SỐ CŨ) · %d mẫu (real retrieved-context, MỚI) · judge=%s"
      % (len(items), len(items_retrieved), MODEL), flush=True)


def judge(it):
    ctx = "\n---\n".join(str(x)[:1200] for x in it["ctx"])
    prompt = (
        "Bạn là giám khảo FAITHFULNESS theo chuẩn RAGAS (NLI entailment).\n"
        "Tách CÂU TRẢ LỜI thành các claim FACTUAL về CVE (BỎ QUA câu khuyến nghị/cảnh báo/giải thích "
        "chung/mở đầu — chỉ tính khẳng định sự thật).\n"
        "Đếm bao nhiêu claim được CONTEXT hậu thuẫn HOẶC SUY LUẬN HỢP LÝ (entailment, KHÔNG cần nguyên văn).\n\n"
        "CONTEXT:\n%s\n\nCÂU HỎI: %s\nCÂU TRẢ LỜI: %s\n\n"
        "Chỉ trả JSON: {\"supported\": <int>, \"total\": <int>}" % (ctx, it["q"], it["ans"][:2500])
    )
    # max_tokens RẤT cao: deepseek-v4 reasoning sinh ~3000-4000 token suy luận TRƯỚC khi xuất JSON;
    # đo thực tế: mt=3000 → finish=length, content rỗng (~30% null); mt=8000 → finish=stop, JSON sạch.
    body = {"model": MODEL, "messages": [{"role": "user", "content": prompt}],
            "temperature": 0, "max_tokens": 8000, "response_format": {"type": "json_object"}}
    for attempt in range(3):
        try:
            r = urllib.request.Request(BASE + "/chat/completions", data=json.dumps(body).encode(),
                                       headers={"Content-Type": "application/json", "Authorization": "Bearer " + KEY}, method="POST")
            with urllib.request.urlopen(r, timeout=240) as x:
                txt = json.loads(x.read().decode())["choices"][0]["message"].get("content") or ""
            # parse robust: thử JSON thuần, fallback regex (model reasoning có thể bọc text/xuống dòng)
            sup = tot = None
            try:
                o = json.loads(txt); sup, tot = int(o.get("supported", 0)), int(o.get("total", 0))
            except Exception:
                ms = re.search(r'supported"?\s*[:=]\s*(\d+)', txt, re.I)
                mt = re.search(r'total"?\s*[:=]\s*(\d+)', txt, re.I)
                if ms and mt:
                    sup, tot = int(ms.group(1)), int(mt.group(1))
            if sup is not None and tot is not None:
                return it["id"], (sup / tot if tot else None)
            if attempt == 2:
                return it["id"], None
        except Exception:
            if attempt == 2:
                return it["id"], None
            time.sleep(2)


lock = Lock(); scores = []; log = open(OUT + "/llm-faithfulness.jsonl", "w", encoding="utf-8"); done = [0]
all_scores = []  # MỚI (additive) — SONG SONG với `scores`, giữ cả None (parse-fail) để tính
                 # mean_faithfulness_standard_pct (đếm parse-fail vào mẫu số thay vì loại bỏ).
t0 = time.time()


def work(it):
    qid, sc = judge(it)
    with lock:
        if sc is not None:
            scores.append(sc)   # SỐ CŨ — không đổi: chỉ mẫu parse được mới vào đây
        all_scores.append(sc)   # MỚI — kể cả None
        log.write(json.dumps({"id": qid, "true_label": "faithful to KB",
                              "predicted_label": round(sc, 4) if sc is not None else None,
                              "score": round(sc, 4) if sc is not None else None}, ensure_ascii=False) + "\n")
        done[0] += 1
        if done[0] % 50 == 0:
            print("  %d/%d · %.1fs" % (done[0], len(items), time.time() - t0), flush=True)


with ThreadPoolExecutor(max_workers=int(os.environ.get("FAITH_WORKERS", "30"))) as ex:
    list(ex.map(work, items))
log.close()

# ── SỐ CŨ — GIỮ NGUYÊN 100% (không đổi công thức/biến) ──
mean = round(100 * statistics.mean(scores), 2) if scores else None
n_scored = len(scores)

# ── MỚI (additive) #1 — parse-fail ĐẾM vào mẫu số (standard, minh bạch — không âm thầm loại) ──
n_total = len(items)
n_parse_fail = sum(1 for s in all_scores if s is None)
standard_vals = [s if s is not None else 0.0 for s in all_scores]  # unparseable → 0/unsupported
mean_faithfulness_standard_pct = round(100 * statistics.mean(standard_vals), 2) if standard_vals else None

if GOLD:
    context_source = ("gold-refetch (Qdrant scroll theo gold cve_id, faith_judge.py:gold_chunks) — "
                       "SỐ CŨ, KHÔNG PHẢI retrieved-context (oracle ground-truth, không đúng chuẩn "
                       "RAGAS faithfulness-to-retrieval). Dùng mean_faithfulness_retrieved_pct cho "
                       "số đúng chuẩn khi có.")
else:
    context_source = ("simulated id-aware retrieval fallback (contexts.jsonl từ retrieval_kpi.py) — "
                       "SỐ CŨ, KHÔNG PHẢI context thật gateway đã dùng để sinh câu trả lời.")

# ── MỚI (additive) #2 — chấm faithfulness trên context THẬT (real-contexts.jsonl), nếu có ──
scores_retrieved = []
if items_retrieved:
    log_r = open(OUT + "/llm-faithfulness-retrieved.jsonl", "w", encoding="utf-8")
    done_r = [0]

    def work_retrieved(it):
        qid, sc = judge(it)
        with lock:
            if sc is not None:
                scores_retrieved.append(sc)
            log_r.write(json.dumps({"id": qid, "true_label": "faithful to retrieved-context",
                                    "predicted_label": round(sc, 4) if sc is not None else None,
                                    "score": round(sc, 4) if sc is not None else None}, ensure_ascii=False) + "\n")
            done_r[0] += 1
            if done_r[0] % 50 == 0:
                print("  [retrieved-context] %d/%d" % (done_r[0], len(items_retrieved)), flush=True)

    with ThreadPoolExecutor(max_workers=int(os.environ.get("FAITH_WORKERS", "30"))) as ex:
        list(ex.map(work_retrieved, items_retrieved))
    log_r.close()

n_retrieved = len(items_retrieved)
n_retrieved_scored = len(scores_retrieved)
mean_faithfulness_retrieved_pct = (
    round(100 * statistics.mean(scores_retrieved), 2) if scores_retrieved else None)
if n_retrieved:
    context_source_retrieved = (
        "retrieved-context THẬT từ gateway SSE tool_result (qdrant_search/neo4j_search) — "
        "chuẩn RAGAS (context = cái gateway đã retrieve để sinh câu trả lời, không phải oracle). "
        f"Chỉ phủ {n_retrieved}/{n_total} mẫu (những câu KHÔNG có tool_result KB nào bắt được "
        "bị bỏ qua, KHÔNG tính vào số này).")
else:
    context_source_retrieved = (
        "UNAVAILABLE (n_retrieved=0) — TODO: cần bench_answers_gateway.py (bản đã vá 2026-09-20) "
        "chạy TRƯỚC để sinh <raw-logs>/real-contexts.jsonl. LƯU Ý đã xác minh 2026-09-20: gateway "
        "'chat' profile pre-fetch KB grounding Ở TẦNG CODE cho câu có CVE/CWE/CAPEC/ATT&CK-id "
        "tường minh (routes.py:_ground_chat_prompt, commit 69553fe) và chèn thẳng vào prompt — "
        "KHÔNG emit qua SSE tool_call/tool_result cho nhánh đó, nên n_retrieved sẽ vẫn THẤP/0 cho "
        "phần lớn câu hỏi nêu CVE-id ngay cả sau khi chạy lại — cần gateway team thêm 1 SSE event "
        "(vd 'context'/'grounding') phát nội dung pre-fetch đó để capture đủ.")

summary = {
    # ── SỐ CŨ — KHÔNG ĐỔI TÊN/GIÁ TRỊ ──
    "mean_faithfulness_pct": mean,
    "n": n_scored,
    "judge": MODEL,
    "method": "direct-judge (RAGAS-style)",
    # ── MỚI, ADDITIVE ──
    "context_source": context_source,
    "n_total": n_total,
    "n_scored": n_scored,
    "n_parse_fail": n_parse_fail,
    "mean_faithfulness_standard_pct": mean_faithfulness_standard_pct,
    "mean_faithfulness_retrieved_pct": mean_faithfulness_retrieved_pct,
    "n_retrieved": n_retrieved,
    "n_retrieved_scored": n_retrieved_scored,
    "context_source_retrieved": context_source_retrieved,
}
json.dump(summary, open(OUT + "/faithfulness-summary.json", "w", encoding="utf-8"),
          ensure_ascii=False, indent=2)
print("== Faithfulness (SỐ CŨ, gold/simulated ctx): %s%% (n=%d/%d, parse_fail=%d) ==" %
      (mean, n_scored, n_total, n_parse_fail), flush=True)
print("== Faithfulness STANDARD (parse-fail=0 tính vào mẫu số): %s%% (n=%d) ==" %
      (mean_faithfulness_standard_pct, n_total), flush=True)
print("== Faithfulness RETRIEVED (context THẬT, chuẩn RAGAS): %s%% (n=%d/%d) ==" %
      (mean_faithfulness_retrieved_pct, n_retrieved_scored, n_retrieved), flush=True)
