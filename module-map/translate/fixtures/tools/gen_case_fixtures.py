#!/usr/bin/env python3
"""Sinh tài liệu mẫu cho 20 ca P0 đợt 1 của 09-case-tai-lieu-nguoi-dung-co-the-thu.txt.

Chạy:  /path/venv/bin/python gen_case_fixtures.py     (cần python-docx openpyxl reportlab pillow pypdf)
Ra:    fixtures/formats-extra/<tệp> + fixtures/formats-extra/manifest.json (đăng ký riêng, không đụng formats/)
Dữ liệu 100% GIẢ. Xác định (không ngẫu nhiên). Các tệp "độc hại" (zip bomb, XXE, liên kết ngoài) chỉ để kiểm hệ thống
của CHÍNH MÌNH từ chối đúng; chúng không chứa mã chạy được.
"""
import io, json, sys, zipfile, hashlib, base64, textwrap
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import gen_format_fixtures as G  # tái dùng hàm vẽ trang/câu nguồn
from PIL import Image, ImageDraw, ImageFont
import docx, openpyxl
from docx.shared import Pt
from openpyxl.styles import Font, Alignment
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.lib.utils import ImageReader

FX = G.FX
OUT = FX / "formats-extra"
OUT.mkdir(parents=True, exist_ok=True)
ITEMS = []
SPECIALS = list("!@#$%^&*()_+={}[]|\\:;\"'<>,.?/~`-")  # 33 ký tự, bỏ 1 khi cần đủ 32


def reg(uc, path, fmt, preserve=(), checks=None, note="", batch=None):
    ITEMS.append({"uc": uc, "path": str(path.relative_to(FX)), "format": fmt, "bytes": path.stat().st_size,
                  "preserve_literals": list(preserve), "checks": checks or {}, "note": note})


def pw(i, ch):
    return f"Ab{i % 10}{ch}xY{(i * 7) % 10}{ch}z"


# ---- #1 UC-C02: 32 mật khẩu, mỗi ký tự đặc biệt một dòng ----
def c02():
    lines, lits = ["Danh sách mật khẩu thử nghiệm (dữ liệu giả), mỗi dòng chứa một ký tự đặc biệt khác nhau:"], []
    for i, ch in enumerate(SPECIALS[:32], 1):
        p = pw(i, ch)
        lines.append(f"Mật khẩu số {i}: {p}")
        lits.append(p)
    p = OUT / "sensitive-specialchars-vi.txt"
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    reg("UC-C02", p, "txt", lits, {"line_count": len(lines)}, "vi->en/ja/ar; mỗi mật khẩu phải còn nguyên")


# ---- #2 UC-C01: 30 cặp tài khoản/mật khẩu ----
def c01():
    lines, lits = ["Bảng tài khoản dịch vụ (giả):"], []
    for i in range(1, 31):
        u, p_ = f"svc_user_{i:02d}", pw(i, SPECIALS[i % 32])
        lines.append(f"Tài khoản: {u} | Mật khẩu: {p_}")
        lits += [u, p_]
    p = OUT / "creds-30pairs-vi.txt"
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    reg("UC-C01", p, "txt", lits, {"line_count": len(lines)}, "vi->en")


