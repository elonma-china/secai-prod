#!/usr/bin/env python3
"""task_kpi.py — [LOCAL] Accuracy 3 nhiệm vụ LLM theo hợp đồng, raw log True/Predicted/Score.

- R-LLM-SAST85: SAST phát hiện & phân loại CWE đúng ≥85% (OWASP Top10/CWE Top25).
- R-LLM-EXPLOIT85: Exploit Explanation đúng logic & đầy đủ ≥85%.
- R-LLM-REMED60: Auto-remediation đề xuất bản vá hợp lệ ≥60%.

Chấm bằng cùng matcher đã dùng ở acceptance (deterministic), thêm trích True/Predicted:
- SAST: True=gold CWE, Predicted=CWE regex tìm thấy trong answer, score=grade.
- Exploit/Remediation: True="đạt tiêu chí", Predicted=pass/fail, score=grade.

Input: answers.jsonl (từ gen_answers.py). Output: sast.jsonl, exploit-explanation.jsonl,
auto-remediation.jsonl + task-summary.json.

Usage: python3 task_kpi.py [--answers ../raw-logs/answers.jsonl] [--out ../raw-logs]
"""
import argparse
import json
import re
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

KPI_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(KPI_DIR))
import config  # noqa: E402 — dùng RAGAS_JUDGE_* (judge ĐỘC LẬP) cho llm_judge_grade() [MỚI, ADDITIVE]

VI = set("àáảãạăằắẳẵặâầấẩẫậèéẻẽẹêềếểễệìíỉĩịòóỏõọôồốổỗộơờớởỡợùúủũụưừứửữựỳýỷỹỵđ")
CWE_RE = re.compile(r"CWE-\d+", re.I)

# Bộ check phụ theo task (port từ eval/grader/graders/*.ts)
EXPLOIT_KW = ["bộ nhớ", "memory", "stack", "heap", "overflow", "payload", "lợi dụng", "exploit",
              "khai thác", "cơ chế", "mechanism", "bypass", "entity", "parser", "JNDI", "lookup",
              "inject", "execute", "trigger", "gây ra", "cho phép", "tấn công", "attacker", "kẻ tấn công"]
REMED_SECTIONS = ["Đánh giá", "Before", "After", "Diff", "Giải thích"]

# Substring duy nhất của agent/hooks.py:_CHAT_EMPTY_FALLBACK (reason=chat_kb_required_exhausted) —
# agent ship khi retrieval/KB bắt buộc mà hết lượt retry (vd embedding/qdrant lỗi kết nối lúc đo),
# KHÔNG phải model không biết trả lời. Nếu accuracy task thấp chủ yếu vì câu này lặp lại thì đó là
# tín hiệu lỗi hạ tầng retrieval tại thời điểm đo, không phải model suy giảm — không được dùng để
# kết luận cần finetune mà không soát qua refusal_rate dưới đây.
KB_REFUSAL_MARKER = "chưa tổng hợp được câu trả lời từ dữ liệu nội bộ"
# Tỉ lệ refusal trong 1 capability từ đó trở lên -> accuracy_pct của capability đó không đáng tin
# cho quyết định finetune (xem check_finetune_thresholds.py qua adapt_task_report.py).
REFUSAL_RATE_UNRELIABLE = 0.5


def is_kb_refusal(a):
    return KB_REFUSAL_MARKER in (a.get("answer") or "")


def vi_ratio(t):
    L = [c for c in t.lower() if c.isalpha()]
    return sum(1 for c in L if c in VI) / len(L) if L else 0.0


def base_grade(exp, text, tools):
    low = text.lower()
    ck = []
    for s in exp.get("must_contain", []):
        ck.append(s.lower() in low)
    if exp.get("must_contain_any"):
        ck.append(any(s.lower() in low for s in exp["must_contain_any"]))
    for s in exp.get("must_not_contain", []):
        ck.append(s.lower() not in low)
    for t in exp.get("must_use_tool", []):
        ck.append(t in tools)
    for c in exp.get("must_find_cwe", []):
        ck.append(c.lower() in low)
    for s in exp.get("must_have_sections", []):
        ck.append(s.lower() in low)
    lang = exp.get("language") or exp.get("target_lang")
    if lang == "vi":
        ck.append(vi_ratio(text) >= 0.02)
    if exp.get("min_words"):
        ck.append(len(text.split()) >= exp["min_words"])
    return ck


