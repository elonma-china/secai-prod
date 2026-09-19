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
from pathlib import Path

KPI_DIR = Path(__file__).resolve().parents[1]
VI = set("àáảãạăằắẳẵặâầấẩẫậèéẻẽẹêềếểễệìíỉĩịòóỏõọôồốổỗộơờớởỡợùúủũụưừứửữựỳýỷỹỵđ")
CWE_RE = re.compile(r"CWE-\d+", re.I)

# Bộ check phụ theo task (port từ eval/grader/graders/*.ts)
EXPLOIT_KW = ["bộ nhớ", "memory", "stack", "heap", "overflow", "payload", "lợi dụng", "exploit",
              "khai thác", "cơ chế", "mechanism", "bypass", "entity", "parser", "JNDI", "lookup",
              "inject", "execute", "trigger", "gây ra", "cho phép", "tấn công", "attacker", "kẻ tấn công"]
REMED_SECTIONS = ["Đánh giá", "Before", "After", "Diff", "Giải thích"]


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


TASKS = {
    "sast": ("sast.jsonl", grade_sast, "R-LLM-SAST85", 85),
    "exploit": ("exploit-explanation.jsonl", grade_exploit, "R-LLM-EXPLOIT85", 85),
    "remediation": ("auto-remediation.jsonl", grade_remediation, "R-LLM-REMED60", 60),
}


def grade_sast_majority(base_items, extra_run_files, out_dir):
    """SAST theo đa số 2/3 lượt (giống aggregate_results.py của bộ nghiệm thu).
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
        summary[code] = {"capability": cap, "n": ntot, "pass": npass,
                         "accuracy_pct": round(acc, 2), "threshold": thr, "passed": acc >= thr}
        print(f"  {cap:12} {npass}/{ntot} = {acc:.1f}%  (ngưỡng ≥{thr}%) {ok}{note}  → {fname}")

    (out_dir / "task-summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"\nsummary → {out_dir/'task-summary.json'}")


if __name__ == "__main__":
    main()
