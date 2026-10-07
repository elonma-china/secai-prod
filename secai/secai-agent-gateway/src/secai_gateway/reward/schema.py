"""Shared scalar-scoring contract; public user/assistant content only."""
from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..api.review_schemas import Message
from ..security import require_public_input


class ScoreInput(BaseModel):
    model_config=ConfigDict(extra='forbid')
    prompt:list[Message]=Field(min_length=1,max_length=100)
    answers:list[str]=Field(min_length=1,max_length=4)

    @model_validator(mode='after')
    def public_content(self):
        if self.prompt[0].role!='user' or self.prompt[-1].role!='user':
            raise ValueError('prompt must start and end with user')
        if sum(len(m.content.encode()) for m in self.prompt)>65536:
            raise ValueError('prompt exceeds 64 KiB')
        for text in [m.content for m in self.prompt]+self.answers:
            if not text.strip() or len(text.encode())>65536:
                raise ValueError('invalid content size')
            require_public_input(text)
        return self

    def messages(self):return [m.model_dump() for m in self.prompt]
