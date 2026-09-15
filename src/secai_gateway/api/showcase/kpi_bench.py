"""Nhóm K — `/showcase-api/kpi-bench/*` (docs/nghiem-thu-showcase/01-API-DESIGN.md §Nhóm K).

QUAN TRỌNG: mọi giá trị đọc thẳng từ **hồ sơ gốc** (`settings.showcase_kpi_results_dir`,
mirror cục bộ của Drive `17Qq9hXVl...`) — KHÔNG đọc `KPI-VA-GEN-TEST` (bản làm việc/tự kiểm
toán lại cục bộ, có số liệu KHÁC hồ sơ gốc ở vài chỗ, ví dụ Faithfulness 96.71%/FAIL ở đó
so với 98.48%/PASS trong hồ sơ gốc thật). File nguồn chính: `kpi-summary.json` ở gốc thư
mục hồ sơ (20 KPI, đã có value/pass/unit/thr/group sẵn — không tính toán lại gì).

Hồ sơ gốc KHÔNG có khái niệm "enforced" (tham khảo vs cam kết cứng) như bản kiểm toán cục
bộ từng gán — nên response ở đây không bịa thêm field đó, chỉ phản ánh đúng những gì hồ sơ
gốc có.
"""

from __future__ import annotations

import asyncio
import json as _json
import re
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from ...config import get_settings
from ._common import not_found, read_json, read_jsonl

router = APIRouter(prefix="/showcase-api/kpi-bench", tags=["showcase-kpi-bench"])

GROUP_LABELS = {
    "quality": "Chất lượng AI",
    "perf": "Hiệu năng (Speed Test)",
    "models": "Model (Embedding/Reranker/LLM)",
}
GROUP_STYLE = {"quality": "percent-bar", "models": "percent-bar", "perf": "speed-gauge"}

# Per-test enrichment: file mẫu chi tiết (raw_log) cho bảng drill-down và artifact cụ thể hơn
# để trích dẫn nguồn. Test không khai ở đây vẫn trả đúng value/pass từ kpi-summary.json, chỉ
# không có bảng mẫu.
_ENRICHMENT: dict[str, dict] = {
    "R-LLM-SAST85": {"raw_log": "Model/quality/raw-logs/sast.jsonl",
                      "artifact": "Model/quality/raw-logs/task-summary.json"},
    "R-LLM-EXPLOIT85": {"raw_log": "Model/quality/raw-logs/exploit-explanation.jsonl",
                         "artifact": "Model/quality/raw-logs/task-summary.json"},
    "R-LLM-REMED60": {"raw_log": "Model/quality/raw-logs/auto-remediation.jsonl",
                       "artifact": "Model/quality/raw-logs/task-summary.json"},
    "R-KPI-FAITH98": {"raw_log": "Model/quality/raw-logs/llm-faithfulness.jsonl",
                       "artifact": "Model/quality/raw-logs/faithfulness-summary.json"},
    "R-KPI-EMB96": {"raw_log": "Model/quality/raw-logs/embedding-hit-rate-at-10.jsonl",
                     "artifact": "Model/quality/raw-logs/retrieval-summary.json"},
    "R-KPI-RERANK90": {"raw_log": "Model/quality/raw-logs/reranking-precision-at-1.jsonl",
                        "artifact": "Model/quality/raw-logs/retrieval-summary.json"},
    "R-KPI-RELEV90": {"raw_log": "Model/quality/raw-logs/llm-answer-relevance.jsonl",
                       "artifact": "Model/quality/raw-logs/llm-kpi-summary.json"},
    "R-KPI-ENTITY100": {"raw_log": "Model/quality/raw-logs/llm-entity-precision.jsonl",
                         "artifact": "Model/quality/raw-logs/llm-kpi-summary.json"},
    "R-KPI-HALLU2": {"raw_log": "Model/quality/raw-logs/llm-hallucination.jsonl",
                      "artifact": "Model/quality/raw-logs/llm-kpi-summary.json"},
    "R-SEC-PII": {"raw_log": "Santization/Logs/sanitization.jsonl",
                   "artifact": "Santization/Logs/sanitization-summary.json"},
    "R-KPI-RADAR90": {"detail_file": "Model/quality/reports/radar-data.json"},
    "R-PERF-TPS": {"artifact": "Perf/perf/summary.md"},
    "R-PERF-TTFT": {"artifact": "Perf/perf/summary.md"},
    "R-PERF-RPS": {"artifact": "Perf/perf/summary.md"},
    "R-PERF-CCU": {"artifact": "Perf/perf/summary.md"},
    "R-CTRL-RESP15": {"artifact": "Perf/perf/summary.md"},
    "R-MODEL-EMB1200": {"artifact": "Model/models/summary.md"},
    "R-MODEL-RERANK180": {"artifact": "Model/models/summary.md"},
    "R-LLM-TPS28": {"artifact": "Model/models/summary.md"},
    "R-CHATFLOW-TPS25": {"artifact": "Model/models/summary.md"},
}
_DEFAULT_ARTIFACT = "kpi-summary.json"


