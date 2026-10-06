"""Admin job relay; training and model dependencies stay on Vast."""
import asyncio
import base64
import json
from urllib.parse import quote, urlsplit

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request

from .review_auth import ReviewActor, require_review_admin

router = APIRouter(prefix='/training')


async def upstream(request, path, method='GET', payload=None):
    state = request.app.state.gateway
    s = state.settings
    url = getattr(s, 'reward_training_url', '').rstrip('/')
    token = getattr(s, 'reward_training_token', '')
    try:
        parsed = urlsplit(url)
        if parsed.scheme not in {'http', 'https'} or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment or not token:
            raise ValueError()
        async with httpx.AsyncClient(timeout=90, follow_redirects=False,
                transport=getattr(state, 'reward_training_transport', None)) as client:
            async with client.stream(method, url+path, headers={'Authorization':'Bearer '+token}, json=payload) as response:
                if response.status_code == 422:
                    raise HTTPException(422, 'Dữ liệu chưa đủ điều kiện: cần campaign QA/demo, mẫu đã duyệt và ít nhất 3 nhóm câu hỏi độc lập.')
                response.raise_for_status()
                body = bytearray()
                async for chunk in response.aiter_bytes():
                    body.extend(chunk)
                    if len(body) > 262144:
                        raise ValueError()
        result = json.loads(body)
        if not isinstance(result, dict):
            raise ValueError()
        return result
    except (httpx.HTTPError, ValueError):
        raise HTTPException(503, 'Không kết nối được dịch vụ huấn luyện Vast.ai.') from None


@router.get('/campaigns/{cid}')
async def list_jobs(cid: str, request: Request, actor: ReviewActor = Depends(require_review_admin)):
    if not getattr(request.app.state.gateway.settings, 'reward_training_enabled', False):
        return {'enabled':False, 'purpose':'demo', 'jobs':[]}
    return await upstream(request, '/campaigns/'+quote(cid, safe='')+'/jobs')


@router.post('/campaigns/{cid}', status_code=202)
async def submit(cid: str, request: Request, actor: ReviewActor = Depends(require_review_admin)):
    if not getattr(request.app.state.gateway.settings, 'reward_training_enabled', False):
        raise HTTPException(404, 'reward training disabled')
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > 1024:
            raise HTTPException(413, 'training input exceeds limit')
    if body and body.strip() != b'{}':
        raise HTTPException(422, 'training parameters are configured by the operator')
    from .reviews import build_export, call
    snapshot = await call(request, 'export_snapshot', cid)
    exports = await asyncio.to_thread(lambda: {kind:base64.b64encode(build_export(snapshot, kind)).decode()
                                              for kind in ('preference','raw')})
    result = await upstream(request, '/jobs', 'POST', {'campaign_id':cid, **exports})
    await asyncio.to_thread(record_job, request.app.state.gateway.review_store, actor.actor_id, cid, result['id'])
    return result


def record_job(store, actor, cid, jid):
    with store.connection(True) as db:
        store.audit(db, actor, 'reward.training.request', cid, {'job_id':jid, 'purpose':'demo'})


@router.get('/jobs/{jid}')
async def get_job(jid: str, request: Request, actor: ReviewActor = Depends(require_review_admin)):
    if not getattr(request.app.state.gateway.settings, 'reward_training_enabled', False):
        raise HTTPException(404, 'reward training disabled')
    return await upstream(request, '/jobs/'+quote(jid, safe=''))