# ---- #3 UC-C07 (+C05/C06): chuỗi kết nối, JWT, khóa PEM ----
def c07():
    b64 = lambda s: base64.urlsafe_b64encode(s.encode()).decode().rstrip("=")
    jwt = ".".join([b64('{"alg":"HS256","typ":"JWT"}'), b64('{"sub":"svc_backup_01","iat":1790000000}'),
                    b64("signature-fake-0123456789abcdef")])
    pem_body = [base64.b64encode(hashlib.sha256(str(i).encode()).digest() * 2).decode() for i in range(6)]
    pem = "-----BEGIN RSA PRIVATE KEY-----\n" + "\n".join(pem_body) + "\n-----END RSA PRIVATE KEY-----"
    ssh = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIFAKEKEYFAKEKEYFAKEKEYFAKEKEYFAKEKEYFAKEKEY0 ops@example.org"
    conns = ["postgres://dbadmin:S3cr3t!@10.20.30.40:5432/prod?sslmode=require",
             "mongodb+srv://app_user:P%40ss%23w0rd@cluster0.example.net/sales?retryWrites=true",
             "Server=10.0.0.5,1433;Database=Hr;User Id=sa;Password=P@ssw0rd!2026;Encrypt=True;",
             "jdbc:mysql://db.example.org:3306/shop?user=root&password=Tr0ub4dor&3",
             "redis://:r3d!s_P@ss@10.0.0.9:6379/0", "amqp://guest:g%2Fuest@mq.example.org:5672/%2F"]
    other = ["AKIAFAKEFAKEFAKE0123", "Bearer " + jwt, "sk-test-9f8E7d6C5b4A3", "ghp_FAKEFAKEFAKEFAKEFAKEFAKEFAKEFAKE0123"]
    txt = ["Connection strings and secrets (all fake):", *conns, "", "Tokens:", *other, "", "JWT: " + jwt, "", "SSH key:", ssh, "", "PEM:", pem, ""]
    p = OUT / "connstrings-en.txt"
    p.write_text("\n".join(txt), encoding="utf-8")
    reg("UC-C07", p, "txt", conns + other + [jwt, ssh] + pem_body, {"keep_pem_lines": len(pem_body) + 2}, "en->vi; gồm UC-C05/C06")


# ---- #4 UC-C12/C13: JSON + YAML phải parse được sau dịch ----
def c12():
    cfg = {"ten_dich_vu": "Dịch vụ sao lưu", "mo_ta": "Tự động sao lưu cơ sở dữ liệu mỗi đêm.", "tai_khoan": "svc_backup_01",
           "mat_khau": "P@ss!w0rd#2026$%^&*()_+", "dia_chi": "192.168.10.25:8443", "canh_bao": ["Dung lượng đĩa thấp", "Sao lưu thất bại"],
           "lien_he": {"email": "ops.team@example.org", "dien_thoai": "+84 24 3999 0000"}, "so_lan_thu_lai": 3, "bat": True}
    p = OUT / "config-json-vi.txt"
    p.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    reg("UC-C12", p, "txt", ["svc_backup_01", "P@ss!w0rd#2026$%^&*()_+", "192.168.10.25:8443", "ops.team@example.org"],
        {"json_parse": True, "json_keys_unchanged": list(cfg)}, "vi->en; JSON còn parse được, khóa không đổi")
    y = ("service:\n  name: Dịch vụ sao lưu\n  description: \"Tự động sao lưu cơ sở dữ liệu mỗi đêm\"\n  account: svc_backup_01\n"
         "  password: 'P@ss!w0rd#2026$%^&*()_+'\n  endpoints:\n    - 192.168.10.25:8443\n    - https://portal.example.org/login?user=admin&pass=Ab!9#x\n"
         "  alerts:\n    - Dung lượng đĩa thấp\n    - Sao lưu thất bại\n")
    p = OUT / "config-yaml.txt"
    p.write_text(y, encoding="utf-8")
    reg("UC-C13", p, "txt", ["svc_backup_01", "P@ss!w0rd#2026$%^&*()_+", "192.168.10.25:8443"],
        {"yaml_keys": ["service", "name", "description", "account", "password", "endpoints", "alerts"]}, "vi->en; thụt lề và khóa YAML giữ nguyên")


