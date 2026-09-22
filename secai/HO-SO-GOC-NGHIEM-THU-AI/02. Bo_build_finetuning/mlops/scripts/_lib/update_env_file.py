#!/usr/bin/env python3
"""Cập nhật/thêm vài KEY=value trong 1 file .env, GIỮ NGUYÊN mọi dòng khác (thứ tự, comment,
biến không liên quan) — dùng khi promote model mới: trỏ lại QWEN_BASE_URL/EMBEDDING_API_URL/
RERANKER_API_URL sau khi rent instance production mới. Không có script sửa .env tại chỗ nào có
sẵn trong repo (các hướng dẫn đều là sửa tay theo HUONG-DAN-CAI-DAT.md) nên đây là code mới.
"""
from __future__ import annotations

import argparse
import shutil
import time
from pathlib import Path


def set_keys(path: Path, updates: dict[str, str]) -> None:
    lines = path.read_text(encoding="utf-8").splitlines() if path.is_file() else []
    remaining = dict(updates)
    out = []
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key = stripped.split("=", 1)[0].strip()
            if key in remaining:
                out.append(f"{key}={remaining.pop(key)}")
                continue
        out.append(line)
    for key, value in remaining.items():
        out.append(f"{key}={value}")

    if path.is_file():
        backup = path.with_name(f"{path.name}.bak.mlops-promote-{int(time.time())}")
        shutil.copy2(path, backup)
        print(f"[update_env_file] backup -> {backup}")
    path.write_text("\n".join(out) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--file", type=Path, required=True)
    parser.add_argument("--set", action="append", default=[], metavar="KEY=VALUE", required=True)
    args = parser.parse_args()
    updates = dict(item.split("=", 1) for item in args.set)
    set_keys(args.file, updates)
    print(f"[update_env_file] {args.file}: cập nhật {sorted(updates)}")


if __name__ == "__main__":
    main()
