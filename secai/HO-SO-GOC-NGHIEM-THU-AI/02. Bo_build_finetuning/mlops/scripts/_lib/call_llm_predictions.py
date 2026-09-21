#!/usr/bin/env python3
"""Harness tối giản cho `eval_llm_qa.py`: gọi thẳng endpoint LLM (OpenAI-compatible, không tool
schema) cho từng câu hỏi trong `llm_test.jsonl`, ghi `predictions.jsonl` {"id","answer"}.

Đúng theo thiết kế của chính eval_llm_qa.py (đọc docstring gốc): "This intentionally evaluates
answers only: no tool-call or trace metric" — nghĩa là bộ gate này vốn đã định kiểm tra model
trần (kiến thức đã học), không phải qua toàn bộ pipeline RAG/tool-use của gateway. Vì vậy gọi
thẳng endpoint LLM là đúng thiết kế, không phải rút gọn tuỳ tiện.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import json
import time
import urllib.error
import urllib.request
from pathlib import Path

SYSTEM_PROMPT = (
    "Bạn là trợ lý an toàn thông tin, trả lời ngắn gọn, chính xác bằng tiếng Việt về lỗ hổng "
    "CVE dựa trên kiến thức đã huấn luyện. Không bịa CVE ID hay chi tiết không chắc chắn."
)


def call_one(item: dict, *, url: str, model: str, api_key: str, retries: int, timeout: int) -> dict:
    body = {
        "model": model,
        "messages": [{"role": "system", "content": SYSTEM_PROMPT},
                     {"role": "user", "content": item["question"]}],
        "temperature": 0, "max_tokens": 512,
    }
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"}
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, data=json.dumps(body).encode(), headers=headers)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                payload = json.loads(resp.read())
            return {"id": item["id"], "answer": payload["choices"][0]["message"]["content"].strip()}
        except (KeyError, ValueError, urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            if attempt == retries:
                return {"id": item["id"], "answer": "", "error": repr(exc)[:300]}
            time.sleep(1 + attempt)
    raise AssertionError("unreachable")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--test", type=Path, required=True, help="llm_test.jsonl {id,question,answer}")
    parser.add_argument("--llm-url", required=True, help=".../v1/chat/completions")
    parser.add_argument("--llm-model", required=True)
    parser.add_argument("--api-key", required=True)
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--retries", type=int, default=2)
    parser.add_argument("--timeout", type=int, default=60)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    items = [json.loads(line) for line in args.test.read_text(encoding="utf-8").splitlines() if line.strip()]
    endpoint = args.llm_url.rstrip("/")
    if not endpoint.endswith("/chat/completions"):
        endpoint += "/chat/completions"

    with concurrent.futures.ThreadPoolExecutor(max_workers=args.concurrency) as executor:
        futures = [executor.submit(call_one, item, url=endpoint, model=args.llm_model,
                                   api_key=args.api_key, retries=args.retries, timeout=args.timeout)
                  for item in items]
        results = [f.result() for f in futures]

    failed = sum(1 for r in results if not r.get("answer"))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text("\n".join(json.dumps({"id": r["id"], "answer": r["answer"]}, ensure_ascii=False)
                                     for r in results if r.get("answer")) + "\n", encoding="utf-8")
    print(f"[call_llm_predictions] {len(results) - failed}/{len(results)} câu trả lời được -> {args.output}")
    if failed:
        print(f"[call_llm_predictions] CẢNH BÁO: {failed} câu gọi LLM thất bại, bị loại khỏi predictions "
              "(eval_llm_qa.py sẽ báo lỗi 'missing prediction' nếu llm_test.jsonl vẫn còn id đó)")


if __name__ == "__main__":
    main()
