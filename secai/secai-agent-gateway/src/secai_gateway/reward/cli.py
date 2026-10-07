"""Offline operator commands; no automatic training, downloads or activation."""
import argparse
import json
import sys
from pathlib import Path

from fastapi import HTTPException

from .pipeline import file_hash, prepare_dataset


def main(argv=None):
    parser=argparse.ArgumentParser(description='Qwen3-1.7B approved preference reward pipeline')
    commands=parser.add_subparsers(dest='command',required=True)
    prepare=commands.add_parser('prepare')
    prepare.add_argument('--exports',nargs=2,action='append',required=True,metavar=('PREFERENCE_ZIP','RAW_ZIP'))
    prepare.add_argument('--output',required=True)
    prepare.add_argument('--purpose',choices=['production','demo'],required=True)
    prepare.add_argument('--seed',type=int,default=42)
    prepare.add_argument('--policy',help='JSON release thresholds, sealed before test evaluation')
    prepare.add_argument('--expert-id',action='append',help='trusted expert reviewer actor ID; required for production')
    prepare.add_argument('--campaign-id',action='append',help='explicit eligible campaign ID; required for production')
    train=commands.add_parser('train')
    train.add_argument('--dataset',required=True)
    train.add_argument('--output',required=True)
    train.add_argument('--revision',required=True,help='immutable Hugging Face model commit SHA')
    train.add_argument('--mode',choices=['head','lora'],default='head')
    train.add_argument('--device',default='cpu')
    train.add_argument('--epochs',type=int,default=1)
    train.add_argument('--learning-rate',type=float,default=1e-4)
    train.add_argument('--max-length',type=int,default=2048)
    train.add_argument('--allow-download',action='store_true',help='explicitly allow base model download')
    evaluate=commands.add_parser('evaluate')
    evaluate.add_argument('--dataset',required=True)
    evaluate.add_argument('--artifact',required=True)
    evaluate.add_argument('--device',default='cpu')
    args=parser.parse_args(argv)
    try:
        if args.command=='prepare':
            policy=json.loads(Path(args.policy).read_text()) if args.policy else None
            result=prepare_dataset(args.exports,args.output,purpose=args.purpose,seed=args.seed,
                                   policy=policy,expert_ids=set(args.expert_id or []),
                                   campaign_ids=set(args.campaign_id or []))
        elif args.command=='train':
            from .transformer import train as train_model
            result=train_model(args.dataset,args.output,revision=args.revision,mode=args.mode,device=args.device,
                epochs=args.epochs,learning_rate=args.learning_rate,max_length=args.max_length,
                local_files_only=not args.allow_download)
        else:
            from .transformer import evaluate_artifact
            result=evaluate_artifact(args.dataset,args.artifact,device=args.device)
            result={**result,'artifact_sha256':file_hash(Path(args.artifact)/'artifact.json')}
        print(json.dumps(result,ensure_ascii=False,allow_nan=False))
        return 0
    except (OSError,ValueError,KeyError,TypeError,ImportError,HTTPException):
        # No input text, credentials or filesystem exception details in operator logs.
        print('Reward command failed: check approved exports, configuration, optional dependencies and local model cache.',file=sys.stderr)
        return 1


if __name__=='__main__':raise SystemExit(main())
