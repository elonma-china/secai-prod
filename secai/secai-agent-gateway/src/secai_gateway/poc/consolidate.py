"""LLM consolidation of a fetched PoC into ONE self-contained, runnable file.

The exploit endpoint used to inject a fetched PoC's real bytes VERBATIM as Section 1 (a
whole multi-file tree for repo PoCs). This step, run deterministically on the request path
BEFORE the model writes Mục 2/3, merges that source into a single runnable file via a
no-tool `poc_consolidate` profile (see prompts/consolidate.md). The result replaces the
verbatim dump as Mục 1 — carrying a LIGHT "đã được AI hợp nhất" banner (poc/inject.py) and
keeping the original sha256/URL as provenance in metadata, NOT the strong SYNTHESIZED banner
(the code is still grounded in the real fetched PoC, only reshaped).

Fail-open by construction: any miss (feature off, no code, empty/invalid model output, LLM
error) returns ``None`` so the caller falls back to the unchanged verbatim T1 injection.
Nothing here executes code — Python output is syntax-checked with ``compile`` (which does
NOT run it); other languages are passed through unvalidated.
"""
from __future__ import annotations

import ast
import asyncio
import logging
import os
import re
import shutil
import subprocess
import tempfile
from typing import Any

from ..config import get_settings

from .fidelity import fidelity_failure

logger = logging.getLogger(__name__)

# Source-budget bounds. The ACTUAL budget is derived per-request from the model's context
# window (`_source_budget`) so a big repo can't overflow a small model; these only floor/ceil
# that computation. The ceiling also bounds latency on a huge-context model (DeepSeek 131k) —
# we never feed a 400 KB repo in full even when it would "fit".
_MIN_SOURCE_CHARS = 8_000
_MAX_SOURCE_CHARS = 120_000
_MAX_FILE_CHARS = 24_000        # per non-entry file, so one support file can't eat the pool
# Conservative chars-per-token for source code (real code tokenizes at ~3.5–4.0 chars/token;
# using 3.0 UNDER-estimates the char budget → we send fewer chars than the token math allows,
# leaving headroom). Reserve output room for the merged file + a fixed prompt/system overhead.
_CHARS_PER_TOKEN = 3.0
_RESERVE_OUTPUT_TOKENS = 12_000  # room for the generated single file (usually ≤ the input)
_RESERVE_OVERHEAD_TOKENS = 1_500  # consolidate.md system prompt + wrapper + dated prefix


def _active_context_window(settings: Any) -> int:
    """The context window (tokens) of the model the runner will actually use — DeepSeek vs the
    default vLLM/Qwen path, mirroring agent/providers.build_llm_provider's selection."""
    provider = str(getattr(settings, "llm_provider", "") or "").lower()
    if provider == "deepseek":
        return int(getattr(settings, "deepseek_context_window", 131072) or 131072)
    return int(getattr(settings, "qwen_context_window", 32768) or 32768)


