#!/usr/bin/env python3
"""Grade harness Q/A predictions against the frozen 1k LLM benchmark with an independent judge.

Predictions are JSONL records with ``id`` and one of ``answer``, ``text`` or ``response``.
The judge endpoint is OpenAI-chat-completions compatible and must use a model other than the
model under test. This intentionally evaluates answers only: no tool-call or trace metric.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import datetime as dt
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ENTITY_RE = re.compile(r"\b(?:CVE-\d{4}-\d{4,7}|CWE-\d+)\b", re.IGNORECASE)


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.open(encoding="utf-8") if line.strip()]


def answer_of(row: dict) -> str:
    for key in ("answer", "text", "response"):
        value = row.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    raise ValueError(f"prediction {row.get('id')!r} needs non-empty answer, text, or response")


def parse_judge_json(content: str) -> dict:
    match = re.search(r"\{.*\}", content, re.DOTALL)
    if not match:
        raise ValueError("judge response did not contain JSON")
    value = json.loads(match.group(0))
    correctness = float(value["correctness"])
    relevance = float(value["relevance"])
    if not 0 <= correctness <= 1 or not 0 <= relevance <= 1:
        raise ValueError("judge scores must be between 0 and 1")
    return {
        "correctness": correctness,
        "relevance": relevance,
        "hallucination": bool(value["hallucination"]),
        "reason": str(value.get("reason", "")).strip(),
    }


def judge_one(item: dict, *, url: str, model: str, api_key: str | None, retries: int) -> dict:
    prompt = f"""You are an independent evaluator for a Vietnamese cybersecurity Q/A benchmark.
Judge the CANDIDATE answer using the QUESTION and GOLD ANSWER. The candidate may use different
wording; do not reward copying. Do not require it to repeat every reference detail.

correctness: factual correctness and coverage of the core requested answer (0.0 to 1.0).
relevance: directness and relevance to the question (0.0 to 1.0).
hallucination: true only if it makes a material unsupported or contradicted factual claim that could mislead.

Return exactly one JSON object, with no Markdown:
{{"correctness": 0.0, "relevance": 0.0, "hallucination": false, "reason": "brief Vietnamese reason"}}

QUESTION:
{item['question']}

GOLD ANSWER:
{item['gold_answer']}

