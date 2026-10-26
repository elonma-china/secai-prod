#!/usr/bin/env python3
"""Sinh dataset CCU riêng; không ghi đè output, không gọi model hay production.

.venv/bin/python module-map/translate/fixtures/tools/gen_ccu_fixtures.py \
    --converter-container secai-opt-converter
Converter tùy chọn chỉ dùng LibreOffice ở thư mục tạm riêng để tạo DOC và render DOCX.
"""
import argparse
from collections import Counter
import hashlib
import io
import json
from pathlib import Path
import random
import shutil
import subprocess
import tempfile

from PIL import Image, ImageDraw, ImageFilter, ImageFont
from docx import Document
from docx.shared import Cm, Pt
import openpyxl
from pypdf import PdfReader, PdfWriter
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4

ROOT = Path(__file__).resolve().parent.parent / "ccu-expanded"
FONT = "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf"
FORMATS = ("text", "txt", "docx", "doc", "pdf", "xlsx", "png", "jpg", "pdf-scan")
SIZES = ("short", "medium", "long")
LEVELS = {
    "standard": {"text_words": (100, 500, 2000), "image_words": (100, 300, 800),
                 "rows": (50, 300, 1000), "pages": (1, 5, 20)},
    "large": {"text_words": (2000, 5000, 10000), "image_words": (500, 1000, 1500),
              "rows": (1000, 3000, 5000), "pages": (10, 30, 60)},
    "within-limits": {"text_words": (200, 350, 450), "file_words": (2000, 5000, 10000),
                      "image_words": (500, 1000, 1500), "rows": (1000, 3000, 4900), "pages": (10, 30, 60)},
}
PROFILES = (("en", "vi"), ("vi", "fr"), ("fr", "vi"), ("vi", "ja"), ("en", "vi"))
TEXT_PROFILES = (("vi", "fr"), ("fr", "vi"), ("vi", "ja"), ("vi", "de"), ("fr", "vi"))
SENTENCES = {
    "en": [
        "The security team reviews access permissions and records the owner of each service.",
        "Analysts compare network alerts with application logs before opening an incident.",
        "The maintenance window requires a tested backup and a documented recovery procedure.",
        "A vulnerability report describes the affected asset, business impact and remediation steps.",
        "Operations monitor resource usage and verify that queued requests finish within the deadline.",
        "The reviewer checks evidence, assigns a priority and confirms the expected resolution date.",
        "Every change is tested with representative documents before release to the customer.",
        "The inventory includes databases, network devices, endpoints and their responsible teams.",
    ],
    "vi": [
        "Nhóm an toàn thông tin kiểm tra quyền truy cập và ghi nhận người phụ trách từng dịch vụ.",
        "Chuyên viên đối chiếu cảnh báo mạng với nhật ký ứng dụng trước khi mở hồ sơ sự cố.",
        "Đợt bảo trì cần bản sao lưu đã kiểm chứng và quy trình khôi phục được ghi rõ.",
        "Báo cáo lỗ hổng mô tả tài sản bị ảnh hưởng, tác động nghiệp vụ và bước khắc phục.",
        "Nhóm vận hành theo dõi tài nguyên và kiểm tra yêu cầu trong hàng đợi kết thúc đúng hạn.",
        "Người đánh giá kiểm tra bằng chứng, phân mức ưu tiên và xác nhận thời hạn xử lý.",
        "Mỗi thay đổi được kiểm thử bằng tài liệu đại diện trước khi bàn giao cho khách hàng.",
        "Danh mục gồm cơ sở dữ liệu, thiết bị mạng, máy trạm và nhóm chịu trách nhiệm.",
    ],
    "fr": [
        "L'équipe de sécurité vérifie les autorisations et identifie le responsable de chaque service.",
        "Les analystes comparent les alertes réseau avec les journaux avant de déclarer un incident.",
        "La maintenance nécessite une sauvegarde vérifiée et une procédure de restauration documentée.",
        "Le rapport décrit les équipements concernés, les conséquences et les mesures correctives.",
        "Les opérateurs surveillent les ressources et vérifient les délais des demandes en attente.",
        "Le responsable examine les preuves, définit la priorité et confirme la date de résolution.",
        "Chaque modification est testée avec des documents représentatifs avant la livraison.",
        "L'inventaire comprend les bases de données, les postes de travail et les équipes responsables.",
    ],
}


