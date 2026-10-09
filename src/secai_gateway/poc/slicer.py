"""Deterministic core extraction of a fetched PoC (the "slice" stage).

Splits the job of turning a fetched PoC into a single runnable Section-1 file into TWO
parts with a hard safety line between them:

  1. **Graph extraction** (this module, deterministic): parse the entry file's AST and
     describe its top-level structure — functions/classes/vars/module-level statements —
     as nodes with line ranges and a `refs` call-graph. Nothing is rewritten.
  2. **Keep/drop decision**: either deterministic (`safe_slice` — noise-name + unreferenced
     heuristic) or, optionally, an LLM that receives ONLY the graph (never the source) and
     returns a `{"keep": [...], "drop": [...]}` list.
  3. **Cut** (deterministic): splice the source by the keep list's line ranges — the output
     is ALWAYS a verbatim subset of the source, so endpoints/constants/payloads can never be
     hallucinated. The LLM never emits code bytes.

Fail-open: any parse/cut/verify failure returns ``None`` so the caller falls back to the
verbatim path. The result is validated with the SAME execution-free checks as
``poc/consolidate.py`` (``compile`` + relative-import + undefined-name scans) — see
``validate_slice``.
"""
from __future__ import annotations

import ast
import logging
import os
import re
import sys
from dataclasses import dataclass, field
from typing import Any

from .consolidate import readme_text

# sys.stdlib_module_names (Python 3.10+, matches this project's pyproject.toml floor) — used
# by _dependency_header to tell a stdlib import from a third-party one requiring `pip install`.
_STDLIB_MODULES = getattr(sys, "stdlib_module_names", frozenset())

logger = logging.getLogger(__name__)

# Module-level statement kinds. `func`/`var` participate in the call graph; `stmt` is a
# bare module-level expression (a banner ``print``/``cprint`` call, a guard, ...) that has no
# name to reference and is decided by line only.
_FUNC_TYPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)

# Names strongly implying non-exploit noise — a safe-slice default drop candidate when ALSO
# unreferenced (a referenced banner/author helper is never dropped deterministically).
_NOISE_NAME_SUBSTR = (
    "banner", "logo", "print_logo", "author", "disclaimer", "usage", "help_text",
    "colors", "colour", "color", "setup_color", "setup_colour", "ascii", "art",
    "credits", "signature", "getopt_help", "print_help",
)

_NOISE_STMT_CALL_SUBSTR = ("cprint(", "print_logo(", "banner(", "logo(")


@dataclass
class Node:
    id: str
    type: str                      # "func" | "var" | "stmt"
    name: str                      # func/class/var name; "" for stmt
    lines: tuple[int, int]         # inclusive [start, end] 1-based
    refs: list[str] = field(default_factory=list)   # node ids this node references
    referenced_by: list[str] = field(default_factory=list)  # node ids referencing this node
    summary: str = ""


def _node_range(node: ast.AST) -> tuple[int, int]:
    """Inclusive 1-based [start, end] for a top-level statement, expanding decorated
    functions to include their decorator lines (``FunctionDef.lineno`` points at ``def``,
    not the decorators above it)."""
    start = getattr(node, "lineno", None)
    end = getattr(node, "end_lineno", None)
    if start is None:
        return (0, 0)
    if end is None:
        end = start
    # Decorators sit on lines before `def` — fold them into the range so the verbatim splice
    # keeps the decorator with its function.
    for deco in getattr(node, "decorator_list", []) or []:
        if getattr(deco, "lineno", None):
            start = min(start, deco.lineno)
    return (start, end)


def _name_of(node: ast.AST) -> str:
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return node.name
    if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
        # first (leftmost) target name; multi-target assigns are rare at module level in PoCs
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        for t in targets:
            if isinstance(t, ast.Name):
                return t.id
        return ""
    return ""


def _summary(node: ast.AST) -> str:
    """A STRUCTURAL description of a node — never its source text. This is what the LLM sees,
    so it must carry the node's shape (name/signature/call-target) but NEVER a literal value
    (endpoint/payload/secret). Built from the AST, not from the source lines."""
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        args = ", ".join(a.arg for a in node.args.args)
        return f"def {node.name}({args})"
    if isinstance(node, ast.ClassDef):
        return f"class {node.name}"
    if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
        nm = _name_of(node)
        if isinstance(node, ast.AnnAssign) and node.annotation is not None:
            hint = ""
            try:
                hint = ast.unparse(node.annotation)
            except Exception:
                hint = ""
            return f"var {nm}: {hint}" if hint else f"var {nm}"
        return f"var {nm}"
    # bare expression / statement: describe the call target, not the args (which may hold payloads)
    if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
        f = node.value.func
        if isinstance(f, ast.Name):
            return f"call {f.id}()"
        if isinstance(f, ast.Attribute):
            return f"call .{f.attr}()"
        return "call ...()"
    if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
        return "literal-str (docstring/header)"
    return type(node).__name__.lower()


