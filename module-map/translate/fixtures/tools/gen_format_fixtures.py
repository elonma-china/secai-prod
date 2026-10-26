#!/usr/bin/env python3
"""Sinh bộ mẫu đa định dạng (ngắn + dài) cho kiểm thử dịch tệp/ảnh.

Chạy:  /path/venv/bin/python gen_format_fixtures.py
Cần:   python-docx openpyxl reportlab pillow pypdf arabic-reshaper(không bắt buộc)
Nguồn: fixtures/files/<lang>-1000.txt + fixtures/text/<lang>-500(.part2).txt (nội dung kỹ thuật GIẢ)
Ra:    fixtures/formats/{docx,xlsx,pdf,pdf-scan,png,jpg}/ + fixtures/formats/manifest.json
Xác định (không ngẫu nhiên): chạy lại cho ra cùng nội dung.
"""
import json, re, sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
import docx
from docx.shared import Pt
from docx.oxml.ns import qn
from docx.oxml import OxmlElement
import openpyxl
from openpyxl.styles import Alignment, Font
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

FX = Path(__file__).resolve().parent.parent
OUT = FX / "formats"
LANGS = ["vi", "en", "zh-Hans", "ja", "ko", "fr", "de", "es", "ru", "ar"]
NOSANS = "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf"
NASKH = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"  # có cả Ả Rập + Latin (Naskh thiếu Latin -> ô vuông)
CJK = "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"
DROID = "/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf"
CJK_INDEX = {"ja": 0, "ko": 1, "zh-Hans": 2}
NO_SPACE = {"zh-Hans", "ja"}
RTL = {"ar"}
# PDF có lớp chữ: reportlab không nhúng được font CFF (Noto CJK) và không ghép chữ Ả Rập -> bỏ ko, ar.
PDF_TEXT_LANGS = [l for l in LANGS if l not in ("ko", "ar")]

W = {  # nhãn theo ngôn ngữ: section, content, account, password
    "vi": ("Phần", "Nội dung", "Tài khoản", "Mật khẩu"),
    "en": ("Section", "Content", "Account", "Password"),
    "zh-Hans": ("第{n}部分", "内容", "账号", "密码"),
    "ja": ("第{n}節", "内容", "アカウント", "パスワード"),
    "ko": ("제{n}절", "내용", "계정", "비밀번호"),
    "fr": ("Section", "Contenu", "Compte", "Mot de passe"),
    "de": ("Abschnitt", "Inhalt", "Konto", "Passwort"),
    "es": ("Sección", "Contenido", "Cuenta", "Contraseña"),
    "ru": ("Раздел", "Содержание", "Учётная запись", "Пароль"),
    "ar": ("القسم", "المحتوى", "الحساب", "كلمة المرور"),
}
# Dữ liệu GIẢ, dùng cho kiểm tra giữ nguyên thông tin (03 mục 1b)
LIT = {
    "account": "svc_backup_01",
    "password": "P@ss!w0rd#2026$%^&*()_+",
    "url": "https://portal.example.org/login?user=admin&pass=Ab!9#x",
    "ip": "192.168.10.25:8443",
    "cve": "CVE-2024-3094",
    "email": "ops.team@example.org",
    "key": "sk-test-9f8E7d6C5b4A3",
}
PRESERVE = list(LIT.values())


def sect(lang, n):
    w = W[lang][0]
    return w.format(n=n) if "{n}" in w else f"{w} {n}"


def sentences(lang):
    parts = [FX / "files" / f"{lang}-1000.txt", FX / "text" / f"{lang}-500.txt",
             FX / "text" / f"{lang}-500-part2.txt"]
    out = []
    for p in parts:
        if p.exists():
            t = p.read_text(encoding="utf-8").replace("\n", " ")
            out += [s.strip() for s in re.split(r"(?<=[.!?。！？؟])\s+|(?<=[。！？])", t) if s.strip()]
    return out


def paragraphs(sents, per=4):
    return [" ".join(sents[i:i + per]) if True else "" for i in range(0, len(sents), per)]


