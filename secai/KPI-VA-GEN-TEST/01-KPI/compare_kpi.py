#!/usr/bin/env python3
"""compare_kpi.py — So sánh KPI của HAI phiên bản hệ thống secAI và áp luật sai lệch ≤5%.

Dùng cho `R-EVAL-INDEP` (task-AI-requirements.md:93-94 · xlsx dòng 537/556):
    "phối hợp Chủ đầu tư tổng hợp 01 bộ test mới ...; chấp nhận khi sai lệch ≤ 5%
     so với chỉ số công bố; vượt ngưỡng → fine-tune/điều chỉnh miễn phí"

  A = bản ĐANG ĐO (phiên bản mới / bộ test độc lập)
  B = BASELINE để đối chiếu (mặc định nên là bản công bố kpi_results/)

Đọc được cả 3 dạng đường dẫn:
  - thư mục run   (…/2026-08-14-010858-evalall)  → tự tìm raw-logs/ và reports/
  - thư mục raw-logs (…/raw-logs)
  - file .json trực tiếp

Thuần stdlib. openpyxl chỉ dùng cho --xlsx và được bọc try/except.

Ví dụ:
  python3 compare_kpi.py --a runs/moi --b kpi_results/quality/raw-logs
  python3 compare_kpi.py --a runs/moi --b kpi_results/quality/raw-logs --md bao-cao.md --xlsx bao-cao.xlsx
"""
import argparse
import json
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
REGISTRY = HERE / "kpi-registry.json"

# ── mã màu (tắt khi không phải TTY) ───────────────────────────────────────
_TTY = sys.stdout.isatty()
def _c(code, s):
    return f"\033[{code}m{s}\033[0m" if _TTY else s
GREEN  = lambda s: _c("32", s)
RED    = lambda s: _c("31", s)
YELLOW = lambda s: _c("33", s)
DIM    = lambda s: _c("2", s)


def load_registry():
    return json.loads(REGISTRY.read_text(encoding="utf-8"))


# ── đọc số đo ─────────────────────────────────────────────────────────────
def _read_json(p):
    try:
        return json.loads(pathlib.Path(p).read_text(encoding="utf-8"))
    except Exception:
        return None


def collect(root):
    """Gom mọi giá trị KPI từ một thư mục run / raw-logs / file json.

    Trả (values: {ma: float}, meta: dict, nguon: {ma: ten_file}).
    """
    root = pathlib.Path(root)
    values, nguon = {}, {}
    meta = {}

    if root.is_file():
        cands = [root]
    else:
        # thư mục run có raw-logs/ + reports/; hoặc chính nó đã là raw-logs/
        subs = [root, root / "raw-logs", root / "reports",
                root / "quality" / "raw-logs", root / "perf" / "raw-logs",
                root / "models" / "raw-logs"]
        cands = []
        for d in subs:
            if d.is_dir():
                for name in ("kpi-summary.json", "performance-summary.json",
                             "radar-data.json", "run-manifest.json"):
                    f = d / name
                    if f.is_file():
                        cands.append(f)

    for f in cands:
        data = _read_json(f)
        if not isinstance(data, dict):
            continue
        if f.name == "run-manifest.json":
            meta.update({k: v for k, v in data.items() if not isinstance(v, (list, dict))})
            continue
        if f.name == "performance-summary.json":
            for k in ("model", "infra", "measured_date", "note"):
                if k in data and k not in meta:
                    meta[k] = data[k]
            if isinstance(data.get("endpoints"), dict):
                meta.setdefault("endpoints", json.dumps(data["endpoints"], ensure_ascii=False))
        # dạng {ma: {value:…}}
        for ma, v in data.items():
            if isinstance(v, dict) and "value" in v:
                if v["value"] is not None and ma not in values:
                    values[ma] = v["value"]
                    nguon[ma] = f.name
    return values, meta, nguon


def sast_method(root):
    """Trả 'majority-2/3' | 'single' | None — để cảnh báo trộn hai cách chấm SAST."""
    root = pathlib.Path(root)
    for d in (root, root / "raw-logs", root / "quality" / "raw-logs"):
        f = d / "sast.jsonl"
        if f.is_file():
            try:
                for line in f.read_text(encoding="utf-8").splitlines():
                    if line.strip():
                        return json.loads(line).get("method") or "single"
            except Exception:
                return None
    return None


# ── đánh giá đạt/không đạt theo registry ─────────────────────────────────
def judge(entry, val):
    """Có đạt ngưỡng hợp đồng không (bất kể script có ép hay không)."""
    if val is None:
        return None
    op, thr = entry["op"], entry["nguong"]
    if op == ">":
        return val > thr
    if op == ">=":
        return val >= thr
    if op == "<":
        return val < thr
    if op == "<=":
        return val <= thr
    if op == "=":
        return val >= thr - 1e-3
    if op == "range":
        return thr[0] <= val <= thr[1]
    if op == "~":
        return val >= thr * 0.9
    return None