def extract_graph(code: str) -> dict[str, Any]:
    """AST-describe the module's top level into nodes (functions/classes/vars/statements)
    plus a `refs` call-graph and the always-keep line ranges (imports + ``__main__`` guard).

    Returns a JSON-serialisable dict:
      {"nodes": [Node.asdict()...], "always_keep": [[start,end]...], "name_index": {...}}
    ``always_keep`` are the line ranges that survive ANY cut (imports, shebang/encoding,
    ``if __name__ == "__main__"``) so the LLM keep/drop list never has to mention them.
    """
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return {"nodes": [], "always_keep": [], "name_index": {}}

    lines = code.splitlines()
    nodes: list[Node] = []
    always_keep: list[list[int]] = []

    # name -> node id, for building refs
    name_index: dict[str, str] = {}
    # AST bodies whose Name-loads count as "used from the top level" but that are NOT a
    # `func`-type node's own body — the `if __name__=="__main__":` guard (always kept
    # regardless of keep_ids, so anything it calls must count as referenced) plus every
    # var/stmt node's own subtree (a var initializer or a bare top-level call can itself
    # reference another node's name). Scanned in a second pass below, AFTER name_index is
    # fully built, so a forward reference still resolves. Without this, a Name-load ONLY
    # reachable from the __main__ guard or from another var/stmt (never from inside a
    # `func`-type body) produces no `referenced_by` edge at all — the LLM keep/drop step
    # (decide_keep_drop) then has no signal that the node is load-bearing and can drop it,
    # silently hollowing out the entry point while the file still compiles clean (see
    # slicer.py module docstring / historical CVE-2026-45504-class incidents in
    # consolidate.py). `_is_noise_node` (the deterministic safe_slice path) checks the same
    # `referenced_by` field, so this fix also protects that path, not just the LLM one.
    ref_source_bodies: list[ast.AST] = []

    for stmt in tree.body:
        rng = _node_range(stmt)
        # imports → always kept (dropping an "unused" import risks a false positive; keep all)
        if isinstance(stmt, (ast.Import, ast.ImportFrom)):
            always_keep.append([rng[0], rng[1]])
            continue
        # `if __name__ == "__main__":` guard → always kept (it's the entry point)
        if isinstance(stmt, ast.If) and isinstance(stmt.test, ast.Compare):
            left = stmt.test.left
            if (isinstance(left, ast.Name) and left.id == "__name__"
                    and any(isinstance(c, ast.Constant) and c.value == "__main__"
                            for c in stmt.test.comparators)):
                always_keep.append([rng[0], rng[1]])
                ref_source_bodies.append(stmt)
                continue
        if isinstance(stmt, _FUNC_TYPES):
            n = Node(id=f"fn_{stmt.name}", type="func", name=stmt.name,
                     lines=rng, summary=_summary(stmt))
            nodes.append(n)
            name_index[stmt.name] = n.id
        elif isinstance(stmt, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
            nm = _name_of(stmt)
            n = Node(id=f"var_{nm or rng[0]}", type="var", name=nm,
                     lines=rng, summary=_summary(stmt))
            nodes.append(n)
            ref_source_bodies.append(stmt)
            if nm:
                name_index[nm] = n.id
        else:
            # bare expression / other module-level statement (banner call, etc.)
            n = Node(id=f"stmt_{rng[0]}", type="stmt", name="",
                     lines=rng, summary=_summary(stmt))
            nodes.append(n)
            ref_source_bodies.append(stmt)

    # Build refs: for each func, scan its subtree for Name loads matching another node's name.
    node_by_id = {n.id: n for n in nodes}
    # index node ids by the name they bind (func/class/var)
    for n in nodes:
        if n.type != "func":
            continue
        body_node = None
        for stmt in tree.body:
            if isinstance(stmt, _FUNC_TYPES) and stmt.name == n.name:
                body_node = stmt
                break
        if body_node is None:
            continue
        for sub in ast.walk(body_node):
            if isinstance(sub, ast.Name) and isinstance(sub.ctx, ast.Load):
                target = name_index.get(sub.id)
                if target and target != n.id and target not in n.refs:
                    n.refs.append(target)
                    node_by_id[target].referenced_by.append(n.id)

    # Second pass: refs FROM the __main__ guard and from every var/stmt node's own body — the
    # blind spot described above. Marked with a synthetic "__toplevel__" referrer (not a real
    # node id — only ever tested for truthiness by _is_noise_node/apply_decision, never looked
    # up), so a stmt/var node doesn't need to appear twice or gain a fabricated self-loop.
    for body_node in ref_source_bodies:
        for sub in ast.walk(body_node):
            if isinstance(sub, ast.Name) and isinstance(sub.ctx, ast.Load):
                target = name_index.get(sub.id)
                if target and "__toplevel__" not in node_by_id[target].referenced_by:
                    node_by_id[target].referenced_by.append("__toplevel__")

    # Shebang + encoding cookie always kept (lines before the first import/code are safe to
    # preserve verbatim; the big banner comment above imports is NOT in any node range so it
    # is naturally dropped by the cut).
    return {
        "nodes": [n.__dict__ for n in nodes],
        "always_keep": always_keep,
        "name_index": name_index,
    }


def _is_noise_node(n: dict[str, Any]) -> bool:
    """Deterministic safe-slice drop heuristic: unreferenced AND (noise-named func OR bare
    banner call). A referenced node is NEVER dropped deterministically."""
    if n["referenced_by"]:
        return False
    nm = (n["name"] or "").lower()
    if n["type"] == "func":
        return any(s in nm for s in _NOISE_NAME_SUBSTR)
    if n["type"] == "var":
        # unreferenced vars are dead by construction — but only drop the clearly-noise-named
        # ones; an unreferenced var might be imported-elsewhere or a data table we shouldn't
        # trim yet.
        return any(s in nm for s in _NOISE_NAME_SUBSTR)
    if n["type"] == "stmt":
        summ = (n["summary"] or "").lower()
        return any(s in summ for s in _NOISE_STMT_CALL_SUBSTR)
    return False


def _safe_keep_ids(graph: dict[str, Any]) -> set[str]:
    """Deterministic keep set: keep everything EXCEPT clearly-noise unreferenced nodes."""
    return {n["id"] for n in graph["nodes"] if not _is_noise_node(n)}


def cut_code(code: str, graph: dict[str, Any], keep_ids: set[str]) -> str:
    """Splice the source to the kept line ranges (verbatim). ``always_keep`` ranges (imports,
    ``__main__`` guard) and the shebang/encoding header survive regardless of ``keep_ids``.

    Output is ALWAYS a verbatim subset of the input — never re-synthesised text."""
    lines = code.splitlines()
    keep_lines: set[int] = set()

    # Always: shebang (line 1) + any encoding cookie in the first two lines.
    for i, ln in enumerate(lines[:2], start=1):
        s = ln.strip()
        if i == 1 and s.startswith("#!"):
            keep_lines.add(i)
        elif s.startswith("#") and ("coding" in s or "encoding" in s):
            keep_lines.add(i)

    for start, end in graph["always_keep"]:
        for ln in range(start, end + 1):
            keep_lines.add(ln)

    for n in graph["nodes"]:
        if n["id"] not in keep_ids:
            continue
        for ln in range(n["lines"][0], n["lines"][1] + 1):
            keep_lines.add(ln)

    # Reassemble in order, collapsing runs of dropped lines into a single blank line for
    # readability (so two kept blocks don't fuse onto one line).
    out: list[str] = []
    prev_kept = False
    for i, ln in enumerate(lines, start=1):
        if i in keep_lines:
            out.append(ln)
            prev_kept = True
        elif prev_kept and out and out[-1] != "":
            out.append("")
            prev_kept = False
    # strip trailing blank lines
    while out and out[-1] == "":
        out.pop()
    return "\n".join(out)


def _dependency_header(code: str, exclude: set[str] | None = None) -> str:
    """Best-effort ``# Yêu cầu môi trường: pip install X, Y`` comment for every top-level
    import that ISN'T in the standard library — ADD-ONLY (never rewrites/removes an existing
    line, so it structurally cannot introduce fidelity drift, unlike an LLM rewrite). Surfaces
    a real environment-dependency gap: consolidate.md's ``<robustness>`` rules already tell the
    LLM-merge path to comment non-stdlib deps, but ``slice_entry`` — the deterministic,
    now-dominant path for a single-file Python PoC with no cross-file imports — never applied
    any equivalent (2026-08 research finding environment-dependency-mismatch: "the fast-path
    slicer that now handles most requests never invokes" the dependency-comment rule). Returns
    ``""`` when there's nothing non-stdlib to flag, or on any parse miss (fail-open, matching
    every other check in this module)."""
    if not code or not code.strip():
        return ""
    # `exclude`: same-repo sibling module basenames (the prune path ships them alongside the
    # entry, so they are NOT pip packages — listing them as `pip install helper` would be wrong).
    exclude = exclude or set()
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return ""
    third_party: list[str] = []
    seen: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                top = a.name.split(".")[0]
                if top and top not in _STDLIB_MODULES and top not in exclude and top not in seen:
                    seen.add(top)
                    third_party.append(top)
        elif isinstance(node, ast.ImportFrom):
            if node.level and node.level > 0:
                continue  # relative import — not a pip package (and slice_entry bails on this)
            top = (node.module or "").split(".")[0]
            if top and top not in _STDLIB_MODULES and top not in exclude and top not in seen:
                seen.add(top)
                third_party.append(top)
    if not third_party:
        return ""
    pkgs = ", ".join(sorted(third_party))
    return (f"# Yêu cầu môi trường (tham khảo, KHÔNG phải bước tải/cài lúc chạy): "
            f"pip install {pkgs}\n")


def validate_slice(code: str, language: str, cve_id: str | None = None) -> str | None:
    """Execution-free validation of a sliced file — reuses consolidate.py's checks so a bad
    keep/drop (dropped import, undefined name, package-relative import) is caught the same way.
    Returns a short Vietnamese reason on failure, ``None`` when it passes."""
    from .consolidate import validation_failure
    return validation_failure(code, language, cve_id)


def imports_local_sibling(code: str, sibling_basenames: set[str]) -> bool:
    """True when the entry file depends on a sibling file in the SAME PoC set — either an
    ``import``/``from … import`` (e.g. ``from utils.menu_functions import ...`` while
    ``utils/menu_functions.py`` is a support file; package-relative imports count as local too)
    OR a string-literal path reference passed to ``subprocess.*``/``os.system``/``os.popen``/
    ``open``/``exec`` (e.g. ``subprocess.run(["python3","listener.py"])``,
    ``open("payload.json")`` — see ``consolidate._sibling_string_refs``). A slice of the entry
    alone cannot inline (or ship alongside) those — dropping the support files would break the
    runnable file — so the caller must fall back to consolidation (which inlines cross-file
    Python deps) or verbatim multi-file (for anything consolidation itself can't fold in)."""
    if not code or not sibling_basenames:
        return False
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return False
    for n in ast.walk(tree):
        if isinstance(n, ast.ImportFrom):
            if n.level and n.level > 0:
                return True  # `from . import x` / `from .mod import y`
            if n.module and n.module.split(".")[0] in sibling_basenames:
                return True
        elif isinstance(n, ast.Import):
            for a in n.names:
                if a.name.split(".")[0] in sibling_basenames:
                    return True
    from .consolidate import _sibling_string_refs
    return bool(_sibling_string_refs(code, sibling_basenames))


def safe_slice(code: str, language: str, cve_id: str | None = None) -> str | None:
    """Deterministic slice: extract graph, keep everything except clearly-noise unreferenced
    nodes, cut, and validate. Returns the sliced code, or ``None`` (caller falls back to the
    verbatim source) on any parse/cut/validation failure."""
    try:
        graph = extract_graph(code)
        if not graph["nodes"]:
            return None
        keep = _safe_keep_ids(graph)
        sliced = cut_code(code, graph, keep)
        if not sliced.strip():
            return None
        if validate_slice(sliced, language, cve_id) is not None:
            return None
        return sliced
    except Exception:
        logger.exception("safe_slice failed for %s", cve_id)
        return None


def build_decision_prompt(graph: dict[str, Any], cve_id: str | None) -> str:
    """The user message for the optional LLM keep/drop decision. Contains ONLY the graph
    (node id/type/name/line/summary/refs), never the source, so the model has nothing to
    rewrite — it can only choose nodes to keep or drop. Always-keep ranges are excluded:
    the model must not mention imports or the ``__main__`` guard."""
    import json
    nodes = graph["nodes"]
    cve = (cve_id or "").strip().upper() or "(không xác định)"
    return (
        f"CVE: {cve}\n"
        "Dưới đây là CẤU TRÚC (đồ thị) của một tệp PoC — KHÔNG phải mã nguồn. Nhiệm vụ: chọn "
        "những node là LÕI khai thác để GIỮ, và node thừa (banner/tác giả/config màu/demo) để BỎ.\n"
        "Quy tắc:\n"
        "- Chỉ chọn từ danh sách node bên dưới, không bịa node mới.\n"
        "- Node có refs/referenced_by (được code dùng) → giữ.\n"
        "- Trả về DUY NHẤT một JSON: {\"keep\": [id...], \"drop\": [id...]}.\n"
        "- KHÔNG in lại code, KHÔNG giải thích gì thêm.\n\n"
        f"{json.dumps(nodes, ensure_ascii=False)}"
    )


def apply_decision(graph: dict[str, Any], decision: dict[str, Any]) -> set[str]:
    """Validate the LLM keep/drop list against the actual graph: only real node ids survive,
    a referenced node is never dropped, and a dropped node is never kept. Returns the final
    keep id set (always inclusive of referenced nodes, for safety)."""
    ids = {n["id"] for n in graph["nodes"]}
    keep = set(decision.get("keep") or [])
    drop = set(decision.get("drop") or [])
    keep &= ids
    drop &= ids
    keep -= drop
    # Safety floor: never drop a node that is referenced by a kept node.
    referenced = {n["id"] for n in graph["nodes"] if n["referenced_by"]}
    keep |= referenced
    return keep


async def decide_keep_drop(runner: Any, graph: dict[str, Any], cve_id: str | None,
                           max_attempts: int = 2) -> set[str] | None:
    """LLM keep/drop decision over the STRUCTURE graph (never the source). Returns the final
    keep id set, or ``None`` on any miss (caller falls back to ``safe_slice``). Retries once
    with a correction note when the model returns an invalid/unparseable list.

    The decision is pure text ("keep"/"drop" ids) — the model has no source to rewrite, and
    ``apply_decision`` further clamps it to real nodes + referenced nodes."""
    import json
    prompt = build_decision_prompt(graph, cve_id)
    for attempt in range(1, max_attempts + 1):
        try:
            text, _meta = await runner.complete("poc_slice", prompt)
        except Exception:
            logger.exception("slice decision LLM call failed for %s (attempt %d)", cve_id, attempt)
            return None
        # Tolerate a ```json fence the model may add despite the contract.
        m = re.search(r"\{.*\}", text or "", re.S)
        if not m:
            if attempt >= max_attempts:
                return None
            prompt += "\n\nLƯU Ý: trả về DUY NHẤT một JSON {\"keep\": [...], \"drop\": [...]}, không kèm chữ khác."
            continue
        try:
            decision = json.loads(m.group(0))
        except json.JSONDecodeError:
            if attempt >= max_attempts:
                return None
            prompt += "\n\nLƯU Ý: JSON không hợp lệ. Trả lại DUY NHẤT {\"keep\": [...], \"drop\": [...]}."
            continue
        if not isinstance(decision, dict) or not isinstance(decision.get("keep"), list):
            return None
        keep = apply_decision(graph, decision)
        return keep if keep else None
    return None


async def slice_entry(ev: dict[str, Any], runner: Any | None = None,
                      cve_id: str | None = None, use_llm: bool = True) -> dict[str, Any] | None:
    """Extract the core of a single real-code evidence entry's ENTRY file into one runnable
    block, returning a new evidence dict (``extracted=True``) — or ``None`` to fall back to
    the verbatim source (never worse than today).

    ``ev`` is a real-code evidence entry (``source_code`` for single-file, or ``source_files``
    with an ``entry`` first for multi-file). Slice is entry-file-only in Phase 1 (cross-file
    inline is a later tier); the result keeps provenance pointing at the original artifact."""
    code = None
    language = None
    source_files = ev.get("source_files")
    if source_files:
        for f in source_files:
            if f.get("role") == "entry" or f is source_files[0]:
                code = f.get("code") or ""
                language = f.get("language") or ev.get("language")
                break
    if code is None:
        code = ev.get("source_code") or ""
        language = ev.get("language") or language
    if not code or not code.strip():
        return None

    lang = (language or "").strip().lower()
    if lang not in ("python", "py"):
        return None  # Phase 1: Python only; other languages stay on the verbatim/consolidate path

    # Cross-file dependency guard: if the entry imports a SIBLING file in the same set, a slice
    # of the entry alone can't inline it (dropping the support files would break the runnable
    # file). Hand that to the LLM consolidation, which inlines cross-file deps.
    if source_files:
        siblings = {
            os.path.splitext(os.path.basename(f.get("path") or ""))[0]
            for f in source_files if f.get("role") != "entry"
        }
        if imports_local_sibling(code, siblings):
            return None

    # 1. deterministic floor
    sliced = safe_slice(code, "python", cve_id)
    # 2. optional LLM keep/drop refinement (graph-only, never source)
    if use_llm and runner is not None:
        graph = extract_graph(code)
        if graph["nodes"]:
            keep = await decide_keep_drop(runner, graph, cve_id)
            if keep:
                llm_sliced = cut_code(code, graph, keep)
                if llm_sliced.strip() and validate_slice(llm_sliced, "python", cve_id) is None:
                    sliced = llm_sliced

    if not sliced or not sliced.strip():
        return None

    header = _dependency_header(sliced)
    if header:
        # Insert AFTER a shebang line if present — `#!` must stay line 1 or direct execution
        # (`./poc.py`) breaks; everything else in `sliced` is unaffected (add-only).
        if sliced.startswith("#!"):
            first_nl = sliced.find("\n") + 1
            sliced = sliced[:first_nl] + header + sliced[first_nl:]
        else:
            sliced = header + sliced

    return {
        "source": ev.get("source"),
        "source_ref": ev.get("source_ref"),
        "url": ev.get("url"),
        "kind": ev.get("kind") or "exploit",
        "title": ev.get("title") or f"Extracted PoC {(cve_id or '').upper()}".strip(),
        "cve_ids": ev.get("cve_ids") or ([cve_id.upper()] if cve_id else []),
        "language": lang,
        "source_code": sliced,
        "source_files": None,
        "code_truncated": False,
        "sha256": ev.get("sha256"),
        # consolidated=True so inject.py renders it under the SAME light banner as an AI-merged
        # file (uniform output, no FE change); extracted=True records the deterministic origin
        # separately for telemetry/fidelity checks.
        "readme_text": readme_text(ev),
        "consolidated": True,
        "extracted": True,
        "extracted_from": {
            "sha256": ev.get("sha256"),
            "url": ev.get("url"),
            "source": ev.get("source"),
        },
        "static_indicators": {"language": lang, "extracted": True},
    }


# ── language sniffing (exploitdb stores real code as .txt) ───────────────────
_EXT_LANG = {
    ".py": "python", ".rb": "ruby", ".php": "php", ".pl": "perl", ".pm": "perl",
    ".sh": "bash", ".bash": "bash", ".js": "javascript", ".ps1": "powershell",
    ".c": "c", ".h": "c", ".cpp": "cpp", ".cc": "cpp", ".cxx": "cpp",
    ".go": "go", ".rs": "rust", ".java": "java",
}
_SHEBANG_LANG = (
    ("python", ("python",)), ("ruby", ("ruby",)), ("perl", ("perl",)),
    ("php", ("php",)), ("node", ("node",)), ("bash", ("bash", "/sh", " sh", "zsh")),
)
_LANG_CANON = {"py": "python", "python3": "python", "rb": "ruby", "js": "javascript",
               "node": "javascript", "sh": "bash", "shell": "bash", "c++": "cpp",
               "golang": "go", "ps1": "powershell"}


# Strong Python-2-only markers. A served PoC carrying any of these cannot run under the
# python3 interpreter the air-gap runner uses (observed: CVE-2014-6271 served a `urllib2`
# PoC → ImportError → no exploit). Presence is a signal to route the entry through the
# consolidate LLM (which ports it) instead of serving the verbatim/sliced py2 bytes.
_PY2_RE = re.compile(
    r"(?m)^\s*(?:import\s+urllib2|from\s+urllib2|import\s+urlparse|from\s+urlparse"
    r"|import\s+httplib|from\s+httplib|import\s+ConfigParser|from\s+ConfigParser"
    r"|import\s+StringIO|from\s+StringIO|import\s+cPickle\b)"
    r"|\braw_input\s*\(|\bxrange\s*\(|\bbasestring\b|\bunicode\s*\("
    r"|\.iteritems\s*\(|\.iterkeys\s*\(|\.itervalues\s*\(|\.has_key\s*\("
    r"|except\s+[A-Za-z_][\w.]*\s*,\s*[A-Za-z_]",
)


def looks_like_python2(code: str) -> bool:
    """True when ``code`` carries a strong Python-2-only marker (``urllib2``, ``urlparse``
    import, ``raw_input``, ``xrange``, ``.iteritems()``, ``except X, e`` …). Conservative:
    only markers that are invalid/absent in Python 3 — never triggers on ordinary py3 code."""
    return bool(code and _PY2_RE.search(code))


def sniff_language(code: str, path: str = "", declared: str = "") -> str:
    """Best-effort TRUE language of a PoC blob. exploitdb keeps real python/ruby/perl exploits
    with a ``.txt`` extension and ``language=txt`` — so a downstream syntax-check / runner keyed
    on the declared field picks the wrong tool. Order: real extension → shebang → content
    signals → declared (unless it is the ambiguous txt/plain). Returns a canonical lang string
    or ``""``. Pure inspection, never executes; fail-open (``""`` on any miss)."""
    ext = os.path.splitext(path or "")[1].lower()
    if ext in _EXT_LANG:
        return _EXT_LANG[ext]
    stripped = (code or "").lstrip()
    first = stripped.splitlines()[0] if stripped else ""
    if first.startswith("#!"):
        low = first.lower()
        for lang, needles in _SHEBANG_LANG:
            if any(n in low for n in needles):
                return lang
    head = (code or "")[:4000]
    low = head.lower()
    if "<?php" in low:
        return "php"
    if re.search(r"^\s*(?:import\s+\w|from\s+[\w.]+\s+import|def\s+\w+\s*\(|print\()", head, re.M):
        return "python"
    if re.search(r"\buse\s+strict\b|\bmy\s+\$\w", head):
        return "perl"
    if re.search(r"\brequire\s+['\"]|\bputs\b|\bend\b", head) and re.search(r"\bdef\s+\w+\b|=>", head):
        return "ruby"
    if re.search(r"^\s*(?:function\s+\w+\s*\(\)|curl\s|wget\s|apt-get\s)", head, re.M):
        return "bash"
    d = (declared or "").strip().lower()
    if d and d not in ("txt", "text", "plain", "plaintext", ""):
        return _LANG_CANON.get(d, d)
    return ""


# ── prune-in-place: keep multi-file structure, drop only the unnecessary ─────
# Manifests are kept as the environment/build hint (pip install / cmake), never dropped.
_MANIFEST_BASENAMES = {"requirements.txt", "setup.py", "pyproject.toml", "package.json",
                       "go.mod", "cargo.toml", "gemfile", "composer.json", "makefile",
                       "cmakelists.txt", "build.gradle", "pom.xml"}
# Calls that defeat STATIC reachability — if a file uses any, its intra-file slice is skipped
# (kept verbatim) so a dynamically-referenced node is never cut. Same fail-open-toward-safety
# bias as the rest of this module.
_DYNAMIC_DISPATCH_MARKERS = ("__import__", "importlib", "getattr(", "eval(", "exec(",
                             "entry_points", "pkgutil", "globals()", "locals()")


def _has_dynamic_dispatch(code: str) -> bool:
    return any(m in (code or "") for m in _DYNAMIC_DISPATCH_MARKERS)


def _is_manifest_name(path: str) -> bool:
    return os.path.basename(path or "").lower() in _MANIFEST_BASENAMES


def _file_stem(f: dict[str, Any]) -> str:
    return os.path.splitext(os.path.basename(f.get("path") or ""))[0]


def _entry_file(source_files: list[dict[str, Any]]) -> dict[str, Any] | None:
    for f in source_files:
        if (f.get("role") or "") == "entry":
            return f
    return source_files[0] if source_files else None


async def prune_evidence(ev: dict[str, Any], runner: Any | None = None,
                         cve_id: str | None = None, use_llm: bool = True) -> dict[str, Any] | None:
    """Prune-in-place: KEEP the PoC's multi-file structure and only remove the parts that don't
    matter — never rewrite a byte, never flatten to one file. The higher-fidelity alternative to
    ``consolidate_poc`` for a genuinely multi-file PoC (an imported helper, a data file the entry
    reads, or source that must be compiled), where an LLM merge risks the CVE-2026-45504 drift
    class and can't be live-verified in air-gap. Returns a NEW evidence dict (``pruned=True``,
    ``source_files`` PRESERVED) or ``None`` to fall back (single-file / no entry / all-empty).

    Two prunes, both fail-open toward keeping more:
    * FILE-level: keep the entry, its transitive load-bearing siblings (``_imported_sibling_names``
      closure — imports AND string-literal path refs), every README and manifest; drop only
      support files nothing references (another CVE's exploit, leftovers).
    * INTRA-file (entry only, Python only, skipped on dynamic dispatch): the conservative
      ``safe_slice`` — drop unreferenced noise-named nodes (banner/logo/author). Support files
      are kept 100% verbatim (their functions are called cross-file, so within-file reachability
      can't judge them safely). ``use_llm`` is reserved; the LLM keep/drop is intentionally NOT
      run here to keep the multi-file path purely deterministic."""
    source_files = ev.get("source_files")
    if not source_files or len(source_files) <= 1:
        return None  # single-file → caller uses slice_entry / verbatim
    entry = _entry_file(source_files)
    if entry is None:
        return None
    entry_code = entry.get("code") or ""
    if not entry_code.strip():
        return None
    entry_lang = sniff_language(entry_code, entry.get("path") or "",
                                entry.get("language") or ev.get("language") or "")

    from .consolidate import _imported_sibling_names
    non_entry = [f for f in source_files if f is not entry]
    support_by_stem: dict[str, list[dict[str, Any]]] = {}
    all_stems: set[str] = set()
    for f in non_entry:
        s = _file_stem(f)
        if s:
            all_stems.add(s)
            support_by_stem.setdefault(s, []).append(f)

    # Transitive closure of siblings actually depended on (entry → siblings → their siblings…).
    reached = _imported_sibling_names(entry_code, all_stems)
    frontier = set(reached)
    while frontier:
        nxt: set[str] = set()
        for s in frontier:
            for f in support_by_stem.get(s, []):
                nxt |= (_imported_sibling_names(f.get("code") or "", all_stems) - reached)
        reached |= nxt
        frontier = nxt

    kept: list[dict[str, Any]] = []
    dropped: list[str] = []
    for f in source_files:
        role = f.get("role") or ""
        if f is entry or role in ("entry", "readme") or _is_manifest_name(f.get("path") or ""):
            kept.append(f)
        elif _file_stem(f) in reached:
            kept.append(f)
        else:
            dropped.append(f.get("path") or "")
    if not kept:
        return None

    pruned_files: list[dict[str, Any]] = []
    for f in kept:
        nf = dict(f)
        is_entry = f is entry or (f.get("role") or "") == "entry"
        code = nf.get("code") or ""
        # Intra-file slice ONLY the entry (support files stay verbatim — safe re cross-file use).
        if is_entry and code.strip() and not _has_dynamic_dispatch(code):
            flang = sniff_language(code, nf.get("path") or "", nf.get("language") or "")
            if flang == "python":
                sliced = safe_slice(code, "python", cve_id)
                if sliced and sliced.strip():
                    code = sliced
            # Exclude same-repo siblings from the pip hint — they ship alongside, not from PyPI.
            header = _dependency_header(code, exclude=all_stems)
            if header:
                if code.startswith("#!"):
                    i = code.find("\n") + 1
                    code = code[:i] + header + code[i:]
                else:
                    code = header + code
            nf["code"] = code
        pruned_files.append(nf)

    return {
        "source": ev.get("source"),
        "source_ref": ev.get("source_ref"),
        "url": ev.get("url"),
        "kind": ev.get("kind") or "exploit",
        "title": ev.get("title") or f"PoC {(cve_id or '').upper()}".strip(),
        "cve_ids": ev.get("cve_ids") or ([cve_id.upper()] if cve_id else []),
        "language": entry_lang,
        "source_code": None,
        "source_files": pruned_files,
        "code_truncated": False,
        "sha256": ev.get("sha256"),
        "readme_text": readme_text(ev),
        "companion_doc_only": ev.get("companion_doc_only", False),
        # NOT consolidated (no LLM rewrite) — renders under the distinct "giữ nguyên cấu trúc" banner.
        "consolidated": False,
        "extracted": True,
        "pruned": True,
        "extracted_from": {"sha256": ev.get("sha256"), "url": ev.get("url"),
                           "source": ev.get("source")},
        "static_indicators": {"language": entry_lang, "pruned": True,
                              "files_in": len(source_files), "files_kept": len(pruned_files),
                              "files_dropped": dropped},
    }
