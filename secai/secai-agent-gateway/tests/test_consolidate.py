"""poc/consolidate.py — extract/validate + the async consolidate_poc orchestration, and the
/ai/exploit consolidated tier end-to-end (real catalog code → merged single file as Mục 1)."""
from __future__ import annotations

import asyncio
import tempfile

import httpx
import pytest

from linch.events import PartialAssistantEvent

from secai_gateway.app import create_app
from secai_gateway.poc.consolidate import (
    build_consolidation_prompt,
    consolidate_poc,
    extract_code_block,
    validate_runnable,
    validation_failure,
)

_MULTIFILE_EVIDENCE = [{
    "source": "poc_github", "kind": "exploit", "source_ref": "foo/bar/exploit.py",
    "url": "https://github.com/foo/bar/blob/main/exploit.py", "sha256": "d" * 64,
    "language": "python", "cve_ids": ["CVE-2026-31431"],
    "source_files": [
        {"path": "exploit.py", "role": "entry", "language": "python",
         "code": "from helper import build\nprint(build())\n"},
        {"path": "helper.py", "role": "support", "language": "python",
         "code": "def build():\n    return 'payload'\n"},
        {"path": "README.md", "role": "readme", "code": "# run: python exploit.py\n"},
    ],
}]


# ── extract_code_block ──────────────────────────────────────────────────────────
def test_extract_picks_largest_fenced_block_and_language() -> None:
    text = ("Đây là kết quả:\n```py\nx=1\n```\nvà giải thích\n"
            "```python\nimport os\nprint(os.getcwd())\n```\n")
    code, lang = extract_code_block(text)
    assert lang == "python"
    assert "import os" in code
    assert "x=1" not in code  # the smaller block is not chosen


def test_extract_returns_none_without_fence() -> None:
    # Prose with no code fence must NOT be treated as code (falls back to verbatim upstream).
    assert extract_code_block("Xin lỗi, tôi không thể hợp nhất mã này.") is None
    assert extract_code_block("") is None


# ── validate_runnable ───────────────────────────────────────────────────────────
def test_validate_python_syntax_ok_and_bad() -> None:
    assert validate_runnable("import sys\nprint(sys.argv)\n", "python", "CVE-2026-1") is True
    assert validate_runnable("def bad(:\n  pass\n", "python", "CVE-2026-1") is False  # SyntaxError


def test_validate_non_python_passthrough_and_empty() -> None:
    # ruby isn't installed on this box (nor the prod image) → the syntax gate fails open.
    assert validate_runnable("puts 'hi'", "ruby") is True
    assert validate_runnable("   ", "python") is False       # empty always fails


def test_validate_non_python_unknown_language_passthrough() -> None:
    # A language with no entry in _SYNTAX_CHECK_CMDS at all → always accepted (unchanged
    # pre-existing behaviour for languages we have no checker for).
    assert validate_runnable("<<< not really yaml but whatever", "yaml") is True


def test_validate_bash_syntax_ok_and_bad() -> None:
    # bash is present in the prod image (RUN apt-get install ... bash tini) so this is a real,
    # live check today — not just a fail-open no-op like the other interpreters below.
    assert validate_runnable("#!/bin/bash\necho hi\n", "bash") is True
    reason = validation_failure("if [ 1 -eq 1 ]; then echo missing_fi\n", "bash")
    assert reason is not None and "bash" in reason


def test_validate_php_and_js_syntax_bad_is_rejected() -> None:
    # php/node are present on THIS dev box but not (yet) in the prod Dockerfile — exercised
    # here to prove the checker logic itself is correct; see _SYNTAX_CHECK_CMDS docstring for
    # the prod-image caveat.
    import shutil

    if shutil.which("php"):
        assert validation_failure("<?php echo 'hi';\n", "php") is None
        assert validation_failure("<?php if (true) { echo 'no close'\n", "php") is not None
    if shutil.which("node"):
        assert validation_failure("console.log('hi')\n", "js") is None
        assert validation_failure("function f( {\n", "js") is not None


