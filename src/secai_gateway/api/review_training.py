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


from typing import Literal
from fastapi import Query
from pydantic import BaseModel, ConfigDict, Field
from ..state.review_training_store import ACTIVE


class BatchInput(BaseModel):
    model_config=ConfigDict(extra='forbid')
    request_key:str=Field(pattern='^[a-zA-Z0-9_-]{1,100}$')
    purpose:Literal['demo','production']='demo'
    snapshot_version:str=Field(pattern='^[0-9a-f]{64}$')


class RetryInput(BaseModel):
    model_config=ConfigDict(extra='forbid')
    request_key:str=Field(pattern='^[a-zA-Z0-9_-]{1,100}$')


def enabled(request):
    if not getattr(request.app.state.gateway.settings,'reward_training_enabled',False):
        raise HTTPException(404,'reward training disabled')


async def reconcile(request,run):
    from .reviews import call,build_export
    if run['status'] not in ACTIVE: return run
    if run['remote_job_id']:
        remote=await upstream(request,'/jobs/'+quote(run['remote_job_id'],safe=''))
    else:
        snapshot=await call(request,'batch_snapshot',run['batch_id'])
        batch=await call(request,'batch_detail',run['batch_id'])
        exports=await asyncio.to_thread(lambda:{kind:base64.b64encode(build_export(snapshot,kind)).decode() for kind in ('raw','preference')})
        partitions=await call(request,'batch_partitions',run['batch_id'])
        try:
            remote=await upstream(request,'/jobs','POST',{'campaign_id':run['batch_id'],'request_key':run['id'],
                'snapshot_sha256':batch['snapshot_sha256'],'partitions':partitions,**exports})
        except HTTPException as exc:
            if exc.status_code==422:
                await call(request,'update_training_run',run['id'],{'status':'failed','error':exc.detail})
            # Network loss leaves dispatching: exact request key reconciles safely.
            raise
    batch=await call(request,'batch_detail',run['batch_id'])
    if remote.get('campaign_id')!=run['batch_id'] or remote.get('request_key')!=run['id'] or remote.get('snapshot_sha256')!=batch['snapshot_sha256']:
        raise HTTPException(502,'training snapshot identity mismatch')
    return await call(request,'update_training_run',run['id'],remote)


@router.get('/eligibility')
async def eligibility(request:Request,purpose:Literal['demo','production']='demo',actor:ReviewActor=Depends(require_review_admin)):
    from .reviews import call
    result=await call(request,'training_eligibility',purpose)
    result['enabled']=bool(getattr(request.app.state.gateway.settings,'reward_training_enabled',False))
    if not result['enabled']:
        result['eligible']=False;result['blocked_reasons'].append('Chưa kết nối dịch vụ huấn luyện.')
    return result


@router.post('/batches',status_code=202)
async def freeze(request:Request,actor:ReviewActor=Depends(require_review_admin)):
    enabled(request)
    from .reviews import call,small_body
    p=await small_body(request,BatchInput)
    batch=await call(request,'reserve_training_batch',p.request_key,p.purpose,p.snapshot_version,actor.actor_id)
    await reconcile(request,batch['runs'][-1])
    return await call(request,'batch_detail',batch['id'])


@router.get('/batches')
async def batches(request:Request,offset:int=Query(0,ge=0),limit:int=Query(20,ge=1,le=100),actor:ReviewActor=Depends(require_review_admin)):
    from .reviews import call
    result=await call(request,'list_batches',offset,limit)
    for batch in result['items']:
        if batch['status'] in ACTIVE:
            try:
                await reconcile(request,batch['runs'][-1])
                batch.update(await call(request,'batch_detail',batch['id']))
            except HTTPException as exc:
                batch['connection_error']=exc.detail
    return result


@router.get('/batches/{bid}')
async def batch_detail(bid:str,request:Request,actor:ReviewActor=Depends(require_review_admin)):
    from .reviews import call
    batch=await call(request,'batch_detail',bid)
    if batch['status'] in ACTIVE:
        try:
            await reconcile(request,batch['runs'][-1])
            batch=await call(request,'batch_detail',bid)
        except HTTPException as exc: batch['connection_error']=exc.detail
    return batch


@router.get('/batches/{bid}/items')
async def batch_items(bid:str,request:Request,offset:int=Query(0,ge=0),limit:int=Query(20,ge=1,le=100),actor:ReviewActor=Depends(require_review_admin)):
    from .reviews import call
    return await call(request,'batch_items',bid,offset,limit)


@router.post('/batches/{bid}/retry',status_code=202)
async def retry(bid:str,request:Request,actor:ReviewActor=Depends(require_review_admin)):
    enabled(request)
    from .reviews import call,small_body
    p=await small_body(request,RetryInput)
    run=await call(request,'retry_batch',bid,p.request_key,actor.actor_id)
    return await reconcile(request,run)


@router.get('/runs/{rid}')
async def run_detail(rid:str,request:Request,actor:ReviewActor=Depends(require_review_admin)):
    from .reviews import call
    return await reconcile(request,await call(request,'training_run',rid))


@router.get('/runs/{rid}/events')
async def run_events(rid:str,request:Request,after_seq:int=Query(0,ge=0),limit:int=Query(100,ge=1,le=200),actor:ReviewActor=Depends(require_review_admin)):
    from .reviews import call
    run=await call(request,'training_run',rid)
    if not run['remote_job_id']: return {'items':[],'next_seq':after_seq}
    return await upstream(request,'/jobs/'+quote(run['remote_job_id'],safe='')+'/events?after_seq='+str(after_seq)+'&limit='+str(limit))