def literals(sample_id):
    digest = hashlib.sha256(sample_id.encode()).hexdigest()
    n = int(digest[:6], 16)
    return [f"svc_ccu_{digest[:8]}", f"Pw{digest[8:16]}!0&*()_+",
            f"https://{digest[:8]}.example.org/audit?id={n}&mode=test",
            f"192.0.2.{n % 250 + 1}:{8000 + n % 1000}",
            f"CVE-2024-{1000 + n % 9000}", f"audit.{digest[:8]}@example.org",
            f"sk-test-{digest[16:32]}"]


def text_content(sample_id, lang, count, include_literals=True, paragraph_ids=True):
    """Câu đủ nghĩa và mã hồ sơ riêng; phần Keywords giúp đạt đúng số từ."""
    rng = random.Random(sample_id)
    info = "\n".join(f"{label}: {value}" for label, value in zip(
        ("Account", "Password", "URL", "IP", "CVE", "Email", "Key"), literals(sample_id))) if include_literals else ""
    lines = [f"SecAI {sample_id}"]
    budget = count - len((lines[0] + " " + info).split())
    index = 0
    while budget > 2:
        sentence = rng.choice(SENTENCES[lang])
        if paragraph_ids:
            sentence = f"[{sample_id}-{index:04d}] {sentence}"
        used = len(sentence.split())
        if used > budget - 1:
            break
        lines.append(sentence)
        budget -= used
        index += 1
    if budget:
        lines.append(" ".join(["Keywords:"] + [f"audit{index + i}" for i in range(budget - 1)]))
    if info:
        lines.append(info)
    text = "\n".join(lines) + "\n"
    assert len(text.split()) == count
    return text


def wrap(text, width, measure):
    lines = []
    for paragraph in text.splitlines():
        line = ""
        for word in paragraph.split():
            trial = f"{line} {word}".strip()
            if line and measure(trial) > width:
                lines.append(line)
                line = word
            else:
                line = trial
        lines.append(line)
    return lines


def draw_page(text, font_size=22, grow=False):
    width = 1640 if grow else 1240
    img = Image.new("RGB", (width, 1754), "white")
    draw = ImageDraw.Draw(img)
    while font_size >= 14:
        font = ImageFont.truetype(FONT, font_size)
        lines = wrap(text, width - 100, lambda value: draw.textlength(value, font=font))
        step = font_size + 7
        if grow:
            height = max(1754, 100 + len(lines) * step)
            if height > 10000 or width * height > 20_000_000:
                raise ValueError("Ảnh vượt 10.000px hoặc 20 megapixel; không cắt bớt chữ")
            img = Image.new("RGB", (width, height), "white")
            draw = ImageDraw.Draw(img)
            break
        if 50 + len(lines) * step <= 1704:
            break
        font_size -= 1
    else:
        raise ValueError("Trang ảnh không đủ chỗ dù dùng font 14; không cắt bớt chữ")
    for index, line in enumerate(lines):
        draw.text((50, 50 + index * step), line, font=font, fill="#111111")
    img.info["font_size"] = font_size
    return img


def pdf_text(path, pages):
    pdfmetrics.registerFont(TTFont("CcuNoto", FONT))
    pdf = canvas.Canvas(str(path), pagesize=A4, invariant=1)
    for text in pages:
        lines = wrap(text, A4[0] - 90, lambda value: pdfmetrics.stringWidth(value, "CcuNoto", 10))
        if len(lines) * 14 > A4[1] - 90:
            raise ValueError("Trang PDF tràn chữ")
        pdf.setFont("CcuNoto", 10)
        for i, line in enumerate(lines):
            pdf.drawString(45, A4[1] - 50 - i * 14, line)
        pdf.showPage()
    pdf.save()


def docx_document(path, pages):
    doc = Document()
    section = doc.sections[0]
    section.page_width, section.page_height = Cm(21), Cm(29.7)
    section.top_margin = section.bottom_margin = Cm(1.5)
    style = doc.styles["Normal"]
    style.font.name, style.font.size = "DejaVu Sans", Pt(10)
    style.paragraph_format.space_after = Pt(2)
    gold_pages = []
    for index, text in enumerate(pages):
        if index:
            doc.add_page_break()
        doc.add_heading(f"SecAI - {index + 1}", level=1)
        for line in text.splitlines():
            doc.add_paragraph(line)
        table = doc.add_table(rows=1, cols=3)
        table.style = "Table Grid"
        for cell, value in zip(table.rows[0].cells, ("Asset", "Owner", "Priority")):
            cell.text = value
        for j in range(2):
            for cell, value in zip(table.add_row().cells, (f"node-{index}-{j}", "SOC", str(j + 1))):
                cell.text = value
        gold_pages.append(f"SecAI - {index + 1}\n{text}\nAsset | Owner | Priority\n" +
                          "\n".join(f"node-{index}-{j} | SOC | {j + 1}" for j in range(2)))
    doc.save(path)
    return "\n\f\n".join(gold_pages) + "\n"


