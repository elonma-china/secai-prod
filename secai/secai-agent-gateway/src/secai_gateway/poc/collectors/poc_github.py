"""PoC-in-GitHub collector — CVE→GitHub-repo index (nomi-sec/PoC-in-GitHub).

Link-only at collect time (biggest coverage, but untrusted). Top-N repos per CVE by stars.
Artifact bytes are NOT here — a SANDBOXED fetcher (fetch_github.py) pre-downloads a bounded
subset into the content store so serving stays offline. `redistributable=False`.
"""
from __future__ import annotations

import json
import re
from collections.abc import Iterator
from pathlib import Path

from ..models import NormalizedPoc
from .github_kind import classify_github_poc_kind

_CVE = re.compile(r"CVE-\d{4}-\d{3,7}", re.I)
RANK = 5  # lowest tier — unverified, community


class PocGithubCollector:
    name = "poc_github"

    def __init__(self, root: str | Path, top_n: int = 5) -> None:
        self.root = Path(root).resolve()
        self.top_n = top_n

    def available(self) -> bool:
        return self.root.is_dir()

    def collect(self) -> Iterator[NormalizedPoc]:
        for jf in self.root.rglob("CVE-*.json"):
            m = _CVE.search(jf.stem)
            if not m:
                continue
            cve = m.group(0).upper()
            try:
                repos = json.loads(jf.read_text(encoding="utf-8", errors="replace"))
            except Exception:
                continue
            if not isinstance(repos, list):
                continue
            repos = sorted(repos, key=lambda r: r.get("stargazers_count") or 0, reverse=True)[: self.top_n]
            for r in repos:
                full = r.get("full_name") or r.get("name")
                if not full:
                    continue
                stars = r.get("stargazers_count") or 0
                title = str(r.get("name") or full)[:200]
                description = str(r.get("description") or "")[:500]
                yield NormalizedPoc(
                    source="poc_github", source_ref=str(full),
                    kind=classify_github_poc_kind(title, description, str(full)),
                    title=title, cve_ids=[cve],
                    description=description,
                    platform=None, language=None, verified=False,
                    rank=RANK + (1 if stars >= 50 else 0),
                    url=str(r.get("html_url") or f"https://github.com/{full}"),
                    license="varies (upstream repo) — link only until fetched", redistributable=False,
                    artifact_sha256=None, artifact_path=None,  # filled by the sandbox fetcher
                    date_published=r.get("created_at"),
                    provenance={"collector": "poc_github", "repo": full, "stars": stars,
                                "default_branch": r.get("default_branch") or "master"})