# ---- #5 UC-C15: log 200 dòng ----
def c15():
    lines = []
    for i in range(200):
        k = i % 3
        ip = f"198.51.100.{i % 250 + 1}"
        if k == 0:
            lines.append(f"Oct 11 02:{i % 60:02d}:{(i * 7) % 60:02d} srv{i % 5} sshd[{1000 + i}]: Failed password for invalid user admin{i} from {ip} port {40000 + i} ssh2")
        elif k == 1:
            lines.append(f'{ip} - - [11/Oct/2026:02:{i % 60:02d}:11 +0700] "GET /login.php?id={i}\' OR 1=1-- HTTP/1.1" 403 {500 + i} "-" "sqlmap/1.7"')
        else:
            lines.append(f"EventID=4625 Account=CORP\\svc_user_{i:03d} Workstation=PC{i:03d} SourceIP={ip} Status=0xC000006D Message=Đăng nhập thất bại")
    p = OUT / "log-200lines.txt"
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    reg("UC-C15", p, "txt", ["198.51.100.1", "CORP\\svc_user_002", "sqlmap/1.7", "0xC000006D"], {"line_count": 200}, "en/vi->vi/en; số dòng ra = vào")


# ---- docx helpers ----
def dtable(doc, rows, header=True):
    t = doc.add_table(rows=len(rows), cols=len(rows[0])); t.style = "Table Grid"
    for i, r in enumerate(rows):
        for j, v in enumerate(r):
            t.cell(i, j).text = str(v)
    return t


def mono(doc, text):
    para = doc.add_paragraph()
    run = para.add_run(text); run.font.name = "Courier New"; run.font.size = Pt(9)
    return para


# ---- #6 UC-C24: bảng IOC 100 hàng ----
def c24():
    d = docx.Document(); d.add_heading("Danh sách IOC (đã defang)", 0)
    rows, lits = [["#", "IOC", "Loại", "Ghi chú"]], []
    for i in range(1, 101):
        k = i % 4
        if k == 0: ioc, typ = f"hxxp://evil{i}[.]example[.]com/payload{i}.bin", "URL"
        elif k == 1: ioc, typ = f"198[.]51[.]100[.]{i}", "IP"
        elif k == 2: ioc, typ = hashlib.md5(str(i).encode()).hexdigest(), "MD5"
        else: ioc, typ = hashlib.sha256(str(i).encode()).hexdigest(), "SHA256"
        rows.append([i, ioc, typ, "Mã độc phát hiện trên máy chủ web, cần chặn tại tường lửa."]); lits.append(ioc)
    dtable(d, rows)
    p = OUT / "ioc-table-vi.docx"; d.save(p)
    reg("UC-C24", p, "docx", lits, {"tables": 1, "table_rows": 101, "cols": 4}, "vi->en; IOC defang giữ nguyên; 101 hàng x 4 cột")


# ---- #7 UC-A01: báo cáo pentest ----
def a01():
    S = G.sentences("vi"); P = G.paragraphs(S)
    d = docx.Document(); d.add_heading("Báo cáo kiểm thử xâm nhập — Hệ thống Cổng thông tin (giả)", 0)
    d.add_heading("1. Tóm tắt điều hành", 1); d.add_paragraph(P[0]); d.add_paragraph(P[1])
    d.add_heading("2. Bảng phát hiện", 1)
    rows = [["ID", "Mức độ", "CVSS", "CVE", "URL ảnh hưởng"]]
    lits = []
    for i in range(1, 13):
        cve, url = f"CVE-2024-{3090 + i}", f"https://portal.example.org/app/v{i}/search?q={i}&token=Ab!{i}#x"
        rows.append([f"PT-{i:03d}", ["Nghiêm trọng", "Cao", "Trung bình", "Thấp"][i % 4], f"{9.8 - i * 0.4:.1f}", cve, url]); lits += [cve, url]
    dtable(d, rows)
    for sec in range(3, 12):
        d.add_heading(f"{sec}. Phát hiện chi tiết {sec - 2}", 1)
        for k in range(4):
            d.add_paragraph(P[(sec * 3 + k) % len(P)])
        cmd = f"curl -k -X POST 'https://portal.example.org/login' -d 'user=admin&pass=P@ss!w0rd#2026$%^&*()_+' -H 'X-Id: {sec}'"
        mono(d, cmd); lits.append(cmd)
        d.add_paragraph("Khuyến nghị: vá lỗi, bật xác thực đa yếu tố và giới hạn số lần đăng nhập thất bại.", style="List Bullet")
    p = OUT / "pentest-report-vi.docx"; d.save(p)
    words = sum(len(x.text.split()) for x in docx.Document(p).paragraphs)
    reg("UC-A01", p, "docx", lits, {"tables": 1, "table_rows": 13, "code_blocks": 9, "approx_pages": round(words / 500 + 2)},
        "vi->en; khối lệnh Courier giữ nguyên từng ký tự; số trang ước tính (không có Word để đếm)")


