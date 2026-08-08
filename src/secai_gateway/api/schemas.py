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
    # The IAS "Exploit Research" screen posts a structured form instead of free text.
    vulnerability_type: str | None = None
    target_system: str | None = None
    context: str | None = None

    def supplied_text(self) -> str:
        for candidate in (self.message, self.question, self.prompt, self.content, self.text):
            if candidate:
                return candidate
        # IAS chat clients send the new turn inside messages[]; take the latest user message.
        for item in reversed(self.messages or []):
            if item.get("role") == "user" and isinstance(item.get("content"), str) and item["content"].strip():
                return item["content"]
        # IAS "Exploit Research" form fields — combine whichever of the three are set.
        form_parts = [
            f"Vulnerability: {self.vulnerability_type}" if self.vulnerability_type else "",
            f"Target: {self.target_system}" if self.target_system else "",
            self.context or "",
        ]
        form_text = " | ".join(p for p in form_parts if p).strip()
        if form_text:
            return form_text
        if self.data is not None:
            return json.dumps(self.data, ensure_ascii=False)
        raise HTTPException(422, "One of message, question, prompt, content, text, messages, or data is required")