def join(lang, sents):
    return ("" if lang in NO_SPACE else " ").join(sents)


def lit_lines(lang):
    _, _, acct, pw = W[lang]
    return [f"{acct}: {LIT['account']}", f"{pw}: {LIT['password']}", f"URL: {LIT['url']}",
            f"IP: {LIT['ip']}", f"CVE: {LIT['cve']}", f"Email: {LIT['email']}", f"Key: {LIT['key']}"]


def title(lang, size):
    return f"SecAI-TEST-{lang}-{size}"


MAN = []


def reg(kind, fmt, lang, size, path, struct, words):
    MAN.append({"id": f"{fmt}-{size}-{lang}", "kind": kind, "format": fmt, "lang": lang,
                "size_class": size, "path": str(path.relative_to(FX)), "bytes": path.stat().st_size,
                "approx_words_or_chars": words, "structure": struct,
                "preserve_literals": PRESERVE, "needs_ocr": fmt in ("pdf-scan", "png", "jpg")})


def newdir(name):
    d = OUT / name
    d.mkdir(parents=True, exist_ok=True)
    return d


# ---------------- DOCX ----------------
def set_par(par, lang):
    if lang in RTL:
        ppr = par._p.get_or_add_pPr()
        ppr.append(OxmlElement("w:bidi"))
        for r in par.runs:
            r._r.get_or_add_rPr().append(OxmlElement("w:rtl"))
    for r in par.runs:
        rpr = r._r.get_or_add_rPr()
        rf = rpr.find(qn("w:rFonts"))
        if rf is None:
            rf = OxmlElement("w:rFonts"); rpr.append(rf)
        rf.set(qn("w:eastAsia"), "Noto Sans CJK JP")
        rf.set(qn("w:ascii"), "Noto Sans"); rf.set(qn("w:hAnsi"), "Noto Sans")


def add(doc, lang, text, style=None):
    p = doc.add_paragraph(text, style=style) if style else doc.add_paragraph(text)
    set_par(p, lang)
    return p


def head(doc, lang, text, level):
    h = doc.add_heading(text, level=level)
    set_par(h, lang)


def table(doc, lang, rows):
    t = doc.add_table(rows=len(rows), cols=len(rows[0]))
    t.style = "Table Grid"
    for i, row in enumerate(rows):
        for j, v in enumerate(row):
            t.cell(i, j).text = str(v)
            set_par(t.cell(i, j).paragraphs[0], lang)


def gen_docx(lang, S, P):
    d = newdir("docx")
    for size in ("short", "long"):
        doc = docx.Document()
        head(doc, lang, title(lang, size), 0)
        tables = bullets = headings = 0
        if size == "short":
            head(doc, lang, sect(lang, 1), 1); headings += 1
            add(doc, lang, join(lang, S[0:3]))
            for s in S[3:6]:
                add(doc, lang, s, "List Bullet"); bullets += 1
            table(doc, lang, [["#", W[lang][1], "Status"]] + [[i + 1, S[6 + i], "OPEN"] for i in range(3)]); tables += 1
            words = sum(len(s) for s in S[0:9])
        else:
            n = 0
            for k in range(0, min(len(P) - 1, 12), 2):
                n += 1
                head(doc, lang, sect(lang, n), 1); headings += 1
                add(doc, lang, P[k])
                for s in sentences_of(P[k + 1], lang)[:3]:
                    add(doc, lang, s, "List Bullet"); bullets += 1
                if n in (2, 4):
                    table(doc, lang, [["#", W[lang][1], "Status"]] +
                          [[i + 1, S[(n * 5 + i) % len(S)], "OPEN"] for i in range(4)]); tables += 1
            words = sum(len(p) for p in P[:12])
        head(doc, lang, "Info", 1); headings += 1
        for l in lit_lines(lang):
            add(doc, lang, l)
        p = d / f"{size}-{lang}.docx"
        doc.save(p)
        reg("file", "docx", lang, size, p, {"headings": headings + 1, "bullets": bullets, "tables": tables,
                                            "literal_lines": 7}, words)


