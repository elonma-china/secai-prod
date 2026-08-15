"""Unified PoC catalog — normalized multi-source store (SQLite + FTS5).

Sources (exploitdb/metasploit/nuclei/poc_github/vulhub/vendor) are collected into one
`poc` table (many-to-many with CVEs), deduped, ranked. The backend queries this; adding a
source never changes the tool contract. See docs/unified-poc-db-design.md.
"""
from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path
from typing import Any

from .models import NormalizedPoc
from .normalize import normalize_cve_ids

_CVE = re.compile(r"CVE-\d{4}-\d{3,7}", re.I)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS poc (
  id INTEGER PRIMARY KEY, source TEXT NOT NULL, source_ref TEXT NOT NULL,
  kind TEXT NOT NULL, title TEXT NOT NULL, description TEXT,
  platform TEXT, language TEXT, verified INTEGER DEFAULT 0, rank INTEGER DEFAULT 0,
  url TEXT, license TEXT, redistributable INTEGER DEFAULT 0,
  artifact_sha256 TEXT, artifact_path TEXT,
  date_published TEXT, date_collected TEXT, provenance TEXT,
  -- NULL = canonical/standalone; non-NULL = a duplicate (fork-family or same content
  -- hash) pointing at the canonical poc.id. Duplicates are NEVER deleted (provenance is
  -- kept so the heuristic can be re-tuned); search/pending_github filter them out.
  duplicate_of INTEGER REFERENCES poc(id),
  UNIQUE(source, source_ref)
);
-- `poc_by_artifact_sha256` (full-artifact /poc/file serving, and read_artifact's
-- single-file fallback) filters on artifact_sha256 with no other equality predicate SQLite
-- can use — without this it falls back to idx_poc_dupof (duplicate_of IS NULL, true for
-- most rows) and linearly scans the match, ~50ms at 121k rows. Partial (WHERE NOT NULL):
-- the column is NULL for the ~half of rows never fetched, and never queried as NULL.
CREATE INDEX IF NOT EXISTS idx_poc_artifact_sha ON poc(artifact_sha256) WHERE artifact_sha256 IS NOT NULL;
CREATE TABLE IF NOT EXISTS poc_cve (poc_id INTEGER, cve_id TEXT, PRIMARY KEY(poc_id, cve_id));
CREATE INDEX IF NOT EXISTS idx_poc_cve_cve ON poc_cve(cve_id);
CREATE TABLE IF NOT EXISTS artifact (
  sha256 TEXT PRIMARY KEY, size INTEGER, language TEXT, license TEXT,
  stored INTEGER DEFAULT 0, storage_path TEXT, first_source TEXT
);
-- Multi-file PoCs (repo-style sources): the bounded file set attached to a poc row,
-- one row per file, repo-relative `rel_path` preserving the directory hierarchy.
-- `ordinal` keeps entry-first order; single-file PoCs have no rows here (artifact_path
-- alone carries them). Repopulated wholesale by the sandbox fetcher (set_files).
CREATE TABLE IF NOT EXISTS poc_file (
  poc_id INTEGER NOT NULL, ordinal INTEGER NOT NULL, rel_path TEXT NOT NULL,
  sha256 TEXT NOT NULL, storage_path TEXT NOT NULL, language TEXT, size INTEGER, role TEXT,
  PRIMARY KEY(poc_id, rel_path)
);
CREATE INDEX IF NOT EXISTS idx_poc_file_poc ON poc_file(poc_id);
-- `file_by_sha256` (multi-file member lookup, same /poc/file path) filters on sha256 with
-- no index today — a real SCAN in EXPLAIN QUERY PLAN, cheap only because poc_file is still
-- small (~5k rows); grows with every multi-file PoC fetched, so index it before it isn't.
CREATE INDEX IF NOT EXISTS idx_poc_file_sha ON poc_file(sha256);
CREATE TABLE IF NOT EXISTS collector_run (
  id INTEGER PRIMARY KEY, source TEXT, started TEXT, finished TEXT,
  added INTEGER, updated INTEGER, errors INTEGER, note TEXT
);
CREATE VIRTUAL TABLE IF NOT EXISTS poc_fts USING fts5(title, description);
"""


class PocCatalog:
    def __init__(self, db_path: str | Path) -> None:
        self.path = Path(db_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(self.path), check_same_thread=False, timeout=30)
        self._db.row_factory = sqlite3.Row
        # WAL: concurrent readers (the serving gateway) + one writer (a rebuild) coexist;
        # busy_timeout retries instead of raising "database is locked" under contention.
        self._db.execute("PRAGMA busy_timeout=30000")
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA synchronous=NORMAL")
        self._db.executescript(_SCHEMA)
        # Lightweight migration for catalogs built before `duplicate_of` existed: the CREATE
        # TABLE above is IF NOT EXISTS, so a pre-existing `poc` table won't gain the column.
        # Fresh DBs already have it from _SCHEMA; this ALTER only backfills older ones.
        try:
            self._db.execute("ALTER TABLE poc ADD COLUMN duplicate_of INTEGER")
        except sqlite3.OperationalError:
            pass  # column already present
        self._db.commit()

    # ── ingest (collectors) ─────────────────────────────────────────────────
    def upsert(self, poc: NormalizedPoc) -> None:
        cur = self._db.execute(
            """INSERT INTO poc(source,source_ref,kind,title,description,platform,language,
                 verified,rank,url,license,redistributable,artifact_sha256,artifact_path,
                 date_published,date_collected,provenance)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,datetime('now'),?)
               ON CONFLICT(source,source_ref) DO UPDATE SET
                 title=excluded.title, description=excluded.description, rank=excluded.rank,
                 -- preserve a previously sandbox-fetched artifact when the collector re-yields
                 -- NULL (link-only sources) — re-collect must NOT wipe fetch work.
                 artifact_sha256=COALESCE(excluded.artifact_sha256, poc.artifact_sha256),
                 artifact_path=COALESCE(excluded.artifact_path, poc.artifact_path),
                 date_collected=excluded.date_collected
               RETURNING id""",
            (poc.source, poc.source_ref, poc.kind, poc.title, poc.description, poc.platform,
             poc.language, int(poc.verified), poc.rank, poc.url, poc.license,
             int(poc.redistributable), poc.artifact_sha256, poc.artifact_path,
             poc.date_published, json.dumps(poc.provenance or {})),
        )
        poc_id = cur.fetchone()[0]
        # standalone FTS5 (no external content) → manage rows by rowid: delete-then-insert
        # keeps re-collect idempotent (UPSERT isn't supported on FTS5 virtual tables).
        self._db.execute("DELETE FROM poc_fts WHERE rowid=?", (poc_id,))
        self._db.execute("INSERT INTO poc_fts(rowid,title,description) VALUES(?,?,?)",
                         (poc_id, poc.title, poc.description))
        for cve in {c.upper() for c in poc.cve_ids}:
            self._db.execute("INSERT OR IGNORE INTO poc_cve(poc_id,cve_id) VALUES(?,?)", (poc_id, cve))
        if poc.artifact_sha256:
            self._db.execute(
                "INSERT OR IGNORE INTO artifact(sha256,storage_path,stored,license,first_source) "
                "VALUES(?,?,?,?,?)",
                (poc.artifact_sha256, poc.artifact_path, int(bool(poc.artifact_path)),
                 poc.license, poc.source))

    def commit(self) -> None:
        self._db.commit()

    def log_run(self, source: str, started: str, added: int, updated: int, errors: int, note: str = "") -> None:
        self._db.execute(
            "INSERT INTO collector_run(source,started,finished,added,updated,errors,note) "
            "VALUES(?,?,datetime('now'),?,?,?,?)", (source, started, added, updated, errors, note))
        self._db.commit()

    def count(self) -> int:
        return self._db.execute("SELECT COUNT(*) FROM poc").fetchone()[0]

    # ── query (backend) ─────────────────────────────────────────────────────
    def _rows_to_dicts(self, rows: list[sqlite3.Row]) -> list[dict[str, Any]]:
        out = []
        for r in rows:
            cves = [x[0] for x in self._db.execute(
                "SELECT cve_id FROM poc_cve WHERE poc_id=? ORDER BY cve_id", (r["id"],)).fetchall()]
            d = dict(r); d["cve_ids"] = cves
            out.append(d)
        return out

    def search(self, query: str, limit: int = 10) -> list[dict[str, Any]]:
        # Repair separator/case typos ("cve 2026 42533") so a malformed id still reaches the
        # CVE-exact path below instead of degrading to weaker keyword FTS. Covers BOTH the
        # deterministic prefetch and the model's own poc_search — both land here.
        query = normalize_cve_ids((query or "").strip())
        if not query:
            return []
        limit = max(1, min(limit, 50))
        cves = {m.upper() for m in _CVE.findall(query)}
        if cves:  # CVE-exact path
            marks = ",".join("?" * len(cves))
            # Exploit PoCs before detection templates (nuclei YAML rank~20 + verified used
            # to bury GitHub/EDB exploits at rank~5). kind=exploit first, then rank/verified.
            rows = self._db.execute(
                f"""SELECT p.* FROM poc p JOIN poc_cve c ON c.poc_id=p.id
                    WHERE c.cve_id IN ({marks}) AND p.duplicate_of IS NULL
                    ORDER BY CASE WHEN p.kind = 'exploit' THEN 0 ELSE 1 END ASC,
                             -- Prefer GitHub PoCs within the exploit tier (requested): a repo
                             -- PoC is what the consolidation step merges into one runnable file.
                             CASE WHEN p.source = 'poc_github' THEN 0 ELSE 1 END ASC,
                             p.rank DESC, p.verified DESC LIMIT ?""",
                (*cves, limit)).fetchall()
            return self._rows_to_dicts(rows)
        # keyword FTS path (sanitize to a safe MATCH expression)
        terms = re.findall(r"[A-Za-z0-9_.\-]+", query)[:8]
        if not terms:
            return []
        match = " OR ".join(f'"{t}"' for t in terms)
        # Rank by TEXT RELEVANCE (FTS5 bm25 — more negative = better match; docs matching
        # more/rarer terms score higher) with a gentle source-tier boost, so the most
        # on-topic PoC surfaces first instead of ordering purely by source. Fetch a wider
        # candidate window then trim, so bm25 (not the LIMIT) decides the top hits.
        rows = self._db.execute(
            """SELECT p.* FROM poc p JOIN poc_fts f ON f.rowid=p.id
               WHERE poc_fts MATCH ? AND p.duplicate_of IS NULL
               ORDER BY bm25(poc_fts) - (p.rank * 0.15) ASC, p.verified DESC
               LIMIT ?""",
            (match, limit)).fetchall()
        return self._rows_to_dicts(rows)

    def get(self, poc_id: int) -> dict[str, Any] | None:
        # Fetches by exact id (used to resolve a search-issued ref). Duplicates are excluded
        # from search(), so a ref never points at a duplicate row — no redirect needed here.
        r = self._db.execute("SELECT * FROM poc WHERE id=?", (poc_id,)).fetchone()
        if not r:
            return None
        return self._rows_to_dicts([r])[0]

    # ── content-hash lookup (full-artifact serving) ─────────────────────────
    # Address a stored file by its sha256 so the /poc/file endpoint can serve the full,
    # untruncated bytes without ever going through a run-scoped `ref`. Two homes for a hash:
    # a multi-file set member (poc_file) or a single-file poc's own artifact.
    def file_by_sha256(self, sha256: str) -> dict[str, Any] | None:
        r = self._db.execute(
            "SELECT poc_id, rel_path, storage_path, language, size FROM poc_file "
            "WHERE sha256=? LIMIT 1", (sha256,)).fetchone()
        return dict(r) if r else None

    def poc_by_artifact_sha256(self, sha256: str) -> dict[str, Any] | None:
        r = self._db.execute(
            "SELECT * FROM poc WHERE artifact_sha256=? AND artifact_path IS NOT NULL "
            "AND duplicate_of IS NULL LIMIT 1", (sha256,)).fetchone()
        return self._rows_to_dicts([r])[0] if r else None

    # ── multi-file PoC set (repo-style sources) ─────────────────────────────
    def get_files(self, poc_id: int) -> list[dict[str, Any]]:
        """The attached file set for a poc, entry-first. Empty list = single-file PoC."""
        rows = self._db.execute(
            "SELECT rel_path, sha256, storage_path, language, size, role FROM poc_file "
            "WHERE poc_id=? ORDER BY ordinal", (poc_id,)).fetchall()
        return [dict(r) for r in rows]

    def set_files(self, poc_id: int, files: list[dict[str, Any]]) -> None:
        """Replace a poc's file set wholesale (idempotent re-fetch). Each `file`:
        {rel_path, sha256, storage_path, language?, size?, role?}. Also registers each
        blob in `artifact` so the content store stays the source of truth."""
        self._db.execute("DELETE FROM poc_file WHERE poc_id=?", (poc_id,))
        for i, f in enumerate(files):
            self._db.execute(
                "INSERT OR REPLACE INTO poc_file"
                "(poc_id,ordinal,rel_path,sha256,storage_path,language,size,role) "
                "VALUES(?,?,?,?,?,?,?,?)",
                (poc_id, i, f["rel_path"], f["sha256"], f["storage_path"],
                 f.get("language"), f.get("size"), f.get("role")))
            self._db.execute(
                "INSERT OR IGNORE INTO artifact(sha256,size,storage_path,stored,language,first_source) "
                "VALUES(?,?,?,1,?,'poc_github')",
                (f["sha256"], f.get("size"), f["storage_path"], f.get("language")))
        self._db.commit()

    # ── on-demand code acquisition (request path) ───────────────────────────
    def github_candidates(self, cve_id: str, limit: int = 3) -> list[dict[str, Any]]:
        """poc_github rows for ``cve_id`` worth fetching code from on the request path,
        highest-star first — link-only rows (no local artifact yet) BEFORE already-fetched
        ones, so the on-demand fetcher spends its budget on rows that would otherwise have no
        code at all. Duplicates excluded. Returns id/source_ref/url/provenance/artifact_path
        so the caller can derive owner/repo/branch and skip rows already served locally."""
        cve = (cve_id or "").strip().upper()
        if not cve:
            return []
        rows = self._db.execute(
            "SELECT p.id, p.source_ref, p.url, p.provenance, p.artifact_path "
            "FROM poc p JOIN poc_cve c ON c.poc_id=p.id "
            "WHERE c.cve_id=? AND p.source='poc_github' AND p.duplicate_of IS NULL "
            "AND (p.url IS NOT NULL AND p.url<>'') "
            "ORDER BY (p.artifact_path IS NOT NULL) ASC, "
            "         CAST(json_extract(p.provenance,'$.stars') AS INTEGER) DESC "
            "LIMIT ?",
            (cve, limit)).fetchall()
        return [dict(r) for r in rows]

    # ── sandbox fetcher support (link-only sources) ─────────────────────────
    def pending_github(self, limit: int, min_stars: int = 0, refetch: bool = False) -> list[sqlite3.Row]:
        """poc_github rows to fetch, highest-star first. Fork-family duplicates (marked by
        dedup_fork_families) are skipped so we don't spend the budget re-downloading the same
        PoC under a different owner.

        Default: rows not yet fetched (``artifact_path IS NULL``). ``refetch=True``: the
        *backfill* set — rows already fetched single-file that don't yet have a multi-file
        set and haven't been checked for one (``fileset_checked`` provenance flag, set by the
        fetcher once processed) — so a pre-multi-file catalog can be upgraded in place."""
        if refetch:
            return self._db.execute(
                "SELECT id, source_ref, url, provenance FROM poc "
                "WHERE source='poc_github' AND duplicate_of IS NULL AND artifact_path IS NOT NULL "
                "AND NOT EXISTS (SELECT 1 FROM poc_file f WHERE f.poc_id = poc.id) "
                "AND COALESCE(CAST(json_extract(provenance,'$.fileset_checked') AS INTEGER), 0) = 0 "
                "AND CAST(json_extract(provenance,'$.stars') AS INTEGER) >= ? "
                "ORDER BY CAST(json_extract(provenance,'$.stars') AS INTEGER) DESC LIMIT ?",
                (min_stars, limit)).fetchall()
        return self._db.execute(
            "SELECT id, source_ref, url, provenance FROM poc "
            "WHERE source='poc_github' AND artifact_path IS NULL AND duplicate_of IS NULL "
            "AND CAST(json_extract(provenance,'$.stars') AS INTEGER) >= ? "
            "ORDER BY CAST(json_extract(provenance,'$.stars') AS INTEGER) DESC LIMIT ?",
            (min_stars, limit)).fetchall()

    def set_artifact(self, poc_id: int, sha256: str, rel_path: str,
                     license: str | None = None, language: str | None = None,
                     source_ref: str | None = None, url: str | None = None,
                     provenance: dict[str, Any] | None = None,
                     verified: bool | None = None) -> None:
        """Attach a fetched, content-addressed artifact to a poc row (make it fetchable offline).

        When ``source_ref``/``url`` are provided they should point at the **picked code file**
        (e.g. ``owner/repo/path/poc.py``), not the repository root — so search/fetch ground
        the LLM on real PoC source rather than a bare repo link.
        """
        sets = ["artifact_sha256=?", "artifact_path=?"]
        vals: list[Any] = [sha256, rel_path]
        if license:
            sets.append("license=?"); vals.append(license)
        if language:
            sets.append("language=?"); vals.append(language)
        if source_ref:
            # Avoid UNIQUE(source, source_ref) collisions when retargeting to a file path.
            clash = self._db.execute(
                "SELECT id FROM poc WHERE source=(SELECT source FROM poc WHERE id=?) "
                "AND source_ref=? AND id<>?",
                (poc_id, source_ref, poc_id)).fetchone()
            if clash is None:
                sets.append("source_ref=?"); vals.append(source_ref)
        if url:
            sets.append("url=?"); vals.append(url)
        if provenance is not None:
            sets.append("provenance=?"); vals.append(json.dumps(provenance))
        if verified is not None:
            sets.append("verified=?"); vals.append(int(verified))
        vals.append(poc_id)
        self._db.execute(f"UPDATE poc SET {', '.join(sets)} WHERE id=?", vals)
        self._db.execute(
            "INSERT OR IGNORE INTO artifact(sha256,storage_path,stored,language,first_source) "
            "VALUES(?,?,1,?,'poc_github')", (sha256, rel_path, language))
        # Keep FTS title useful when we retarget to a file path.
        if source_ref:
            row = self._db.execute("SELECT title, description FROM poc WHERE id=?", (poc_id,)).fetchone()
            if row:
                self._db.execute("DELETE FROM poc_fts WHERE rowid=?", (poc_id,))
                self._db.execute(
                    "INSERT INTO poc_fts(rowid,title,description) VALUES(?,?,?)",
                    (poc_id, row[0] or source_ref, row[1] or ""))
        self._db.commit()
