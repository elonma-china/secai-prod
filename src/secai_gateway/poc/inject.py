"""Deterministic Section-1 placement for the exploit-research profile.
"""
from __future__ import annotations

import re
from typing import Any

# Decorative banner noise stripped at DISPLAY time only (Mục 1). Catalog bytes / sha256
# stay untouched so provenance remains grounded. Docstrings, argparse help, and normal
# technical comments are kept — they feed Mục 2 ("Cách chạy PoC").
#
# Comment-prefix variants (line-start only — we do NOT strip mid-line or string contents):
#   #        Python / Ruby / Perl / YAML / shell
#   //  /*   C / C++ / Java / JS / Go / Rust / PHP / C#
#   ;        asm / Lisp / some INI
#   --       SQL / Lua / Haskell
#   '  REM   VB / BASIC / batch
# A lone "# probe the endpoint" technical comment does NOT match (no long ruler / banner key).
_COMMENT_PREFIX = r"(?:\#|//|/\*|\;|--|'|REM\b)"
_BANNER_LINE = re.compile(
    rf"""^[ \t]*(?:
        {_COMMENT_PREFIX}?[ \t]*[#/*;\-_=+~]{{8,}}[#/*;\-_=+~\s]*$  # # ===== / // **** rulers
      | [─━═░▒▓█▄▀■□★☆✔✗⚠🚀💀🔥]{{3,}}                              # box-drawing / emoji art
      | {_COMMENT_PREFIX}?[ \t]*(?:Author|Coded\s+by|POC\s+by|Written\s+by|Discovered\s+by)\s*:
      | {_COMMENT_PREFIX}?[ \t]*(?:Twitter|Telegram|Follow\s+me|Website|Blog|Email)\s*:
      | {_COMMENT_PREFIX}?[ \t]*DISCLAIMER\b
      | {_COMMENT_PREFIX}?[ \t]*={{3,}}\s*[A-Za-z].*={{3,}}\s*$       # === TITLE ===
    )""",
    re.I | re.X,
)
# Collapse 3+ blank lines left after banner removal → at most one blank.
_MULTI_BLANK = re.compile(r"\n{3,}")

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

# language -> canonical file extension. The exploit environment is fully air-gapped: the
# report must present each PoC as a LOCAL file named after the CVE (e.g. cve-2026-21111.py),
# never the upstream GitHub URL / nuclei-template path / exploit-db path the artifact was
# fetched from — those imply an online checkout that doesn't exist offline. See _airgap_name.
_LANG_EXT = {
    "python": "py", "typescript": "ts", "tsx": "tsx", "javascript": "js", "jsx": "jsx",
    "ruby": "rb", "go": "go", "rust": "rs", "c": "c", "cpp": "cpp", "csharp": "cs",
    "java": "java", "php": "php", "perl": "pl", "bash": "sh", "powershell": "ps1",
    "json": "json", "yaml": "yaml", "yml": "yaml", "toml": "toml", "markdown": "md",
    "html": "html", "xml": "xml", "sql": "sql", "ini": "ini",
}


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


def sanitize_poc_display(code: str) -> str:
    """Strip decorative PoC banners for Mục 1 display — not a full comment remover.

    Removes Exploit-DB / community-style noise (long ``====`` rulers, ASCII/emoji art,
    ``Author:`` / ``Twitter:`` / ``DISCLAIMER`` banner lines). Keeps shebang, encoding
    cookies, normal ``#`` technical comments, docstrings, and argparse/usage text so Mục 2
    can still ground run instructions. Idempotent; empty input unchanged.
    """
    if not code:
        return code
    kept: list[str] = []
    for line in code.splitlines(keepends=True):
        core = line.rstrip("\n\r")
        if _BANNER_LINE.match(core):
            continue
        kept.append(line)
    out = "".join(kept)
    out = _MULTI_BLANK.sub("\n\n", out)
    # Preserve a trailing newline when the original had one (typical source files).
    if code.endswith("\n") and out and not out.endswith("\n"):
        out += "\n"
    return out


