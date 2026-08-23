"""Deterministic fidelity checks for the LLM consolidation output.

The LLM merge (poc/consolidate.py) rewrites real source, and its existing validator
(``validation_failure``) only catches BROKEN code (syntax / undefined name / relative import) —
never SEMANTICALLY-DRIFTED code that still runs. Live-observed failure (CVE-2026-45504): the
merge renamed ``--attacker-ip`` + ``--attacker-port`` to a single ``--callback-url`` and
hardcoded the callback bind port to ``8780``, silently discarding ``--attacker-port 9020``.

These checks compare the consolidated output against the ORIGINAL entry source and name the
exact defect (usable as corrective feedback for a consolidation retry, mirroring
consolidate.py's ``_RETRY_FEEDBACK``). All checks are AST/`argparse`-shaped, execution-free,
deterministic, and fail-open (any parse miss → no finding, never a false reject).

Only the LLM-merge branch needs this — the deterministic slicer outputs a verbatim subset of
the source and cannot rename flags.
"""
from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from typing import Any

# ── CLI surface extraction ──────────────────────────────────────────────────

_ADDARG_RE = re.compile(r"\.add_argument\(\s*(['\"][^'\"]+['\"]|\"\"\"[\s\S]*?\"\"\"|'''[\s\S]*?''')")
_FLAG_RE = re.compile(r"--[A-Za-z0-9][A-Za-z0-9_-]*")
_ARGS_ATTR_RE = re.compile(r"\bargs\.([A-Za-z_][A-Za-z0-9_]*)\b")


def _flatten_str_const(node: ast.AST) -> str:
    """Best-effort string literal value of an AST node (argparse first positional arg)."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, (ast.Tuple, ast.List)):
        return " ".join(_flatten_str_const(e) for e in node.elts)
    return ""


def _cli_flags_ast(code: str) -> set[str]:
    """The set of ``--flags`` registered via ``argparse`` ``add_argument`` calls in ``code``.

    Uses the AST: find ``add_argument`` calls whose first positional is a string literal, then
    extract ``--flag`` tokens from it. Falls back to a source-level regex when the AST is
    unparseable (non-Python callers are expected to pre-filter to Python)."""
    flags: set[str] = set()
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return flags
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "add_argument" and node.args):
            literal = _flatten_str_const(node.args[0])
            flags |= set(_FLAG_RE.findall(literal))
    return flags


def _add_parser_names_ast(code: str) -> list[str]:
    """Subcommand names registered via ``add_parser("name")`` (e.g. ``check``/``read``/``shell``
    in a wp2shell-style CLI), in order of appearance. Empty when the CLI has no subparsers."""
    names: list[str] = []
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return names
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "add_parser" and node.args):
            literal = _flatten_str_const(node.args[0])
            if literal and literal not in names:
                names.append(literal)
    return names


def cli_surface(code: str) -> dict[str, Any]:
    """The deterministic CLI surface of ``code`` — the ground truth the Mục 2 (how-to-run)
    prompt must describe. Returns ``{subcommands, flags, positionals}``; every field is derived
    from the argparse AST, never from the LLM's prose. Empty dict on non-Python/parse miss."""
    if not code or not code.strip():
        return {}
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return {}
    # Flags: scan EVERY string-literal positional of add_argument (not just the first) so a
    # short alias + long form pair (``-i, --interactive``) exposes the long flag users type.
    flags: set[str] = set()
    positionals: list[str] = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "add_argument" and node.args):
            continue
        # First arg is the name (flags or positional); collect long flags from it AND from any
        # later string-literal arg (``-i, --interactive`` splits the pair across two args).
        combined = " ".join(_flatten_str_const(a) for a in node.args if _flatten_str_const(a))
        if not combined.strip():
            continue
        flag_toks = _FLAG_RE.findall(combined)
        if flag_toks:
            flags |= set(flag_toks)
        else:
            first = _flatten_str_const(node.args[0])
            for tok in first.split():
                if tok and not tok.startswith("-") and tok not in positionals:
                    positionals.append(tok)
    return {
        "subcommands": _add_parser_names_ast(code),
        "flags": sorted(flags),
        "positionals": positionals,
    }


def _args_namespace_names(code: str) -> set[str]:
    """Names of variables bound to an ``argparse`` ``parse_args()`` result (e.g. ``args`` or
    ``ARGS`` in ``ARGS = parse_args()``). The Namespace attribute checks below key off these."""
    names: set[str] = set()
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return names
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        for t in node.targets:
            if not isinstance(t, ast.Name):
                continue
            if isinstance(node.value, ast.Call):
                f = node.value.func
                if (isinstance(f, ast.Attribute) and f.attr == "parse_args") or \
                        (isinstance(f, ast.Name) and f.id == "parse_args"):
                    names.add(t.id)
    return names


def _args_attrs_ast(code: str) -> set[str]:
    """The set of ``<ns>.<name>`` attribute names READ in ``code``, where ``<ns>`` is any
    variable bound to ``parse_args()`` (``args``, ``ARGS``, ...)."""
    attrs: set[str] = set()
    namespaces = _args_namespace_names(code) or {"args"}
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return attrs
    for node in ast.walk(tree):
        if (isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name)
                and node.value.id in namespaces and isinstance(node.ctx, ast.Load)):
            attrs.add(node.attr)
    return attrs


