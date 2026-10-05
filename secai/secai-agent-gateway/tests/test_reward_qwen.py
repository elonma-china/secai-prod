"""Offline technical tests; synthetic labels are never production quality evidence."""
import json
import math
from pathlib import Path

import pytest

from test_reward_pipeline import archives, example
from test_review_auth import client, ADMIN


def test_dedup_preserves_all_conversation_links():
    from secai_gateway.reward.dataset import deduplicate, split_groups
    rows = [example(i) for i in range(20)]
    duplicate = dict(rows[0], item_id='duplicate', source_group='conversation-1')
    rows.insert(1, duplicate)
    unique = deduplicate(rows)
    train, validation = split_groups(unique, seed=7)
    assert (unique[0] in train) == (next(r for r in unique if r['item_id']=='1') in train)
    assert len(unique) == 20


def test_preparation_checksum_three_disjoint_partitions_and_qa_rejection(tmp_path):
    from secai_gateway.reward.pipeline import prepare_dataset, read_dataset
    pref, raw = archives(tmp_path, [example(i) for i in range(40)])
    target = tmp_path/'dataset'
    manifest = prepare_dataset([(pref, raw)], target, purpose='production', seed=42,
                               expert_ids={'expert'},campaign_ids={'campaign'})
    partitions, loaded = read_dataset(target)
    assert manifest == loaded and manifest['purpose'] == 'production'
    assert set(partitions) == {'train', 'validation', 'test'}
    groups = [{r['source_group'] for r in partitions[p]} for p in partitions]
    assert all(groups) and not groups[0]&groups[1] and not groups[0]&groups[2] and not groups[1]&groups[2]
    (target/'test.jsonl').write_text('{}\n')
    with pytest.raises(ValueError, match='integrity'): read_dataset(target)
    with pytest.raises(FileExistsError): prepare_dataset([(pref, raw)], target, purpose='production')
    pref, raw = archives(tmp_path, [example(i) for i in range(40)], title='QA demo')
    with pytest.raises(ValueError, match='QA'): prepare_dataset([(pref, raw)], tmp_path/'bad', purpose='production', expert_ids={'expert'},campaign_ids={'campaign'})
    assert not (tmp_path/'bad').exists()


def test_pairwise_gradient_and_real_tiny_qwen_scalar_roundtrip(tmp_path):
    torch = pytest.importorskip('torch')
    from secai_gateway.reward.transformer import pairwise_loss
    try:
        from transformers import Qwen3Config, Qwen3ForSequenceClassification
    except ImportError as exc:
        pytest.skip(f'optional reward environment unavailable: {exc}')
    torch.set_num_threads(1)
    torch.manual_seed(42)
    model = Qwen3ForSequenceClassification(Qwen3Config(
        vocab_size=32, hidden_size=16, intermediate_size=32, num_hidden_layers=1,
        num_attention_heads=2, num_key_value_heads=1, head_dim=8,
        max_position_embeddings=64, num_labels=1, pad_token_id=0))
    for name, p in model.named_parameters(): p.requires_grad_(name.startswith('score.'))
    batch = {'input_ids':torch.tensor([[1,2,3,4],[1,2,5,6]]), 'attention_mask':torch.ones((2,4),dtype=torch.long)}
    optimizer = torch.optim.AdamW([p for p in model.parameters() if p.requires_grad],lr=0.05)
    model.eval()
    initial = pairwise_loss(model(**batch).logits.flatten()[:1],model(**batch).logits.flatten()[1:]).item()
    for _ in range(20):
        scores=model(**batch).logits.flatten()
        loss=pairwise_loss(scores[:1],scores[1:]);loss.backward();optimizer.step();optimizer.zero_grad()
    final=model(**batch).logits.detach().flatten()
    assert final[0] > final[1] and pairwise_loss(final[:1],final[1:]).item() < initial
    model.save_pretrained(tmp_path/'tiny',safe_serialization=True)
    restored=Qwen3ForSequenceClassification.from_pretrained(tmp_path/'tiny',local_files_only=True).eval()
    assert torch.allclose(restored(**batch).logits.flatten(),final)
    with pytest.raises(ValueError): pairwise_loss(torch.tensor([float('nan')]),torch.tensor([0.]))


