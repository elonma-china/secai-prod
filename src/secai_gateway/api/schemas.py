from __future__ import annotations

import json
from typing import Any

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict


class LegacyRequest(BaseModel):
    """Permissive adapter for deployed IAS v1 clients with field-name drift."""

    model_config = ConfigDict(extra="allow")
    user_id: str | int | None = None
    message: str | None = None
    question: str | None = None
    prompt: str | None = None
    content: str | None = None
    text: str | None = None
    messages: list[dict[str, Any]] | None = None
    language: str | None = None
    data: Any = None

    def supplied_text(self) -> str:
        for candidate in (self.message, self.question, self.prompt, self.content, self.text):
            if candidate:
                return candidate
        # IAS chat clients send the new turn inside messages[]; take the latest user message.
        for item in reversed(self.messages or []):
            if item.get("role") == "user" and isinstance(item.get("content"), str) and item["content"].strip():
                return item["content"]
        if self.data is not None:
            return json.dumps(self.data, ensure_ascii=False)
        raise HTTPException(422, "One of message, question, prompt, content, text, messages, or data is required")