def spreadsheet(path, sample_id, lang, rows):
    book = openpyxl.Workbook()
    sheet = book.active
    sheet.title = "Data"
    sheet.append(["Asset", "Description", "Owner", "Quantity"])
    all_text = []
    for i in range(rows):
        description = f"{sample_id}-{i:04d}: {SENTENCES[lang][i % len(SENTENCES[lang])]}"
        values = [f"node-{i:04d}", description, f"team-{i % 11}", i + 1]
        sheet.append(values)
        all_text.append(" | ".join(map(str, values)))
    summary = book.create_sheet("Summary")
    summary["A1"], summary["B1"] = "Total", f"=SUM(Data!D2:D{rows + 1})"
    info = book.create_sheet("Info")
    for label, value in zip(("Account", "Password", "URL", "IP", "CVE", "Email", "Key"), literals(sample_id)):
        info.append([label, value])
        all_text.append(f"{label}: {value}")
    book.save(path)
    return "\n".join(all_text) + "\n"


def convert_document(source, suffix, container=None):
    """Profile/thư mục riêng, không đổi cấu hình converter hay container."""
    with tempfile.TemporaryDirectory(prefix="ccu-convert-") as temp:
        work = Path(temp)
        if container:
            script = """import pathlib, subprocess, sys, tempfile
with tempfile.TemporaryDirectory(prefix='ccu-fixture-') as temp:
    work = pathlib.Path(temp)
    source = work / ('input' + sys.argv[2])
    source.write_bytes(sys.stdin.buffer.read())
    subprocess.run(['soffice', '-env:UserInstallation=' + (work / 'profile').as_uri(), '--headless',
        '--convert-to', sys.argv[1], '--outdir', str(work), str(source)],
        check=True, stdout=subprocess.DEVNULL, stderr=sys.stderr, timeout=90)
    sys.stdout.buffer.write(source.with_suffix('.' + sys.argv[1]).read_bytes())
"""
            return subprocess.run(["docker", "exec", "-i", container, "python3", "-c", script, suffix, source.suffix],
                                  input=source.read_bytes(), capture_output=True, check=True, timeout=100).stdout
        soffice = shutil.which("soffice") or shutil.which("libreoffice")
        if not soffice:
            raise RuntimeError("Cần LibreOffice hoặc --converter-container để tạo DOC thật")
        src = work / f"input{source.suffix}"
        src.write_bytes(source.read_bytes())
        subprocess.run([soffice, f"-env:UserInstallation={(work / 'profile').as_uri()}", "--headless",
                        "--convert-to", suffix, "--outdir", str(work), str(src)],
                       capture_output=True, check=True, timeout=90)
        return src.with_suffix(f".{suffix}").read_bytes()


