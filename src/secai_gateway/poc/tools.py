"""Linch tools exposing the read-only PoC catalog and explicitly gated runners."""
from __future__ import annotations

import hashlib
import json
from typing import Any

from linch.tools.base import ToolContext, ToolResult
from linch.tools.function import tool

from ..state import SqliteLedger
from .backend import LocalPocBackend, PocError

_DRAFT = "Unverified lab draft: validate only in an authorized, isolated environment."


def record_fetch_evidence(
    ledger: SqliteLedger,
    run_id: str,
    idempotency_key: str,
    result: dict[str, Any],
) -> None:
    """Persist a successful fetch in the shape consumed by evidence injection.

    The identifier is scoped to the run: the same artifact fetched by independent
    runs must remain available to both reports.
    """
    sha256 = str(result.get("sha256") or "")
    if not sha256:
        return
    evidence_id = f"evidence:{run_id}:{sha256[:24]}"
    evidence = dict(result)
    evidence["draft_not_executed"] = True
    evidence["draft_notice"] = _DRAFT
    evidence["canonical_url"] = result.get("url") or ""
    if result.get("source") == "exploitdb":
        evidence["edb_id"] = result.get("source_ref")
    ledger.save_evidence(evidence_id, run_id, evidence)
    payload = json.dumps(result, ensure_ascii=False, sort_keys=True, default=str)
    ledger.audit_once(
        idempotency_key or f"{run_id}:poc_fetch:{sha256[:24]}",
        run_id,
        "poc_fetch",
        hashlib.sha256(payload.encode()).hexdigest(),
        {"evidence_id": evidence_id, "sha256": sha256, "source": result.get("source")},
    )


def _ctx_run_id(ctx: ToolContext) -> str:
    return str(getattr(ctx, "run_id", None) or getattr(ctx, "session_id", None) or "")


def _ctx_idempotency_key(ctx: ToolContext, suffix: str) -> str:
    return str(getattr(ctx, "idempotency_key", None) or f"{_ctx_run_id(ctx)}:{suffix}")


def _json_result(content: dict[str, Any], summary: str) -> ToolResult:
    return ToolResult(content=json.dumps(content, ensure_ascii=False), summary=summary)


def _error_result(tool_name: str, exc: Exception) -> ToolResult:
    return ToolResult(
        content=json.dumps({"error": str(exc)}, ensure_ascii=False),
        summary=f"{tool_name} error",
        is_error=True,
    )