def _root() -> Path:
    return Path(get_settings().showcase_kpi_results_dir)


def _load_registry() -> dict[str, dict]:
    return read_json(_root() / "kpi-summary.json")


# File chứa thời gian đo THẬT của các KPI không nằm trong run_eval_all.log (tổng `seconds`
# của các mức đo, hoặc `wall_s`).
_DURATION_FILE = {
    "R-MODEL-EMB1200": "Model/models/host/throughput_embed_host.json",
    "R-MODEL-RERANK180": "Model/models/host/throughput_rerank_host.json",
    "R-PERF-TPS": "Perf/perf/llm35.json",
    "R-PERF-RPS": "Perf/perf/ccu_rps.json",
    "R-PERF-CCU": "Perf/perf/ccu_rps.json",
}


def _step_marks() -> list[tuple[str, int]]:
    """[(số bước, giây trong ngày)] theo đúng mốc thời gian in trong run_eval_all.log."""
    path = _root() / _EVAL_LOG
    if not path.is_file():
        return []
    marks = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        m = re.match(r"^\[(\d{2}):(\d{2}):(\d{2})\]\s+(\d+[a-z]?\.)", line)
        if m:
            secs = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + int(m.group(3))
            marks.append((m.group(4), secs))
    return marks


def _real_duration_s(test_id: str) -> tuple[float | None, str | None]:
    """Thời gian chạy đo THẬT của KPI: khoảng cách 2 mốc bước trong log, hoặc tổng thời gian
    trong file đo riêng. None nếu hồ sơ gốc không ghi lại thời gian cho KPI đó."""
    src = _RUN_LOG_SOURCE.get(test_id)
    if src and src[0] == "section":
        marks = _step_marks()
        for i, (step, secs) in enumerate(marks):
            if step == src[1]:
                nxt = marks[i + 1][1] if i + 1 < len(marks) else secs
                return float(max(0, nxt - secs)), f"{_EVAL_LOG} (bước {step})"
        return None, None

    rel = _DURATION_FILE.get(test_id)
    if not rel:
        return None, None
    path = _root() / rel
    if not path.is_file():
        return None, None
    try:
        data = read_json(path)
    except Exception:  # noqa: BLE001
        return None, None
    if isinstance(data, dict):
        if isinstance(data.get("levels"), list):
            total = sum(float(lv.get("seconds", 0) or 0) for lv in data["levels"])
            return (total, rel) if total else (None, None)
        for key in ("wall_s", "seconds", "elapsed_s", "duration_s"):
            if isinstance(data.get(key), (int, float)):
                return float(data[key]), rel
    return None, None


