"""Metasploit collector — parse db/modules_metadata_base.json (structured, no repo walk).

CVE-linked modules only (the catalog is CVE-centric). Artifact = the module .rb inside the
cloned repo; `artifact_path` is repo-relative, resolved by the backend. BSD, redistributable.
"""
from __future__ import annotations

import json
import re
from collections.abc import Iterator
from pathlib import Path

from ..models import NormalizedPoc

_CVE = re.compile(r"CVE-\d{4}-\d{3,7}", re.I)
RANK = 30  # highest source tier (verified, curated)


class MetasploitCollector:
    name = "metasploit"

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).resolve()
        self.meta = self.root / "db" / "modules_metadata_base.json"

    def available(self) -> bool:
        return self.meta.is_file()

    def collect(self) -> Iterator[NormalizedPoc]:
        data = json.loads(self.meta.read_text(encoding="utf-8", errors="replace"))
        for fullname, e in data.items():
            refs = e.get("references") or []
            cves = sorted({m.upper() for r in refs for m in _CVE.findall(str(r))})
            if not cves:
                continue
            path = (e.get("path") or "").lstrip("/")  # e.g. modules/exploits/.../x.rb
            if not path:
                continue
            msf_rank = e.get("rank") or 0
            yield NormalizedPoc(
                source="metasploit", source_ref=fullname, kind="exploit",
                title=e.get("name") or fullname, cve_ids=cves,
                description=(e.get("description") or "").strip()[:2000],
                platform=e.get("platform") or None, language="rb",
                verified=True, rank=RANK + (1 if msf_rank >= 400 else 0),  # good+ rank
                url=f"https://github.com/rapid7/metasploit-framework/blob/master/{path}",
                license="BSD-3-Clause (Metasploit Framework)", redistributable=True,
                artifact_sha256=None, artifact_path=path,
                date_published=e.get("disclosure_date") or None,
                provenance={"collector": "metasploit", "fullname": fullname, "msf_rank": msf_rank})
