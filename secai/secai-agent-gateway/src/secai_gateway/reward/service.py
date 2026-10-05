"""Separate private model service. Gateway never loads torch or model weights."""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import hmac
import json
import math
import re
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from pydantic import ValidationError

from .schema import ScoreInput

MAX_BODY=262144


def create_service(scorer, *, artifact_id, token_sha256):
    if not re.fullmatch('[0-9a-f]{64}',artifact_id) or not re.fullmatch('[0-9a-f]{64}',token_sha256):
        raise ValueError('service requires artifact SHA-256 and token SHA-256')
    app=FastAPI(title='Private Qwen3 reward scorer',docs_url=None,redoc_url=None,openapi_url=None)
    lock=asyncio.Lock()

    @app.post('/score')
    async def score(request:Request):
        parts=request.headers.get('authorization','').split()
        if (len(parts)!=2 or parts[0].lower()!='bearer' or
                not hmac.compare_digest(hashlib.sha256(parts[1].encode()).hexdigest(),token_sha256)):
            raise HTTPException(401,'valid service bearer token required')
        body=bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body)>MAX_BODY:raise HTTPException(413,'reward request exceeds limit')
        try:payload=ScoreInput.model_validate_json(body)
        except ValidationError:raise HTTPException(422,'invalid reward scoring input') from None
        # One inference at a time; retain ownership until the worker finishes even if
        # the HTTP task is cancelled. Avoid parallel GPU workers after disconnects.
        async with lock:
            task=asyncio.create_task(asyncio.to_thread(scorer.scores,payload.messages(),payload.answers))
            try:
                scores=await asyncio.shield(task)
            except asyncio.CancelledError:
                try:await task
                finally:raise
            except ValueError:
                raise HTTPException(422,'reward scoring input or output invalid') from None
            except Exception:
                raise HTTPException(503,'reward model unavailable') from None
        if len(scores)!=len(payload.answers) or any(type(s) not in (int,float) or not math.isfinite(s) for s in scores):
            raise HTTPException(503,'reward model returned invalid scores')
        return {'artifact_id':artifact_id,'scores':scores}
    return app


def main():
    parser=argparse.ArgumentParser(description='Serve an eligible checksum-pinned Qwen3 reward artifact')
    parser.add_argument('--artifact',required=True)
    parser.add_argument('--sha256',required=True)
    parser.add_argument('--token-hash-file',required=True,help='private file containing SHA-256 of service token')
    parser.add_argument('--device',default='cpu')
    parser.add_argument('--host',default='127.0.0.1')
    parser.add_argument('--port',type=int,default=8198)
    args=parser.parse_args()
    from .artifact import verify_artifact
    from .transformer import load_scorer
    import uvicorn
    metadata=verify_artifact(args.artifact,args.sha256)
    scorer=load_scorer(args.artifact,metadata,device=args.device)
    app=create_service(scorer,artifact_id=args.sha256,
                       token_sha256=Path(args.token_hash_file).read_text().strip())
    uvicorn.run(app,host=args.host,port=args.port)


if __name__=='__main__':main()
