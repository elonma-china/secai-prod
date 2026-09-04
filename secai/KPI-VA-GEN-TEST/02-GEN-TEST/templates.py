#!/usr/bin/env python3
"""templates.py — Bảng đồng nghĩa + mẫu câu hỏi tiếng Việt cho bộ sinh test độc lập.

Các bảng SEV_TERMS / SEV_VI / CWE_INFO / REMED_ANY / NOTFOUND_ANY / OFFTOPIC_ANY /
TECH_ACRONYMS được giữ NGUYÊN theo bộ sinh nghiệm thu gốc
(source/eval/nghiem-thu-suite/acceptance-bench/gen_acceptance.py:33-105) để tiêu chí
chấm của bộ mới không lệch chuẩn bộ cũ.

Mỗi hàm template nhận (rec, rng) và trả dict:
    {input, expected, tags, notes}
`template_id` chính là tên hàm (bỏ tiền tố). Bộ sinh tự gắn id/bucket/difficulty/metadata.

`rec` là một bản ghi CVE đã chuẩn hoá:
    {cve_id, severity, cvss_score, cvss_vector, year, kev,
     cwe_refs[], products[], text, capec_entries[], mitre_techniques[]}
"""
import re

# ── bảng đồng nghĩa (giữ nguyên từ generator gốc) ─────────────────────────
SEV_TERMS = {
    "CRITICAL": ["CRITICAL", "nghiêm trọng", "rất nghiêm trọng"],
    "HIGH": ["HIGH", "cao", "nghiêm trọng"],
    "MEDIUM": ["MEDIUM", "trung bình"],
    "LOW": ["LOW", "thấp"],
}
SEV_VI = {"CRITICAL": "rất nghiêm trọng", "HIGH": "cao", "MEDIUM": "trung bình", "LOW": "thấp"}

CWE_INFO = {
    "CWE-79": ["XSS", "cross-site scripting"],
    "CWE-89": ["SQL injection", "SQLi"],
    "CWE-78": ["command injection", "OS command"],
    "CWE-77": ["command injection", "chèn lệnh"],
    "CWE-787": ["out-of-bounds write", "buffer overflow", "tràn bộ đệm"],
    "CWE-125": ["out-of-bounds read", "đọc ngoài vùng"],
    "CWE-119": ["buffer overflow", "tràn bộ đệm"],
    "CWE-120": ["buffer overflow", "tràn bộ đệm"],
    "CWE-20": ["input validation", "kiểm tra đầu vào"],
    "CWE-22": ["path traversal", "directory traversal"],
    "CWE-416": ["use after free", "use-after-free"],
    "CWE-94": ["code injection", "chèn mã"],
    "CWE-352": ["CSRF", "request forgery"],
    "CWE-798": ["hard-coded", "credentials"],
    "CWE-306": ["missing authentication", "thiếu xác thực"],
    "CWE-862": ["missing authorization", "thiếu phân quyền"],
    "CWE-863": ["incorrect authorization", "phân quyền"],
    "CWE-502": ["deserialization", "giải tuần tự"],
    "CWE-918": ["SSRF", "request forgery"],
    "CWE-611": ["XXE", "external entity"],
    "CWE-434": ["file upload", "tải tệp lên"],
    "CWE-190": ["integer overflow", "tràn số nguyên"],
    "CWE-476": ["NULL pointer", "con trỏ NULL"],
    "CWE-287": ["authentication", "xác thực"],
    "CWE-295": ["certificate", "chứng chỉ"],
    "CWE-269": ["privilege", "đặc quyền"],
    "CWE-200": ["information disclosure", "lộ thông tin"],
    "CWE-639": ["IDOR", "tham chiếu trực tiếp"],
    "CWE-400": ["resource exhaustion", "DoS", "từ chối dịch vụ"],
    "CWE-401": ["memory leak", "rò rỉ bộ nhớ"],
    "CWE-284": ["access control", "kiểm soát truy cập"],
    "CWE-362": ["race condition", "tranh chấp"],
    "CWE-601": ["open redirect", "chuyển hướng mở"],
    "CWE-326": ["weak encryption", "mã hóa yếu"],
    "CWE-327": ["weak cryptography", "mã hóa yếu"],
    "CWE-312": ["cleartext", "bản rõ"],
    "CWE-59": ["symlink", "link following"],
    "CWE-617": ["assertion", "DoS"],
    "CWE-369": ["divide by zero", "chia cho không"],
}

