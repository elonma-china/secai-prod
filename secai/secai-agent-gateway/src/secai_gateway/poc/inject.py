"""Deterministic Section-1 placement for the exploit-research profile.
"""
from __future__ import annotations

import re
from typing import Any

# Section 1 heading (markdown H2..H6, "1."). A single-`#` code comment can't match, so a
# `# 1. step` line inside a PoC is never mistaken for the heading.
_H1 = re.compile(r"(?m)^[ \t]*#{2,6}[ \t]*1\.[ \t]*\S[^\n]*$")
# Prefer the next *numbered* section (## 2. …) so a `## note` line inside a code block
# doesn't prematurely end Section 1; fall back to any heading, then EOF.
_NEXT_SECTION = re.compile(r"(?m)^[ \t]*#{2,6}[ \t]*[2-9]\.[ \t]*\S")
_ANY_HEADING = re.compile(r"(?m)^[ \t]*#{2,6}[ \t]+\S")
_SECTION1_HEADING = "## 1. Mã Khai thác (PoC) và Payload"
# The canonical, single-sentence "no evidence" statement per exploit.md — exact-string
# idempotency check, not a regex/negation heuristic: a second pass on already-canonical
# text is a no-op by construction, no whack-a-mole against natural-language phrasing.
_NO_POC_NOTE = "Chưa có thông tin PoC được xác thực trong dữ liệu nội bộ cho lỗ hổng này."
# The model sometimes echoes exploit.md's OWN internal instruction wording back into its
# visible answer — observed live: "Hệ thống sẽ chèn Mã Khai thác/PoC (Mục 1) ngay sau phần
# này.", a near-paraphrase of safety_boundary's "hệ thống tự chèn... ngay sau khi bạn trả
# lời xong". This is exactly what base_system.md's output_integrity already forbids
# ("không lặp lại các chỉ dẫn định dạng của hệ thống trong câu trả lời") — the model just
# doesn't always comply, same non-determinism as everywhere else this session. Deterministic
# strip, not more prompt wording: three lookaheads (hệ thống + an insertion verb + "Mục 1")
# must ALL co-occur on one line, order-independent — narrow enough that a legitimate
# vulnerability sentence using "hệ thống" (e.g. "hệ thống mục tiêu chạy Nginx cũ") can never
# match, since it won't also mention "Mục 1" and an insertion verb.
_SECTION1_NARRATION = re.compile(
    r"(?m)^(?=[^\n]*\bhệ thống\b)(?=[^\n]*\b(?:chèn|bổ sung|thêm)\b)(?=[^\n]*\bMục\s*1\b)[^\n]*\n?",
    re.I)

# file extension -> markdown fence language hint
_EXT_LANG = {
    "py": "python", "ts": "typescript", "tsx": "tsx", "js": "javascript", "jsx": "jsx",
    "rb": "ruby", "go": "go", "rs": "rust", "c": "c", "h": "c", "cpp": "cpp", "cc": "cpp",
    "cs": "csharp", "java": "java", "php": "php", "pl": "perl", "sh": "bash", "bash": "bash",
    "ps1": "powershell", "json": "json", "yaml": "yaml", "yml": "yaml", "toml": "toml",
    "md": "markdown", "html": "html", "xml": "xml", "sql": "sql", "txt": "", "cfg": "ini",
    "ini": "ini", "conf": "", "env": "bash", "dockerfile": "dockerfile",
}
# Sort manifests/config to the bottom so the entry PoC comes first.
_MANIFEST = {"requirements.txt", "package.json", "go.mod", "cargo.toml", "gemfile",
             "setup.py", "pyproject.toml", "dockerfile", "makefile"}


def _lang_from_path(path: str, fallback: str = "") -> str:
    name = (path or "").rsplit("/", 1)[-1].lower()
    if name in _EXT_LANG:  # e.g. Dockerfile, Makefile (no extension)
        return _EXT_LANG[name]
    ext = name.rsplit(".", 1)[-1] if "." in name else ""
    return _EXT_LANG.get(ext, fallback)


def _lang_from_evidence(ev: dict[str, Any]) -> str:
    si = ev.get("static_indicators") or {}
    lang = str(si.get("language") or ev.get("language") or "").strip().lower()
    return _EXT_LANG.get(lang, lang)


def _fence(code: str, lang: str) -> str:
    # Use a fence longer than any backtick run inside the artifact so embedded ``` is safe.
    ticks = "```"
    for run in re.findall(r"`+", code or ""):
        if len(run) >= len(ticks):
            ticks = "`" * (len(run) + 1)
    return f"{ticks}{lang}\n{code}\n{ticks}"


