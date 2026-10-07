"""Validate paired IAS exports and split connected prompt/conversation groups."""
from __future__ import annotations

import hashlib
import json
import math
import re
import unicodedata
import zipfile
from pathlib import Path

from ..api.review_schemas import Message
from ..security import require_public_input

MAX_BYTES = 10 * 1024 * 1024
QA_MARKER = re.compile(r'\bqa\b|\bdemo\b|\be2e\b|technical qa only|không dùng (?:training|huấn luyện)|do not train', re.I)


def _canonical(text):
    return ' '.join(unicodedata.normalize('NFKC', text).casefold().split())


def prompt_key(prompt):
    value = [(m['role'], _canonical(m['content'])) for m in prompt]
    return hashlib.sha256(json.dumps(value, ensure_ascii=False).encode()).hexdigest()


def _read(path, kind):
    filename = kind + '.jsonl'
    try:
        if Path(path).stat().st_size > MAX_BYTES:
            raise ValueError('export exceeds 10 MiB')
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
            if sorted(names) != sorted([filename, 'manifest.json']):
                raise ValueError('export must contain exactly the dataset and manifest')
            if any(info.file_size > MAX_BYTES for info in archive.infolist()):
                raise ValueError('uncompressed export exceeds 10 MiB')
            if archive.getinfo('manifest.json').file_size > 65536:
                raise ValueError('manifest exceeds 64 KiB')
            body = archive.read(filename)
            manifest = json.loads(archive.read('manifest.json'))
        rows = [json.loads(line) for line in body.splitlines() if line.strip()]
        if not isinstance(manifest, dict) or any(not isinstance(row, dict) for row in rows):
            raise ValueError('invalid review export records')
        if (manifest['schema_version'] != 1 or manifest['format'] != kind or
                not manifest.get('campaign_id') or
                manifest['files'][filename]['sha256'] != hashlib.sha256(body).hexdigest() or
                manifest['counts']['records'] != len(rows) or
                manifest['files'][filename]['records'] != len(rows)):
            raise ValueError('manifest hash, schema or counts mismatch')
    except (KeyError, TypeError, json.JSONDecodeError, UnicodeDecodeError, zipfile.BadZipFile) as exc:
        raise ValueError('invalid review export') from exc
    return rows, manifest


def validate_record(row):
    try:
        messages = [Message.model_validate(m).model_dump() for m in row['prompt']]
        if not messages or len(messages) > 100 or messages[0]['role'] != 'user' or messages[-1]['role'] != 'user':
            raise ValueError('invalid preference prompt')
        if sum(len(m['content'].encode()) for m in messages) > 65536:
            raise ValueError('preference prompt exceeds 64 KiB')
        for message in messages:
            require_public_input(message['content'])
        for name in ['chosen', 'rejected']:
            value = row[name]
            if not isinstance(value, str) or not value.strip() or len(value.encode()) > 65536:
                raise ValueError('invalid preference answer')
            require_public_input(value)
        if _canonical(row['chosen']) == _canonical(row['rejected']):
            raise ValueError('preference answers must differ')
        if any(not isinstance(row.get(k), str) or not row[k].strip()
               for k in ['item_id', 'adjudication_id', 'rubric_version', 'source_group']):
            raise ValueError('missing approval/provenance fields')
        if 'source_groups' in row and (not isinstance(row['source_groups'], list) or
                not row['source_groups'] or any(not isinstance(g, str) or not g.strip()
                                              for g in row['source_groups'])):
            raise ValueError('invalid source groups')
    except (KeyError, TypeError) as exc:
        raise ValueError('invalid preference record') from exc