REMED_ANY = ["vá", "patch", "cập nhật", "nâng cấp", "update", "upgrade", "phiên bản",
             "workaround", "giảm thiểu", "mitigation", "khuyến nghị", "khắc phục", "bản vá"]
NOTFOUND_ANY = ["không tìm thấy", "chưa index", "không chính xác", "không tồn tại",
                "không có thông tin", "không có dữ liệu"]
OFFTOPIC_ANY = ["ngoài phạm vi", "An toàn thông tin", "không thuộc phạm vi", "không hỗ trợ",
                "chuyên về bảo mật"]
TECH_ACRONYMS = ["SQL injection", "XSS", "RCE", "SSRF", "CSRF", "XXE", "DoS",
                 "buffer overflow", "remote code execution", "denial of service",
                 "privilege escalation", "use after free", "directory traversal",
                 "command injection", "authentication bypass", "memory corruption"]

DIFF_RATIOS = [("de", 0.4), ("trung_binh", 0.3), ("kho", 0.2), ("tu_duy", 0.1)]
BUCKET_RATIOS = [("dinh_danh", 0.40), ("khac_phuc", 0.40), ("tinh_huong", 0.17), ("ngoai_le", 0.03)]


def quotas(total, ratios):
    """Chia `total` theo tỉ lệ, dùng largest-remainder (giống gen_acceptance.py:99-105)."""
    raw = [(k, total * w) for k, w in ratios]
    base = {k: int(x) for k, x in raw}
    rem = total - sum(base.values())
    for k, _ in sorted(raw, key=lambda kv: kv[1] - int(kv[1]), reverse=True)[:rem]:
        base[k] += 1
    return base


# ── tiện ích ──────────────────────────────────────────────────────────────
def _sev_any(rec):
    out = list(SEV_TERMS.get((rec.get("severity") or "").upper(), []))
    if rec.get("cvss_score") is not None:
        out.append(str(rec["cvss_score"]))
    return out or ["mức độ"]


def _cwe_any(rec):
    out = []
    for c in (rec.get("cwe_refs") or [])[:3]:
        out.append(c)
        out += CWE_INFO.get(c.upper(), [])
    return out


def _prod(rec, rng=None):
    ps = rec.get("products") or []
    if not ps:
        return None
    return ps[0] if rng is None else rng.choice(ps[:5])


def _base(rec, extra_any=None, tools=True, min_words=40):
    exp = {
        "must_contain": [rec["cve_id"]],
        "must_contain_any": list(extra_any or _sev_any(rec)),
        "language": "vi",
        "min_words": min_words,
    }
    if tools:
        exp["must_use_tool"] = ["qdrant_search"]
    return exp


# ══════════════════════════════════════════════════════════════════════════
# BUCKET dinh_danh — định danh / phân loại CVE
# ══════════════════════════════════════════════════════════════════════════
def dd_lookup_basic(rec, rng):
    return {"input": f"{rec['cve_id']} là lỗ hổng gì? Mức độ nghiêm trọng thế nào?",
            "expected": _base(rec), "tags": ["lookup"],
            "notes": f"GT: {rec.get('severity')} {rec.get('cvss_score')}"}


def dd_lookup_cvss(rec, rng):
    return {"input": f"Điểm CVSS của {rec['cve_id']} là bao nhiêu và xếp mức nào?",
            "expected": _base(rec), "tags": ["cvss"],
            "notes": f"GT: CVSS {rec.get('cvss_score')} {rec.get('severity')}"}


def dd_lookup_cwe(rec, rng):
    return {"input": f"{rec['cve_id']} thuộc loại điểm yếu CWE nào? Giải thích ngắn gọn.",
            "expected": _base(rec, _cwe_any(rec)), "tags": ["cwe"],
            "notes": f"GT: {rec.get('cwe_refs')}"}