# ---- #12 UC-B02: bảng lồng bảng ----
def b02():
    d = docx.Document(); d.add_heading("Bảng lồng nhau", 1)
    t = d.add_table(rows=3, cols=3); t.style = "Table Grid"
    for i in range(3):
        for j in range(3):
            t.cell(i, j).text = f"Ô {i + 1}-{j + 1}: nội dung tiếng Việt"
    inner = t.cell(1, 1).add_table(rows=2, cols=2); inner.style = "Table Grid"
    for i in range(2):
        for j in range(2):
            inner.cell(i, j).text = f"Ô lồng {i + 1}-{j + 1} IP 10.0.{i}.{j}"
    p = OUT / "nested-tables-vi.docx"; d.save(p)
    reg("UC-B02", p, "docx", ["10.0.0.0", "10.0.1.1"], {"tables": 1, "nested_tables": 1, "outer": "3x3", "inner": "2x2"}, "vi->en; bảng lồng còn nguyên 3x3 chứa 2x2")


# ---- xlsx ----
def b8():
    S = G.sentences("vi")
    wb = openpyxl.Workbook(); ws = wb.active; ws.title = "Tồn kho"
    ws.merge_cells("A1:F1"); ws["A1"] = "Bảng tồn kho tài sản CNTT (giả)"; ws["A1"].font = Font(bold=True, size=14)
    for j, h in enumerate(["Hostname", "IP", "Hệ điều hành", "Người phụ trách", "Ngày mua", "Trạng thái"], 1):
        ws.cell(3, j, h).font = Font(bold=True)
    import datetime
    names = ["Nguyễn Văn An", "Trần Thị Bích", "Lê Quốc Cường", "Phạm Thu Dung"]
    for i in range(300):
        r = 4 + i
        ws.cell(r, 1, f"srv-hn-{i:03d}"); ws.cell(r, 2, f"10.20.{i // 250}.{i % 250 + 1}")
        ws.cell(r, 3, ["Ubuntu 22.04 (cần nâng cấp)", "Windows Server 2019", "Debian 12", "CentOS 7 (hết hỗ trợ)"][i % 4])
        ws.cell(r, 4, names[i % 4]); ws.cell(r, 5, datetime.date(2022, 1 + i % 12, 1 + i % 27))
        ws.cell(r, 6, ["Đang hoạt động", "Chờ thanh lý", "Bảo trì"][i % 3])
    p = OUT / "inventory-300rows-vi.xlsx"; wb.save(p)
    reg("UC-A08", p, "xlsx", ["srv-hn-000", "10.20.0.1", "srv-hn-299"], {"sheets": 1, "rows": 300, "merged": ["A1:F1"], "date_col": "E"},
        "vi->en; hostname/IP/ngày giữ nguyên, chỉ dịch chữ; ô gộp tiêu đề còn")