def _fence(code: str, lang: str) -> str:
    # Use a fence longer than any backtick run inside the artifact so embedded ``` is safe.
    # Decorative banners are stripped here (display path) — catalog/sha stay original.
    code = sanitize_poc_display(code or "")
    ticks = "```"
    for run in re.findall(r"`+", code):
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


def _airgap_name(cve_id: str | None, lang: str, orig_path: str = "") -> str:
    """Local, air-gapped display filename for a PoC block. `<cve-id>.<ext>` (lowercased) when
    a CVE was identified, else the bare basename of the original path — NEVER a URL, repo
    path or upstream template/module path. The report is consumed offline, so it must name a
    file the reader saved locally, not the internet location it came from."""
    ext = _LANG_EXT.get((lang or "").strip().lower(), "")
    base = _safe_path(orig_path).rsplit("/", 1)[-1]
    if not ext and "." in base:  # fall back to the artifact's own extension (never its dirs)
        ext = base.rsplit(".", 1)[-1].lower()
    if cve_id:
        stem = cve_id.strip().lower()
        return f"{stem}.{ext}" if ext else stem
    return base or (f"poc.{ext}" if ext else "poc")


def _airgap_names(files: list[dict[str, Any]], cve_id: str | None, default_lang: str) -> list[str]:
    """Display names aligned with `files`: the entry PoC → `<cve>.<ext>`, every
    companion/readme/manifest → its bare basename (no directories, no URLs)."""
    names: list[str] = []
    entry_done = False
    for f in files:
        path = f.get("path", "")
        lang = (f.get("language") or _lang_from_path(path, default_lang))
        if not entry_done and (f.get("role") or "") != "readme" and not _is_manifest(path):
            names.append(_airgap_name(cve_id, lang, path))
            entry_done = True
        else:
            names.append(_safe_path(path).rsplit("/", 1)[-1] or "poc")
    return names


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


def render_files(files: list[dict[str, Any]], default_lang: str = "", show_tree: bool = True,
                 cve_id: str | None = None) -> str:
    """The multi-file contract: a local-file listing (when >1 file AND `show_tree`) followed
    by one file-headed, extension-typed block per file — entry file first, manifests last.
    Each `file` is {path, code, sha256?, truncated?, language?}. Shared by the injector and
    (in prose) by the exploit prompt.

    Air-gap naming: the entry PoC is shown as `<cve-id>.<ext>` and companions as their bare
    basename (see `_airgap_names`) — never the upstream repo/URL path, which is meaningless
    offline. `show_tree=False` for a companion-doc set (metasploit/vulhub —
    FetchResult.companion_doc_only): those files are disparate paths plucked from a huge
    shared external repo, not this PoC's own repo."""
    usable = [f for f in files if f.get("code")]
    ordered = sorted(usable, key=_order_key)
    names = _airgap_names(ordered, cve_id, default_lang)
    parts: list[str] = []
    if show_tree and len(ordered) > 1:
        # Flat list of the LOCAL file names (README included) so the reader knows the whole
        # set; the README's content isn't dumped as a fenced block below (see loop) — the
        # model translates that into "Cách chạy PoC" at the top of its own Mục 2.
        tree = _file_tree(names)
        parts.append(f"Các tệp PoC (cục bộ):\n\n```\n{tree}\n```")
    for f, name in zip(ordered, names):
        if f.get("role") == "readme":
            continue
        lang = (f.get("language") or _lang_from_path(f.get("path", ""), default_lang))
        header = f"**`{name}`**"
        if f.get("truncated"):
            header += " — *(đã cắt bớt theo giới hạn tải)*"
        parts.append(f"{header}\n\n{_fence(f['code'], lang)}")
    return "\n\n".join(parts)


