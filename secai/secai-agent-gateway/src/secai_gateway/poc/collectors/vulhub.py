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
            readme_text = ""
            if readme.is_file():
                readme_text = readme.read_text(encoding="utf-8", errors="replace")
                for line in readme_text.splitlines():
                    if line.startswith("# "):
                        title = line[2:].strip()[:160]; break
            # No compose file to list as an artifact: the README body is the only real
            # evidence left for this CVE (reproduction steps, affected versions) — surface
            # it as `description` instead of discarding it, so a code-less catalog hit still
            # carries real text for the synthesize-from-evidence tier (poc/inject.py) to use.
            description = f"Vulhub reproducible lab for {cve} ({app})."
            if not compose and readme_text.strip():
                description = readme_text.strip()[:2000]
            yield NormalizedPoc(
                source="vulhub", source_ref=f"{app}/{cve}", kind="lab", title=title,
                cve_ids=[cve], description=description,
                platform="docker", language="yaml", verified=True, rank=RANK,
                url=f"https://github.com/vulhub/vulhub/tree/master/{d.relative_to(self.root)}",
                license="Vulhub (MIT); reference only — never executed", redistributable=False,
                artifact_sha256=None, artifact_path=rel if compose else None,
                date_published=None, provenance={"collector": "vulhub", "app": app})
