"""Render separate non-Arabic script calibration images before prompt selection."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
from fontTools.ttLib import TTFont

ROOT = Path(__file__).resolve().parent / "calibration" / "cross_script"
LATIN = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
CJK = "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"
TEXTS = {
    "vi": "Bản vá đã được kiểm tra trên máy chủ.",
    "en": "The security patch is ready for review.",
    "zh": "请检查最新的安全更新。",
    "ja": "安全更新を確認してください。",
    "ko": "최신 보안 업데이트를 확인하세요.",
    "fr": "La mise à jour de sécurité est prête.",
    "de": "Das Sicherheitsupdate ist bereit.",
    "es": "La actualización de seguridad está lista.",
    "ru": "Обновление безопасности готово.",
}


def main() -> None:
    ROOT.mkdir(parents=True, exist_ok=True)
    entries = []
    for lang, expected in TEXTS.items():
        font_path = CJK if lang in {"zh", "ja", "ko"} else LATIN
        cmap = TTFont(font_path, fontNumber=0).getBestCmap()
        missing = {char for char in expected if not char.isspace() and
                   char not in ".,-" and ord(char) not in cmap}
        if missing:
            raise RuntimeError(f"{lang} font lacks glyphs: {missing}")
        font = ImageFont.truetype(font_path, 30)
        image = Image.new("RGB", (1100, 160), "white")
        ImageDraw.Draw(image).text((30, 35), expected, font=font, fill="black",
                                   direction="ltr", anchor="la")
        path = ROOT / f"{lang}.png"
        image.save(path)
        entries.append({"id": f"cal-{lang}", "language": lang, "image": path.name,
                        "text": expected, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
    (ROOT / "manifest.json").write_text(
        json.dumps({"purpose": "cross-script prompt calibration, excluded from acceptance",
                    "provenance": "manually authored text; native review pending", "images": entries},
                   ensure_ascii=False, indent=2) + "\n"
    )


if __name__ == "__main__":
    main()
