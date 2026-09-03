"""Filter → Elasticsearch DSL translation for the /logs viewer."""
from __future__ import annotations

from secai_gateway.activity.query import build_facets_body, build_search_body


def test_default_body_is_a_newest_first_match_all() -> None:
    body = build_search_body()
    assert body["query"]["bool"]["must"] == [{"match_all": {}}]
    assert body["sort"] == [{"@timestamp": "desc"}, {"request_id": "desc"}]


def test_date_range_becomes_a_range_filter() -> None:
    body = build_search_body(date_from="2026-09-01T00:00:00Z", date_to="2026-09-02T00:00:00Z")
    assert {"range": {"@timestamp": {"gte": "2026-09-01T00:00:00Z",
                                     "lte": "2026-09-02T00:00:00Z"}}} in body["query"]["bool"]["filter"]


def test_term_filters_are_allowlisted() -> None:
    body = build_search_body(filters={"function": "chat", "user_id": "u1",
                                      "not_a_field": "x", "answer": "hack"})
    filters = body["query"]["bool"]["filter"]
    assert {"term": {"function": "chat"}} in filters
    assert {"term": {"user_id": "u1"}} in filters
    # Anything outside TERM_FILTERS is ignored rather than passed through to ES.
    assert all("not_a_field" not in str(f) and "answer" not in str(f) for f in filters)


def test_freetext_searches_the_text_fields() -> None:
    body = build_search_body(q="sql injection")
    must = body["query"]["bool"]["must"][0]
    assert "multi_match" in must
    assert "question^2" in must["multi_match"]["fields"]


def test_field_scoped_text_search_uses_match() -> None:
    body = build_search_body(q="phòng chống", field="question")
    assert body["query"]["bool"]["must"][0] == {
        "match": {"question": {"query": "phòng chống", "operator": "and"}}}


def test_keyword_field_search_uses_prefix() -> None:
    """Keyword fields aren't analyzed — a `match` on a partial id would never hit, so a
    partial IP/id search has to be a prefix query to be useful."""
    body = build_search_body(q="10.0.0", field="client_ip")
    assert body["query"]["bool"]["must"][0] == {"prefix": {"client_ip": "10.0.0"}}


def test_unknown_field_falls_back_to_fulltext() -> None:
    body = build_search_body(q="abc", field="../etc/passwd")
    assert "multi_match" in body["query"]["bool"]["must"][0]


def test_boolean_filters_and_pagination() -> None:
    body = build_search_body(blocked=True, error=False, size=10, search_after=[1, "x"])
    filters = body["query"]["bool"]["filter"]
    assert {"term": {"blocked": True}} in filters
    assert {"term": {"error": False}} in filters
    assert body["search_after"] == [1, "x"]
    assert body["size"] == 10


def test_size_is_bounded() -> None:
    assert build_search_body(size=10_000)["size"] == 500
    assert build_search_body(size=0)["size"] == 1


def test_ascending_sort_for_the_realtime_tail() -> None:
    body = build_search_body(ascending=True)
    # Sorting on _id is rejected by ES 8, so request_id is the tiebreaker.
    assert body["sort"] == [{"@timestamp": "asc"}, {"request_id": "asc"}]


def test_facets_body_asks_for_counts_only() -> None:
    body = build_facets_body(q="x")
    assert body["size"] == 0 and "sort" not in body
    assert set(body["aggs"]) == {"functions", "blocked", "errors", "top_users", "top_ips"}
