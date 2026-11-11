"""Strict input contracts for immutable expert review snapshots."""
from __future__ import annotations
import json
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Decision = Literal['left', 'right', 'tie', 'both_bad', 'insufficient_context', 'skip']
ErrorTag = Literal['incorrect_fact', 'unsupported_claim', 'incomplete', 'irrelevant', 'unsafe', 'other']
RUBRIC = 'Ưu tiên đúng kỹ thuật và có bằng chứng; sau đó đủ ý và phù hợp câu hỏi. Không chọn chỉ vì câu trả lời dài. Phân biệt không có bằng chứng với đã xác minh sai.'

def _has_lone_surrogate(value) -> bool:
    if isinstance(value, str):
        return any('\ud800' <= ch <= '\udfff' for ch in value)
    if isinstance(value, dict):
        return any(_has_lone_surrogate(k) or _has_lone_surrogate(v) for k, v in value.items())
    if isinstance(value, (list, tuple)):
        return any(_has_lone_surrogate(v) for v in value)
    return False

class Input(BaseModel):
    model_config = ConfigDict(extra='forbid')

    @model_validator(mode='before')
    @classmethod
    def reject_lone_surrogates(cls, data):
        # JSON "\ud800" decodes to an unpaired surrogate that cannot be UTF-8 encoded; left alone
        # it would crash the SQLite/JSON write with HTTP 500 instead of a 422.
        if _has_lone_surrogate(data):
            raise ValueError('text must not contain unpaired UTF-16 surrogates')
        return data

    @field_validator('id', 'external_id', 'title', 'source_group', 'comment', 'reason', 'winning_candidate_id', mode='before', check_fields=False)
    @classmethod
    def strip_labels(cls, value):
        return value.strip() if isinstance(value, str) else value

class Message(Input):
    role: Literal['user', 'assistant']
    content: str = Field(min_length=1, max_length=65536)

    @field_validator('content')
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError('message content must not be blank')
        return value

class Candidate(Input):
    id: str = Field(min_length=1, max_length=200)
    text: str = Field(min_length=1, max_length=65536)
    provenance: dict = Field(default_factory=lambda: {'model': 'unknown'})
    @model_validator(mode='after')
    def byte_limit(self):
        if not self.text.strip():
            raise ValueError('candidate text must not be blank')
        if len(self.text.encode('utf-8')) > 65536:
            raise ValueError('candidate text exceeds 64 KiB')
        return self

class ReviewItemInput(Input):
    external_id: str = Field(min_length=1, max_length=200)
    prompt_messages: list[Message] = Field(min_length=1, max_length=100)
    candidates: list[Candidate] = Field(min_length=2, max_length=2)
    evidence: str = Field(default='', max_length=262144)
    source_group: str = Field(default='unknown', max_length=200)
    provenance: dict = Field(default_factory=lambda: {'model': 'unknown'})
    @model_validator(mode='after')
    def distinct(self):
        if self.prompt_messages[0].role != 'user' or self.prompt_messages[-1].role != 'user':
            raise ValueError('prompt must start and end with a user message, excluding candidate answers')
        if self.candidates[0].id == self.candidates[1].id or self.candidates[0].text.strip() == self.candidates[1].text.strip():
            raise ValueError('two candidates must have distinct IDs and nonidentical texts')
        if len(json.dumps(self.model_dump(), ensure_ascii=False).encode('utf-8')) > 1048576:
            raise ValueError('item exceeds 1 MiB')
        return self

class CampaignInput(Input):
    title: str = Field(min_length=1, max_length=200)
    rubric_version: Literal['security_qa_v1'] = 'security_qa_v1'

class CampaignPatch(Input):
    state: Literal['open', 'closed']

class AssignmentInput(Input):
    reviewer_ids: list[str] = Field(min_length=1, max_length=100)

class ErrorTags(Input):
    left: list[ErrorTag] = Field(default_factory=list, max_length=6)
    right: list[ErrorTag] = Field(default_factory=list, max_length=6)

class JudgmentInput(Input):
    decision: Decision
    error_tags: ErrorTags = Field(default_factory=ErrorTags)
    comment: str = Field(default='', max_length=4000)
    @model_validator(mode='after')
    def reason(self):
        if (self.decision in {'both_bad', 'insufficient_context'} or 'other' in self.error_tags.left + self.error_tags.right) and not self.comment:
            raise ValueError('comment required for this decision or other tag')
        self.error_tags.left = sorted(set(self.error_tags.left))
        self.error_tags.right = sorted(set(self.error_tags.right))
        return self

class AdjudicationInput(Input):
    outcome: Literal['winner', 'tie', 'both_bad', 'insufficient_context', 'skip']
    winning_candidate_id: str | None = Field(default=None, max_length=200)
    reason: str = Field(min_length=1, max_length=4000)
    @model_validator(mode='after')
    def winner(self):
        if (self.outcome == 'winner') != bool(self.winning_candidate_id):
            raise ValueError('winner outcome requires candidate ID; other outcomes forbid it')
        return self

class LivePairInput(Input):
    always_offer: bool = False
    prompt_messages: list[Message] = Field(min_length=1,max_length=30)
    answer: str = Field(min_length=1,max_length=65536)
    source_group: str = Field(default='unknown',min_length=1,max_length=200)
    # Parallel-generation path (2026-11-10): when the caller already produced B itself —
    # via /live-pairs/decide + /live-pairs/alternative run concurrently with its own chat
    # request — it passes that text here and this endpoint only validates + persists,
    # skipping the server-side sampling roll and generation entirely.
    alternative: str | None = Field(default=None,max_length=65536)
    @model_validator(mode='after')
    def live_context(self):
        if self.prompt_messages[0].role != 'user' or self.prompt_messages[-1].role != 'user':
            raise ValueError('visible context must start with user and end at the user question, excluding produced answer')
        if not self.answer.strip() or len(self.answer.encode('utf-8'))>65536:
            raise ValueError('answer must be nonblank and at most 64 KiB')
        if self.alternative is not None and (not self.alternative.strip() or len(self.alternative.encode('utf-8'))>65536):
            raise ValueError('alternative, when given, must be nonblank and at most 64 KiB')
        return self

class LiveDecideInput(Input):
    """Body for POST /live-pairs/decide. Same always_offer escape hatch as LivePairInput,
    for callers (like the devspace client today) that bypass sampling on every turn."""
    always_offer: bool = False

class LiveAlternativeInput(Input):
    """Body for POST /live-pairs/alternative — generates B independently of A, so the
    caller can fire it in parallel with its own chat request instead of waiting for A."""
    prompt_messages: list[Message] = Field(min_length=1,max_length=30)
    @model_validator(mode='after')
    def live_context(self):
        if self.prompt_messages[0].role != 'user' or self.prompt_messages[-1].role != 'user':
            raise ValueError('visible context must start with user and end at the user question, excluding produced answer')
        return self