def test_validate_syntax_gate_fails_open_on_missing_interpreter(monkeypatch) -> None:
    from secai_gateway.poc import consolidate as consolidate_mod

    monkeypatch.setattr(consolidate_mod.shutil, "which", lambda _b: None)
    # even blatantly broken bash source is accepted once the interpreter can't be found —
    # matches this module's fail-open-on-miss philosophy everywhere else.
    assert validation_failure("if [ 1 -eq 1 ]; then echo missing_fi\n", "bash") is None


def test_validate_rejects_relative_import() -> None:
    # Syntactically valid but GUARANTEED to ImportError standalone (no parent package after
    # merging into one file) — compile() alone can't see this; confirmed live on a
    # wp2shell-derived consolidation that kept `from . import __version__` and crashed.
    bad = "from . import __version__\nprint(__version__)\n"
    assert validate_runnable(bad, "python", "CVE-2026-1") is False
    bad2 = "from .helpers import build\nbuild()\n"
    assert validate_runnable(bad2, "python") is False
    # Absolute from-imports (not relative) are fine.
    ok = "from os import path\nprint(path.exists('x'))\n"
    assert validate_runnable(ok, "python") is True


def test_build_prompt_includes_all_files_entry_first() -> None:
    prompt = build_consolidation_prompt(_MULTIFILE_EVIDENCE[0], "CVE-2026-31431")
    assert "CVE-2026-31431" in prompt
    assert "exploit.py" in prompt and "helper.py" in prompt
    # entry file appears before the support file it depends on
    assert prompt.index("exploit.py") < prompt.index("helper.py")


# ── consolidate_poc orchestration (fake single-shot runner) ─────────────────────
class _ConsolidatingRunner:
    """Returns a proper fenced single file for the poc_consolidate profile."""

    MERGED = "```python\ndef build():\n    return 'payload'\n\nprint(build())\n```"

    async def complete(self, profile_name: str, prompt: str, session_id=None):
        assert profile_name == "poc_consolidate"
        return self.MERGED, {"run_id": "c1", "profile": profile_name}


@pytest.mark.asyncio
async def test_consolidate_poc_merges_and_preserves_provenance() -> None:
    out = await consolidate_poc(_ConsolidatingRunner(), _MULTIFILE_EVIDENCE, "CVE-2026-31431")
    assert out is not None
    assert out["consolidated"] is True
    assert out["source_files"] is None          # collapsed to ONE file
    assert "def build()" in out["source_code"] and "print(build())" in out["source_code"]
    assert out["sha256"] == "d" * 64            # provenance = original artifact hash
    assert out["consolidated_from"]["files"] == ["exploit.py", "helper.py", "README.md"]
    assert out["language"] == "python"
    # README run-instructions survive the merge so Mục 2 can be grounded in the author's own
    # usage (not guessed from code) — the CVE-2026-60137/45504 failure mode.
    assert out["readme_text"] == "# run: python exploit.py"


class _NonCodeRunner:
    async def complete(self, profile_name: str, prompt: str, session_id=None):
        return "Tôi không thể hợp nhất.", {"run_id": "c2"}


@pytest.mark.asyncio
async def test_consolidate_poc_returns_none_on_non_code_reply() -> None:
    # No fenced block → None → caller falls back to verbatim injection.
    assert await consolidate_poc(_NonCodeRunner(), _MULTIFILE_EVIDENCE, "CVE-2026-31431") is None


class _HangingRunner:
    """Simulates a stuck backend call (network stall / queue starvation) — never returns."""

    async def complete(self, profile_name: str, prompt: str, session_id=None):
        import asyncio
        await asyncio.sleep(3600)  # far longer than any test timeout below
        raise AssertionError("unreachable — wait_for must cancel this first")


@pytest.mark.asyncio
async def test_consolidate_poc_times_out_instead_of_hanging(monkeypatch) -> None:
    # A stuck LLM call must not block the request indefinitely (observed: one real call hung
    # ~3h before failing). Shrink the timeout so the test itself stays fast.
    monkeypatch.setenv("POC_CONSOLIDATE_TIMEOUT_S", "1")
    out = await consolidate_poc(_HangingRunner(), _MULTIFILE_EVIDENCE, "CVE-2026-31431")
    assert out is None  # timeout → treated like any other consolidation miss