def _is_detection_evidence(ev: dict[str, Any]) -> bool:
    """Nuclei / kind=detection templates detect a vuln — they are NOT exploit PoCs for T1
    injection. Their YAML body is synthesis grounding (T2) instead: method/path/payload/
    matcher feed the model so it can write a real standalone PoC."""
    kind = str(ev.get("kind") or "").strip().lower()
    source = str(ev.get("source") or "").strip().lower()
    return kind == "detection" or source == "nuclei"


def _blocks_for(ev: dict[str, Any], cve_id: str | None = None) -> str:
    """Render an evidence entry's exploit code for T1 Section-1 injection.

    Detection templates (nuclei YAML, ``kind=detection``) are deliberately excluded even
    when they carry ``source_code`` — injecting the scanner template as "Mã Khai thác"
    blocked synthesis of a real PoC. Those entries go through ``_description_for`` (T2).

    No provenance prose — the UI shows the "PoC công khai (nguồn: …)" sentence as clutter,
    and the API response's ``metadata.provenance`` already carries source/URL/sha256 per
    entry. The header names the PoC as a local file (``<cve-id>.<ext>`` in air-gap), not
    its upstream path/URL."""
    if _is_detection_evidence(ev):
        return ""
    files = ev.get("source_files")
    if files:
        block = render_files(files, default_lang=_lang_from_evidence(ev),
                             show_tree=not ev.get("companion_doc_only", False), cve_id=cve_id)
        return _with_consolidated_banner(block, ev)
    code = ev.get("source_code")
    if not code:
        return ""
    lang = _lang_from_evidence(ev)
    name = _airgap_name(cve_id, lang, ev.get("source_ref") or ev.get("edb_id") or "")
    header = f"**`{name}`**"
    if ev.get("code_truncated"):
        header += " — *(đã cắt bớt theo giới hạn tải)*"
    return _with_consolidated_banner(f"{header}\n\n{_fence(code, lang)}", ev)


def _with_consolidated_banner(block: str, ev: dict[str, Any]) -> str:
    """Prepend the light consolidated-tier banner to a rendered code block when the evidence
    entry is an AI-merged single file (`consolidated=True`). Idempotent — a block that already
    opens with the banner is returned unchanged."""
    if not block or not ev.get("consolidated"):
        return block
    if block.lstrip().startswith(_CONSOLIDATED_BANNER):
        return block
    return f"{_CONSOLIDATED_BANNER}\n\n{block}"


def _description_for(ev: dict[str, Any]) -> str:
    """Fallback evidence text when there's no injectable exploit code (`_blocks_for` empty):
    a GHSA advisory body, a vulhub lab whose README was captured because it has no compose
    file, a GitHub-fetched PoC whose only file is a README (`role="readme"`, deliberately
    skipped by `render_files`), or a nuclei/detection YAML (template body = synthesis
    grounding: HTTP method/path/payload/matcher). This is the synthesize-from-evidence
    tier's input."""
    if _blocks_for(ev):
        return ""
    desc = str(ev.get("description") or "").strip()
    if desc:
        return desc
    for f in ev.get("source_files") or []:
        if f.get("role") == "readme" and f.get("code"):
            return str(f["code"]).strip()
    # Detection YAML with no separate description — the template itself is the evidence.
    if _is_detection_evidence(ev):
        code = ev.get("source_code")
        if code:
            return str(code).strip()
        for f in ev.get("source_files") or []:
            if f.get("code"):
                return str(f["code"]).strip()
    return ""


def has_real_code_evidence(evidence: list[dict[str, Any]]) -> bool:
    """Whether any entry has injectable exploit code (`_blocks_for`) — the T1 signal.
    Detection YAML does NOT count (see ``_is_detection_evidence``). Used by
    RequireSection1SynthesisHook to skip forcing a synthesis retry when Section 1 will be
    the deterministic real-code tier regardless."""
    return any(_blocks_for(e) for e in (evidence or []))


