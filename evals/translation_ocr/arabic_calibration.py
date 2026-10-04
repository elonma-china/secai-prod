"""Render a separate Arabic reading-order calibration set, never acceptance images."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, features

ROOT = Path(__file__).resolve().parent / "calibration" / "arabic"
FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
TEXTS = (
    "عنوان الخادم 203.0.113.7 مرتبط بالثغرة CVE-2023-4911.",
    "تم رصد CVE-2022-22800 على 198.51.100.4.",
    "لا تتأثر الخدمة بالثغرة CVE-2021-44228.",
)


def main() -> None:
    if not features.check("raqm"):
        raise RuntimeError("Arabic shaping and bidi require Pillow libraqm")
    ROOT.mkdir(parents=True, exist_ok=True)
    font = ImageFont.truetype(FONT, 30)
    entries = []
    for number, expected in enumerate(TEXTS, 1):
        image = Image.new("RGB", (1100, 160), "white")
        ImageDraw.Draw(image).text(
            (1060, 35), expected, font=font, direction="rtl", anchor="ra", fill="black"
        )
        path = ROOT / f"{number:02d}.png"
        image.save(path)
        entries.append({"id": f"cal-ar-{number}", "image": path.name,
                        "text": expected, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
    (ROOT / "manifest.json").write_text(
        json.dumps({"purpose": "prompt calibration, excluded from acceptance",
                    "language": "ar", "provenance": "manually authored Arabic; native review pending",
                    "images": entries}, ensure_ascii=False, indent=2) + "\n"
    )


if __name__ == "__main__":
    main()