def sentences_of(par, lang):
    return [s.strip() for s in re.split(r"(?<=[.!?。！？؟])\s+|(?<=[。！？])", par) if s.strip()]


# ---------------- XLSX ----------------
def gen_xlsx(lang, S):
    d = newdir("xlsx")
    for size in ("short", "long"):
        wb = openpyxl.Workbook()
        sheets = 1 if size == "short" else 3
        rows_n = 8 if size == "short" else 150
        for si in range(sheets):
            ws = wb.active if si == 0 else wb.create_sheet()
            ws.title = f"Data-{si + 1}"
            ws.merge_cells("A1:D1")
            ws["A1"] = title(lang, size) + f"-S{si + 1}"
            ws["A1"].font = Font(bold=True, size=14)
            for j, h in enumerate(["#", W[lang][1], "Status", "Qty"], 1):
                ws.cell(3, j, h).font = Font(bold=True)
            for i in range(rows_n):
                r = 4 + i
                ws.cell(r, 1, i + 1)
                c = ws.cell(r, 2, S[(si * 37 + i) % len(S)])
                c.alignment = Alignment(wrap_text=True, vertical="top")
                ws.cell(r, 3, "OPEN" if i % 3 else "DONE")
                ws.cell(r, 4, (i * 7) % 50 + 1)
            last = 3 + rows_n
            ws.cell(last + 1, 3, "TOTAL"); ws.cell(last + 1, 4, f"=SUM(D4:D{last})")
            for k, l in enumerate(lit_lines(lang)):
                lab, _, val = l.partition(": ")
                ws.cell(last + 3 + k, 2, lab); ws.cell(last + 3 + k, 3, val)
            ws.column_dimensions["B"].width = 80
        p = d / f"{size}-{lang}.xlsx"
        wb.save(p)
        reg("file", "xlsx", lang, size, p, {"sheets": sheets, "rows_per_sheet": rows_n, "merged_title": True,
                                            "formula_cells": sheets, "numeric_cols": ["#", "Qty"],
                                            "literal_rows_per_sheet": 7}, sheets * rows_n)


# ---------------- PDF có lớp chữ ----------------
def gen_pdf(lang, S, P):
    d = newdir("pdf")
    pdfmetrics.registerFont(TTFont("NS", NOSANS))
    pdfmetrics.registerFont(TTFont("DF", DROID))
    cjk = lang in NO_SPACE
    font = "NS"

    def runs(t):
        """Tách theo font: ký tự Latin dùng NS, CJK dùng DF (Droid không có glyph Latin)."""
        out = []
        for ch in t:
            f = "NS" if (not cjk or ord(ch) < 0x2000) else "DF"
            if out and out[-1][0] == f:
                out[-1][1] += ch
            else:
                out.append([f, ch])
        return out

    def width(t, s):
        return sum(pdfmetrics.stringWidth(x, f, s) for f, x in runs(t))

    for size in ("short", "long"):
        p = d / f"{size}-{lang}.pdf"
        c = canvas.Canvas(str(p), pagesize=A4)
        pw, ph = A4
        m, fs, lead = 54, 11, 16
        pages = [1]
        y = [ph - m]

        def newpage():
            c.setFont("NS", 8); c.drawString(pw / 2 - 10, 30, f"p. {pages[0]}")
            c.showPage(); pages[0] += 1; y[0] = ph - m

        def line(t, f=font, s=fs):
            if y[0] < m + 20:
                newpage()
            x = m
            for rf, rt in runs(t):
                c.setFont(rf, s); c.drawString(x, y[0], rt); x += pdfmetrics.stringWidth(rt, rf, s)
            y[0] -= lead if s <= 12 else s + 8

        def wrap(t):
            out, cur = [], ""
            units = list(t) if cjk else t.split(" ")
            sep = "" if cjk else " "
            for u in units:
                trial = (cur + sep + u) if cur else u
                if width(trial, fs) <= pw - 2 * m:
                    cur = trial
                else:
                    out.append(cur); cur = u
            return out + ([cur] if cur else [])

        line(title(lang, size), "NS", 18)
        paras = [join(lang, S[0:3])] if size == "short" else P[:12]
        headings = 1
        for i, t in enumerate(paras, 1):
            if size == "long":
                line(sect(lang, i), font, 13); headings += 1
            for w in wrap(t):
                line(w)
            y[0] -= 6
        line("Info", "NS", 13); headings += 1
        for l in lit_lines(lang):
            line(l)
        c.setFont("NS", 8); c.drawString(pw / 2 - 10, 30, f"p. {pages[0]}")
        c.save()
        reg("file", "pdf", lang, size, p, {"pages": pages[0], "headings": headings, "text_layer": True,
                                           "literal_lines": 7}, sum(len(t) for t in paras))


