"""Expert review API. SQLite and export work are moved off the event loop."""
from __future__ import annotations
import asyncio
import hashlib
import io
import json
import zipfile
from typing import Literal
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from .review_auth import ReviewActor, get_review_actor, require_review_admin
from .review_schemas import AdjudicationInput, AssignmentInput, CampaignInput, CampaignPatch, JudgmentInput, ReviewItemInput
from ..state.review_store import ReviewError

class ReviewRoute(APIRoute):
    """Answer body-validation failures with a 422 that never echoes the submitted input.

    FastAPI's default 422 body embeds the rejected value; for a lone UTF-16 surrogate
    ("\\ud800") that value cannot be JSON-encoded, so the error response itself crashed (500).
    """
    def get_route_handler(self):
        handler = super().get_route_handler()
        async def guarded(request: Request):
            try:
                return await handler(request)
            except RequestValidationError as exc:
                return JSONResponse({'detail': [{'loc': list(e['loc']), 'msg': str(e['msg']), 'type': e['type']} for e in exc.errors()]}, status_code=422)
        return guarded

router = APIRouter(prefix='/review-api',tags=['expert review'],route_class=ReviewRoute)
MAX_UPLOAD = 10*1024*1024
MAX_OFFSET = 1_000_000_000  # keeps OFFSET inside SQLite INTEGER; larger values 422 instead of OverflowError/500

async def call(request, method, *args):
    try:
        return await asyncio.to_thread(getattr(request.app.state.gateway.review_store,method),*args)
    except ReviewError as exc:
        raise HTTPException(exc.status,exc.detail) from None


def page(items,offset,limit):
    return {'items':items[offset:offset+limit],'total':len(items),'offset':offset,'limit':limit}

class BulkApprovalInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    source: Literal['all','live','qa','import'] = 'all'
    search: str = Field(default='',max_length=200)
    snapshot_version: str = Field(pattern='^[0-9a-f]{64}$')


async def small_body(request, schema):
    data=bytearray()
    async for chunk in request.stream():
        data.extend(chunk)
        if len(data)>4096: raise HTTPException(413,'input exceeds limit')
    try: return schema.model_validate_json(data)
    except ValidationError: raise HTTPException(422,'invalid input') from None


@router.get('/overview')
async def overview(request:Request,status:Literal['pending','approved','excluded','all']='pending',
    source:Literal['all','live','qa','import']='all',search:str=Query('',max_length=200),
    offset:int=Query(0,ge=0,le=MAX_OFFSET),limit:int=Query(20,ge=1,le=100),actor:ReviewActor=Depends(require_review_admin)):
    return await call(request,'overview',status,source,search,offset,limit)


@router.get('/overview/{iid}')
async def overview_detail(iid:str,request:Request,actor:ReviewActor=Depends(require_review_admin)):
    return await call(request,'overview_detail',iid)


@router.get('/approvals/preview')
async def bulk_preview(request:Request,source:Literal['all','live','qa','import']='all',search:str=Query('',max_length=200),actor:ReviewActor=Depends(require_review_admin)):
    return await call(request,'bulk_preview',source,search)


@router.post('/approvals/bulk')
async def bulk_approve(request:Request,actor:ReviewActor=Depends(require_review_admin)):
    payload=await small_body(request,BulkApprovalInput)
    return await call(request,'bulk_approve',payload.source,payload.search,payload.snapshot_version,actor.actor_id)


@router.get('/me')
async def me(actor: ReviewActor=Depends(get_review_actor)):
    return actor

@router.get('/reviewers')
async def reviewers(request:Request,offset:int=Query(0,ge=0,le=MAX_OFFSET),limit:int=Query(50,ge=1,le=100),actor:ReviewActor=Depends(require_review_admin)):
    return page(sorted([a.model_dump() for a in request.app.state.gateway.review_credentials.values() if a.role=='reviewer'],key=lambda a:a['actor_id']),offset,limit)

@router.post('/campaigns')
async def create_campaign(payload:CampaignInput,request:Request,actor:ReviewActor=Depends(require_review_admin)):
    return await call(request,'create_campaign',payload.title,payload.rubric_version,actor.actor_id)

@router.get('/campaigns')
async def campaigns(request:Request,offset:int=Query(0,ge=0,le=MAX_OFFSET),limit:int=Query(50,ge=1,le=100),actor:ReviewActor=Depends(get_review_actor)):
    return page(await call(request,'list_campaigns',actor.actor_id if actor.role=='reviewer' else None),offset,limit)