def load_exports(preference_path, raw_path, *, purpose, expert_ids=None, campaign_ids=None):
    if purpose not in {'demo', 'production'}:
        raise ValueError('purpose must be demo or production')
    if purpose == 'production' and (not expert_ids or any(not isinstance(e, str) or not e.strip() for e in expert_ids)):
        raise ValueError('production training requires explicit trusted expert IDs')
    preferences, pm = _read(preference_path, 'preference')
    raw, rm = _read(raw_path, 'raw')
    if purpose == 'production' and (not campaign_ids or pm['campaign_id'] not in campaign_ids):
        raise ValueError('production training requires explicit eligible campaign IDs')
    if any(pm.get(k) != rm.get(k) for k in ['campaign_id', 'rubric_version', 'campaign_title']):
        raise ValueError('raw and preference exports must match the campaign')
    if purpose == 'production' and not pm.get('campaign_title'):
        raise ValueError('production training requires campaign_title; re-export with the current gateway')
    if purpose == 'production' and QA_MARKER.search(pm.get('campaign_title', '')):
        raise ValueError('QA/demo campaigns cannot train a production model')
    items = {i['id']: i for i in raw}
    if len(items) != len(raw):
        raise ValueError('duplicate raw item IDs')
    seen = set()
    for row in preferences:
        validate_record(row)
        if purpose == 'production':
            groups = row.get('source_groups', [row['source_group']])
            if (row['source_group'] not in groups or
                    any(g.strip().casefold() in {'unknown', 'none', 'null'} for g in groups)):
                raise ValueError('production requires known stable source conversation IDs')
        if row['item_id'] in seen:
            raise ValueError('duplicate preference item IDs')
        seen.add(row['item_id'])
        item = items.get(row['item_id'])
        approval = item.get('adjudication') if item else None
        if (not approval or approval.get('outcome') != 'winner' or
                approval.get('id') != row['adjudication_id'] or
                not approval.get('admin_id') or not approval.get('reason') or not item.get('judgments')):
            raise ValueError('preference must match an approved winner with expert judgments')
        if purpose == 'production' and not any(j.get('reviewer_id') in expert_ids and
                j.get('decision') in {'left', 'right'} and
                j.get('winning_candidate_id') == approval.get('winning_candidate_id')
                for j in item['judgments']):
            raise ValueError('approved winner requires agreement from a trusted expert')
        candidates = item.get('candidates', [])
        chosen = [c for c in candidates if c.get('id') == approval.get('winning_candidate_id')]
        rejected = [c for c in candidates if c.get('id') != approval.get('winning_candidate_id')]
        if (len(chosen) != 1 or len(rejected) != 1 or chosen[0].get('text') != row['chosen'] or
                rejected[0].get('text') != row['rejected'] or item.get('prompt_messages') != row['prompt'] or
                item.get('source_group') != row['source_group'] or row['rubric_version'] != pm['rubric_version']):
            raise ValueError('approved source and preference content differ')
        comments = [approval['reason']] + [j.get('comment', '') for j in item['judgments']]
        if purpose == 'production' and any(QA_MARKER.search(c) for c in comments):
            raise ValueError('QA/demo judgments cannot train a production model')
    expected = {i['id'] for i in raw if i.get('adjudication') and i['adjudication'].get('outcome') == 'winner'}
    if seen != expected:
        raise ValueError('preference export omits approved winners; exports must use the same snapshot')
    provenance = {'campaign_id': pm['campaign_id'], 'rubric_version': pm['rubric_version'],
                  'preference_sha256': pm['files']['preference.jsonl']['sha256'],
                  'raw_sha256': rm['files']['raw.jsonl']['sha256']}
    return preferences, provenance


def deduplicate(records):
    unique = {}
    for row in records:
        p = prompt_key(row['prompt'])
        answers = sorted([_canonical(row['chosen']), _canonical(row['rejected'])])
        key = (p, *answers)
        if key in unique and _canonical(unique[key]['chosen']) != _canonical(row['chosen']):
            raise ValueError('conflicting preferences for the same prompt and answers')
        if key not in unique:
            unique[key] = dict(row, source_groups=list(row.get('source_groups', [row['source_group']])))
        else:
            unique[key]['source_groups'] = sorted(set(unique[key]['source_groups']) |
                                                 set(row.get('source_groups', [row['source_group']])))
    return list(unique.values())


def split_groups(records, *, seed=42, validation_fraction=0.2):
    if not 0 < validation_fraction < 1:
        raise ValueError('validation fraction must be between 0 and 1')
    parents = list(range(len(records)))
    def find(i):
        while parents[i] != i:
            parents[i] = parents[parents[i]]
            i = parents[i]
        return i
    keys = {}
    for i, row in enumerate(records):
        group_keys = ['prompt:' + prompt_key(row['prompt'])]
        for source in row.get('source_groups', [row['source_group']]):
            if source.casefold() not in {'unknown', 'none', 'null'}:
                group_keys.append('source:' + source)
        for key in group_keys:
            if key in keys:
                parents[find(i)] = find(keys[key])
            keys[key] = i
    groups = {}
    for i, row in enumerate(records):
        groups.setdefault(find(i), []).append(row)
    if len(groups) < 2:
        raise ValueError('need at least two independent prompt/conversation groups')
    ordered = sorted(groups.values(), key=lambda group: hashlib.sha256(
        (str(seed) + ':' + '|'.join(sorted(prompt_key(r['prompt']) for r in group))).encode()).hexdigest())
    n = max(1, min(len(ordered) - 1, math.ceil(len(ordered) * validation_fraction)))
    return ([row for group in ordered[n:] for row in group], [row for group in ordered[:n] for row in group])