def _source_budget(settings: Any) -> int:
    """Max source chars to feed the consolidation LLM, derived from the model context window so
    input + generated file + prompt overhead all fit. Clamped to [_MIN, _MAX] chars."""
    ctx = _active_context_window(settings)
    reserve_output = min(ctx // 2, _RESERVE_OUTPUT_TOKENS)
    avail_tokens = ctx - reserve_output - _RESERVE_OVERHEAD_TOKENS
    chars = int(max(0, avail_tokens) * _CHARS_PER_TOKEN)
    return max(_MIN_SOURCE_CHARS, min(chars, _MAX_SOURCE_CHARS))

# language -> canonical extension (mirrors poc/inject.py _LANG_EXT; kept local to avoid a
# cross-module private import). Only used to name the file passed to `compile`.
_LANG_EXT = {
    "python": "py", "py": "py", "ruby": "rb", "rb": "rb", "php": "php", "javascript": "js",
    "js": "js", "typescript": "ts", "go": "go", "rust": "rs", "c": "c", "cpp": "cpp",
    "java": "java", "perl": "pl", "bash": "sh", "sh": "sh", "powershell": "ps1",
}
# fenced-block language hint -> our canonical language name
_FENCE_LANG = {
    "python": "python", "py": "python", "python3": "python", "ruby": "ruby", "rb": "ruby",
    "php": "php", "javascript": "javascript", "js": "javascript", "node": "javascript",
    "typescript": "typescript", "ts": "typescript", "go": "go", "golang": "go",
    "rust": "rust", "rs": "rust", "c": "c", "cpp": "cpp", "c++": "cpp", "java": "java",
    "perl": "perl", "pl": "perl", "bash": "bash", "sh": "bash", "shell": "bash",
    "powershell": "powershell", "ps1": "powershell",
}

_FENCE_RE = re.compile(r"```([A-Za-z0-9_+.\-]*)[ \t]*\r?\n(.*?)```", re.S)


def _entry_language(ev: dict[str, Any]) -> str:
    """Best-guess canonical language of a real-code evidence entry (from its own field, its
    entry file's language, or the static-indicator language)."""
    lang = str(ev.get("language") or "").strip().lower()
    if not lang:
        for f in ev.get("source_files") or []:
            if (f.get("role") or "") == "entry" or f is (ev.get("source_files") or [None])[0]:
                lang = str(f.get("language") or "").strip().lower()
                break
    if not lang:
        lang = str((ev.get("static_indicators") or {}).get("language") or "").strip().lower()
    return _FENCE_LANG.get(lang, lang)


def _ordered_files(ev: dict[str, Any], needed: set[str] | None = None) -> list[dict[str, Any]]:
    """Entry file first, then support files the entry *imports* (``needed`` — a hard dependency
    that must survive the budget cut), then the remaining support files, README last (it feeds
    run-instructions, not code)."""
    needed = needed or set()

    def key(f: dict[str, Any]) -> tuple[int, int, int]:
        role = f.get("role") or ""
        name = os.path.splitext(os.path.basename(f.get("path") or ""))[0]
        if role == "readme":
            return (3, 0, 0)
        if role == "entry":
            return (0, 0, 0)
        return (1 if name in needed else 2, 0, 0)

    return sorted(ev.get("source_files") or [], key=key)


def _entry_code(ev: dict[str, Any]) -> str:
    """The single most important file's full code — the entry PoC (or the lone source_code).
    Used to decide whether consolidation is even viable within the model's source budget."""
    files = _ordered_files(ev)
    if files:
        return str(files[0].get("code") or "")
    return str(ev.get("source_code") or "")


def readme_text(ev: dict[str, Any]) -> str:
    """The README/usage text of one real-code evidence entry, if any — the author's own
    run-instructions (setup, CLI examples, ports, prerequisites). This is the ground truth for
    Mục 2 (Cách chạy PoC); it survives consolidation so the appendix can feed it to the model
    verbatim instead of letting the model guess flags from code (it can't see READMEs that were
    only used during the merge). Empty when the entry has no README file."""
    parts: list[str] = []
    for f in ev.get("source_files") or []:
        if (f.get("role") or "") == "readme" and f.get("code"):
            parts.append(str(f["code"]))
    return "\n\n".join(parts).strip()


def _sibling_basenames(ev: dict[str, Any]) -> set[str]:
    """Basenames (no dir, no ext) of every NON-entry file in the PoC set — the names an entry
    ``import``/``from … import`` could resolve to a same-repo module (mirrors slicer's sibling
    set so the two paths agree on what counts as a local dependency)."""
    return {
        os.path.splitext(os.path.basename(f.get("path") or ""))[0]
        for f in (ev.get("source_files") or []) if (f.get("role") or "") != "entry"
    }


_SIBLING_RISKY_CALLS = {
    "run", "call", "check_call", "check_output", "Popen",  # subprocess.*
    "system", "popen",                                     # os.*
    "open", "exec",                                        # builtins
}


def _sibling_string_refs(code: str, sibling_basenames: set[str]) -> set[str]:
    """Same-repo siblings the entry file depends on via a STRING LITERAL passed to
    ``subprocess.*``/``os.system``/``os.popen``/``open``/``exec`` — a sibling script or data
    file executed or read by path, not imported as a Python module. Any of these is a hard
    dependency exactly like an ``import``: ``subprocess.run(["python3","listener.py"])``,
    ``os.system(f"python3 {x}.py")``, ``open("payload.json")``,
    ``exec(open("helper.py").read())``. Completely invisible to an AST-import scan, so without
    this an entry file referencing it slices/merges as if self-contained while the sibling
    never ships — a *guaranteed* runtime failure (FileNotFoundError / missing companion
    process) on an otherwise byte-perfect consolidation (2026-08 research finding
    silent-fidelity-loss/missing-file-dependency). Substring match on purpose (conservative:
    a false positive here only costs a wider dependency net / a fallback to the slower path,
    never a silent drop — the same fail-open-toward-safety bias as the rest of this module).
    Only catches a DIRECT string literal, not one assembled from an f-string variable/
    concatenation at runtime — same conservative-miss trade-off as everything else here."""
    if not code or not sibling_basenames:
        return set()
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return set()
    needed: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        call_name = func.attr if isinstance(func, ast.Attribute) else (
            func.id if isinstance(func, ast.Name) else "")
        if call_name not in _SIBLING_RISKY_CALLS:
            continue
        for arg in list(node.args) + [kw.value for kw in node.keywords]:
            for sub in ast.walk(arg):
                if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
                    for base in sibling_basenames:
                        if base and base in sub.value:
                            needed.add(base)
    return needed


def _imported_sibling_names(code: str, sibling_basenames: set[str]) -> set[str]:
    """Which same-repo siblings the entry file actually depends on — via ``import``/``from …
    import`` (``import utils`` / ``from utils.menu_functions import x`` where ``utils``
    matches a sibling basename) OR via a string-literal path reference (see
    ``_sibling_string_refs``). These are hard dependencies: dropping their source from the feed
    would make the LLM invent their contents (a silent semantic drift, the CVE-2026-45504 class
    of bug) or, for the string-literal case, ship a file that FileNotFoundErrors at runtime.
    Package-relative imports (``from . import x``) can't be resolved to one basename → return
    the WHOLE sibling set (all support files are treated as needed)."""
    if not code or not sibling_basenames:
        return set()
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return set()
    needed: set[str] = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.ImportFrom):
            if n.level and n.level > 0:
                return set(sibling_basenames)  # `from . import x` — can't tell which file
            if n.module and n.module.split(".")[0] in sibling_basenames:
                needed.add(n.module.split(".")[0])
        elif isinstance(n, ast.Import):
            for a in n.names:
                if a.name.split(".")[0] in sibling_basenames:
                    needed.add(a.name.split(".")[0])
    needed |= _sibling_string_refs(code, sibling_basenames)
    return needed


