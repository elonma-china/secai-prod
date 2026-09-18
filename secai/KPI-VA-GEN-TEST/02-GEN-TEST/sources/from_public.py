#!/usr/bin/env python3
"""from_public.py — Lấy CVE từ NGUỒN CÔNG KHAI, độc lập hoàn toàn với KB của hệ thống.

Dùng khi cần phản biện "tự ra đề tự chấm": ground truth đến từ bên thứ ba, không phải
từ chính Qdrant mà hệ thống đang tra cứu.

Feeds:
  nvd        NVD 2.0 REST API   https://services.nvd.nist.gov/rest/json/cves/2.0
  kev        CISA KEV catalog   https://www.cisa.gov/sites/default/files/feeds/known_exploited_vulnerabilities.json
  exploitdb  Exploit-DB CSV     https://gitlab.com/exploit-database/exploitdb/-/raw/main/files_exploits.csv
  ctibench   CTIBench TSV (tuỳ chọn — phải tự tải về, xem GHI CHÚ LICENSE)

GHI CHÚ LICENSE — BẮT BUỘC ĐỌC:
  * NVD / CISA KEV: dữ liệu chính phủ Hoa Kỳ, dùng tự do.
  * Exploit-DB: GPLv2 — chỉ lấy METADATA (tiêu đề/EDB-ID), không tải/phát tán mã khai thác.
  * CTIBench (NeurIPS'24, HF `AI4Sec/cti-bench`): **CC-BY-NC-SA-4.0** → CHỈ dùng đánh giá
    nội bộ, **KHÔNG đóng gói lại / bàn giao / phát hành kèm sản phẩm**. Vì vậy module này
    KHÔNG tự tải CTIBench; phải trỏ tới file TSV đã tải sẵn.

Thuần stdlib (urllib). Cần mạng ra ngoài.
"""
import csv
import io
import json
import os
import re
import sys
import urllib.request

NVD_API = "https://services.nvd.nist.gov/rest/json/cves/2.0"
KEV_URL = ("https://www.cisa.gov/sites/default/files/feeds/"
           "known_exploited_vulnerabilities.json")
EDB_CSV = "https://gitlab.com/exploit-database/exploitdb/-/raw/main/files_exploits.csv"
UA = {"User-Agent": "secai-testset-generator/1.0"}


def _get(url, timeout=60):
    req = urllib.request.Request(url, headers=dict(UA))
    key = os.environ.get("NVD_API_KEY")
    if key and url.startswith(NVD_API):
        req.add_header("apiKey", key)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def _sev_from_cvss(score):
    if score is None:
        return None
    return ("CRITICAL" if score >= 9.0 else "HIGH" if score >= 7.0
            else "MEDIUM" if score >= 4.0 else "LOW")


def _parse_cve(item):
    """Chuẩn hoá 1 bản ghi 'vulnerabilities[]' của NVD 2.0."""
    c = item.get("cve", {})
    cid = c.get("id")
    desc = ""
    for d in c.get("descriptions", []):
        if d.get("lang") == "en":
            desc = d.get("value", "")
            break
    score = vector = sev = None
    for k in ("cvssMetricV31", "cvssMetricV30", "cvssMetricV2"):
        if c.get("metrics", {}).get(k):
            cd = c["metrics"][k][0].get("cvssData", {})
            score, vector = cd.get("baseScore"), cd.get("vectorString")
            sev = cd.get("baseSeverity") or _sev_from_cvss(score)
            break
    cwes = [d["value"] for w in c.get("weaknesses", []) for d in w.get("description", [])
            if re.fullmatch(r"CWE-\d+", d.get("value", ""))]
    prods = []
    for cfg in c.get("configurations", []):
        for node in cfg.get("nodes", []):
            for m in node.get("cpeMatch", []):
                p = (m.get("criteria") or "").split(":")
                if len(p) > 4 and p[4] not in prods:
                    prods.append(p[4])
    return {"cve_id": cid, "severity": (sev or "").upper() or None,
            "cvss_score": score, "cvss_vector": vector,
            "year": (cid or "CVE-0-0").split("-")[1],
            "kev": bool(c.get("cisaExploitAdd")), "cwe_refs": sorted(set(cwes))[:5],
            "products": prods[:12], "text": desc[:2400],
            "capec_entries": [], "mitre_techniques": [], "_source": "public:nvd"}


def fetch_kev(limit=None, log=print):
    log("   · tải CISA KEV…")
    data = json.loads(_get(KEV_URL))
    out = []
    for v in data.get("vulnerabilities", []):
        cid = v.get("cveID")
        if not cid:
            continue
        out.append({"cve_id": cid, "severity": None, "cvss_score": None, "cvss_vector": None,
                    "year": cid.split("-")[1], "kev": True, "cwe_refs": [],
                    "products": [p for p in (v.get("product"), v.get("vendorProject")) if p],
                    "text": (v.get("shortDescription") or "").strip(),
                    "capec_entries": [], "mitre_techniques": [], "_source": "public:kev"})
        if limit and len(out) >= limit:
            break
    log(f"     KEV: {len(out)} CVE")
    return out


