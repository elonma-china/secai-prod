#!/usr/bin/env python3
"""Sinh answers qua FULL AGENT secAI (gateway /ai/chat) — đúng luồng Phase 0 của kpi_bench/bench.sh
(run_flow_gateway). Mỗi câu hỏi -> POST {base}/ai/chat {message} -> answer + tool_registry.
Ghép với dataset (expected/gold) -> answers.jsonl đúng schema mà llm_kpi.py/faith_judge.py/task_kpi.py cần.

KHÁC phép đo model TRẦN (call_llm_predictions.py): đây đi qua RAG + tool của sản phẩm -> đúng thang
KPI hợp đồng (faithfulness/relevance/entity/hallucination + SAST/exploit/remediation).

Usage:
  gen_agent_answers.py --dataset <dir acceptance_*.jsonl> --base http://localhost:8131 \
    --out answers.jsonl [--workers 6] [--limit N]
"""
import argparse
import json
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path


def load_dataset(ds_dir: Path) -> list[dict]:
    rows = []
    for fp in sorted(ds_dir.glob("acceptance_*.jsonl")):
        for line in fp.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rows.append(json.loads(line))
    return rows


def build_message(s: dict) -> str:
    """Ghép nội dung file đính kèm vào message (SAST cần mã nguồn; /ai/chat text-only, không nhận
    attachment nên inline mã vào text — tương đương ngữ nghĩa để model phân tích)."""
    msg = s["input"]
    for fp in s.get("files") or []:
        try:
            code = Path(fp).read_text(encoding="utf-8", errors="replace")[:8000]
        except Exception:
            continue
        msg += f"\n\n--- Nội dung tệp {Path(fp).name} ---\n```\n{code}\n```"
    return msg


def ask(base: str, message: str, timeout: int) -> tuple[str, list, str]:
    body = json.dumps({"message": message}).encode()
    req = urllib.request.Request(base.rstrip("/") + "/ai/chat", data=body,
                                 headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        d = json.loads(r.read())
    text = d.get("response") or d.get("answer") or d.get("message") or ""
    tools = sorted((d.get("metadata") or {}).get("tool_registry") or [])
    return (text if isinstance(text, str) else str(text)), tools, ""


def build_record(s: dict, text: str, tools: list, err: str) -> dict:
    meta = s.get("metadata") or {}
    return {
        "id": s["id"], "capability": s.get("capability"), "bucket": s.get("bucket"),
        "difficulty": s.get("difficulty"), "question": s["input"],
        "answer": text or "", "tools": tools, "files": s.get("files", []),
        "expected": s.get("expected", {}), "tags": s.get("tags", []),
        "gold": {"cve_id": meta.get("cve_id"), "cwe": meta.get("cwe", []),
                 "severity": meta.get("severity"), "products": meta.get("products", []),
                 "fake_cve": meta.get("fake_cve"), "fake_product": meta.get("fake_product")},
        "error": err or None,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", required=True)
    ap.add_argument("--base", default="http://localhost:8131")
    ap.add_argument("--out", required=True)
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--timeout", type=int, default=180)
    ap.add_argument("--limit", type=int, default=None)
    args = ap.parse_args()

    rows = load_dataset(Path(args.dataset))
    if args.limit:
        rows = rows[:args.limit]

    def one(s):
        try:
            text, tools, err = ask(args.base, build_message(s), args.timeout)
        except Exception as e:  # noqa: BLE001 — 1 câu lỗi không được làm hỏng cả lượt
            text, tools, err = "", [], repr(e)[:300]
        return build_record(s, text, tools, err)

    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        recs = list(ex.map(one, rows))

    out = Path(args.out); out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in recs) + "\n", encoding="utf-8")
    ok = sum(1 for r in recs if r["answer"].strip())
    print(f"[gen_agent_answers] {ok}/{len(recs)} answers -> {out}")
    if ok < len(recs):
        print(f"[gen_agent_answers] CẢNH BÁO: {len(recs)-ok} câu rỗng/lỗi (agent /ai/chat) — xem field error")


if __name__ == "__main__":
    main()