def test_expert_release_gate_never_promotes_demo_or_missing_policy():
    from secai_gateway.reward.pipeline import release_gate
    metrics={'records':100,'accuracy':0.9,'accuracy_wilson_lower_95':0.825,
             'max_swap_score_delta':0.0,'max_equal_score_delta':0.0}
    policy={'min_test_records':100,'min_accuracy':0.8,'min_wilson_lower_95':0.7,
            'max_invariance_delta':1e-5}
    assert not release_gate(metrics,purpose='production',policy=None)['eligible']
    assert not release_gate(metrics,purpose='demo',policy=policy)['eligible']
    assert release_gate(metrics,purpose='production',policy=policy)['eligible']
    assert not release_gate({**metrics,'accuracy':0.5},purpose='production',policy=policy)['eligible']
    assert not release_gate({**metrics,'max_swap_score_delta':float('nan')},purpose='production',policy=policy)['eligible']
    with pytest.raises(ValueError): release_gate(metrics,purpose='production',policy={**policy,'min_accuracy':-1})


@pytest.mark.parametrize('mode',['head','lora'])
def test_offline_training_evaluation_and_artifact_roundtrip(tmp_path,monkeypatch,mode,client):
    torch=pytest.importorskip('torch')
    try:
        from transformers import AutoModelForSequenceClassification, AutoTokenizer, Qwen3Config, Qwen3ForSequenceClassification, PreTrainedTokenizerFast
        from tokenizers import Tokenizer
        from tokenizers.models import WordLevel
        from tokenizers.pre_tokenizers import Whitespace
        if mode=='lora':__import__('peft')
    except ImportError as exc:pytest.skip(f'optional reward dependencies unavailable: {exc}')
    from secai_gateway.reward.pipeline import BASE_MODEL, prepare_dataset, file_hash
    from secai_gateway.reward.transformer import train,evaluate_artifact,load_scorer
    from secai_gateway.reward.artifact import verify_artifact
    torch.set_num_threads(1)
    source=tmp_path/'tiny-source'
    tokenizer_backend=Tokenizer(WordLevel({'<pad>':0,'<unk>':1,'user':2,'assistant':3,
        'Question':4,'about':5,'service':6,'Good':7,'Bad':8},unk_token='<unk>'))
    tokenizer_backend.pre_tokenizer=Whitespace()
    tokenizer=PreTrainedTokenizerFast(tokenizer_object=tokenizer_backend,pad_token='<pad>',unk_token='<unk>')
    tokenizer.chat_template="{% for message in messages %}{{ message['role'] }} {{ message['content'] }} {% endfor %}"
    tokenizer.save_pretrained(source)
    model=Qwen3ForSequenceClassification(Qwen3Config(vocab_size=9,hidden_size=16,intermediate_size=32,
        num_hidden_layers=1,num_attention_heads=2,num_key_value_heads=1,head_dim=8,
        max_position_embeddings=128,num_labels=1,pad_token_id=0))
    model.save_pretrained(source,safe_serialization=True)
    original_model_loader=AutoModelForSequenceClassification.from_pretrained
    original_tokenizer_loader=AutoTokenizer.from_pretrained
    def model_loader(name,**kwargs):
        if name==BASE_MODEL:
            name=source;kwargs.pop('revision',None)
        return original_model_loader(name,**kwargs)
    def tokenizer_loader(name,**kwargs):
        if name==BASE_MODEL:
            name=source;kwargs.pop('revision',None)
        return original_tokenizer_loader(name,**kwargs)
    monkeypatch.setattr(AutoModelForSequenceClassification,'from_pretrained',model_loader)
    monkeypatch.setattr(AutoTokenizer,'from_pretrained',tokenizer_loader)
    pref,raw=archives(tmp_path,[example(i,good='Good',bad='Bad') for i in range(40)],title='Synthetic demo')
    dataset=tmp_path/'dataset'
    prepare_dataset([(pref,raw)],dataset,purpose='demo')
    artifact=tmp_path/'trained'
    metadata=train(dataset,artifact,revision='a'*40,mode=mode,epochs=2,learning_rate=0.01,max_length=128)
    assert 'test' not in metadata and metadata['mode']==mode
    report=evaluate_artifact(dataset,artifact)
    assert report['test']['records']>0 and not report['gate']['eligible']
    digest=file_hash(artifact/'artifact.json')
    assert verify_artifact(artifact,digest,production=False)['mode']==mode
    scorer=load_scorer(artifact,metadata)
    prompt=[{'role':'user','content':'Question'}]
    assert scorer.scores(prompt,['Good','Bad'])[0]>scorer.scores(prompt,['Good','Bad'])[1]
    # Exercise the real gateway relay against the private service ASGI app and
    # the learned tiny Qwen3 scorer, without a synthetic response from the relay.
    import hashlib
    import httpx
    from secai_gateway.reward.service import create_service
    service=create_service(scorer,artifact_id=digest,
        token_sha256=hashlib.sha256(b'technical-test-service').hexdigest())
    settings=client.app.state.gateway.settings
    settings.reward_enabled=True
    settings.reward_service_url='http://private-test'
    settings.reward_service_token='technical-test-service'
    settings.reward_artifact_sha256=digest
    client.app.state.gateway.reward_transport=httpx.ASGITransport(app=service)
    result=client.post('/review-api/reward/score',headers=ADMIN,
                       json={'prompt':prompt,'answers':['Good','Bad']})
    assert result.status_code==200 and result.json()['preferred_index']==0
    assert result.json()['artifact_id']==digest
    with pytest.raises(FileExistsError):evaluate_artifact(dataset,artifact)
    with pytest.raises(ValueError,match='token limit'):scorer.scores(prompt,['Good '*200])
    with pytest.raises(ValueError,match='production'):verify_artifact(artifact,digest)


