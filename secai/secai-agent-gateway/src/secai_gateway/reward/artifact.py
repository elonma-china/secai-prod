"""Checksum-pinned local reward artifacts; verify before importing model weights."""
from __future__ import annotations

import json
import re
from pathlib import Path

from .pipeline import BASE_MODEL, file_hash, release_gate, write_json


def seal_artifact(directory):
    directory=Path(directory)
    path=directory/'artifact.json'
    if path.exists():raise FileExistsError('artifact already sealed')
    files={}
    for file in sorted(directory.rglob('*')):
        if file.is_symlink():raise ValueError('artifact symlinks forbidden')
        if file.is_file():files[file.relative_to(directory).as_posix()]=file_hash(file)
    if not {'training.json','evaluation.json'} <= files.keys():
        raise ValueError('artifact requires training and evaluation')
    write_json(path,{'schema_version':1,'files':files})
    return file_hash(path)


def verify_artifact(directory, expected_sha256, *, production=True):
    directory=Path(directory)
    path=directory/'artifact.json'
    try:
        if (not re.fullmatch('[0-9a-f]{64}',expected_sha256) or path.is_symlink() or
                path.stat().st_size>1024*1024 or file_hash(path)!=expected_sha256):
            raise ValueError('artifact integrity mismatch')
        manifest=json.loads(path.read_text())
        actual={p.relative_to(directory).as_posix() for p in directory.rglob('*') if p.is_file() and p!=path}
        if manifest['schema_version']!=1 or set(manifest['files'])!=actual:
            raise ValueError('artifact integrity file list mismatch')
        for name,digest in manifest['files'].items():
            file=directory/name
            if Path(name).is_absolute() or '..' in Path(name).parts or file.is_symlink() or file_hash(file)!=digest:
                raise ValueError('artifact integrity mismatch')
        metadata=json.loads((directory/'training.json').read_text())
        report=json.loads((directory/'evaluation.json').read_text())
        if (metadata['base_model']!=BASE_MODEL or not re.fullmatch('[0-9a-f]{40}',metadata['base_revision']) or
                metadata['mode'] not in {'head','lora'} or not 32<=metadata['max_length']<=32768):
            raise ValueError('invalid Qwen3 artifact metadata')
        if production:
            gate=release_gate(report['test'],purpose=metadata['purpose'],policy=metadata['policy'])
            if metadata['purpose']!='production' or report['purpose']!='production' or not gate['eligible'] or report['gate']!=gate:
                raise ValueError('artifact is not eligible for production')
        return metadata
    except (OSError,KeyError,TypeError,json.JSONDecodeError) as exc:
        raise ValueError('invalid reward artifact') from exc
