"""Runtime compat shim for a broken upstream import chain — import this BEFORE anything
that imports `ragas` (ragas_kpi.py does this first thing).

`ragas` (0.3.x/0.4.x as of 2026-09) does an unconditional
``from langchain_community.chat_models.vertexai import ChatVertexAI`` at module-import time
in ragas/llms/base.py, used only for an isinstance() branch — never actually constructed by
this suite (we only ever build ChatOpenAI via langchain_openai, pointed at the independent
judge = config.JUDGE_MODEL). langchain-community 0.4.x removed that submodule (Vertex AI
chat support moved to the standalone `langchain-google-vertexai` package), so a plain
``pip install -r requirements.txt`` reproduces ``ModuleNotFoundError:
No module named 'langchain_community.chat_models.vertexai'`` on first `import ragas`.

Fix: register a minimal inert stub module in sys.modules before ragas is imported, so the
import succeeds without touching the installed langchain-community package on disk (so this
survives a from-scratch `.venv` rebuild via requirements.txt alone — no manual venv patching
needed). Idempotent; safe to call more than once.
"""
from __future__ import annotations

import sys
import types


def ensure_langchain_community_vertexai_stub() -> None:
    mod_name = "langchain_community.chat_models.vertexai"
    if mod_name in sys.modules:
        return
    try:
        import langchain_community.chat_models.vertexai  # noqa: F401
        return  # real module present (future langchain-community may restore it) — nothing to do
    except ImportError:
        pass

    try:
        from langchain_core.language_models.chat_models import BaseChatModel as _Base
    except Exception:  # pragma: no cover - defensive fallback
        _Base = object

    class ChatVertexAI(_Base):  # noqa: N818 - name matches upstream symbol ragas expects
        """Inert stub: never instantiated by this suite. Present only so
        `isinstance(x, ChatVertexAI)` checks in ragas/llms/base.py resolve."""

    stub = types.ModuleType(mod_name)
    stub.ChatVertexAI = ChatVertexAI
    sys.modules[mod_name] = stub

    # Also make it attribute-accessible as `langchain_community.chat_models.vertexai`
    # (some import styles do `from langchain_community.chat_models import vertexai`).
    import langchain_community.chat_models as _chat_models
    _chat_models.vertexai = stub
