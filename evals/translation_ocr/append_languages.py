"""Append eight script-aware language sets to the original frozen 30 images."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont, features
from fontTools.ttLib import TTFont

from build_fixtures import ROOT, _annotations

FONTS = {
    "zh": "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "ja": "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "ko": "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "ar": "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
}
LATIN = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
TEXT = {
    "zh": ["系统安全检查已经完成。", "服务器正在正常运行。", "請確認網路連線正常。", "漏洞 CVE-2024-3011 位於 192.0.2.11。"],
    "ja": ["システムの安全確認が完了しました。", "サーバーは正常に動作しています。", "ネットワーク接続を確認してください。", "脆弱性 CVE-2024-3012 は 192.0.2.12 にあります。"],
    "ko": ["시스템 보안 점검이 완료되었습니다.", "서버가 정상적으로 작동하고 있습니다.", "네트워크 연결을 확인하세요.", "취약점 CVE-2024-3013 은 192.0.2.13 에 있습니다."],
    "fr": ["Le contrôle de sécurité est terminé.", "Le serveur fonctionne normalement.", "Veuillez vérifier la connexion réseau.", "La faille CVE-2024-3014 concerne 192.0.2.14."],
    "de": ["Die Sicherheitsprüfung ist abgeschlossen.", "Der Server funktioniert normal.", "Bitte prüfen Sie die Netzwerkverbindung.", "Die Schwachstelle CVE-2024-3015 betrifft 192.0.2.15."],
    "es": ["La revisión de seguridad ha terminado.", "El servidor funciona con normalidad.", "Compruebe la conexión de red.", "La vulnerabilidad CVE-2024-3016 afecta a 192.0.2.16."],
    "ru": ["Проверка безопасности системы завершена.", "Сервер работает нормально.", "Проверьте сетевое подключение.", "Уязвимость CVE-2024-3017 затрагивает 192.0.2.17."],
    "ar": ["اكتمل فحص أمان النظام.", "يعمل الخادم بشكل طبيعي.", "يرجى التحقق من اتصال الشبكة.", "الثغرة CVE-2024-3018 تخص 192.0.2.18."],
}


def main() -> None:
    if not features.check("raqm"):
        raise RuntimeError("Pillow libraqm required for shaped Arabic/bidi rendering")
    manifest_path = ROOT / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    if len(manifest["images"]) != 30:
        raise RuntimeError("Original 30-image corpus must be unchanged before append")
    for lang, texts in TEXT.items():
        font_path = FONTS.get(lang, LATIN)
        font = ImageFont.truetype(font_path, 30)
        cmap = TTFont(font_path, fontNumber=0).getBestCmap()
        missing = {ch for text in texts for ch in text if not ch.isspace() and
                   ord(ch) not in cmap and ch not in ".-0123456789"}
        if missing:
            raise RuntimeError(f"Missing {lang} glyphs: {missing}")
        for variant, text in enumerate(texts):
            index = len(manifest["images"]) + 1
            image = Image.new("RGB", (1100, 200), "white")
            draw = ImageDraw.Draw(image)
            direction = "rtl" if lang == "ar" else "ltr"
            x = 1060 if lang == "ar" else 30
            anchor = "ra" if lang == "ar" else "la"
            draw.text((x, 45), text, font=font, fill=(10, 10, 10) if variant < 3 else (40, 40, 40),
                      direction=direction, anchor=anchor)
            if variant == 2:
                image = image.rotate(2, expand=True, fillcolor="white")
            if variant == 3:
                image = image.filter(ImageFilter.GaussianBlur(0.45))
            name = f"{index:02d}.png"
            path = ROOT / "images" / name
            image.save(path)
            manifest["images"].append({"id": f"a{index:02d}", "image": f"images/{name}",
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "category": ("clear", "clear", "skew", "technical")[variant],
                "language": lang, "technical": variant == 3, "text": text,
                "tokens": _annotations(text), "provenance": "manually authored source; render by Pillow/RAQM; native review pending"})
    manifest["version"] = 2
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")


if __name__ == "__main__":
    main()