def build_poc_tools(
    poc: LocalPocBackend,
    ledger: SqliteLedger,
    settings: Any = None,
) -> dict[str, Any]:
    """Build catalog tools plus feature-flagged active validation tools."""

    @tool(
        name="poc_search",
        description=(
            "Search the local, read-only PoC catalog. Use a CVE ID or concise query; "
            "pass a returned ref to poc_fetch in the same run."
        ),
        scope="read",
    )
    async def poc_search(query: str, ctx: ToolContext, max_results: int = 10) -> ToolResult:
        try:
            result = await poc.search(query, max(1, min(int(max_results), 50)), _ctx_run_id(ctx))
            hits = result.get("hits", [])
            return _json_result(result, f"poc_search({query}) {len(hits)} hits")
        except (PocError, ValueError, TypeError) as exc:
            return _error_result("poc_search", exc)

    @tool(
        name="poc_fetch",
        description=(
            "Fetch static evidence for a ref previously returned by poc_search. "
            "Never executes an artifact; source is returned only when license policy permits."
        ),
        scope="read",
    )
    async def poc_fetch(ref: str, ctx: ToolContext) -> ToolResult:
        run_id = _ctx_run_id(ctx)
        try:
            result = await poc.fetch(ref, run_id)
            record_fetch_evidence(ledger, run_id, _ctx_idempotency_key(ctx, "poc_fetch"), result)
            return _json_result(result, f"poc_fetch({ref})")
        except PocError as exc:
            return _error_result("poc_fetch", exc)

    tools: dict[str, Any] = {
        "poc_search": poc_search,
        "poc_fetch": poc_fetch,
    }
    if settings is None:
        return tools

    if getattr(settings, "verify_enabled", False):
        from .verify import verify_poc
        from .runners.toolbox import run_tool

        @tool(
            name="poc_verify",
            description="Verify a runnable nuclei or Metasploit PoC against an authorized target.",
            scope="write",
        )
        async def poc_verify(
            target: str,
            ctx: ToolContext,
            cve: str | None = None,
            ref: str | None = None,
            source: str | None = None,
            rport: int | None = None,
        ) -> ToolResult:
            try:
                result = await verify_poc(
                    poc, settings, target=target, run_id=_ctx_run_id(ctx), cve=cve,
                    ref=ref, source=source, rport=rport,
                )
                return _json_result(result, f"poc_verify({target}) {result.get('verdict', 'done')}")
            except PocError as exc:
                return _error_result("poc_verify", exc)

        @tool(
            name="poc_scan",
            description="Run an authorized scoped scanner (nmap, searchsploit, gobuster, sqlmap, hydra).",
            scope="write",
        )
        async def poc_scan(
            tool_name: str,
            ctx: ToolContext,
            target: str | None = None,
            ports: str | None = None,
            query: str | None = None,
            wordlist: str | None = None,
            threads: int | None = None,
            service: str | None = None,
            username: str | None = None,
            password: str | None = None,
        ) -> ToolResult:
            params = {
                key: value for key, value in {
                    "target": target, "ports": ports, "query": query, "wordlist": wordlist,
                    "threads": threads, "service": service, "username": username, "password": password,
                }.items() if value is not None
            }
            try:
                result = await run_tool(settings, tool_name, params, run_id=_ctx_run_id(ctx))
                return _json_result(result, f"poc_scan({tool_name})")
            except PocError as exc:
                return _error_result("poc_scan", exc)

        tools.update(poc_verify=poc_verify, poc_scan=poc_scan)

    if getattr(settings, "exec_enabled", False):
        from .runners.toolbox import run_raw_command

        @tool(
            name="poc_exec",
            description="Run a raw command in the configured Kali executor. Restricted deployment feature.",
            scope="write",
        )
        async def poc_exec(
            ctx: ToolContext,
            cmd: list[str] | None = None,
            shell: str | None = None,
            timeout_s: int | None = None,
            privileged: bool | None = None,
        ) -> ToolResult:
            try:
                result = await run_raw_command(
                    settings, argv=cmd, shell=shell, timeout_s=timeout_s,
                    privileged=privileged, run_id=_ctx_run_id(ctx),
                )
                return _json_result(result, "poc_exec")
            except PocError as exc:
                return _error_result("poc_exec", exc)

        tools["poc_exec"] = poc_exec

    if (
        getattr(settings, "poc_exploit_enabled", False)
        and str(getattr(settings, "poc_exploit_api_url", "") or "").strip()
    ):
        from .kt_exploit import run_kt_exploit

        @tool(
            name="poc_kt_exploit",
            description="Run the deterministic KT exploit service against an authorized target and CVE.",
            scope="write",
        )
        async def poc_kt_exploit(
            ip: str,
            cve: str,
            ctx: ToolContext,
            port: str | int | None = None,
            command: str | None = "id",
            use_proxy: bool = True,
        ) -> ToolResult:
            try:
                result = await run_kt_exploit(
                    settings, ip=ip, cve=cve, port=port, command=command,
                    use_proxy=use_proxy, run_id=_ctx_run_id(ctx),
                )
                return _json_result(result, f"poc_kt_exploit({cve}) {result.get('exploit_verdict', 'done')}")
            except PocError as exc:
                return _error_result("poc_kt_exploit", exc)

        tools["poc_kt_exploit"] = poc_kt_exploit

    return tools
