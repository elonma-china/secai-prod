#!/usr/bin/env python3
"""clearml_report.py — ADDITIVE. Đẩy kết quả 1 run KPI (đã có sẵn) lên ClearML để so sánh xu
hướng qua nhiều lần chạy. KHÔNG sửa build_reports.py / bench.sh, chỉ ĐỌC output của chúng.

Đọc:
  <run_dir>/**/raw-logs/kpi-summary.json          (11 KPI nhóm A, do build_reports.py sinh)
  <run_dir>/**/raw-logs/kpi-summary.json          (nhóm B/C nếu có, do build_perf_summary.py sinh)
  <run_dir>/**/raw-logs/llm-kpi-summary.json      (R-KPI-REASONING, phụ — không nằm trong 19 KPI)
  <run_dir>/**/raw-logs/performance-summary.json  (fallback nhóm B khi thiếu kpi-summary.json riêng)
  <run_dir>/**/raw-logs/throughput_embed.json     (fallback R-MODEL-EMB1200)
  <run_dir>/**/raw-logs/throughput_rerank.json    (fallback R-MODEL-RERANK180)
  <run_dir>/**/raw-logs/llm-layer.json            (fallback R-LLM-TPS28)
  <run_dir>/**/raw-logs/sast.jsonl                (field "method" — majority-2/3 vs single-pass)
  01-KPI/kpi-registry.json                        (nguồn sự thật: mã/tên/ngưỡng/op/ghi_chú)

Ghi lên ClearML (project "secai-kpi"):
  - report_scalar mỗi KPI trong 19 KPI (title=nhóm_tên, series=tên, iteration=0)
  - report_table tổng hợp 19 dòng
  - set_comment gồm cảnh báo phương pháp từ registry (_meta + ghi_chu từng KPI), đặc biệt
    2 cạm bẫy: R-LLM-SAST85 (majority vs single-pass) và R-MODEL-EMB1200/RERANK180 (host vs WAN)
  - tags: sast-method:<...>, emb-rerank-source:<...>

Không có raw-log nào cho 1 KPI → KPI đó bị bỏ qua (không crash), đánh dấu N/A trong bảng.

Usage:
  python3 clearml_report.py <run_dir> [--task-name NAME] [--project NAME] [--registry PATH]
                             [--dry-run] [--out-json PATH]

  <run_dir> có thể là:
    KPI-VA-GEN-TEST/runs/latest                       (1 folder phẳng raw-logs/+reports/)
    KPI-VA-GEN-TEST/runs/<timestamp>
    KPI-VA-GEN-TEST/KET-QUA/2026-08-26                 (nhiều group-*/raw-logs/ bên trong)
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

KPI_DIR = Path(__file__).resolve().parents[1]  # .../KPI-VA-GEN-TEST
DEFAULT_REGISTRY = KPI_DIR / "01-KPI" / "kpi-registry.json"
DEFAULT_CLEARML_CONF = Path.home() / ".config" / "secai-mlops" / "clearml.conf"


def warn(msg: str) -> None:
    print(f"[clearml_report][WARN] {msg}", file=sys.stderr)


def info(msg: str) -> None:
    print(f"[clearml_report] {msg}")


# ───────────────────────── load registry ─────────────────────────

def load_registry(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    if "kpi" not in data or not isinstance(data["kpi"], list):
        raise ValueError(f"registry {path} thiếu key 'kpi' hợp lệ")
    return data


# ───────────────────────── generic file discovery ─────────────────────────

def find_files(run_dir: Path, filename: str) -> list[Path]:
    try:
        return sorted(run_dir.rglob(filename))
    except Exception as e:  # pragma: no cover - defensive
        warn(f"rglob({filename}) lỗi: {e}")
        return []


def load_json_safe(p: Path):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        warn(f"không đọc được {p}: {e}")
        return None


def rel(run_dir: Path, p: Path) -> str:
    try:
        return str(p.relative_to(run_dir))
    except Exception:
        return str(p)


# ───────────────────────── primary source: kpi-summary.json (mọi group) ─────────────────────────

def collect_kpi_summary(run_dir: Path) -> dict:
    """Merge mọi kpi-summary.json tìm thấy (1 cái phẳng, hoặc nhiều cái group-A/B/C/...).
    First-wins theo mã KPI; không có mã trùng giữa các group trong thực tế."""
    merged: dict[str, dict] = {}
    for f in find_files(run_dir, "kpi-summary.json"):
        obj = load_json_safe(f)
        if not isinstance(obj, dict):
            continue
        for code, v in obj.items():
            if not isinstance(v, dict):
                continue
            if code in merged:
                continue
            entry = dict(v)
            entry["_source"] = rel(run_dir, f)
            entry["_fallback"] = False
            merged[code] = entry
    return merged


def collect_reasoning(run_dir: Path):
    """R-KPI-REASONING — KPI thứ 12, KHÔNG có ngưỡng riêng, không nằm trong 19 KPI chính thức.
    Chỉ dùng để làm giàu comment/log phụ."""
    for f in find_files(run_dir, "llm-kpi-summary.json"):
        obj = load_json_safe(f)
        if not isinstance(obj, dict):
            continue
        r = obj.get("R-KPI-REASONING")
        if isinstance(r, dict) and r.get("reasoning_present_pct") is not None:
            return {
                "value": r.get("reasoning_present_pct"),
                "n": r.get("n"),
                "_source": rel(run_dir, f),
            }
    return None


# ───────────────────────── fallback: nhóm B (perf) từ performance-summary.json ─────────────────────────

def fallback_perf_group(run_dir: Path) -> dict:
    result: dict[str, dict] = {}
    for f in find_files(run_dir, "performance-summary.json"):
        obj = load_json_safe(f)
        if not isinstance(obj, dict):
            continue
        d = obj.get("derived") or {}
        pl = obj.get("perf_load") or {}
        conc_probe = obj.get("concurrency") if isinstance(obj.get("concurrency"), dict) else {}

        tps = pl.get("tps_per_stream_p50")
        if tps is None:
            tps = (d.get("tps_est") or {}).get("p50")

        ttft = (d.get("ttft_ms") or {}).get("p50")
        if ttft is None:
            ttft = pl.get("ttft_ms_p50")

        resp = (d.get("response_ms") or {}).get("p50")
        if resp is None:
            resp = pl.get("latency_total_p50_ms")

        rps = pl.get("rps")
        if rps is None:
            rps = conc_probe.get("rps")

        ccu = pl.get("concurrency")
        if ccu is None:
            ccu = conc_probe.get("concurrency")

        errs = pl.get("errors")
        if errs is None:
            errs = conc_probe.get("errors")

        candidates = {
            "R-PERF-TPS": tps,
            "R-PERF-TTFT": ttft,
            "R-PERF-RPS": rps,
            "R-PERF-CCU": ccu,
            "R-CTRL-RESP15": resp,
        }
        for code, val in candidates.items():
            if val is not None and code not in result:
                result[code] = {
                    "value": val,
                    "_source": rel(run_dir, f),
                    "_fallback": True,
                    "note": f"suy ra từ performance-summary.json (errors={errs})" if errs is not None else "suy ra từ performance-summary.json",
                }
        if len(result) == 5:
            break
    return result


# ───────────────────────── fallback: nhóm C (throughput mô hình) ─────────────────────────

def fallback_model_group(run_dir: Path) -> dict:
    result: dict[str, dict] = {}
    specs = [
        ("throughput_embed.json", "R-MODEL-EMB1200", "peak_per_s"),
        ("throughput_rerank.json", "R-MODEL-RERANK180", "peak_per_s"),
        ("llm-layer.json", "R-LLM-TPS28", "tps_per_stream_p50"),
    ]
    for filename, code, key in specs:
        for f in find_files(run_dir, filename):
            obj = load_json_safe(f)
            if not isinstance(obj, dict):
                continue
            val = obj.get(key)
            if val is not None:
                result[code] = {
                    "value": val,
                    "_source": rel(run_dir, f),
                    "_fallback": True,
                    "note": obj.get("note", ""),
                }
                break
    return result


# ───────────────────────── đánh giá đạt/không đạt theo registry op ─────────────────────────

def eval_pass(code: str, op: str, nguong, value, higher_is_better: bool):
    if value is None:
        return None
    try:
        if op == ">":
            return value > nguong
        if op == ">=":
            return value >= nguong
        if op == "<":
            return value < nguong
        if op == "<=":
            return value <= nguong
        if op == "=":
            # R-KPI-ENTITY100: hợp đồng ghi "=100" nhưng thực tế áp dụng >= 99.999 (xem noi_ap_nguong)
            if code == "R-KPI-ENTITY100":
                return value >= 99.999
            return value == nguong
        if op == "range" and isinstance(nguong, (list, tuple)) and len(nguong) == 2:
            lo, hi = nguong
            return lo <= value <= hi
        if op == "~":
            return None  # không phải gate cứng theo hợp đồng — chỉ hiển thị tham khảo
    except Exception as e:
        warn(f"eval_pass({code}) lỗi: {e}")
        return None
    return None


def fmt_nguong(op: str, nguong) -> str:
    if op == "range" and isinstance(nguong, (list, tuple)):
        return f"{nguong[0]}–{nguong[1]}"
    return f"{op} {nguong}"


# ───────────────────────── cạm bẫy #1: SAST majority vs single-pass ─────────────────────────

def detect_sast_method(run_dir: Path):
    for f in find_files(run_dir, "sast.jsonl"):
        methods = set()
        try:
            with f.open(encoding="utf-8") as fh:
                for i, line in enumerate(fh):
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                    except Exception:
                        continue
                    m = obj.get("method")
                    if m:
                        methods.add(str(m))
                    elif isinstance(obj.get("runs"), int) and obj.get("runs", 1) <= 1:
                        methods.add("single-pass (suy ra: runs<=1, không có field method)")
                    if i > 500:
                        break
        except Exception as e:
            warn(f"đọc {f} lỗi: {e}")
            continue
        if methods:
            return ", ".join(sorted(methods)), rel(run_dir, f)
    return None, None


def sast_method_tag(method_label: str | None) -> str:
    if not method_label:
        return "unknown"
    low = method_label.lower()
    if "majority" in low:
        return "majority-2-3"
    if "single-pass" in low:
        return "single-pass"
    return "unknown"


# ───────────────────────── cạm bẫy #2: EMB/RERANK đo tại host hay WAN ─────────────────────────

def detect_emb_rerank_source(kpi_data: dict, fallback_data: dict):
    notes = []
    for code in ("R-MODEL-EMB1200", "R-MODEL-RERANK180"):
        e = kpi_data.get(code) or fallback_data.get(code)
        if e and e.get("note"):
            notes.append(str(e["note"]))
    combined = " ".join(notes)
    low = combined.lower()
    if "đo tại host" in low or "do tai host" in low or ("ssh" in low and "host" in low):
        return "host (xác nhận trong note của raw-log — đúng vị trí đo theo ngưỡng gốc A100)", "host", combined
    if "wan" in low:
        return ("WAN/qua mạng (KHÔNG PHẢI đo tại host — số throughput có thể thấp giả, "
                "so với ngưỡng gốc tính trên A100 tại chỗ)"), "wan", combined
    return ("KHÔNG XÁC ĐỊNH — không tìm thấy ghi chú nguồn đo (host/WAN) trong kpi-summary.json "
            "hay throughput_embed.json/throughput_rerank.json của run này"), "unknown", combined


# ───────────────────────── build hàng dữ liệu cho 19 KPI ─────────────────────────

def build_rows(registry: dict, kpi_data: dict, fallback_data: dict) -> list[dict]:
    rows = []
    for item in registry["kpi"]:
        code = item["ma"]
        entry = kpi_data.get(code)
        used_fallback = False
        if entry is None:
            entry = fallback_data.get(code)
            used_fallback = True
        value = entry.get("value") if entry else None
        source = entry.get("_source") if entry else None
        note = (entry.get("note") or "") if entry else ""

        if value is None:
            status = "N/A (thiếu dữ liệu)"
            passed = None
        else:
            if entry is not None and not used_fallback and entry.get("pass") is not None:
                passed = bool(entry["pass"])
            else:
                passed = eval_pass(code, item["op"], item["nguong"], value, item.get("higher_is_better", True))
            if passed is None:
                status = "tham khảo (không phải gate cứng)"
            else:
                status = "Đạt" if passed else "Không đạt"

        rows.append({
            "ma": code,
            "ten": item["ten"],
            "nhom": item["nhom"],
            "nhom_ten": item["nhom_ten"],
            "don_vi": item.get("don_vi", ""),
            "nguong": fmt_nguong(item["op"], item["nguong"]),
            "enforced": item.get("enforced"),
            "value": value,
            "pass": passed,
            "status": status,
            "source": source,
            "used_fallback": used_fallback,
            "note": note,
            "ghi_chu": item.get("ghi_chu", ""),
        })
    return rows


# ───────────────────────── ClearML reporting ─────────────────────────

def report_to_clearml(rows: list[dict], registry: dict, run_dir: Path, task_name: str,
                       project: str, sast_info, emb_rerank_info, reasoning) -> tuple[str, str]:
    os.environ.setdefault("CLEARML_CONFIG_FILE", str(DEFAULT_CLEARML_CONF))
    from clearml import Task  # import muộn để --dry-run không cần cài clearml

    task = Task.init(project_name=project, task_name=task_name, reuse_last_task_id=False)
    logger = task.get_logger()

    n_logged = 0
    for r in rows:
        if r["value"] is None:
            continue
        try:
            logger.report_scalar(title=r["nhom_ten"], series=r["ten"], value=float(r["value"]), iteration=0)
            n_logged += 1
        except Exception as e:
            warn(f"report_scalar lỗi cho {r['ma']}: {e}")
    info(f"đã report_scalar {n_logged}/{len(rows)} KPI (còn lại thiếu dữ liệu, đã bỏ qua)")

    if reasoning and reasoning.get("value") is not None:
        try:
            logger.report_scalar(title="Chất lượng AI (phụ, không tính vào 19 KPI)",
                                  series="Logic/Reasoning marker (R-KPI-REASONING)",
                                  value=float(reasoning["value"]), iteration=0)
        except Exception as e:
            warn(f"report_scalar R-KPI-REASONING lỗi: {e}")

    # report_table
    try:
        try:
            import pandas as pd
            df = pd.DataFrame([{
                "Mã": r["ma"], "Tên": r["ten"], "Nhóm": r["nhom_ten"],
                "Giá trị đo": r["value"], "Đơn vị": r["don_vi"], "Ngưỡng": r["nguong"],
                "Gate cứng?": r["enforced"], "Kết quả": r["status"], "Nguồn": r["source"] or "-",
                "Ghi chú": r["note"],
            } for r in rows])
            logger.report_table(title="Tổng hợp 19 KPI", series="ket-qua", iteration=0, table_plot=df)
        except ImportError:
            warn("pandas không có sẵn — dùng đường CSV tạm cho report_table")
            tmp = Path(tempfile.mkstemp(suffix=".csv")[1])
            with tmp.open("w", newline="", encoding="utf-8") as fh:
                w = csv.writer(fh)
                w.writerow(["Mã", "Tên", "Nhóm", "Giá trị đo", "Đơn vị", "Ngưỡng", "Gate cứng?", "Kết quả", "Nguồn", "Ghi chú"])
                for r in rows:
                    w.writerow([r["ma"], r["ten"], r["nhom_ten"], r["value"], r["don_vi"], r["nguong"],
                                r["enforced"], r["status"], r["source"] or "-", r["note"]])
            logger.report_table(title="Tổng hợp 19 KPI", series="ket-qua", iteration=0, csv=str(tmp))
    except Exception as e:
        warn(f"report_table lỗi: {e}")

    # comment + tags
    try:
        meta = registry.get("_meta", {})
        n_pass = sum(1 for r in rows if r["pass"] is True)
        n_fail = sum(1 for r in rows if r["pass"] is False)
        n_na = sum(1 for r in rows if r["value"] is None)
        lines = [
            f"Run folder: {run_dir}",
            f"Báo cáo tạo lúc: {datetime.now(timezone.utc).isoformat()}Z",
            f"Tổng quan: {n_pass} đạt / {n_fail} không đạt / {n_na} thiếu dữ liệu (tổng {len(rows)} KPI)",
            "",
            "=== Cảnh báo phương pháp chung (01-KPI/kpi-registry.json _meta) ===",
        ]
        lsl = meta.get("luat_sai_lech", {})
        if lsl:
            lines.append(f"- {lsl.get('ma_hop_dong','R-EVAL-INDEP')}: ngưỡng sai lệch {lsl.get('nguong_phan_tram')}% — {lsl.get('cong_thuc','')}")
        mdt = meta.get("muc_dat_tung_cau", {})
        if mdt:
            lines.append(f"- Mức đạt từng câu (base_grade) dùng chung nhiều KPI: {mdt.get('gia_tri')}")
        c20 = meta.get("canh_bao_ma_thu_20", {})
        if c20:
            lines.append(f"- {c20.get('ma','R-CHATFLOW-TPS25')} (KHÔNG nằm trong 19 KPI): {c20.get('ghi_chu','')}")
        k12 = meta.get("kpi_thu_12_khong_nguong", {})
        if k12:
            rv = f"{reasoning['value']}%" if reasoning and reasoning.get("value") is not None else "không tìm thấy dữ liệu trong run này"
            lines.append(f"- {k12.get('ma','R-KPI-REASONING')} (KHÔNG nằm trong 19 KPI, chỉ là 1 trục radar): {k12.get('ghi_chu','')} — đo được: {rv}")

        lines += ["", "=== 2 cạm bẫy phương pháp đã biết (bắt buộc lưu ý khi so sánh run) ==="]
        sast_label, sast_src = sast_info
        sast_ghi_chu = next((i.get("ghi_chu", "") for i in registry["kpi"] if i["ma"] == "R-LLM-SAST85"), "")
        lines.append(f"[R-LLM-SAST85] method phát hiện: {sast_label or 'KHÔNG XÁC ĐỊNH (không tìm thấy sast.jsonl trong run này)'}"
                     + (f" (nguồn: {sast_src})" if sast_src else ""))
        lines.append(f"    {sast_ghi_chu}")

        emb_label, _emb_tag, _emb_raw = emb_rerank_info
        emb_ghi_chu = " | ".join(
            i.get("ghi_chu", "") for i in registry["kpi"] if i["ma"] in ("R-MODEL-EMB1200", "R-MODEL-RERANK180") and i.get("ghi_chu")
        )
        lines.append(f"[R-MODEL-EMB1200 / R-MODEL-RERANK180] nguồn đo phát hiện: {emb_label}")
        lines.append(f"    {emb_ghi_chu}")

        lines += ["", "=== Ghi chú phương pháp từng KPI (registry.ghi_chu, đầy đủ 19 KPI) ==="]
        for item in registry["kpi"]:
            if item.get("ghi_chu"):
                lines.append(f"[{item['ma']}] {item['ghi_chu']}")

        task.set_comment("\n".join(lines))
    except Exception as e:
        warn(f"set_comment lỗi: {e}")

    try:
        tags = [f"sast-method:{sast_method_tag(sast_info[0])}", f"emb-rerank-source:{emb_rerank_info[1]}"]
        task.add_tags(tags)
    except Exception as e:
        warn(f"add_tags lỗi: {e}")

    task_id = task.id
    try:
        task_url = task.get_output_log_web_page()
    except Exception:
        task_url = f"(xem trong ClearML web UI, project={project}, task_id={task_id})"

    try:
        task.flush(wait_for_uploads=True)
    except Exception:
        pass
    try:
        task.close()
    except Exception:
        pass

    return task_id, task_url


# ───────────────────────── main ─────────────────────────

def resolve_run_dir(raw: str) -> Path:
    p = Path(raw)
    if not p.is_absolute():
        cand = Path.cwd() / p
        if cand.exists():
            p = cand
        else:
            cand2 = KPI_DIR / p
            if cand2.exists():
                p = cand2
            else:
                p = cand  # để lỗi rõ ràng bên dưới nếu vẫn không tồn tại
    return p.resolve()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("run_dir", help="folder run KPI (vd runs/latest, hoặc KET-QUA/2026-08-26)")
    ap.add_argument("--task-name", default=None, help='mặc định "kpi-run-<tên folder>"')
    ap.add_argument("--project", default="secai-kpi")
    ap.add_argument("--registry", default=str(DEFAULT_REGISTRY))
    ap.add_argument("--dry-run", action="store_true", help="chỉ in ra, không gọi ClearML")
    ap.add_argument("--out-json", default=None, help="ghi task_id/task_url (hoặc kết quả dry-run) ra file JSON")
    args = ap.parse_args()

    run_dir = resolve_run_dir(args.run_dir)
    if not run_dir.exists() or not run_dir.is_dir():
        print(f"[clearml_report][ERROR] run_dir không tồn tại: {run_dir}", file=sys.stderr)
        sys.exit(1)

    registry_path = Path(args.registry)
    if not registry_path.is_absolute():
        registry_path = (KPI_DIR / registry_path).resolve() if not registry_path.exists() else registry_path.resolve()
    try:
        registry = load_registry(registry_path)
    except Exception as e:
        print(f"[clearml_report][ERROR] không load được registry {registry_path}: {e}", file=sys.stderr)
        sys.exit(1)

    run_id = run_dir.name
    task_name = args.task_name or f"kpi-run-{run_id}"

    info(f"run_dir      = {run_dir}")
    info(f"registry     = {registry_path}  ({len(registry['kpi'])} KPI)")
    info(f"task_name    = {task_name}  (project={args.project})")

    kpi_data = collect_kpi_summary(run_dir)
    info(f"tìm thấy kpi-summary.json cho {len(kpi_data)} mã KPI trực tiếp: {sorted(kpi_data)}")

    missing_codes = [i["ma"] for i in registry["kpi"] if i["ma"] not in kpi_data]
    fallback_data = {}
    if missing_codes:
        fallback_data.update(fallback_perf_group(run_dir))
        fallback_data.update(fallback_model_group(run_dir))
        found_fb = [c for c in missing_codes if c in fallback_data]
        if found_fb:
            info(f"fallback (raw perf/throughput files) bổ sung thêm: {found_fb}")

    still_missing = [c for c in missing_codes if c not in fallback_data]
    if still_missing:
        warn(f"KHÔNG tìm thấy dữ liệu cho: {still_missing} — sẽ đánh dấu N/A, không crash.")

    reasoning = collect_reasoning(run_dir)
    sast_label, sast_src = detect_sast_method(run_dir)
    emb_label, emb_tag, _emb_raw = detect_emb_rerank_source(kpi_data, fallback_data)
    info(f"R-LLM-SAST85 method       = {sast_label or 'KHÔNG XÁC ĐỊNH'}")
    info(f"EMB/RERANK nguồn đo       = {emb_label}")

    rows = build_rows(registry, kpi_data, fallback_data)

    if args.dry_run:
        info("=== DRY-RUN — sẽ KHÔNG gọi ClearML ===")
        for r in rows:
            print(f"  {r['ma']:<20} {r['ten']:<32} value={r['value']!s:<12} thr={r['nguong']:<14} -> {r['status']}"
                  + ("  [fallback]" if r["used_fallback"] else ""))
        result = {
            "dry_run": True, "run_dir": str(run_dir), "task_name": task_name, "project": args.project,
            "rows": rows,
            "sast_method": sast_label, "sast_source": sast_src,
            "emb_rerank_source_label": emb_label, "emb_rerank_source_tag": emb_tag,
            "reasoning": reasoning,
        }
        if args.out_json:
            Path(args.out_json).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
            info(f"đã ghi {args.out_json}")
        return

    task_id, task_url = report_to_clearml(
        rows, registry, run_dir, task_name, args.project,
        (sast_label, sast_src), (emb_label, emb_tag, _emb_raw), reasoning,
    )
    print(f"TASK_ID={task_id}")
    print(f"TASK_URL={task_url}")
    if args.out_json:
        Path(args.out_json).write_text(
            json.dumps({"task_id": task_id, "task_url": task_url, "project": args.project,
                        "task_name": task_name, "run_dir": str(run_dir)}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        info(f"đã ghi {args.out_json}")


if __name__ == "__main__":
    main()