# [LEGACY — giữ nguyên, KHÔNG đổi] grade_sast/grade_exploit/grade_remediation: chấm bằng
# substring/keyword/regex/vi_ratio/min_words (base_grade), KHÔNG execution/KHÔNG judge ngữ nghĩa.
# Giữ lại để không đổi ý nghĩa field cũ (R-LLM-SAST85/EXPLOIT85/REMED60). Xem
# grade_sast_llmjudge/grade_exploit_llmjudge/grade_remediation_llmjudge bên dưới cho biến thể
# LLM-judge rubric ĐỘC LẬP (chuẩn ngành), báo THÊM ở field *_llmjudge riêng, không thay số cũ.
# TODO: pass@k với THỰC THI thật (chạy scanner SAST / trigger exploit PoC trong sandbox) — cần
# tài nguyên ngoài (sandbox thực thi code không tin cậy), ngoài phạm vi framework/TODO ở đây.
def grade_sast(a):
    exp = a["expected"]
    ck = base_grade(exp, a["answer"], a["tools"])
    # check phụ SAST: nhắc CWE + có phân tích
    found_cwe = sorted(set(c.upper() for c in CWE_RE.findall(a["answer"])))
    gold_cwe = [c.upper() for c in (exp.get("must_find_cwe") or a["gold"].get("cwe") or [])]
    ck.append(any(c in found_cwe for c in gold_cwe) if gold_cwe else True)
    score = sum(ck) / len(ck) if ck else 1.0
    return {"true_label": gold_cwe, "predicted_label": found_cwe, "score": round(score, 3), "pass": score >= 0.75}


def grade_exploit(a):
    exp = a["expected"]
    ck = base_grade(exp, a["answer"], a["tools"])
    ck.append(any(k in a["answer"].lower() for k in EXPLOIT_KW))  # has mechanism
    score = sum(ck) / len(ck) if ck else 1.0
    return {"true_label": "đúng logic & đầy đủ", "predicted_label": "pass" if score >= 0.75 else "fail",
            "score": round(score, 3), "pass": score >= 0.75}


def grade_remediation(a):
    exp = a["expected"]
    ck = base_grade(exp, a["answer"], a["tools"])
    # remediation: có đề xuất bản vá (must_contain_any fix kw đã trong exp) — đủ
    score = sum(ck) / len(ck) if ck else 1.0
    return {"true_label": "bản vá hợp lệ", "predicted_label": "pass" if score >= 0.75 else "fail",
            "score": round(score, 3), "pass": score >= 0.75}


# ==================== [MỚI — chuẩn ngành, ADDITIVE] LLM-judge rubric (judge ĐỘC LẬP) ====================
# Thay base_grade (substring/keyword) làm CĂN CỨ CHẤM CHÍNH bằng judge ngữ nghĩa độc lập
# (config.RAGAS_JUDGE_BASE_URL/RAGAS_JUDGE_MODEL — KHÁC model under test config.LLM_MODEL).
# base_grade/EXPLOIT_KW/vi_ratio vẫn được GIỮ và tính kèm làm tín hiệu PHỤ (aux_checks), không
# quyết định pass/fail của biến thể này.
RUBRICS = {
    "sast": (
        "Bạn là giám khảo an ninh mạng chấm câu trả lời SAST (phát hiện & phân loại lỗ hổng).\n"
        "Tiêu chí (rubric), chấm 0.0-1.0:\n"
        "1) Xác định ĐÚNG loại lỗ hổng / CWE khớp với gold (nếu có nêu CWE gold).\n"
        "2) Giải thích ĐÚNG cơ chế/nguyên nhân gốc (root cause) của lỗ hổng.\n"
        "3) Không có sai sót kỹ thuật nghiêm trọng (technical hallucination).\n"
    ),
    "exploit": (
        "Bạn là giám khảo an ninh mạng chấm câu GIẢI THÍCH KHAI THÁC (exploit explanation).\n"
        "Tiêu chí (rubric), chấm 0.0-1.0:\n"
        "1) Giải thích ĐÚNG cơ chế khai thác cụ thể (không chỉ liệt kê từ khóa chung chung).\n"
        "2) Logic nhất quán với loại lỗ hổng/CWE liên quan.\n"
        "3) Đầy đủ: có điều kiện tiên quyết, cách kích hoạt, và tác động.\n"
    ),
    "remediation": (
        "Bạn là giám khảo an ninh mạng chấm ĐỀ XUẤT BẢN VÁ (auto-remediation).\n"
        "Tiêu chí (rubric), chấm 0.0-1.0:\n"
        "1) Đề xuất bản vá CỤ THỂ, hợp lệ, giải quyết đúng nguyên nhân gốc.\n"
        "2) Không có lỗi kỹ thuật rõ ràng (vd code vá không hợp lệ, phá vỡ chức năng).\n"
        "3) Có giải thích đủ rõ vì sao bản vá này khắc phục lỗ hổng.\n"
        f"(Gợi ý cấu trúc thường gặp, KHÔNG bắt buộc: {', '.join(REMED_SECTIONS)})\n"
    ),
}