def b08():
    wb = openpyxl.Workbook(); names = ["Báo cáo & Phân tích", "Dữ liệu (2026)", "Danh sách máy chủ quan trọng", "Tổng hợp quý 1 & quý 2", "Cảnh báo (nghiêm trọng)", "Ghi chú vận hành hàng ngày"]
    for k, n in enumerate(names):
        ws = wb.active if k == 0 else wb.create_sheet(); ws.title = n[:31]
        ws["A1"] = f"Nội dung sheet {k + 1}"; ws["A2"] = "Hệ thống giám sát phát hiện bất thường."
    p = OUT / "sheets-special-names.xlsx"; wb.save(p)
    reg("UC-B08", p, "xlsx", [], {"sheets": 6, "sheet_names": [n[:31] for n in names], "max_name_len": 31},
        "vi->en; tên sheet không được làm hỏng tệp (ký tự cấm, >31 ký tự, trùng tên sau dịch)")


def b10():
    wb = openpyxl.Workbook(); ws = wb.active; ws.title = "Tính toán"
    ws["A1"], ws["B1"] = "Hạng mục", "Số lượng"
    for i, (n, v) in enumerate([("Máy chủ web", 12), ("Máy chủ CSDL", 5), ("Thiết bị mạng", 30)], 2):
        ws.cell(i, 1, n); ws.cell(i, 2, v)
    ws["A6"], ws["B6"] = "Tổng", "=SUM(B2:B4)"
    ws["A7"], ws["B7"] = "Đánh giá", '=IF(B6>40,"Cao","Thấp")'
    ws["A8"], ws["B8"] = "Tra cứu", '=VLOOKUP("Máy chủ web",A2:B4,2,FALSE)'
    ws["A9"], ws["B9"] = "Liên kết", '=HYPERLINK("https://portal.example.org/help","Trợ giúp")'
    p = OUT / "formulas-vi.xlsx"; wb.save(p)
    reg("UC-B10", p, "xlsx", ["=SUM(B2:B4)", '=IF(B6>40,"Cao","Thấp")', '=VLOOKUP("Máy chủ web",A2:B4,2,FALSE)', "https://portal.example.org/help"],
        {"formula_cells": ["B6", "B7", "B8", "B9"]}, "vi->en; công thức KHÔNG bị dịch (cả chuỗi 'Cao'/'Thấp' trong công thức) và vẫn tính được")


def b13():
    import datetime
    wb = openpyxl.Workbook(); ws = wb.active; ws.title = "Số liệu"
    ws["A1"], ws["B1"] = "Chỉ tiêu", "Giá trị"
    rows = [("Mã nhân viên (văn bản)", "0123"), ("Số khoa học", 1.5e10), ("Ngày", datetime.date(2026, 3, 15)), ("Tiền tệ", 1250000.5), ("Tỉ lệ", 0.375), ("Ghi chú", "Chờ phê duyệt")]
    for i, (a, b) in enumerate(rows, 2):
        ws.cell(i, 1, a); ws.cell(i, 2, b)
    ws["B5"].number_format = '#,##0.00 "₫"'; ws["B6"].number_format = "0.0%"; ws["B4"].number_format = "dd/mm/yyyy"
    p = OUT / "numbers-dates-vi.xlsx"; wb.save(p)
    reg("UC-B13", p, "xlsx", ["0123"], {"cells": {"B2": "0123 (text)", "B3": 1.5e10, "B4": "2026-03-15", "B5": 1250000.5, "B6": 0.375}},
        "vi->en; '0123' còn là văn bản, số/ngày/định dạng không đổi")