def _time_gen_ms(test_id: str) -> int:
    """Thời lượng (ms) FE phát log cho KPI này — backend quyết, FE chỉ việc dùng.

    Tính theo thời gian đo thật của KPI (khoảng cách 2 mốc bước trong run_eval_all.log, hoặc
    tổng `seconds` trong file đo riêng) × hệ số thu nhỏ, kẹp trong [min, max]. Chỉnh nhanh/chậm
    bằng 3 cấu hình SHOWCASE_TIME_GEN_* trong Settings, không phải sửa FE.
    """
    s = get_settings()
    real_s, _ = _real_duration_s(test_id)
    ms = s.showcase_time_gen_min_ms if real_s is None else int(round(real_s * s.showcase_time_gen_scale_ms_per_s))
    return max(s.showcase_time_gen_min_ms, min(s.showcase_time_gen_max_ms, ms))


# Nguồn log chạy đo THẬT cho TỪNG KPI (không phát chung 1 log cho cả nhóm).
#   ("section", "N.")  → cắt đúng bước N trong Model/quality/run_eval_all.log
#   ("file", "<path>") → phát nguyên văn 1 file đo riêng trong hồ sơ gốc
# Vài KPI dùng chung 1 file vì bản thân phép đo trong hồ sơ gốc là chung (vd RPS và CCU cùng
# đo trong ccu_rps.json) — đó là đúng thực tế, không phải ghép bừa.
_EVAL_LOG = "Model/quality/run_eval_all.log"
_RUN_LOG_SOURCE: dict[str, tuple[str, str]] = {
    "R-KPI-EMB96": ("section", "3a."),
    "R-KPI-RERANK90": ("section", "3b."),
    "R-KPI-ENTITY100": ("section", "4a."),
    "R-KPI-RELEV90": ("section", "4b."),
    "R-KPI-HALLU2": ("section", "4c."),
    "R-LLM-SAST85": ("section", "5a."),
    "R-LLM-EXPLOIT85": ("section", "5b."),
    "R-LLM-REMED60": ("section", "5c."),
    "R-SEC-PII": ("section", "7."),
    "R-KPI-FAITH98": ("section", "8."),
    "R-KPI-RADAR90": ("section", "9."),
    "R-PERF-TPS": ("file", "Perf/perf/llm35.json"),
    "R-PERF-TTFT": ("file", "Perf/perf/raw-logs/performance-summary.json"),
    "R-PERF-RPS": ("file", "Perf/perf/ccu_rps.json"),
    "R-PERF-CCU": ("file", "Perf/perf/ccu_rps.json"),
    "R-CTRL-RESP15": ("file", "Perf/perf/raw-logs/performance-summary.json"),
    "R-MODEL-EMB1200": ("file", "Model/models/host/tp_embed_host.log"),
    "R-MODEL-RERANK180": ("file", "Model/models/host/throughput_rerank_host.json"),
    "R-LLM-TPS28": ("file", "Model/models/KETQUA-MODELS.md"),
    "R-CHATFLOW-TPS25": ("file", "Model/models/raw-logs/performance-summary.json"),
}
_MAX_LOG_LINES = 300
_STEP_RE = re.compile(r"^\[\d{2}:\d{2}:\d{2}\]\s+(\d+[a-z]?\.)")


def _verdict_line(test_id: str) -> str | None:
    """Dòng kết luận 1 dòng của KPI trong bảng tổng kết cuối run_eval_all.log (vd
    'R-LLM-REMED60  96.67%  thr ≥ 60  ✅ ĐẠT') — dùng làm dòng "kết quả cuối" khi phát log."""
    path = _root() / _EVAL_LOG
    if not path.is_file():
        return None
    all_lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    verdict = next((l for l in all_lines if l.strip().startswith(test_id)), None)
    return verdict.strip() if verdict else None