# ── over-context safety: budget scales with model window; oversized entry skips ─
def test_source_budget_scales_with_context_window() -> None:
    from types import SimpleNamespace

    from secai_gateway.poc.consolidate import _MAX_SOURCE_CHARS, _source_budget

    qwen = _source_budget(SimpleNamespace(llm_provider="vllm", qwen_context_window=32768,
                                          deepseek_context_window=131072))
    deep = _source_budget(SimpleNamespace(llm_provider="deepseek", qwen_context_window=32768,
                                          deepseek_context_window=131072))
    assert deep > qwen                      # bigger window → bigger source budget
    assert deep <= _MAX_SOURCE_CHARS        # but never above the latency ceiling
    tiny = _source_budget(SimpleNamespace(llm_provider="vllm", qwen_context_window=4096,
                                          deepseek_context_window=131072))
    assert tiny >= 8_000                    # floored so we still attempt small PoCs


@pytest.mark.asyncio
async def test_consolidate_skips_when_entry_exceeds_budget(monkeypatch) -> None:
    # A single entry file larger than the source budget must NOT be truncated-and-fed (broken
    # merge risk) — consolidate returns None so the caller injects the verbatim PoC.
    import secai_gateway.poc.consolidate as C

    monkeypatch.setattr(C, "_source_budget", lambda _s: 1000)
    huge = [{"source": "poc_github", "kind": "exploit", "source_ref": "a/b/x.py",
             "language": "python", "sha256": "f" * 64,
             "source_code": "x = 1\n" * 500}]  # ~3000 chars > 1000 budget

    class _R:
        async def complete(self, *a, **k):
            raise AssertionError("LLM must not be called when the entry exceeds budget")

    assert await consolidate_poc(_R(), huge, "CVE-2026-9") is None


# ── imported-sibling guard: never drop a dependency the entry imports ───────────
def test_imported_sibling_names_detects_dependencies() -> None:
    from secai_gateway.poc.consolidate import _imported_sibling_names

    sibs = {"utils", "helper", "requirements"}
    assert _imported_sibling_names("from utils.menu_functions import run\n", sibs) == {"utils"}
    assert _imported_sibling_names("import helper\n", sibs) == {"helper"}
    assert _imported_sibling_names("from Crypto.Cipher import AES\n", sibs) == set()
    assert _imported_sibling_names("import requests\n", sibs) == set()
    # package-relative import can't be resolved to one file → all siblings treated as needed
    assert _imported_sibling_names("from . import version\n", sibs) == sibs


def test_sibling_string_refs_detects_subprocess_and_open() -> None:
    """Regression guard for the missing-file-dependency gap (2026-08 research pass, critical
    severity): a sibling script/data file referenced by STRING LITERAL path — never an
    `import` — used to be completely invisible to _imported_sibling_names. A slice/merge that
    misses this ships a byte-perfect entry file that FileNotFoundErrors at runtime because its
    companion script/data was never included."""
    from secai_gateway.poc.consolidate import _imported_sibling_names, _sibling_string_refs

    sibs = {"listener", "payload", "config"}
    # subprocess.run with a list arg naming a sibling script
    assert _sibling_string_refs('subprocess.run(["python3", "listener.py"])\n', sibs) == {"listener"}
    # subprocess.Popen, os.system, os.popen — same class of call
    assert _sibling_string_refs('subprocess.Popen(["listener.py"])\n', sibs) == {"listener"}
    assert _sibling_string_refs('os.system("python3 listener.py")\n', sibs) == {"listener"}
    assert _sibling_string_refs('os.popen("python3 listener.py")\n', sibs) == {"listener"}
    # bare open() reading a sibling data file, incl. wrapped in exec()
    assert _sibling_string_refs('open("payload.json")\n', sibs) == {"payload"}
    assert _sibling_string_refs('exec(open("payload.json").read())\n', sibs) == {"payload"}
    # a call/name that isn't in the risky set, or a literal matching nothing, must not false-positive
    assert _sibling_string_refs('requests.get("http://x/listener.py")\n', sibs) == set()
    assert _sibling_string_refs('open("unrelated.txt")\n', sibs) == set()
    # unions correctly into the umbrella function callers actually use
    assert _imported_sibling_names('subprocess.run(["python3","listener.py"])\n', sibs) == {"listener"}


