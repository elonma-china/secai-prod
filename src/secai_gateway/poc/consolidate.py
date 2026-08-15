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

import asyncio
import logging
import re
from typing import Any

from ..config import get_settings

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


def _ordered_files(ev: dict[str, Any]) -> list[dict[str, Any]]:
    """Entry file first, support files next, README last (it feeds run-instructions, not code)."""
    return sorted(ev.get("source_files") or [],
                  key=lambda f: ((f.get("role") or "") == "readme", (f.get("role") or "") != "entry"))


def _entry_code(ev: dict[str, Any]) -> str:
    """The single most important file's full code — the entry PoC (or the lone source_code).
    Used to decide whether consolidation is even viable within the model's source budget."""
    files = _ordered_files(ev)
    if files:
        return str(files[0].get("code") or "")
    return str(ev.get("source_code") or "")


def _gather_source(ev: dict[str, Any], budget: int = _MAX_SOURCE_CHARS) -> str:
    """The full source of one real-code evidence entry as a labelled blob bounded by ``budget``
    chars — entry file first (so if anything is dropped it's a support/README file, never the
    entry PoC). Per non-entry file is additionally capped so one file can't eat the whole pool."""
    files = ev.get("source_files")
    parts: list[str] = []
    used = 0
    if files:
        ordered = _ordered_files(ev)
        for i, f in enumerate(ordered):
            code = f.get("code") or ""
            if not code or used >= budget:
                continue
            per_file = budget - used if i == 0 else min(_MAX_FILE_CHARS, budget - used)
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


def validate_runnable(code: str, language: str, cve_id: str | None = None) -> bool:
    """Cheap, EXECUTION-FREE sanity check that the consolidated file is well-formed. Python is
    syntax-checked with ``compile`` (compiles, never runs) PLUS an AST scan rejecting
    package-relative imports (see ``_has_relative_import`` — a class of guaranteed-crash bug
    ``compile()`` cannot see, since the import statement itself is syntactically valid). Other
    languages can't be validated cheaply offline → accept as-is (the light banner already flags
    it as unverified). Empty code always fails."""
    if not code or not code.strip():
        return False
    if language in ("python", "py"):
        name = f"{(cve_id or 'poc').strip().lower()}.py"
        try:
            compile(code, name, "exec")
        except SyntaxError as exc:
            logger.info("consolidated PoC failed python syntax check: %s", exc)
            return False
        if _has_relative_import(code):
            logger.info("consolidated PoC for %s kept a relative import — guaranteed crash "
                        "standalone, rejecting", cve_id)
            return False
    return True


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
    entry_len = len(_entry_code(ev))
    if entry_len > budget:
        logger.info("skip consolidation for %s: entry PoC %d chars > source budget %d chars",
                    cve_id, entry_len, budget)
        return None
    source = _gather_source(ev, budget)
    if not source.strip():
        return None
    prompt = build_consolidation_prompt(ev, cve_id, budget)
    timeout_s = get_settings().poc_consolidate_timeout_s
    try:
        text, _meta = await asyncio.wait_for(
            runner.complete("poc_consolidate", prompt), timeout=timeout_s)
    except asyncio.TimeoutError:
        logger.warning("poc consolidation LLM call timed out (%ss) for %s", timeout_s, cve_id)
        return None
    except Exception:
        logger.exception("poc consolidation LLM call failed for %s", cve_id)
        return None
    parsed = extract_code_block(text or "")
    if not parsed:
        return None
    code, lang = parsed
    language = lang or _entry_language(ev) or ""
    if not validate_runnable(code, language, cve_id):
        return None
    # Provenance: keep the ORIGINAL artifact's hash/url so metadata still points at the real
    # source, even though Mục 1 now shows the AI-merged file. `sha256` is preserved but the
    # entry is flagged `consolidated` so _fullen_evidence_for_display won't re-read the verbatim
    # bytes over our merged code.
    orig_files = [f.get("path") for f in (ev.get("source_files") or []) if f.get("path")]
    return {
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
        "consolidated": True,
        "consolidated_from": {
            "sha256": ev.get("sha256"),
            "url": ev.get("url"),
            "source": ev.get("source"),
            "files": orig_files,
        },
        "static_indicators": {"language": language or None, "consolidated": True},
    }
