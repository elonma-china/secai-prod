"""Disposable GPU child. Parent supervises memory, ownership and cleanup."""
import argparse
import os
import time
from pathlib import Path

from .pipeline import file_hash
from .training_jobs import REVISION, atomic_json


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('directory')
    args = parser.parse_args()
    root = Path(args.directory)
    import torch
    torch.cuda.set_per_process_memory_fraction(6144 / (torch.cuda.get_device_properties(0).total_memory / 1024**2))
    torch.cuda.init()
    atomic_json(root/'owner.json', {'pid':os.getpid(), 'nonce':os.environ['REWARD_JOB_NONCE']})
    from .transformer import train, evaluate_artifact
    def progress(event):
        atomic_json(root/'progress.json',event)
    train(root/'dataset', root/'artifact', revision=REVISION, mode='lora', device='cuda',
          epochs=1, learning_rate=1e-4, max_length=384, local_files_only=True,on_progress=progress)
    atomic_json(root/'progress.json', {'stage':'evaluating'})
    result = evaluate_artifact(root/'dataset', root/'artifact', device='cuda')
    atomic_json(root/'result.json', {'test':result['test'], 'gate':result['gate'],
                                    'artifact_sha256':file_hash(root/'artifact/artifact.json')})
    # Keep CUDA ownership until the parent observes the result and reaps this child.
    while True:
        time.sleep(1)


if __name__ == '__main__':
    main()
