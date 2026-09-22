#!/usr/bin/env python3
"""Giai đoạn 4 (trước khi gọi train_*): ghép dữ liệu CVE mới (đã tổng hợp ở Giai đoạn 2,
`$EVAL_DIR/{embedding,reranker,llm_test}.jsonl`) vào ĐUÔI file train tĩnh hiện có của từng
component, tạo 1 file train MỚI (không sửa file train tĩnh gốc) để 04_auto_finetune.sh trỏ vào.

Vì sao cần: `train_llm.py`/`train_embedding.py`/`train_reranker.sh` luôn đọc đúng
`data/{llm,embedding,reranker}/train.jsonl` tĩnh (dữ liệu tại thời điểm finetune lần trước) —
nếu 04_auto_finetune.sh gọi thẳng chúng không đổi gì, "Tự động Finetune" sau khi phát hiện suy
giảm sẽ chỉ train LẠI trên đúng dữ liệu CŨ, không dạy model biết gì thêm về đúng các CVE mới vừa
gây suy giảm — không giải quyết được nguyên nhân. Script này KHÔNG đổi thuật toán train (không
đụng train_*.py/train_reranker.sh) — chỉ tạo input mới rồi trỏ CLI/env override đã có sẵn
(`train_llm.py --dataset-path`, `train_embedding.py --train`, `train_reranker.sh $TRAIN`) vào.

Chỉ ghép vào TRAIN, KHÔNG đụng validation tĩnh: theo đúng docstring của `adapt_eval_set.py`,
gold ở đây suy ra tự động (distant supervision) từ mô tả CVE, chất lượng thấp hơn gold tay của
benchmark gốc — phù hợp làm thêm tín hiệu train hơn là làm tín hiệu chọn model (validation).
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

SYSTEM_PROMPT = (
    "Bạn là trợ lý an toàn thông tin, trả lời ngắn gọn, chính xác bằng tiếng Việt về lỗ hổng "
    "CVE dựa trên kiến thức đã huấn luyện. Không bịa CVE ID hay chi tiết không chắc chắn."
)


def read_jsonl(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + ("\n" if rows else ""),
                     encoding="utf-8")


def to_llm_messages(row: dict) -> dict:
    return {"messages": [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": row["question"]},
        {"role": "assistant", "content": row["answer"]},
    ]}


CONVERTERS = {
    "embedding": lambda row: {"query": row["query"], "positive": row["positive"]},
    "reranker": lambda row: {"query": row["query"], "positive": row["positive"], "negative": row["negative"]},
    "llm": to_llm_messages,
}
SOURCE_FILE = {"embedding": "embedding.jsonl", "reranker": "reranker.jsonl", "llm": "llm_test.jsonl"}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--component", choices=["embedding", "reranker", "llm"], required=True)
    parser.add_argument("--eval-dir", type=Path, required=True, help="$EVAL_DIR của Giai đoạn 2 (đã adapt)")
    parser.add_argument("--static-train", type=Path, required=True, help="data/<component>/train.jsonl gốc")
    parser.add_argument("--out-train", type=Path, required=True)
    args = parser.parse_args()

    static_rows = read_jsonl(args.static_train)
    new_raw = read_jsonl(args.eval_dir / SOURCE_FILE[args.component])
    convert = CONVERTERS[args.component]
    new_rows = [convert(row) for row in new_raw]

    write_jsonl(args.out_train, static_rows + new_rows)
    print(f"[build_incremental_train_data] {args.component}: {len(static_rows)} (tĩnh) + "
          f"{len(new_rows)} (CVE mới) = {len(static_rows) + len(new_rows)} dòng -> {args.out_train}")


if __name__ == "__main__":
    main()
