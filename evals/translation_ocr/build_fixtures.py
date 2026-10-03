"""Create deterministic text images from manually authored source strings.

The source strings below are the goldens; no OCR/LLM output participates in
their creation. Regeneration is byte-stable with the same Pillow/font versions.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = Path(__file__).resolve().parent
FONT = Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")
FONT_BOLD = Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")

VI = [
    "Báo cáo kiểm tra hệ thống", "Máy chủ đã được cập nhật an toàn", "Kết nối mạng ổn định",
    "Dữ liệu khách hàng được mã hóa", "Quản trị viên xác nhận kết quả", "Tường lửa đã chặn truy cập",
    "Phiên đăng nhập cần xác minh", "Bản vá bảo mật đã triển khai",
]
EN = [
    "Security review completed successfully", "The server remains available today",
    "All backups passed integrity checks", "Incident response follows the runbook",
]


def _annotations(text: str) -> list[dict]:
    matches = []
    for kind, pattern in (
        ("cve", r"\bCVE-\d{4}-\d{4,7}\b"),
        ("ip", r"\b(?:\d{1,3}\.){3}\d{1,3}(?:/\d{1,2})?\b"),
    ):
        matches.extend({"kind": kind, "start": m.start(), "end": m.end(), "text": m.group()}
                       for m in re.finditer(pattern, text))
    return sorted(matches, key=lambda x: x["start"])


def main() -> None:
    images = ROOT / "images"
    images.mkdir(exist_ok=True)
    records = []
    for index in range(30):
        if index < 8:
            category, language, text = "clear_vi", "vi", VI[index]
        elif index < 12:
            category, language, text = "clear_en", "en", EN[index - 8]
        elif index < 20:
            category, language = "technical", "en"
            n = index - 12
            text = "\n".join(
                f"CVE-2024-{1200+n*4+j} 192.0.{n}.{j+1}/24" for j in range(4)
            )
        elif index < 26:
            category, language = "phone", "vi" if index % 2 else "en"
            text = ("Ảnh chụp màn hình nghiêng cần được đọc đầy đủ" if language == "vi"
                    else "The photographed page remains readable")
        else:
            category, language = "low_contrast", "vi" if index % 2 else "en"
            text = ("Văn bản mờ vẫn cần giữ nguyên dấu" if language == "vi"
                    else "Low contrast document text remains intact")
        font = ImageFont.truetype(str(FONT_BOLD if category == "technical" else FONT), 27)
        lines = text.split("\n")
        width = max(800, max(int(ImageDraw.Draw(Image.new("RGB", (1, 1))).textlength(line, font=font))
                             for line in lines) + 80)
        height = max(180, 60 + 50 * len(lines))
        image = Image.new("RGB", (width, height), "white")
        draw = ImageDraw.Draw(image)
        for row, line in enumerate(lines):
            draw.text((35, 25 + 50 * row), line, font=font,
                      fill=(90, 90, 90) if category == "low_contrast" else (10, 10, 10))
        if category == "phone":
            image = image.rotate(2.5 if index % 2 else -2.5, expand=True, fillcolor="white")
        if category == "low_contrast" and index % 2:
            image = image.filter(ImageFilter.GaussianBlur(0.7))
        name = f"{index+1:02d}.png"
        path = images / name
        image.save(path)
        records.append({"id": f"a{index+1:02d}", "image": f"images/{name}",
                        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                        "category": category, "language": language,
                        "technical": category == "technical", "text": text,
                        "tokens": _annotations(text)})
    blank = Image.new("RGB", (800, 180), "white")
    blank.save(images / "blank.png")
    (ROOT / "manifest.json").write_text(json.dumps({"version": 1, "images": records,
        "negatives": [{"image": "images/blank.png", "expected_code": "NO_TEXT_DETECTED"}]},
        ensure_ascii=False, indent=2) + "\n")


if __name__ == "__main__":
    main()