def generate(out, formats, sizes, copies, container=None, difficult=True, profile="standard"):
    if out.exists() and any(out.iterdir()):
        raise ValueError(f"Không ghi đè thư mục đã có dữ liệu: {out}")
    out.mkdir(parents=True, exist_ok=True)
    (out / "gold").mkdir()
    items, blocked = [], []
    levels = LEVELS[profile]
    converter = bool(container or shutil.which("soffice") or shutil.which("libreoffice"))
    for fmt in formats:
        (out / fmt).mkdir(exist_ok=True)
        for size in sizes:
            level = SIZES.index(size)
            for copy in range(copies):
                profiles = TEXT_PROFILES if fmt == "text" and profile == "within-limits" else PROFILES
                source, target = profiles[copy % len(profiles)]
                prefix = f"{fmt}-{profile}" if profile != "standard" else fmt
                sid = f"{prefix}-{size}-{source}-{target}-{copy + 1:02d}"
                if fmt == "doc" and not converter:
                    blocked.append({"id": sid, "format": fmt, "size_class": size,
                                    "reason": "Thiếu LibreOffice; dùng --converter-container để tạo DOC thật"})
                    continue
                suffix = "pdf" if fmt == "pdf-scan" else "txt" if fmt == "text" else fmt
                path = out / fmt / f"{sid}.{suffix}"
                meta = {"id": sid, "format": fmt, "size_class": size, "source_lang": source,
                        "target_lang": target, "lang": source, "needs_ocr": fmt in ("png", "jpg", "pdf-scan"),
                        "group": "text" if fmt == "text" else "images" if fmt in ("png", "jpg") else "scan" if fmt == "pdf-scan" else "files",
                        "route": "/ai/v3/translate" if fmt == "text" else "/ai/v2/translate/images" if fmt in ("png", "jpg") else "/ai/v2/translate/files",
                        "pages": None, "preserve_literals": literals(sid), "synthetic": True,
                        "profile": profile, "benchmark_role": "load"}
                if fmt in ("text", "txt"):
                    word_counts = levels.get("file_words", levels["text_words"]) if fmt == "txt" else levels["text_words"]
                    text = text_content(sid, source, word_counts[level],
                                        paragraph_ids=not (fmt == "text" and profile == "within-limits"))
                    path.write_text(text, encoding="utf-8")
                    if fmt == "text":
                        meta.update(requires_capability_check=True, text_char_limit_reference=24000,
                                    limit_notes=["TEXT_CHAR_LIMIT"] if len(text) > 24000 else [])
                        if meta["limit_notes"]:
                            meta["benchmark_role"] = "boundary"
                elif fmt == "xlsx":
                    rows = levels["rows"][level]
                    text = spreadsheet(path, sid, source, rows)
                    meta.update(rows=rows, sheets=3, cells=(rows + 1) * 4 + 16)
                elif fmt in ("png", "jpg"):
                    text = text_content(sid, source, levels["image_words"][level])
                    img = draw_page(text, (28, 26, 24)[level] if profile != "standard" else (28, 24, 20)[level],
                                    grow=profile != "standard")
                    img.save(path, quality=85) if fmt == "jpg" else img.save(path, optimize=True)
                    meta.update(pages=1, pages_basis="one_uploaded_image", pixels=[img.width, img.height],
                                image_type="clean-render", font_size_px=img.info["font_size"])
                else:
                    n = levels["pages"][level]
                    pages = [text_content(f"{sid}-p{i + 1:02d}", source, 220, False) for i in range(n)]
                    pages[-1] += "\n".join(f"{label}: {value}" for label, value in zip(
                        ("Account", "Password", "URL", "IP", "CVE", "Email", "Key"), meta["preserve_literals"])) + "\n"
                    text = "\n\f\n".join(pages)
                    meta["pages_target"] = n
                    if fmt == "pdf":
                        pdf_text(path, pages)
                    elif fmt == "pdf-scan":
                        images = [draw_page(page, 24) for page in pages]
                        images[0].save(path, "PDF", resolution=150, save_all=True, append_images=images[1:])
                        meta.update(text_layer=False, dpi=150, image_type="clean-render")
                    else:
                        docx_path = path if fmt == "docx" else path.with_suffix(".docx")
                        text = docx_document(docx_path, pages)
                        if fmt == "doc":
                            path.write_bytes(convert_document(docx_path, "doc", container))
                            if not path.read_bytes().startswith(bytes.fromhex("d0cf11e0a1b11ae1")):
                                raise ValueError("Converter không tạo DOC nhị phân OLE hợp lệ")
                        if converter:
                            pdf_data = convert_document(path, "pdf", container)
                            meta.update(pages=len(PdfReader(io.BytesIO(pdf_data)).pages), pages_basis="LibreOffice_render")
                            render = out / "rendered"
                            render.mkdir(exist_ok=True)
                            (render / f"{sid}.pdf").write_bytes(pdf_data)
                            meta["render_path"] = f"rendered/{sid}.pdf"
                        if fmt == "doc":
                            docx_path.unlink()
                    if fmt in ("pdf", "pdf-scan"):
                        meta.update(pages=len(PdfReader(path).pages), pages_basis="PDF_page_tree")
                    if meta["pages"] is not None and meta["pages"] != n:
                        raise ValueError(f"{sid}: số trang thực {meta['pages']} khác mục tiêu {n}")
                gold = out / "gold" / f"{sid}.txt"
                gold.write_text(text, encoding="utf-8")
                data = path.read_bytes()
                meta.update(path=str(path.relative_to(out)), gold_path=str(gold.relative_to(out)),
                            bytes=len(data), sha256=hashlib.sha256(data).hexdigest(), chars=len(text), words=len(text.split()))
                items.append(meta)
                print(f"{sid}: {meta['bytes']} bytes, {meta['words']} words, pages={meta['pages']}", flush=True)
    if difficult:
        for original in [item for item in items if item["format"] == "png" and item["size_class"] == "medium"]:
            for variant in ("blur", "skew", "dark"):
                sid = f"{original['id']}-{variant}"
                img = Image.open(out / original["path"])
                if variant == "blur":
                    img = img.filter(ImageFilter.GaussianBlur(1.0))
                elif variant == "skew":
                    img = img.rotate(2, expand=True, fillcolor="white")
                else:
                    from PIL import ImageOps
                    img = ImageOps.invert(img.convert("RGB"))
                path = out / "png" / f"{sid}.png"
                img.save(path, optimize=True)
                meta = dict(original, id=sid, path=str(path.relative_to(out)),
                            bytes=path.stat().st_size, sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                            group="difficult", base_group="images", variant=variant,
                            pixels=[img.width, img.height], image_type="synthetic-degraded")
                items.append(meta)
        text_pdfs = [item for item in items if item["format"] == "pdf" and item["size_class"] == "medium"]
        scan_pdfs = [item for item in items if item["format"] == "pdf-scan" and item["size_class"] == "medium"]
        for index, (plain, scan) in enumerate(zip(text_pdfs, scan_pdfs), 1):
            prefix = f"pdf-mixed-{profile}" if profile != "standard" else "pdf-mixed"
            sid = f"{prefix}-medium-{plain['source_lang']}-{plain['target_lang']}-{index:02d}"
            plain_pages, scan_pages = PdfReader(out / plain["path"]).pages, PdfReader(out / scan["path"]).pages
            writer, gold_pages = PdfWriter(), []
            plain_gold = (out / plain["gold_path"]).read_text().split("\n\f\n")
            scan_gold = (out / scan["gold_path"]).read_text().split("\n\f\n")
            n = min(len(plain_pages), len(scan_pages))
            for page_index in range(n):
                scanned = page_index % 2 == 0
                writer.add_page((scan_pages if scanned else plain_pages)[page_index])
                gold_pages.append((scan_gold if scanned else plain_gold)[page_index])
            directory = out / "pdf-mixed"
            directory.mkdir(exist_ok=True)
            path = directory / f"{sid}.pdf"
            with path.open("wb") as handle:
                writer.write(handle)
            gold = out / "gold" / f"{sid}.txt"
            text = "\n\f\n".join(gold_pages)
            gold.write_text(text, encoding="utf-8")
            items.append(dict(scan, id=sid, format="pdf-mixed", path=str(path.relative_to(out)),
                              gold_path=str(gold.relative_to(out)), group="difficult", base_group="scan",
                              variant="mixed-text-scan", bytes=path.stat().st_size,
                              sha256=hashlib.sha256(path.read_bytes()).hexdigest(), words=len(text.split()),
                              chars=len(text), image_type="mixed", text_layer="partial",
                              preserve_literals=(scan if (n - 1) % 2 == 0 else plain)["preserve_literals"],
                              text_pages=n // 2, scan_pages=(n + 1) // 2))
    manifest = {"version": 1, "profile": profile, "levels": levels,
                "generator": "tools/gen_ccu_fixtures.py", "path_base": "manifest_directory",
                "note": "Dữ liệu tổng hợp; gold là chữ nguồn, chưa phải bản dịch chuẩn. Không có số CCU hoặc chất lượng dịch từ bộ dữ liệu này.",
                "count": len(items), "blocked": blocked, "items": items}
    (out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"count": len(items), "formats": dict(Counter(i["format"] for i in items)),
                      "blocked": len(blocked), "MB": round(sum(i["bytes"] for i in items) / 1e6, 2)}, ensure_ascii=False), flush=True)
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--profile", choices=LEVELS, default="standard")
    parser.add_argument("--formats", default=",".join(FORMATS))
    parser.add_argument("--sizes", default=",".join(SIZES))
    parser.add_argument("--copies", type=int, default=5)
    parser.add_argument("--converter-container")
    parser.add_argument("--no-difficult", action="store_true")
    args = parser.parse_args()
    if args.out is None:
        args.out = ROOT.with_name(f"ccu-{args.profile}") if args.profile == "within-limits" else ROOT.with_name("ccu-expanded-large") if args.profile == "large" else ROOT
    formats, sizes = args.formats.split(","), args.sizes.split(",")
    if args.copies < 1 or len(formats) != len(set(formats)) or len(sizes) != len(set(sizes)) or set(formats) - set(FORMATS) or set(sizes) - set(SIZES):
        parser.error("formats/sizes phải hợp lệ, không trùng; copies phải > 0")
    try:
        generate(args.out, formats, sizes, args.copies, args.converter_container, not args.no_difficult, args.profile)
    except (ValueError, RuntimeError) as exc:
        parser.exit(1, f"{exc}\n")
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or b"").decode(errors="replace")
        parser.exit(1, f"Converter thất bại (exit {exc.returncode}): {detail}\n")


if __name__ == "__main__":
    main()
