#!/usr/bin/env python3
"""llm_kpi.py — [LOCAL] KPI tầng LLM: Faithfulness, Answer Relevance, Entity Precision,
Hallucination, Reasoning. Raw log True/Predicted/Score.

- R-KPI-FAITH98 Faithfulness >98% — RAGAS faithfulness (LLM judge = DeepSeek). Cần `ragas`.
- R-KPI-RELEV90 Answer Relevance >90% — Cosine Similarity embed(answer)·embed(question) (bge).
- R-KPI-ENTITY100 Entity Precision =100% — regex CVE/CWE, mỗi id phải grounded (gold/contexts).
- R-KPI-HALLU2 Hallucination <2% — câu có entity bịa / khẳng định CVE giả tồn tại.
- R-KPI-REASONING — heuristic có giải thích từng bước.

Faithfulness/Relevance cần endpoint: LLM (DeepSeek cloud) + embedding bge (EMBED_URL; từ local
mở tunnel `ssh -L 8010:localhost:8010 secai`). entity/hallu/reasoning chạy offline thuần.

Usage:
  python3 llm_kpi.py --answers ../raw-logs/answers.jsonl --contexts ../raw-logs/contexts.jsonl \
    --out ../raw-logs [--ragas-sample 250] [--no-ragas]
"""
import argparse
import json
import math
import re
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

KPI_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(KPI_DIR))
import config  # noqa: E402

CVE_RE = re.compile(r"CVE-\d{4}-\d{4,7}", re.I)
CWE_RE = re.compile(r"CWE-\d+", re.I)
URL_RE = re.compile(r"https?://[^\s)\]]+")

# CWE view/pillar/category hợp lệ của MITRE (KHÔNG phải weakness-level → không nằm trong cwe_refs
# của CVE trong KB, nhưng là id chính danh khi model phân loại). Trích dẫn các id này KHÔNG phải bịa.
MITRE_CWE_META = frozenset({
    # Views
    629, 631, 635, 699, 700, 711, 734, 809, 868, 884, 888, 900, 919, 928, 1000, 1003, 1008,
    1026, 1040, 1128, 1178, 1194, 1200, 1305, 1337, 1340, 1343, 1344, 1350, 1358, 1359, 1387, 1400, 1424,
    # Pillars (CWE-1000)
    284, 435, 664, 682, 691, 693, 697, 703, 707, 710,
    # Categories phổ biến (gồm các id quan sát: 445, 808, 903, 937)
    16, 17, 18, 19, 21, 137, 189, 199, 254, 255, 264, 265, 275, 310, 320, 355, 361, 388, 389, 399,
    411, 417, 429, 438, 442, 445, 452, 465, 485, 490, 503, 504, 505, 519, 569, 632, 633, 634, 657, 675,
    706, 708, 712, 713, 714, 715, 716, 717, 718, 719, 720, 721, 722, 723, 724, 725, 726, 727, 728, 729,
    730, 731, 769, 801, 802, 803, 808, 840, 845, 846, 847, 848, 849, 896, 901, 902, 903, 904, 905, 906,
    907, 929, 930, 931, 932, 933, 934, 935, 936, 937, 938, 939, 944, 1027,
})

# Câu "không tìm thấy / có thể sai chính tả / ví dụ:" → id nêu trong đó là GỢI Ý, không phải khẳng định.
DISCLAIMER_MARKERS = ["không có dữ liệu", "không tìm thấy", "không tồn tại", "chưa được index",
                      "chưa index", "sai chính tả", "có thể là", "có thể:", "ví dụ:", "did you mean",
                      "không chính xác", "ý của bạn"]


def _asserted(text, ident):
    """True nếu ident được KHẲNG ĐỊNH (≥1 lần xuất hiện KHÔNG trong ngữ cảnh disclaimer/gợi ý)."""
    low = text.lower(); idl = ident.lower(); start = 0
    while True:
        pos = low.find(idl, start)
        if pos < 0:
            return False
        if not any(m in low[max(0, pos - 140):pos] for m in DISCLAIMER_MARKERS):
            return True
        start = pos + len(idl)