def drift_pct(baseline, moi, higher_is_better):
    """Sai lệch % theo hướng XẤU ĐI. Âm = tốt lên.

    Cùng công thức với ho_so_ft/finetuning/scripts/check_finetune_thresholds.py:30-37.

    Trả None khi KHÔNG tính được tỉ lệ: baseline = 0 mà giá trị mới khác 0
    (vd Hallucination baseline 0% → mọi thay đổi đều là vô cùng phần trăm).
    Khi đó `worse_than()` quyết định kết luận theo HƯỚNG thay đổi.
    """
    if baseline is None or moi is None:
        return None
    if baseline == moi:
        return 0.0
    if baseline == 0:
        return None
    if higher_is_better:
        return 100.0 * (baseline - moi) / abs(baseline)
    return 100.0 * (moi - baseline) / abs(baseline)


def worse_than(baseline, moi, higher_is_better):
    """Giá trị mới có XẤU HƠN baseline không (chỉ xét hướng, không xét độ lớn)."""
    if baseline is None or moi is None or baseline == moi:
        return False
    return (moi < baseline) if higher_is_better else (moi > baseline)


def fmt(v):
    if v is None:
        return "—"
    if isinstance(v, float):
        return f"{v:.2f}".rstrip("0").rstrip(".")
    return str(v)


def fmt_thr(entry):
    op, thr = entry["op"], entry["nguong"]
    if op == "range":
        return f"{thr[0]}–{thr[1]}"
    if op == "~":
        return f"~{thr}"
    return f"{op} {thr}"