# ---------------- Ảnh / PDF scan ----------------
def pil_font(lang, px):
    if lang in CJK_INDEX:
        return ImageFont.truetype(CJK, px, index=CJK_INDEX[lang])
    if lang in RTL:
        return ImageFont.truetype(NASKH, px)
    return ImageFont.truetype(NOSANS, px)


def wrap_px(text, font, maxw, lang):
    out, cur = [], ""
    units = list(text) if lang in NO_SPACE else text.split(" ")
    sep = "" if lang in NO_SPACE else " "
    for u in units:
        trial = (cur + sep + u) if cur else u
        if font.getlength(trial) <= maxw:
            cur = trial
        else:
            out.append(cur); cur = u
    return out + ([cur] if cur else [])


def draw_page(lang, W_, H_, blocks, px, lit=True, ttl=None, crop=False):
    """blocks: danh sách đoạn; trả (ảnh, số đoạn đã vẽ hết, hết nguồn?)"""
    img = Image.new("RGB", (W_, H_), (252, 252, 250))
    dr = ImageDraw.Draw(img)
    font = pil_font(lang, px)
    big = pil_font(lang, int(px * 1.5))
    m, y = 70, 60
    rtl = lang in RTL
    lead = int(px * 1.45)
    kw = dict(direction="rtl", language="ar") if rtl else {}

    def put(t, f):
        nonlocal y
        if rtl:
            dr.text((W_ - m, y), t, font=f, fill=(20, 20, 20), anchor="ra", **kw)
        else:
            dr.text((m, y), t, font=f, fill=(20, 20, 20))
        y += lead

    if ttl:
        put(ttl, big); y += 10
    used = 0
    for t in blocks:
        ls = wrap_px(t, font, W_ - 2 * m, lang)
        if y + lead * (len(ls) + 1) > H_ - 60 - (lead * 8 if lit else 0):
            break
        for l in ls:
            put(l, font)
        y += 8; used += 1
    if lit:
        for l in lit_lines(lang):
            put(l, font)
    if crop:
        img = img.crop((0, 0, W_, min(H_, y + 40)))
    return img, used


def gen_images(lang, S, P):
    dpng, djpg, dsc = newdir("png"), newdir("jpg"), newdir("pdf-scan")
    # ảnh ngắn
    for fmt, d, src in (("png", dpng, S[0:3]), ("jpg", djpg, S[3:6])):
        img, used = draw_page(lang, 1200, 2400, [join(lang, src)], 32, ttl=title(lang, "short"), crop=True)
        assert used == 1, f"ảnh ngắn {lang} bị cắt đoạn văn"
        p = d / f"short-{lang}.{fmt}"
        img.save(p, quality=82) if fmt == "jpg" else img.save(p, optimize=True)
        reg("image", fmt, lang, "short", p, {"lines": "title+3 câu+7 dòng literal", "px": [1200, img.height]},
            sum(len(x) for x in src))
    # ảnh dài (A4 150dpi, dày chữ)
    for fmt, d, start in (("png", dpng, 0), ("jpg", djpg, 5)):
        img, used = draw_page(lang, 1240, 1754, P[start:], 22, ttl=title(lang, "long"))
        p = d / f"long-{lang}.{fmt}"
        img.save(p, quality=82) if fmt == "jpg" else img.save(p, optimize=True)
        reg("image", fmt, lang, "long", p, {"paragraphs": used, "px": [1240, 1754], "dense_page": True,
                                            "literal_lines": 7}, sum(len(x) for x in P[start:start + used]))
    # PDF scan: chỉ ảnh, không lớp chữ
    for size, npages in (("short", 1), ("long", 3)):
        pages, k = [], 0
        for pg in range(npages):
            blocks = [join(lang, S[0:3])] if size == "short" else P[k:]
            img, used = draw_page(lang, 1240, 1754, blocks, 26, lit=(pg == npages - 1),
                                  ttl=title(lang, size) if pg == 0 else None)
            pages.append(img); k += used
        p = dsc / f"{size}-{lang}.pdf"
        pages[0].save(p, "PDF", resolution=150, save_all=True, append_images=pages[1:])
        reg("file", "pdf-scan", lang, size, p, {"pages": npages, "text_layer": False, "literal_lines": 7},
            sum(len(x) for x in (P[:k] if size == "long" else S[0:3])))


