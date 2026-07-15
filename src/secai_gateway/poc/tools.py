from __future__ import annotations

import hashlib
import json
from typing import Any

from linch.tools.base import ToolContext, ToolResult
from linch.tools.function import tool

from ..state import SqliteLedger
from .backend import LocalPocBackend, PocError

_DRAFT = "Unverified lab draft: validate only in an authorized, isolated environment."


def build_poc_tools(poc: LocalPocBackend, ledger: SqliteLedger) -> dict[str, Any]:
    """The 2-tool PoC surface: search → fetch(ref). `poc_analyze`/`poc_generate_draft`
    are folded into `fetch` (fewer turns for small models, fewer fabrication points)."""

    @tool(name="poc_search",
          description="Find public PoC/exploit metadata by CVE-ID or keyword across the local "
                      "catalog (Exploit-DB and more). Returns hits, each with an opaque `ref` — "
                      "pass a ref to poc_fetch.", scope="read")
    async def poc_search(query: str, ctx: ToolContext, max_results: int = 10) -> ToolResult:
        try:
            result = await poc.search(query, max_results, ctx.run_id)
        except PocError as exc:
            return ToolResult(content=f"poc_search unavailable: {exc}", summary="poc_search", is_error=True)
        n = len(result.get("hits", []))
        ledger.audit_once(ctx.idempotency_key, ctx.run_id, "poc_search",
                          hashlib.sha256(query.encode()).hexdigest(), {"count": n})
        return ToolResult(content=json.dumps(result), summary=f"poc_search({query}) {n} hits")

    @tool(name="poc_fetch",
          description="Fetch analysis for a PoC using the `ref` returned by poc_search "
                      "(e.g. ref='poc:ab12…', NOT a numeric id). Returns metadata + static "
                      "analysis + a non-executable lab-draft marker in one call. `source_code` "
                      "is populated with the REAL fetched artifact text when the source's "
                      "license allows it (exploitdb/metasploit/nuclei/ghsa); otherwise it is "
                      "null — quote/adapt `source_code` when present, never invent code when "
                      "it is null. Capped at a few calls per run — pick the best hits, don't "
                      "fetch every result.", scope="read")
    async def poc_fetch(ref: str, ctx: ToolContext) -> ToolResult:
        try:
            result = await poc.fetch(ref, ctx.run_id)
        except PocError as exc:
            return ToolResult(content=f"poc_fetch failed: {exc}", summary="poc_fetch", is_error=True)
        # Preserve exploit_metadata(): record evidence + a draft_not_executed marker.
        ev_id = f"evidence:{result['sha256'][:24]}"
        ledger.save_evidence(ev_id, ctx.run_id, {
            "edb_id": result["source_ref"], "sha256": result["sha256"],
            "canonical_url": result["url"], **result})
        ledger.draft_once(ctx.idempotency_key, ctx.run_id, _DRAFT, {
            "candidate_status": "draft_not_executed",
            "edb_id": result["source_ref"], "sha256": result["sha256"]})
        return ToolResult(content=json.dumps(result),
                          summary=f"poc_fetch({result['source']}:{result['source_ref']})")

    return {t.name: t for t in (poc_search, poc_fetch)}
