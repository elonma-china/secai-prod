#!/usr/bin/env python3
"""Populate the catalog's `poc_canonical` table by VERIFYING catalog candidates on a lab.

For a CVE with several catalog PoCs, run each candidate's ENTRY file against a live vulnerable
instance and mark the FIRST one that exploits as canonical (`PocCatalog.set_canonical`). Once
marked, `/ai/exploit` serves that exact artifact VERBATIM (routes skips the lossy slice/
consolidate for a canonical entry), so a CVE with a verified PoC always returns the known-working
one instead of a same-CVE sibling that merely ranks higher.

Verification target (pick one):
  --labs CVE-2017-5638,...   use evals/exploit_lab/labs/<cve>/ (docker compose up + success_signal)
  --all-labs                 every lab under evals/exploit_lab/labs
  --target URL --signal STR  an already-running target (no docker lifecycle)

Run from the repo root:  .venv/bin/python scripts/poc_verify_canonical.py --all-labs
Dry-run (no write):      ... --dry-run
"""
from __future__ import annotations

import argparse
import os
import pathlib
import re
import subprocess
import sys
import tempfile
from typing import Any

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "evals" / "exploit_lab"))

from secai_gateway.config import get_settings                      # noqa: E402
from secai_gateway.poc.catalog import PocCatalog                   # noqa: E402
from secai_gateway.poc.slicer import looks_like_python2, sniff_language  # noqa: E402

# Lab lifecycle + verdict + adaptive invocation are the eval harness's, reused verbatim so the
# verifier and the eval agree on "exploits" byte-for-byte.
from run_lab_eval import (  # noqa: E402
    LabSpec, _candidate_argvs, _sniff_lang, lab_down, lab_up, load_labs, verdict, wait_ready,
)

_MAX_CANDIDATES = 8
_CVE = re.compile(r"^CVE-\d{4}-\d{3,7}$", re.I)


def _store_dir() -> pathlib.Path:
    s = get_settings()
    return pathlib.Path(s.poc_artifact_store)


def _candidate_files(con, store: pathlib.Path, poc_id: int, artifact_path: str | None
                     ) -> list[dict[str, Any]]:
    """Read a catalog row's file set (or single artifact) from the content-addressed store."""
    rows = con.execute(
        "select rel_path,storage_path,language,role from poc_file where poc_id=? order by ordinal",
        (poc_id,)).fetchall()
    files: list[dict[str, Any]] = []
    if rows:
        for r in rows:
            p = store / r["storage_path"]
            code = p.read_text(encoding="utf-8", errors="replace") if p.is_file() else ""
            if code:
                files.append({"path": r["rel_path"], "code": code,
                              "role": r["role"] or "", "language": r["language"]})
    elif artifact_path:
        p = store / artifact_path
        if p.is_file():
            files.append({"path": artifact_path,
                          "code": p.read_text(encoding="utf-8", errors="replace"),
                          "role": "entry", "language": None})
    return files


def _entry(files: list[dict[str, Any]]) -> dict[str, Any] | None:
    for f in files:
        if f.get("role") == "entry":
            return f
    return files[0] if files else None


def _run_adaptive(files: list[dict[str, Any]], host_url: str, success: str, alt: str,
                  timeout: int = 45) -> tuple[bool, str]:
    """Materialize the file set, try the common CLI conventions, return (exploited, invocation)."""
    if not files:
        return False, "no-files"
    entry = files[0]
    lang = _sniff_lang(entry["code"])
    binaries = {"python": sys.executable, "bash": "bash", "ruby": "ruby", "php": "php",
                "perl": "perl", "javascript": "node"}
    binary = binaries.get(lang, sys.executable)
    import shutil
    if lang != "python" and shutil.which(binary) is None:
        return False, f"no-interpreter:{lang}"
    # Build a minimal LabSpec-like object for _candidate_argvs (needs host_url).
    class _L:
        pass
    lab = _L(); lab.host_url = host_url; lab.success_signal = success; lab.success_signal_alt = alt
    with tempfile.TemporaryDirectory() as td:
        tdp = pathlib.Path(td)
        entry_path = tdp / (os.path.basename(entry["path"]) or "poc")
        for i, f in enumerate(files):
            (tdp / (os.path.basename(f["path"]) or f"file{i}")).write_text(f["code"], encoding="utf-8")
        for argv in _candidate_argvs(entry["code"], lab, entry_path, lang):
            try:
                pr = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, cwd=td)
            except Exception as exc:  # noqa: BLE001
                continue
            if verdict(pr.stdout, pr.stderr, pr.returncode, lab) == "exploit_success":
                return True, " ".join(argv[2:]) or "(no args)"
    return False, "exhausted"


