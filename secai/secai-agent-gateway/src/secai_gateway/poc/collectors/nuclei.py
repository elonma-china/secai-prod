"""Nuclei collector — CVE-tagged detection templates (projectdiscovery/nuclei-templates).

Detection (not exploit) — how to DETECT a vuln, defensive-leaning. Artifact = the YAML in
the clone; `artifact_path` repo-relative, resolved by the backend. MIT, redistributable.
"""
from __future__ import annotations

import re
from collections.abc import Iterator
from pathlib import Path

import yaml

from ..models import NormalizedPoc

_CVE = re.compile(r"CVE-\d{4}-\d{3,7}", re.I)
RANK = 20


class NucleiCollector:
    name = "nuclei"

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).resolve()

    def available(self) -> bool:
        return self.root.is_dir()

    def collect(self) -> Iterator[NormalizedPoc]:
        for yf in self.root.rglob("*.yaml"):
            try:
                doc = yaml.safe_load(yf.read_text(encoding="utf-8", errors="replace"))
            except Exception:
                continue
            if not isinstance(doc, dict) or "info" not in doc:
                continue
            info = doc.get("info") or {}
            cls = info.get("classification") or {}
            hay = f"{cls.get('cve-id') or ''} {doc.get('id') or ''}"
            cves = sorted({m.upper() for m in _CVE.findall(hay)})
            if not cves:
                continue
            rel = str(yf.relative_to(self.root))
            yield NormalizedPoc(
                source="nuclei", source_ref=str(doc.get("id") or rel), kind="detection",
                title=info.get("name") or str(doc.get("id") or rel), cve_ids=cves,
                description=str(info.get("description") or "").strip()[:2000],
                platform=None, language="yaml",
                verified=True, rank=RANK + (1 if str(info.get("severity")).lower() in ("critical", "high") else 0),
                url=f"https://github.com/projectdiscovery/nuclei-templates/blob/main/{rel}",
                license="MIT (nuclei-templates)", redistributable=True,
                artifact_sha256=None, artifact_path=rel,
                date_published=None,
                provenance={"collector": "nuclei", "template_id": doc.get("id"),
                            "severity": info.get("severity")})