def dd_lookup_product(rec, rng):
    p = _prod(rec, rng)
    return {"input": f"Lỗ hổng {rec['cve_id']} ảnh hưởng tới sản phẩm/phiên bản nào?",
            "expected": _base(rec, [p] if p else None), "tags": ["product"],
            "notes": f"GT: {rec.get('products', [])[:3]}"}


def dd_classify_type(rec, rng):
    return {"input": f"Phân loại kiểu tấn công của {rec['cve_id']}. Đây là dạng lỗ hổng nào?",
            "expected": _base(rec, _cwe_any(rec) or _sev_any(rec)), "tags": ["classify"],
            "notes": f"GT: {rec.get('cwe_refs')}"}


def dd_kev_check(rec, rng):
    if rec.get("kev"):
        any_ = ["KEV", "đang bị khai thác", "khai thác thực tế", "Known Exploited"]
    else:
        any_ = _sev_any(rec)
    return {"input": f"{rec['cve_id']} có nằm trong danh mục KEV (đang bị khai thác thực tế) không?",
            "expected": _base(rec, any_), "tags": ["kev"],
            "notes": f"GT: kev={rec.get('kev')}"}


def dd_cvss_vector(rec, rng):
    v = rec.get("cvss_vector")
    return {"input": f"Phân tích vector CVSS của {rec['cve_id']}: khai thác từ xa hay cục bộ, "
                     f"có cần xác thực không?",
            "expected": _base(rec, [v] if v else _sev_any(rec)), "tags": ["cvss", "vector"],
            "notes": f"GT: {v or rec.get('severity')}"}


def dd_year(rec, rng):
    return {"input": f"{rec['cve_id']} được công bố năm nào và hiện còn đáng lo ngại không?",
            "expected": _base(rec, [str(rec.get("year"))] + _sev_any(rec)), "tags": ["year"],
            "notes": f"GT: {rec.get('year')}"}


def dd_describe_infer(rec, rng):
    return {"input": f"Mô tả cơ chế của lỗ hổng {rec['cve_id']} và cho biết kẻ tấn công "
                     f"đạt được gì khi khai thác thành công.",
            "expected": _base(rec, _cwe_any(rec) or _sev_any(rec), min_words=60),
            "tags": ["infer"], "notes": f"GT: {rec.get('cwe_refs')}"}


def dd_risk_rank(rec, rng):
    return {"input": f"Nếu hệ thống của tôi có {rec['cve_id']}, mức ưu tiên xử lý nên là gì? Vì sao?",
            "expected": _base(rec, _sev_any(rec) + ["ưu tiên", "khẩn", "ngay"], min_words=60),
            "tags": ["risk"], "notes": f"GT: {rec.get('severity')} {rec.get('cvss_score')}"}


def tr_en_vi(rec, rng):
    src = (rec.get("text") or "")[:400]
    return {"input": f"Dịch đoạn mô tả lỗ hổng sau sang tiếng Việt, giữ nguyên các thuật ngữ "
                     f"kỹ thuật và mã định danh:\n\n{src}",
            "expected": {"must_contain": [rec["cve_id"]], "language": "vi",
                         "keep_terms": [rec["cve_id"]], "target_lang": "vi", "min_words": 30},
            "tags": ["translation"], "notes": "GT: dịch VI, giữ CVE-id"}


def tr_keep_terms(rec, rng):
    cwe = (rec.get("cwe_refs") or ["CWE-20"])[0]
    src = (rec.get("text") or "")[:300]
    return {"input": f"Dịch sang tiếng Việt cho báo cáo nội bộ, GIỮ NGUYÊN {rec['cve_id']} và {cwe}:\n\n{src}",
            "expected": {"must_contain": [rec["cve_id"]], "must_contain_any": [cwe] + CWE_INFO.get(cwe, []),
                         "language": "vi", "keep_terms": [rec["cve_id"], cwe],
                         "target_lang": "vi", "min_words": 30},
            "tags": ["translation", "keep_terms"], "notes": f"GT: giữ {rec['cve_id']}, {cwe}"}


def tr_vi_en(rec, rng):
    return {"input": f"Viết một đoạn tiếng Anh ngắn tóm tắt lỗ hổng {rec['cve_id']} để gửi "
                     f"cho đối tác nước ngoài.",
            "expected": {"must_contain": [rec["cve_id"]], "language": "en",
                         "target_lang": "en", "min_words": 30},
            "tags": ["translation", "vi_en"], "notes": "GT: đầu ra tiếng Anh"}


