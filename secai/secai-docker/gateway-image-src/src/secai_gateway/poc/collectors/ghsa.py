"""GHSA collector — GitHub Security Advisories (github/advisory-database), CVE-aliased only.

kind=advisory: high-quality curated advisory metadata (summary, severity, affected ranges).
No exploit artifact — metadata/context that enriches a CVE. url points to the GHSA page.
"""
from __future__ import annotations

import json
import re
from collections.abc import Iterator
from pathlib import Path

from ..models import NormalizedPoc

_CVE = re.compile(r"CVE-\d{4}-\d{3,7}", re.I)
RANK = 12


class GhsaCollector:
    name = "ghsa"

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).resolve()
        self.adv = self.root / "advisories" / "github-reviewed"

    def available(self) -> bool:
        return self.adv.is_dir()

    def collect(self) -> Iterator[NormalizedPoc]:
        for jf in self.adv.rglob("GHSA-*.json"):
            try:
                d = json.loads(jf.read_text(encoding="utf-8", errors="replace"))
            except Exception:
                continue
            cves = sorted({m.upper() for a in (d.get("aliases") or []) for m in _CVE.findall(str(a))})
            if not cves:
                continue
            sev = str((d.get("database_specific") or {}).get("severity") or "").upper()
            yield NormalizedPoc(
                source="ghsa", source_ref=str(d.get("id") or jf.stem), kind="advisory",
                title=(d.get("summary") or d.get("id") or "GHSA advisory")[:200], cve_ids=cves,
                description=str(d.get("details") or "").strip()[:2000],
                platform=None, language="json",
                verified=True, rank=RANK + (1 if sev in ("HIGH", "CRITICAL") else 0),
                url=f"https://github.com/advisories/{d.get('id')}",
                license="CC-BY-4.0 (GitHub Advisory Database)", redistributable=True,
                artifact_sha256=None, artifact_path=None,  # advisory = metadata, no code artifact
                date_published=d.get("published"),
                provenance={"collector": "ghsa", "ghsa_id": d.get("id"), "severity": sev})
