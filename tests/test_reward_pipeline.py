"""Technical synthetic cases, not a quality benchmark or expert training corpus."""
import hashlib
import json
import zipfile
from io import BytesIO

import numpy as np
import pytest

from secai_gateway.reward.model import RewardModel, features, fit, evaluate
from secai_gateway.reward.dataset import load_exports, split_groups


def example(i, good='Verify evidence and permissions', bad='Always safe; no verification needed'):
    return {'item_id': str(i), 'prompt': [{'role': 'user', 'content': f'Question about service {i}'}],
            'chosen': good, 'rejected': bad, 'source_group': f'conversation-{i}',
            'rubric_version': 'security_qa_v1', 'adjudication_id': f'approval-{i}'}


def archives(tmp_path, rows, title='Expert campaign', alter=None):
    raw=[]
    for r in rows:
        raw.append({'id':r['item_id'], 'prompt_messages':r['prompt'], 'source_group':r['source_group'],
                    'candidates':[{'id':'a','text':r['chosen']},{'id':'b','text':r['rejected']}],
                    'adjudication':{'id':r['adjudication_id'],'outcome':'winner','winning_candidate_id':'a','admin_id':'admin','reason':'Reviewed'},
                    'judgments':[{'comment':'Reviewed by expert','reviewer_id':'expert',
                                  'decision':'left','winning_candidate_id':'a'}]})
    if alter: alter(raw)
    paths=[]
    for kind, values in [('preference',rows),('raw',raw)]:
        body=''.join(json.dumps(v,ensure_ascii=False)+'\n' for v in values).encode()
        manifest={'schema_version':1,'campaign_title':title,'campaign_id':'campaign','rubric_version':'security_qa_v1','format':kind,
                  'counts':{'items':len(raw),'records':len(values)},
                  'files':{kind+'.jsonl':{'sha256':hashlib.sha256(body).hexdigest(),'records':len(values)}}}
        path=tmp_path/(kind+'.zip')
        with zipfile.ZipFile(path,'w') as z:
            z.writestr(kind+'.jsonl',body);z.writestr('manifest.json',json.dumps(manifest))
        paths.append(path)
    return paths


def test_pairwise_learning_scoring_and_roundtrip(tmp_path):
    train=[example(i) for i in range(12)]
    model=fit(train, purpose='demo', epochs=200)
    held=[example(i) for i in range(20,25)]
    metrics=evaluate(model,held)
    assert metrics['accuracy']==1 and metrics['log_loss']<0.4
    prompt=held[0]['prompt']
    assert model.score(prompt,held[0]['chosen'])>model.score(prompt,held[0]['rejected'])
    assert not np.array_equal(features(prompt,'Verify permissions'),features([{'role':'user','content':'Verify permissions'}],'Verify permissions'))
    path=tmp_path/'model.json';model.save(path)
    restored=RewardModel.load(path)
    assert restored.model_id==model.model_id
    assert restored.score(prompt,held[0]['chosen'])==pytest.approx(model.score(prompt,held[0]['chosen']))
    data=json.loads(path.read_text());data['weights'][0]=float('nan');path.write_text(json.dumps(data))
    with pytest.raises(ValueError):RewardModel.load(path)


def test_group_split_prevents_prompt_and_conversation_leakage():
    rows=[example(i) for i in range(15)]
    rows[1]['source_group']=rows[0]['source_group']
    rows[2]['prompt']=rows[1]['prompt']
    train, validation=split_groups(rows,seed=4)
    def partition(item):return 'train' if item in train else 'validation'
    assert partition(rows[0])==partition(rows[1])==partition(rows[2])
    assert train and validation
    assert split_groups(rows,seed=4)==(train,validation)
    with pytest.raises(ValueError,match='groups'):
        split_groups([dict(example(i),source_group='same') for i in range(10)])


def test_verified_exports_require_matching_approved_winner(tmp_path):
    rows=[example(i) for i in range(10)]
    pref, raw=archives(tmp_path,rows)
    loaded, provenance=load_exports(pref,raw,purpose='production',expert_ids={'expert'},campaign_ids={'campaign'})
    assert loaded==rows and provenance['campaign_id']=='campaign'
    pref,raw=archives(tmp_path,rows,alter=lambda raw:raw[0]['adjudication'].update(outcome='skip'))
    with pytest.raises(ValueError,match='approved'):load_exports(pref,raw,purpose='production',expert_ids={'expert'},campaign_ids={'campaign'})


def test_export_hash_and_qa_exclusion(tmp_path):
    rows=[example(i) for i in range(10)]
    pref,raw=archives(tmp_path,rows,alter=lambda raw:raw[0]['adjudication'].update(reason='TECHNICAL QA ONLY: do not train'))
    with pytest.raises(ValueError,match='QA'):load_exports(pref,raw,purpose='production',expert_ids={'expert'},campaign_ids={'campaign'})
    assert len(load_exports(pref,raw,purpose='demo')[0])==10
    with pytest.warns(UserWarning,match='Duplicate name'):
        with zipfile.ZipFile(pref,'a') as z:z.writestr('preference.jsonl',b'altered')
    with pytest.raises(ValueError):load_exports(pref,raw,purpose='demo')