def ps_search(rec, rng):
    return {"input": f"Có mã khai thác (PoC/exploit) công khai nào cho {rec['cve_id']} không? "
                     f"Nếu có thì thuộc loại nào?",
            "expected": {"must_contain": [rec["cve_id"]],
                         "must_use_tool": ["poc_search"], "language": "vi", "min_words": 30},
            "tags": ["poc"], "notes": "GT: phải gọi poc_search"}


def ps_search_product(rec, rng):
    p = _prod(rec, rng) or "sản phẩm liên quan"
    return {"input": f"Tìm mã khai thác công khai liên quan tới {p} (ngữ cảnh {rec['cve_id']}).",
            "expected": {"must_contain": [rec["cve_id"]],
                         "must_use_tool": ["poc_search"], "language": "vi", "min_words": 30},
            "tags": ["poc", "product"], "notes": f"GT: poc_search cho {p}"}


def ps_verify(rec, rng):
    return {"input": f"Kiểm tra xem {rec['cve_id']} đã có exploit công khai chưa, và điều đó "
                     f"ảnh hưởng thế nào tới mức ưu tiên vá?",
            "expected": {"must_contain": [rec["cve_id"]],
                         "must_contain_any": REMED_ANY + ["ưu tiên"],
                         "must_use_tool": ["poc_search"], "language": "vi", "min_words": 50},
            "tags": ["poc", "risk"], "notes": "GT: poc_search + đánh giá ưu tiên"}


DD_TEMPLATES = [dd_lookup_basic, dd_lookup_cvss, dd_lookup_cwe, dd_lookup_product,
                dd_classify_type, dd_kev_check, dd_cvss_vector, dd_year,
                dd_describe_infer, dd_risk_rank]
TR_TEMPLATES = [tr_en_vi, tr_keep_terms, tr_vi_en]
PS_TEMPLATES = [ps_search, ps_search_product, ps_verify]


# ══════════════════════════════════════════════════════════════════════════
# BUCKET khac_phuc — tư vấn khắc phục
# ══════════════════════════════════════════════════════════════════════════
def kp_patch_basic(rec, rng):
    return {"input": f"Làm thế nào để khắc phục {rec['cve_id']}?",
            "expected": _base(rec, REMED_ANY, min_words=60), "tags": ["remediation"],
            "notes": "GT: nêu bản vá/nâng cấp"}


def kp_patch_product(rec, rng):
    p = _prod(rec, rng) or "hệ thống"
    return {"input": f"Hệ thống của tôi đang chạy {p} và dính {rec['cve_id']}. Cần làm gì?",
            "expected": _base(rec, REMED_ANY, min_words=60), "tags": ["remediation", "product"],
            "notes": f"GT: vá cho {p}"}


def kp_mitigation(rec, rng):
    return {"input": f"Chưa thể vá ngay {rec['cve_id']}. Có biện pháp giảm thiểu tạm thời nào?",
            "expected": _base(rec, REMED_ANY + ["tạm thời", "hạn chế", "chặn"], min_words=60),
            "tags": ["mitigation"], "notes": "GT: workaround"}


def kp_priority(rec, rng):
    return {"input": f"Trong đợt vá tháng này, {rec['cve_id']} nên được xếp ưu tiên thế nào so với "
                     f"các lỗ hổng mức thấp hơn? Giải thích.",
            "expected": _base(rec, _sev_any(rec) + ["ưu tiên"], min_words=60),
            "tags": ["priority"], "notes": f"GT: {rec.get('severity')}"}


def kp_workaround(rec, rng):
    return {"input": f"Nêu cấu hình tạm thời để giảm rủi ro từ {rec['cve_id']} khi chưa nâng cấp được.",
            "expected": _base(rec, REMED_ANY + ["cấu hình", "vô hiệu hoá", "giới hạn"], min_words=60),
            "tags": ["workaround"], "notes": "GT: cấu hình giảm thiểu"}


