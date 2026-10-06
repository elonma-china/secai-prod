"""Offline approved dataset preparation and explicit expert-agreement release gates."""
from __future__ import annotations

import hashlib
import json
import math
import os
import shutil
import tempfile
from pathlib import Path

from .dataset import MAX_BYTES, deduplicate, load_exports, split_groups, validate_record

BASE_MODEL = 'Qwen/Qwen3-1.7B'


def write_json(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True,
                                    allow_nan=False) + '\n', encoding='utf-8')


def file_hash(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024*1024), b''):
            digest.update(block)
    return digest.hexdigest()


def prepare_dataset(exports, output, *, purpose, seed=42, policy=None, expert_ids=None, campaign_ids=None, partitions=None):
    output = Path(output)
    if output.exists():
        raise FileExistsError('dataset output already exists')
    if policy is not None:
        release_gate({}, purpose=purpose, policy=policy)  # validate before sealing test
    rows, provenance = [], []
    for preference, raw in exports:
        records, source = load_exports(preference, raw, purpose=purpose,
                                      expert_ids=expert_ids, campaign_ids=campaign_ids)
        rows.extend(records)
        provenance.append(source)
    if not rows:
        raise ValueError('no approved winner preferences')
    unique = deduplicate(rows)
    if partitions is None:
        development, test = split_groups(unique, seed=seed, validation_fraction=0.2)
        train, validation = split_groups(development, seed=seed+1, validation_fraction=0.25)
    else:
        if set(partitions)!= {r['item_id'] for r in unique} or any(v not in {'train','validation','test'} for v in partitions.values()):
            raise ValueError('invalid pinned partitions')
        train,validation,test=([r for r in unique if partitions[r['item_id']]==name] for name in ('train','validation','test'))
        if not train or not validation or not test: raise ValueError('empty pinned partition')
        from .dataset import prompt_key
        seen=set()
        for rows in (train,validation,test):
            keys={key for r in rows for key in ['prompt:'+prompt_key(r['prompt']), *['source:'+g for g in r['source_groups'] if g.casefold() not in {'unknown','none','null'}]]}
            if seen & keys: raise ValueError('pinned partition leakage')
            seen|=keys
    output.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix='.reward-dataset-', dir=output.parent))
    try:
        files = {}
        for name, records in [('train', train), ('validation', validation), ('test', test)]:
            path = staging/(name+'.jsonl')
            path.write_text(''.join(json.dumps(r, ensure_ascii=False, sort_keys=True)+'\n'
                                    for r in records), encoding='utf-8')
            if path.stat().st_size > MAX_BYTES:
                raise ValueError('dataset partition exceeds 10 MiB')
            files[path.name] = {'sha256':file_hash(path), 'records':len(records)}
        manifest = {'schema_version':1, 'base_model':BASE_MODEL, 'purpose':purpose,
                    'seed':seed, 'input_records':len(rows), 'unique_records':len(unique),
                    'trusted_expert_ids':sorted(expert_ids or []),
                    'eligible_campaign_ids':sorted(campaign_ids or []),
                    'provenance':provenance, 'policy':policy, 'files':files}
        write_json(staging/'manifest.json', manifest)
        os.rename(staging, output)
        return manifest
    finally:
        if staging.exists(): shutil.rmtree(staging)


def read_dataset(directory):
    directory = Path(directory)
    try:
        if (directory/'manifest.json').stat().st_size > 65536:
            raise ValueError('dataset manifest exceeds limit')
        manifest = json.loads((directory/'manifest.json').read_text())
        expected = {'train.jsonl', 'validation.jsonl', 'test.jsonl'}
        if (manifest['schema_version'] != 1 or manifest['base_model'] != BASE_MODEL or
                manifest['purpose'] not in {'production','demo'} or set(manifest['files']) != expected):
            raise ValueError('invalid dataset manifest')
        partitions = {}
        for name in sorted(expected):
            path = directory/name
            if path.is_symlink() or path.stat().st_size > MAX_BYTES or file_hash(path) != manifest['files'][name]['sha256']:
                raise ValueError('dataset integrity mismatch')
            rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
            if not rows or len(rows) != manifest['files'][name]['records']:
                raise ValueError('dataset integrity record count mismatch')
            for row in rows: validate_record(row)
            partitions[name.removesuffix('.jsonl')] = rows
        # Reject manifests altered to move a prompt/conversation across partitions.
        from .dataset import prompt_key
        seen = set()
        for rows in partitions.values():
            keys = set()
            for row in rows:
                keys.add('prompt:'+prompt_key(row['prompt']))
                keys.update('source:'+g for g in row.get('source_groups',[row['source_group']])
                            if g.casefold() not in {'unknown','none','null'})
            if seen & keys: raise ValueError('dataset partition leakage')
            seen.update(keys)
        return partitions, manifest
    except (OSError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise ValueError('invalid dataset') from exc


def release_gate(metrics, *, purpose, policy):
    reasons = []
    if purpose != 'production': reasons.append('demo artifacts cannot serve production')
    if policy is None:
        return {'eligible':False, 'reasons':reasons+['release policy was not set before test evaluation']}
    fields = {'min_test_records','min_accuracy','min_wilson_lower_95','max_invariance_delta'}
    if (set(policy) != fields or type(policy['min_test_records']) is not int or
            policy['min_test_records'] < 1 or
            any(type(policy[k]) not in (int,float) or not math.isfinite(policy[k]) for k in fields) or
            not 0.5 < policy['min_accuracy'] <= 1 or
            not 0.5 < policy['min_wilson_lower_95'] <= policy['min_accuracy'] or
            not 0 <= policy['max_invariance_delta'] <= 0.01):
        raise ValueError('invalid release policy')
    if (type(metrics.get('records')) is not int or metrics['records'] < 1 or
            any(type(metrics.get(k)) not in (int,float) or not math.isfinite(metrics[k]) or
                not 0 <= metrics[k] <= 1 for k in ['accuracy','accuracy_wilson_lower_95']) or
            metrics['accuracy_wilson_lower_95'] > metrics['accuracy'] or
            any(type(metrics.get(k)) not in (int,float) or not math.isfinite(metrics[k]) or
                metrics[k] < 0 for k in ['max_swap_score_delta','max_equal_score_delta'])):
        return {'eligible':False,'reasons':reasons+['invalid evaluation metric domains']}
    for key in ['correct','ties']:
        if key in metrics and (type(metrics[key]) is not int or not 0 <= metrics[key] <= metrics['records']):
            return {'eligible':False,'reasons':reasons+['invalid evaluation counts']}
    if ('correct' in metrics and abs(metrics['accuracy']-metrics['correct']/metrics['records'])>1e-8 or
            metrics.get('correct',0)+metrics.get('ties',0)>metrics['records']):
        return {'eligible':False,'reasons':reasons+['inconsistent evaluation counts']}
    checks = [('records',policy['min_test_records'],True),
              ('accuracy',policy['min_accuracy'],True),
              ('accuracy_wilson_lower_95',policy['min_wilson_lower_95'],True),
              ('max_swap_score_delta',policy['max_invariance_delta'],False),
              ('max_equal_score_delta',policy['max_invariance_delta'],False)]
    for key, threshold, minimum in checks:
        value = metrics.get(key)
        if (type(value) not in (int,float) or not math.isfinite(value) or
                (value < threshold if minimum else value > threshold)):
            reasons.append('failed '+key)
    return {'eligible':not reasons, 'reasons':reasons}
