"""Raw-tarball pre-fetcher for GitHub PoCs → content-addressed store (offline serving).

Runtime has NO internet, so link-only poc_github rows are pre-fetched HERE (fetch time) into
the store. We download the repo **tarball over HTTPS** (codeload.github.com — no git, no API
token) and read it **entirely in memory**: nothing untrusted is written to disk and no code is
ever executed, so no sandbox is needed. `_pick()` selects the authoritative entry PoC file and
`_pick_set()` gathers the bounded file set around it (entry's package subtree + root manifests,
hierarchy preserved) for multi-file PoCs. Caps on total tarball size / per-file size / set size /
download time. Resumable (pending_github skips rows already fetched, and dedup-marked forks).

    cd /home/ict/AIEngines/secai-agent-gateway
    ./.venv/bin/python -m secai_gateway.poc.fetch_github --limit 200 --min-stars 5
    # backfill multi-file sets onto a catalog fetched before multi-file existed:
    ./.venv/bin/python -m secai_gateway.poc.fetch_github --refetch --limit 500 --min-stars 5

Also callable as `run(...)` from build_catalog.py so a single `build_catalog --fetch-limit N`
runs collect → prune → dedup → fetch end-to-end. A tarball that 404s (renamed/removed default
branch, private/deleted repo) is simply skipped — there is no git fallback.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import re
import tarfile
from collections.abc import Iterable, Iterator
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path, PurePosixPath
from typing import Any

import httpx

from ..airgap import airgap_enabled
from .backend import LocalPocBackend

_CODE_EXT = (".py", ".rb", ".c", ".cpp", ".sh", ".pl", ".php", ".js", ".go", ".java",
             ".txt", ".md", ".ps1", ".rs", ".html")
_MAX_FILE = 200_000        # per-file byte cap
_MAX_TARBALL = 50_000_000  # whole-tarball download cap (guards decompression bombs / huge repos)
_REPO_TIMEOUT = 60         # seconds per tarball download

# dirs that hold deps/vendored/build/test/docs — NOT the authoritative PoC.
# NOTE: do NOT include bare "example" — it false-positives on Java/Android packages
# like com.example.* which are common in real mobile PoCs.
_SKIP_DIRS = {"node_modules", "vendor", "third_party", "3rdparty", "deps", "dist", "build",
              "target", ".git", "test", "tests", "spec", "docs", "doc", "examples",
              "sample", "samples", "assets", "static", "img", "images", "fixtures", "__pycache__",
              "site-packages", "venv", ".venv"}
_NAME_HINTS = ("exploit", "poc", "cve", "payload", "rce", "attack", "vuln")
_EXPLOIT_SIGNALS = ("requests", "socket", "subprocess", "urllib", "http", "payload",
                    "exploit", "target", "shell", "cmd", "system(", "os.system")


def _tarball_urls(owner: str, repo: str, branch: str) -> list[str]:
    """Candidate codeload URLs, trying the recorded default branch first then main/master
    (codeload 404s on a wrong ref, so `_fetch_one` falls through to the next)."""
    branches: list[str] = []
    for b in (branch, "main", "master"):
        if b and b not in branches:
            branches.append(b)
    return [f"https://codeload.github.com/{owner}/{repo}/tar.gz/{b}" for b in branches]


def _download(url: str, timeout: int, max_bytes: int) -> bytes | None:
    """Stream a tarball into memory, aborting past `max_bytes` (no giant/bomb downloads)."""
    try:
        with httpx.Client(follow_redirects=True, timeout=timeout) as client:
            with client.stream("GET", url) as resp:
                if resp.status_code != 200:
                    return None
                buf = bytearray()
                for chunk in resp.iter_bytes():
                    buf += chunk
                    if len(buf) > max_bytes:
                        return None
                return bytes(buf)
    except Exception:
        return None


def _iter_tar(data: bytes) -> Iterator[tuple[str, bytes]]:
    """Yield (repo-relative-path, bytes) for code files in a .tar.gz, read in memory.

    GitHub wraps everything in a top-level ``{repo}-{ref}/`` dir — we strip it so depth is
    measured from the repo root (matches the old clone-to-/out/repo behaviour). Members with
    path traversal / absolute paths are dropped (zip-slip guard — belt-and-braces even though
    we never write to disk). Per-file byte cap enforced on the ACTUAL read, not the header."""
    try:
        tf = tarfile.open(fileobj=io.BytesIO(data), mode="r:gz")
    except Exception:
        return
    with tf:
        for m in tf:
            if not m.isfile():
                continue
            parts = PurePosixPath(m.name).parts
            if len(parts) <= 1:
                continue  # the bare top-level dir itself, or an unexpected flat entry
            rel = PurePosixPath(*parts[1:])
            if rel.is_absolute() or ".." in rel.parts:
                continue
            if rel.suffix.lower() not in _CODE_EXT:
                continue
            if m.size <= 0 or m.size > _MAX_FILE:
                continue
            try:
                f = tf.extractfile(m)
                if f is None:
                    continue
                blob = f.read(_MAX_FILE + 1)
            except Exception:
                continue
            if not blob or len(blob) > _MAX_FILE:
                continue
            yield str(rel), blob


def _pick(files: Iterable[tuple[str, bytes]], cve: str) -> tuple[bytes, str] | None:
    """Pick the AUTHORITATIVE main PoC file (condensed = one reputable file, not the whole
    repo). Skip vendor/deps/test/docs; score by name hints (CVE/poc/exploit), shallow depth,
    and in-file exploit signals — NOT raw size (avoids grabbing a bundled library). `files`
    is an iterable of (repo-relative-path, bytes) so this is testable without a filesystem."""
    cve_l = cve.lower()
    best: tuple[float, bytes, str] | None = None
    for rel_str, data in files:
        rel = PurePosixPath(rel_str)
        if rel.suffix.lower() not in _CODE_EXT:
            continue
        if any(part.lower() in _SKIP_DIRS for part in rel.parts[:-1]):
            continue
        sz = len(data)
        if sz == 0 or sz > _MAX_FILE:
            continue
        low = data[:20000].decode("utf-8", "replace").lower()
        name = rel.name.lower()
        depth = len(rel.parts) - 1
        # Never treat docs/licenses as the authoritative PoC file — LLM needs code. This is
        # UNCONDITIONAL (no hint-word exception): a writeup conventionally named with the
        # CVE id (e.g. "CVE-2024-XXXXX-writeup.md") used to bypass this via the old "unless a
        # hint word (exploit/poc/cve/...) is in the filename" carve-out below, then score
        # +1000 for the CVE-in-name bonus — an order of magnitude above a same-repo exploit.py
        # (~140) — and win as "the PoC" with nothing downstream able to catch prose
        # masquerading as code (validation_failure only inspects language in ("python","py")).
        # A doc file remains eligible as a SUPPORT/readme file via _pick_set's separate
        # doc-inclusion logic; it just can never win the entry-file vote here (2026-08
        # research finding wrong-entry-file-selected).
        if name.startswith(("readme", "license", "changelog", "makefile", "contributing", "code_of_conduct")):
            continue
        if rel.suffix.lower() in {".md", ".txt", ".rst"}:
            continue
        score = 0.0
        if cve_l in name:
            score += 1000
        score += 60 * sum(h in name for h in _NAME_HINTS)
        score += 8 * sum(low.count(s) for s in _EXPLOIT_SIGNALS)
        score += 200 if cve_l in low else 0       # CVE referenced in the file body
        score -= 40 * depth                        # prefer root-level
        score += min(sz, 8000) / 1000.0            # mild size pref, capped (no lib-grabbing)
        # Prefer real code extensions over docs when scores tie-ish
        if rel.suffix.lower() in {".py", ".rb", ".c", ".cpp", ".go", ".rs", ".java", ".js", ".sh", ".ps1", ".php", ".pl"}:
            score += 80
        if best is None or score > best[0]:
            best = (score, data, rel_str)
    if best is None:
        return None
    return best[1], best[2]


_MANIFEST_NAMES = {"requirements.txt", "setup.py", "pyproject.toml", "package.json",
                   "go.mod", "cargo.toml", "gemfile", "composer.json"}
# How-to-run docs — real usage instructions, not exploit mechanics; unlike LICENSE/
# CHANGELOG these are worth keeping in the file set (see prompts/exploit.md: the model may
# translate real doc run-steps into the report's overview, grounded in this exact text —
# never invent run-steps otherwise). Extension-based, not a fixed name list: authors name
# these README.md, report.md, usage.md, writeup.md, walkthrough.md, NOTES.md — .md/.rst is
# a strong enough signal on its own (these extensions are essentially never used for
# functional PoC code, unlike .txt which is ambiguous — requirements.txt is a manifest,
# not prose). LICENSE/CHANGELOG/CONTRIBUTING/CODE_OF_CONDUCT are excluded earlier in
# _pick_set regardless of extension, so this only needs to catch what's left.
_DOC_EXT = {".md", ".rst"}
_DOC_EXACT_NAMES = {"readme", "readme.txt"}  # extensionless or .txt README — .txt alone is too broad


def _is_doc_file(name: str) -> bool:
    n = name.lower()
    return n in _DOC_EXACT_NAMES or PurePosixPath(n).suffix in _DOC_EXT
_SET_MAX_FILES = 12
_SET_MAX_BYTES = 400_000  # total bytes stored across the set (backend re-caps at read time)


def _pick_set(files: list[tuple[str, bytes]], entry_rel: str
             ) -> tuple[list[tuple[str, bytes]], bool]:
    """Given all repo files and the chosen entry file, return the bounded set belonging to
    the same PoC: the entry file first, then any sibling file the entry's own code actually
    ``import``s / references by path (forced in BEFORE the cap can discard them — see below),
    then other code/manifest files in the entry's package subtree (its top-level dir, or the
    repo root when the entry is at root), plus repo-root dependency manifests. Repo-relative
    paths preserved (hierarchy). Skips vendor/deps/test/doc dirs. Bounded by file count and
    total bytes so a big repo can't blow the store or the model's context.

    Returns ``(picked, truncated)`` — ``truncated`` is True when the cap actually discarded a
    candidate file (provenance signal for the caller; see catalog rows / research finding
    silent-dependency-drop). Import-aware ordering matters because ``catalog.set_files()``
    replaces the WHOLE file set wholesale — a sibling dropped here by pure tar-iteration-order
    luck is gone permanently; nothing downstream can resurrect it."""
    entry = PurePosixPath(entry_rel)
    pkg = entry.parts[0] if len(entry.parts) > 1 else None  # top package dir, None = repo root
    picked: list[tuple[str, bytes]] = []
    seen: set[str] = set()
    total = 0
    truncated = False

    def add(rel: str, data: bytes) -> None:
        nonlocal total, truncated
        if rel in seen:
            return
        if len(picked) >= _SET_MAX_FILES or total + len(data) > _SET_MAX_BYTES:
            truncated = True
            return
        seen.add(rel); picked.append((rel, data)); total += len(data)

    entry_data = b""
    for rel, data in files:  # entry always first
        if rel == entry_rel:
            add(rel, data)
            entry_data = data
            break

    # Import-aware priority pass: force in whatever the entry's own code actually references
    # (import OR a subprocess/open/exec string-literal path — see consolidate.py's
    # _imported_sibling_names) BEFORE the generic same-package sweep below, so the
    # file-count/byte cap can only ever discard files the entry doesn't depend on.
    if entry_data:
        from .consolidate import _imported_sibling_names
        basenames_by_stem: dict[str, list[tuple[str, bytes]]] = {}
        for rel_str, data in files:
            if rel_str == entry_rel:
                continue
            stem = PurePosixPath(rel_str).stem
            basenames_by_stem.setdefault(stem, []).append((rel_str, data))
        try:
            entry_text = entry_data.decode("utf-8", "replace")
        except Exception:
            entry_text = ""
        needed = _imported_sibling_names(entry_text, set(basenames_by_stem.keys()))
        for stem in needed:
            for rel_str, data in basenames_by_stem.get(stem, []):
                add(rel_str, data)

    for rel_str, data in files:
        if rel_str in seen:
            continue
        rel = PurePosixPath(rel_str)
        if any(part.lower() in _SKIP_DIRS for part in rel.parts[:-1]):
            continue
        name = rel.name.lower()
        if name.startswith(("license", "changelog", "contributing", "code_of_conduct")):
            continue
        in_pkg = (rel.parts[0] == pkg) if pkg else (len(rel.parts) == 1)
        is_root_manifest = len(rel.parts) == 1 and (name in _MANIFEST_NAMES or _is_doc_file(name))
        if in_pkg or is_root_manifest:
            add(rel_str, data)
    return picked, truncated


def _fetch_one(urls: list[str], cve: str, timeout: int
               ) -> tuple[bytes, str, list[tuple[str, bytes]], bool] | None:
    """Try each candidate tarball URL until one downloads and yields a picked PoC. Returns
    (entry_bytes, entry_rel, file_set, truncated) — file_set is [(rel_path, bytes)], entry
    first; truncated is True when _pick_set's file-count/byte cap discarded a candidate file
    (provenance signal — see _pick_set)."""
    for url in urls:
        data = _download(url, timeout, _MAX_TARBALL)
        if not data:
            continue
        all_files = list(_iter_tar(data))
        picked = _pick(all_files, cve)
        if picked:
            entry_bytes, entry_rel = picked
            file_set, truncated = _pick_set(all_files, entry_rel)
            return entry_bytes, entry_rel, file_set, truncated
    return None


_GH_URL = re.compile(
    r"(?:https?://)?(?:www\.)?github\.com/([^/\s]+)/([^/\s?#]+)", re.I)


def _owner_repo(source_ref: str, url: str = "") -> tuple[str, str] | None:
    """Normalize ``owner/repo``, ``owner/repo/path.py``, or full GitHub URL → (owner, repo)."""
    for raw in (source_ref or "", url or ""):
        s = str(raw).strip().rstrip("/")
        m = _GH_URL.search(s)
        if m:
            return m.group(1), m.group(2).removesuffix(".git")
        # owner/repo or owner/repo/deeper/path
        parts = [p for p in s.split("/") if p and p not in (".", "..")]
        if len(parts) >= 2 and ":" not in parts[0]:
            return parts[0], parts[1]
    return None


def fetch_repo_code(source_ref: str, url: str = "", branch: str = "",
                    cve: str = "", timeout: int = _REPO_TIMEOUT
                    ) -> tuple[bytes, str, list[tuple[str, bytes]], bool] | None:
    """On-demand, READ-ONLY code fetch for a link-only GitHub PoC — the request-path twin of
    ``run()``'s per-row body, WITHOUT any store/catalog writes.

    Given a catalog row's ``source_ref``/``url`` (and optionally the recorded default
    ``branch`` + the ``cve`` for entry-file picking), download the repo tarball and return
    ``(entry_bytes, entry_rel, file_set, truncated)`` exactly like ``_fetch_one`` — or ``None``
    on any miss (bad url, 404, no picked PoC). Never raises, never touches disk beyond the
    in-memory tarball, never executes code — same safety envelope as ``run()``. The caller
    turns the bytes into ledger evidence; nothing is persisted to the poc catalog/store."""
    if airgap_enabled():  # air-gap: no codeload.github.com fetch (offline build sets AIRGAP=false)
        return None
    parsed = _owner_repo(str(source_ref or ""), str(url or ""))
    if not parsed:
        return None
    owner, repo = parsed
    return _fetch_one(_tarball_urls(owner, repo, branch or "master"),
                      cve or repo, timeout)


def _fetch_row(args: tuple[tuple[str, str], str, str, int]
              ) -> tuple[bytes, str, list[tuple[str, bytes]], bool] | None:
    """Network-only half of a fetch: download + pick, no store/catalog writes. Thread-safe
    (a fresh httpx client per call) so a worker pool can run it in parallel."""
    (owner, repo), pick_hint, branch, timeout = args
    return _fetch_one(_tarball_urls(owner, repo, branch), pick_hint, timeout)


def run(limit: int, min_stars: int, per_timeout: int = _REPO_TIMEOUT,
        refetch: bool = False, workers: int = 1) -> None:
    be = LocalPocBackend()
    store = be.roots["poc_github"]
    store.mkdir(parents=True, exist_ok=True)
    rows = be.catalog.pending_github(limit, min_stars, refetch=refetch)
    mode = "re-fetch (multi-file backfill)" if refetch else "fetch"
    print(f"pending github PoCs to {mode}: {len(rows)} (min_stars={min_stars}, workers={workers})",
          flush=True)
    ok = skip = 0

    # Prepare the immutable per-row fetch spec (owner/repo/branch/pick-hint/provenance) up
    # front so the worker threads never touch the shared catalog connection.
    specs: list[tuple[int, tuple[tuple[str, str], str, str, int], dict[str, Any]]] = []
    for r in rows:
        poc_id, full, url = r["id"], r["source_ref"], r["url"]
        parsed = _owner_repo(str(full), str(url or ""))
        if not parsed:
            skip += 1
            continue
        owner, repo = parsed
        prov = json.loads(r["provenance"] or "{}")
        branch = prov.get("default_branch") or "master"
        pick_hint = repo
        poc_meta = be.catalog.get(poc_id)
        if poc_meta and poc_meta.get("cve_ids"):
            pick_hint = poc_meta["cve_ids"][0]
        specs.append((poc_id, ((owner, repo), pick_hint, branch, per_timeout), prov))

    done = 0
    # Network phase in parallel (the slow part); store/catalog writes stay on THIS thread so
    # the single SQLite connection (WAL + busy_timeout) never sees concurrent writers.
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = {pool.submit(_fetch_row, spec): (poc_id, spec, prov)
                   for poc_id, spec, prov in specs}
        for fut in as_completed(futures):
            poc_id, spec, prov = futures[fut]
            (parsed, _pick_hint, branch, _timeout) = spec
            owner, repo = parsed
            picked = fut.result()
            if not picked:
                skip += 1
                done += 1
                continue
            blob, rel_in_repo, file_set, fileset_truncated = picked
            lang = Path(rel_in_repo).suffix.lstrip(".") or None  # store name is hash-only
            sha = hashlib.sha256(blob).hexdigest()
            dst = store / sha[:2] / sha
            dst.parent.mkdir(parents=True, exist_ok=True)
            if not dst.exists():
                dst.write_bytes(blob)
            # Point catalog at the CODE FILE (not repo root) so LLM retrieval is grounded.
            file_ref = f"{owner}/{repo}/{rel_in_repo}"
            blob_url = f"https://github.com/{owner}/{repo}/blob/{branch}/{rel_in_repo}"
            prov.update({
                "repo": f"{owner}/{repo}",
                "canonical_poc_path": rel_in_repo,
                "points_to_code_file": True,
                "fetched_bytes": len(blob),
                "fileset_checked": 1,  # processed for a multi-file set (single-file repos too) → backfill skips next time
                # True when _pick_set's file-count/byte cap discarded a candidate file for
                # this repo — a durable signal to find/backfill affected rows later (2026-08
                # research finding silent-dependency-drop: catalog.set_files() replaces the
                # whole set wholesale, so a dropped sibling can't be recovered downstream).
                "fileset_truncated": bool(fileset_truncated),
            })
            be.catalog.set_artifact(
                poc_id, sha, f"{sha[:2]}/{sha}",
                license="upstream repo license; static analysis / lab grounding only",
                language=lang,
                source_ref=file_ref,
                url=blob_url,
                provenance=prov,
                verified=True,
            )
            # Multi-file PoC: store the whole bounded set (hierarchy preserved) so fetch can
            # return a real repo layout, not just the entry file. Single-file repos skip this.
            if len(file_set) > 1:
                file_rows = []
                for frel, fdata in file_set:
                    fsha = hashlib.sha256(fdata).hexdigest()
                    fdst = store / fsha[:2] / fsha
                    fdst.parent.mkdir(parents=True, exist_ok=True)
                    if not fdst.exists():
                        fdst.write_bytes(fdata)
                    if frel == rel_in_repo:
                        role = "entry"
                    elif _is_doc_file(Path(frel).name):
                        role = "readme"
                    else:
                        role = "support"
                    file_rows.append({
                        "rel_path": frel, "sha256": fsha, "storage_path": f"{fsha[:2]}/{fsha}",
                        "language": Path(frel).suffix.lstrip(".") or None, "size": len(fdata),
                        "role": role,
                    })
                be.catalog.set_files(poc_id, file_rows)
            ok += 1
            done += 1
            if ok % 20 == 0 or ok <= 5:
                print(f"  [{done}/{len(specs)}] fetched {file_ref} sha={sha[:12]}", flush=True)
    print(f"DONE fetched={ok} skipped={skip} · store={store}", flush=True)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=200)
    ap.add_argument("--min-stars", type=int, default=5)
    ap.add_argument("--timeout", type=int, default=_REPO_TIMEOUT)
    ap.add_argument("--workers", type=int, default=1,
                    help="Parallel tarball downloads (network is the bottleneck; store/catalog "
                         "writes stay single-threaded).")
    ap.add_argument("--refetch", action="store_true",
                    help="Backfill mode: re-download already-fetched repos that lack a "
                         "multi-file set and populate it (upgrade a pre-multi-file catalog).")
    a = ap.parse_args()
    run(a.limit, a.min_stars, a.timeout, refetch=a.refetch, workers=a.workers)


if __name__ == "__main__":
    main()