def kp_verify(rec, rng):
    return {"input": f"Sau khi vá {rec['cve_id']}, làm sao xác minh bản vá đã có hiệu lực?",
            "expected": _base(rec, REMED_ANY + ["kiểm tra", "xác minh", "phiên bản"], min_words=60),
            "tags": ["verify"], "notes": "GT: cách xác minh"}


def kp_rollout(rec, rng):
    return {"input": f"Lập kế hoạch triển khai bản vá cho {rec['cve_id']} trên môi trường "
                     f"production nhiều máy chủ.",
            "expected": _base(rec, REMED_ANY + ["kế hoạch", "triển khai", "thử nghiệm"], min_words=80),
            "tags": ["rollout"], "notes": "GT: kế hoạch vá"}


def kp_compensating(rec, rng):
    return {"input": f"Nếu buộc phải giữ phiên bản dính {rec['cve_id']} vì lý do nghiệp vụ, "
                     f"cần bổ sung kiểm soát bù trừ nào?",
            "expected": _base(rec, REMED_ANY + ["kiểm soát", "giám sát", "phân vùng", "WAF"],
                              min_words=80),
            "tags": ["compensating"], "notes": "GT: compensating control"}


def kp_impact_scope(rec, rng):
    return {"input": f"Đánh giá phạm vi ảnh hưởng của {rec['cve_id']} trong hạ tầng doanh nghiệp "
                     f"và thứ tự xử lý.",
            "expected": _base(rec, _sev_any(rec) + REMED_ANY, min_words=80),
            "tags": ["scope"], "notes": "GT: phạm vi + thứ tự"}


def kp_detect(rec, rng):
    return {"input": f"Làm sao phát hiện hệ thống có đang bị ảnh hưởng bởi {rec['cve_id']} hay không?",
            "expected": _base(rec, ["kiểm tra", "phiên bản", "quét", "log", "phát hiện"], min_words=60),
            "tags": ["detect"], "notes": "GT: cách phát hiện"}


KP_TEMPLATES = [kp_patch_basic, kp_patch_product, kp_mitigation, kp_priority, kp_workaround,
                kp_verify, kp_rollout, kp_compensating, kp_impact_scope, kp_detect]


# ── SAST: mã nguồn có lỗi (ghi ra file thật) ─────────────────────────────
SAST_BASES = [
    ("CWE-89", "py", '''import sqlite3

def get_user(conn, user_id):
    cur = conn.cursor()
    query = "SELECT * FROM users WHERE id = '" + user_id + "'"
    cur.execute(query)
    return cur.fetchall()
'''),
    ("CWE-78", "py", '''import os

def ping_host(host):
    os.system("ping -c 1 " + host)
'''),
    ("CWE-79", "js", '''function render(req, res) {
  const name = req.query.name;
  res.send("<h1>Xin chao " + name + "</h1>");
}
module.exports = { render };
'''),
    ("CWE-22", "py", '''def read_file(base, filename):
    path = base + "/" + filename
    with open(path) as f:
        return f.read()
'''),
    ("CWE-798", "py", '''import requests

API_TOKEN = "sk_live_9f8a7b6c5d4e3f2a1b0c9d8e7f6a5b4c"
DB_PASSWORD = "P@ssw0rd123!"

def call_api(path):
    return requests.get("https://api.example.com" + path,
                        headers={"Authorization": "Bearer " + API_TOKEN})
'''),
    ("CWE-502", "py", '''import pickle

def load_session(blob):
    return pickle.loads(blob)
'''),
    ("CWE-918", "py", '''import requests

def fetch(url):
    return requests.get(url, timeout=5).text
'''),
    ("CWE-327", "py", '''import hashlib

def hash_password(pw):
    return hashlib.md5(pw.encode()).hexdigest()
'''),
]