def has_synthesis_evidence(evidence: list[dict[str, Any]]) -> bool:
    """Whether any entry has description/README/detection-YAML text but no injectable
    exploit code (`_description_for`) — the T2 signal, independent of `cve_known` (T3).
    Used by RequireSection1SynthesisHook to know whether forcing a Section-1 retry is
    worth it."""
    return any(_description_for(e) for e in (evidence or []))


def section1_has_body(text: str) -> bool:
    """Whether `text` already contains a non-empty `## 1.` section — used by
    RequireSection1SynthesisHook to check the model's own candidate answer before forcing
    a retry."""
    bounds = _section1_bounds(text or "")
    return bool(bounds and (text[bounds[0]:bounds[1]] or "").strip())


_SYNTHESIS_BANNER = (
    "⚠️ SYNTHESIZED — CẢNH BÁO: CODE DO AI TỰ TỔNG HỢP DỰA TRÊN BẰNG CHỨNG NỘI BỘ, "
    "KHÔNG PHẢI PoC ĐÃ FETCH THẬT, CHƯA ĐƯỢC THỰC THI HAY XÁC THỰC."
)
# LIGHT banner for the consolidated tier (poc/consolidate.py): the code IS grounded in a real
# fetched PoC — only merged into one runnable file and stripped of noise by an LLM pass — so it
# gets a softer note than the SYNTHESIZED banner (which is for code invented from a description).
# Provenance (original sha256/URL) stays in metadata regardless. Prepended by `_blocks_for`.
_CONSOLIDATED_BANNER = (
    "ℹ️ Tệp dưới đây đã được AI hợp nhất/tinh gọn thành MỘT tệp duy nhất từ (các) tệp PoC gốc "
    "đã fetch — CHƯA chạy thử; nguồn gốc (URL/sha256) xem ở metadata."
)
# Distinct from _NO_POC_NOTE: evidence/CVE DID exist (see Mục 3 for real CWE/CVSS/CAPEC
# data), only the synthesis attempt itself failed this turn — must not claim the CVE is
# unknown when it demonstrably isn't.
_SYNTHESIS_FAILED_NOTE = "Không tổng hợp được PoC minh hoạ cho lỗ hổng này từ dữ liệu nội bộ."

# A SECOND, END-OF-ANSWER warning for the synthesized tiers (T2/T3), on top of the
# in-place `_SYNTHESIS_BANNER` at the top of Mục 1. The banner lives inline with the code
# and can get skimmed past; a reader scrolling to act on Mục 2's run instructions may never
# scroll back up to it. Placing the same warning again at the very end — where a reader's
# eye naturally lands after reading the whole report — is a deliberate, requested redundancy,
# not a duplicate: it's the last thing read before acting on the PoC.
# Substring used for the idempotency check below — must stay a stable prefix of the footer.
_SYNTHESIS_FOOTER_MARKER = "Mã PoC/hướng dẫn ở trên là bản GỢI Ý do AI tự tổng hợp"
_SYNTHESIS_FOOTER_TEMPLATE = (
    "\n\n---\n\n⚠️ **Cảnh báo:** " + _SYNTHESIS_FOOTER_MARKER + "{sources}, KHÔNG phải PoC đã "
    "fetch và xác thực từ nguồn thật. Đây là bản nháp CHƯA được thực thi — cần kiểm chứng lại "
    "trong môi trường lab được uỷ quyền trước khi sử dụng cho bất kỳ mục đích nào."
)


def _synthesis_source_urls(evidence: list[dict[str, Any]]) -> list[str]:
    """URLs of the description/README-only evidence that fed the synthesis (T2 catalog
    GHSA/README, or T3 web hints from ``retrieval.websearch.gather_web_hints`` — both carry
    ``url``/``source_ref``) — named in the end-of-answer footer so the warning is concrete
    ("dựa trên X") rather than generic."""
    urls: list[str] = []
    for e in evidence or []:
        if not _description_for(e):
            continue
        url = e.get("url") or e.get("source_ref")
        if url and str(url) not in urls:
            urls.append(str(url))
    return urls


