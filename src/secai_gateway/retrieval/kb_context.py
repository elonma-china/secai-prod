"""Bounded technical grounding for one exact CVE, with explicitly general references."""
from __future__ import annotations

import asyncio
import re
from typing import Any

import httpx

from ..config import get_settings
from . import neo4j, qdrant
from .filters import ENTITY_ID_RES, build_filter, upper_list

MAX_CONTEXT_CHARS = 6000
PRIMARY_LIMIT = 8
SUPPLEMENTAL_LIMIT = 16
GHSA_LIMIT = 8
GHSA_CONTEXT_BUDGET = 1600
GHSA_SOURCE = "ghsa"
_REF_FIELDS = {
    "cwe_id": ("cwe_id", "cwe_refs"),
    "capec_id": ("capec_id", "capec_entries"),
    "technique_id": ("technique_id", "mitre_techniques"),
}
_CYPHER = """
MATCH (c:CVE {id: $id})
OPTIONAL MATCH (c)-[:HAS_WEAKNESS]->(w:CWE)
WITH c, collect(DISTINCT w.id) AS cwe_refs
OPTIONAL MATCH (c)-[:MAPS_TO_CAPEC]->(p:CAPEC)
WITH c, cwe_refs, collect(DISTINCT p.id) AS capec_entries
OPTIONAL MATCH (c)-[:EXPLOITS_TECHNIQUE]->(t:Technique)
RETURN properties(c) AS cve, cwe_refs, capec_entries,
       collect(DISTINCT t.id) AS mitre_techniques
""".strip()


async def _graph_context(client: httpx.AsyncClient, cve_id: str) -> list[dict[str, Any]]:
    response = await client.post(
        f"{neo4j._neo4j_http_url()}/db/{neo4j.NEO4J_DATABASE}/tx/commit",
        json={"statements": [{"statement": _CYPHER, "parameters": {"id": cve_id}}]},
        headers={"Content-Type": "application/json", "Accept": "application/json", "Authorization": neo4j._auth_header()},
        timeout=15,
    )
    response.raise_for_status()
    data = response.json()
    if data.get("errors"):
        raise ValueError("Neo4j context query failed")
    records = []
    for result in data["results"]:
        columns = result["columns"]
        for item in result["data"]:
            row = dict(zip(columns, item["row"]))
            properties = row["cve"]
            if not isinstance(properties, dict):
                raise ValueError("Invalid Neo4j CVE properties")
            record = dict(properties)
            record["cve_id"] = properties.get("id")
            for field in ("cwe_refs", "capec_entries", "mitre_techniques"):
                record[field] = upper_list(properties.get(field)) + upper_list(row.get(field))
            records.append(record)
    return records


def _body(payload: dict[str, Any]) -> str:
    for field in ("description", "text", "content"):
        value = payload.get(field)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _has_description(payload: dict[str, Any]) -> bool:
    # Mitigation/pattern chunks can accompany a definition; they cannot establish one.
    kind = str(payload.get("chunk_kind") or payload.get("kind") or "").lower()
    if kind and kind not in {"core", "cve", "vulnerability"}:
        return False
    return has_technical_description(_body(payload), str(payload.get("title") or ""))


