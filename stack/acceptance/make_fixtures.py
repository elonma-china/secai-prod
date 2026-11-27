#!/usr/bin/env python3
"""Sinh fixture dịch tệp: fixtures/sample-en.docx (1 trang) và fixtures/sample-en.png (ảnh chữ, cần OCR).
Cần python-docx và Pillow:  uv run --with python-docx --with pillow python3 make_fixtures.py"""
from pathlib import Path

from docx import Document
from PIL import Image, ImageDraw, ImageFont

OUT = Path(__file__).parent / "fixtures"
OUT.mkdir(exist_ok=True)

PARAS = [
    "Security Advisory: Remote Code Execution in Apache Log4j2",
    "A remote code execution vulnerability in Apache Log4j2 allows an unauthenticated attacker to run arbitrary code on the server by sending a crafted log message.",
    "Affected versions: Apache Log4j2 2.0-beta9 through 2.14.1. The vulnerability is tracked as CVE-2021-44228 and has a CVSS score of 10.0.",
    "Mitigation: upgrade to Log4j2 2.17.1 or later, remove the JndiLookup class from the classpath, and restrict outbound network access from application servers.",
    "Detection: search application logs for suspicious JNDI strings and monitor for unexpected outbound LDAP or RMI connections.",
]

doc = Document()
doc.add_heading(PARAS[0], level=1)
for t in PARAS[1:]:
    doc.add_paragraph(t)
doc.save(OUT / "sample-en.docx")

def font(size):
    for f in ("DejaVuSans.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "LiberationSans-Regular.ttf"):
        try:
            return ImageFont.truetype(f, size)
        except OSError:
            pass
    return ImageFont.load_default(size)

lines = [
    "Security Advisory",
    "A remote code execution vulnerability in Apache Log4j2",
    "allows an unauthenticated attacker to run arbitrary code",
    "on the server. Upgrade to version 2.17.1 or later.",
]
img = Image.new("RGB", (1250, 330), "white")
d = ImageDraw.Draw(img)
for i, l in enumerate(lines):
    d.text((30, 25 + i * 75), l, fill="black", font=font(38 if i else 46))
img.save(OUT / "sample-en.png")
print("ok", sorted(p.name for p in OUT.iterdir()))