def _gather_source(ev: dict[str, Any], budget: int = _MAX_SOURCE_CHARS,
                   needed: set[str] | None = None) -> str:
    """The full source of one real-code evidence entry as a labelled blob bounded by ``budget``
    chars — entry file first, then imported siblings (``needed``), then other support files.
    Imported siblings are fed in FULL (never per-file capped): a dropped dependency is what
    makes the merge hallucinate, so they take priority over unrelated support/README. Per
    non-imported support file is capped so one file can't eat the whole pool."""
    needed = needed or set()
    files = ev.get("source_files")
    parts: list[str] = []
    used = 0
    if files:
        ordered = _ordered_files(ev, needed)
        for i, f in enumerate(ordered):
            code = f.get("code") or ""
            if not code or used >= budget:
                continue
            name = os.path.splitext(os.path.basename(f.get("path") or ""))[0]
            is_entry = i == 0
            is_needed = name in needed
            if is_entry or is_needed:
                per_file = budget - used  # entry + imported deps: feed full, never cap
            else:
                per_file = min(_MAX_FILE_CHARS, budget - used)
            take = code[:per_file]
            parts.append(f"===== FILE: {f.get('path', 'poc')} =====\n{take}")
            used += len(take)
    else:
        code = ev.get("source_code") or ""
        if code:
            label = ev.get("source_ref") or ev.get("url") or "poc"
            parts.append(f"===== FILE: {label} =====\n{code[:budget]}")
    return "\n\n".join(parts)