def _cwe_num(c):
    try:
        return int(c.split("-")[1])
    except (IndexError, ValueError):
        return -1

# Grounding KB: cve/cwe có THỰC trong Qdrant. Build 1 lần qua _post, cache ra file.
_KB_CACHE = {"cve": {}, "cwe": {}}


def _qpost(path, body, timeout=30):
    req = urllib.request.Request(f"{config.QDRANT_URL}{path}", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def kb_has(kind, ident):
    """True nếu cve_id / cwe tồn tại trong Qdrant cve_kb (cache)."""
    ident = ident.upper()
    cache = _KB_CACHE[kind]
    if ident in cache:
        return cache[ident]
    key = "cve_id" if kind == "cve" else "cwe_refs"
    try:
        r = _qpost(f"/collections/{config.QDRANT_COLLECTION}/points/count",
                   {"filter": {"must": [{"key": key, "match": {"value": ident}}]}, "exact": False})
        exists = r.get("result", {}).get("count", 0) > 0
    except Exception:
        exists = True  # lỗi mạng → không phạt (coi grounded)
    cache[ident] = exists
    return exists


def build_kb_grounding(answers, cache_path=None):
    """Quét toàn bộ id được nhắc → tra Qdrant 1 lần. Lưu cache nếu cache_path."""
    if cache_path and Path(cache_path).exists():
        global _KB_CACHE
        _KB_CACHE = json.loads(Path(cache_path).read_text())
        return
    cves, cwes = set(), set()
    for a in answers:
        c1, c2 = extract_ids(a["answer"])
        cves |= c1; cwes |= c2
    for c in sorted(cves):
        kb_has("cve", c)
    for c in sorted(cwes):
        kb_has("cwe", c)
    if cache_path:
        Path(cache_path).parent.mkdir(parents=True, exist_ok=True)
        Path(cache_path).write_text(json.dumps(_KB_CACHE, ensure_ascii=False))
REASON_MARKERS = ["bước 1", "bước 2", "1.", "2.", "3.", "đầu tiên", "trước tiên", "tiếp theo",
                  "sau đó", "cuối cùng", "thứ nhất", "thứ hai", "vì", "do đó", "bởi vì", "->", "→"]


# ---------------- offline metrics ----------------
def extract_ids(text):
    cves = set(c.upper() for c in CVE_RE.findall(text))
    cwes = set(c.upper() for c in CWE_RE.findall(text))
    return cves, cwes


def entity_precision(a):
    """Mỗi CVE/CWE nhắc trong answer phải grounded (==gold, hoặc trong contexts)."""
    text = a["answer"]
    cves, cwes = extract_ids(text)
    gold_cve = {(a["gold"].get("cve_id") or "").upper()} if a["gold"].get("cve_id") else set()
    gold_cwe = {c.upper() for c in (a["gold"].get("cwe") or [])}
    ctx_cves = set(c.upper() for ctx in a.get("contexts", []) for c in CVE_RE.findall(ctx))
    ctx_cwes = set(c.upper() for ctx in a.get("contexts", []) for c in CWE_RE.findall(ctx))
    # cộng cả CVE/CWE trong câu hỏi (người dùng nêu) là grounded
    q_cves, q_cwes = extract_ids(a["question"])
    grounded_cve = gold_cve | ctx_cves | q_cves
    grounded_cwe = gold_cwe | ctx_cwes | q_cwes
    mentioned = list(cves) + list(cwes)
    if not mentioned:
        return None, [], []  # không nhắc id → loại khỏi mẫu số
    # id "bad" = bịa THẬT SỰ: không khớp gold/contexts/question, không trong KB,
    #   KHÔNG phải CWE category/view hợp lệ của MITRE, VÀ được model KHẲNG ĐỊNH (không phải gợi ý "did you mean").
    bad = [c for c in cves if c not in grounded_cve and not kb_has("cve", c) and _asserted(text, c)]
    bad += [c for c in cwes if c not in grounded_cwe and not kb_has("cwe", c)
            and _cwe_num(c) not in MITRE_CWE_META and _asserted(text, c)]
    prec = (len(mentioned) - len(bad)) / len(mentioned)
    return prec, mentioned, bad


def is_hallucinated(a):
    """True nếu: (trap) khẳng định CVE/sản phẩm giả tồn tại; hoặc nhắc CVE/CWE bịa; hoặc URL lạ."""
    text = a["answer"]
    low = text.lower()
    tags = a.get("tags", [])
    fake_cve = (a["gold"].get("fake_cve") or "")
    # trap fake CVE: nếu KHÔNG nói "không tìm thấy" mà mô tả như thật → hallucinate
    if "trap" in tags and fake_cve:
        refused = any(k in low for k in ["không tìm thấy", "không tồn tại", "chưa index",
                                          "không có thông tin", "không chính xác"])
        return (not refused)
    # entity bịa
    prec, mentioned, bad = entity_precision(a)
    if prec is not None and bad:
        return True
    return False


def has_reasoning(a):
    low = a["answer"].lower()
    return any(m in low for m in REASON_MARKERS) and len(a["answer"].split()) >= 40


# ---------------- embedding (cho answer relevance cosine) ----------------
def _post(url, body, timeout=60):
    req = urllib.request.Request(url, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def embed(text):
    r = _post(f"{config.EMBED_URL}/v1/embeddings", {"model": config.EMBED_MODEL, "input": text[:4000]})
    return r["data"][0]["embedding"]


def cosine(a, b):
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a)); nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def judge_relevance(question, answer):
    """Answer-relevancy bằng LLM-judge (local LLM). Thay cosine bge — vốn (a) bị trần thang
    ~0.7-0.85, (b) lỗi 512-token làm drop ~60% câu. Judge chấm độ liên quan trực tiếp, không drop.
    Điểm 0..1; trả None nếu parse lỗi (loại khỏi mẫu, không tính 0 oan)."""
    prompt = (
        "Bạn là giám khảo chấm ĐỘ LIÊN QUAN của câu trả lời với câu hỏi (answer relevancy).\n"
        "Thang 0.0–1.0: 1.0=trả lời trúng & đúng trọng tâm câu hỏi; 0.5=liên quan nhưng lan man/thiếu; "
        "0.0=lạc đề. CHỈ xét độ liên quan với câu hỏi, KHÔNG chấm đúng/sai sự thật.\n\n"
        f"CÂU HỎI: {question}\n\nCÂU TRẢ LỜI: {answer[:3000]}\n\n"
        'Chỉ trả JSON: {"score": <số thực 0..1>}'
    )
    body = {"model": config.LLM_MODEL, "messages": [{"role": "user", "content": prompt}],
            "temperature": 0, "max_tokens": 800, "chat_template_kwargs": {"enable_thinking": False}}
    headers = {"Content-Type": "application/json"}
    if getattr(config, "LLM_API_KEY", ""):
        headers["Authorization"] = "Bearer " + config.LLM_API_KEY
    for _ in range(2):
        try:
            req = urllib.request.Request(f"{config.LLM_BASE_URL}/chat/completions",
                                         data=json.dumps(body).encode(), headers=headers)
            with urllib.request.urlopen(req, timeout=120) as r:
                txt = json.loads(r.read())["choices"][0]["message"]["content"]
            m = re.search(r'"score"\s*:\s*([0-9]*\.?[0-9]+)', txt)
            if m:
                return max(0.0, min(1.0, float(m.group(1))))
        except Exception:
            continue
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--answers", default=str(KPI_DIR / "raw-logs" / "answers.jsonl"))
    ap.add_argument("--contexts", default=str(KPI_DIR / "raw-logs" / "contexts.jsonl"))
    ap.add_argument("--out", default=str(KPI_DIR / "raw-logs"))
    ap.add_argument("--ragas-sample", type=int, default=250)
    ap.add_argument("--no-ragas", action="store_true")
    ap.add_argument("--no-embed", action="store_true", help="bỏ answer-relevance cosine (không có embed endpoint)")
    ap.add_argument("--no-kb", action="store_true", help="bỏ grounding KB (Qdrant không reachable)")
    ap.add_argument("--kb-cache", default=str(KPI_DIR / "raw-logs" / "kb-grounding-cache.json"))
    ap.add_argument("--faith-gold", action="store_true", help="faithfulness dùng chunk thật của gold CVE (cần Qdrant)")
    ap.add_argument("--only-faith", action="store_true", help="chỉ chạy faithfulness → faithfulness-summary.json")
    args = ap.parse_args()

    out_dir = Path(args.out); out_dir.mkdir(parents=True, exist_ok=True)
    answers = [json.loads(l) for l in Path(args.answers).read_text(encoding="utf-8").splitlines() if l.strip()]
    ctx_map = {}
    if Path(args.contexts).exists():
        for l in Path(args.contexts).read_text(encoding="utf-8").splitlines():
            if l.strip():
                c = json.loads(l); ctx_map[c["id"]] = c.get("contexts", [])
    for a in answers:
        a["contexts"] = ctx_map.get(a["id"], [])

    # --only-faith: chỉ chạy faithfulness (RAGAS) → faithfulness-summary.json, không đụng log khác.
    if getattr(args, "only_faith", False):
        try:
            fr = run_faithfulness(answers, out_dir, args.ragas_sample, gold_contexts=args.faith_gold)
        except Exception as e:
            fr = {"error": repr(e)[:200]}
        (out_dir / "faithfulness-summary.json").write_text(json.dumps(fr, ensure_ascii=False, indent=2))
        print("FAITHFULNESS:", json.dumps(fr, ensure_ascii=False))
        return

    # Grounding KB: tra Qdrant cho mọi CVE/CWE được nhắc (chạy trên VPS hoặc dùng cache).
    if not args.no_kb:
        print("Đang build KB grounding (tra Qdrant cho id được nhắc)...", flush=True)
        build_kb_grounding(answers, args.kb_cache)

    summary = {}

    # 1. Entity Precision (offline, mọi answer có nhắc id)
    ent_log = (out_dir / "llm-entity-precision.jsonl").open("w", encoding="utf-8")
    precs = []
    for a in answers:
        prec, mentioned, bad = entity_precision(a)
        if prec is None:
            continue
        precs.append(prec)
        ent_log.write(json.dumps({"id": a["id"], "true_label": "grounded ids",
                                  "predicted_label": mentioned, "bad": bad,
                                  "score": round(prec, 4)}, ensure_ascii=False) + "\n")
    ent_log.close()
    ent_pct = 100 * sum(precs) / len(precs) if precs else 100.0
    perfect = 100 * sum(1 for p in precs if p >= 1.0) / len(precs) if precs else 100.0
    summary["R-KPI-ENTITY100"] = {"mean_precision_pct": round(ent_pct, 3),
                                  "perfect_samples_pct": round(perfect, 2), "n": len(precs)}

    # 2. Hallucination (offline, mọi answer)
    hal_log = (out_dir / "llm-hallucination.jsonl").open("w", encoding="utf-8")
    halluc = 0
    for a in answers:
        h = is_hallucinated(a)
        halluc += 1 if h else 0
        hal_log.write(json.dumps({"id": a["id"], "true_label": "grounded",
                                  "predicted_label": "hallucinated" if h else "grounded",
                                  "score": 1 if h else 0}, ensure_ascii=False) + "\n")
    hal_log.close()
    hal_pct = 100 * halluc / len(answers)
    summary["R-KPI-HALLU2"] = {"hallucination_rate_pct": round(hal_pct, 3), "n": len(answers), "halluc": halluc}

    # 3. Reasoning (offline)
    reas = sum(1 for a in answers if has_reasoning(a))
    summary["R-KPI-REASONING"] = {"reasoning_present_pct": round(100 * reas / len(answers), 2), "n": len(answers)}

    # 4. Answer Relevance — LLM-judge (local LLM). Thay cosine bge (trần thang + drop 512-token).
    if not args.no_embed:
        rel_log = (out_dir / "llm-answer-relevance.jsonl").open("w", encoding="utf-8")
        sample = [a for a in answers if a["answer"].strip()][: args.ragas_sample]
        scores = []
        with ThreadPoolExecutor(max_workers=8) as ex:
            for a, sc in ex.map(lambda x: (x, judge_relevance(x["question"], x["answer"])), sample):
                if sc is None:
                    rel_log.write(json.dumps({"id": a["id"], "error": "judge_parse_fail"}, ensure_ascii=False) + "\n")
                    continue
                scores.append(sc)
                rel_log.write(json.dumps({"id": a["id"], "true_label": "relevant",
                                          "predicted_label": round(sc, 4), "score": round(sc, 4),
                                          "judge": config.LLM_MODEL}, ensure_ascii=False) + "\n")
        rel_log.close()
        summary["R-KPI-RELEV90"] = {"mean_relevance_pct": round(100 * sum(scores) / len(scores), 2) if scores else None,
                                    "n": len(scores), "method": "llm-judge"}

    # 5. Faithfulness (RAGAS) — cần ragas + LLM
    if not args.no_ragas:
        try:
            summary["R-KPI-FAITH98"] = run_faithfulness(answers, out_dir, args.ragas_sample, gold_contexts=args.faith_gold)
        except Exception as e:
            summary["R-KPI-FAITH98"] = {"error": repr(e)[:200], "note": "cần pip install ragas + LLM key"}

    (out_dir / "llm-kpi-summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2))
    print("\n=== LLM KPI ===")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


def fetch_gold_contexts(cve_id, limit=8):
    """Lấy các chunk THẬT của gold CVE từ Qdrant làm context (đúng bản ghi KB của CVE đó)."""
    body = {"filter": {"must": [{"key": "cve_id", "match": {"value": cve_id}}]},
            "limit": limit, "with_payload": True}
    r = _qpost(f"/collections/{config.QDRANT_COLLECTION}/points/scroll", body)
    return [(p["payload"].get("text") or "")[:1500] for p in r.get("result", {}).get("points", [])]


def run_faithfulness(answers, out_dir, n_sample, gold_contexts=False):
    """RAGAS faithfulness. gold_contexts=True → dùng chunk thật của gold CVE (đúng phương pháp,
    tránh nhiễu retrieval-miss). LLM-judge độc lập (config.RAGAS_JUDGE_*)."""
    from ragas import evaluate
    from ragas.metrics import faithfulness
    from ragas.llms import LangchainLLMWrapper
    from langchain_openai import ChatOpenAI
    from datasets import Dataset

    if gold_contexts:
        items = []
        for a in answers:
            if not a["answer"].strip() or not a.get("gold", {}).get("cve_id"):
                continue
            gc = fetch_gold_contexts(a["gold"]["cve_id"])
            if gc:
                a = {**a, "contexts": gc}
                items.append(a)
            if len(items) >= n_sample:
                break
    else:
        items = [a for a in answers if a.get("contexts") and a["answer"].strip()][:n_sample]
    if not items:
        return {"error": "không có sample kèm contexts (chạy retrieval_kpi hoặc bật --faith-gold)"}
    ds = Dataset.from_dict({
        "question": [a["question"] for a in items],
        "answer": [a["answer"] for a in items],
        "contexts": [a["contexts"] for a in items],
    })
    # Judge ĐỘC LẬP (mặc định gpt-4o-mini) — không trùng model under test (deepseek).
    llm = LangchainLLMWrapper(ChatOpenAI(model=config.RAGAS_JUDGE_MODEL, base_url=config.RAGAS_JUDGE_BASE_URL,
                                         api_key=config.RAGAS_JUDGE_API_KEY, temperature=0.0, timeout=120))
    res = evaluate(ds, metrics=[faithfulness], llm=llm)
    df = res.to_pandas()
    log = (out_dir / "llm-faithfulness.jsonl").open("w", encoding="utf-8")
    scores = []
    for a, (_, row) in zip(items, df.iterrows()):
        sc = float(row.get("faithfulness", float("nan")))
        if sc == sc:  # not nan
            scores.append(sc)
        log.write(json.dumps({"id": a["id"], "true_label": "faithful to KB",
                              "predicted_label": round(sc, 4) if sc == sc else None,
                              "score": round(sc, 4) if sc == sc else None}, ensure_ascii=False) + "\n")
    log.close()
    return {"mean_faithfulness_pct": round(100 * sum(scores) / len(scores), 2) if scores else None, "n": len(scores)}


if __name__ == "__main__":
    main()
