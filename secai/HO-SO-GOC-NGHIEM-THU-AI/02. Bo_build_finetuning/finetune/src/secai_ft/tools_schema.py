"""Serving tool schemas, snapshotted for train/serve parity.
"""
from __future__ import annotations

import json
from typing import Any

_QDRANT_SCHEMA = {
    "type": "object",
    "properties": {
        "query": {"type": "string", "description": "Natural language search query about CVE, CWE, CAPEC, or MITRE ATT&CK."},
        "top_k": {"type": "integer", "description": "Number of results after rerank+boost (default 5, max 20)."},
        "kind": {"type": "array", "items": {"type": "string", "enum": ["core", "context", "pattern", "technique", "weakness"]},
                 "description": "Filter by chunk kind. core=CVE main; context=CVE metadata; pattern=CAPEC; technique=MITRE; weakness=CWE."},
        "cve_id": {"type": "string", "description": "Exact CVE ID filter, e.g. CVE-2024-12345."},
        "cwe_id": {"type": "string", "description": "Exact CWE ID filter (matches cwe_refs[]), e.g. CWE-79."},
        "capec_id": {"type": "string", "description": "Exact CAPEC ID filter (matches capec_entries[]), e.g. CAPEC-66."},
        "technique_id": {"type": "string", "description": "Exact MITRE ATT&CK technique ID filter (matches mitre_techniques[]), e.g. T1059.001."},
        "product": {"type": "string", "description": "Exact product name filter (matches products[]). E.g. 'linux_kernel'. NOT substring."},
        "severity": {"type": "array", "items": {"type": "string", "enum": ["CRITICAL", "HIGH", "MEDIUM", "LOW"]},
                     "description": "Filter by severity. Defaults to all severities."},
        "year": {"type": "array", "items": {"type": "string"}, "description": "Filter by CVE publication year (string). E.g. ['2024','2025']."},
        "kev_only": {"type": "boolean", "description": "If true, only return CVEs in CISA Known Exploited Vulnerabilities catalog."},
    },
    "required": ["query"],
}

_NEO4J_SCHEMA = {
    "type": "object",
    "properties": {
        "query": {
            "type": "string",
            "description": (
                "ID-based query: CVE-XXXX-XXXX, CWE-N, CAPEC-N, Tnnnn[.nnn], TAnnnn — OR a "
                "raw Cypher statement. For free-text keywords use qdrant_search first to "
                "obtain an ID."
            ),
        },
        "depth": {"type": "integer", "description": "Traversal LIMIT multiplier (1-5). Default 3 -> LIMIT 30. Max LIMIT 50."},
    },
    "required": ["query"],
}

_POC_SCHEMA = {
    "type": "object",
    "properties": {
        "query": {"type": "string", "description": "CVE-ID (e.g. 'CVE-2021-44228') or keyword (e.g. 'apache 2.4 RCE')"},
        "max_results": {"type": "integer", "description": "Max PoC entries to return (default: 10, max: 50)"},
    },
    "required": ["query"],
}

_POC_FETCH_SCHEMA = {
    "type": "object",
    "properties": {
        "ref": {
            "type": "string",
            "description": "Opaque PoC reference returned by poc_search, e.g. 'poc:abc123...'.",
        },
    },
    "required": ["ref"],
}

TOOLS_SCHEMA: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "qdrant_search",
            "description": (
                "Search the cybersecurity knowledge base (CVE/CWE/CAPEC/MITRE ATT&CK) via hybrid "
                "dense retrieval over Qdrant. Use for free-text queries about vulnerabilities, "
                "weaknesses, attack patterns, or techniques."
            ),
            "parameters": _QDRANT_SCHEMA,
        },
    },
    {
        "type": "function",
        "function": {
            "name": "neo4j_search",
            "description": (
                "Traverse the CVE/CWE/CAPEC/MITRE-ATT&CK knowledge graph in Neo4j by exact ID. "
                "Requires a CVE-ID, CWE-ID, CAPEC-ID, MITRE technique/tactic ID, or raw Cypher — "
                "does NOT support free-text (use qdrant_search first to resolve an ID)."
            ),
            "parameters": _NEO4J_SCHEMA,
        },
    },
    {
        "type": "function",
        "function": {
            "name": "poc_search",
            "description": (
                "Search Exploit-DB (~47k entries) for public proof-of-concept exploits by CVE-ID or "
                "keyword. Use to check whether a vulnerability has known public exploit code."
            ),
            "parameters": _POC_SCHEMA,
        },
    },
    {
        "type": "function",
        "function": {
            "name": "poc_fetch",
            "description": (
                "Fetch the full details for one public proof-of-concept result returned by poc_search. "
                "Use its opaque ref exactly as returned; do not invent refs."
            ),
            "parameters": _POC_FETCH_SCHEMA,
        },
    },
]


def normalize_tool_calls(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return a copy of `messages` with assistant tool_call arguments as dicts.

    On disk, `tool_calls[].function.arguments` is a JSON *string* (that is how the OpenAI
    wire format carries it). Qwen3's chat template runs `arguments | tojson`, so a string
    argument gets double-encoded into a quoted JSON literal — the model would learn to emit
    `"{\\"query\\": ...}"` instead of `{"query": ...}`. Decoding to a dict here makes the
    rendered tool call match what the serving parser expects. Non-tool messages pass through
    untouched.
    """
    out: list[dict[str, Any]] = []
    for m in messages:
        tcs = m.get("tool_calls")
        if not tcs:
            out.append(m)
            continue
        m2 = dict(m)
        new_tcs = []
        for tc in tcs:
            fn = tc.get("function", {})
            args = fn.get("arguments")
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except (json.JSONDecodeError, TypeError):
                    pass  # leave as-is if it is not valid JSON
            new_tcs.append({**tc, "function": {**fn, "arguments": args}})
        m2["tool_calls"] = new_tcs
        out.append(m2)
    return out


def verify_against_live() -> None:
    """Assert this snapshot matches the live agentic_rag tool objects. No-op importless call
    is fine; raises AssertionError on drift. Only importable where agentic_rag + linch are."""
    from agentic_rag.tools.neo4j_search import neo4j_search
    from agentic_rag.tools.poc_search import poc_fetch, poc_search
    from agentic_rag.tools.qdrant_search import qdrant_search

    live = {t.name: t for t in (qdrant_search, neo4j_search, poc_search, poc_fetch)}
    for spec in TOOLS_SCHEMA:
        fn = spec["function"]
        name = fn["name"]
        assert name in live, f"unknown tool {name}"
        lt = live[name]
        assert fn["description"] == lt.description, f"{name} description drifted"
        assert fn["parameters"] == lt.input_schema, f"{name} input_schema drifted"
