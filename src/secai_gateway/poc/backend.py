"""Catalog-backed PoC backend — the 2-tool (search + fetch) contract with opaque,
run-scoped capability refs. Swapping/adding sources happens here, behind the tool contract.

Safety invariants (all sources): read-only; never executes an artifact; ref cannot be
fabricated or reused across runs (so the model can't fetch what it didn't search); real
source code is only ever returned for sources with a confirmed-redistributable license
(exploitdb/metasploit/nuclei/ghsa — see each collector's `redistributable` flag) and always
grounded in an actual fetched artifact, never model-invented; unresolved-license sources
(raw GitHub PoCs, vulhub) get static-derived evidence only, no code."""
from __future__ import annotations

import asyncio
import hashlib
import shutil
import uuid
from collections import OrderedDict
from pathlib import Path
from typing import Any

from ..config import get_settings
from .catalog import PocCatalog
from .models import FetchResult, SearchHit

_MAX_REFS = 20000  # cap the run-scoped ref map (evict oldest)
_MAX_CODE_CHARS = 8000  # ~2k tokens; files are already condensed to one picked PoC file
# Hard cap on poc_fetch calls per run — enforced HERE, not left to the system prompt. A
# small model asked to "fetch 1-2 PoCs" will happily fetch every hit for a well-covered CVE
# (Log4Shell alone has 64) if nothing stops it; prompt wording is advisory, this is not.
_MAX_FETCH_PER_RUN = 3


class PocError(ValueError):
    pass


import re as _re

_URL = _re.compile(r"https?://[^\s\"'<>)\]]+")
_IP = _re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
_PORT = _re.compile(r"(?:port|LPORT|RPORT|PORT)\s*[=:]\s*(\d{1,5})")
_IMPORT = _re.compile(r"^\s*(?:import|from|require|use|#include|using|package)\s+[^\n;{]+", _re.M)
_CVE = _re.compile(r"CVE-\d{4}-\d{3,7}", _re.I)
_DEDUP = lambda xs, n: list(dict.fromkeys(xs))[:n]  # noqa: E731


def _static_indicators(language: str, path: Path) -> dict[str, Any]:
    """Condensed but INFORMATIVE static evidence of what the PoC does — never the source."""
    text = path.read_text(encoding="utf-8", errors="replace")
    low = text.lower()
    return {
        "language": language or path.suffix.lstrip(".") or "unknown",
        "loc": text.count("\n") + 1,
        "imports": [m.strip()[:100] for m in _IMPORT.findall(text)[:15]],
        "network": {
            "urls": _DEDUP(_URL.findall(text), 8),
            "ips": _DEDUP(_IP.findall(text), 8),
            "ports": _DEDUP(_PORT.findall(text), 8),
            "socket_or_http": sum(low.count(x) for x in ("socket", "requests.", "urllib", "http")),
        },
        "process_exec": sum(low.count(x) for x in ("subprocess", "os.system", "popen", "exec(", "eval(", "system(")),
        "writes_files": any(x in low for x in ("open(", "write(", "fopen", "with open")),
        "cve_refs": _DEDUP([c.upper() for c in _CVE.findall(text)], 5),
    }