def has_technical_description(text: str, title: str = "") -> bool:
    """Reject identifiers, formatted metadata and empty-result placeholders."""
    blocks = re.split(r"(?im)^\s*###\s*(?:Result|Row)\s+\d+[^\n]*", text)
    if len(blocks) > 1:
        return any(has_technical_description(block, title) for block in blocks if block.strip())
    if not title:
        match = re.search(r"(?im)^\s*\*\*Title:\*\*\s*([^\n]+)", text)
        if match:
            title = match.group(1).strip()
    if text.strip().casefold() == title.strip().casefold():
        return False
    if re.search(r"(?i)(?:no description (?:is )?available|description (?:is )?(?:unavailable|not available)|(?:qdrant|neo4j) (?:request|query) (?:failed|error))", text):
        return False
    text = text.replace("**", "")
    text = re.sub(r"(?im)^\s*#+\s*(?:row|result)\s+\d+[^\n]*", "", text)
    metadata = r"(?:id|type|entity|cves?(?:[ _]id)?|title|severity|cvss(?:[ _]score)?|vector|year|products?|affected(?:[ _](?:products|versions))?|published|cwes?(?:[ _](?:id|refs|weaknesses))?|capecs?(?:[ _]id)?|techniques?(?:[ _]id)?|tactics?|(?:is[ _]in[ _])?kev|source(?:[ _]url)?)"
    meaningful = []
    skip_list = False
    for line in text.splitlines():
        if re.match(r"^\s*[-*]?\s*" + metadata + r"\s*:", line, re.I):
            skip_list = True
        elif not (skip_list and re.match(r"^\s+[-*]\s", line)):
            skip_list = False
            meaningful.append(line)
        # Pipe-separated metadata can share a line with a usable Description field.
        if "|" in line:
            meaningful.extend(part for part in line.split("|")
                              if not re.match(r"^\s*[-*]?\s*" + metadata + r"\s*:", part, re.I))
    text = "\n".join(meaningful)
    for pattern in ENTITY_ID_RES.values():
        text = pattern.sub("", text)
    text = text.strip(" :-|\n")
    if text.casefold() == title.strip().casefold():
        return False
    return len(re.findall(r"[^\W\d_]+", text)) >= 6 and len(text) >= 30


def _refs(payloads: list[dict[str, Any]]) -> dict[str, list[str]]:
    refs = {}
    for entity, fields in _REF_FIELDS.items():
        refs[entity] = sorted({value for payload in payloads for field in fields
                               for value in upper_list(payload.get(field))
                               if ENTITY_ID_RES[entity].fullmatch(value)})[:16]
    return refs


def _is_linked(payload: dict[str, Any], refs: dict[str, list[str]]) -> bool:
    return any(set(refs[entity]) & set(upper_list(payload.get(field)))
               for entity, fields in _REF_FIELDS.items() for field in fields)


def _format_records(payloads: list[dict[str, Any]], budget: int) -> str:
    sections = []
    seen = set()
    sources_by_text: dict[str, list[str]] = {}
    for payload in payloads:
        key = " ".join(_body(payload).split())
        source = str(payload.get("source_url") or "").strip()
        if source and source not in sources_by_text.setdefault(key, []):
            sources_by_text[key].append(source)
    for payload in payloads:
        text = _body(payload)
        if not text:
            continue
        key = " ".join(text.split())
        if key in seen:
            continue
        seen.add(key)
        labels = [f"Source: {source[:512]}" for source in sources_by_text.get(key, [])[:2]]
        for field in ("source", "kind", "section", "cve_id", "cwe_id", "capec_id",
                      "technique_id", "title", "severity", "cvss_score", "is_in_kev",
                      "year", "products", "tags"):
            if payload.get(field) is not None:
                labels.append(f"{field}: {str(payload[field])[:200]}")
        for entity, values in _refs([payload]).items():
            if values:
                labels.append(f"{entity}: {', '.join(values)}")
        prefix = "\n".join(labels) + ("\n" if labels else "")
        separator = "\n\n" if sections else ""
        available = budget - sum(len(s) for s in sections) - len(separator) - len(prefix)
        if available < 30:
            continue
        sections.append(separator + prefix + (text if len(text) <= available else text[:available - 1] + "…"))
    return "".join(sections)


def _configured_supplemental_collections() -> list[str]:
    raw = get_settings().qdrant_kb_supplemental_collections
    values = raw if isinstance(raw, (list, tuple)) else str(raw or "").split(",")
    primary = qdrant.COLLECTION.casefold()
    return list(dict.fromkeys(
        value.strip() for value in values
        if isinstance(value, str) and value.strip() and value.strip().casefold() != primary
    ))


async def _ghsa_advisories(
    client: httpx.AsyncClient, cve_id: str, collections: list[str],
) -> list[dict[str, Any]]:
    """Read optional GHSA patch/advisory chunks linked to this exact CVE.

    These records enrich a usable primary CVE description; they never establish that a CVE
    exists. Each collection is independently fail-open and is queried without embeddings.
    """
    qfilter = {"must": [
        {"key": "source", "match": {"value": GHSA_SOURCE}},
        {"key": "cve_refs", "match": {"value": cve_id}},
    ]}
    tasks = [qdrant._scroll_by_filter(client, qfilter, GHSA_LIMIT, collection)
             for collection in collections]
    if not tasks:
        return []
    results = await asyncio.gather(*tasks, return_exceptions=True)
    records = []
    for collection, result in zip(collections, results):
        if isinstance(result, asyncio.CancelledError):
            raise result
        if isinstance(result, Exception):
            continue
        for _point_id, payload in result:
            source = str(payload.get("source") or "").strip().casefold()
            refs = {value.upper() for value in upper_list(payload.get("cve_refs"))}
            if source == GHSA_SOURCE and refs == {cve_id} and _body(payload):
                record = dict(payload)
                record["supplemental_collection"] = collection
                records.append(record)
    # Reserve scarce prompt budget for explicit patched-version records before advisory prose.
    records.sort(key=lambda record: str(record.get("section") or "").casefold() != "patched-versions")
    return records


