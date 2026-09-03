"""Activity-log record shape + the Elasticsearch index template it is written against.

One record == one AI request (question in, answer out), regardless of whether that request
was streamed. The field set answers the operational questions the gateway could not answer
before: who asked what, from which machine, through which function, how long it took, and
whether it failed or was blocked.
"""
from __future__ import annotations

import socket
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

# Resolved once — this is the *server* hostname (the container/host running the gateway),
# which is what "tên máy" means for a server-side log. The client's own machine name is not
# knowable over HTTP; client_ip + user_agent identify the caller instead.
SERVER_HOSTNAME = socket.gethostname()


def _clip(text: str | None, limit: int) -> tuple[str, bool]:
    """Return (text, truncated). Guards the index against one pathological answer (a full
    exploit report can be hundreds of KB) rather than rejecting or dropping the record."""
    s = text or ""
    if len(s) <= limit:
        return s, False
    return s[:limit], True


@dataclass(slots=True)
class LogRecord:
    """One finished AI request. Built by ``LogContext.finish()``; serialized by ``to_doc()``."""

    request_id: str
    function: str                     # chat | conversation_chat | translate | report | ...
    endpoint: str                     # /ai/chat
    http_method: str = "POST"
    profile: str = ""                 # LLM profile actually run
    stream: bool = False

    client_ip: str = ""
    client_forwarded_for: str = ""
    user_agent: str = ""

    user_id: str = ""
    conversation_id: str = ""
    run_id: str = ""

    question: str = ""
    answer: str = ""

    duration_ms: float = 0.0
    http_status: int = 200
    error: bool = False
    error_detail: str = ""
    blocked: bool = False             # short-circuited by the input gate
    block_reason: str = ""            # classifier label/action, or unsupported_language
    tools_called: list[str] = field(default_factory=list)
    started_at: datetime | None = None

    def to_doc(self, *, max_field_chars: int) -> dict[str, Any]:
        question, q_trunc = _clip(self.question, max_field_chars)
        answer, a_trunc = _clip(self.answer, max_field_chars)
        ts = self.started_at or datetime.now(timezone.utc)
        # Always present: filtering/sorting in the UI depends on these existing even when
        # falsy (`error: false` must be searchable, `duration_ms: 0` must sort).
        doc: dict[str, Any] = {
            "@timestamp": ts.astimezone(timezone.utc).isoformat(),
            "server_hostname": SERVER_HOSTNAME,
            "request_id": self.request_id,
            "function": self.function,
            "endpoint": self.endpoint,
            "http_method": self.http_method,
            "stream": self.stream,
            "question": question,
            "answer": answer,
            "question_len": len(self.question or ""),
            "answer_len": len(self.answer or ""),
            "truncated": q_trunc or a_trunc,
            "duration_ms": round(self.duration_ms, 2),
            "http_status": self.http_status,
            "error": self.error,
            "blocked": self.blocked,
        }
        # Optional: omitted when empty so `exists` queries stay meaningful and the `ip`
        # field never receives "" (which it would reject).
        optional = {
            "profile": self.profile,
            "client_ip": self.client_ip,
            "client_forwarded_for": self.client_forwarded_for,
            "user_agent": self.user_agent,
            "user_id": self.user_id,
            "conversation_id": self.conversation_id,
            "run_id": self.run_id,
            "error_detail": self.error_detail,
            "block_reason": self.block_reason,
            "tools_called": self.tools_called,
        }
        doc.update({k: v for k, v in optional.items() if v})
        return doc


# ── Elasticsearch index template ──────────────────────────────────────────────────────────
# `vi_text` folds diacritics (asciifolding) so "phong chong" finds "phòng chống" — real
# Vietnamese word segmentation needs a plugin, which would mean a custom ES image; folding
# covers the common case while keeping the stock image (and therefore an offline-friendly
# deployment). refresh_interval 1s is what makes a newly logged request visible to the UI
# within the ~1-2s realtime target.
INDEX_SETTINGS: dict[str, Any] = {
    "number_of_shards": 1,
    "number_of_replicas": 0,          # single node
    "refresh_interval": "1s",
    "analysis": {
        "analyzer": {
            "vi_text": {
                "type": "custom",
                "tokenizer": "standard",
                "filter": ["lowercase", "asciifolding"],
            }
        }
    },
}

INDEX_MAPPINGS: dict[str, Any] = {
    "properties": {
        "@timestamp": {"type": "date"},
        "server_hostname": {"type": "keyword"},
        "request_id": {"type": "keyword"},
        "function": {"type": "keyword"},
        "endpoint": {"type": "keyword"},
        "http_method": {"type": "keyword"},
        "profile": {"type": "keyword"},
        "stream": {"type": "boolean"},
        "client_ip": {"type": "ip", "ignore_malformed": True},
        "client_forwarded_for": {"type": "keyword"},
        "user_agent": {"type": "keyword", "fields": {"text": {"type": "text", "analyzer": "vi_text"}}},
        "user_id": {"type": "keyword"},
        "conversation_id": {"type": "keyword"},
        "run_id": {"type": "keyword"},
        "question": {"type": "text", "analyzer": "vi_text",
                     "fields": {"kw": {"type": "keyword", "ignore_above": 256}}},
        "answer": {"type": "text", "analyzer": "vi_text"},
        "question_len": {"type": "integer"},
        "answer_len": {"type": "integer"},
        "truncated": {"type": "boolean"},
        "duration_ms": {"type": "float"},
        "http_status": {"type": "short"},
        "error": {"type": "boolean"},
        "error_detail": {"type": "text"},
        "blocked": {"type": "boolean"},
        "block_reason": {"type": "keyword"},
        "tools_called": {"type": "keyword"},
    }
}


def ilm_policy(retention_days: int) -> dict[str, Any]:
    """Roll over daily, delete after the retention window. ILM is available in the free
    Basic tier, so this needs no license and no cleanup cron of our own."""
    return {
        "policy": {
            "phases": {
                "hot": {"actions": {"rollover": {"max_age": "1d", "max_primary_shard_size": "5gb"}}},
                "delete": {"min_age": f"{max(1, retention_days)}d", "actions": {"delete": {}}},
            }
        }
    }


def index_template(index: str, policy_name: str) -> dict[str, Any]:
    return {
        "index_patterns": [f"{index}*"],
        "data_stream": {},
        "priority": 200,
        "template": {
            "settings": {**INDEX_SETTINGS, "index.lifecycle.name": policy_name},
            "mappings": INDEX_MAPPINGS,
        },
    }