# ---- PDF: hóa đơn có lớp chữ ----
def a07():
    pdfmetrics.registerFont(TTFont("NS", G.NOSANS))
    p = OUT / "invoice-en.pdf"; c = canvas.Canvas(str(p), pagesize=A4); W, H = A4
    c.setFont("NS", 18); c.drawString(50, H - 60, "INVOICE / QUOTATION")
    c.setFont("NS", 10)
    for i, t in enumerate(["Seller: ACME Security Ltd.   Tax ID: 0312345678", "Buyer: Example Corp.   Contact: billing@example.org",
                           "IBAN: DE89 3704 0044 0532 0130 00   SWIFT: COBADEFFXXX", "Invoice no.: INV-2026-00123   Date: 15/03/2026"]):
        c.drawString(50, H - 90 - i * 16, t)
    cols = [50, 130, 330, 400, 470, 545]; y = H - 190
    hdr = ["SKU", "Description", "Qty", "Unit price", "Total"]
    rows = [hdr] + [[f"SKU-{1000 + i}", f"Annual license for module {i}", str(i + 1), "1,250.00", f"{(i + 1) * 1250:,.2f}"] for i in range(8)]
    for r in rows:
        for k, v in enumerate(r):
            c.drawString(cols[k] + 4, y - 12, v)
        c.line(cols[0], y, cols[-1], y); y -= 22
    c.line(cols[0], y, cols[-1], y)
    for x in cols:
        c.line(x, H - 190, x, y)
    c.drawString(400, y - 24, "VAT 10%: 1,800.00     Total due: USD 19,800.00")
    c.save()
    reg("UC-A07", p, "pdf", ["DE89 3704 0044 0532 0130 00", "COBADEFFXXX", "0312345678", "billing@example.org", "INV-2026-00123", "SKU-1000", "SKU-1007"],
        {"pages": 1, "table_rows": 9, "text_layer": True}, "en->vi; bảng giữ hàng/cột; IBAN/mã số thuế nguyên văn")


# ---- PDF trộn / scan 10 trang ----
def scan_page(lang, blocks, ttl=None):
    return G.draw_page(lang, 1240, 1754, blocks, 26, lit=False, ttl=ttl)[0]


def pdf_from_pages(path, pages):
    pages[0].save(path, "PDF", resolution=150, save_all=True, append_images=pages[1:])


def e18():
    S = G.sentences("en"); P = G.paragraphs(S)
    p = OUT / "mixed-scan-text-5p.pdf"; c = canvas.Canvas(str(p), pagesize=A4); W, H = A4
    pdfmetrics.registerFont(TTFont("NS", G.NOSANS))
    def put_image(img):
        b = io.BytesIO(); img.convert("RGB").save(b, "JPEG", quality=80); b.seek(0)
        c.drawImage(ImageReader(b), 0, 0, W, H)
    def put_text(par_list, title):
        c.setFont("NS", 16); c.drawString(50, H - 60, title); c.setFont("NS", 10); y = H - 90
        for par in par_list:
            for line in textwrap.wrap(par, 95):
                c.drawString(50, y, line); y -= 14
            y -= 8
        return y
    put_image(scan_page("en", P[0:4], "SecAI-TEST mixed p1 (scan)")); c.showPage()
    put_image(scan_page("en", P[4:8], "SecAI-TEST mixed p2 (scan)")); c.showPage()
    put_text(P[8:11], "SecAI-TEST mixed p3 (text layer)"); c.showPage()
    put_text(P[11:14], "SecAI-TEST mixed p4 (text layer)"); c.showPage()
    put_image(scan_page("en", P[14:17], "SecAI-TEST mixed p5 (image + hidden text)"))
    t = c.beginText(50, H - 60); t.setTextRenderMode(3); t.setFont("NS", 10)
    for line in textwrap.wrap(" ".join(P[14:17]), 95):
        t.textLine(line)
    c.drawText(t); c.showPage(); c.save()
    reg("UC-E18", p, "pdf", [], {"pages": 5, "page_kinds": ["scan", "scan", "text", "text", "image+hidden-text"]}, "en->vi; 5 trang giữ thứ tự; trang scan được OCR, trang chữ không bị OCR thừa")


