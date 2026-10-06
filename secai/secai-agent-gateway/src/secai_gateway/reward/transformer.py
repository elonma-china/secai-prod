"""Optional offline Qwen3 scalar training. No import of torch in the gateway."""
from __future__ import annotations

import math
import re
from pathlib import Path

from .pipeline import BASE_MODEL, file_hash, read_dataset, release_gate, write_json


def pairwise_loss(chosen, rejected):
    import torch
    if chosen.shape != rejected.shape or not torch.isfinite(chosen).all() or not torch.isfinite(rejected).all():
        raise ValueError('invalid pairwise scores')
    return -torch.nn.functional.logsigmoid(chosen-rejected).mean()


class TransformerScorer:
    def __init__(self, model, tokenizer, *, max_length=2048):
        self.model = model.eval()
        self.tokenizer = tokenizer
        self.max_length = max_length
        if tokenizer.pad_token_id is None: tokenizer.pad_token = tokenizer.eos_token
        tokenizer.padding_side = 'right'
        model.config.pad_token_id = tokenizer.pad_token_id

    def encode(self, prompt, answers):
        texts = [self.tokenizer.apply_chat_template(
            prompt+[{'role':'assistant','content':answer}], tokenize=False,
            add_generation_prompt=False, enable_thinking=False) for answer in answers]
        encoded = self.tokenizer(texts, padding=True, truncation=False, add_special_tokens=False,
                                 return_tensors='pt')
        if encoded['input_ids'].shape[1] > self.max_length:
            raise ValueError('prompt + answer exceeds reward token limit; no silent truncation')
        return {key:value.to(next(self.model.parameters()).device) for key,value in encoded.items()}

    def scores(self, prompt, answers):
        import torch
        with torch.inference_mode():
            scores = self.model(**self.encode(prompt, answers)).logits.reshape(-1).float().cpu().tolist()
        if len(scores) != len(answers) or not all(math.isfinite(s) for s in scores):
            raise ValueError('invalid reward model scores')
        return scores

    def score(self, prompt, answer):
        return self.scores(prompt, [answer])[0]


def agreement(scorer, records, *, invariance=False):
    from .model import evaluate
    metrics = evaluate(scorer, records)
    if invariance:
        swap, equal = [], []
        for row in records:
            a,b = scorer.scores(row['prompt'],[row['chosen'],row['rejected']])
            rb,ra = scorer.scores(row['prompt'],[row['rejected'],row['chosen']])
            ea,eb = scorer.scores(row['prompt'],[row['chosen'],row['chosen']])
            swap.extend([abs(a-ra),abs(b-rb)])
            equal.append(abs(ea-eb))
        metrics.update(max_swap_score_delta=max(swap),max_equal_score_delta=max(equal))
    return metrics