@router.patch('/campaigns/{cid}')
async def set_campaign(cid:str,payload:CampaignPatch,request:Request,actor:ReviewActor=Depends(require_review_admin)):
    return await call(request,'set_campaign_state',cid,payload.state,actor.actor_id)


def parse_import(data):
    try: text = data.decode('utf-8')
    except UnicodeDecodeError: raise HTTPException(422,'JSONL must be UTF-8') from None
    items = []
    for number,line in enumerate(text.splitlines(),1):
        if not line.strip(): continue
        if len(items)>=500: raise HTTPException(413,'maximum 500 records per import')
        if len(line.encode('utf-8'))>1048576: raise HTTPException(413,f'line {number}: exceeds 1 MiB')
        try: items.append(ReviewItemInput.model_validate_json(line))
        except ValidationError as exc:
            # Do not echo uploaded content (may contain sensitive text) in validation errors.
            details = [{'loc':list(e['loc']),'msg':e['msg'],'type':e['type']} for e in exc.errors()]
            raise HTTPException(422,{'line':number,'errors':details}) from None
    if not items: raise HTTPException(422,'import must contain records')
    return items

@router.post('/campaigns/{cid}/imports')
async def import_items(cid:str,request:Request,actor:ReviewActor=Depends(require_review_admin)):
    length = request.headers.get('content-length')
    if length:
        try: size = int(length)
        except ValueError: raise HTTPException(422,'invalid Content-Length') from None
        if size<0: raise HTTPException(422,'invalid Content-Length')
        if size>MAX_UPLOAD: raise HTTPException(413,'upload exceeds 10 MiB')
    data = bytearray()
    async for chunk in request.stream():
        if len(data)+len(chunk)>MAX_UPLOAD: raise HTTPException(413,'upload exceeds 10 MiB')
        data.extend(chunk)
    items = await asyncio.to_thread(parse_import,bytes(data))
    return await call(request,'import_items',cid,items,actor.actor_id)

@router.get('/campaigns/{cid}/items')
async def items(cid:str,request:Request,disagreement:bool=False,offset:int=Query(0,ge=0,le=MAX_OFFSET),limit:int=Query(50,ge=1,le=100),actor:ReviewActor=Depends(require_review_admin)):
    return page(await call(request,'list_items',cid,disagreement),offset,limit)

@router.post('/items/{iid}/assignments')
async def assign(iid:str,payload:AssignmentInput,request:Request,actor:ReviewActor=Depends(require_review_admin)):
    known = {a.actor_id for a in request.app.state.gateway.review_credentials.values() if a.role=='reviewer'}
    if any(r not in known for r in payload.reviewer_ids): raise HTTPException(422,'unknown reviewer ID')
    result = await call(request,'assign',iid,payload.reviewer_ids,actor.actor_id)
    return {'items':result,'total':len(result)}

@router.delete('/assignments/{aid}',status_code=204)
async def delete_assignment(aid:str,request:Request,actor:ReviewActor=Depends(require_review_admin)):
    await call(request,'delete_assignment',aid,actor.actor_id)
    return Response(status_code=204)

@router.get('/assignments')
async def assignments(request:Request,status:Literal['pending','submitted']|None=None,campaign_id:str|None=None,offset:int=Query(0,ge=0,le=MAX_OFFSET),limit:int=Query(50,ge=1,le=100),actor:ReviewActor=Depends(get_review_actor)):
    return page(await call(request,'list_assignments',actor.actor_id,status,campaign_id),offset,limit)

@router.get('/assignments/{aid}')
async def assignment(aid:str,request:Request,actor:ReviewActor=Depends(get_review_actor)):
    return await call(request,'assignment_detail',aid,actor.actor_id)

@router.put('/assignments/{aid}/judgment')
async def judge(aid:str,payload:JudgmentInput,request:Request,actor:ReviewActor=Depends(get_review_actor)):
    return await call(request,'submit_judgment',aid,actor.actor_id,payload)

@router.get('/items/{iid}/judgments')
async def judgments(iid:str,request:Request,actor:ReviewActor=Depends(require_review_admin)):
    return await call(request,'item_judgments',iid)

@router.put('/items/{iid}/adjudication')
async def adjudicate(iid:str,payload:AdjudicationInput,request:Request,actor:ReviewActor=Depends(require_review_admin)):
    return await call(request,'adjudicate',iid,payload,actor.actor_id)