def _append_synthesis_footer(text: str, evidence: list[dict[str, Any]]) -> str:
    """Append the end-of-answer synthesis warning once. Idempotent — a second pass over
    already-footed text (e.g. a retry that re-runs enforce_section1) is a no-op by marker
    check, mirroring force_no_poc_notice's own idempotency pattern."""
    if _SYNTHESIS_FOOTER_MARKER in text:
        return text
    urls = _synthesis_source_urls(evidence)
    sources = f" (tham khảo: {', '.join(urls)})" if urls else ""
    return text.rstrip() + _SYNTHESIS_FOOTER_TEMPLATE.format(sources=sources)


def canonical_block(evidence: list[dict[str, Any]], cve_id: str | None = None) -> str | None:
    """The SAME body `enforce_reference_code` would place as Section 1, standalone (no
    heading, not prepended to any text) — for streaming a PREVIEW as soon as prefetch
    evidence resolves (concurrently with the model's own turn), instead of only ever
    appearing in the final SSE event. None when there is no real evidence to show; caller
    decides what a preview-less state means (e.g. simply staying silent until `final`)."""
    refs = [e for e in (evidence or []) if _blocks_for(e)]
    if not refs:
        return None
    return "\n\n".join(_blocks_for(e, cve_id) for e in refs)


_CODE_HOST_RE = re.compile(
    r"(?:raw\.|gist\.|objects\.|codeload\.)?(?:github|githubusercontent|gitlab|bitbucket|gitee)"
    r"\.com|(?:www\.)?exploit-db\.com|packetstormsecurity\.com", re.I)
_SRC_EXTS = {"yaml", "yml", "py", "rb", "sh", "ps1", "pl", "php", "js", "ts", "go", "rs",
             "c", "cpp", "cc", "h", "java", "json", "nasl", "rc"}
_URL_RE = re.compile(r"""https?://[^\s`)\]<>"']+""", re.I)
# a path (>=1 leading dir) OR bare filename ending in CVE-YYYY-NNNN.<ext>
_CVE_PATH_RE = re.compile(r"(?:[A-Za-z0-9._-]+/)+(CVE-\d{4}-\d{3,})\.([A-Za-z0-9]+)", re.I)
_CVE_FILE_RE = re.compile(r"\b(CVE-\d{4}-\d{3,})\.([A-Za-z0-9]+)", re.I)
# `-t <token>` whose value is still a template path/URL (nuclei et al.)
_DASH_T_RE = re.compile(r"(-t\s+)(\S+)")
# Internet package/tool installers + git clone. NOT curl/wget — those may BE the PoC action
# (an exploit request against the target), not a tooling download; rewriting them is unsafe.
_INSTALL_RE = re.compile(
    r"(?:sudo\s+)?(?:go\s+(?:install|get)|git\s+clone|pip[0-9]?\s+install|pipx\s+install|"
    r"python[0-9.]*\s+-m\s+pip\s+install|npm\s+(?:install|i|ci)|yarn\s+add|pnpm\s+add|"
    r"gem\s+install|cargo\s+install|apt(?:-get)?\s+install|yum\s+install|dnf\s+install|"
    r"brew\s+install)\b[^\n`]*", re.I)
_AIRGAP_INSTALL_NOTE = "công cụ đã có sẵn cục bộ (môi trường air-gap)"


def _basename_ext(path: str) -> str:
    tail = path.rstrip("/").rsplit("/", 1)[-1]
    return tail.rsplit(".", 1)[-1].lower() if "." in tail else ""