@pytest.mark.asyncio
async def test_consolidate_skips_when_imported_sibling_would_exceed_budget(monkeypatch) -> None:
    # Entry imports a huge support file; entry + imported deps exceed the budget → must skip
    # (a dropped dependency is what makes the LLM invent its contents), not feed a truncated set.
    import secai_gateway.poc.consolidate as C

    monkeypatch.setattr(C, "_source_budget", lambda _s: 500)
    huge_dep = [{
        "source": "poc_github", "kind": "exploit", "source_ref": "a/b/exploit.py",
        "language": "python", "sha256": "e" * 64,
        "source_files": [
            {"path": "exploit.py", "role": "entry", "language": "python",
             "code": "from payloads import get\nprint(get())\n"},
            {"path": "payloads.py", "role": "support", "language": "python",
             "code": "def get():\n    return 'x'\n" * 300},  # ~8100 chars >> 500 budget
        ],
    }]

    class _R:
        async def complete(self, *a, **k):
            raise AssertionError("LLM must not be called when entry+imported deps exceed budget")

    assert await consolidate_poc(_R(), huge_dep, "CVE-2026-10") is None


@pytest.mark.asyncio
async def test_consolidate_feeds_imported_sibling_in_full_within_budget() -> None:
    # When entry + imported deps DO fit, the imported sibling is fed in full (not capped) and
    # ordered right after the entry, ahead of unrelated support/README.
    import secai_gateway.poc.consolidate as C

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(C, "_MAX_FILE_CHARS", 100)  # would cap a non-imported support file
    ev = {
        "source": "poc_github", "kind": "exploit", "source_ref": "a/b/exploit.py",
        "language": "python", "sha256": "e" * 64,
        "source_files": [
            {"path": "exploit.py", "role": "entry", "language": "python",
             "code": "from payloads import get\nprint(get())\n"},
            {"path": "payloads.py", "role": "support", "language": "python",
             "code": "def get():\n    return 'x' * 2000\n"},  # imported → must be full, ~40 chars
            {"path": "unrelated.py", "role": "support", "language": "python",
             "code": "y = 1\n" * 100},                       # not imported → capped at 100
        ],
    }
    src = C._gather_source(ev, budget=1000, needed=C._imported_sibling_names(
        C._entry_code(ev), C._sibling_basenames(ev)))
    try:
        assert src.index("exploit.py") < src.index("payloads.py") < src.index("unrelated.py")
    finally:
        monkeypatch.undo()
    assert "return 'x' * 2000" in src           # imported dep is full, uncapped
    assert "y = 1\n" * 100 not in src           # unrelated support was capped


# ── on-demand temp cache (DB → temp → internet) ─────────────────────────────────
def test_ondemand_cache_roundtrip_and_default_dir(tmp_path) -> None:
    from types import SimpleNamespace

    from secai_gateway.poc.ondemand_cache import load_cached, resolve_cache_dir, save_cached

    ev = {"source": "poc_github", "source_code": "print('x')\n", "source_files": None,
          "url": "https://github.com/a/b", "sha256": "e" * 64, "language": "python"}
    cache_dir = tmp_path / "cache"
    assert load_cached(cache_dir, "CVE-2026-31431") is None      # empty → miss
    save_cached(cache_dir, "CVE-2026-31431", ev)
    got = load_cached(cache_dir, "CVE-2026-31431")
    assert got is not None and got["source_code"] == "print('x')\n"
    assert got["ondemand_cached"] is True                        # provenance breadcrumb
    # default dir derives from the artifact store's parent
    settings = SimpleNamespace(poc_ondemand_cache_dir="", poc_artifact_store="/data/poc/store")
    assert str(resolve_cache_dir(settings)).endswith("/data/poc/ondemand-cache")


