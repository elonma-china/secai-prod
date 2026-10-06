"""Private, durable QA training snapshots. This module never loads a GPU model."""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import shutil
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from .dataset import MAX_BYTES, _read
from .pipeline import prepare_dataset

REVISION = '70d244cc86ccca08cf5af4e1e306ecf908b1ad5e'
ACTIVE = {'queued', 'waiting_gpu', 'training', 'evaluating'}


def timestamp():
    return datetime.now(timezone.utc).isoformat()


def fsync_directory(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def atomic_json(path, value):
    path = Path(path)
    temporary = path.with_name(path.name + '.tmp-' + str(uuid4()))
    fd = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, 'w') as out:
        json.dump(value, out, ensure_ascii=False, allow_nan=False)
        out.flush()
        os.fsync(out.fileno())
    os.replace(temporary, path)
    fsync_directory(path.parent)


class TrainingJobs:
    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        if self.root.stat().st_mode & 0o077:
            raise ValueError('training directory must be private')
        self.lock = threading.RLock()

    def get(self, jid):
        if not re.fullmatch(r'[0-9a-f-]{36}', jid):
            return None
        path = self.root/jid/'job.json'
        return json.loads(path.read_text()) if path.is_file() else None

    def list(self, campaign_id=None):
        rows = []
        for p in self.root.glob('*/job.json'):
            if not re.fullmatch(r'[0-9a-f-]{36}', p.parent.name):
                continue
            job = json.loads(p.read_text())
            if job.get('id') != p.parent.name:
                raise ValueError('published job identity mismatch')
            rows.append(job)
        return sorted([j for j in rows if campaign_id is None or j['campaign_id'] == campaign_id],
                      key=lambda j: j['created_at'], reverse=True)

    def update(self, jid, **values):
        with self.lock:
            job = self.get(jid)
            if job is None:
                raise ValueError('job not found')
            job.update(values, updated_at=timestamp())
            atomic_json(self.root/jid/'job.json', job)
            return job

    def submit(self, payload):
        if not isinstance(payload, dict) or set(payload) != {'campaign_id', 'preference', 'raw'}:
            raise ValueError('invalid training request')
        with self.lock:
            stage = Path(tempfile.mkdtemp(prefix='.prepare-', dir=self.root))
            try:
                for kind in ('preference', 'raw'):
                    try:
                        content = base64.b64decode(payload[kind], validate=True)
                    except (ValueError, TypeError):
                        raise ValueError('invalid encoded export') from None
                    if len(content) > MAX_BYTES:
                        raise ValueError('export exceeds limit')
                    (stage/(kind+'.zip')).write_bytes(content)
                _, manifest = _read(stage/'preference.zip', 'preference')
                if manifest['campaign_id'] != payload['campaign_id']:
                    raise ValueError('campaign mismatch')
                if not re.search(r'\b(?:qa|demo|e2e)\b', manifest.get('campaign_title', ''), re.I):
                    raise ValueError('Only explicitly marked QA/demo campaigns can use trial training')
                dataset = prepare_dataset([(stage/'preference.zip', stage/'raw.zip')], stage/'dataset', purpose='demo')
                fingerprint = hashlib.sha256(json.dumps({'files':dataset['files'], 'revision':REVISION,
                    'mode':'lora', 'max_length':384, 'epochs':1}, sort_keys=True).encode()).hexdigest()
                for job in self.list(payload['campaign_id']):
                    if job['fingerprint'] == fingerprint and job['status'] != 'failed' and job['status'] != 'interrupted':
                        return job
                if sum(j['status'] in ACTIVE for j in self.list()) >= 4:
                    raise ValueError('training queue is full')
                jid = str(uuid4())
                job = {'id':jid, 'campaign_id':payload['campaign_id'], 'purpose':'demo', 'status':'queued',
                    'created_at':timestamp(), 'updated_at':timestamp(), 'fingerprint':fingerprint,
                    'model':'Qwen/Qwen3-1.7B', 'revision':REVISION, 'mode':'lora', 'max_length':384,
                    'counts':{k.removesuffix('.jsonl'):v['records'] for k,v in dataset['files'].items()},
                    'result':None, 'error':None}
                atomic_json(stage/'job.json', job)
                for path in stage.rglob('*'):
                    if path.is_file():
                        with path.open('rb') as source:
                            os.fsync(source.fileno())
                    elif path.is_dir():
                        fsync_directory(path)
                os.rename(stage, self.root/jid)
                fsync_directory(self.root)
                return job
            finally:
                if stage.exists():
                    shutil.rmtree(stage)