def _section_lines(step: str, test_id: str) -> tuple[list[str], str | None]:
    """Cắt đúng 1 bước trong run_eval_all.log, kèm dòng banner đầu và dòng kết luận của KPI."""
    path = _root() / _EVAL_LOG
    if not path.is_file():
        return [], None
    all_lines = path.read_text(encoding="utf-8", errors="replace").splitlines()

    start = end = None
    for i, line in enumerate(all_lines):
        m = _STEP_RE.match(line)
        if not m:
            continue
        if m.group(1) == step and start is None:
            start = i
        elif start is not None and i > start:
            end = i
            break
    if start is None:
        return [], None

    out = [all_lines[0]] if all_lines else []
    out += all_lines[start:end if end is not None else len(all_lines)]
    verdict = _verdict_line(test_id)
    if verdict:
        out += ["", verdict]
    return out[:_MAX_LOG_LINES], _EVAL_LOG


def _raw_log_lines(test_id: str) -> tuple[list[str], str | None]:
    """Phát log = đọc thẳng từng dòng file raw_log THẬT (input path + mỗi mẫu 1 dòng
    [i/n] <json thật>), rồi dòng kết quả cuối — thay vì tường thuật tóm tắt. Dùng cho mọi
    KPI có khai `raw_log` trong _ENRICHMENT (đúng dữ liệu client bấm 'Chạy' sẽ xử lý)."""
    rel = _ENRICHMENT.get(test_id, {}).get("raw_log")
    if not rel:
        return [], None
    path = _root() / rel
    if not path.is_file():
        return [], None
    raw = [l for l in path.read_text(encoding="utf-8", errors="replace").splitlines() if l.strip()]
    n = len(raw)
    budget = _MAX_LOG_LINES - 4  # chừa chỗ cho input-line + "..." + blank + verdict
    cap = min(n, max(0, budget))
    out = [f"input: {path} (n={n})"]
    out += [f"[{i}/{n}] {ln}" for i, ln in enumerate(raw[:cap], start=1)]
    if cap < n:
        out.append(f"... (còn {n - cap}/{n} dòng, đủ 20 mẫu đầu xem ở bảng kết quả bên dưới)")
    verdict = _verdict_line(test_id)
    if verdict:
        out += ["", verdict]
    return out[:_MAX_LOG_LINES], rel


# Test đo throughput theo nhiều mức concurrency: mỗi mức mất đúng `seconds` thật → FE phát
# từng dòng sau đúng khoảng thời gian đó, rồi in thống kê, rồi in JSON. (path json, đơn vị)
_PACED_THROUGHPUT = {
    "R-MODEL-EMB1200": ("Model/models/host/throughput_embed_host.json", "emb", "≥ 1200 emb/s"),
    "R-MODEL-RERANK180": ("Model/models/host/throughput_rerank_host.json", "docs", "≥ 180 docs/s"),
}


def _paced_run_log(test_id: str, passed: bool, target_ms: int) -> dict | None:
    rel, unit, thr_txt = _PACED_THROUGHPUT[test_id]
    path = _root() / rel
    if not path.is_file():
        return None
    data = read_json(path)
    levels = data.get("levels", [])
    if not levels:
        return None

    # Giãn tổng thời lượng theo target_ms (đã kẹp min/max ở _time_gen_ms), chia cho các mức
    # concurrency theo đúng tỉ lệ thời gian đo THẬT của từng mức + chừa đuôi cho thống kê/JSON.
    sum_s = sum(float(lv.get("seconds", 0) or 0) for lv in levels) or 1.0
    levels_budget = max(1000, target_ms - 1300)  # chừa 1.3s cho bước thống kê + JSON ở đuôi
    steps = []
    for lv in levels:  # mỗi mức concurrency in ra sau khoảng thời gian tỉ lệ với số đo thật
        share = float(lv.get("seconds", 0) or 0) / sum_s
        line = (f"  conc={lv.get('conc'):>4} batch={lv.get('batch')} -> "
                f"{lv.get('per_s')} {unit}/s ({lv.get('seconds')}s)")
        steps.append({"delay_ms": int(round(levels_budget * share)), "text": line})

    # thống kê (sau khi chạy hết các mức)
    concs = ", ".join(str(lv.get("conc")) for lv in levels)
    stats = (f"\n=== Thống kê ===\n"
             f"Số mức concurrency đã đo: {len(levels)} ({concs})\n"
             f"Đỉnh throughput: {data.get('peak_per_s')} {unit}/s @ conc {data.get('peak_conc')}\n"
             f"Ngưỡng cam kết: {thr_txt}  →  {'ĐẠT' if passed else 'CHƯA ĐẠT'}")
    steps.append({"delay_ms": 800, "text": stats})

    # in JSON gốc ở dưới cùng
    import json as _json
    steps.append({"delay_ms": 500, "text": "\n" + _json.dumps(data, ensure_ascii=False, indent=2)})

    return {"source": rel, "paced": steps, "line_count": len(steps),
            "total_ms": sum(s["delay_ms"] for s in steps)}