async def get_cve_context(cve_id: str) -> dict[str, Any]:
    """Return status/content/cwe_refs; ``found`` requires a usable exact CVE definition.

    Service failure remains ``error`` when no definition is usable. ``insufficient``
    denotes an exact record without technical description; ``not_found`` denotes
    successful empty lookups. Linked references never upgrade those outcomes.
    """
    cve_id = (cve_id or "").strip().upper()
    empty = {"status": "not_found", "content": "", "cwe_refs": []}
    if not ENTITY_ID_RES["cve_id"].fullmatch(cve_id):
        return empty
    async with httpx.AsyncClient() as client:
        qresult, nresult = await asyncio.gather(
            qdrant._scroll_by_filter(client, build_filter({"cve_id": cve_id}), PRIMARY_LIMIT),
            _graph_context(client, cve_id), return_exceptions=True,
        )
        errors = isinstance(qresult, Exception) or isinstance(nresult, Exception)
        payloads = ([] if isinstance(qresult, Exception) else [p for _, p in qresult])
        payloads += [] if isinstance(nresult, Exception) else nresult
        exact = [p for p in payloads if str(p.get("cve_id") or "").upper() == cve_id]
        usable = [p for p in exact if _has_description(p)]
        refs = _refs(exact)
        if not usable:
            return {**empty, "status": "error" if errors else "insufficient" if exact else "not_found", "cwe_refs": refs["cwe_id"]}

        supplemental = []
        ghsa = []
        collections = _configured_supplemental_collections()
        if collections:
            ghsa = await _ghsa_advisories(client, cve_id, collections)
        should = [{"key": field, "match": {"any": refs[entity]}}
                  for entity, fields in _REF_FIELDS.items() if refs[entity] for field in fields]
        if should:
            qfilter = {"should": should, "must": [{"should": [
                {"is_empty": {"key": "cve_id"}}, {"key": "cve_id", "match": {"value": cve_id}},
            ]}]}
            try:
                hits = await qdrant._scroll_by_filter(client, qfilter, SUPPLEMENTAL_LIMIT)
                for _, payload in hits:
                    cv = str(payload.get("cve_id") or "").upper()
                    other_ids = {m.upper() for m in ENTITY_ID_RES["cve_id"].findall(_body(payload))}
                    other_ids.update(upper_list(payload.get("cve_ids")))
                    if (not cv or cv == cve_id) and not (other_ids - {cve_id}) and _is_linked(payload, refs):
                        supplemental.append(payload)
            except Exception:
                pass  # Exact grounding remains usable if optional references fail.

    primary_header = f"Exact CVE technical context: {cve_id}\n"
    primary = _format_records(usable, 4200 - len(primary_header))
    if not primary:
        return {**empty, "status": "insufficient", "cwe_refs": refs["cwe_id"]}
    content = primary_header + primary
    if ghsa:
        ghsa_header = "\n\nCVE-linked GHSA advisory/patch context (supplemental evidence):\n"
        ghsa_budget = min(GHSA_CONTEXT_BUDGET, MAX_CONTEXT_CHARS - len(content) - len(ghsa_header))
        ghsa_text = _format_records(ghsa, ghsa_budget)
        if ghsa_text:
            content += ghsa_header + ghsa_text
    if supplemental:
        general_header = "\n\nGeneral knowledge (linked references; not CVE-specific evidence):\n"
        general = _format_records(supplemental, MAX_CONTEXT_CHARS - len(content) - len(general_header))
        if general:
            content += general_header + general
    return {"status": "found", "content": content, "cwe_refs": refs["cwe_id"]}
