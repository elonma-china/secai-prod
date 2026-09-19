#!/usr/bin/env python3
"""make_deliverables.py — tạo kpi-summary.xlsx (Excel THẬT) + radar.svg (chart THẬT) bằng STDLIB.
Dùng khi máy chạy thiếu matplotlib/openpyxl (vd VPS Python 3.14). Đọc kpi-summary.json + radar-data.json.

Usage: python3 make_deliverables.py --logs RAW_DIR --reports REP_DIR
  → RAW_DIR/kpi-summary.xlsx · REP_DIR/radar.svg
"""
import argparse
import json
import math
import zipfile
from pathlib import Path


def esc(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;"))


def col(n):  # 0->A, 1->B...
    s = ""
    n += 1
    while n:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


def write_xlsx(path, rows):
    """rows = list[list[cell]]; cell = str | (số). Ghi .xlsx tối thiểu hợp lệ (stdlib zipfile)."""
    sheet = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
             '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>']
    for ri, row in enumerate(rows, 1):
        cells = []
        for ci, val in enumerate(row):
            ref = f"{col(ci)}{ri}"
            if isinstance(val, (int, float)) and not isinstance(val, bool):
                cells.append(f'<c r="{ref}"><v>{val}</v></c>')
            else:
                cells.append(f'<c r="{ref}" t="inlineStr"><is><t>{esc(val)}</t></is></c>')
        sheet.append(f'<row r="{ri}">{"".join(cells)}</row>')
    sheet.append("</sheetData></worksheet>")
    parts = {
        "[Content_Types].xml":
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
            '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/></Types>',
        "_rels/.rels":
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>',
        "xl/workbook.xml":
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            '<sheets><sheet name="KPI" sheetId="1" r:id="rId1"/></sheets></workbook>',
        "xl/_rels/workbook.xml.rels":
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/></Relationships>',
        "xl/worksheets/sheet1.xml": "".join(sheet),
    }
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in parts.items():
            z.writestr(name, data)


def write_radar_svg(path, axes, actual, committed, coverage, title=""):
    """Spider/radar chart SVG thuần (actual vs committed)."""
    n = len(axes); cx = cy = 260; R = 180
    def pts(vals):
        out = []
        for i, v in enumerate(vals):
            ang = math.radians(-90 + i * 360 / n)
            r = R * max(0, min(100, v)) / 100.0
            out.append(f"{cx + r*math.cos(ang):.1f},{cy + r*math.sin(ang):.1f}")
        return " ".join(out)
    svg = [f'<svg xmlns="http://www.w3.org/2000/svg" width="520" height="560" font-family="sans-serif">',
           f'<text x="{cx}" y="24" text-anchor="middle" font-size="15" font-weight="bold">{esc(title)}</text>']
    for ring in (25, 50, 75, 100):  # lưới
        svg.append(f'<polygon points="{pts([ring]*n)}" fill="none" stroke="#ddd"/>')
    for i in range(n):  # spoke + nhãn
        ang = math.radians(-90 + i * 360 / n)
        ex, ey = cx + R*math.cos(ang), cy + R*math.sin(ang)
        svg.append(f'<line x1="{cx}" y1="{cy}" x2="{ex:.1f}" y2="{ey:.1f}" stroke="#ccc"/>')
        lx, ly = cx + (R+24)*math.cos(ang), cy + (R+24)*math.sin(ang)
        anc = "middle" if abs(math.cos(ang)) < 0.3 else ("start" if math.cos(ang) > 0 else "end")
        svg.append(f'<text x="{lx:.1f}" y="{ly:.1f}" text-anchor="{anc}" font-size="11">{esc(axes[i])}</text>')
        svg.append(f'<text x="{lx:.1f}" y="{ly+13:.1f}" text-anchor="{anc}" font-size="10" fill="#c00">{actual[i]:.1f}%</text>')
    svg.append(f'<polygon points="{pts(committed)}" fill="none" stroke="#888" stroke-dasharray="5,4" stroke-width="1.5"/>')
    svg.append(f'<polygon points="{pts(actual)}" fill="#3b82f6" fill-opacity="0.30" stroke="#1d4ed8" stroke-width="2"/>')
    svg.append(f'<text x="{cx}" y="{cy+R+70}" text-anchor="middle" font-size="12">Diện tích phủ: {coverage}% · '
               f'<tspan fill="#1d4ed8">đặc=thực đo</tspan> · <tspan fill="#888">nét đứt=cam kết</tspan></text>')
    svg.append("</svg>")
    Path(path).write_text("\n".join(svg), encoding="utf-8")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--logs", required=True)
    ap.add_argument("--reports", required=True)
    args = ap.parse_args()
    logs, reps = Path(args.logs), Path(args.reports)

    # 1. kpi-summary.xlsx
    kp = json.loads((logs / "kpi-summary.json").read_text(encoding="utf-8"))
    rows = [["Mã KPI", "Tên", "Đơn vị", "Ngưỡng", "Thực đo", "Đạt"]]
    for code, v in kp.items():
        rows.append([code, v.get("name", ""), v.get("unit", ""), str(v.get("thr", "")),
                     v.get("value") if isinstance(v.get("value"), (int, float)) else str(v.get("value")),
                     "ĐẠT" if v.get("pass") else "CHƯA"])
    write_xlsx(logs / "kpi-summary.xlsx", rows)
    print(f"  ✓ {logs/'kpi-summary.xlsx'}")

    # 2. radar.svg
    rd_path = reps / "radar-data.json"
    if rd_path.exists():
        rd = json.loads(rd_path.read_text(encoding="utf-8"))
        write_radar_svg(reps / "radar.svg", rd["axes"], rd["actual"], rd["committed"],
                        rd.get("coverage_area_pct", "?"), "KPI AI Radar")
        print(f"  ✓ {reps/'radar.svg'}")
    else:
        print("  ✗ thiếu radar-data.json → bỏ radar.svg")


if __name__ == "__main__":
    main()
