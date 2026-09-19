#!/usr/bin/env python3
"""from_qdrant.py — Lấy CVE thật từ Qdrant `cve_kb` (KB nội bộ của hệ thống).

Khác bản gốc `export_pool.py`: bản gốc chạy qua `ssh secai python3 -`; bản này gọi
HTTP THẲNG tới QDRANT_URL nên chạy được ở bất kỳ máy nào thấy Qdrant (mặc định
http://localhost:6333). Chiến lược lấy mẫu phân tầng giữ nguyên:

  - KEV oversample ~15%
  - severity: CRITICAL 25% · HIGH 35% · MEDIUM 30% · LOW 10%
  - nhóm năm: 2017-2020 20% · 2021-2023 40% · 2024-2025 40%
  - lọc chất lượng: có cwe_refs + products + len(text) > 400 (LOW nới xuống 250)

Thuần stdlib.
"""
import json
import os
import sys
import urllib.request

PAGE = 256
MAX_PAGES_PER_STRATUM = 60
SEV_SHARE = {"CRITICAL": 0.25, "HIGH": 0.35, "MEDIUM": 0.30, "LOW": 0.10}
YEAR_GROUPS = [(["2017", "2018", "2019", "2020"], 0.20),
               (["2021", "2022", "2023"], 0.40),
               (["2024", "2025", "2026"], 0.40)]


def _base_url():
    url = os.environ.get("QDRANT_URL", "http://localhost:6333").rstrip("/")
    col = os.environ.get("QDRANT_COLLECTION", "cve_kb")
    return f"{url}/collections/{col}/points"


def _post(path, body, timeout=30):
    req = urllib.request.Request(_base_url() + path, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def ping():
    """Kiểm tra Qdrant sống và collection tồn tại. Trả (ok, thông điệp)."""
    try:
        url = os.environ.get("QDRANT_URL", "http://localhost:6333").rstrip("/")
        col = os.environ.get("QDRANT_COLLECTION", "cve_kb")
        with urllib.request.urlopen(f"{url}/collections/{col}", timeout=8) as r:
            d = json.loads(r.read())
        n = d.get("result", {}).get("points_count")
        return True, f"Qdrant OK · {col} · {n} điểm"
    except Exception as e:
        return False, f"Qdrant KHÔNG kết nối được: {e}"


def count_cve(cve_id):
    """Đếm số điểm của một CVE — dùng để xác minh câu bẫy zero-hit."""
    try:
        r = _post("/count", {"filter": {"must": [{"key": "cve_id", "match": {"value": cve_id}}]},
                             "exact": True})
        return r["result"]["count"]
    except Exception:
        return None


def _scroll(must, cap_pages=MAX_PAGES_PER_STRATUM):
    offset = None
    for _ in range(cap_pages):
        body = {"filter": {"must": must}, "limit": PAGE,
                "with_payload": True, "with_vector": False}
        if offset is not None:
            body["offset"] = offset
        res = _post("/scroll", body)["result"]
        for p in res["points"]:
            yield p["payload"]
        offset = res.get("next_page_offset")
        if offset is None:
            return


def _quality(pl, min_text):
    return bool(pl.get("cve_id") and pl.get("severity") and pl.get("year")
                and pl.get("cwe_refs") and pl.get("products")
                and len(pl.get("text") or "") > min_text)


def _norm(pl):
    return {
        "cve_id": pl["cve_id"],
        "severity": (pl.get("severity") or "").upper(),
        "cvss_score": pl.get("cvss_score"),
        "cvss_vector": pl.get("cvss_vector"),
        "year": str(pl.get("year") or pl["cve_id"].split("-")[1]),
        "kev": bool(pl.get("is_in_kev")),
        "cwe_refs": list(pl.get("cwe_refs") or [])[:5],
        "products": list(pl.get("products") or [])[:12],
        "text": (pl.get("text") or "")[:2400],
        "capec_entries": list(pl.get("capec_entries") or [])[:5],
        "mitre_techniques": list(pl.get("mitre_techniques") or [])[:5],
        "_source": "qdrant",
    }


def fetch(target=2000, exclude=None, log=print):
    """Lấy ~`target` CVE phân tầng. `exclude` = set cve_id phải loại (bảo đảm độc lập)."""
    exclude = {c.upper() for c in (exclude or set())}
    seen, out = set(exclude), []
    kev_quota = round(target * 0.15)

    def take(must, quota, min_text):
        n = 0
        for pl in _scroll(must):
            if n >= quota:
                break
            cid = (pl.get("cve_id") or "").upper()
            if not cid or cid in seen or not _quality(pl, min_text):
                continue
            seen.add(cid)
            out.append(_norm(pl))
            n += 1
        return n

    core = {"key": "chunk_kind", "match": {"value": "core"}}
    got = take([core, {"key": "is_in_kev", "match": {"value": True}}], kev_quota, 400)
    log(f"   KEV: {got}/{kev_quota}")

    rest = target - len(out)
    for sev, sshare in SEV_SHARE.items():
        min_text = 250 if sev == "LOW" else 400
        for years, yshare in YEAR_GROUPS:
            quota = max(1, round(rest * sshare * yshare))
            got = take([core, {"key": "severity", "match": {"value": sev}},
                        {"key": "year", "match": {"any": years}}], quota, min_text)
            log(f"   {sev:<8} {years[0]}-{years[-1]}: {got}/{quota}")

    if len(out) < target:  # bù thêm, bỏ ràng buộc năm
        for sev in SEV_SHARE:
            if len(out) >= target:
                break
            take([core, {"key": "severity", "match": {"value": sev}}],
                 target - len(out), 250 if sev == "LOW" else 400)

    log(f"   → tổng {len(out)} CVE (loại trừ {len(exclude)} đã dùng)")
    return out[:target]


if __name__ == "__main__":
    ok, msg = ping()
    print(msg, file=sys.stderr)
    if ok:
        for r in fetch(int(sys.argv[1]) if len(sys.argv) > 1 else 20,
                       log=lambda *a: print(*a, file=sys.stderr)):
            print(json.dumps(r, ensure_ascii=False))
