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
# Code budget shared by the model-facing quote AND the deterministic reference-code
# injection (which reads source_code back from the ledger — see poc/inject.py). Raised
# from 2k→6k tokens so a typical single-file PoC comes back COMPLETE, not cut mid-exploit:
# the old 8k-char cap lost >half of an 18 KB PoC, which is what made output "lúc đủ lúc
# thiếu". Big artifacts still cap here to bound context/latency on the public endpoint.
_MAX_CODE_CHARS = 24000   # ~6k tokens; single-file PoC / entry file
# Multi-file PoCs (repo-style sources): the SAME total budget shared across the file set,
# entry file first, so a repo can't blow the window; per-file cap stops one giant file from
# eating the whole pool.
_MAX_SET_FILES = 12
_MAX_SET_CHARS = _MAX_CODE_CHARS
_MAX_SET_FILE_CHARS = 12000
# Hard cap on poc_fetch calls per run — enforced HERE, not left to the system prompt. A
# small model asked to "fetch 1-2 PoCs" will happily fetch every hit for a well-covered CVE
# (Log4Shell alone has 64) if nothing stops it; prompt wording is advisory, this is not.
# Matches exploit.md's "tối đa hai kết quả tốt nhất" — every extra fetch adds a full
# sequential LLM round-trip plus another _MAX_CODE_CHARS of quoted source in the context,
# both of which extend wall-clock latency on the public exploit endpoint.
_MAX_FETCH_PER_RUN = 2
# Full-artifact serving (/poc/file) is DELIBERATELY not bound by the LLM-context budgets
# above — its whole purpose is to return the complete file the model only saw a slice of.
# This ceiling is a pathological-size backstop only (PoC files are KB-range); it is not the
# "download limit" that truncates Section 1.
_MAX_FULL_CHARS = 20_000_000


class PocError(ValueError):
    pass


# Typed variants so the HTTP layer can map to the right status without string-sniffing.
class PocBadRequest(PocError):
    pass


class PocForbidden(PocError):
    pass


class PocNotFound(PocError):
    pass


import re as _re

