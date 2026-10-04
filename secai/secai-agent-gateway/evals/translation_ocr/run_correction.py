"""Replay raw OCR rows through the live isolated correction profile.

Requires provider settings in the environment. Use synthetic fixtures only.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
from pathlib import Path

from secai_gateway.agent.runner import LinchRunner
from secai_gateway.translation.ocr_correction import correct_ocr
from secai_gateway.translation.types import DocumentBlock, OcrResult

logging.getLogger("httpx").setLevel(logging.WARNING)


class Context:
    def __init__(self, runner: LinchRunner):
        self.runner = runner

    async def complete(self, profile: str, prompt: str):
        output, metadata = await self.runner.complete(profile, prompt, None)
        if metadata.get("tool_registry") != []:
            raise RuntimeError("Correction profile unexpectedly exposed tools")
        return output, metadata


async def replay(source: Path, destination: Path, runner: LinchRunner) -> None:
    with destination.open("w") as sink:
        for line in source.read_text().splitlines():
            row = json.loads(line)
            raw = row["raw_text"]
            if row.get("error") or not raw:
                row["corrected_text"] = raw
                row["correction_warning_codes"] = []
                row["needs_review"] = bool(row.get("error"))
            else:
                block = DocumentBlock(id="b000001", kind="ocr_text", order=0,
                                      text=raw, metadata={})
                corrected = await correct_ocr(OcrResult(blocks=(block,), raw_text=raw),
                                              context=Context(runner))
                row["corrected_text"] = corrected.corrected_text
                row["correction_warning_codes"] = [w.code for w in corrected.warnings]
                row["needs_review"] = corrected.needs_review
                row["edits"] = [vars(edit) for edit in corrected.edits]
            sink.write(json.dumps(row, ensure_ascii=False) + "\n")
            sink.flush()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("raw_dir", type=Path)
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("--gateway-db", type=Path, required=True)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    runner = LinchRunner(args.gateway_db)
    for source in sorted(args.raw_dir.glob("round-*.jsonl")):
        destination = args.output_dir / source.name
        asyncio.run(replay(source, destination, runner))
        print("complete", source.name, flush=True)


if __name__ == "__main__":
    main()
