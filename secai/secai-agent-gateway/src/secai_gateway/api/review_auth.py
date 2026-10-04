"""Dedicated review identities. Only SHA-256 token hashes enter configuration."""
from __future__ import annotations
import hashlib
import json
import re
from pathlib import Path
from typing import Literal
from fastapi import Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, ValidationError

class ReviewActor(BaseModel):
    model_config = ConfigDict(extra='forbid')
    actor_id: str = Field(min_length=1,max_length=200)
    role: Literal['admin','reviewer']


def load_credentials(path):
    try:
        data = json.loads(Path(path).read_text(encoding='utf-8'))
        records = data['credentials']
        if set(data) != {'credentials'} or not isinstance(records,list) or not records:
            raise ValueError()
        credentials = {}
        actors = set()
        for record in records:
            record = dict(record)
            digest = record.pop('token_sha256')
            if not isinstance(digest,str) or not re.fullmatch('[0-9a-f]{64}',digest) or digest in credentials:
                raise ValueError()
            actor = ReviewActor.model_validate(record)
            if actor.actor_id in actors: raise ValueError()
            credentials[digest] = actor
            actors.add(actor.actor_id)
        if not any(a.role == 'admin' for a in credentials.values()): raise ValueError()
        return credentials
    except (OSError,KeyError,TypeError,ValueError,ValidationError) as exc:
        raise ValueError('invalid review credentials file; require unique SHA-256 tokens and actor IDs, including admin') from None


def get_review_actor(request: Request) -> ReviewActor:
    state = getattr(request.app.state,'gateway',None)
    if state is None or not state.settings.review_enabled:
        raise HTTPException(404,'review is disabled')
    authorization = request.headers.get('authorization','')
    parts = authorization.split()
    if len(parts) != 2 or parts[0].lower() != 'bearer':
        raise HTTPException(401,'valid review bearer token required',headers={'WWW-Authenticate':'Bearer'})
    digest = hashlib.sha256(parts[1].encode()).hexdigest()
    actor = state.review_credentials.get(digest)
    if actor is None:
        raise HTTPException(401,'valid review bearer token required',headers={'WWW-Authenticate':'Bearer'})
    return actor


def require_review_admin(actor: ReviewActor = Depends(get_review_actor)) -> ReviewActor:
    if actor.role != 'admin': raise HTTPException(403,'review admin required')
    return actor