# Ngữ cảnh nghiệp vụ — làm mỗi câu SAST/remediation KHÁC NHAU về câu chữ
# (nếu mọi câu giống hệt nhau thì bước khử trùng lặp sẽ xoá gần hết).
SAST_CONTEXTS = [
    "module thanh toán", "cổng API nội bộ", "dịch vụ quản trị người dùng",
    "tác vụ nền xử lý hàng đợi", "trang quản trị nội bộ", "dịch vụ báo cáo",
    "cổng tích hợp đối tác", "công cụ nhập liệu hàng loạt", "dịch vụ xác thực",
    "trình xử lý tệp tải lên", "API tra cứu khách hàng", "dịch vụ đồng bộ dữ liệu",
]
SAST_ASKS = [
    "Nêu rõ mã CWE tương ứng và cách khắc phục.",
    "Chỉ ra dòng có vấn đề, mã CWE, và đề xuất sửa.",
    "Cho biết đây là điểm yếu CWE nào và vì sao nguy hiểm.",
    "Xác định mã CWE, đánh giá mức rủi ro và hướng khắc phục.",
]


def sast_prompt(cwe, path, ctx="", ask=""):
    name = path.rsplit("/", 1)[-1]
    where = f" của {ctx}" if ctx else ""
    return {"input": f"Rà soát tệp mã nguồn `{name}`{where} trong phần đính kèm và chỉ ra "
                     f"lỗ hổng bảo mật (nếu có). {ask or SAST_ASKS[0]}",
            "expected": {"must_find_cwe": [cwe], "must_contain_any": [cwe] + CWE_INFO.get(cwe, []),
                         "language": "vi", "min_words": 50},
            "tags": ["sast", cwe.lower()], "notes": f"GT: {cwe}", "files": [path]}


RM_BASES = [
    ("CWE-89", '''def search(conn, keyword):
    sql = "SELECT * FROM products WHERE name LIKE '%" + keyword + "%'"
    return conn.execute(sql).fetchall()'''),
    ("CWE-78", '''import subprocess
def backup(target):
    subprocess.call("tar -czf /tmp/b.tgz " + target, shell=True)'''),
    ("CWE-22", '''def download(name):
    return open("/var/data/" + name, "rb").read()'''),
    ("CWE-798", '''SECRET_KEY = "hardcoded-secret-do-not-use"
def sign(payload):
    import hmac, hashlib
    return hmac.new(SECRET_KEY.encode(), payload, hashlib.sha256).hexdigest()'''),
    ("CWE-502", '''import pickle, base64
def restore(state):
    return pickle.loads(base64.b64decode(state))'''),
    ("CWE-918", '''import requests
def preview(target_url):
    return requests.get(target_url, timeout=3).text[:500]'''),
    ("CWE-327", '''import hashlib
def store_password(pw):
    return hashlib.sha1(pw.encode()).hexdigest()'''),
    ("CWE-79", '''from flask import request
def greet():
    return "<div>" + request.args.get("msg", "") + "</div>"'''),
]

RM_ASKS = [
    "Trình bày theo các mục: Đánh giá, Before, After, Giải thích.",
    "Trình bày đủ các mục: Đánh giá, Before, After, Giải thích. Nêu rõ mã CWE.",
    "Yêu cầu: Đánh giá mức rủi ro, Before, After, Giải thích lý do sửa.",
    "Cấu trúc trả lời: Đánh giá, Before, After, Giải thích — kèm mã CWE tương ứng.",
    "Viết theo mục Đánh giá, Before, After, Giải thích; nói rõ vì sao bản cũ không an toàn.",
]


def rm_fix(cwe, code, ctx="", ask=""):
    where = f" (trích từ {ctx})" if ctx else ""
    return {"input": f"Đoạn mã sau{where} có lỗ hổng bảo mật. Hãy đề xuất bản vá.\n\n"
                     f"```python\n{code}\n```\n\n{ask or RM_ASKS[0]}",
            "expected": {"must_find_cwe": [cwe],
                         "must_have_sections": ["Đánh giá", "Before", "After", "Giải thích"],
                         "must_contain_any": [cwe] + CWE_INFO.get(cwe, []),
                         "language": "vi", "min_words": 60},
            "tags": ["remediation", "code", cwe.lower()], "notes": f"GT: vá {cwe}"}


def rm_diff(cwe, code, ctx="", ask=""):
    where = f" trong {ctx}" if ctx else ""
    return {"input": f"Viết bản vá dạng diff cho đoạn mã sau{where} và giải thích vì sao cách cũ "
                     f"không an toàn.\n\n```python\n{code}\n```\n\n"
                     f"Trình bày theo mục: Đánh giá, Before, After, Diff. "
                     f"{ask or ''}".rstrip(),
            "expected": {"must_find_cwe": [cwe],
                         "must_have_sections": ["Đánh giá", "Before", "After", "Diff"],
                         "must_contain_any": [cwe] + CWE_INFO.get(cwe, []),
                         "language": "vi", "min_words": 60},
            "tags": ["remediation", "diff", cwe.lower()], "notes": f"GT: diff vá {cwe}"}