# ── main ──────────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser(
        description="So sánh KPI 2 phiên bản secAI + áp luật sai lệch ≤5% (R-EVAL-INDEP)",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--a", required=True, help="Bản ĐANG ĐO (phiên bản mới)")
    ap.add_argument("--b", required=True, help="BASELINE đối chiếu (nên là kpi_results/quality/raw-logs)")
    ap.add_argument("--ten-a", default="A (đo mới)")
    ap.add_argument("--ten-b", default="B (baseline)")
    ap.add_argument("--tolerance", type=float, default=5.0, help="Ngưỡng sai lệch %% (mặc định 5 theo hợp đồng)")
    ap.add_argument("--md", help="Xuất báo cáo Markdown")
    ap.add_argument("--xlsx", help="Xuất Excel (hình dạng baseline-comparison.xlsx)")
    ap.add_argument("--strict", action="store_true",
                    help="exit 1 nếu có KPI vượt ngưỡng sai lệch")
    args = ap.parse_args()

    reg = load_registry()
    va, meta_a, src_a = collect(args.a)
    vb, meta_b, src_b = collect(args.b)

    if not va:
        print(RED(f"✗ Không đọc được KPI nào từ --a {args.a}"), file=sys.stderr)
        return 2
    if not vb:
        print(RED(f"✗ Không đọc được KPI nào từ --b {args.b}"), file=sys.stderr)
        return 2

    rows, canh_bao = [], []
    for e in reg["kpi"]:
        ma = e["ma"]
        a, b = va.get(ma), vb.get(ma)
        d = None if (a is None or b is None) else a - b
        dp = drift_pct(b, a, e["higher_is_better"])
        if a is None or b is None:
            ket_luan = "THIẾU SỐ"
        elif dp is None:
            # baseline = 0 → không có tỉ lệ %; kết luận theo hướng thay đổi.
            ket_luan = ("VƯỢT NGƯỠNG SAI LỆCH"
                        if worse_than(b, a, e["higher_is_better"]) else "TỐT HƠN")
        elif abs(dp) <= args.tolerance:
            ket_luan = "CHẤP NHẬN"
        elif dp < 0:
            ket_luan = "TỐT HƠN"          # lệch > 5% nhưng theo hướng tốt
        else:
            ket_luan = "VƯỢT NGƯỠNG SAI LỆCH"
        rows.append({
            "ma": ma, "ten": e["ten"], "nhom": e["nhom"], "don_vi": e["don_vi"],
            "nguong": fmt_thr(e), "enforced": e["enforced"],
            "a": a, "b": b, "delta": d, "delta_pct": dp,
            "dat_a": judge(e, a), "dat_b": judge(e, b),
            "ket_luan": ket_luan,
        })

    # ── cảnh báo so sánh khập khiễng ──────────────────────────────────────
    for k in ("model", "endpoints", "dataset", "commit", "n", "infra"):
        x, y = meta_a.get(k), meta_b.get(k)
        if x and y and str(x) != str(y):
            canh_bao.append(f"`{k}` khác nhau: A = `{x}` · B = `{y}`")
    ma_, mb_ = sast_method(args.a), sast_method(args.b)
    if ma_ and mb_ and ma_ != mb_:
        canh_bao.append(f"SAST chấm KHÁC CÁCH: A = `{ma_}` · B = `{mb_}` "
                        f"— majority-2/3 và 1-lượt KHÔNG so sánh được với nhau")

    # ── in console ────────────────────────────────────────────────────────
    print()
    print(f"  SO SÁNH KPI — {args.ten_a}  vs  {args.ten_b}")
    print(f"  Ngưỡng sai lệch cho phép: ±{args.tolerance}%  (R-EVAL-INDEP)")
    print("  " + "─" * 100)
    print(f"  {'MÃ KPI':<20}{'NGƯỠNG':<12}{'A':>10}{'B':>10}{'Δ':>10}{'Δ%':>9}  {'ĐẠT A':<7}{'KẾT LUẬN'}")
    print("  " + "─" * 100)
    nhom_cur = None
    for r in rows:
        if r["nhom"] != nhom_cur:
            nhom_cur = r["nhom"]
            ten = {"A": "CHẤT LƯỢNG AI", "B": "HIỆU NĂNG CHATFLOW", "C": "THROUGHPUT MÔ HÌNH"}[nhom_cur]
            print(DIM(f"  ── {ten} " + "─" * (98 - len(ten))))
        dat = "—" if r["dat_a"] is None else ("ĐẠT" if r["dat_a"] else "KHÔNG")
        if r["dat_a"] is True:
            dat = GREEN(f"{dat:<7}")
        elif r["dat_a"] is False:
            dat = RED(f"{dat:<7}")
        else:
            dat = f"{dat:<7}"
        kl = r["ket_luan"]
        if kl == "CHẤP NHẬN":
            kl_s = GREEN(kl)
        elif kl == "TỐT HƠN":
            kl_s = GREEN(kl + " ▲")
        elif kl == "THIẾU SỐ":
            kl_s = YELLOW(kl)
        else:
            kl_s = RED(kl)
        star = "" if r["enforced"] is True else " *"
        print(f"  {r['ma']+star:<20}{r['nguong']:<12}{fmt(r['a']):>10}{fmt(r['b']):>10}"
              f"{fmt(r['delta']):>10}{fmt(r['delta_pct']):>9}  {dat}{kl_s}")
    print("  " + "─" * 100)

    n_ok  = sum(1 for r in rows if r["ket_luan"] in ("CHẤP NHẬN", "TỐT HƠN"))
    n_bad = sum(1 for r in rows if r["ket_luan"] == "VƯỢT NGƯỠNG SAI LỆCH")
    n_na  = sum(1 for r in rows if r["ket_luan"] == "THIẾU SỐ")
    print(f"  Tổng: {n_ok} chấp nhận · {n_bad} vượt ngưỡng · {n_na} thiếu số  (trên {len(rows)} KPI)")
    print(DIM("  * = KPI mà SCRIPT KHÔNG ép ngưỡng (xem BANG-KPI.md, cạm bẫy #1) — cột ĐẠT A ở đây do"))
    print(DIM("      compare_kpi.py tự chấm lại theo ngưỡng hợp đồng, KHÔNG lấy trường 'pass' của script."))

    if canh_bao:
        print()
        print(RED("  ⚠ KHÔNG SO SÁNH ĐƯỢC MỘT CÁCH CÔNG BẰNG:"))
        for c in canh_bao:
            print(RED(f"    · {c}"))

    if n_bad:
        print()
        print(RED(f"  → {n_bad} KPI vượt ngưỡng sai lệch {args.tolerance}%: "
                  "theo hợp đồng phải fine-tune/điều chỉnh MIỄN PHÍ rồi chạy lại."))
    elif not n_na:
        print()
        print(GREEN(f"  → Toàn bộ KPI nằm trong sai lệch ±{args.tolerance}% → ĐẠT kiểm chứng độc lập."))
    print()

    if args.md:
        write_md(args, rows, canh_bao, meta_a, meta_b, n_ok, n_bad, n_na)
        print(f"  ✓ Markdown → {args.md}")
    if args.xlsx:
        if write_xlsx(args, rows):
            print(f"  ✓ Excel    → {args.xlsx}")
        else:
            print(YELLOW("  ! Thiếu openpyxl → bỏ qua .xlsx (bản .md vẫn đầy đủ)"))
        print()

    return 1 if (args.strict and n_bad) else 0


