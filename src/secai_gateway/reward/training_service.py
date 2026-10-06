"""Single-worker private job API. Authenticated transport only; no activation endpoint."""
from __future__ import annotations

import argparse
import asyncio
import fcntl
import hashlib
import hmac
import json
import re
from contextlib import asynccontextmanager, suppress
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request

from .training_jobs import ACTIVE, TrainingJobs


def create_training_service(jobs, *, token_sha256, runner=None):
    if not re.fullmatch('[0-9a-f]{64}', token_sha256):
        raise ValueError('private token hash required')

    @asynccontextmanager
    async def lifespan(app):
        with (jobs.root/'service.lock').open('a+') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            for j in await asyncio.to_thread(jobs.list):
                if j['status'] in ACTIVE - {'queued'}:
                    await asyncio.to_thread(jobs.update, j['id'], status='interrupted', error='Dịch vụ đã khởi động lại; job bị gián đoạn.')
            async def worker():
                while True:
                    pending = [j for j in reversed(await asyncio.to_thread(jobs.list)) if j['status'] == 'queued']
                    if pending and runner is not None:
                        job = pending[0]
                        try:
                            await runner(jobs, job)
                        except asyncio.CancelledError:
                            await asyncio.to_thread(jobs.update, job['id'], status='interrupted', error='Dịch vụ dừng trong khi huấn luyện.')
                            raise
                        except Exception:
                            await asyncio.to_thread(jobs.update, job['id'], status='failed', error='Job thất bại; kiểm tra log riêng trên VPS.')
                    await asyncio.sleep(.5)
            task = asyncio.create_task(worker())
            try:
                yield
            finally:
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task

    app = FastAPI(title='Private QA reward training', lifespan=lifespan,
                  docs_url=None, redoc_url=None, openapi_url=None)

    def auth(request):
        parts = request.headers.get('authorization', '').split()
        if len(parts) != 2 or parts[0].lower() != 'bearer' or not hmac.compare_digest(
                hashlib.sha256(parts[1].encode()).hexdigest(), token_sha256):
            raise HTTPException(401, 'valid training service bearer required')

    @app.post('/jobs', status_code=202)
    async def submit(request: Request):
        auth(request)
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > 29 * 1024 * 1024:
                raise HTTPException(413, 'training request exceeds limit')
        try:
            return await asyncio.to_thread(jobs.submit, json.loads(body))
        except (ValueError, TypeError, KeyError):
            raise HTTPException(422, 'Invalid QA exports: verify approvals, checksums and at least three independent groups') from None

    @app.get('/jobs/{jid}')
    async def get(jid: str, request: Request):
        auth(request)
        job = await asyncio.to_thread(jobs.get, jid)
        if job is None:
            raise HTTPException(404, 'job not found')
        return job

    @app.get('/campaigns/{cid}/jobs')
    async def list_jobs(cid: str, request: Request):
        auth(request)
        return {'enabled':True, 'purpose':'demo', 'jobs':await asyncio.to_thread(jobs.list, cid)}

    return app


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', required=True)
    parser.add_argument('--token-hash-file', required=True)
    parser.add_argument('--specialist-config', required=True)
    parser.add_argument('--port', type=int, default=8197)
    args = parser.parse_args()
    import uvicorn
    from .training_runner import VastTrainingRunner
    app = create_training_service(TrainingJobs(args.root),
        token_sha256=Path(args.token_hash_file).read_text().strip(),
        runner=VastTrainingRunner(args.specialist_config))
    uvicorn.run(app, host='127.0.0.1', port=args.port, workers=1)


if __name__ == '__main__':
    main()