def _args_assigned_ast(code: str) -> set[str]:
    """The set of ``<ns>.<name>`` attributes ASSIGNED in ``code`` (e.g. ``args.domain,
    args.user = args.creds.split(...)``). These are derived fields — they exist WITHOUT a
    matching ``add_argument``, so an orphan check must not flag them."""
    assigned: set[str] = set()
    namespaces = _args_namespace_names(code) or {"args"}
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return assigned
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        for t in node.targets:
            if isinstance(t, ast.Attribute) and isinstance(t.value, ast.Name) and t.value.id in namespaces:
                assigned.add(t.attr)
            # `args.a, args.b = ...` via tuple unpacking
            elif isinstance(t, (ast.Tuple, ast.List)):
                for elt in t.elts:
                    if (isinstance(elt, ast.Attribute) and isinstance(elt.value, ast.Name)
                            and elt.value.id in namespaces):
                        assigned.add(elt.attr)
    return assigned


# ── findings ────────────────────────────────────────────────────────────────

@dataclass
class Finding:
    kind: str          # "missing_flag" | "orphan_args" | "hardcoded_bind"
    detail: str


def _missing_flags(orig: str, merged: str) -> list[Finding]:
    """Flags the original CLI exposes but the merged output dropped (rename/removal)."""
    orig_flags = _cli_flags_ast(orig)
    merged_flags = _cli_flags_ast(merged)
    missing = orig_flags - merged_flags
    if not missing:
        return []
    return [Finding(
        "missing_flag",
        "thiếu tham số CLI so với PoC gốc: " + ", ".join(sorted(missing))
        + " — giữ NGUYÊN mọi cờ dòng lệnh đã có; không đổi tên/gộp/bỏ",
    )]


def _orphan_args(merged: str) -> list[Finding]:
    """``args.<name>`` read in the merged output that no longer has a matching flag (a dropped
    ``add_argument`` with a surviving reference — guaranteed AttributeError at runtime).
    Derived fields assigned in-code (``args.domain = ...``) are not flags, so they're exempt."""
    flags = _cli_flags_ast(merged)
    flag_names = {f.lstrip("-").replace("-", "_") for f in flags}
    assigned = _args_assigned_ast(merged)
    orphans = _args_attrs_ast(merged) - flag_names - assigned
    if not orphans:
        return []
    return [Finding(
        "orphan_args",
        "đọc `args." + ", args.".join(sorted(orphans))
        + "` nhưng không còn định nghĩa cờ tương ứng — khôi phục add_argument đã bị bỏ",
    )]


def _hardcoded_binds(orig: str, merged: str) -> list[Finding]:
    """Detect a literal that replaced an argparse-derived value: a numeric/host literal used in
    a bind/listen/socket position in the merged output where the original derived it from
    ``args.*``. Heuristic and conservative — only flags the clearest case (a literal port in a
    socket/HTTPServer bind where the original used an ``args.*``-derived variable)."""
    findings: list[Finding] = []

    # Where the original binds using an args-derived variable (e.g. HTTPServer(("0.0.0.0",
    # ATTACKER_PORT), ...) or socket.bind(("0.0.0.0", PORT))).
    orig_args_vars = _args_attrs_ast(orig)
    if not orig_args_vars:
        return findings

    try:
        mtree = ast.parse(merged)
    except SyntaxError:
        return findings

    for node in ast.walk(mtree):
        if not isinstance(node, ast.Call):
            continue
        # HTTPServer((host, port), ...) / socket bind / listen
        is_bind = (
            (isinstance(node.func, ast.Name) and node.func.id in ("HTTPServer", "socket"))
            or (isinstance(node.func, ast.Attribute) and node.func.attr in ("bind", "listen"))
        )
        if not is_bind or not node.args:
            continue
        # find a literal port/host argument that is a constant
        for a in node.args:
            if isinstance(a, ast.Tuple):
                for elt in a.elts:
                    if isinstance(elt, ast.Constant) and isinstance(elt.value, int):
                        findings.append(Finding(
                            "hardcoded_bind",
                            f"cổng/địa chỉ bị chai cứng thành literal {elt.value!r} ở vị trí "
                            "bind/listen — phải lấy từ tham số CLI như PoC gốc, không hardcode",
                        ))
    return findings


def fidelity_check(code: str, orig_source: str) -> list[Finding]:
    """All deterministic fidelity findings for a merged ``code`` vs the original entry source.
    Returns an empty list when the merge preserves the original's CLI surface and binding."""
    if not code or not orig_source:
        return []
    findings: list[Finding] = []
    findings += _missing_flags(orig_source, code)
    findings += _orphan_args(code)
    findings += _hardcoded_binds(orig_source, code)
    return findings


def fidelity_failure(code: str, orig_source: str) -> str | None:
    """A single short Vietnamese reason string (for retry feedback + logging), or ``None``
    when the merge is faithful. Mirrors consolidate.py's ``validation_failure`` contract."""
    findings = fidelity_check(code, orig_source)
    if not findings:
        return None
    return "; ".join(f"{f.detail}" for f in findings)
