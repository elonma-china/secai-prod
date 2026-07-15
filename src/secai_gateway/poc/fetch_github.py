"""Raw-tarball pre-fetcher for GitHub PoCs → content-addressed store (offline serving).

Runtime has NO internet, so link-only poc_github rows are pre-fetched HERE (fetch time) into
the store. We download the repo **tarball over HTTPS** (codeload.github.com — no git, no API
token) and read it **entirely in memory**: nothing untrusted is written to disk and no code is
ever executed, so no sandbox is needed. `_pick()` then selects the single authoritative PoC
file. Caps on total tarball size / per-file size / download time. Resumable (pending_github
skips rows already fetched, and dedup-marked fork duplicates).

    cd /home/ict/AIEngines/secai-agent-gateway
    ./.venv/bin/python -m secai_gateway.poc.fetch_github --limit 200 --min-stars 5

Also callable as `run(...)` from build_catalog.py so a single `build_catalog --fetch-limit N`
runs collect → prune → dedup → fetch end-to-end. A tarball that 404s (renamed/removed default
branch, private/deleted repo) is simply skipped — there is no git fallback.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import tarfile
from collections.abc import Iterable, Iterator
from pathlib import Path, PurePosixPath

import httpx

from .backend import LocalPocBackend

_CODE_EXT = (".py", ".rb", ".c", ".cpp", ".sh", ".pl", ".php", ".js", ".go", ".java",
             ".txt", ".md", ".ps1", ".rs", ".html")
_MAX_FILE = 200_000        # per-file byte cap
_MAX_TARBALL = 50_000_000  # whole-tarball download cap (guards decompression bombs / huge repos)
_REPO_TIMEOUT = 60         # seconds per tarball download

# dirs that hold deps/vendored/build/test/docs — NOT the authoritative PoC
_SKIP_DIRS = {"node_modules", "vendor", "third_party", "3rdparty", "deps", "dist", "build",
              "target", ".git", "test", "tests", "spec", "docs", "doc", "examples", "example",
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
        score = 0.0
        if cve_l in name:
            score += 1000
        score += 60 * sum(h in name for h in _NAME_HINTS)
        score += 8 * sum(low.count(s) for s in _EXPLOIT_SIGNALS)
        score += 200 if cve_l in low else 0       # CVE referenced in the file body
        score -= 40 * depth                        # prefer root-level
        score += min(sz, 8000) / 1000.0            # mild size pref, capped (no lib-grabbing)
        score -= 50 if name.startswith(("readme", "license", "changelog", "setup", "makefile")) else 0
        if best is None or score > best[0]:
            best = (score, data, rel_str)
    if best is None:
        return None
    return best[1], best[2]


def _fetch_one(urls: list[str], cve: str, timeout: int) -> tuple[bytes, str] | None:
    """Try each candidate tarball URL until one downloads and yields a picked PoC file."""
    for url in urls:
        data = _download(url, timeout, _MAX_TARBALL)
        if not data:
            continue
        picked = _pick(_iter_tar(data), cve)
        if picked:
            return picked
    return None


def run(limit: int, min_stars: int, per_timeout: int = _REPO_TIMEOUT) -> None:
    be = LocalPocBackend()
    store = be.roots["poc_github"]
    store.mkdir(parents=True, exist_ok=True)
    rows = be.catalog.pending_github(limit, min_stars)
    print(f"pending github PoCs to fetch: {len(rows)} (min_stars={min_stars})", flush=True)
    ok = skip = 0
    for i, r in enumerate(rows, 1):
        poc_id, full, url = r["id"], r["source_ref"], r["url"]
        owner, _, repo = str(full).partition("/")
        branch = (json.loads(r["provenance"] or "{}").get("default_branch") or "master")
        # repo name doubles as the pick hint (a pending row maps to a repo, not one CVE)
        picked = _fetch_one(_tarball_urls(owner, repo, branch), full.split("/")[-1], per_timeout)
        if not picked:
            skip += 1
            continue
        blob, rel_in_repo = picked
        lang = Path(rel_in_repo).suffix.lstrip(".") or None  # store name is hash-only
        sha = hashlib.sha256(blob).hexdigest()
        dst = store / sha[:2] / sha
        dst.parent.mkdir(parents=True, exist_ok=True)
        if not dst.exists():
            dst.write_bytes(blob)
        be.catalog.set_artifact(poc_id, sha, f"{sha[:2]}/{sha}",
                                license="upstream repo license; static analysis only",
                                language=lang)
        ok += 1
        if i % 20 == 0 or ok <= 5:
            print(f"  [{i}/{len(rows)}] fetched {full} ({rel_in_repo}) sha={sha[:12]}", flush=True)
    print(f"DONE fetched={ok} skipped={skip} · store={store}", flush=True)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=200)
    ap.add_argument("--min-stars", type=int, default=5)
    ap.add_argument("--timeout", type=int, default=_REPO_TIMEOUT)
    a = ap.parse_args()
    run(a.limit, a.min_stars, a.timeout)


if __name__ == "__main__":
    main()
