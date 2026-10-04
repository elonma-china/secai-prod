"""Additive, stateless Markdown template assistance; no template/code evaluation."""
from __future__ import annotations
import asyncio
import json
import re
from collections import Counter
from uuid import uuid4
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from .review_auth import ReviewActor, get_review_actor
from ..security import require_public_input, redact_public_input

router=APIRouter(prefix='/report-templates',tags=['report templates'])
MAX_BODY=262144
MODEL_TIMEOUT=45

class TemplateInput(BaseModel):
    model_config=ConfigDict(extra='forbid')
    template:str=Field(min_length=1,max_length=65536)
    input_clean_confirmed:bool=Field(strict=True)
    @model_validator(mode='after')
    def confirm_and_limit(self):
        if not self.input_clean_confirmed:
            raise ValueError('explicit confirmation that input has been cleaned is required')
        if not self.template.strip() or len(self.template.encode('utf-8'))>65536:
            raise ValueError('template must be nonblank and at most 64 KiB')
        return self

class ReviseInput(TemplateInput):
    instruction:str=Field(min_length=1,max_length=8192)
    @model_validator(mode='after')
    def instruction_limit(self):
        if not self.instruction.strip() or len(self.instruction.encode('utf-8'))>8192:
            raise ValueError('instruction must be nonblank and at most 8 KiB')
        return self

class RenderInput(TemplateInput):
    logs:str=Field(min_length=1,max_length=131072)
    @model_validator(mode='after')
    def logs_limit(self):
        if not self.logs.strip() or len(self.logs.encode('utf-8'))>131072:
            raise ValueError('logs must be nonblank and at most 128 KiB')
        return self

async def read_input(request, schema):
    data=bytearray()
    async for chunk in request.stream():
        if len(data)+len(chunk)>MAX_BODY:
            raise HTTPException(413,'template request exceeds 256 KiB')
        data.extend(chunk)
    try:
        payload=schema.model_validate_json(bytes(data))
    except ValidationError as exc:
        raise HTTPException(422,[{'loc':list(e['loc']),'msg':e['msg'],'type':e['type']} for e in exc.errors()]) from None
    # The acknowledgement does not replace the existing public-input secret guard.
    require_public_input(payload.template)
    require_public_input(payload.instruction if isinstance(payload,ReviseInput) else payload.logs)
    return payload

async def generate(request, profile, values):
    state=request.app.state.gateway
    sid='report-template-'+str(uuid4())
    try:
        try:
            output,metadata=await asyncio.wait_for(state.runner.complete(profile,json.dumps(values,ensure_ascii=False),sid),timeout=MODEL_TIMEOUT)
        except Exception:
            raise HTTPException(503,'report template model unavailable or timed out') from None
        if not isinstance(output,str) or not output.strip() or len(output.encode('utf-8'))>262144 or (isinstance(metadata,dict) and (metadata.get('error') or metadata.get('status')=='error')):
            raise HTTPException(503,'report template model returned empty or invalid output')
        if re.match(r'^(?:error|lỗi)\s*:',output.strip(),re.IGNORECASE):
            raise HTTPException(503,'report template model returned an error')
        return redact_public_input(output).text
    finally:
        # Release Linch's live session registry/work before removing durable history.
        try:
            agent=getattr(state.runner,'agent',None)
            release=getattr(agent,'release_session',None)
            if callable(release):
                await asyncio.wait_for(release(sid,force=True),timeout=5)
        except Exception:
            pass
        try:
            session_store=getattr(state.runner,'session_store',None)
            if session_store is not None:
                await asyncio.wait_for(session_store.delete(sid),timeout=5)
        except Exception:
            pass

@router.post('/revise')
async def revise(request:Request,actor:ReviewActor=Depends(get_review_actor)):
    payload=await read_input(request,ReviseInput)
    draft=await generate(request,'report_template_edit',{'template':payload.template,'instruction':payload.instruction})
    if len(draft.encode('utf-8'))>65536:
        raise HTTPException(422,'generated draft exceeds 64 KiB template limit; keep original template and retry')
    original=Counter(re.findall(r'\{\{[^{}]+\}\}',payload.template))
    revised=Counter(re.findall(r'\{\{[^{}]+\}\}',draft))
    if any(revised[token]<count for token,count in original.items()):
        raise HTTPException(422,'generated draft removed or changed existing {{placeholder}} tokens; keep original template and retry')
    return {'draft':draft}

@router.post('/render')
async def render(request:Request,actor:ReviewActor=Depends(get_review_actor)):
    payload=await read_input(request,RenderInput)
    report=await generate(request,'report_template_render',{'template':payload.template,'logs':payload.logs})
    return {'report':report}
