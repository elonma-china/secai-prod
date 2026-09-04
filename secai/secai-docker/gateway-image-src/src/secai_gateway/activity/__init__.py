"""Activity log: one Elasticsearch document per AI request, plus the /logs viewer.

Separate from ``state/ledger.py`` (which only hashes prompts, and only for blocked turns)
and from ``state/conversations.py`` (durable chat transcript, but only for the two
/ai/conversations endpoints). This package is the cross-cutting observability trail: every
AI endpoint, with the HTTP envelope (client IP, user agent, server hostname, duration,
status) that neither of the other two records.
"""

from .context import LogContext, client_ip
from .schema import LogRecord
from .sink import ActivityLogSink
from .ui import register_logs_ui

__all__ = ["ActivityLogSink", "LogContext", "LogRecord", "client_ip", "register_logs_ui"]