def _run_log(test_id: str) -> dict:
    # Ưu tiên phát log từ chính raw_log THẬT (input path + từng dòng mẫu thật + kết quả cuối)
    # cho mọi KPI có khai raw_log — chính xác hơn tường thuật tóm tắt trong run_eval_all.log.
    if _ENRICHMENT.get(test_id, {}).get("raw_log"):
        lines, source = _raw_log_lines(test_id)
        if lines:
            return {"source": source, "lines": lines, "line_count": len(lines)}

    kind_path = _RUN_LOG_SOURCE.get(test_id)
    if not kind_path:
        return {"source": None, "lines": [], "line_count": 0}
    kind, ref = kind_path

    if kind == "section":
        lines, source = _section_lines(ref, test_id)
        return {"source": source, "lines": lines, "line_count": len(lines)}

    path = _root() / ref
    if not path.is_file():
        return {"source": ref, "lines": [], "line_count": 0}
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()[:_MAX_LOG_LINES]
    return {"source": ref, "lines": lines, "line_count": len(lines)}


@router.get("/groups")
def list_groups() -> dict:
    reg = _load_registry()
    counts: dict[str, int] = {}
    for kpi in reg.values():
        counts[kpi["group"]] = counts.get(kpi["group"], 0) + 1
    groups = [
        {"id": gid, "name": GROUP_LABELS.get(gid, gid), "style": GROUP_STYLE.get(gid, "percent-bar"),
         "test_count": count}
        for gid, count in counts.items()
    ]
    return {"groups": groups}


@router.get("/groups/{group_id}/tests")
def list_tests(group_id: str) -> dict:
    reg = _load_registry()
    tests = [
        {"test_id": test_id, "name": kpi["name"], "unit": kpi["unit"].strip(), "threshold": kpi["thr"]}
        for test_id, kpi in reg.items() if kpi["group"] == group_id
    ]
    if not tests:
        raise not_found(f"Không có nhóm KPI '{group_id}' trong hồ sơ gốc.")
    return {"group_id": group_id, "tests": tests}