def test_ondemand_cache_fail_open_on_bad_input(tmp_path) -> None:
    from secai_gateway.poc.ondemand_cache import load_cached, save_cached

    # A non-CVE key never builds a path; a description-only dict is not cached.
    save_cached(tmp_path, "not-a-cve", {"source_code": "x"})
    assert load_cached(tmp_path, "not-a-cve") is None
    save_cached(tmp_path, "CVE-2026-1000", {"description": "no code"})
    assert load_cached(tmp_path, "CVE-2026-1000") is None
    assert load_cached(None, "CVE-2026-1000") is None            # disabled → None


# ── /ai/exploit consolidated tier end-to-end ────────────────────────────────────
class _ExploitConsolidateRunner:
    """Serves poc_consolidate (merged file) and exploit_research (Mục 2/3) from an in-memory
    catalog holding one real-code GitHub PoC row for the requested CVE."""

    MERGED = "```python\nimport requests\nrequests.get('http://TARGET/x')\n```"

    def __init__(self, poc, ledger) -> None:
        self.poc = poc
        self.ledger = ledger

    async def complete(self, profile_name: str, prompt: str, session_id=None):
        if profile_name == "poc_consolidate":
            return self.MERGED, {"run_id": "cc", "profile": profile_name}
        return ("## 2. Cách chạy PoC\n\n- python3 cve-2026-31431.py\n\n"
                "## 3. Tổng quan Lỗ hổng\n\n- SSRF\n"), {"run_id": "rr", "profile": profile_name}

    async def stream(self, profile_name: str, prompt: str, session_id=None):
        text, _ = await self.complete(profile_name, prompt, session_id)
        yield PartialAssistantEvent(delta={"kind": "text", "text": text})


def _catalog_with_github_code(tmp_db: str):
    from secai_gateway.poc.backend import LocalPocBackend
    from secai_gateway.poc.catalog import PocCatalog
    from secai_gateway.poc.models import NormalizedPoc

    cat = PocCatalog(tmp_db)
    store = tempfile.mkdtemp()
    import hashlib
    import pathlib
    code = b"import requests, sys\n# noisy banner\nrequests.get(sys.argv[1])\n"
    sha = hashlib.sha256(code).hexdigest()
    p = pathlib.Path(store) / sha[:2] / sha
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(code)
    cat.upsert(NormalizedPoc(
        source="poc_github", source_ref="foo/bar/exploit.py", kind="exploit",
        title="PoC CVE-2026-31431", cve_ids=["CVE-2026-31431"], language="python",
        verified=True, rank=50, url="https://github.com/foo/bar/blob/main/exploit.py",
        artifact_sha256=sha, artifact_path=f"{sha[:2]}/{sha}",
        provenance={"stars": 10}))
    cat.commit()
    poc = LocalPocBackend(catalog=cat, source_roots={"poc_github": pathlib.Path(store)})
    return poc


@pytest.mark.asyncio
async def test_exploit_endpoint_consolidated_tier(monkeypatch) -> None:
    from secai_gateway.state import SqliteLedger

    poc = _catalog_with_github_code(tempfile.mktemp(suffix=".db"))
    ledger = SqliteLedger(tempfile.mktemp(suffix=".db"))
    runner = _ExploitConsolidateRunner(poc, ledger)
    async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=create_app(runner)), base_url="http://test") as client:
        resp = await client.post("/ai/exploit", json={"question": "CVE-2026-31431"})
    assert resp.status_code == 200
    data = resp.json()
    meta = data["metadata"]
    assert meta.get("consolidated") is True
    assert meta.get("section1_tier") == "real_consolidated"
    body = data["description"]
    assert "cve-2026-31431.py" in body                          # air-gap local name
    assert "requests.get('http://TARGET/x')" in body            # MERGED code is Section 1
    assert "hợp nhất" in body                                    # light consolidated banner
    assert "SYNTHESIZED" not in body                            # not the strong banner
    assert "## 2. Cách chạy PoC" in body                        # model's Mục 2 kept