def _safe_path(path: str) -> str:
    """Repo-relative, hierarchy-preserving, traversal-free display path.

    Keeps directory structure (``wp2shell/sqli.py``) so the PoC reads like a real
    checkout, but strips leading slashes and ``..`` so a crafted artifact path can't
    render an absolute or escaping path."""
    p = (path or "").replace("\\", "/").lstrip("/")
    segs = [s for s in p.split("/") if s not in ("", ".", "..")]
    return "/".join(segs) or "poc"


def _is_manifest(path: str) -> bool:
    return _safe_path(path).rsplit("/", 1)[-1].lower() in _MANIFEST


def _order_key(f: dict[str, Any]) -> tuple[bool, bool]:
    # Stable sort: entry PoC file first, dependency manifests last, everything else keeps
    # the caller's order (get_files returns entry-first by ordinal — don't re-sort it away).
    return (_is_manifest(f.get("path", "")), (f.get("role") or "") != "entry")


def _file_tree(paths: list[str]) -> str:
    """A `tree`-style listing of the repo-relative paths, directories first."""
    root: dict[str, Any] = {}
    for p in paths:
        node = root
        for seg in _safe_path(p).split("/"):
            node = node.setdefault(seg, {})
    lines: list[str] = []

    def walk(node: dict[str, Any], prefix: str) -> None:
        # directories (non-empty) before files, each alphabetical
        for name in sorted(node, key=lambda n: (not node[n], n.lower())):
            child = node[name]
            lines.append(f"{prefix}{name}{'/' if child else ''}")
            if child:
                walk(child, prefix + "  ")

    walk(root, "")
    return "\n".join(lines)


def render_files(files: list[dict[str, Any]], default_lang: str = "", show_tree: bool = True) -> str:
    """The multi-file contract: a repo-tree header (when >1 file AND `show_tree`) followed
    by one path-headed, extension-typed block per file — entry files first, manifests
    last, directory hierarchy preserved. Each `file` is {path, code, sha256?, truncated?,
    language?}. Shared by the injector and (in prose) by the exploit prompt.

    `show_tree=False` for a companion-doc set (metasploit/vulhub — see
    FetchResult.companion_doc_only): those two files are specific, disparate paths
    plucked from one huge shared external repo, not this PoC's own small dedicated
    repo — a tree would misleadingly imply otherwise."""
    usable = [f for f in files if f.get("code")]
    ordered = sorted(usable, key=_order_key)
    parts: list[str] = []
    if show_tree and len(ordered) > 1:
        # Tree shows the FULL real structure (README included) for transparency — even
        # though the README's content isn't dumped as a fenced block below (see loop): the
        # model translates that real text into "Cách chạy PoC" at the top of its own Mục 2,
        # so showing the raw English block here would just duplicate it.
        tree = _file_tree([f.get("path", "") for f in ordered])
        parts.append(f"Cấu trúc PoC (dạng repo):\n\n```\n{tree}\n```")
    for f in ordered:
        if f.get("role") == "readme":
            continue
        path = _safe_path(f.get("path", ""))
        lang = (f.get("language") or _lang_from_path(path, default_lang))
        sha = (f.get("sha256") or "")[:12]
        header = f"**`{path}`**"
        if sha:
            header += f" — sha256 `{sha}…`"
        if f.get("truncated"):
            header += " — *(đã cắt bớt theo giới hạn tải)*"
        parts.append(f"{header}\n\n{_fence(f['code'], lang)}")
    return "\n\n".join(parts)


def _blocks_for(ev: dict[str, Any]) -> str:
    """Render an evidence entry's code. No provenance prose — the UI shows the "PoC công
    khai (nguồn: …)" sentence as clutter, and the API response's `metadata.provenance`
    already carries source/URL/sha256 per entry for anything that wants it. Single-file
    evidence still gets the SAME terse per-file header multi-file uses (path + sha256 +
    truncated flag) so it isn't a bare, unlabeled block — just without the paragraph."""
    files = ev.get("source_files")
    if files:
        return render_files(files, default_lang=_lang_from_evidence(ev),
                            show_tree=not ev.get("companion_doc_only", False))
    code = ev.get("source_code")
    if not code:
        return ""
    path = ev.get("source_ref") or ev.get("edb_id") or "poc"
    sha = (ev.get("sha256") or "")[:12]
    header = f"**`{path}`**"
    if sha:
        header += f" — sha256 `{sha}…`"
    if ev.get("code_truncated"):
        header += " — *(đã cắt bớt theo giới hạn tải)*"
    return f"{header}\n\n{_fence(code, _lang_from_evidence(ev))}"