def e07():
    import random
    rng = random.Random(7)
    S = G.sentences("en"); P = G.paragraphs(S)
    pages = []
    for n in range(1, 11):
        if n in (4, 9):
            pages.append(Image.new("RGB", (1240, 1754), (255, 255, 255)))
        elif n == 6:
            im = Image.new("RGB", (1240, 1754), (240, 240, 240)); px = im.load()
            for _ in range(60000):
                px[rng.randrange(1240), rng.randrange(1754)] = (rng.randrange(256),) * 3
            pages.append(im)
        else:
            pages.append(scan_page("en", P[(n * 3) % len(P):], f"SecAI-TEST scan page {n}"))
    p = OUT / "scan-10p-with-blank.pdf"; pdf_from_pages(p, pages)
    reg("UC-E07", p, "pdf-scan", [], {"pages": 10, "blank_pages": [4, 9], "noise_pages": [6]},
        "en->vi; trang trắng không bịa nội dung; trang nhiễu không làm hỏng job; thứ tự 10 trang đúng")


# ---- ảnh terminal tối ----
def e10():
    img = Image.new("RGB", (1300, 900), (18, 18, 18)); d = ImageDraw.Draw(img)
    f = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf", 22)
    lines = ["$ nmap -sV -p 22,80,443,8443 192.168.10.25", "Starting Nmap 7.94 ( https://nmap.org ) at 2026-10-11 02:14 +07",
             "PORT     STATE SERVICE  VERSION", "22/tcp   open  ssh      OpenSSH 8.9p1 Ubuntu", "443/tcp  open  https    nginx 1.18.0",
             "8443/tcp open  https    Apache Tomcat 9.0.30 (CVE-2024-3094?)", "", "$ sqlmap -u 'https://portal.example.org/item?id=1' --batch --dbs",
             "[02:15:44] [INFO] testing connection to the target URL", "[02:15:51] [WARNING] password: P@ss!w0rd#2026$%^&*()_+",
             "$ export TOKEN=sk-test-9f8E7d6C5b4A3", "$ ssh svc_backup_01@192.168.10.25 -p 2222"]
    y = 30
    for i, l in enumerate(lines):
        d.text((30, y), l, font=f, fill=(90, 255, 120) if l.startswith("$") else (220, 220, 220)); y += 34
    p = OUT / "terminal-nmap-dark.png"; img.save(p)
    reg("UC-E10", p, "png", ["192.168.10.25", "CVE-2024-3094", "P@ss!w0rd#2026$%^&*()_+", "sk-test-9f8E7d6C5b4A3", "svc_backup_01",
                             "nmap -sV -p 22,80,443,8443", "https://portal.example.org/item?id=1"],
        {"dark_background": True}, "en->vi; lệnh/IP/CVE giữ nguyên; OCR đọc được chữ sáng trên nền tối")


# ---- an toàn: zip bomb, nhiều entry, liên kết ngoài, XXE ----
def base_docx():
    b = io.BytesIO(); d = docx.Document(); d.add_paragraph("Tài liệu kiểm thử an toàn."); d.save(b); return b.getvalue()


def g08():
    src = zipfile.ZipFile(io.BytesIO(base_docx()))
    out = OUT / "zipbomb-ratio.docx"
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for n in src.namelist():
            data = src.read(n)
            if n == "word/document.xml":
                head, tail = data.split(b"<w:body>")[0] + b"<w:body>", b"</w:body></w:document>"
                filler = b"<w:p><w:r><w:t>" + b"A" * 1000 + b"</w:t></w:r></w:p>"
                data = head + filler * 70000 + tail  # ~70 MB sau giải nén, nén rất nhỏ
            z.writestr(n, data)
    reg("UC-G08a", out, "docx", [], {"expect_error": "ARCHIVE_LIMIT_EXCEEDED/413 (tỉ lệ nén > 100:1 hoặc > 50 MiB giải nén)"}, "tệp nhỏ trên đĩa, giải nén rất lớn")
    out2 = OUT / "zip-many-entries.docx"
    with zipfile.ZipFile(out2, "w", zipfile.ZIP_DEFLATED) as z:
        for n in src.namelist():
            z.writestr(n, src.read(n))
        for i in range(2100):
            z.writestr(f"word/media/pad{i}.txt", b"x")
    reg("UC-G08b", out2, "docx", [], {"expect_error": "ARCHIVE_LIMIT_EXCEEDED/413 (> 2.000 entry)"}, "2.100 entry")