def sanitize_airgap_runguide(text: str, cve_id: str | None = None) -> str:
    """Deterministically remove internet/upstream-path leaks from the MODEL's run guide
    (Mục 2/3) — a backstop for when the exploit prompt's air-gap rules don't hold (model
    non-determinism, same reason enforce_section1 doesn't trust the model). It:
      1. rewrites source URLs (github/raw/gitlab/exploit-db/…) to the local `<cve>.<ext>`;
      2. flattens upstream template/repo paths carrying the CVE id (e.g.
         `javascript/cves/2026/CVE-….yaml`) and normalizes bare `CVE-….yaml` to `<cve>.<ext>`;
      3. rewrites any remaining `-t <path|url>` template arg to `<cve>.yaml`;
      4. neutralizes package-manager/`git clone` install commands (tools are pre-present).
    It never touches curl/wget (those can be the PoC itself) and is meant to run on the model
    text BEFORE Section-1 injection, so the real injected PoC code is left untouched.
    Target URLs like `http://<target>:<port>` are preserved (not a code host, no source ext)."""
    if not text:
        return text
    stem = (cve_id or "").strip().lower()

    def _local(ext: str) -> str:
        ext = (ext or "").lower()
        if stem:
            return f"{stem}.{ext}" if ext else stem
        return f"poc.{ext}" if ext else "poc"

    def _url_sub(m: "re.Match[str]") -> str:
        url = m.group(0)
        host_m = re.match(r"https?://([^/\s]+)", url, re.I)
        host = host_m.group(1) if host_m else ""
        ext = _basename_ext(url.split("://", 1)[-1])
        if _CODE_HOST_RE.search(host) or ext in _SRC_EXTS:
            return _local(ext if ext in _SRC_EXTS else "")
        return url  # target URL / non-source host — leave as-is

    def _cve_sub(m: "re.Match[str]") -> str:
        ext = m.group(2).lower()
        return f"{m.group(1).lower()}.{ext}" if ext in _SRC_EXTS else m.group(0)

    def _dash_t_sub(m: "re.Match[str]") -> str:
        tok = m.group(2)
        if "://" in tok or ("/" in tok and _basename_ext(tok) in {"yaml", "yml", "json"}):
            return f"{m.group(1)}{_local('yaml')}"
        return m.group(0)

    text = _URL_RE.sub(_url_sub, text)
    text = _CVE_PATH_RE.sub(_cve_sub, text)
    text = _CVE_FILE_RE.sub(_cve_sub, text)
    text = _DASH_T_RE.sub(_dash_t_sub, text)
    text = _INSTALL_RE.sub(_AIRGAP_INSTALL_NOTE, text)
    return text


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


def enforce_reference_code(text: str, evidence: list[dict[str, Any]],
                           cve_id: str | None = None) -> tuple[str, dict[str, Any]]:
    """Place the canonical fetched code as Section 1 for this run.

    Returns (rewritten text, report). `report["reference_code_injected"]` is False (with a
    `reason`) when the run gathered no real evidence to place."""
    refs = [e for e in (evidence or []) if _blocks_for(e)]
    if not refs:
        return text, {"reference_code_injected": False, "reason": "no_source_code"}
    canonical = "\n\n".join(_blocks_for(e, cve_id) for e in refs)
    new_text = _place_section1(text, canonical)
    n_files = sum(len(e.get("source_files") or [None]) for e in refs)
    return new_text, {
        "reference_code_injected": True,
        "refs": len(refs),
        "files": n_files,
        "sha256": [e.get("sha256") for e in refs if e.get("sha256")],
    }


def force_no_poc_notice(text: str, evidence: list[dict[str, Any]],
                        notice: str = _NO_POC_NOTE) -> tuple[str, dict[str, Any]]:
    """Place the canonical "no PoC info" sentence as Section 1 when the run gathered no
    real evidence — unconditionally, regardless of anything the model wrote (fabricated
    code, inconsistently-worded honesty, or nothing). Only call this when
    `enforce_reference_code` did NOT already place real evidence.

    `notice` overrides the default sentence (e.g. to name a specific CVE that isn't in the
    KB at all) — still enforced unconditionally, idempotency checked against whatever
    string is passed."""
    if any(_blocks_for(e) for e in (evidence or [])):
        return text, {"no_poc_forced": False, "reason": "has_real_evidence"}
    bounds = _section1_bounds(text or "")
    if bounds:
        body_start, body_end = bounds
        if (text[body_start:body_end] or "").strip() == notice:
            return text, {"no_poc_forced": False, "reason": "already_canonical"}
    elif (text or "").lstrip().startswith(f"{_SECTION1_HEADING}\n\n{notice}"):
        return text, {"no_poc_forced": False, "reason": "already_canonical"}
    new_text = _place_section1(text, notice)
    return new_text, {"no_poc_forced": True}