def _judge_call(prompt, timeout=90):
    body = {"model": config.RAGAS_JUDGE_MODEL, "messages": [{"role": "user", "content": prompt}],
            "temperature": 0, "max_tokens": 500}
    headers = {"Content-Type": "application/json"}
    if getattr(config, "RAGAS_JUDGE_API_KEY", ""):
        headers["Authorization"] = "Bearer " + config.RAGAS_JUDGE_API_KEY
    req = urllib.request.Request(f"{config.RAGAS_JUDGE_BASE_URL}/chat/completions",
                                 data=json.dumps(body).encode(), headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())["choices"][0]["message"]["content"]


def llm_judge_grade(cap, a):
    """Chấm ngữ nghĩa bằng LLM-judge ĐỘC LẬP theo rubric (thay khớp chuỗi làm căn cứ chính).
    Trả (score 0..1 hoặc None nếu lỗi judge, raw_text hoặc None)."""
    exp = a["expected"]
    gold = a.get("gold", {})
    hints = []
    if exp.get("must_find_cwe") or gold.get("cwe"):
        hints.append(f"CWE gold tham khảo: {exp.get('must_find_cwe') or gold.get('cwe')}")
    if exp.get("must_have_sections"):
        hints.append(f"Các mục nên có: {exp.get('must_have_sections')}")
    hint_txt = "\n".join(hints) if hints else "(không có gợi ý gold bổ sung)"
    prompt = (
        RUBRICS[cap] +
        f"\nGỢI Ý GOLD (tham khảo, KHÔNG bắt buộc khớp chữ):\n{hint_txt}\n\n"
        f"CÂU HỎI/NHIỆM VỤ:\n{a['question'][:2000]}\n\n"
        f"CÂU TRẢ LỜI CỦA MODEL:\n{a['answer'][:4000]}\n\n"
        'Chấm điểm theo mức độ đạt các tiêu chí trên (1.0=đạt đầy đủ, 0.5=đạt một phần, '
        '0.0=không đạt/sai). Chỉ trả JSON: {"score": <số thực 0..1>, "reason": "<lý do ngắn>"}'
    )
    for _ in range(2):
        try:
            txt = _judge_call(prompt)
            m = re.search(r'"score"\s*:\s*([0-9]*\.?[0-9]+)', txt)
            if m:
                return max(0.0, min(1.0, float(m.group(1)))), txt[:300]
        except Exception:
            continue
    return None, None


def _grade_llmjudge(cap, a):
    score, raw = llm_judge_grade(cap, a)
    aux = base_grade(a["expected"], a["answer"], a["tools"])  # tín hiệu PHỤ, không quyết định pass/fail
    row = {"aux_checks": aux, "aux_score": round(sum(aux) / len(aux), 3) if aux else None,
           "grader": "llm-judge-rubric (independent)", "judge_model": config.RAGAS_JUDGE_MODEL}
    if score is None:
        row.update({"score_llmjudge": None, "pass_llmjudge": None, "judge_error": True, "judge_raw": raw})
        return row
    row.update({"score_llmjudge": round(score, 3), "pass_llmjudge": score >= 0.75,
                "judge_error": False, "judge_raw": raw})
    return row


def grade_sast_llmjudge(a):
    exp = a["expected"]
    found_cwe = sorted(set(c.upper() for c in CWE_RE.findall(a["answer"])))
    gold_cwe = [c.upper() for c in (exp.get("must_find_cwe") or a["gold"].get("cwe") or [])]
    row = _grade_llmjudge("sast", a)
    row.update({"true_label": gold_cwe, "predicted_label": found_cwe})
    return row


