#!/usr/bin/env python3
"""from_customer.py — Đọc bảng do KHÁCH HÀNG cung cấp (CSV/XLSX) thành bản ghi chuẩn hoá.

Đây là đường dùng khi "phối hợp Chủ đầu tư tổng hợp bộ test mới" (R-EVAL-INDEP):
khách đưa danh sách CVE quan tâm (hoặc câu hỏi thô), công cụ chuẩn hoá + bổ sung
ground truth còn thiếu.

Cột nhận biết (không phân biệt hoa/thường, dấu tiếng Việt tuỳ chọn):
  cve_id       CVE-2024-3272            (khuyến nghị — có thì ground truth tự suy ra được)
  cau_hoi      câu hỏi tiếng Việt       (tuỳ chọn — bỏ trống thì sinh theo template)
  nang_luc     cve_qa|sast|exploit|remediation|translation|report|poc_search|pentest
  do_kho       de|trung_binh|kho|tu_duy
  tu_bat_buoc  từ khoá bắt buộc, phân tách bằng `;`
  ghi_chu      ghi chú ground truth của khách

QUAN TRỌNG: module này **chạy được hoàn toàn offline**. Chỉ khi bật `--enrich` mới gọi
NVD (hoặc Qdrant) để bổ sung severity/CVSS/CWE cho những dòng khách bỏ trống.
"""
import csv
import json
import os
import re
import sys

CVE_RE = re.compile(r"CVE-\d{4}-\d{4,7}", re.I)

ALIASES = {
    "cve_id": {"cve_id", "cve", "cveid", "ma_cve", "mã cve", "ma cve"},
    "cau_hoi": {"cau_hoi", "câu hỏi", "cau hoi", "question", "input", "noi_dung", "nội dung"},
    "nang_luc": {"nang_luc", "năng lực", "nang luc", "capability", "loai", "loại"},
    "do_kho": {"do_kho", "độ khó", "do kho", "difficulty", "muc_do", "mức độ"},
    "tu_bat_buoc": {"tu_bat_buoc", "từ bắt buộc", "tu bat buoc", "must_contain", "keywords",
                    "tu_khoa", "từ khoá", "từ khóa"},
    "ghi_chu": {"ghi_chu", "ghi chú", "ghi chu", "notes", "note", "ground_truth"},
}


def _canon(name):
    n = (name or "").strip().lower().lstrip("﻿")
    for canon, alts in ALIASES.items():
        if n == canon or n in alts:
            return canon
    return n


def _read_rows(path):
    """Đọc CSV hoặc XLSX → list[dict] với khoá đã chuẩn hoá."""
    ext = os.path.splitext(path)[1].lower()
    if ext in (".xlsx", ".xlsm"):
        try:
            from openpyxl import load_workbook
        except ImportError:
            raise SystemExit("Cần openpyxl để đọc .xlsx — hoặc xuất file sang .csv rồi chạy lại.")
        wb = load_workbook(path, read_only=True, data_only=True)
        ws = wb.active
        it = ws.iter_rows(values_only=True)
        header = [_canon(str(c) if c is not None else "") for c in next(it)]
        rows = []
        for r in it:
            if r is None or all(c is None or str(c).strip() == "" for c in r):
                continue
            rows.append({h: ("" if v is None else str(v).strip())
                         for h, v in zip(header, r)})
        return rows
    with open(path, encoding="utf-8-sig", newline="") as f:
        rd = csv.DictReader(f)
        rd.fieldnames = [_canon(h) for h in (rd.fieldnames or [])]
        return [{k: (v or "").strip() for k, v in row.items() if k}
                for row in rd
                if any((v or "").strip() for v in row.values())]


def fetch(path, enrich=False, qdrant_lookup=None, log=print):
    """Trả (records, overrides).

    records  — bản ghi CVE chuẩn hoá (để sinh câu theo template)
    overrides— các dòng khách đã tự viết sẵn câu hỏi; giữ nguyên câu của khách
    """
    rows = _read_rows(path)
    log(f"   · đọc {len(rows)} dòng từ {os.path.basename(path)}")

    records, overrides, need_enrich = [], [], []
    for i, row in enumerate(rows, 1):
        cid_raw = row.get("cve_id", "")
        m = CVE_RE.search(cid_raw) or CVE_RE.search(row.get("cau_hoi", ""))
        cid = m.group(0).upper() if m else None

        must = [t.strip() for t in re.split(r"[;,|]", row.get("tu_bat_buoc", "")) if t.strip()]
        rec = {
            "cve_id": cid, "severity": None, "cvss_score": None, "cvss_vector": None,
            "year": cid.split("-")[1] if cid else None, "kev": False,
            "cwe_refs": [c.upper() for c in re.findall(r"CWE-\d+", " ".join(
                [row.get("ghi_chu", ""), row.get("tu_bat_buoc", "")]), re.I)],
            "products": [], "text": row.get("ghi_chu", "") or row.get("cau_hoi", ""),
            "capec_entries": [], "mitre_techniques": [], "_source": "customer",
            "_row": i,
        }

        if row.get("cau_hoi"):
            overrides.append({
                "input": row["cau_hoi"],
                "capability": (row.get("nang_luc") or "cve_qa").strip() or "cve_qa",
                "difficulty": (row.get("do_kho") or "").strip() or None,
                "must_contain": ([cid] if cid else []) + must,
                "notes": row.get("ghi_chu", ""),
                "rec": rec,
            })
        elif cid:
            records.append(rec)
            need_enrich.append(cid)
        else:
            log(f"     ! dòng {i}: không có cve_id lẫn cau_hoi → bỏ qua")

    log(f"   · {len(overrides)} câu khách tự viết · {len(records)} dòng chỉ có CVE-id")

    if enrich and need_enrich:
        log(f"   · bổ sung ground truth cho {len(need_enrich)} CVE…")
        got = {}
        if qdrant_lookup:                       # ưu tiên KB nội bộ (offline, nhanh)
            got = qdrant_lookup(need_enrich) or {}
            log(f"     Qdrant bổ sung được {len(got)}")
        missing = [c for c in need_enrich if c not in got]
        if missing:
            try:
                from . import from_public
            except ImportError:
                import from_public
            for r in from_public.enrich_from_nvd(missing, log=log):
                got[r["cve_id"]] = r
        for rec in records:
            src = got.get(rec["cve_id"])
            if src:
                for k in ("severity", "cvss_score", "cvss_vector", "text"):
                    rec[k] = rec[k] or src.get(k)
                rec["cwe_refs"] = rec["cwe_refs"] or src.get("cwe_refs") or []
                rec["products"] = rec["products"] or src.get("products") or []
                rec["kev"] = rec["kev"] or src.get("kev", False)
        for o in overrides:
            src = got.get(o["rec"]["cve_id"])
            if src:
                o["rec"].update({k: src.get(k) for k in
                                 ("severity", "cvss_score", "cvss_vector")})
                o["rec"]["text"] = o["rec"]["text"] or src.get("text", "")

    return records, overrides


if __name__ == "__main__":
    recs, ovr = fetch(sys.argv[1], log=lambda *a: print(*a, file=sys.stderr))
    for r in recs:
        print(json.dumps(r, ensure_ascii=False))
    for o in ovr:
        print(json.dumps(o, ensure_ascii=False, default=str))