@router.get('/campaigns/{cid}/stats')
async def stats(cid:str,request:Request,actor:ReviewActor=Depends(require_review_admin)):
    return await call(request,'stats',cid)


def build_export(snapshot,format):
    records=[]
    for item in snapshot['items']:
        if format=='raw': records.append(item); continue
        approval = item['adjudication']
        if not approval or approval['outcome']!='winner': continue
        chosen = next(c for c in item['candidates'] if c['id']==approval['winning_candidate_id'])
        rejected = next(c for c in item['candidates'] if c['id']!=approval['winning_candidate_id'])
        records.append({'item_id':item['id'],'prompt':item['prompt_messages'],'chosen':chosen['text'],'rejected':rejected['text'],'rubric_version':snapshot['campaign']['rubric_version'],'adjudication_id':approval['id'],'source_group':item['source_group'], **({'source_groups':item['source_groups']} if 'source_groups' in item else {})})
    name = format+'.jsonl'
    body = ''.join(json.dumps(r,ensure_ascii=False,sort_keys=True)+'\n' for r in records).encode('utf-8')
    manifest = {'schema_version':snapshot['schema_version'],'rubric_version':snapshot['campaign']['rubric_version'],'campaign_id':snapshot['campaign']['id'],'campaign_title':snapshot['campaign']['title'],'exported_at':snapshot['exported_at'],'format':format,'counts':{'items':len(snapshot['items']),'records':len(records)},'files':{name:{'sha256':hashlib.sha256(body).hexdigest(),'records':len(records)}}}
    output = io.BytesIO()
    with zipfile.ZipFile(output,'w',zipfile.ZIP_DEFLATED) as archive:
        info=zipfile.ZipInfo(name,date_time=(1980,1,1,0,0,0)); info.compress_type=zipfile.ZIP_DEFLATED
        archive.writestr(info,body)
        info=zipfile.ZipInfo('manifest.json',date_time=(1980,1,1,0,0,0)); info.compress_type=zipfile.ZIP_DEFLATED
        archive.writestr(info,json.dumps(manifest,ensure_ascii=False,indent=2))
    return output.getvalue()

@router.get('/campaigns/{cid}/export')
async def export(cid:str,request:Request,format:Literal['raw','preference']='raw',actor:ReviewActor=Depends(require_review_admin)):
    snapshot = await call(request,'export_snapshot',cid)
    body = await asyncio.to_thread(build_export,snapshot,format)
    return Response(body,media_type='application/zip',headers={'Content-Disposition':f'attachment; filename="review-{snapshot["campaign"]["id"]}-{format}.zip"'})

class LiveSettingsInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    sample_percent: int = Field(ge=0, le=100, strict=True)


def live_defaults(request):
    settings = request.app.state.gateway.settings
    return round(getattr(settings, 'review_live_sample_rate', 0.1) * 100, 6)


def live_settings_response(request, value):
    return {**value, 'scope': 'global', 'cooldown_seconds': getattr(request.app.state.gateway.settings, 'review_live_cooldown_seconds', 600)}


@router.get('/live-settings')
async def get_live_settings(request: Request, actor: ReviewActor = Depends(require_review_admin)):
    value = await call(request, 'live_settings', live_defaults(request))
    return live_settings_response(request, value)


@router.put('/live-settings')
async def put_live_settings(request: Request, actor: ReviewActor = Depends(require_review_admin)):
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > 1024:
            raise HTTPException(413, 'settings body too large')
        body.extend(chunk)
    try:
        payload = LiveSettingsInput.model_validate_json(bytes(body))
    except ValidationError:
        raise HTTPException(422, 'sample_percent must be an integer between 0 and 100; unknown fields are forbidden') from None
    value = await call(request, 'set_live_settings', payload.sample_percent, live_defaults(request), actor.actor_id)
    return live_settings_response(request, value)