def g06():
    d = docx.Document(); par = d.add_paragraph("Xem tài liệu tại ")
    from docx.opc.constants import RELATIONSHIP_TYPE as RT
    rid = d.part.relate_to("http://tracker.example.invalid/pixel?doc=1", RT.HYPERLINK, is_external=True)
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    h = OxmlElement("w:hyperlink"); h.set(qn("r:id"), rid)
    r = OxmlElement("w:r"); t = OxmlElement("w:t"); t.text = "liên kết ngoài"; r.append(t); h.append(r); par._p.append(h)
    p = OUT / "docx-external-rel.docx"; d.save(p)
    reg("UC-G06", p, "docx", [], {"expect_error": "UNSAFE_DOCUMENT (liên kết ngoài); không được truy cập URL"}, "ghi nhận: UI hiện chỉ báo 'Dịch thất bại' (rủi ro UX)")


def g07():
    src = zipfile.ZipFile(io.BytesIO(base_docx()))
    p = OUT / "docx-xxe.docx"
    with zipfile.ZipFile(p, "w", zipfile.ZIP_DEFLATED) as z:
        for n in src.namelist():
            data = src.read(n)
            if n == "word/document.xml":
                s = data.decode("utf-8")
                s = s.replace("?>", '?>\n<!DOCTYPE d [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>', 1)
                s = s.replace("Tài liệu kiểm thử an toàn.", "Nội dung &xxe; kết thúc.")
                data = s.encode("utf-8")
            z.writestr(n, data)
    reg("UC-G07", p, "docx", [], {"expect_error": "UNSAFE_DOCUMENT/CORRUPT_DOCUMENT", "must_not_contain": ["root:x:0:0", "/bin/bash"]},
        "kiểm hệ thống từ chối DOCTYPE/entity; đầu ra không chứa nội dung /etc/passwd")


# ---- #19 hành vi: lô tệp ----
def h01():
    pick = [("fixtures/files/vi-1000.txt", "txt"), ("fixtures/formats/docx/short-vi.docx", "docx"), ("fixtures/formats/png/short-vi.png", "png"),
            ("fixtures/formats/jpg/short-vi.jpg", "jpg"), ("fixtures/formats/xlsx/short-vi.xlsx", "xlsx"), ("fixtures/formats/pdf/short-vi.pdf", "pdf")]
    ITEMS.append({"uc": "UC-H02/H01", "path": None, "format": "batch", "bytes": 0, "preserve_literals": [],
                  "checks": {"batch_ok_5": [p for p, _ in pick[:5]], "batch_over_limit_6": [p for p, _ in pick],
                             "expect": "5 tệp trong 1 phiên chạy độc lập, kết quả không lẫn nhau; tệp thứ 6 bị chặn rõ ràng; FE không im lặng khi 429"},
                  "note": "dùng tệp có sẵn trong fixtures/ (đường dẫn tương đối fixtures-root = module-map/translate)"})


def main():
    for fn in (c02, c01, c07, c12, c15, c24, a01, b8, b08, b10, b13, b02, a07, e18, e07, e10, g08, g06, g07, h01):
        fn()
    (OUT / "manifest.json").write_text(json.dumps({
        "generator": "tools/gen_case_fixtures.py", "source_cases": "09-case-tai-lieu-nguoi-dung-co-the-thu.txt (20 ca P0 đợt 1)",
        "note": "Dữ liệu GIẢ. UC-I02 (so xem trước/tải TXT/JSON) dùng đầu ra của UC-C02. .doc chưa có (thiếu LibreOffice).",
        "count": len(ITEMS), "items": ITEMS}, ensure_ascii=False, indent=1), encoding="utf-8")
    print("items", len(ITEMS), "MB", round(sum(i["bytes"] for i in ITEMS) / 1e6, 2))


if __name__ == "__main__":
    main()
