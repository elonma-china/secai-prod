"""Admin-only opt-in HTTP relay for separately served reward model."""
import json
import math
import re
from urllib.parse import urlsplit

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import ValidationError

from .review_auth import ReviewActor, require_review_admin
from ..reward.schema import ScoreInput

router=APIRouter(prefix='/reward',tags=['reward'])
from .review_training import router as training_router
router.include_router(training_router)


class RewardUnavailable(ValueError):
    """Raised by `score_with_reward_model` for every failure mode — disabled, misconfigured,
    unreachable, or an invalid/mismatched response. Callers outside the HTTP route (the
    background autolabel job) only need to catch this one type, never an HTTPException."""


async def score_with_reward_model(state, payload:ScoreInput) -> dict:
    """Core of `POST /reward/score`, factored out so `reward/autolabel.py`'s background
    job can call it directly with `state` (``request.app.state.gateway``) instead of going
    through HTTP+admin-auth. Returns {'artifact_id','scores','preferred_index','margin'} or
    raises `RewardUnavailable` — never partial/best-effort success."""
    settings=state.settings
    if not getattr(settings,'reward_enabled',False):raise RewardUnavailable('reward scoring disabled')
    url=getattr(settings,'reward_service_url','').rstrip('/')
    digest=getattr(settings,'reward_artifact_sha256','')
    token=getattr(settings,'reward_service_token','')
    try:parsed=urlsplit(url)
    except ValueError:raise RewardUnavailable('reward service not configured') from None
    if parsed.scheme not in {'http','https'} or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment or not token or not re.fullmatch('[0-9a-f]{64}',digest):
        raise RewardUnavailable('reward service not configured')
    try:
        async with httpx.AsyncClient(timeout=getattr(settings,'reward_timeout_seconds',30),
                                     transport=getattr(state,'reward_transport',None),follow_redirects=False) as http:
            async with http.stream('POST',url+'/score',headers={'Authorization':'Bearer '+token},
                                   json=payload.model_dump()) as response:
                response.raise_for_status()
                content=bytearray()
                async for chunk in response.aiter_bytes():
                    content.extend(chunk)
                    if len(content)>16384:raise ValueError('reward response exceeds limit')
        result=json.loads(content)
        scores=result['scores']
        if (result['artifact_id']!=digest or not isinstance(scores,list) or len(scores)!=len(payload.answers) or
                any(type(s) not in (int,float) or not math.isfinite(s) for s in scores)):
            raise ValueError('invalid reward response')
    except (httpx.HTTPError,ValueError,KeyError,TypeError) as exc:
        raise RewardUnavailable('reward scoring unavailable or artifact mismatch') from exc
    best=max(range(len(scores)),key=lambda i:scores[i])
    ordered=sorted(scores,reverse=True)
    margin=ordered[0]-ordered[1] if len(ordered)>1 else None
    if margin is not None and not math.isfinite(margin):
        raise RewardUnavailable('reward scoring returned invalid margin')
    return {'artifact_id':digest,'scores':scores,'preferred_index':best if margin is not None and margin>1e-8 else None,
            'margin':margin}


@router.post('/score')
async def score(request:Request,actor:ReviewActor=Depends(require_review_admin)):
    body=bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body)>262144:raise HTTPException(413,'reward request exceeds limit')
    try:payload=ScoreInput.model_validate_json(body)
    except ValidationError:raise HTTPException(422,'invalid reward scoring input') from None
    try:
        return await score_with_reward_model(request.app.state.gateway,payload)
    except RewardUnavailable as exc:
        raise HTTPException(404 if str(exc)=='reward scoring disabled' else 503,str(exc)) from None