def _verify_cve(cat: PocCatalog, con, store: pathlib.Path, cve: str, host_url: str,
                success: str, alt: str, *, dry_run: bool) -> dict[str, Any]:
    rows = cat.search(cve, _MAX_CANDIDATES)
    tried = 0
    for r in rows:
        files = _candidate_files(con, store, r["id"], r.get("artifact_path"))
        files = [f for f in files if f.get("role") != "readme"]
        if not _entry(files):
            continue
        tried += 1
        ok, inv = _run_adaptive(files, host_url, success, alt)
        if ok:
            if not dry_run:
                cat.set_canonical(cve, r["id"], success_signal=success, target=host_url,
                                  note=f"verified via {r['source']}/{r.get('language')} inv=[{inv}]")
            return {"cve": cve, "poc_id": r["id"], "source": r["source"],
                    "language": r.get("language"), "invocation": inv, "tried": tried, "ok": True}
    return {"cve": cve, "poc_id": None, "ok": False, "tried": tried}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--labs", help="comma-separated CVE ids to verify against their lab")
    ap.add_argument("--all-labs", action="store_true", help="verify every lab under evals/exploit_lab/labs")
    ap.add_argument("--target", help="an already-running target URL (no docker lifecycle)")
    ap.add_argument("--signal", default="", help="success signal substring for --target")
    ap.add_argument("--dry-run", action="store_true", help="verify but do not write canonical")
    args = ap.parse_args()

    s = get_settings()
    cat = PocCatalog(s.poc_catalog_db)
    con = cat._db
    store = _store_dir()
    print(f"catalog={s.poc_catalog_db} store={store} canonical_before={cat.canonical_count()}")

    if args.target:
        if not args.labs:
            ap.error("--target needs --labs CVE,... (which CVE to verify)")
        out = []
        for cve in [c.strip().upper() for c in args.labs.split(",") if c.strip()]:
            out.append(_verify_cve(cat, con, store, cve, args.target, args.signal, "",
                                   dry_run=args.dry_run))
        for r in out:
            print(r)
        return 0 if any(r["ok"] for r in out) else 2

    if not (args.labs or args.all_labs):
        ap.error("pass --labs CVE,... / --all-labs / --target URL")

    labs = load_labs(set(c.strip().upper() for c in (args.labs or "").split(",") if c.strip())
                     or None)
    results = []
    for lab in labs:
        print(f"\n[verify] {lab.cve_id}")
        try:
            lab_up(lab)
            if not wait_ready(lab, 120):
                print("  lab not ready — skip")
                lab_down(lab)
                continue
            r = _verify_cve(cat, con, store, lab.cve_id, lab.host_url,
                            str(lab.success_signal), str(lab.success_signal_alt or ""),
                            dry_run=args.dry_run)
            results.append(r)
            print(f"  -> {'CANONICAL poc_id=%s (%s/%s) inv=%s' % (r['poc_id'], r['source'], r['language'], r['invocation']) if r['ok'] else 'no candidate exploited (%d tried)' % r['tried']}")
        finally:
            lab_down(lab)

    ok = sum(1 for r in results if r["ok"])
    print(f"\n=== verified {ok}/{len(results)} labs; canonical_total={cat.canonical_count()} ===")
    return 0 if ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