class LocalPocBackend:
    """In-process, catalog-backed. `search`/`fetch` are async (file/db work off-loaded)."""

    def __init__(self, catalog: PocCatalog | None = None, source_roots: dict[str, Path] | None = None) -> None:
        s = get_settings()
        self.catalog = catalog or PocCatalog(s.poc_catalog_db)
        src = Path(s.poc_sources_root)
        self.roots = source_roots or {
            "exploitdb": Path(s.exploitdb_root).resolve(),
            "metasploit": (src / "metasploit-framework").resolve(),
            "nuclei": (src / "nuclei-templates").resolve(),
            "vulhub": (src / "vulhub").resolve(),
            "ghsa": (src / "advisory-database").resolve(),
            "poc_github": Path(s.poc_artifact_store).resolve(),  # sandbox-fetched artifacts
        }
        self._refs: "OrderedDict[str, tuple[str, int]]" = OrderedDict()  # ref -> (run_id, poc_id)
        self._fetch_counts: "OrderedDict[str, int]" = OrderedDict()  # run_id -> fetch() calls so far

    # ── ref lifecycle ───────────────────────────────────────────────────────
    def _issue(self, run_id: str, poc_id: int) -> str:
        ref = f"poc:{uuid.uuid4().hex}"
        self._refs[ref] = (run_id or "", poc_id)
        while len(self._refs) > _MAX_REFS:
            self._refs.popitem(last=False)
        return ref

    def _resolve_ref(self, ref: str, run_id: str) -> int:
        entry = self._refs.get((ref or "").strip())
        if entry is None:
            raise PocError("Unknown ref — call poc_search first and pass a ref it returned")
        issued_run, poc_id = entry
        if issued_run and run_id and issued_run != run_id:
            raise PocError("This ref belongs to a different session — call poc_search in this session")
        return poc_id

    def _artifact_path(self, poc: dict[str, Any]) -> Path:
        root = self.roots.get(poc["source"])
        if root is None or not poc.get("artifact_path"):
            raise PocError(f"No local artifact for source '{poc['source']}'")
        candidate = (root / poc["artifact_path"]).resolve()
        try:
            candidate.relative_to(root)   # path-escape guard
        except ValueError as exc:
            raise PocError("Artifact path escapes source root") from exc
        if not candidate.is_file():
            raise PocError("Artifact not found on disk")
        return candidate

    # ── tool ops ────────────────────────────────────────────────────────────
    async def search(self, query: str, max_results: int, run_id: str) -> dict[str, Any]:
        rows = await asyncio.to_thread(self.catalog.search, query, max_results)
        hits = []
        for r in rows:
            ref = self._issue(run_id, r["id"])
            hits.append(SearchHit(
                ref=ref, title=r["title"], source=r["source"], kind=r["kind"],
                cve_ids=r["cve_ids"], verified=bool(r["verified"]), url=r["url"] or "",
                platform=r.get("platform")).to_dict())
        return {"hits": hits}

    def _check_fetch_budget(self, run_id: str) -> None:
        key = run_id or ""
        n = self._fetch_counts.get(key, 0)
        if n >= _MAX_FETCH_PER_RUN:
            raise PocError(
                f"poc_fetch limit reached ({_MAX_FETCH_PER_RUN} per run) — stop fetching and "
                f"write the report from the evidence already gathered.")
        self._fetch_counts[key] = n + 1
        while len(self._fetch_counts) > _MAX_REFS:
            self._fetch_counts.popitem(last=False)

    async def fetch(self, ref: str, run_id: str) -> dict[str, Any]:
        self._check_fetch_budget(run_id)
        poc_id = self._resolve_ref(ref, run_id)
        poc = await asyncio.to_thread(self.catalog.get, poc_id)
        if poc is None:
            raise PocError("Catalog entry not found")
        # Metadata-only sources (GHSA advisory; GitHub PoC not yet sandbox-fetched): no local
        # bytes to analyze offline → return metadata + a clear note, not an error.
        meta = dict(kind=poc["kind"], title=poc["title"],
                    description=(poc.get("description") or "")[:2000], cve_ids=poc["cve_ids"])
        root = self.roots.get(poc["source"])
        if not poc.get("artifact_path") or root is None:
            return FetchResult(
                source_ref=poc["source_ref"], source=poc["source"], sha256="",
                url=poc["url"] or "", license=poc["license"] or "", **meta,
                static_indicators={"note": "no local artifact — metadata only (offline)"},
                prerequisites=[], side_effects=["No artifact fetched; metadata only"],
                candidate_status="metadata_only").to_dict()
        path = self._artifact_path(poc)
        blob = await asyncio.to_thread(path.read_bytes)
        indicators = await asyncio.to_thread(_static_indicators, poc.get("language") or "", path)
        code, truncated = None, False
        if poc.get("redistributable"):
            # Confirmed-redistributable license (exploitdb/metasploit/nuclei/ghsa) → return the
            # REAL fetched artifact text, grounded and sha256-attributable. Unresolved-license
            # sources (raw GitHub PoCs, vulhub) fall through with code=None — static_indicators
            # only, same as before. Never model-generated either way.
            text = blob.decode("utf-8", errors="replace")
            truncated = len(text) > _MAX_CODE_CHARS
            code = text[:_MAX_CODE_CHARS]
        return FetchResult(
            source_ref=poc["source_ref"], source=poc["source"],
            sha256=hashlib.sha256(blob).hexdigest(), url=poc["url"] or "",
            license=poc["license"] or "", **meta, static_indicators=indicators,
            prerequisites=["target access", "matching vulnerable version", "authorized lab"],
            side_effects=["Artifact was not executed; static inspection only"],
            source_code=code, code_truncated=truncated,
        ).to_dict()

    async def health(self) -> tuple[bool, str | None]:
        try:
            n = await asyncio.to_thread(self.catalog.count)
        except Exception:
            return False, None
        edb = self.roots.get("exploitdb")
        data_ok = bool(edb and (edb / "files_exploits.csv").is_file())
        return (n > 0 and data_ok), f"catalog:{n}"

    # ── one-shot / cron: (re)build the catalog from enabled collectors ────────
    def rebuild(self, sources: list[str] | None = None) -> dict[str, Any]:
        from .collectors import (ExploitDbCollector, GhsaCollector, MetasploitCollector,
                                 NucleiCollector, PocGithubCollector, TrickestCollector,
                                 VulhubCollector)
        src = Path(get_settings().poc_sources_root)
        cols = {
            "exploitdb": ExploitDbCollector(self.roots["exploitdb"]),
            "metasploit": MetasploitCollector(self.roots["metasploit"]),
            "nuclei": NucleiCollector(self.roots["nuclei"]),
            "vulhub": VulhubCollector(self.roots["vulhub"]),
            "ghsa": GhsaCollector(src / "advisory-database"),
            "poc_github": PocGithubCollector(src / "PoC-in-GitHub"),
            "trickest": TrickestCollector(src / "trickest-cve"),
        }
        counts: dict[str, Any] = {}
        for name, col in cols.items():
            if sources and name not in sources:
                continue
            if not col.available():
                counts[name] = "unavailable"
                continue
            n = 0
            for poc in col.collect():
                self.catalog.upsert(poc); n += 1
                if n % 5000 == 0:
                    self.catalog.commit()
            self.catalog.commit()
            self.catalog.log_run(name, "collect", added=n, updated=0, errors=0)
            counts[name] = n
        return counts