def fetch_nvd(n=200, days_back=720, log=print):
    """Lấy CVE GẦN ĐÂY từ NVD.

    Quan trọng: gọi NVD với `startIndex=0` sẽ trả về CVE cũ nhất (1999) — hầu hết
    KHÔNG có CVSS v3 lẫn CWE nên bị loại hết ở bước lọc chất lượng. Vì vậy ở đây
    duyệt theo CỬA SỔ NGÀY CÔNG BỐ lùi dần từ hiện tại (NVD giới hạn mỗi cửa sổ
    tối đa 120 ngày).

    Không có API key: NVD giới hạn 5 request/30 giây. Đặt biến NVD_API_KEY để nhanh hơn.
    """
    import datetime as _dt
    out, seen = [], set()
    end = _dt.datetime.now(_dt.timezone.utc)
    step = _dt.timedelta(days=110)
    windows = max(1, days_back // 110)
    for w in range(windows):
        if len(out) >= n:
            break
        start = end - step
        s_iso = start.strftime("%Y-%m-%dT%H:%M:%S.000")
        e_iso = end.strftime("%Y-%m-%dT%H:%M:%S.000")
        idx = 0
        while len(out) < n:
            per = min(2000, max(200, (n - len(out)) * 4))
            url = (f"{NVD_API}?pubStartDate={s_iso}&pubEndDate={e_iso}"
                   f"&resultsPerPage={per}&startIndex={idx}")
            log(f"   · NVD {start:%Y-%m-%d}→{end:%Y-%m-%d} idx={idx}…")
            try:
                data = json.loads(_get(url, timeout=90))
            except Exception as e:
                log(f"     ! lỗi NVD: {e}")
                break
            vulns = data.get("vulnerabilities", [])
            if not vulns:
                break
            for item in vulns:
                r = _parse_cve(item)
                if r["cve_id"] and r["cve_id"] not in seen:
                    seen.add(r["cve_id"])
                    out.append(r)
            idx += len(vulns)
            if idx >= data.get("totalResults", 0):
                break
        end = start
    log(f"     NVD: {len(out)} CVE")
    return out


def enrich_from_nvd(cve_ids, log=print):
    """Bổ sung mô tả/CVSS/CWE cho danh sách CVE cụ thể (dùng cho nguồn khách hàng)."""
    out = []
    ids = list(cve_ids)
    for i, cid in enumerate(ids, 1):
        try:
            data = json.loads(_get(f"{NVD_API}?cveId={cid}", timeout=30))
            v = data.get("vulnerabilities", [])
            if not v:
                log(f"     ! {cid}: NVD không có dữ liệu")
                continue
            out.append(_parse_cve(v[0]))
            log(f"     [{i}/{len(ids)}] {cid} ✓")
        except Exception as e:
            log(f"     ! {cid}: {e}")
    return out


def fetch_exploitdb(limit=None, log=print):
    """Metadata Exploit-DB (KHÔNG tải mã khai thác). Trả map cve_id -> [tiêu đề]."""
    log("   · tải Exploit-DB metadata…")
    raw = _get(EDB_CSV).decode("utf-8", "replace")
    m = {}
    for r in csv.DictReader(io.StringIO(raw)):
        for cid in re.findall(r"CVE-\d{4}-\d{4,7}", r.get("codes") or ""):
            m.setdefault(cid, []).append(r.get("description") or "")
        if limit and len(m) >= limit:
            break
    log(f"     Exploit-DB: {len(m)} CVE có PoC công khai")
    return m


def fetch_ctibench(path, log=print):
    """Đọc TSV CTIBench ĐÃ TẢI SẴN. Không tự tải (license CC-BY-NC-SA-4.0)."""
    if not path or not os.path.isfile(path):
        log(f"     ! Không thấy file CTIBench: {path}")
        return []
    with open(path, encoding="utf-8") as f:
        rows = [{"prompt": r.get("Prompt"), "gt": r.get("GT")}
                for r in csv.DictReader(f, delimiter="\t")]
    log(f"     CTIBench: {len(rows)} dòng (CC-BY-NC-SA-4.0 — CHỈ dùng nội bộ)")
    return rows


def fetch(n=300, feeds=("nvd", "kev"), exclude=None, log=print):
    """Gộp các feed thành danh sách bản ghi chuẩn hoá, đã loại `exclude`."""
    exclude = {c.upper() for c in (exclude or set())}
    recs, seen = [], set(exclude)

    if "kev" in feeds:
        for r in fetch_kev(log=log):
            if r["cve_id"].upper() not in seen:
                seen.add(r["cve_id"].upper())
                recs.append(r)

    if "nvd" in feeds:
        by_id = {r["cve_id"]: r for r in fetch_nvd(max(n * 6, 400), log=log)}
        for r in recs:                       # làm giàu KEV (KEV thiếu CVSS/CWE)
            src = by_id.get(r["cve_id"])
            if src:
                for k in ("severity", "cvss_score", "cvss_vector"):
                    r[k] = r[k] or src[k]
                r["cwe_refs"] = r["cwe_refs"] or src["cwe_refs"]
                r["products"] = r["products"] or src["products"]
                r["text"] = r["text"] or src["text"]
        for cid, r in by_id.items():
            if cid.upper() not in seen:
                seen.add(cid.upper())
                recs.append(r)

    if "exploitdb" in feeds:
        m = fetch_exploitdb(log=log)
        for r in recs:
            if r["cve_id"] in m:
                r["has_poc"] = True

    good = [r for r in recs if r.get("cve_id") and r.get("text") and len(r["text"]) > 80
            and r.get("severity") and r.get("cwe_refs")]
    log(f"   → {len(good)}/{len(recs)} bản ghi đủ chất lượng (cần mô tả + severity + CWE)")
    return good[:n]


if __name__ == "__main__":
    for r in fetch(int(sys.argv[1]) if len(sys.argv) > 1 else 10,
                   log=lambda *a: print(*a, file=sys.stderr)):
        print(json.dumps(r, ensure_ascii=False))