def build_consolidation_prompt(ev: dict[str, Any], cve_id: str | None,
                               budget: int = _MAX_SOURCE_CHARS) -> str:
    """The user message for the `poc_consolidate` profile: the CVE id + the full PoC source
    (bounded by ``budget`` chars), with the one instruction the system prompt expands on."""
    cve = (cve_id or "").strip().upper() or "(không xác định)"
    lang = _entry_language(ev) or "(theo mã gốc)"
    source = _gather_source(ev, budget)
    return (
        f"CVE: {cve}\nNgôn ngữ PoC: {lang}\n\n"
        "Dưới đây là toàn bộ mã nguồn PoC công khai đã fetch (có thể nhiều tệp). Hãy hợp nhất "
        "thành ĐÚNG một tệp độc lập, gọn, chạy được theo đúng các quy tắc — chỉ trả về một khối "
        "mã duy nhất, không kèm chữ nào khác.\n\n"
        f"{source}"
    )


def extract_code_block(text: str) -> tuple[str, str] | None:
    """The single fenced code block the consolidation profile is told to emit → (code, lang).
    Picks the LARGEST fenced block (the model may wrap a stray note in a tiny inline fence).

    Returns ``None`` when there is NO fenced block — deliberately strict: a reply that is prose
    (an apology, an explanation, a refusal) must fall back to the verbatim real PoC rather than
    be mistaken for code and injected as Section 1. Same "never put ungrounded text in Section
    1" stance as the rest of poc/inject.py."""
    if not text:
        return None
    blocks = _FENCE_RE.findall(text)
    if not blocks:
        return None
    hint, code = max(blocks, key=lambda b: len(b[1]))
    code = code.strip("\n")
    if not code.strip():
        return None
    lang = _FENCE_LANG.get((hint or "").strip().lower(), (hint or "").strip().lower())
    return code, lang


def _has_relative_import(code: str) -> bool:
    """True when ``code`` contains a package-relative import (``from . import x``, ``from
    .module import x``) — syntactically valid Python, so ``compile()`` never catches it, but
    GUARANTEED to raise ``ImportError: attempted relative import with no known parent package``
    the moment the merged file is run standalone (there is no package left after merging into
    one file). Confirmed live: a wp2shell-derived consolidation kept ``from . import
    __version__`` from the original package's ``cli.py`` and crashed immediately on execution.
    ``ast.parse`` only builds a syntax tree — this never executes the code."""
    import ast
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return False  # compile() below reports the real syntax error; not this check's job
    return any(isinstance(n, ast.ImportFrom) and n.level > 0 for n in ast.walk(tree))


