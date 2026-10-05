"""Expert review API. SQLite and export work are moved off the event loop."""
from __future__ import annotations
import asyncio
import hashlib
import io
import json
import zipfile
from typing import Literal
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import ValidationError
from .review_auth import ReviewActor, get_review_actor, require_review_admin
from .review_schemas import AdjudicationInput, AssignmentInput, CampaignInput, CampaignPatch, JudgmentInput, ReviewItemInput
from ..state.review_store import ReviewError

router = APIRouter(prefix='/review-api',tags=['expert review'])
MAX_UPLOAD = 10*1024*1024

async def call(request, method, *args):
    try:
        return await asyncio.to_thread(getattr(request.app.state.gateway.review_store,method),*args)
    except ReviewError as exc:
        raise HTTPException(exc.status,exc.detail) from None


def page(items,offset,limit):
    return {'items':items[offset:offset+limit],'total':len(items),'offset':offset,'limit':limit}

@router.get('/me')
async def me(actor: ReviewActor=Depends(get_review_actor)):
    return actor

@router.get('/reviewers')
async def reviewers(request:Request,offset:int=Query(0,ge=0),limit:int=Query(50,ge=1,le=100),actor:ReviewActor=Depends(require_review_admin)):
    return page(sorted([a.model_dump() for a in request.app.state.gateway.review_credentials.values() if a.role=='reviewer'],key=lambda a:a['actor_id']),offset,limit)

@router.post('/campaigns')
async def create_campaign(payload:CampaignInput,request:Request,actor:ReviewActor=Depends(require_review_admin)):
    return await call(request,'create_campaign',payload.title,payload.rubric_version,actor.actor_id)

@router.get('/campaigns')
async def campaigns(request:Request,offset:int=Query(0,ge=0),limit:int=Query(50,ge=1,le=100),actor:ReviewActor=Depends(get_review_actor)):
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
async def items(cid:str,request:Request,disagreement:bool=False,offset:int=Query(0,ge=0),limit:int=Query(50,ge=1,le=100),actor:ReviewActor=Depends(require_review_admin)):
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
async def assignments(request:Request,status:Literal['pending','submitted']|None=None,campaign_id:str|None=None,offset:int=Query(0,ge=0),limit:int=Query(50,ge=1,le=100),actor:ReviewActor=Depends(get_review_actor)):
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
        records.append({'item_id':item['id'],'prompt':item['prompt_messages'],'chosen':chosen['text'],'rejected':rejected['text'],'rubric_version':snapshot['campaign']['rubric_version'],'adjudication_id':approval['id'],'source_group':item['source_group']})
    name = format+'.jsonl'
    body = ''.join(json.dumps(r,ensure_ascii=False,sort_keys=True)+'\n' for r in records).encode('utf-8')
    manifest = {'schema_version':snapshot['schema_version'],'rubric_version':snapshot['campaign']['rubric_version'],'campaign_id':snapshot['campaign']['id'],'campaign_title':snapshot['campaign']['title'],'exported_at':snapshot['exported_at'],'format':format,'counts':{'items':len(snapshot['items']),'records':len(records)},'files':{name:{'sha256':hashlib.sha256(body).hexdigest(),'records':len(records)}}}
    output = io.BytesIO()
    with zipfile.ZipFile(output,'w',zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(name,body)
        archive.writestr('manifest.json',json.dumps(manifest,ensure_ascii=False,indent=2))
    return output.getvalue()

@router.get('/campaigns/{cid}/export')
async def export(cid:str,request:Request,format:Literal['raw','preference']='raw',actor:ReviewActor=Depends(require_review_admin)):
    snapshot = await call(request,'export_snapshot',cid)
    body = await asyncio.to_thread(build_export,snapshot,format)
    return Response(body,media_type='application/zip',headers={'Content-Disposition':f'attachment; filename="review-{snapshot["campaign"]["id"]}-{format}.zip"'})

@router.post('/live-pairs')
async def live_pair(request:Request,actor:ReviewActor=Depends(get_review_actor)):
    """Occasional independent B generation; the original chat/session stays untouched."""
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
    settings=state.settings
    sample_rate=getattr(settings,'review_live_sample_rate',0.1)
    if sample_rate<=0 or secrets.randbelow(1000000)>=sample_rate*1000000:
        return {'offered':False}
    # Reserving before any await prevents two simultaneous actor requests spending twice.
    if not hasattr(state,'review_live_inflight'):
        state.review_live_inflight=set()
        state.review_live_next={}
    current=time.monotonic()
    if actor.actor_id in state.review_live_inflight or state.review_live_next.get(actor.actor_id,0)>current:
        return {'offered':False}
    state.review_live_inflight.add(actor.actor_id)
    state.review_live_next[actor.actor_id]=current+getattr(settings,'review_live_cooldown_seconds',600)
    session_id='review-live-'+str(uuid4())
    try:
        if not await call(request,'live_campaign_is_open'):
            return {'offered':False}
        messages=[{'role':m.role,'content':redact_public_input(m.content).text} for m in payload.prompt_messages]
        original=redact_public_input(payload.answer).text
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
            return {'offered':False}
        if not isinstance(generated,str): return {'offered':False}
        alternative=redact_public_input(generated).text
        if not alternative.strip() or original.strip()==alternative.strip(): return {'offered':False}
        try:
            item=ReviewItemInput.model_validate({'external_id':str(uuid4()),'prompt_messages':messages,'candidates':[{'id':'original','text':original,'provenance':{'source':'original_chat_final','model':'unknown'}},{'id':'generated','text':alternative,'provenance':{'source':'review_generation','model':'unknown','profile':'chat'}}],'source_group':redact_public_input(payload.source_group).text,'evidence':'','provenance':{'source':'live_chat','retrieved_context':'unknown','comparison':'independent regeneration from visible context'}})
        except ValidationError:
            return {'offered':False}
        try:
            aid=await call(request,'create_live_assignment',item,actor.actor_id)
        except HTTPException:
            return {'offered':False}
        return {'offered':True,'assignment':await call(request,'assignment_detail',aid,actor.actor_id)}
    finally:
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
        state.review_live_inflight.discard(actor.actor_id)

# Template assistance shares review identity only; no existing IAS report route changes.
from .review_templates import router as template_router
from .review_reward import router as reward_router
router.include_router(reward_router)
router.include_router(template_router)
