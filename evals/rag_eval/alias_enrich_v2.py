#!/usr/bin/env python3
"""Alias enrichment, round 2: a common name PLUS the effect in the words users use, for CVEs that discovery
questions could not reach ("CVE nào của Citrix cho phép chiếm phiên đăng nhập?").

Why not alias_enrich.py: it skips a core chunk that already starts with "Tên gọi phổ biến:". Citrix Bleed had
the name but the record never says "session", so a question in the user's words found nothing.

Writes the prod collection (text prefix + re-embedded dense/bm25 of the core chunks of the listed CVEs only).
  python3 alias_enrich_v2.py plan                    # show what would change, write nothing
  python3 alias_enrich_v2.py apply --backup FILE     # backup (payload text + vectors) first, then update
  python3 alias_enrich_v2.py restore --backup FILE   # put the saved text + vectors back
Idempotent: a chunk already carrying exactly the wanted prefix is left alone.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts" / "knowledge"))
import kb_knowledge as kb  # noqa: E402  (http_json, embed_dense, sparse_embed, scroll)

COLLECTION = "cve_kb_hybrid"
PREFIX = "Tên gọi phổ biến:"
# CVE -> alias line. Every entry is a documented public name; the effect words are the ones the record lacks.
LINES: dict[str, str] = {
    "CVE-2023-4966": "Citrix Bleed (rò rỉ session token của NetScaler ADC / Gateway, kẻ tấn công chiếm phiên đăng nhập — session hijacking)",
    # ProxyLogon = the four Exchange Server 2021 CVEs chained for unauthenticated RCE (only the first carried the name)
    "CVE-2021-26855": "ProxyLogon (SSRF trên Microsoft Exchange Server, mở đầu chuỗi ProxyLogon cùng CVE-2021-26857, CVE-2021-26858, CVE-2021-27065)",
    "CVE-2021-26857": "ProxyLogon (giải tuần tự hóa không an toàn trong Exchange Unified Messaging, thuộc chuỗi ProxyLogon cùng CVE-2021-26855)",
    "CVE-2021-26858": "ProxyLogon (ghi file tùy ý sau xác thực trên Exchange, thuộc chuỗi ProxyLogon cùng CVE-2021-26855)",
    "CVE-2021-27065": "ProxyLogon (ghi file tùy ý sau xác thực trên Exchange, thuộc chuỗi ProxyLogon cùng CVE-2021-26855)",
}


def core_points(cve: str):
    flt = {"must": [{"key": "cve_id", "match": {"value": cve}}, {"key": "chunk_kind", "match": {"value": "core"}}]}
    return list(kb.scroll(COLLECTION, flt, with_vector=True))


def wanted_text(old: str, line: str) -> str:
    body = re.sub(r"^" + re.escape(PREFIX) + r"[^\n]*?\.\s(?=CVE ID:)", "", old, count=1)  # drop an older alias line
    return f"{PREFIX} {line}. {body}"


def plan() -> list[dict]:
    rows = []
    for cve, line in LINES.items():
        pts = core_points(cve)
        if not pts:
            print(f"{cve}: no core chunk in {COLLECTION}; skipped")
        for p in pts:
            old = str(p["payload"].get("text") or "")
            new = wanted_text(old, line)
            rows.append({"cve": cve, "id": p["id"], "old": old, "new": new, "same": new == old,
                         "vector": p.get("vector")})
    return rows


def put(pid, text: str, dense, sparse) -> None:
    kb.http_json(f"{kb.QDRANT}/collections/{COLLECTION}/points/payload?wait=true", method="POST",
                 body={"payload": {"text": text}, "points": [pid]})
    kb.http_json(f"{kb.QDRANT}/collections/{COLLECTION}/points/vectors?wait=true", method="PUT",
                 body={"points": [{"id": pid, "vector": {"dense": dense, "bm25": sparse}}]})


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=("plan", "apply", "restore"))
    ap.add_argument("--backup")
    a = ap.parse_args()
    if a.cmd == "restore":
        saved = json.loads(Path(a.backup).read_text())
        for r in saved:
            put(r["id"], r["old"], r["vector"]["dense"], r["vector"]["bm25"])
            print("restored", r["cve"], r["id"])
        return 0
    rows = plan()
    for r in rows:
        print(f"{r['cve']} {r['id']} {'unchanged' if r['same'] else 'UPDATE'}\n  old: {r['old'][:140]!r}\n  new: {r['new'][:200]!r}")
    if a.cmd == "plan":
        return 0
    if not a.backup:
        raise SystemExit("--backup FILE is required for apply")
    todo = [r for r in rows if not r["same"]]
    kb.check_embed_model()
    Path(a.backup).write_text(json.dumps(
        [{k: r[k] for k in ("cve", "id", "old", "vector")} for r in todo], ensure_ascii=False))
    Path(a.backup).chmod(0o600)
    dense = kb.embed_dense([r["new"] for r in todo], [r["id"] for r in todo])
    sparse = kb.sparse_embed([r["new"] for r in todo])
    for r, d, s in zip(todo, dense, sparse):
        put(r["id"], r["new"], d, s)
        print("updated", r["cve"], r["id"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
