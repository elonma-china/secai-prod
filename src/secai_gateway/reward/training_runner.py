"""Vast adapter to the accepted shared GPU ownership/budget supervisor."""
import asyncio
import json
import os
import secrets
import sys
import time
from pathlib import Path

from deploy.gpu_stage.coordinator import GpuBudget, StageFailure
from deploy.gpu_stage.owner import GpuStageOwner, NvidiaSmiObserver, PinnedCommand

from .pipeline import file_hash
from .training_jobs import REVISION


class VastTrainingRunner:
    def __init__(self, specialist_config):
        config = json.loads(Path(specialist_config).read_text())
        gpu = config['gpu']
        self.budget = GpuBudget(gpu['uuid'], frozenset(gpu['incumbents']))
        self.lock_path = Path(config['environment']['OCR_SHARED_GPU_LOCK_PATH'])

    async def __call__(self, jobs, job):
        root = jobs.root/job['id']
        jobs.update(job['id'], status='waiting_gpu')
        owner = GpuStageOwner(self.lock_path, self.budget, NvidiaSmiObserver())
        waiting_until = time.monotonic()+600
        while True:
            try:
                await owner.start()
                break
            except BlockingIOError:
                if time.monotonic() >= waiting_until:
                    raise StageFailure('TIMEOUT', pre_admission=True)
                await asyncio.sleep(1)
        try:
            nonce = secrets.token_hex(32)
            source = Path(__file__).resolve().parents[2]
            bundle = source.parent
            model = Path(os.environ['HF_HOME'])/'hub/models--Qwen--Qwen3-1.7B/snapshots'/REVISION
            if not model.is_dir():
                raise ValueError('pinned base model is not cached')
            files = [*source.rglob('*.py'), *bundle.joinpath('deploy/gpu_stage').glob('*.py'),
                     bundle/'deploy/mt_cpu_service/child_supervisor.py', *model.glob('*'),
                     *root.joinpath('dataset').glob('*')]
            artifacts = await asyncio.to_thread(lambda: tuple((p, file_hash(p)) for p in files if p.is_file()))
            python = Path(sys.executable)
            command = PinnedCommand(binary=python,
                args=('-m', 'secai_gateway.reward.training_worker', str(root)), sha256=file_hash(python),
                artifacts=artifacts, environment={'PYTHONPATH':str(source)+':'+str(bundle),
                    'CUDA_VISIBLE_DEVICES':self.budget.uuid, 'HF_HOME':os.environ['HF_HOME'],
                    'HF_HUB_OFFLINE':'1', 'TRANSFORMERS_OFFLINE':'1', 'REWARD_JOB_NONCE':nonce,
                    'LD_LIBRARY_PATH':os.environ.get('LD_LIBRARY_PATH', '')},
                stdio='log', log_path=root/'worker.log')
            async def proof(proc, host_pid):
                try:
                    data = json.loads((root/'owner.json').read_text())
                    return data['pid'] == proc.pid and data['nonce'] == nonce
                except FileNotFoundError:
                    return None

            async def work(proc, cancel, deadline):
                while proc.poll() is None:
                    if (root/'progress.json').exists() and jobs.get(job['id'])['status'] != 'evaluating':
                        jobs.update(job['id'], status='evaluating')
                    if (root/'result.json').exists():
                        return json.loads((root/'result.json').read_text())
                    await asyncio.sleep(.25)
                if proc.returncode != 0:
                    raise ValueError('training child failed')
                raise ValueError('training child exited without a result')

            jobs.update(job['id'], status='training')
            result = await owner.run(command, work, proof, deadline=time.monotonic()+3600, cancel=asyncio.Event())
            jobs.update(job['id'], status='succeeded', result=result)
        finally:
            await owner.close()