CANDIDATE ANSWER:
{item['candidate_answer']}"""
    body = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0,
        # Verified live (2026-09-18, OpenRouter + deepseek/deepseek-v4.1-flash): a reasoning-first
        # judge model spends part of its completion budget on hidden reasoning before emitting
        # visible content; `reasoning.exclude` hides those tokens from the response but does NOT
        # stop the model from spending them, so at the original 220 the real (long) judge prompt
        # here still exhausted the budget on reasoning 14/15 times, returning content=None
        # (finish_reason="length") -> TypeError in parse_judge_json. 1024 leaves enough headroom
        # for reasoning + the short required JSON; cost is still trivial (fractions of a cent).
        "max_tokens": 1024,
        # Hides reasoning tokens from the response (irrelevant for a grading task) — confirmed to
        # work (finish_reason="stop", real content) and ignored harmlessly by providers/models
        # that don't support reasoning tokens at all.
        "reasoning": {"exclude": True},
    }
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    for attempt in range(retries + 1):
        try:
            request = urllib.request.Request(url, data=json.dumps(body).encode(), headers=headers)
            with urllib.request.urlopen(request, timeout=120) as response:
                payload = json.loads(response.read())
            content = payload["choices"][0]["message"]["content"]
            return parse_judge_json(content)
        except (KeyError, ValueError, TypeError, urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            # TypeError covers content=None (e.g. a reasoning model exhausting max_tokens on
            # hidden reasoning despite `reasoning.exclude` above, or a provider quirk) — degrade
            # to a per-item error record instead of an uncaught crash of the whole judge run.
            if attempt == retries:
                return {"error": repr(exc)[:300]}
            time.sleep(1 + attempt)
    raise AssertionError("unreachable")


def main() -> None:
    root = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser()
    parser.add_argument("--predictions", type=Path, required=True)
    parser.add_argument("--test", type=Path, default=root / "benchmarks/data/llm_qa_harness_v1.jsonl")
    parser.add_argument("--judge-url", required=True, help="OpenAI-compatible .../v1/chat/completions URL")
    parser.add_argument("--judge-model", required=True, help="Independent model used only as evaluator")
    parser.add_argument("--api-key-env", default="OPENAI_API_KEY")
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--retries", type=int, default=2)
    parser.add_argument("--correctness-pass-score", type=float, default=0.75,
                        help="Per-answer judge correctness score required for the accuracy KPI")
    parser.add_argument("--max-samples", type=int, help="Smoke test only; cannot pass the 1k quality gate")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not 0 <= args.correctness_pass_score <= 1:
        parser.error("--correctness-pass-score must be between 0 and 1")

    expected = read_jsonl(args.test)
    if args.max_samples:
        expected = expected[:args.max_samples]
    ids = [row["id"] for row in expected]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate benchmark ids")
    predictions = {row.get("id"): answer_of(row) for row in read_jsonl(args.predictions)}
    missing = [item_id for item_id in ids if item_id not in predictions]
    if missing:
        raise ValueError(f"missing {len(missing)} prediction(s), first: {missing[:3]}")

    endpoint = args.judge_url.rstrip("/")
    if not endpoint.endswith("/chat/completions"):
        endpoint += "/chat/completions"
    if any(not isinstance(row.get("answer"), str) or not row["answer"].strip() for row in expected):
        raise ValueError("benchmark rows need non-empty gold answer")
    items = [{"id": row["id"], "question": row["question"], "gold_answer": row["answer"],
              "candidate_answer": predictions[row["id"]]} for row in expected]
    api_key = os.environ.get(args.api_key_env) or None
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.concurrency) as executor:
        futures = [executor.submit(judge_one, item, url=endpoint, model=args.judge_model,
                                   api_key=api_key, retries=args.retries) for item in items]
        judged = [future.result() for future in futures]

    entity_mentions = entity_grounded = 0
    records = []
    for item, grade in zip(items, judged):
        allowed_entities = {value.upper() for value in ENTITY_RE.findall(item["question"] + "\n" + item["gold_answer"])}
        mentioned_entities = {value.upper() for value in ENTITY_RE.findall(item["candidate_answer"])}
        entity_mentions += len(mentioned_entities)
        entity_grounded += len(mentioned_entities & allowed_entities)
        records.append({
            "id": item["id"],
            "answer": item["candidate_answer"],
            "entity_mentions": sorted(mentioned_entities),
            "ungrounded_entities": sorted(mentioned_entities - allowed_entities),
            **grade,
        })
    successful = [record for record in records if "error" not in record]
    if not successful:
        raise RuntimeError("all judge requests failed; no quality metric was produced")
    metrics = {
        "answer_correctness": sum(record["correctness"] >= args.correctness_pass_score for record in successful) / len(successful),
        "mean_correctness": sum(record["correctness"] for record in successful) / len(successful),
        "answer_relevance": sum(record["relevance"] for record in successful) / len(successful),
        "entity_precision": entity_grounded / entity_mentions if entity_mentions else None,
        "hallucination_rate": sum(bool(record["hallucination"]) for record in successful) / len(successful),
    }
    result = {
        "schema_version": 1,
        "component": "llm",
        "suite_id": "secai-llm-qa-harness-v1",
        "test": str(args.test),
        "predictions": str(args.predictions),
        "judge": {"url": endpoint, "model": args.judge_model, "independent_from_model_under_test": True},
        "evaluated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "examples": len(expected),
        "judge_successful_examples": len(successful),
        "judge_errors": len(records) - len(successful),
        "correctness_pass_score": args.correctness_pass_score,
        "entity_scored_mentions": entity_mentions,
        "metrics": {key: round(value, 6) if value is not None else None for key, value in metrics.items()},
        "records": records,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: result[key] for key in ("examples", "judge_successful_examples", "judge_errors", "metrics")}, ensure_ascii=False, indent=2))
    if result["judge_errors"]:
        print("WARNING: partial judge failure; the fine-tune gate rejects this report.", file=sys.stderr)


if __name__ == "__main__":
    main()
