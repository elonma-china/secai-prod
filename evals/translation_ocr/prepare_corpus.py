"""Generate immutable normalized images from the frozen source manifest."""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import shutil
from pathlib import Path

from secai_gateway.translation.ocr_preprocess import preprocess_image


async def prepare(source_manifest: Path, output_dir: Path) -> None:
    source = json.loads(source_manifest.read_text())
    output_images = output_dir / "images"
    output_images.mkdir(parents=True, exist_ok=True)
    for item in source["images"]:
        original = source_manifest.parent / item["image"]
        digest = hashlib.sha256(original.read_bytes()).hexdigest()
        if digest != item["sha256"]:
            raise ValueError("Source image checksum mismatch")
        prepared = await preprocess_image(original, work_dir=output_dir / "work" / item["id"])
        target = output_images / (item["id"] + prepared.path.suffix)
        shutil.copyfile(prepared.path, target)
        item["source_sha256"] = item["sha256"]
        item["image"] = str(target.relative_to(output_dir))
        item["sha256"] = hashlib.sha256(target.read_bytes()).hexdigest()
    source["prepared_from_sha256"] = hashlib.sha256(source_manifest.read_bytes()).hexdigest()
    (output_dir / "manifest.json").write_text(json.dumps(source, ensure_ascii=False, indent=2) + "\n")
    shutil.rmtree(output_dir / "work")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("source_manifest", type=Path)
    parser.add_argument("output_dir", type=Path)
    args = parser.parse_args()
    asyncio.run(prepare(args.source_manifest, args.output_dir))


if __name__ == "__main__":
    main()