# ══════════════════════════════════════════════════════════════════════════
# BUCKET tinh_huong — suy luận tình huống
# ══════════════════════════════════════════════════════════════════════════
def pt_scan(rec, rng):
    p = _prod(rec, rng) or "dịch vụ mục tiêu"
    return {"input": f"Trong một đợt kiểm thử xâm nhập có phạm vi hợp lệ, tôi phát hiện {p} "
                     f"có thể dính {rec['cve_id']}. Nêu các bước kiểm chứng an toàn.",
            "expected": _base(rec, ["kiểm chứng", "xác minh", "phiên bản", "phạm vi"], min_words=80),
            "tags": ["pentest"], "notes": "GT: quy trình kiểm chứng"}


def pt_scope(rec, rng):
    return {"input": f"Khi lập phạm vi pentest cho hệ thống có {rec['cve_id']}, cần lưu ý những "
                     f"ràng buộc và rủi ro nào?",
            "expected": _base(rec, ["phạm vi", "rủi ro", "cho phép", "ràng buộc"], min_words=80),
            "tags": ["pentest", "scope"], "notes": "GT: scoping"}


def pt_priority(rec, rng):
    return {"input": f"Đội pentest có 2 ngày. Với {rec['cve_id']} trong danh sách, nên tập trung "
                     f"kiểm thử gì trước và vì sao?",
            "expected": _base(rec, _sev_any(rec) + ["ưu tiên", "trước"], min_words=80),
            "tags": ["pentest", "priority"], "notes": "GT: ưu tiên kiểm thử"}


def pt_recon(rec, rng):
    p = _prod(rec, rng) or "hệ thống"
    return {"input": f"Giai đoạn thu thập thông tin cho {p}: cần xác định dấu hiệu nào để biết "
                     f"mục tiêu có khả năng dính {rec['cve_id']}?",
            "expected": _base(rec, ["phiên bản", "banner", "dấu hiệu", "nhận diện"], min_words=80),
            "tags": ["pentest", "recon"], "notes": "GT: dấu hiệu nhận diện"}


def ex_explain(rec, rng):
    return {"input": f"Giải thích cơ chế khai thác của {rec['cve_id']}: điều kiện tiên quyết, "
                     f"luồng tấn công và hệ quả.",
            "expected": _base(rec, _cwe_any(rec) + ["cơ chế", "khai thác", "điều kiện"], min_words=80),
            "tags": ["exploit"], "notes": f"GT: {rec.get('cwe_refs')}"}


def ex_mechanism(rec, rng):
    return {"input": f"Vì sao {rec['cve_id']} lại cho phép kẻ tấn công vượt qua kiểm soát an ninh? "
                     f"Phân tích nguyên nhân gốc.",
            "expected": _base(rec, _cwe_any(rec) + ["nguyên nhân", "cơ chế"], min_words=80),
            "tags": ["exploit", "rootcause"], "notes": "GT: nguyên nhân gốc"}


def ex_impact(rec, rng):
    return {"input": f"Nếu {rec['cve_id']} bị khai thác thành công trong môi trường doanh nghiệp, "
                     f"hậu quả cụ thể là gì?",
            "expected": _base(rec, _sev_any(rec) + ["hậu quả", "ảnh hưởng", "rủi ro"], min_words=80),
            "tags": ["exploit", "impact"], "notes": "GT: hệ quả"}


def ex_chain(rec, rng):
    return {"input": f"{rec['cve_id']} có thể được kết hợp với lỗ hổng nào khác để leo thang "
                     f"đặc quyền? Phân tích chuỗi tấn công khả dĩ.",
            "expected": _base(rec, ["chuỗi", "kết hợp", "leo thang", "đặc quyền"], min_words=80),
            "tags": ["exploit", "chain"], "notes": "GT: attack chain"}


