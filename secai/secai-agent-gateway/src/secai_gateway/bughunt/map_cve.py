"""MapCVE — extract / sweep CVEs from hunt artifacts (BB ``/scan-cves`` + nuclei parse).

Only attaches CVE IDs that appear in scanner output. Never invents CVEs.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Callable

from . import arsenal
from .engine import bugbounty_root
from .workdir import findings_dir, recon_dir

LogFn = Callable[[str, str], None]

CVE_RE = re.compile(r"CVE-\d{4}-\d{4,}", re.I)


def _collect_text_files(root: Path, *, limit: int = 80) -> list[Path]:
    if not root.is_dir():
        return []
    out: list[Path] = []
    for p in sorted(root.rglob("*")):
        if not p.is_file() or p.stat().st_size == 0 or p.stat().st_size > 5_000_000:
            continue
        if p.suffix.lower() in {".png", ".jpg", ".zip", ".gz", ".woff"}:
            continue
        out.append(p)
        if len(out) >= limit:
            break
    return out


def extract_cves_from_workdir(workdir: Path) -> list[dict[str, Any]]:
    """Parse CVE IDs from recon/findings text artifacts."""
    hits: dict[str, dict[str, Any]] = {}
    roots = [findings_dir(workdir), recon_dir(workdir)]
    for root in roots:
        for path in _collect_text_files(root):
            try:
                text = path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            for m in CVE_RE.finditer(text):
                cve = m.group(0).upper()
                # Grab surrounding line as evidence
                line = ""
                for ln in text.splitlines():
                    if m.group(0) in ln:
                        line = ln.strip()[:400]
                        break
                prev = hits.get(cve)
                if prev is None:
                    try:
                        rel = str(path.relative_to(workdir))
                    except ValueError:
                        rel = str(path)
                    hits[cve] = {
                        "cve": cve,
                        "evidence": line or f"found in {path.name}",
                        "source_file": rel,
                        "cve_evidence": True,
                        "confidence": 0.75,
                        "vul_class": "cve_match",
                    }
    return list(hits.values())


async def run_cve_sweep(
    workdir: Path,
    *,
    settings: Any | None = None,
    on_log: LogFn | None = None,
) -> dict[str, Any]:
    """Run BB ``cve_scan.sh --recon`` into per-run findings/cve/."""
    td = arsenal.tools_dir(settings)
    script = td / "cve_scan.sh"
    rdir = recon_dir(workdir)
    out = findings_dir(workdir) / "cve"
    out.mkdir(parents=True, exist_ok=True)
    if not script.is_file():
        return {"ok": False, "skipped": True, "reason": "cve_scan.sh not found", "cves": []}

    live = rdir / "live" / "urls.txt"
    if not live.is_file() or live.stat().st_size == 0:
        return {"ok": False, "skipped": True, "reason": "no live URLs for CVE sweep", "cves": []}

    rc, so, se = await arsenal.run_bash_script(
        script,
        ["--recon", str(rdir)],
        cwd=bugbounty_root(settings),
        timeout_s=min(400, arsenal._timeout_s(settings)),
        on_log=on_log,
        env={
            "CVE_OUT_DIR": str(out),
            "NUCLEI_NO_UPDATE": "1",
            "NUC_RATE_LIMIT": "150",
            "NUC_CONCURRENCY": "25",
        },
        label="cve_scan",
    )
    # Parse jsonl if present
    jsonl = out / "nuclei_cve.jsonl"
    cves: list[dict[str, Any]] = []
    if jsonl.is_file():
        for ln in jsonl.read_text(encoding="utf-8", errors="replace").splitlines():
            if not ln.strip():
                continue
            try:
                row = json.loads(ln)
            except json.JSONDecodeError:
                continue
            info = row.get("info") if isinstance(row, dict) else {}
            name = ""
            severity = ""
            if isinstance(info, dict):
                name = str(info.get("name") or "")
                severity = str(info.get("severity") or "")
            blob = ln
            for m in CVE_RE.finditer(blob):
                cve = m.group(0).upper()
                cves.append({
                    "cve": cve,
                    "evidence": name or ln[:300],
                    "severity": severity or "unknown",
                    "matched_at": row.get("matched-at") or row.get("host") or "",
                    "cve_evidence": True,
                    "confidence": 0.85,
                    "vul_class": "nuclei_cve",
                    "source": "cve_scan",
                })
    # Also regex any other files written under out/
    for extra in extract_cves_from_workdir(workdir):
        if not any(x["cve"] == extra["cve"] for x in cves):
            cves.append(extra)

    # Dedupe by CVE
    uniq: dict[str, dict[str, Any]] = {}
    for c in cves:
        uniq[c["cve"]] = c

    return {
        "ok": rc in (0, 124) or bool(uniq),
        "rc": rc,
        "skipped": False,
        "cves": list(uniq.values()),
        "stdout_tail": (so or "")[-2000:],
        "stderr_tail": (se or "")[-1000:],
        "out_dir": str(out),
        "summary": f"MapCVE: {len(uniq)} CVE id(s) from evidence"
        + (f" (sweep rc={rc})" if not uniq else ""),
    }


async def map_cve_phase(
    workdir: Path,
    *,
    leads: list[dict[str, Any]],
    settings: Any | None = None,
    on_log: LogFn | None = None,
    run_sweep: bool = True,
) -> dict[str, Any]:
    """Combine artifact CVE parse + optional nuclei CVE sweep; attach to leads."""
    from_files = extract_cves_from_workdir(workdir)
    sweep: dict[str, Any] = {"cves": [], "skipped": True}
    if run_sweep:
        sweep = await run_cve_sweep(workdir, settings=settings, on_log=on_log)

    by_cve: dict[str, dict[str, Any]] = {}
    for c in from_files + list(sweep.get("cves") or []):
        by_cve[c["cve"]] = c

    mapped_leads: list[dict[str, Any]] = []
    for lead in leads:
        mapped_leads.append({**lead, "cve": lead.get("cve"), "mapped_cves": []})

    # Promote CVE hits as first-class mapped items (also merge into leads list)
    cve_leads = []
    for cve, meta in by_cve.items():
        cve_leads.append({
            "vul_class": meta.get("vul_class") or "cve_match",
            "cve": cve,
            "cve_evidence": True,
            "confidence": meta.get("confidence") or 0.8,
            "evidence": meta.get("evidence"),
            "severity": meta.get("severity"),
            "matched_at": meta.get("matched_at"),
            "source": meta.get("source") or "map_cve",
            "validated": True,
            "note": "CVE from scanner evidence only",
        })

    return {
        "ok": True,
        "cves": list(by_cve.values()),
        "cve_leads": cve_leads,
        "leads": mapped_leads,
        "sweep": {k: sweep.get(k) for k in ("ok", "skipped", "rc", "reason", "out_dir", "summary")},
        "summary": (
            f"MapCVE: {len(by_cve)} evidence-backed CVE(s); "
            f"sweep={'skipped' if sweep.get('skipped') else 'ran'}"
        ),
    }
