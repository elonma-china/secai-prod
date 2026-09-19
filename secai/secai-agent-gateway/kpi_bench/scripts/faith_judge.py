#!/usr/bin/env python3
"""Faithfulness judge trực tiếp (gpt-4o-mini) — RAGAS-style, stdlib only.
Merge answers + contexts theo id → mỗi mẫu hỏi judge: bao nhiêu claim được context hậu thuẫn.
score = supported/total. Output faithfulness-summary.json + llm-faithfulness.jsonl."""
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
items = []
for l in open(ADIR + "/answers.jsonl"):
    a = json.loads(l)
    if not (a.get("answer") or "").strip():
        continue
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
items = items[:N]
print("faithfulness: %d mẫu · judge=%s" % (len(items), MODEL), flush=True)


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
t0 = time.time()


def work(it):
    qid, sc = judge(it)
    with lock:
        if sc is not None:
            scores.append(sc)
        log.write(json.dumps({"id": qid, "true_label": "faithful to KB",
                              "predicted_label": round(sc, 4) if sc is not None else None,
                              "score": round(sc, 4) if sc is not None else None}, ensure_ascii=False) + "\n")
        done[0] += 1
        if done[0] % 50 == 0:
            print("  %d/%d · %.1fs" % (done[0], len(items), time.time() - t0), flush=True)


with ThreadPoolExecutor(max_workers=int(os.environ.get("FAITH_WORKERS", "30"))) as ex:
    list(ex.map(work, items))
log.close()
mean = round(100 * statistics.mean(scores), 2) if scores else None
json.dump({"mean_faithfulness_pct": mean, "n": len(scores), "judge": MODEL, "method": "direct-judge (RAGAS-style)"},
          open(OUT + "/faithfulness-summary.json", "w", encoding="utf-8"), ensure_ascii=False, indent=2)
print("== Faithfulness: %s%% (n=%d) ==" % (mean, len(scores)), flush=True)
