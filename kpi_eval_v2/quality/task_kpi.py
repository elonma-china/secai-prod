"""R2-SAST85, R2-EXPLOIT85, R2-REMED60 — run the task through the real gateway, then grade
with a DIRECT rubric prompt to the INDEPENDENT judge (config.JUDGE_MODEL, OpenRouter
deepseek) — the task brief's explicitly-allowed alternative to ragas RubricsScore/AspectCritic.
pass@k with real code execution is OUT OF SCOPE (noted, not measured — see notes on each KPI).
"""
from __future__ import annotations

import json
import os
import re

from .. import config, schema
from . import common
from .answers import collect_answers

_JSON_RE = re.compile(r'"pass"\s*:\s*(true|false)', re.I)
_SCORE_RE = re.compile(r'"score"\s*:\s*([0-9]*\.?[0-9]+)')


def judge_rubric(rubric_prompt: str) -> dict:
    """One direct rubric call to the independent judge. Fail-closed: any error/unparseable
    response -> pass=None (excluded from the pass-rate denominator, never counted a pass)."""
    body = {
        "model": config.JUDGE_MODEL,
        "messages": [{"role": "user", "content": rubric_prompt}],
        "temperature": 0, "max_tokens": 1200,
        "response_format": {"type": "json_object"},
    }
    import urllib.request
    req = urllib.request.Request(
        config.JUDGE_BASE_URL.rstrip("/") + "/chat/completions",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + config.JUDGE_KEY},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            txt = json.loads(r.read())["choices"][0]["message"].get("content") or ""
    except Exception as e:  # noqa: BLE001
        return {"pass": None, "score": None, "rationale": f"judge_http_error:{repr(e)[:120]}"}
    try:
        o = json.loads(txt)
        p, sc, rat = o.get("pass"), o.get("score"), o.get("rationale", "")
        if isinstance(p, bool):
            return {"pass": p, "score": sc, "rationale": rat}
    except Exception:
        pass
    m = _JSON_RE.search(txt)
    ms = _SCORE_RE.search(txt)
    if m:
        return {"pass": m.group(1).lower() == "true", "score": float(ms.group(1)) if ms else None,
                "rationale": txt[:300]}
    return {"pass": None, "score": None, "rationale": f"unparseable_judge_output:{txt[:200]}"}


def _run_task(kpi_id: str, tasks: list[dict], build_message, build_rubric, log_name: str) -> dict:
    """tasks: list of {id, ...}. build_message(t)->str sent to gateway. build_rubric(t, answer)
    ->str rubric prompt sent to the judge. Returns schema.make_result() dict."""
    n_req = int(os.environ.get("KPI2_N", "20"))
    tasks = tasks[:n_req]
    items = [{"id": t["id"], "message": build_message(t), **t} for t in tasks]
    answered = collect_answers(items, message_key="message", cache_name=log_name)

    n_422 = sum(1 for a in answered if a["error"] == "HTTP422:blocked_by_require_public_input")
    n_other_err = sum(1 for a in answered if a["error"] and a["error"] != "HTTP422:blocked_by_require_public_input")
    usable = [a for a in answered if not a["error"] and (a["answer"] or "").strip()]

    log_path = config.RESULTS / f"quality_{log_name}_items.jsonl"
    n_pass = 0
    n_judged = 0
    n_judge_fail = 0
    with log_path.open("w", encoding="utf-8") as f:
        for a in usable:
            verdict = judge_rubric(build_rubric(a, a["answer"]))
            rec = {"id": a["id"], "answer": (a["answer"] or "")[:600], **verdict}
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            if verdict["pass"] is None:
                n_judge_fail += 1
                continue
            n_judged += 1
            n_pass += 1 if verdict["pass"] else 0

    pct = round(100 * n_pass / n_judged, 2) if n_judged else None
    basis = (f"gateway :8131 real answers, n_requested={len(tasks)}, n_HTTP422_blocked={n_422}, "
             f"n_other_gateway_errors={n_other_err}, n_usable={len(usable)}, "
             f"n_judge_parse_fail_excluded={n_judge_fail}, n_judged={n_judged}; "
             f"judge={config.JUDGE_MODEL} (independent)")
    return schema.make_result(
        kpi_id, pct,
        method="task run through real gateway :8131 chat, graded PASS/FAIL by a direct rubric "
               "prompt to the independent judge LLM (fail-closed: unparseable judge output excluded)",
        basis=basis, n=n_judged, evidence=str(log_path),
        notes="pass@k with real code execution is out of scope for this suite (future work).",
        extra={"n_422_blocked": n_422, "n_other_errors": n_other_err, "n_judge_parse_fail": n_judge_fail},
    )