def write_md(args, rows, canh_bao, meta_a, meta_b, n_ok, n_bad, n_na):
    L = ["# Báo cáo so sánh KPI — kiểm chứng độc lập (`R-EVAL-INDEP`)", "",
         f"- **{args.ten_a}**: `{args.a}`",
         f"- **{args.ten_b}**: `{args.b}`",
         f"- **Ngưỡng sai lệch cho phép**: ±{args.tolerance}%",
         f"- **Kết quả**: {n_ok} chấp nhận · {n_bad} vượt ngưỡng · {n_na} thiếu số (trên {len(rows)} KPI)",
         ""]
    if canh_bao:
        L += ["> ⚠ **Cảnh báo so sánh khập khiễng**", ""]
        L += [f"> - {c}" for c in canh_bao] + [""]
    L += ["`Δ%` tính theo **hướng xấu đi**: dương = tệ hơn baseline, âm = tốt hơn.", ""]
    for nhom, ten in (("A", "Chất lượng AI"), ("B", "Hiệu năng chatflow"), ("C", "Throughput mô hình")):
        sub = [r for r in rows if r["nhom"] == nhom]
        if not sub:
            continue
        L += [f"## Nhóm {nhom} — {ten}", "",
              "| Mã KPI | Tên KPI | Ngưỡng cam kết | Đo thực tế (A) | Baseline (B) | Chênh lệch | Δ% | Đạt A | Kết luận |",
              "|---|---|---|---:|---:|---:|---:|:---:|---|"]
        for r in sub:
            star = "" if r["enforced"] is True else " ¹"
            dat = "—" if r["dat_a"] is None else ("✅" if r["dat_a"] else "❌")
            L.append(f"| `{r['ma']}`{star} | {r['ten']} | {r['nguong']} {r['don_vi']} | "
                     f"{fmt(r['a'])} | {fmt(r['b'])} | {fmt(r['delta'])} | {fmt(r['delta_pct'])} | "
                     f"{dat} | {r['ket_luan']} |")
        L.append("")
    L += ["¹ KPI mà **script không ép ngưỡng** (`build_perf_summary.py:65` và các lambda "
          "`v is not None`). Cột *Đạt A* ở đây do `compare_kpi.py` **tự chấm lại** theo ngưỡng "
          "hợp đồng, không lấy trường `pass` của script.", ""]
    if meta_a or meta_b:
        L += ["## Metadata hai lần chạy", "", "| Trường | A | B |", "|---|---|---|"]
        for k in sorted(set(meta_a) | set(meta_b)):
            L.append(f"| `{k}` | {meta_a.get(k,'—')} | {meta_b.get(k,'—')} |")
        L.append("")
    if n_bad:
        L += ["## Kết luận", "",
              f"**{n_bad} KPI vượt ngưỡng sai lệch {args.tolerance}%.** Theo `R-EVAL-INDEP` "
              "(xlsx dòng 556), nhà thầu phải **fine-tune/điều chỉnh miễn phí** rồi chạy lại "
              "trước khi nghiệm thu.", ""]
    elif not n_na:
        L += ["## Kết luận", "",
              f"Toàn bộ KPI nằm trong sai lệch ±{args.tolerance}% so với baseline → "
              "**ĐẠT kiểm chứng độc lập**.", ""]
    pathlib.Path(args.md).write_text("\n".join(L), encoding="utf-8")


def write_xlsx(args, rows):
    """Xuất đúng hình dạng kpi_results/quality/reports/baseline-comparison.xlsx."""
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Font, PatternFill, Alignment
    except Exception:
        return False
    wb = Workbook()
    ws = wb.active
    ws.title = "Baseline vs Do thuc"
    hdr = ["Mã KPI", "Tên KPI", "Nhóm", "Ngưỡng cam kết", "Đo thực tế (A)",
           "Baseline (B)", "Chênh lệch", "Δ%", "Đạt A", "Kết luận"]
    ws.append(hdr)
    head_fill = PatternFill("solid", fgColor="DDDDDD")
    for c in ws[1]:
        c.font = Font(bold=True)
        c.fill = head_fill
        c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    fill_ok  = PatternFill("solid", fgColor="C6EFCE")
    fill_bad = PatternFill("solid", fgColor="FFC7CE")
    fill_na  = PatternFill("solid", fgColor="FFEB9C")
    for r in rows:
        ws.append([r["ma"], r["ten"], r["nhom"], f"{r['nguong']} {r['don_vi']}",
                   r["a"], r["b"], r["delta"], r["delta_pct"],
                   "—" if r["dat_a"] is None else ("ĐẠT" if r["dat_a"] else "KHÔNG ĐẠT"),
                   r["ket_luan"]])
        f = {"CHẤP NHẬN": fill_ok, "TỐT HƠN": fill_ok,
             "THIẾU SỐ": fill_na}.get(r["ket_luan"], fill_bad)
        ws.cell(row=ws.max_row, column=10).fill = f
    for col, w in zip("ABCDEFGHIJ", (20, 34, 7, 18, 15, 14, 12, 10, 12, 26)):
        ws.column_dimensions[col].width = w
    ws.freeze_panes = "A2"
    wb.save(args.xlsx)
    return True


if __name__ == "__main__":
    sys.exit(main())
