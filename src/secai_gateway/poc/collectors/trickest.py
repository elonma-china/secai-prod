"""Trickest CVE collector — trickest/cve (~163k CVE markdown files, each with a POC section
of GitHub repo links). Far broader than nomi-sec. Yields source='poc_github' so repos dedup
with the nomi-sec collector via UNIQUE(source, source_ref); new repos add coverage.

No star counts in the markdown → provenance stars=0 (searchable, not auto-prioritised for
the sandbox fetch; nomi-sec's rows keep their stars since upsert doesn't overwrite provenance).
"""
from __future__ import annotations

import re
from collections.abc import Iterator
from pathlib import Path

from ..models import NormalizedPoc

_CVE = re.compile(r"CVE-\d{4}-\d{3,7}", re.I)
_GH = re.compile(r"github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)")
_SKIP_OWNER = {"advisories", "sponsors", "topics", "collections", "features"}
RANK = 5


class TrickestCollector:
    name = "trickest"

    def __init__(self, root: str | Path, per_cve: int = 10) -> None:
        self.root = Path(root).resolve()
        self.per_cve = per_cve

    def available(self) -> bool:
        return self.root.is_dir()

    def collect(self) -> Iterator[NormalizedPoc]:
        for md in self.root.rglob("CVE-*.md"):
            m = _CVE.search(md.stem)
            if not m:
                continue
            cve = m.group(0).upper()
            try:
                text = md.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue
            seen: set[str] = set()
            for owner, repo in _GH.findall(text):
                repo = repo[:-4] if repo.endswith(".git") else repo
                full = f"{owner}/{repo}"
                if owner.lower() in _SKIP_OWNER or full in seen:
                    continue
                seen.add(full)
                if len(seen) > self.per_cve:
                    break
                yield NormalizedPoc(
                    source="poc_github", source_ref=full, kind="exploit",
                    title=repo[:200], cve_ids=[cve], description="",
                    platform=None, language=None, verified=False, rank=RANK,
                    url=f"https://github.com/{full}",
                    license="varies (upstream repo) — link only until fetched", redistributable=False,
                    artifact_sha256=None, artifact_path=None,
                    date_published=None,
                    provenance={"collector": "trickest", "repo": full, "stars": 0,
                                "default_branch": "master"})
