from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class NormalizedPoc:
    """What a collector yields; the catalog upserts it. `artifact_path` is a resolvable
    path to the bytes (used ONLY for static analysis at fetch time — never returned)."""
    source: str                       # exploitdb|metasploit|nuclei|vulhub|poc_github|vendor
    source_ref: str                   # native id (EDB id, msf fullname, template id, repo url)
    kind: str                         # exploit|detection|lab|advisory
    title: str
    cve_ids: list[str] = field(default_factory=list)
    description: str = ""
    platform: str | None = None
    language: str | None = None
    verified: bool = False
    rank: int = 0
    url: str = ""
    license: str = ""
    redistributable: bool = False
    artifact_sha256: str | None = None
    artifact_path: str | None = None
    date_published: str | None = None
    provenance: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class SearchHit:
    """A poc_search result the model sees. `ref` is an opaque capability token — the model
    passes it to poc_fetch; it can never be fabricated or reused across runs. No raw path."""
    ref: str
    title: str
    source: str
    kind: str
    cve_ids: list[str]
    verified: bool
    url: str
    platform: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class FetchResult:
    """poc_fetch output — folds the old fetch+analyze+draft into one. ``source_code`` is the
    real, grounded artifact text — populated ONLY when the source's license is confirmed
    redistributable for internal security research (exploitdb/metasploit/nuclei/ghsa); for
    unresolved-license sources (raw GitHub PoCs, vulhub) it stays ``None`` and only
    ``static_indicators`` (never fabricated) is available. The model must never invent code
    to fill this gap — see prompts/exploit.md."""
    source_ref: str
    source: str
    sha256: str
    url: str
    license: str
    kind: str
    title: str
    description: str
    cve_ids: list[str]
    static_indicators: dict[str, Any]
    prerequisites: list[str]
    side_effects: list[str]
    candidate_status: str = "draft_not_executed"
    source_code: str | None = None
    code_truncated: bool = False
    # Multi-file PoCs — the bounded, hierarchy-preserving file set. Each entry: {path,
    # code, sha256, language, truncated, role}. ``None`` for single-file PoCs, where
    # ``source_code`` alone carries the artifact. The entry (main) file is first;
    # ``source_code`` mirrors it for back-compat.
    source_files: list[dict[str, Any]] | None = None
    # True when `source_files` is an entry file + a companion doc bolted on at fetch time
    # (metasploit's documentation/*.md, vulhub's README.md — see backend.py's
    # `_companion_doc_path`) — both files are two specific, disparate paths plucked from
    # ONE HUGE shared external repo (rapid7/metasploit-framework, vulhub/vulhub), not a
    # small repo dedicated to this PoC. False for poc_github's `_pick_set`-built sets,
    # which genuinely are one small repo for this PoC. Controls whether poc/inject.py's
    # render_files shows a "Cấu trúc PoC (dạng repo)" tree — doing so for the companion
    # case would misleadingly imply that's the PoC's whole project structure.
    companion_doc_only: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