def _undefined_names(code: str) -> set[str]:
    """Names that are *loaded* but never bound anywhere in the file (minus builtins/dunders).

    A consolidated single file is self-contained by construction, so a loaded-but-unbound name
    is almost always a real runtime ``NameError`` introduced by the merge — a dropped ``import``
    (e.g. ``import time``), an inconsistently renamed module constant, or a referenced function
    parameter that was dropped. Detected live: a wp2shell merge lost ``import time`` (→
    ``name 'time' is not defined``) and renamed ``POSTS_ITEM_SOURCE_PATH`` at its definition
    only (→ ``name 'POSTS_ITEM_SOURCE_PATH' is not defined``). Both are syntactically valid, so
    ``compile()`` cannot see them. AST walk only — never executes code.

    Deliberately conservative: a name that is only ever bound through ``exec``/``eval`` or a
    star import is NOT reliably detectable; those are rare in PoC scripts and the miss merely
    lets a broken file through (same as today) rather than falsely rejecting a good one."""
    import ast
    import builtins
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return set()
    bound: set[str] = set()
    used: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                bound.add((a.asname or a.name).split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            for a in node.names:
                if a.name != "*":
                    bound.add(a.asname or a.name)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            bound.add(node.name)
        elif isinstance(node, ast.arg):  # Python 3.8+ function params are ast.arg, not Name
            bound.add(node.arg)
        elif isinstance(node, ast.ExceptHandler):  # `except X as exc` binds `exc` (a str field)
            if node.name:
                bound.add(node.name)
        elif isinstance(node, ast.MatchAs):  # pattern matching `case x as y` (Py 3.10+)
            if node.name:
                bound.add(node.name)
        elif isinstance(node, ast.MatchStar):  # `case *rest`
            if node.name:
                bound.add(node.name)
        elif isinstance(node, ast.MatchMapping):  # `case {"k": v, **rest}`
            if node.rest:
                bound.add(node.rest)
        elif isinstance(node, ast.Name):
            if isinstance(node.ctx, ast.Load):
                used.add(node.id)
            else:  # Store / Del / Param
                bound.add(node.id)
    builtins_names = set(dir(builtins)) | {
        "__name__", "__file__", "__doc__", "__package__", "__spec__", "__loader__",
        "__builtins__", "__annotations__", "__debug__", "__cached__",
    }
    return {n for n in used - bound - builtins_names if not n.startswith("__")}


# Execution-free syntax-only check (compile/parse, no `-e`/eval, nothing executed) per
# non-Python language the pipeline can produce (source= metasploit -> ruby, ghsa/vulhub PoCs
# -> php/js/bash/perl are all real, live sources — see tests/test_multifile.py's companion-doc
# tests). Each entry is (interpreter binary, args to append the code file path to). Fail-open
# by design (see the try/except in `validation_failure` below) when the interpreter binary
# isn't installed — matching this whole module's "any check miss -> accept, never a false
# reject" philosophy. IMPORTANT DEPLOYMENT CAVEAT: the shipped Dockerfile's runtime image
# (python:3.12-slim, `apt-get install bash tini` only) does NOT include ruby/php/node/perl —
# only the `bash` entry has real effect there today; the other four are genuinely inert until
# the image is rebuilt with those interpreters installed. Left in (rather than only shipping
# bash) so enabling the rest later is a Dockerfile change alone, no code change (2026-08
# research finding missing-validation/non-Python).
_SYNTAX_CHECK_CMDS: dict[str, tuple[str, ...]] = {
    "rb": ("ruby", "-c"), "ruby": ("ruby", "-c"),
    "php": ("php", "-l"),
    "sh": ("bash", "-n"), "bash": ("bash", "-n"),
    "js": ("node", "--check"),
    "pl": ("perl", "-c"),
}


def _non_python_syntax_failure(code: str, language: str, cve_id: str | None) -> str | None:
    """Best-effort syntax-only check for a non-Python language via the matching interpreter's
    check-only flag (see ``_SYNTAX_CHECK_CMDS``) — never executes the code. Returns ``None``
    (accepted) when the language has no known checker, the interpreter binary isn't installed,
    or any other transport issue occurs — a miss here degrades to today's prior behaviour
    (accept), never a false reject."""
    cmd = _SYNTAX_CHECK_CMDS.get((language or "").strip().lower())
    if cmd is None:
        return None
    binary = cmd[0]
    if shutil.which(binary) is None:
        return None  # interpreter not present in this image — fail-open, not a reject
    suffix = "." + (language if language not in ("ruby",) else "rb")
    try:
        with tempfile.NamedTemporaryFile("w", suffix=suffix, delete=False, encoding="utf-8") as f:
            f.write(code)
            path = f.name
        result = subprocess.run(
            [*cmd, path], capture_output=True, text=True, timeout=10)
    except Exception:
        return None
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip().splitlines()
        first_line = detail[0] if detail else f"exit code {result.returncode}"
        return f"lỗi cú pháp {language}: {first_line}"
    return None


def validation_failure(code: str, language: str, cve_id: str | None = None) -> str | None:
    """The single EXECUTION-FREE check on a consolidated file. Returns ``None`` when it passes,
    else a short Vietnamese reason naming the exact defect — usable both for logging and as
    corrective feedback on a consolidation retry (see ``consolidate_poc``).

    Python is syntax-checked with ``compile`` (compiles, never runs) PLUS an AST scan rejecting
    package-relative imports (``_has_relative_import`` — a guaranteed-crash bug ``compile()``
    can't see, the import being syntactically valid) and loaded-but-unbound names
    (``_undefined_names`` — dropped imports / renamed constants). Other languages get a
    best-effort syntax-only check via the matching interpreter's check flag when available
    (see ``_non_python_syntax_failure`` — fail-open when the interpreter isn't installed, so
    this can only ever catch MORE than before, never reject something the old accept-everything
    behaviour would have let through). Empty code always fails."""
    if not code or not code.strip():
        return "tệp rỗng"
    if language in ("python", "py"):
        name = f"{(cve_id or 'poc').strip().lower()}.py"
        try:
            compile(code, name, "exec")
        except SyntaxError as exc:
            return f"lỗi cú pháp Python: {exc.msg} (dòng {exc.lineno})"
        if _has_relative_import(code):
            return ("còn import tương đối kiểu package (vd `from . import x`) — tệp hợp nhất là "
                    "một script độc lập nên sẽ lỗi ImportError ngay khi chạy; hãy nội tuyến giá "
                    "trị/hàm đó thay vì giữ câu import")
        undef = _undefined_names(code)
        if undef:
            return ("dùng tên chưa được định nghĩa/import trong tệp: " + ", ".join(sorted(undef))
                    + " — thường do gộp làm rớt một `import` hoặc đổi tên hằng/biến ở nơi định "
                    "nghĩa nhưng không đổi ở nơi dùng; hãy đảm bảo mọi tên đều có định nghĩa/import")
        return None
    return _non_python_syntax_failure(code, language, cve_id)


def validate_runnable(code: str, language: str, cve_id: str | None = None) -> bool:
    """Boolean gate over ``validation_failure`` (kept for existing callers/tests). Logs the
    named defect when it rejects."""
    reason = validation_failure(code, language, cve_id)
    if reason is not None:
        logger.info("consolidated PoC for %s rejected: %s", cve_id, reason)
        return False
    return True


# Corrective suffix appended to the consolidation prompt on a retry. Names the exact defect
# the execution-free validator found in the previous attempt (dropped import, undefined name,
# syntax error, relative import, missing code block) so the model fixes THAT rather than
# re-rolling blindly. The base prompt (consolidate.md) already carries every merge rule; this
# only points at what went wrong last time.
_RETRY_FEEDBACK = (
    "LƯU Ý SỬA LỖI: bản hợp nhất TRƯỚC của bạn bị loại vì {reason}. Hãy sinh lại TOÀN BỘ tệp và "
    "sửa đúng lỗi này, vẫn tuân thủ mọi quy tắc ở trên (một tệp độc lập chạy được, giữ NGUYÊN mọi "
    "giá trị kỹ thuật thật trong mã gốc, không bịa thêm). Chỉ trả về DUY NHẤT một khối mã."
)


async def consolidate_poc(runner: Any, real_evidence: list[dict[str, Any]],
                          cve_id: str | None) -> dict[str, Any] | None:
    """Merge the best real-code evidence entry into ONE runnable file via the LLM, returning a
    synthetic evidence dict (``consolidated=True``) ready for Section-1 injection — or ``None``
    on any miss (caller falls back to verbatim). Consolidates the FIRST real-code entry only
    (already ranked GitHub/exploit-first); it never fuses two unrelated PoCs together."""
    if not real_evidence:
        return None
    ev = real_evidence[0]
    # Context-aware source budget: never feed the LLM more than its window can hold alongside
    # the generated file + prompt overhead (see _source_budget). If the ENTRY PoC alone exceeds
    # the budget, a truncated feed would produce a broken/partial merge — skip consolidation and
    # let the caller inject the verbatim PoC instead (that path doesn't go through the LLM).
    budget = _source_budget(get_settings())
    entry_code = _entry_code(ev)
    entry_len = len(entry_code)
    if entry_len > budget:
        logger.info("skip consolidation for %s: entry PoC %d chars > source budget %d chars",
                    cve_id, entry_len, budget)
        return None
    # Hard-dependency guard: support files the entry IMPORTS must be fed in full or not at all.
    # If entry + imported siblings can't fit the budget, dropping a dependency would make the
    # LLM invent its contents (silent semantic drift) — skip consolidation and fall back to
    # verbatim, exactly like the entry-over-budget case above.
    needed = _imported_sibling_names(entry_code, _sibling_basenames(ev))
    needed_chars = sum(len(f.get("code") or "") for f in (ev.get("source_files") or [])
                       if os.path.splitext(os.path.basename(f.get("path") or ""))[0] in needed)
    if entry_len + needed_chars > budget:
        logger.info("skip consolidation for %s: entry %d + imported deps %d chars > budget %d",
                    cve_id, entry_len, needed_chars, budget)
        return None
    source = _gather_source(ev, budget, needed)
    if not source.strip():
        return None
    base_prompt = build_consolidation_prompt(ev, cve_id, budget)
    settings = get_settings()
    timeout_s = settings.poc_consolidate_timeout_s
    max_attempts = max(1, int(getattr(settings, "poc_consolidate_max_attempts", 1) or 1))
    code = language = None
    prompt = base_prompt
    # The original entry source — the fidelity check compares the merged output against it so a
    # merge that renames CLI flags / drops an argparse param / hardcodes a port is rejected
    # (see poc/fidelity.py; the CVE-2026-45504 merge did all three).
    entry_source = _entry_code(ev)
    for attempt in range(1, max_attempts + 1):
        try:
            text, _meta = await asyncio.wait_for(
                runner.complete("poc_consolidate", prompt), timeout=timeout_s)
        except asyncio.TimeoutError:
            # A timeout is a transport/capacity problem, not a fidelity one — retrying would
            # just burn more of the request's time budget on the same stall. Give up now.
            logger.warning("poc consolidation timed out (%ss) for %s (attempt %d/%d)",
                           timeout_s, cve_id, attempt, max_attempts)
            return None
        except Exception:
            logger.exception("poc consolidation LLM call failed for %s (attempt %d/%d)",
                             cve_id, attempt, max_attempts)
            return None
        parsed = extract_code_block(text or "")
        if not parsed:
            reason: str | None = "không tìm thấy khối mã trong phản hồi (có thể kèm lời dẫn thừa)"
        else:
            code, lang = parsed
            language = lang or _entry_language(ev) or ""
            reason = validation_failure(code, language, cve_id)
            if reason is None:
                reason = fidelity_failure(code, entry_source)
            if reason is None:
                break  # merged file passes the execution-free + fidelity checks — done
        # Rejected. Retry with the exact defect fed back, unless this was the last attempt.
        if attempt >= max_attempts:
            logger.info("poc consolidation for %s gave up after %d attempt(s); last defect: %s",
                        cve_id, max_attempts, reason)
            return None
        logger.info("poc consolidation for %s attempt %d rejected (%s) — retrying with feedback",
                    cve_id, attempt, reason)
        prompt = f"{base_prompt}\n\n{_RETRY_FEEDBACK.format(reason=reason)}"
    if code is None or validation_failure(code, language, cve_id) is not None:
        return None
    # Provenance: keep the ORIGINAL artifact's hash/url so metadata still points at the real
    # source, even though Mục 1 now shows the AI-merged file. `sha256` is preserved but the
    # entry is flagged `consolidated` so _fullen_evidence_for_display won't re-read the verbatim
    # bytes over our merged code.
    orig_files = [f.get("path") for f in (ev.get("source_files") or []) if f.get("path")]
    result = {
        "source": ev.get("source"),
        "source_ref": ev.get("source_ref"),
        "url": ev.get("url"),
        "kind": "exploit",
        "title": ev.get("title") or f"Consolidated PoC {(cve_id or '').upper()}".strip(),
        "cve_ids": ev.get("cve_ids") or ([cve_id.upper()] if cve_id else []),
        "language": language or None,
        "source_code": code,
        "source_files": None,   # collapsed to ONE file — inject renders `source_code`
        "code_truncated": False,
        "sha256": ev.get("sha256"),
        "readme_text": readme_text(ev),
        "consolidated": True,
        "consolidated_from": {
            "sha256": ev.get("sha256"),
            "url": ev.get("url"),
            "source": ev.get("source"),
            "files": orig_files,
        },
        "static_indicators": {"language": language or None, "consolidated": True},
    }
    return result