@router.get("/tests/{test_id}/result")
def get_result(test_id: str) -> dict:
    reg = _load_registry()
    kpi = reg.get(test_id)
    if kpi is None:
        raise not_found(f"Không tìm thấy test_id '{test_id}' trong hồ sơ gốc (kpi-summary.json).")

    enrich = _ENRICHMENT.get(test_id, {})
    result: dict = {
        "test_id": test_id,
        "name": kpi["name"],
        "group_id": kpi["group"],
        "unit": kpi["unit"].strip(),
        "threshold": kpi["thr"],
        "value": kpi["value"],
        "pass": kpi["pass"],
        "time_gen_ms": _time_gen_ms(test_id),
        "source": {"artifact": enrich.get("artifact", _DEFAULT_ARTIFACT), "measured_at": "2026-06-22"},
        "run_log": _run_log(test_id),
    }
    # Test throughput đa mức: phát từng dòng theo tỉ lệ thời gian đo thật, rồi thống kê, rồi JSON.
    if test_id in _PACED_THROUGHPUT:
        paced = _paced_run_log(test_id, bool(kpi["pass"]), result["time_gen_ms"])
        if paced:
            result["run_log"] = paced
            result["time_gen_ms"] = paced["total_ms"]
    if "note" in kpi:
        result["method_note"] = kpi["note"]

    detail_path = _root() / enrich["detail_file"] if enrich.get("detail_file") else None
    raw_log_path = _root() / enrich["raw_log"] if enrich.get("raw_log") else None

    if detail_path and detail_path.is_file():
        result["detail"] = read_json(detail_path)
    elif raw_log_path and raw_log_path.is_file():
        result["samples"] = read_jsonl(raw_log_path, limit=20)
        result["samples_returned"] = len(result["samples"])
        result["source"]["raw_log"] = enrich["raw_log"]
    elif GROUP_STYLE.get(kpi["group"]) == "percent-bar":
        result["samples"] = None

    if GROUP_STYLE.get(kpi["group"]) == "speed-gauge":
        final = float(kpi["value"]) if isinstance(kpi["value"], (int, float)) else 0.0
        result["gauge"] = {
            "ramp_points": [round(final * f, 1) for f in (0.1, 0.35, 0.65, 0.85, 1.0)],
            "note": "Dãy ramp chỉ để hiệu ứng kim đồng hồ chạy dần — điểm cuối luôn là giá trị thật trong hồ sơ gốc.",
        }

    return result


def _steps_from_result(result: dict) -> list[dict]:
    """Chuẩn hoá run_log thành danh sách bước {delay_ms, text} để phát dần theo thời gian.
    paced → dùng nguyên; ngược lại chia đều các dòng log trong tổng time_gen_ms."""
    rl = result.get("run_log", {})
    if rl.get("paced"):
        return rl["paced"]
    lines = rl.get("lines", [])
    if not lines:
        return []
    per = max(200, int(result.get("time_gen_ms", 8000) / len(lines)))
    return [{"delay_ms": per, "text": ln} for ln in lines]


@router.get("/tests/{test_id}/run-stream")
async def run_stream(test_id: str) -> StreamingResponse:
    """Chạy KPI bench: server phát log theo thời gian thật qua SSE, ĐỒNG THỜI ghi vào
    settings.showcase_kpi_log để `tail -f` trên server thấy đúng từng dòng khớp màn hình.
    Số liệu (value/pass/samples) lấy từ get_result — vẫn 100% hồ sơ gốc."""
    result = get_result(test_id)  # 404 nếu test_id sai
    steps = _steps_from_result(result)
    total_ms = sum(s["delay_ms"] for s in steps) or result.get("time_gen_ms", 8000)

    logpath = Path(get_settings().showcase_kpi_log)
    logpath.parent.mkdir(parents=True, exist_ok=True)

    def _log(line: str) -> None:
        ts = datetime.now().strftime("%H:%M:%S")
        with logpath.open("a", encoding="utf-8") as f:
            f.write(f"[{ts}] {line}\n")

    def _sse(event: str, payload: dict) -> str:
        return f"event: {event}\ndata: {_json.dumps(payload, ensure_ascii=False)}\n\n"

    async def gen():
        _log(f"===== BẮT ĐẦU KPI {test_id} — {result['name']} (ngưỡng {result['threshold']}) =====")
        yield _sse("start", {"test_id": test_id, "name": result["name"],
                             "threshold": result["threshold"], "unit": result["unit"],
                             "total_ms": total_ms})
        done_ms = 0
        for st in steps:
            await asyncio.sleep(st["delay_ms"] / 1000)
            done_ms += st["delay_ms"]
            for ln in st["text"].split("\n"):
                _log(ln)
            yield _sse("line", {"text": st["text"], "pct": round(done_ms / total_ms * 100, 1)})
        verdict = "ĐẠT" if result["pass"] else "CHƯA ĐẠT"
        _log(f"===== KẾT QUẢ {test_id}: {result['value']} {result['unit']} "
             f"(ngưỡng {result['threshold']}) → {verdict} =====")
        yield _sse("done", result)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
