"""Unit tests for the durable ConversationStore (SQLite index + transcript)."""
from __future__ import annotations

import tempfile

import pytest

from secai_gateway.state import ConversationStore


@pytest.fixture()
def store() -> ConversationStore:
    return ConversationStore(tempfile.mktemp(suffix=".db"))


def test_create_and_get(store: ConversationStore) -> None:
    store.create("ownerA", "c1", "chat:conv:ownerA:c1", "", 100)
    row = store.get("c1")
    assert row is not None
    assert row["owner_hash"] == "ownerA"
    assert row["session_id"] == "chat:conv:ownerA:c1"
    assert row["message_count"] == 0 and row["deleted"] == 0


def test_append_turn_bumps_count_and_sets_title(store: ConversationStore) -> None:
    store.create("ownerA", "c1", "sid", "", 100)
    store.append_turn("c1", "Giải thích CVE-2021-44228", "Log4Shell là...", 101)
    store.append_turn("c1", "còn gì nữa", "RCE", 102)
    row = store.get("c1")
    assert row["message_count"] == 4          # 2 messages per turn
    assert row["title"] == "Giải thích CVE-2021-44228"  # derived from first user message
    assert row["updated_at"] == 102
    msgs, total = store.messages("c1", 100, 0)
    assert total == 4
    assert [m["role"] for m in msgs] == ["user", "assistant", "user", "assistant"]
    assert msgs[0]["content"] == "Giải thích CVE-2021-44228"
    assert msgs[1]["content"] == "Log4Shell là..."


def test_explicit_title_not_overwritten(store: ConversationStore) -> None:
    store.create("ownerA", "c1", "sid", "Tiêu đề của tôi", 100)
    store.append_turn("c1", "một câu hỏi", "một trả lời", 101)
    assert store.get("c1")["title"] == "Tiêu đề của tôi"


def test_list_owner_isolation_and_ordering(store: ConversationStore) -> None:
    store.create("ownerA", "a1", "sid", "", 100)
    store.create("ownerA", "a2", "sid", "", 100)
    store.create("ownerB", "b1", "sid", "", 100)
    store.append_turn("a1", "hi", "yo", 200)   # a1 updated latest → should sort first
    store.append_turn("a2", "hi", "yo", 150)
    rows, total = store.list_for_owner("ownerA", 20, 0)
    assert total == 2
    assert [r["conversation_id"] for r in rows] == ["a1", "a2"]   # newest updated_at first
    # ownerB's conversation never appears in ownerA's listing.
    assert all(r["conversation_id"] != "b1" for r in rows)
    rows_b, total_b = store.list_for_owner("ownerB", 20, 0)
    assert total_b == 1 and rows_b[0]["conversation_id"] == "b1"


def test_list_pagination(store: ConversationStore) -> None:
    for i in range(5):
        store.create("ownerA", f"c{i}", "sid", "", 100 + i)
    rows, total = store.list_for_owner("ownerA", 2, 0)
    assert total == 5 and len(rows) == 2
    page2, _ = store.list_for_owner("ownerA", 2, 2)
    assert len(page2) == 2
    assert {r["conversation_id"] for r in rows} & {r["conversation_id"] for r in page2} == set()


def test_owns_and_soft_delete(store: ConversationStore) -> None:
    store.create("ownerA", "c1", "sid", "", 100)
    assert store.owns("ownerA", "c1") is True
    assert store.owns("ownerB", "c1") is False   # wrong owner
    assert store.owns("ownerA", "nope") is False  # missing
    assert store.owns("", "c1") is False          # anonymous never owns
    store.soft_delete("c1")
    assert store.owns("ownerA", "c1") is False     # soft-deleted no longer owned
    assert store.list_for_owner("ownerA", 20, 0)[1] == 0


def test_recent_turns_oldest_first(store: ConversationStore) -> None:
    store.create("ownerA", "c1", "sid", "", 100)
    store.append_turn("c1", "u1", "a1", 101)
    store.append_turn("c1", "u2", "a2", 102)
    store.append_turn("c1", "u3", "a3", 103)
    recent = store.recent_turns("c1", 3)   # last 3 messages, oldest-first
    assert [t["content"] for t in recent] == ["a2", "u3", "a3"]
    assert store.recent_turns("c1", 0) == []