def rp_exec(rec, rng):
    return {"input": f"Viết phần tóm tắt điều hành (dành cho lãnh đạo không chuyên kỹ thuật) "
                     f"về rủi ro từ {rec['cve_id']}.",
            "expected": _base(rec, _sev_any(rec) + ["rủi ro", "khuyến nghị"], min_words=80),
            "tags": ["report", "exec"], "notes": "GT: executive summary"}


def rp_tech(rec, rng):
    return {"input": f"Soạn mục kỹ thuật trong báo cáo đánh giá an ninh cho {rec['cve_id']}: "
                     f"mô tả, mức độ, ảnh hưởng, khuyến nghị.",
            "expected": _base(rec, _sev_any(rec) + REMED_ANY, min_words=100),
            "tags": ["report", "tech"], "notes": "GT: mục kỹ thuật"}


def rp_risk(rec, rng):
    return {"input": f"Lập bảng đánh giá rủi ro cho {rec['cve_id']}: khả năng khai thác, "
                     f"mức tác động, và điểm rủi ro tổng hợp.",
            "expected": _base(rec, _sev_any(rec) + ["khả năng", "tác động", "rủi ro"], min_words=80),
            "tags": ["report", "risk"], "notes": "GT: bảng rủi ro"}


PT_TEMPLATES = [pt_scan, pt_scope, pt_priority, pt_recon]
EX_TEMPLATES = [ex_explain, ex_mechanism, ex_impact, ex_chain]
RP_TEMPLATES = [rp_exec, rp_tech, rp_risk]


# ══════════════════════════════════════════════════════════════════════════
# BUCKET ngoai_le — câu bẫy (trap)
# ══════════════════════════════════════════════════════════════════════════
OFF_TOPIC = [
    "Hôm nay thời tiết Hà Nội thế nào?",
    "Gợi ý cho tôi một công thức nấu phở bò ngon.",
    "Dịch bài hát tiếng Hàn này sang tiếng Việt giúp tôi.",
    "Tỷ giá USD hôm nay bao nhiêu?",
    "Viết cho tôi một bài thơ về mùa thu.",
    "Đội tuyển Việt Nam đá với ai tối nay?",
    "Cách trồng lan hồ điệp trong nhà?",
    "Giới thiệu vài địa điểm du lịch Đà Nẵng.",
    "Hôm nay có phim gì hay đang chiếu rạp?",
    "Giá vàng SJC sáng nay thế nào?",
    "Cho tôi lịch thi đấu vòng loại World Cup.",
    "Hướng dẫn pha cà phê sữa đá chuẩn vị.",
]
FAKE_PRODUCTS = [
    "SuperSecure Firewall XZ-9000", "QuantumMail Server 12", "NebulaDB Enterprise 7",
    "HyperGuard IPS 3000", "OmniAuth Gateway 5", "TitanCache Cluster 8",
    "AeroShield WAF 2100", "CryptoVault Storage 4", "SentinelMesh EDR 11",
    "PolarStack Orchestrator 6", "IronGate VPN 900", "SkyForge CI 15",
]


def trap_fake_cve(fake_id, rng):
    return {"input": f"{fake_id} là lỗ hổng gì? Cho tôi biết mức độ nghiêm trọng và cách khắc phục.",
            "expected": {"must_contain_any": NOTFOUND_ANY, "language": "vi", "min_words": 15},
            "tags": ["trap", "fake_cve"], "notes": f"GT: phải từ chối — {fake_id} không tồn tại"}


def trap_offtopic(question, rng):
    return {"input": question,
            "expected": {"must_contain_any": OFFTOPIC_ANY, "language": "vi", "min_words": 10},
            "tags": ["trap", "off_topic"], "notes": "GT: phải từ chối — ngoài phạm vi ATTT"}


def trap_fake_product(product, rng):
    return {"input": f"Liệt kê toàn bộ CVE đã biết của {product} trong năm nay.",
            "expected": {"must_contain_any": NOTFOUND_ANY, "language": "vi", "min_words": 15},
            "tags": ["trap", "zero_hit"], "notes": f"GT: phải từ chối — {product} không có thật"}


CVE_RE = re.compile(r"CVE-\d{4}-\d{4,7}")