_SHA256 = _re.compile(r"[0-9a-f]{64}")
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
            # Evidence-grounded fetches (secai-data/poc/_ground_cve_update_evidence.py) write
            # into this SAME content-addressed store, just tagged source='vendor' at the
            # catalog level — without this entry, fetch()'s early "no local artifact"
            # metadata_only path fires before `_allow_code` is ever consulted, since it looks
            # up the root by source name first.
            "vendor": Path(s.poc_artifact_store).resolve(),
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

    def _read_file_set(self, source: str, files: list[dict[str, Any]]) -> list[dict[str, Any]] | None:
        """Read a poc's attached file set from the content store into the source_files
        contract, entry-first, under the shared total budget. Path-escape guarded. Returns
        None when nothing readable (falls back to single-file source_code)."""
        root = self.roots.get(source) or self.roots.get("poc_github")
        if root is None:
            return None
        out: list[dict[str, Any]] = []
        total = 0
        for f in files[:_MAX_SET_FILES]:
            if total >= _MAX_SET_CHARS:
                break
            candidate = self._locate(root, f["storage_path"])
            if candidate is None:
                continue
            text = candidate.read_bytes().decode("utf-8", errors="replace")
            cap = min(_MAX_SET_FILE_CHARS, _MAX_SET_CHARS - total)
            truncated = len(text) > cap
            code = text[:cap]
            total += len(code)
            out.append({
                "path": f["rel_path"], "code": code, "sha256": f["sha256"],
                "language": f.get("language"), "truncated": truncated,
                "role": f.get("role"),
            })
        return out or None

    def _companion_doc_path(self, source: str, artifact_path: str) -> str | None:
        """Repo-relative path to a real "how to run" doc alongside the main artifact, per
        source convention — or None if that source has no such convention. Existence is
        checked by the caller; this is pure path derivation, no I/O.

        - metasploit: modules/exploits/X/Y/name.rb -> documentation/modules/exploit/X/Y/name.md
          (~43% of modules have one — Verification Steps with real `use`/`run` commands and
          option values, richer than anything in the .rb source itself).
        - vulhub: README.md next to the docker-compose.yml — the real step-by-step HTTP
          exploit walkthrough; the collector currently reads it only for a title string and
          discards the body, so this is the only place that content reaches the model."""
        if source == "metasploit" and artifact_path.startswith("modules/exploits/"):
            rest = artifact_path[len("modules/exploits/"):]
            if rest.endswith(".rb"):
                return f"documentation/modules/exploit/{rest[:-3]}.md"
        if source == "vulhub":
            return str(Path(artifact_path).parent / "README.md")
        return None

    def _locate(self, root: Path | None, rel_path: str | None) -> Path | None:
        """Resolve ``rel_path`` under ``root``, path-escape-guarded, falling back to the
        content-addressed store (``roots["poc_github"]``, same physical dir as
        ``roots["vendor"]``) when it's not under the source's own root.

        Grounding a poc row with real fetched bytes (``_ground_cve_update_evidence.py``)
        writes into that ONE shared store regardless of which `source` the row already
        carried — e.g. a CVE with no prior ``vendor`` row canonicalizes onto its existing
        ``ghsa`` row and gets `artifact_path` set there, but `roots["ghsa"]` points at the
        advisory-database mirror, not the store. Without this fallback the artifact silently
        "isn't found on disk" even though `_allow_code` correctly permits it (observed live:
        9/44 grounded CVEs landed on a pre-existing ghsa/poc_github row and fell through to
        T2 synthesis instead of T1 real code purely because of this path mismatch). A sha256-
        addressed relative path (``xx/xxxxx…``) can't collide with an unrelated real file
        under a different root, so trying a second root is safe."""
        if not rel_path:
            return None
        candidates = [root] if root is not None else []
        store = self.roots.get("poc_github")
        if store is not None and store != root:
            candidates.append(store)
        for r in candidates:
            candidate = (r / rel_path).resolve()
            try:
                candidate.relative_to(r)   # path-escape guard
            except ValueError:
                continue
            if candidate.is_file():
                return candidate
        return None

    def _artifact_path(self, poc: dict[str, Any]) -> Path:
        if not poc.get("artifact_path"):
            raise PocError(f"No local artifact for source '{poc['source']}'")
        found = self._locate(self.roots.get(poc["source"]), poc.get("artifact_path"))
        if found is None:
            raise PocError("Artifact not found on disk")
        return found

    def _allow_code(self, poc: dict[str, Any]) -> bool:
        """The SINGLE license/verification gate deciding whether real PoC source may leave
        the box — shared by ``fetch`` (LLM grounding) and the ``/poc/file`` full-artifact
        endpoint so both honor identical rules:
        - redistributable sources (exploitdb/metasploit/nuclei/ghsa) always
        - sandbox-fetched GitHub/vulhub/vendor artifacts (private SecAI lab) only when
          verified or when an artifact was explicitly stored for offline retrieval.
          ``vendor`` covers the same-trust-class case of a poc row whose bytes were fetched
          directly from the CVE's OWN Evidence link (commit diff, GitHub blob, PoC repo,
          researcher-hosted PoC file) by ``secai-data/poc/_ground_cve_update_evidence.py`` —
          identical fetch/verify contract to poc_github's sandbox fetcher, just a different
          collector tag. Only rows THIS grounding pass touched carry both artifact_path AND
          verified=1 on a vendor row; a bare catalog placeholder never does."""
        return bool(poc.get("redistributable")) or (
            poc.get("source") in {"poc_github", "vulhub", "vendor"} and bool(poc.get("artifact_path"))
            and (bool(poc.get("verified")) or bool(poc.get("artifact_sha256")))
        )

    async def read_artifact(self, sha256: str) -> dict[str, Any]:
        """Full, UNTRUNCATED bytes of one stored PoC file, addressed by content hash. Used to
        embed the complete script into the /ai/exploit response (server-side, AFTER the model
        runs) so it NEVER enters the model context (no 24k budget), yet is gated by the SAME
        ``_allow_code`` rule and path-escape guard as poc_fetch."""
        sha = (sha256 or "").strip().lower()
        if not _SHA256.fullmatch(sha):
            raise PocBadRequest("Invalid sha256")
        # A hash lives either in a multi-file set (poc_file) or as a poc's own artifact.
        frow = await asyncio.to_thread(self.catalog.file_by_sha256, sha)
        if frow:
            poc = await asyncio.to_thread(self.catalog.get, frow["poc_id"])
            if poc is None:
                raise PocNotFound("Artifact owner not found")
            root = self.roots.get(poc["source"]) or self.roots.get("poc_github")
            rel_display, storage, language = frow["rel_path"], frow["storage_path"], frow.get("language")
        else:
            poc = await asyncio.to_thread(self.catalog.poc_by_artifact_sha256, sha)
            if poc is None:
                raise PocNotFound("Artifact not found")
            root = self.roots.get(poc["source"])
            rel_display = poc.get("source_ref") or poc["artifact_path"]
            storage, language = poc["artifact_path"], poc.get("language")
        if root is None:
            raise PocNotFound("No local root for source")
        if not self._allow_code(poc):
            raise PocForbidden("Artifact source is not cleared for full retrieval")
        candidate = self._locate(root, storage)
        if candidate is None:
            raise PocNotFound("Artifact not found on disk")
        blob = await asyncio.to_thread(candidate.read_bytes)
        text = blob.decode("utf-8", errors="replace")
        return {
            "sha256": sha, "path": rel_display, "language": language, "size": len(blob),
            "content": text[:_MAX_FULL_CHARS], "truncated": len(text) > _MAX_FULL_CHARS,
            "source": poc["source"], "poc_id": poc["id"], "cve_ids": poc.get("cve_ids") or [],
        }

    async def read_full_fileset(self, sha256: str) -> list[dict[str, Any]] | None:
        """The COMPLETE file set — ALL files of the PoC that owns the file with this content
        hash, each with full untruncated bytes, entry-first — for rendering the whole repo in
        Section 1 rather than the budget-limited subset the model fetched. Returns the
        source_files contract ({path, code, sha256, language, role, truncated}); None when
        ``sha256`` is not a stored multi-file member. Same license gate + path-escape guard as
        read_artifact."""
        sha = (sha256 or "").strip().lower()
        if not _SHA256.fullmatch(sha):
            return None
        frow = await asyncio.to_thread(self.catalog.file_by_sha256, sha)
        if not frow:
            return None
        poc = await asyncio.to_thread(self.catalog.get, frow["poc_id"])
        if poc is None or not self._allow_code(poc):
            return None
        root = self.roots.get(poc["source"]) or self.roots.get("poc_github")
        if root is None:
            return None
        rows = await asyncio.to_thread(self.catalog.get_files, frow["poc_id"])
        out: list[dict[str, Any]] = []
        for f in rows:
            candidate = self._locate(root, f["storage_path"])
            if candidate is None:
                continue
            blob = await asyncio.to_thread(candidate.read_bytes)
            out.append({"path": f["rel_path"], "code": blob.decode("utf-8", errors="replace"),
                        "sha256": f["sha256"], "language": f.get("language"),
                        "role": f.get("role"), "truncated": False})
        return out or None

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
        allow_code = self._allow_code(poc)
        source_files = None
        companion_doc_only = False
        if allow_code:
            text = blob.decode("utf-8", errors="replace")
            truncated = len(text) > _MAX_CODE_CHARS
            code = text[:_MAX_CODE_CHARS]
            # Repo-style PoC with an attached file set → return the whole bounded set
            # (hierarchy preserved) instead of just the picked entry file.
            file_rows = await asyncio.to_thread(self.catalog.get_files, poc_id)
            if file_rows:
                source_files = await asyncio.to_thread(self._read_file_set, poc["source"], file_rows)
            else:
                # Single-artifact sources (metasploit/vulhub) may still have a real
                # companion "how to run" doc on disk — attach it dynamically (no catalog
                # row needed) rather than leaving the model with only the raw module/compose
                # file, which rarely states how to actually invoke the PoC.
                doc_rel = self._companion_doc_path(poc["source"], poc["artifact_path"])
                doc_path = (root / doc_rel).resolve() if doc_rel else None
                if doc_path is not None and doc_path.is_file():
                    try:
                        doc_path.relative_to(root)  # path-escape guard, mirrors _artifact_path
                        doc_bytes = await asyncio.to_thread(doc_path.read_bytes)
                        doc_text = doc_bytes[:_MAX_SET_FILE_CHARS].decode("utf-8", errors="replace")
                        source_files = [
                            {"path": poc["artifact_path"], "code": code,
                             "sha256": hashlib.sha256(blob).hexdigest(),
                             "language": poc.get("language"), "truncated": truncated, "role": "entry"},
                            {"path": doc_rel, "code": doc_text, "sha256": hashlib.sha256(doc_bytes).hexdigest(),
                             "language": "md", "truncated": len(doc_bytes) > _MAX_SET_FILE_CHARS, "role": "readme"},
                        ]
                        companion_doc_only = True
                    except ValueError:
                        pass  # escaped root — ignore, main artifact still returned via source_code
        return FetchResult(
            source_ref=poc["source_ref"], source=poc["source"],
            sha256=hashlib.sha256(blob).hexdigest(), url=poc["url"] or "",
            license=poc["license"] or "", **meta, static_indicators=indicators,
            prerequisites=["target access", "matching vulnerable version", "authorized lab"],
            side_effects=["Artifact was not executed; static inspection only"],
            source_code=code, code_truncated=truncated, source_files=source_files,
            companion_doc_only=companion_doc_only,
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