def gen_xl():
    """Tệp rất lớn (chỉ en): kiểm giới hạn dung lượng/thời gian."""
    lang = "en"
    S = sentences(lang)
    d = newdir("xlsx")
    wb = openpyxl.Workbook(); ws = wb.active; ws.title = "Data-1"
    for j, h in enumerate(["#", W[lang][1], "Status", "Qty"], 1):
        ws.cell(1, j, h)
    for i in range(5000):
        ws.cell(2 + i, 1, i + 1); ws.cell(2 + i, 2, S[i % len(S)])
        ws.cell(2 + i, 3, "OPEN"); ws.cell(2 + i, 4, (i * 7) % 50 + 1)
    p = d / "xl-en.xlsx"; wb.save(p)
    reg("file", "xlsx", lang, "xl", p, {"sheets": 1, "rows_per_sheet": 5000}, 5000)
    img_pages, k = [], 0
    P = paragraphs(S)
    for pg in range(20):
        blocks = [P[(pg * 3 + j) % len(P)] for j in range(8)]
        img, _ = draw_page(lang, 1240, 1754, blocks, 26, lit=(pg == 19), ttl=title(lang, "xl") if pg == 0 else None)
        img_pages.append(img)
    p = newdir("pdf-scan") / "xl-en.pdf"
    img_pages[0].save(p, "PDF", resolution=150, save_all=True, append_images=img_pages[1:])
    reg("file", "pdf-scan", lang, "xl", p, {"pages": 20, "text_layer": False}, 20 * 600)


def main():
    for lang in LANGS:
        S = sentences(lang)
        P = paragraphs(S)
        if len(S) < 12:
            sys.exit(f"thiếu câu nguồn cho {lang}: {len(S)}")
        gen_docx(lang, S, P)
        gen_xlsx(lang, S)
        if lang in PDF_TEXT_LANGS:
            gen_pdf(lang, S, P)
        gen_images(lang, S, P)
    gen_xl()
    blocked = [{"format": "doc", "reason": "Không có LibreOffice/antiword để tạo .doc (Word 97-2003) thật trên máy dựng; "
                "đổi đuôi .docx thành .doc là mẫu sai định dạng nên không tạo. Cần "
                "`soffice --headless --convert-to doc` từ docx/ để bổ sung."},
               {"format": "pdf (lớp chữ)", "langs": ["ko", "ar"], "reason":
                "reportlab không nhúng được font CFF (Noto CJK) và không ghép chữ Ả Rập; ko/ar chỉ có ở pdf-scan/png/jpg/docx/xlsx."}]
    (OUT / "manifest.json").write_text(json.dumps(
        {"generator": "tools/gen_format_fixtures.py", "note": "Nội dung kỹ thuật và thông tin đăng nhập là GIẢ.",
         "count": len(MAN), "blocked": blocked, "items": MAN}, ensure_ascii=False, indent=1), encoding="utf-8")
    print("items", len(MAN), "total MB", round(sum(m["bytes"] for m in MAN) / 1e6, 1))


if __name__ == "__main__":
    main()
