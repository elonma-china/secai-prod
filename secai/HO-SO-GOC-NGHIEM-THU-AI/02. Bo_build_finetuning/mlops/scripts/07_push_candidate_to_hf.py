#!/usr/bin/env python3
"""Giai đoạn 6 — Push model finetune lên HF: upload 1 checkpoint local lên 1 repo CANDIDATE
riêng (không đụng repo production tới khi 10_promote_or_hold.sh PASS).

Dùng chung cho cả 3 component (embedding/reranker: checkpoint train_*.py; llm: thư mục AWQ của
06_quantize_candidate.sh) — chỉ khác --repo-id truyền vào. Không có script push HF nào có sẵn
trong repo (đã grep xác nhận) nên đây là code mới, dùng thẳng `huggingface_hub.HfApi`, không tự
chế cơ chế upload riêng.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _lib import common


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--local-dir", type=Path, required=True)
    parser.add_argument("--component", choices=["embedding", "reranker", "llm"], required=True)
    parser.add_argument("--target", choices=["candidate", "production"], default="candidate")
    parser.add_argument("--commit-message")
    args = parser.parse_args()

    config = common.load_config()
    hf = config["hf"]
    token = os.environ.get(hf["token_env"])
    if not token:
        raise SystemExit(f"[07_push_candidate_to_hf] thiếu ${hf['token_env']} — export token HF có quyền ghi trước")
    repo_key = "candidate_repos" if args.target == "candidate" else "production_repos"
    repo_id = hf[repo_key][args.component]

    if not args.local_dir.is_dir() or not any(args.local_dir.iterdir()):
        raise SystemExit(f"[07_push_candidate_to_hf] {args.local_dir} rỗng hoặc không tồn tại")

    # Verified live (2026-09-18): push một model lớn (66GB, LLM merged fp16) qua cơ chế Xet
    # mặc định của huggingface_hub bị `TimeoutError: error decoding response body` LẶP LẠI
    # nhiều lần (không phải lỗi code, lỗi tầng transfer Xet với file lớn). Chuyển sang LFS cổ
    # điển (đã dùng đúng biến này ở quantization/run_all.sh cho cùng lý do) chạy ổn định ngay.
    # Không ảnh hưởng repo embedding/reranker nhỏ (checkpoint train_*.py, vài trăm MB-vài GB).
    os.environ.setdefault("HF_HUB_DISABLE_XET", "1")

    from huggingface_hub import HfApi

    api = HfApi(token=token)
    api.create_repo(repo_id, repo_type="model", exist_ok=True, private=False)
    commit = api.upload_folder(
        repo_id=repo_id, repo_type="model", folder_path=str(args.local_dir),
        commit_message=args.commit_message or f"mlops: {args.target} {args.component} từ {args.local_dir}",
    )
    print(f"[07_push_candidate_to_hf] {args.component} -> {repo_id} ({args.target})")
    print(f"[07_push_candidate_to_hf] commit: {commit}")


if __name__ == "__main__":
    main()