def train(dataset, output, *, revision, mode='head', device='cpu', epochs=1,
          learning_rate=1e-4, max_length=2048, local_files_only=True, on_progress=None):
    import torch
    from transformers import AutoModelForSequenceClassification, AutoTokenizer
    if not re.fullmatch(r'[0-9a-f]{40}', revision):
        raise ValueError('base model revision must be an immutable 40-character commit')
    if mode not in {'head','lora'} or epochs < 1 or epochs > 20 or not 0 < learning_rate <= 0.1 or not 32 <= max_length <= 32768:
        raise ValueError('invalid training configuration')
    output = Path(output)
    if output.exists(): raise FileExistsError('training output already exists')
    partitions, manifest = read_dataset(dataset)
    torch.manual_seed(manifest['seed'])
    if on_progress: on_progress({'stage':'loading'})
    tokenizer = AutoTokenizer.from_pretrained(BASE_MODEL,revision=revision,local_files_only=local_files_only,
                                              trust_remote_code=False)
    model = AutoModelForSequenceClassification.from_pretrained(
        BASE_MODEL, revision=revision, num_labels=1, torch_dtype=torch.float32 if device=='cpu' else torch.bfloat16,
        local_files_only=local_files_only, trust_remote_code=False).to(device)
    if mode == 'lora':
        from peft import LoraConfig, TaskType, get_peft_model
        model = get_peft_model(model,LoraConfig(task_type=TaskType.SEQ_CLS,r=8,lora_alpha=16,
                target_modules=['q_proj','v_proj'],modules_to_save=['score'],lora_dropout=0.0))
    else:
        for name, parameter in model.named_parameters(): parameter.requires_grad_(name.startswith('score.'))
    scorer = TransformerScorer(model,tokenizer,max_length=max_length)
    optimizer = torch.optim.AdamW([p for p in model.parameters() if p.requires_grad],lr=learning_rate)
    # Check all rows for token overflow before any training; test content remains unevaluated.
    for row in partitions['train']+partitions['validation']:
        scorer.encode(row['prompt'],[row['chosen'],row['rejected']])
    completed=0; total_steps=epochs*len(partitions['train'])
    for epoch in range(epochs):
        # eval mode disables dropout; gradients remain enabled for trainable head/adapters.
        for index in torch.randperm(len(partitions['train'])).tolist():
            row = partitions['train'][index]
            scores = model(**scorer.encode(row['prompt'],[row['chosen'],row['rejected']])).logits.reshape(-1)
            loss = pairwise_loss(scores[:1],scores[1:])
            loss.backward()
            torch.nn.utils.clip_grad_norm_([p for p in model.parameters() if p.requires_grad],1.0)
            optimizer.step(); optimizer.zero_grad()
            completed+=1
            if on_progress: on_progress({'stage':'training','epoch':epoch+1,'step':completed,'total_steps':total_steps,'loss':float(loss.detach().item())})
    if on_progress: on_progress({'stage':'evaluating'})
    metrics = agreement(scorer,partitions['validation'])
    output.mkdir(parents=True,mode=0o700)
    model.save_pretrained(output,safe_serialization=True)
    tokenizer.save_pretrained(output)
    import importlib.metadata
    metadata = {'schema_version':1,'base_model':BASE_MODEL,'base_revision':revision,
                'purpose':manifest['purpose'],'mode':mode,'max_length':max_length,
                'epochs':epochs,'learning_rate':learning_rate,
                'dataset_manifest_sha256':file_hash(Path(dataset)/'manifest.json'),
                'policy':manifest['policy'],'validation':metrics,
                'versions':{p:importlib.metadata.version(p) for p in
                            (['torch','transformers','peft'] if mode=='lora' else ['torch','transformers'])}}
    write_json(output/'training.json',metadata)
    return metadata


def load_scorer(directory, metadata, *, device='cpu'):
    import torch
    from transformers import AutoModelForSequenceClassification, AutoTokenizer
    directory = Path(directory)
    tokenizer = AutoTokenizer.from_pretrained(directory,local_files_only=True,trust_remote_code=False)
    if metadata['mode'] == 'lora':
        from peft import PeftModel
        base = AutoModelForSequenceClassification.from_pretrained(BASE_MODEL,
            revision=metadata['base_revision'],num_labels=1,local_files_only=True,trust_remote_code=False,
            torch_dtype=torch.float32 if device=='cpu' else torch.bfloat16)
        model = PeftModel.from_pretrained(base,directory,local_files_only=True)
    else:
        model = AutoModelForSequenceClassification.from_pretrained(directory,local_files_only=True,
            trust_remote_code=False,torch_dtype=torch.float32 if device=='cpu' else torch.bfloat16)
    if model.config.num_labels != 1 or model.config.model_type != 'qwen3':
        raise ValueError('reward model must be Qwen3 with one score head')
    return TransformerScorer(model.to(device),tokenizer,max_length=metadata['max_length'])


def evaluate_artifact(dataset, directory, *, device='cpu'):
    import json
    directory = Path(directory)
    if (directory/'evaluation.json').exists():
        raise FileExistsError('test evaluation already sealed; prepare a new experiment for a new evaluation')
    partitions, manifest = read_dataset(dataset)
    metadata = json.loads((directory/'training.json').read_text())
    if metadata['dataset_manifest_sha256'] != file_hash(Path(dataset)/'manifest.json') or metadata['policy'] != manifest['policy']:
        raise ValueError('training dataset/policy mismatch')
    scorer = load_scorer(directory,metadata,device=device)
    metrics = agreement(scorer,partitions['test'],invariance=True)
    report = {'schema_version':1,'purpose':metadata['purpose'],'test':metrics,
              'gate':release_gate(metrics,purpose=metadata['purpose'],policy=metadata['policy'])}
    write_json(directory/'evaluation.json',report)
    from .artifact import seal_artifact
    seal_artifact(directory)
    return report
