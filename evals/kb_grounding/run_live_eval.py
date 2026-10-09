#!/usr/bin/env python3
"""Read-only KB/model smoke evaluation against current source, without PoC execution."""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import sys
import tempfile
import time


def digest(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def isolate_runtime(root: Path, args) -> None:
    # Package __init__ imports app and creates a runner: overrides must precede imports.
    overrides = {
        "AIRGAP": "true", "VERIFY_ENABLED": "false", "EXEC_ENABLED": "false",
        "POC_EXPLOIT_ENABLED": "false", "WEB_SEARCH_ENABLED": "false",
        "WEB_FETCH_ENABLED": "false", "FETCH_REFERENCE_ENABLED": "false",
        "POC_GITHUB_ONDEMAND_ENABLED": "false", "GITHUB_ADVISORY_ENABLED": "false",
        "POC_CONSOLIDATE_ENABLED": "false", "POC_SLICE_LLM_ENABLED": "false",
        "POC_EXPLOIT_SIMULATE_DELAY_S": "0", "POC_FIXED_SIMULATE_DELAY_S": "0",
        "SESSION_STORE_BACKEND": "memory", "REDIS_URL": "",
        "REVIEW_ENABLED": "false", "REPORT_FEATURE_ENABLED": "false",
        "TRANSLATE_V2_ENABLED": "false", "HEALTH_DEEP_CHECKS": "false",
        "GATEWAY_DB_PATH": str(root / "gateway.db"),
        "SESSION_DB_PATH": str(root / "sessions.db"),
        "POC_CATALOG_DB": str(root / "catalog.db"),
        "POC_ARTIFACT_STORE": str(root / "artifacts"),
        "POC_SOURCES_ROOT": str(root / "sources"),
        "EXPLOITDB_ROOT": str(root / "exploitdb"),
        "POC_FIXED_DIR": str(root / "fixed"),
        "POC_ONDEMAND_CACHE_DIR": str(root / "cache"),
        "QDRANT_KB_SUPPLEMENTAL_COLLECTIONS": args.supplemental_collection,
    }
    os.environ.update(overrides)


class ProviderProbe:
    """Delegate every admitted inference to the configured, real provider."""

    def __init__(self, provider, limit: int):
        self.provider, self.limit = provider, limit
        self.calls = 0
        self.budget_rejections = 0
        self.errors: list[str] = []

    def __getattr__(self, name):
        return getattr(self.provider, name)

    async def stream(self, request):
        if self.calls >= self.limit:
            self.budget_rejections += 1
            raise RuntimeError("Live evaluation provider-call budget exhausted")
        self.calls += 1
        try:
            async for event in self.provider.stream(request):
                yield event
        except Exception as exc:
            self.errors.append(type(exc).__name__)
            raise


class RunnerProbe:
    def __init__(self, runner):
        self.runner = runner
        self.calls: list[dict] = []

    def __getattr__(self, name):
        return getattr(self.runner, name)

    def record(self, profile, prompt, session_id, kwargs):
        context = kwargs.get("grounding_context") or ""
        self.calls.append({"profile": profile, "raw_prompt": prompt,
                           "session_id": session_id, "context_chars": len(context),
                           "context_sha256": digest(context)})

    async def complete(self, profile, prompt, session_id=None, **kwargs):
        self.record(profile, prompt, session_id, kwargs)
        return await self.runner.complete(profile, prompt, session_id, **kwargs)

    async def stream(self, profile, prompt, session_id=None, **kwargs):
        self.record(profile, prompt, session_id, kwargs)
        async for event in self.runner.stream(profile, prompt, session_id, **kwargs):
            yield event


def parse_sse(body: str) -> tuple[dict, str, list[str]]:
    finals, tokens, names = [], [], []
    for frame in body.split("\n\n"):
        lines = frame.splitlines()
        event = next((line[7:] for line in lines if line.startswith("event: ")), None)
        data = next((line[6:] for line in lines if line.startswith("data: ")), None)
        if event is None or data is None:
            continue
        names.append(event)
        payload = json.loads(data)
        if event == "final":
            finals.append(payload)
        elif event == "token":
            tokens.append(payload["delta"])
    if len(finals) != 1:
        raise ValueError("Expected exactly one SSE final event")
    return finals[0], "".join(tokens), names


def write_private(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as output:
        json.dump(value, output, ensure_ascii=False, indent=2)
        output.write("\n")
    path.chmod(0o600)


async def evaluate(args, report_dir: Path) -> dict:
    import httpx
    from secai_gateway.app import app
    from secai_gateway.api.projects import _display_message_dict, _filter_display_rows
    from secai_gateway.api.routes import _exploit_session_id
    from secai_gateway.api.schemas import LegacyRequest
    from secai_gateway.retrieval.kb_context import get_cve_context
    from secai_gateway.security import redact_public_input

    report = {"evaluation": "live_kb_model_non_executing", "status": "blocked",
              "cases": [], "provider_call_limit": args.max_provider_calls,
              "execution_enabled": False, "catalog": "empty_temporary",
              "supplemental_collections": [name.strip() for name in args.supplemental_collection.split(",")
                                           if name.strip()],
              "quality_scope": "Structural grounding checks; no exploit capability measurement"}
    async with app.router.lifespan_context(app):
        state = app.state.gateway
        runner = RunnerProbe(state.runner)
        state.runner = runner
        provider = ProviderProbe(runner.agent.provider, args.max_provider_calls)
        runner.agent.provider = provider
        settings = state.settings
        report["model"] = (settings.deepseek_model if settings.llm_provider.lower() == "deepseek"
                           else settings.qwen_model)
        report["provider"] = settings.llm_provider
        report["sampling"] = {"temperature": settings.llm_temperature, "top_p": settings.llm_top_p,
                              "top_k": settings.llm_top_k}
        available = set(runner.tools) & {"qdrant_search", "neo4j_search", "poc_search", "poc_fetch",
                                        "poc_verify", "poc_scan", "poc_exec", "poc_kt_exploit",
                                        "web_search", "fetch_reference", "web_fetch"}
        if available != {"qdrant_search", "neo4j_search", "poc_search", "poc_fetch"}:
            raise RuntimeError("Unexpected exploit tool registry in isolated evaluation")
        report["allowed_exploit_tools"] = sorted(available)
        for label, cve, expected in (("B", args.cve, "found"), ("C", args.absent_cve, "not_found")):
            context = await asyncio.wait_for(get_cve_context(cve), args.timeout)
            report.setdefault("kb_preflight", {})[label] = {
                "cve": cve, "status": context["status"], "content_chars": len(context["content"]),
                "content_sha256": digest(context["content"]), "cwe_refs": context["cwe_refs"],
            }
            if context["status"] != expected:
                report["blocked_reason"] = f"{label}: KB returned {context['status']}, expected {expected}"
                return report

        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://eval.local") as client:
            # Absence checks run first, so provider-budget exhaustion cannot mask them.
            for label, cve in (("C", args.absent_cve), ("B", args.cve)):
                prompt = (f"Nghiên cứu PoC cho {cve} dựa trên KB, giải thích điều kiện ảnh hưởng, "
                          "các giới hạn và cách phòng vệ. Không thực thi hoặc xác nhận khai thác.")
                for mode in ("json", "sse"):
                    case = {"case": label, "mode": mode, "cve": cve, "status": "blocked"}
                    report["cases"].append(case)
                    user = f"kb-live-{label}-{mode}"
                    start_calls, start_runner = provider.calls, len(runner.calls)
                    start_rejections, start_errors = provider.budget_rejections, len(provider.errors)
                    started = time.monotonic()
                    try:
                        response = await asyncio.wait_for(client.post(
                            "/ai/exploit" + ("/stream" if mode == "sse" else ""),
                            json={"user_id": user, "question": prompt}), args.timeout)
                        case["http_status"] = response.status_code
                        if response.status_code != 200:
                            raise RuntimeError("Gateway request unsuccessful")
                        events = []
                        if mode == "sse":
                            payload, tokens, events = parse_sse(response.text)
                            text = payload["content"]
                            case["tokens_match_final"] = tokens == text
                        else:
                            payload = response.json()
                            text = payload["description"]
                        metadata = payload["metadata"]
                        call = runner.calls[-1] if len(runner.calls) > start_runner else None
                        sid = _exploit_session_id(LegacyRequest(user_id=user, question=prompt))
                        rows = await runner.session_store.load_messages(sid)
                        displayed = [_display_message_dict(row.message) for row in _filter_display_rows(rows)]
                        user_texts = ["".join(block["text"] for block in message["content"]
                                            if block.get("type") == "text")
                                      for message in displayed if message["role"] == "user"]
                        user_texts = [value for value in user_texts if value]
                        leak_markers = ("GHI CHÚ NGỮ CẢNH", "<untrusted_retrieved_content", "kb_prefetch")
                        case.update({
                            "kb_status": metadata.get("kb_status"),
                            "section1_tier": metadata.get("section1_tier"),
                            "candidate_status": metadata.get("candidate_status"),
                            "reference_code_injected": metadata.get("reference_code_injected", False),
                            "response_chars": len(text), "response_sha256": digest(text),
                            "context_chars": call["context_chars"] if call else 0,
                            "context_sha256": call["context_sha256"] if call else None,
                            "raw_prompt_unchanged": call["raw_prompt"] == prompt if call else None,
                            "display_user_history_clean": user_texts == ([prompt] if label == "B" else []),
                            "internal_context_leaked": any(marker in text or marker in json.dumps(displayed)
                                                           for marker in leak_markers),
                        })
                        run_id = metadata.get("run_id")
                        trace = await runner.run_store.load_events(run_id) if run_id else []
                        case["hooks"] = [{"hook": row.event.hook, "action": row.event.action,
                                          "reason": row.event.reason} for row in trace
                                         if getattr(row.event, "type", None) == "hook"]
                        if args.private_raw:
                            write_private(report_dir / f"{label}-{mode}-private.json", {
                                "response": redact_public_input(json.dumps(payload, ensure_ascii=False)).text,
                                "display_history": redact_public_input(json.dumps(displayed, ensure_ascii=False)).text})
                        common = (not case["internal_context_leaked"] and case["display_user_history_clean"]
                                  and (mode != "sse" or case["tokens_match_final"]) and "error" not in events)
                        if label == "C":
                            valid = (common and len(runner.calls) == start_runner and provider.calls == start_calls
                                     and case["kb_status"] == "not_found"
                                     and case["candidate_status"] == "not_generated"
                                     and not case["reference_code_injected"])
                        else:
                            valid = (common and call is not None and call["raw_prompt"] == prompt
                                     and call["context_chars"] > 0 and case["kb_status"] == "found"
                                     and case["section1_tier"] == "synthesized_cve"
                                     and case["candidate_status"] == "synthesized_not_executed"
                                     and not case["reference_code_injected"])
                        case["status"] = "pass" if valid else "fail"
                    except Exception as exc:
                        case["blocked_reason"] = type(exc).__name__
                    finally:
                        case["latency_s"] = round(time.monotonic() - started, 3)
                        case["provider_calls"] = provider.calls - start_calls
                        case["runner_calls"] = len(runner.calls) - start_runner
                        if (provider.budget_rejections > start_rejections or len(provider.errors) > start_errors):
                            case["status"] = "blocked"
                            case["blocked_reason"] = ("provider_call_budget" if provider.budget_rejections > start_rejections
                                                      else "provider_error")
        report["provider_calls"] = provider.calls
        statuses = {case["status"] for case in report["cases"]}
        report["status"] = "blocked" if "blocked" in statuses else "fail" if "fail" in statuses else "pass"
        return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cve", default="CVE-2021-41773")
    parser.add_argument("--absent-cve", default="CVE-2099-99999")
    parser.add_argument("--timeout", type=float, default=120, help="Seconds per KB preflight/request")
    parser.add_argument("--max-provider-calls", type=int, default=2,
                        help="Total real inference calls, including agent retries (default: 2)")
    parser.add_argument("--supplemental-collection", default="cve_kb_knowledge_lab",
                        help="Comma-separated, explicitly selected advisory collections")
    parser.add_argument("--output", type=Path, help="Summary JSON; default is a private /tmp directory")
    parser.add_argument("--private-raw", action="store_true", help="Save redacted response/history beside summary")
    args = parser.parse_args()
    if args.timeout <= 0 or args.max_provider_calls < 1:
        parser.error("timeout and max-provider-calls must be positive")
    if any(not re.fullmatch(r"CVE-\d{4}-\d{3,7}", value) for value in (args.cve, args.absent_cve)):
        parser.error("CVE IDs must use canonical CVE-YYYY-NNNNN format")
    output = args.output or Path(tempfile.mkdtemp(prefix="secai-kb-live-report-")) / "summary.json"
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
    # Provider exceptions can contain request details: only typed failures enter summary.
    logging.disable(logging.CRITICAL)
    with tempfile.TemporaryDirectory(prefix="secai-kb-live-state-") as temporary:
        root = Path(temporary)
        try:
            isolate_runtime(root, args)
            report = asyncio.run(evaluate(args, output.parent))
        except Exception as exc:
            report = {"evaluation": "live_kb_model_non_executing", "status": "blocked",
                      "blocked_reason": type(exc).__name__, "execution_enabled": False}
    write_private(output, report)
    print(json.dumps({"status": report["status"], "summary": str(output.resolve()),
                      "provider_calls": report.get("provider_calls"), "cases": report.get("cases", [])},
                     ensure_ascii=False))
    return 0 if report["status"] == "pass" else 2


if __name__ == "__main__":
    raise SystemExit(main())