async def _generate_alternative(state,actor:ReviewActor,messages:list[dict]) -> str | None:
    """Run an independent regeneration of the last message in `messages`, in an isolated
    session seeded with the prior turns. Shared by the legacy sequential /live-pairs path,
    the standalone /live-pairs/alternative endpoint (parallel-generation path), and the
    background reward-autolabel job (`reward/autolabel.py`) — takes `state`
    (``request.app.state.gateway``) directly so non-review-api callers can reuse it too."""
    from uuid import uuid4
    settings=state.settings
    # Every opted-in turn gets a chance; serialize model work instead of dropping turns.
    if not hasattr(state,'review_live_locks'):
        state.review_live_locks={}
    generation_lock=state.review_live_locks.setdefault(actor.actor_id,asyncio.Lock())
    lock_acquired=False
    session_id='review-live-'+str(uuid4())
    try:
        await generation_lock.acquire()
        lock_acquired=True
        # Seed only the isolated session with prior public turns. The runner's guards
        # must receive the current question, not history masquerading as a new prompt.
        async def generate():
            if len(messages)>1:
                from linch.types import Message, TextBlock
                await state.runner.session_store.create(id=session_id)
                await state.runner.session_store.append_messages(session_id,[
                    Message(role=m['role'],content=[TextBlock(text=m['content'])])
                    for m in messages[:-1]
                ])
            return await state.runner.complete('chat',messages[-1]['content'],session_id)
        try:
            generated,_metadata=await asyncio.wait_for(generate(),timeout=getattr(settings,'review_live_timeout_seconds',45))
        except Exception:
            return None
        return generated if isinstance(generated,str) else None
    finally:
        try:
            # Release Linch's live session registry/work before removing durable history.
            try:
                agent=getattr(state.runner,'agent',None)
                release=getattr(agent,'release_session',None)
                if callable(release):
                    await asyncio.wait_for(release(session_id,force=True),timeout=5)
            except Exception:
                pass
            try:
                session_store=getattr(state.runner,'session_store',None)
                if session_store is not None:
                    await asyncio.wait_for(session_store.delete(session_id),timeout=5)
            except Exception:
                # Only this isolated UUID is ever targeted; cleanup failure cannot touch chat.
                pass
        finally:
            if lock_acquired:
                generation_lock.release()


@router.post('/live-pairs/decide')
async def live_pair_decide(request:Request,actor:ReviewActor=Depends(get_review_actor)):
    """Roll the sampling decision BEFORE the main chat answer exists, so the FE can fire
    the normal chat request (A) and /live-pairs/alternative (B) at the same time instead
    of waiting for A to finish first. Reserves the per-actor cooldown immediately on a
    sampled turn, same window as the legacy /live-pairs path, so the two can't double-spend
    a turn against each other."""
    import secrets
    import time
    from .review_schemas import LiveDecideInput
    payload=await small_body(request,LiveDecideInput)
    state=request.app.state.gateway
    settings=state.settings
    live_config=await call(request, 'live_settings', live_defaults(request))
    sample_rate=live_config['sample_percent']/100
    if not payload.always_offer and (sample_rate<=0 or secrets.randbelow(1000000)>=sample_rate*1000000):
        return {'sampled':False}
    if not hasattr(state,'review_live_inflight'):
        state.review_live_inflight=set()
        state.review_live_next={}
    current=time.monotonic()
    if not payload.always_offer and (actor.actor_id in state.review_live_inflight or state.review_live_next.get(actor.actor_id,0)>current):
        return {'sampled':False}
    if not await call(request,'live_campaign_is_open'):
        return {'sampled':False}
    if not payload.always_offer:
        state.review_live_next[actor.actor_id]=current+getattr(settings,'review_live_cooldown_seconds',600)
    return {'sampled':True}


@router.post('/live-pairs/alternative')
async def live_pair_alternative(request:Request,actor:ReviewActor=Depends(get_review_actor)):
    """Generate B on its own, independent of A — call only after /live-pairs/decide
    returned sampled=true, in parallel with the caller's own chat request for A."""
    from .review_schemas import LiveAlternativeInput
    from ..security import redact_public_input
    data=bytearray()
    async for chunk in request.stream():
        if len(data)+len(chunk)>262144:
            raise HTTPException(413,'live context exceeds 256 KiB')
        data.extend(chunk)
    try:
        payload=LiveAlternativeInput.model_validate_json(bytes(data))
    except ValidationError as exc:
        raise HTTPException(422,[{'loc':list(e['loc']),'msg':e['msg'],'type':e['type']} for e in exc.errors()]) from None
    if not await call(request,'live_campaign_is_open'):
        raise HTTPException(409,'review campaign is closed')
    messages=[{'role':m.role,'content':redact_public_input(m.content).text} for m in payload.prompt_messages]
    generated=await _generate_alternative(request.app.state.gateway,actor,messages)
    alternative=redact_public_input(generated).text if generated is not None else ''
    if not alternative.strip():
        raise HTTPException(503,'could not generate an alternative answer')
    return {'text':alternative}