def test_prepare_cli_and_no_data_failure(tmp_path,capsys):
    from secai_gateway.reward.cli import main
    pref,raw=archives(tmp_path,[example(i) for i in range(30)],title='Synthetic demo')
    assert main(['prepare','--exports',str(pref),str(raw),'--output',str(tmp_path/'prepared'),'--purpose','demo'])==0
    manifest=json.loads(capsys.readouterr().out)
    assert manifest['purpose']=='demo' and manifest['unique_records']==30
    assert main(['prepare','--exports',str(pref),str(raw),'--output',str(tmp_path/'no'),'--purpose','production'])==1
    captured=capsys.readouterr()
    assert 'chosen' not in captured.err and not (tmp_path/'no').exists()


def test_customer_feedback_requires_trusted_expert_before_training(tmp_path):
    from secai_gateway.reward.dataset import load_exports
    pref,raw=archives(tmp_path,[example(i) for i in range(5)],
        alter=lambda items: [item['judgments'][0].update(reviewer_id='customer') for item in items])
    with pytest.raises(ValueError,match='expert'):
        load_exports(pref,raw,purpose='production',expert_ids={'expert'},campaign_ids={'campaign'})


@pytest.mark.parametrize('decision,winner',[('tie',None),('right','b')])
def test_production_requires_expert_agreement_with_approved_winner(tmp_path,decision,winner):
    from secai_gateway.reward.dataset import load_exports
    pref,raw=archives(tmp_path,[example(i) for i in range(5)],alter=lambda items:
        [item['judgments'][0].update(decision=decision,winning_candidate_id=winner) for item in items])
    with pytest.raises(ValueError,match='expert'):
        load_exports(pref,raw,purpose='production',expert_ids={'expert'},campaign_ids={'campaign'})


def test_production_requires_known_source_and_explicit_campaign_selection(tmp_path):
    from secai_gateway.reward.dataset import load_exports
    pref,raw=archives(tmp_path,[example(i) for i in range(5)])
    with pytest.raises(ValueError,match='campaign'):
        load_exports(pref,raw,purpose='production',expert_ids={'expert'},campaign_ids={'other'})
    rows=[dict(example(i),source_group='unknown') for i in range(5)]
    pref,raw=archives(tmp_path,rows)
    with pytest.raises(ValueError,match='source'):
        load_exports(pref,raw,purpose='production',expert_ids={'expert'},campaign_ids={'campaign'})