def enforce_section1(text: str, evidence: list[dict[str, Any]], *,
                     cve_known: bool = False, cve_id: str | None = None) -> tuple[str, dict[str, Any]]:
    """4-tier dispatch for Section 1, replacing the old binary
    `enforce_reference_code` → `force_no_poc_notice` fallback with two grounded synthesis
    tiers in between. The system decides the tier from measured evidence/CVE-KB state —
    never from what the model claims — matching this module's existing "don't trust the
    model to self-classify" philosophy (see prefetch.py's rationale).

    T1 real code    : `evidence` has real `source_code`/`source_files` → delegates to
                       `enforce_reference_code` unchanged, discarding anything the model wrote.
    T2 readme/desc   : no real code, but a catalog entry has real description/README text
                       (`_description_for`) → keeps the model's own `## 1.` body (it was told,
                       for this case only, to write one) with a mandatory warning banner
                       force-prepended.
    T3 cve-only      : no catalog evidence at all, but `cve_known` (confirmed via the CVE KB)
                       → same as T2, model writes from the CVE definition instead.
    synthesis_failed : T2/T3 evidence exists (readme text or a confirmed CVE) but the model
                       wrote no usable Section 1 anyway — fail-closed, but the message must
                       NOT claim the CVE is unknown (it demonstrably isn't: Mục 3 may already
                       show real CWE/CVSS/CAPEC data from the same tool calls).
    not_found        : no evidence and no confirmed CVE at all → the canonical notice,
                       naming `cve_id` when one was identified.
    """
    text = text or ""
    text, report = enforce_reference_code(text, evidence, cve_id=cve_id)
    if report.get("reference_code_injected"):
        # Consolidated tier: the injected code is a real fetched PoC merged into one runnable
        # file (poc/consolidate.py), shown with the LIGHT banner (not SYNTHESIZED). Provenance
        # (sha256/url) already sits in `report`; flag it so the FE/metadata can label it.
        consolidated = any(
            e.get("consolidated") for e in (evidence or []) if _blocks_for(e))
        report["section1_tier"] = "real_consolidated" if consolidated else "real"
        if consolidated:
            report["consolidated"] = True
        return text, report

    readme_refs = [e for e in (evidence or []) if _description_for(e)]
    if readme_refs or cve_known:
        bounds = _section1_bounds(text)
        body = (text[bounds[0]:bounds[1]] if bounds else "").strip()
        if body:
            tier = "synthesized_readme" if readme_refs else "synthesized_cve"
            # Idempotency guard: a second pass over text this function already produced sees
            # its own banner as the start of `body` — re-wrapping would nest the banner twice.
            new_text = text if body.startswith(_SYNTHESIS_BANNER) else \
                _place_section1(text, f"{_SYNTHESIS_BANNER}\n\n{body}")
            new_text = _append_synthesis_footer(new_text, evidence)
            return new_text, {**report, "section1_tier": tier, "synthesized": True}
        text, fallback = force_no_poc_notice(text, evidence, notice=_SYNTHESIS_FAILED_NOTE)
        report.update(fallback)
        report["section1_tier"] = "synthesis_failed"
        return text, report

    notice = f"CVE {cve_id} chưa có trong dữ liệu nội bộ." if cve_id else _NO_POC_NOTE
    text, fallback = force_no_poc_notice(text, evidence, notice=notice)
    report.update(fallback)
    report["section1_tier"] = "not_found"
    return text, report