@router.post('/live-pairs')
async def live_pair(request:Request,actor:ReviewActor=Depends(get_review_actor)):
    """Create a live A/B pair. Two shapes:
    - pre-generated (parallel path): payload.alternative is set — caller already ran
      /live-pairs/decide + /live-pairs/alternative itself, concurrently with its own chat
      request; this only validates + persists, no sampling or generation here.
    - legacy (sequential path, kept for always_offer / any caller not yet using the split
      flow): payload.alternative is absent — rolls sampling and generates B itself, same
      behavior as before this endpoint was split.
    """
    import secrets
    import time
    from uuid import uuid4
    from .review_schemas import LivePairInput
    from ..security import redact_public_input

    # Bound actual streamed bytes before decoding Pydantic input.
    data=bytearray()
    async for chunk in request.stream():
        if len(data)+len(chunk)>262144:
            raise HTTPException(413,'live context exceeds 256 KiB')
        data.extend(chunk)
    try:
        payload=LivePairInput.model_validate_json(bytes(data))
    except ValidationError as exc:
        raise HTTPException(422,[{'loc':list(e['loc']),'msg':e['msg'],'type':e['type']} for e in exc.errors()]) from None
    state=request.app.state.gateway
    messages=[{'role':m.role,'content':redact_public_input(m.content).text} for m in payload.prompt_messages]
    original=redact_public_input(payload.answer).text

    def build_item(alternative:str):
        return ReviewItemInput.model_validate({'external_id':str(uuid4()),'prompt_messages':messages,'candidates':[{'id':'original','text':original,'provenance':{'source':'original_chat_final','model':'unknown'}},{'id':'generated','text':alternative,'provenance':{'source':'review_generation','model':'unknown','profile':'chat'}}],'source_group':redact_public_input(payload.source_group).text,'evidence':'','provenance':{'source':'live_chat','retrieved_context':'unknown','comparison':'independent regeneration from visible context'}})

    if payload.alternative is not None:
        if not await call(request,'live_campaign_is_open'):
            return {'offered':False}
        alternative=redact_public_input(payload.alternative).text
        if not alternative.strip() or original.strip()==alternative.strip():
            return {'offered':False}
        try:
            item=build_item(alternative)
        except ValidationError:
            return {'offered':False}
        try:
            aid=await call(request,'create_live_assignment',item,actor.actor_id)
        except HTTPException:
            return {'offered':False}
        return {'offered':True,'assignment':await call(request,'assignment_detail',aid,actor.actor_id)}

    settings=state.settings
    live_config=await call(request, 'live_settings', live_defaults(request))
    sample_rate=live_config['sample_percent']/100
    if not payload.always_offer and (sample_rate<=0 or secrets.randbelow(1000000)>=sample_rate*1000000):
        return {'offered':False}
    # Reserving before any await prevents two simultaneous actor requests spending twice.
    if not hasattr(state,'review_live_inflight'):
        state.review_live_inflight=set()
        state.review_live_next={}
    current=time.monotonic()
    if not payload.always_offer and (actor.actor_id in state.review_live_inflight or state.review_live_next.get(actor.actor_id,0)>current):
        return {'offered':False}
    if not payload.always_offer:
        state.review_live_inflight.add(actor.actor_id)
        state.review_live_next[actor.actor_id]=current+getattr(settings,'review_live_cooldown_seconds',600)
    try:
        if not await call(request,'live_campaign_is_open'):
            return {'offered':False}
        generated=await _generate_alternative(state,actor,messages)
        if generated is None: return {'offered':False}
        alternative=redact_public_input(generated).text
        if not alternative.strip() or original.strip()==alternative.strip(): return {'offered':False}
        try:
            item=build_item(alternative)
        except ValidationError:
            return {'offered':False}
        try:
            aid=await call(request,'create_live_assignment',item,actor.actor_id)
        except HTTPException:
            return {'offered':False}
        return {'offered':True,'assignment':await call(request,'assignment_detail',aid,actor.actor_id)}
    finally:
        if not payload.always_offer:
            state.review_live_inflight.discard(actor.actor_id)

# Template assistance shares review identity only; no existing IAS report route changes.
from .review_templates import router as template_router
from .review_reward import router as reward_router
router.include_router(reward_router)
router.include_router(template_router)
