"""Vulhub collector — per-CVE vulnerable lab environments (dir named CVE-xxxx-yyyy).

kind=lab: a docker-compose reproduction. REFERENCE ONLY — never run. Artifact = the
compose file (for static listing), resolved from the clone by the backend.
"""
from __future__ import annotations

import re
from collections.abc import Iterator
from pathlib import Path

from ..models import NormalizedPoc

_CVE_DIR = re.compile(r"^CVE-\d{4}-\d{3,7}$", re.I)
RANK = 15


class VulhubCollector:
    name = "vulhub"

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).resolve()

    def available(self) -> bool:
        return self.root.is_dir()

    def collect(self) -> Iterator[NormalizedPoc]:
        for d in self.root.rglob("CVE-*"):
            if not d.is_dir() or not _CVE_DIR.match(d.name):
                continue
            cve = d.name.upper()
            app = d.parent.name
            compose = next((p for p in (d / "docker-compose.yml", d / "compose.yml") if p.is_file()), None)
            rel = str((compose or d).relative_to(self.root))
            title = f"{app} — {cve} vulnerable lab"
            readme = d / "README.md"
            if readme.is_file():
                for line in readme.read_text(encoding="utf-8", errors="replace").splitlines():
                    if line.startswith("# "):
                        title = line[2:].strip()[:160]; break
            yield NormalizedPoc(
                source="vulhub", source_ref=f"{app}/{cve}", kind="lab", title=title,
                cve_ids=[cve], description=f"Vulhub reproducible lab for {cve} ({app}).",
                platform="docker", language="yaml", verified=True, rank=RANK,
                url=f"https://github.com/vulhub/vulhub/tree/master/{d.relative_to(self.root)}",
                license="Vulhub (MIT); reference only — never executed", redistributable=False,
                artifact_sha256=None, artifact_path=rel if compose else None,
                date_published=None, provenance={"collector": "vulhub", "app": app})