# ── SAST ─────────────────────────────────────────────────────────────────────────────────
def _sast_message(t: dict) -> str:
    return ("Hãy phân tích lỗ hổng bảo mật (SAST) trong đoạn mã sau: xác định loại lỗ hổng "
            "(kèm mã CWE nếu biết) và giải thích ngắn gọn vì sao đoạn mã này có lỗ hổng.\n\n"
            f"```\n{t['code']}\n```")


def _sast_rubric(t: dict, answer: str) -> str:
    return (
        "Bạn là giám khảo chấm bài phân tích SAST (static application security testing).\n"
        f"MÃ NGUỒN CÓ LỖ HỔNG THẬT THUỘC: {t['gold_cwe']}.\n"
        f"CÂU TRẢ LỜI CẦN CHẤM:\n{answer[:2500]}\n\n"
        f"Câu trả lời PASS nếu nó xác định đúng lớp lỗ hổng khớp {t['gold_cwe']} "
        "(chấp nhận nêu đúng mã CWE, HOẶC mô tả rõ ràng đúng bản chất lỗ hổng đó dù không nêu "
        "mã số, vd command injection cho CWE-78, hardcoded credentials cho CWE-798) VÀ có giải "
        "thích tối thiểu vì sao đoạn mã có lỗ hổng (không chỉ nêu tên).\n"
        'Chỉ trả JSON: {"pass": true/false, "score": <0..1>, "rationale": "<ngắn gọn>"}'
    )


def run_sast() -> dict:
    tasks = common.load_sast_samples()
    return _run_task("R2-SAST85", tasks, _sast_message, _sast_rubric, "sast")


# ── Exploit explanation (pentest/tinh_huong bucket: verify/exploit a real CVE in scope) ────
def _exploit_message(t: dict) -> str:
    return t["input"]


def _exploit_rubric(t: dict, answer: str) -> str:
    exp = t.get("expected") or {}
    must = exp.get("must_contain") or []
    must_any = exp.get("must_contain_any") or []
    return (
        "Bạn là giám khảo chấm bài GIẢI THÍCH KHAI THÁC/KIỂM CHỨNG lỗ hổng (exploit "
        "explanation) trong bối cảnh pentest hợp lệ.\n"
        f"CÂU HỎI: {t['input']}\n"
        f"CÂU TRẢ LỜI CẦN CHẤM:\n{answer[:2500]}\n\n"
        f"Tiêu chí: PHẢI nêu đủ các nội dung sau: {must}. "
        f"VÀ nêu ít nhất một trong: {must_any if must_any else '(không bắt buộc)'}. "
        "Câu trả lời phải giải thích được CƠ CHẾ/LOGIC khai thác hoặc quy trình kiểm chứng an "
        "toàn một cách cụ thể, đúng kỹ thuật, không chung chung.\n"
        'Chỉ trả JSON: {"pass": true/false, "score": <0..1>, "rationale": "<ngắn gọn>"}'
    )


def run_exploit() -> dict:
    tasks = common.load_gold_jsonl("acceptance_indep_tinh_huong")
    return _run_task("R2-EXPLOIT85", tasks, _exploit_message, _exploit_rubric, "exploit")


# ── Remediation ──────────────────────────────────────────────────────────────────────────
def _remed_message(t: dict) -> str:
    return t["input"]


def _remed_rubric(t: dict, answer: str) -> str:
    exp = t.get("expected") or {}
    must = exp.get("must_contain") or []
    must_any = exp.get("must_contain_any") or []
    return (
        "Bạn là giám khảo chấm bài ĐỀ XUẤT KHẮC PHỤC (auto-remediation) cho một CVE.\n"
        f"CÂU HỎI: {t['input']}\n"
        f"CÂU TRẢ LỜI CẦN CHẤM:\n{answer[:2500]}\n\n"
        f"Tiêu chí: PHẢI nêu: {must}. VÀ nêu ít nhất một biện pháp khắc phục hợp lệ trong: "
        f"{must_any if must_any else '(không bắt buộc)'} (vd bản vá/nâng cấp phiên bản/"
        "workaround/giảm thiểu cụ thể — KHÔNG chấp nhận câu trả lời chung chung kiểu "
        "'hãy cập nhật phần mềm' mà không gắn với CVE/sản phẩm cụ thể).\n"
        'Chỉ trả JSON: {"pass": true/false, "score": <0..1>, "rationale": "<ngắn gọn>"}'
    )


def run_remediation() -> dict:
    tasks = common.load_gold_jsonl("acceptance_indep_khac_phuc")
    return _run_task("R2-REMED60", tasks, _remed_message, _remed_rubric, "remed")