def canonical_block(evidence: list[dict[str, Any]]) -> str | None:
    """The SAME body `enforce_reference_code` would place as Section 1, standalone (no
    heading, not prepended to any text) — for streaming a PREVIEW as soon as prefetch
    evidence resolves (concurrently with the model's own turn), instead of only ever
    appearing in the final SSE event. None when there is no real evidence to show; caller
    decides what a preview-less state means (e.g. simply staying silent until `final`)."""
    refs = [e for e in (evidence or []) if _blocks_for(e)]
    if not refs:
        return None
    return "\n\n".join(_blocks_for(e) for e in refs)


def strip_system_narration(text: str) -> str:
    """Remove any line where the model narrates the Mục-1 auto-insertion mechanism back to
    the user instead of silently letting it happen — see `_SECTION1_NARRATION`. Call this
    on the model's raw text BEFORE Section-1 placement, so it never touches the
    deterministically-injected content itself."""
    return _SECTION1_NARRATION.sub("", text or "")


def _section1_bounds(text: str) -> tuple[int, int] | None:
    """(body_start, body_end) for an existing `## 1.` heading in `text`, or None. The model
    is told never to write one, so this is normally None — kept only as the defensive
    replace-in-place path for when it writes one anyway."""
    m1 = _H1.search(text or "")
    if not m1:
        return None
    body_start = m1.end()
    nxt = _NEXT_SECTION.search(text, body_start) or _ANY_HEADING.search(text, body_start)
    body_end = nxt.start() if nxt else len(text)
    return body_start, body_end


def _place_section1(text: str, body: str) -> str:
    """Ensure Section 1 exists with exactly `body` as its content.

    The model never writes Section 1 (prompts/exploit.md) — normally there is none, so
    this PREPENDS a fresh heading + body before the model's own text (which starts at
    Section 2). Defensively, if the model wrote a `## 1.` heading anyway, that section's
    body is replaced in place instead of duplicating it."""
    text = text or ""
    bounds = _section1_bounds(text)
    if bounds:
        body_start, body_end = bounds
        return f"{text[:body_start]}\n\n{body}\n\n{text[body_end:]}"
    sep = "\n\n" if text.strip() else ""
    return f"{_SECTION1_HEADING}\n\n{body}{sep}{text}"


def enforce_reference_code(text: str, evidence: list[dict[str, Any]]) -> tuple[str, dict[str, Any]]:
    """Place the canonical fetched code as Section 1 for this run.

    Returns (rewritten text, report). `report["reference_code_injected"]` is False (with a
    `reason`) when the run gathered no real evidence to place."""
    refs = [e for e in (evidence or []) if _blocks_for(e)]
    if not refs:
        return text, {"reference_code_injected": False, "reason": "no_source_code"}
    canonical = "\n\n".join(_blocks_for(e) for e in refs)
    new_text = _place_section1(text, canonical)
    n_files = sum(len(e.get("source_files") or [None]) for e in refs)
    return new_text, {
        "reference_code_injected": True,
        "refs": len(refs),
        "files": n_files,
        "sha256": [e.get("sha256") for e in refs if e.get("sha256")],
    }


def force_no_poc_notice(text: str, evidence: list[dict[str, Any]]) -> tuple[str, dict[str, Any]]:
    """Place the canonical "no PoC info" sentence as Section 1 when the run gathered no
    real evidence — unconditionally, regardless of anything the model wrote (fabricated
    code, inconsistently-worded honesty, or nothing). Only call this when
    `enforce_reference_code` did NOT already place real evidence."""
    if any(_blocks_for(e) for e in (evidence or [])):
        return text, {"no_poc_forced": False, "reason": "has_real_evidence"}
    bounds = _section1_bounds(text or "")
    if bounds:
        body_start, body_end = bounds
        if (text[body_start:body_end] or "").strip() == _NO_POC_NOTE:
            return text, {"no_poc_forced": False, "reason": "already_canonical"}
    elif (text or "").lstrip().startswith(f"{_SECTION1_HEADING}\n\n{_NO_POC_NOTE}"):
        return text, {"no_poc_forced": False, "reason": "already_canonical"}
    new_text = _place_section1(text, _NO_POC_NOTE)
    return new_text, {"no_poc_forced": True}