def grade_exploit_llmjudge(a):
    return _grade_llmjudge("exploit", a)


def grade_remediation_llmjudge(a):
    return _grade_llmjudge("remediation", a)


TASKS = {
    "sast": ("sast.jsonl", grade_sast, "R-LLM-SAST85", 85),
    "exploit": ("exploit-explanation.jsonl", grade_exploit, "R-LLM-EXPLOIT85", 85),
    "remediation": ("auto-remediation.jsonl", grade_remediation, "R-LLM-REMED60", 60),
}

# [MỚI — ADDITIVE] biến thể LLM-judge rubric độc lập, chạy SONG SONG với TASKS ở trên (không thay).
TASKS_LLMJUDGE = {
    "sast": ("sast-llmjudge.jsonl", grade_sast_llmjudge, "R-LLM-SAST85", 85),
    "exploit": ("exploit-explanation-llmjudge.jsonl", grade_exploit_llmjudge, "R-LLM-EXPLOIT85", 85),
    "remediation": ("auto-remediation-llmjudge.jsonl", grade_remediation_llmjudge, "R-LLM-REMED60", 60),
}


def grade_sast_majority(base_items, extra_run_files, out_dir):
    """[LEGACY — giữ nguyên, KHÔNG đổi, theo yêu cầu giữ cả 2 chế độ chấm cũ] SAST theo đa số 2/3
    lượt (giống aggregate_results.py của bộ nghiệm thu).
    base_items = lượt 1 (đầy đủ gold/expected); extra_run_files = results phụ (id,text,tools).
    1 câu pass nếu ≥ ceil(N/2) lượt pass → khử flakiness 'đáp án ngắn quên in nhãn CWE'."""
    runs_extra = []
    for f in extra_run_files:
        m = {}
        for line in Path(f).read_text(encoding="utf-8").splitlines():
            if line.strip():
                r = json.loads(line)
                m[r["id"]] = {"answer": r.get("text", ""), "tools": r.get("tools") or []}
        runs_extra.append(m)
    logf = (out_dir / "sast.jsonl").open("w", encoding="utf-8")
    npass = 0
    for a in base_items:
        verdicts = [grade_sast(a)["pass"]]  # lượt 1 = answers gốc
        for m in runs_extra:
            rr = m.get(a["id"])
            if rr is not None:
                verdicts.append(grade_sast({**a, "answer": rr["answer"], "tools": rr["tools"]})["pass"])
        need = (len(verdicts) + 1) // 2  # ceil(N/2): 3 lượt → cần ≥2
        passes = sum(1 for v in verdicts if v)
        ok = passes >= need
        npass += 1 if ok else 0
        logf.write(json.dumps({"id": a["id"], "capability": "sast", "difficulty": a["difficulty"],
                               "runs": len(verdicts), "passes": passes, "need": need,
                               "pass": ok, "method": "majority-2/3"}, ensure_ascii=False) + "\n")
    logf.close()
    return npass, len(base_items)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--answers", default=str(KPI_DIR / "raw-logs" / "answers.jsonl"))
    ap.add_argument("--out", default=str(KPI_DIR / "raw-logs"))
    ap.add_argument("--sast-runs", nargs="*", default=[],
                    help="file results phụ (id,text,tools) cho SAST majority 2/3 (lượt 2,3...)")
    ap.add_argument("--no-llmjudge", action="store_true",
                    help="bỏ pass chấm LLM-judge rubric ĐỘC LẬP [MỚI, ADDITIVE] (vd RAGAS_JUDGE endpoint không reachable)")
    ap.add_argument("--llmjudge-workers", type=int, default=6)
    args = ap.parse_args()

    answers = [json.loads(l) for l in Path(args.answers).read_text(encoding="utf-8").splitlines() if l.strip()]
    by_cap = {}
    for a in answers:
        by_cap.setdefault(a["capability"], []).append(a)

    out_dir = Path(args.out)
    summary = {}
    print("\n=== Task accuracy KPI ===")
    for cap, (fname, grader, code, thr) in TASKS.items():
        items = by_cap.get(cap, [])
        if cap == "sast" and args.sast_runs:
            npass, ntot = grade_sast_majority(items, args.sast_runs, out_dir)
            note = f" [majority {len(args.sast_runs)+1} lượt]"
        else:
            logf = (out_dir / fname).open("w", encoding="utf-8")
            npass = 0
            for a in items:
                g = grader(a)
                npass += 1 if g["pass"] else 0
                logf.write(json.dumps({"id": a["id"], "capability": cap, "difficulty": a["difficulty"],
                                       **g}, ensure_ascii=False) + "\n")
            logf.close()
            ntot = len(items); note = ""
        acc = 100 * npass / ntot if ntot else 0.0
        ok = "✓" if acc >= thr else "✗"
        n_refusal = sum(1 for a in items if is_kb_refusal(a))
        refusal_rate = round(n_refusal / ntot, 3) if ntot else 0.0
        summary[code] = {"capability": cap, "n": ntot, "pass": npass,
                         "accuracy_pct": round(acc, 2), "threshold": thr, "passed": acc >= thr,
                         "grader": "keyword/substring (legacy, base_grade)",
                         "refusal_count": n_refusal, "refusal_rate": refusal_rate}
        print(f"  {cap:12} {npass}/{ntot} = {acc:.1f}%  (ngưỡng ≥{thr}%) {ok}{note}  → {fname}")
        if ntot and refusal_rate >= REFUSAL_RATE_UNRELIABLE:
            print(f"    CẢNH BÁO [{cap}]: {n_refusal}/{ntot} câu ({refusal_rate*100:.0f}%) là fallback "
                  f"'KB retrieval exhausted' (agent/hooks.py reason=chat_kb_required_exhausted) — "
                  f"nghi do lỗi hạ tầng retrieval (embedding/qdrant không reachable) lúc đo, KHÔNG "
                  f"phải model yếu. accuracy_pct={acc:.1f}% ở trên KHÔNG đáng tin cho quyết định "
                  f"finetune — adapt_task_report.py sẽ defer capability này.")

    # [MỚI — chuẩn ngành, ADDITIVE] pass thứ 2: LLM-judge rubric ĐỘC LẬP — field *_llmjudge RIÊNG,
    # KHÔNG thay/xoá số cũ ở trên. Chạy trên cùng `by_cap` (không chạy lại majority-2/3, chỉ single-pass
    # judge vì majority-2/3 là cơ chế khử flakiness của chấm-chuỗi, không áp dụng cho judge ngữ nghĩa).
    if not args.no_llmjudge:
        print("\n=== Task accuracy KPI — LLM-judge rubric (độc lập, ADDITIVE) ===")
        for cap, (fname, grader, code, thr) in TASKS_LLMJUDGE.items():
            items = by_cap.get(cap, [])
            logf = (out_dir / fname).open("w", encoding="utf-8")
            npass = n_graded = n_err = 0
            with ThreadPoolExecutor(max_workers=args.llmjudge_workers) as ex:
                rows = list(ex.map(grader, items))
            for a, g in zip(items, rows):
                logf.write(json.dumps({"id": a["id"], "capability": cap, "difficulty": a["difficulty"],
                                       **g}, ensure_ascii=False) + "\n")
                if g.get("judge_error"):
                    n_err += 1
                    continue
                n_graded += 1
                npass += 1 if g["pass_llmjudge"] else 0
            logf.close()
            acc_v2 = 100 * npass / n_graded if n_graded else None
            ok_v2 = "?" if acc_v2 is None else ("✓" if acc_v2 >= thr else "✗")
            summary.setdefault(code, {}).update({
                "accuracy_llmjudge_pct": round(acc_v2, 2) if acc_v2 is not None else None,
                "n_llmjudge": n_graded, "pass_llmjudge": npass, "n_judge_errors": n_err,
                "grader_llmjudge": "llm-judge-rubric (independent)",
                "judge_model_llmjudge": config.RAGAS_JUDGE_MODEL,
            })
            acc_str = f"{acc_v2:.1f}%" if acc_v2 is not None else "n/a"
            print(f"  {cap:12} {npass}/{n_graded} = {acc_str}  (ngưỡng ≥{thr}%) {ok_v2}  "
                  f"[judge_errors={n_err}] → {fname}")

    (out_dir / "task-summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"\nsummary → {out_dir/'task-summary.json'}")


if __name__ == "__main__":
    main()
