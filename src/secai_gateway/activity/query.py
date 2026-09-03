"""Turn UI filters into Elasticsearch query bodies.

Kept as pure functions (no I/O) so the query shape is unit-testable without Elasticsearch.
"""
from __future__ import annotations

from typing import Any

# Fields the UI is allowed to search on by name. An allowlist rather than passing the
# field through: it keeps a crafted `field=` from probing arbitrary parts of the document,
# and each entry maps to the sub-field that actually searches well.
SEARCHABLE_FIELDS: dict[str, str] = {
    "all": "",                                  # multi_match across the text fields
    "question": "question",
    "answer": "answer",
    "user_id": "user_id",
    "conversation_id": "conversation_id",
    "request_id": "request_id",
    "run_id": "run_id",
    "client_ip": "client_ip",
    "user_agent": "user_agent.text",
    "server_hostname": "server_hostname",
}

# Exact-match filters (keyword/boolean fields) the sidebar and dropdowns drive.
TERM_FILTERS = ("function", "profile", "endpoint", "user_id", "conversation_id",
                "client_ip", "server_hostname", "request_id", "run_id")

_FULLTEXT_FIELDS = ["question^2", "answer", "user_agent.text"]
_KEYWORD_FIELDS = {"user_id", "conversation_id", "request_id", "run_id", "client_ip",
                   "server_hostname"}


def build_search_body(
    *,
    date_from: str | None = None,
    date_to: str | None = None,
    q: str | None = None,
    field: str = "all",
    filters: dict[str, Any] | None = None,
    blocked: bool | None = None,
    error: bool | None = None,
    size: int = 50,
    search_after: list[Any] | None = None,
    ascending: bool = False,
) -> dict[str, Any]:
    """Build an ES search body.

    Sort is always the total order ``(@timestamp, request_id)`` so ``search_after``
    paginates without gaps or repeats — the same property the realtime tail relies on to
    never re-send or skip a record.

    The tiebreaker is ``request_id`` (a keyword, unique per request) rather than the more
    obvious ``_id``: Elasticsearch 8 refuses to sort on ``_id`` ("Fielddata access on the
    _id field is disallowed"), which a shape-only unit test cannot catch — this was found by
    running the query against a real cluster.
    """
    must: list[dict[str, Any]] = []
    filt: list[dict[str, Any]] = []

    if date_from or date_to:
        rng: dict[str, Any] = {}
        if date_from:
            rng["gte"] = date_from
        if date_to:
            rng["lte"] = date_to
        filt.append({"range": {"@timestamp": rng}})

    for key, value in (filters or {}).items():
        if key in TERM_FILTERS and value not in (None, ""):
            filt.append({"term": {key: value}})

    if blocked is not None:
        filt.append({"term": {"blocked": blocked}})
    if error is not None:
        filt.append({"term": {"error": error}})

    text = (q or "").strip()
    if text:
        target = SEARCHABLE_FIELDS.get(field, "")
        if not target:
            must.append({"multi_match": {"query": text, "fields": _FULLTEXT_FIELDS,
                                         "type": "best_fields", "operator": "and"}})
        elif target in _KEYWORD_FIELDS:
            # Keyword fields aren't analyzed: match_phrase would never hit. Use a prefix so
            # a partial id/IP still finds its rows, which is how people actually search.
            must.append({"prefix": {target: text}})
        else:
            must.append({"match": {target: {"query": text, "operator": "and"}}})

    order = "asc" if ascending else "desc"
    body: dict[str, Any] = {
        "size": max(1, min(int(size), 500)),
        "query": {"bool": {"must": must or [{"match_all": {}}], "filter": filt}},
        "sort": [{"@timestamp": order}, {"request_id": order}],
        "track_total_hits": 10_000,
    }
    if search_after:
        body["search_after"] = search_after
    return body


def build_facets_body(**kw: Any) -> dict[str, Any]:
    """Same filters, zero hits, just the counts the sidebar shows."""
    body = build_search_body(size=1, **kw)
    body["size"] = 0
    body.pop("sort", None)
    body.pop("search_after", None)
    body["aggs"] = {
        "functions": {"terms": {"field": "function", "size": 20}},
        "blocked": {"terms": {"field": "blocked", "size": 2}},
        "errors": {"terms": {"field": "error", "size": 2}},
        "top_users": {"terms": {"field": "user_id", "size": 10}},
        "top_ips": {"terms": {"field": "client_ip", "size": 10}},
    }
    return body
