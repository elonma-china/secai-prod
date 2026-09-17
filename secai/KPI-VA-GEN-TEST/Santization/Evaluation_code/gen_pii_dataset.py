#!/usr/bin/env python3
"""gen_pii_dataset.py — Sinh bộ test PII/Secret có nhãn span (True Label) cho R-SEC-PII.

Mẫu = văn bản ngữ cảnh ATTT thật (config, log, incident, code, chat) + PII/secret chèn vào,
kèm nhãn entities. Có **hard negatives** (chuỗi giống PII nhưng KHÔNG phải: CVE id, version,
hash, placeholder password) để đo Precision/False-Positive.

Output: Dataset/sanitization_pii.jsonl  (mỗi dòng {id, text, entities:[{type,value}], kind})
Usage: python3 gen_pii_dataset.py [--n 500] [--seed 42]   # HĐ yêu cầu 500 mẫu
"""
import argparse
import json
import random
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "Dataset" / "sanitization_pii.jsonl"

FIRST = ["nguyen", "tran", "le", "pham", "hoang", "admin", "devops", "secops", "john", "alice", "bob", "kaito"]
DOMAIN = ["corp.vn", "company.com", "internal.local", "gov.vn", "bank.vn", "mail.io", "secai.vn"]
PRODUCTS = ["Apache", "Nginx", "PostgreSQL", "Redis", "Jenkins", "GitLab", "WordPress", "Tomcat"]


def rand_email(r):
    return f"{r.choice(FIRST)}.{r.choice(['it','sec','dev','ops'])}@{r.choice(DOMAIN)}"


def rand_ipv4(r):
    return ".".join(str(r.randint(1, 254)) for _ in range(4))


def rand_phone(r):
    return r.choice(["0", "+84"]) + "".join(str(r.randint(0, 9)) for _ in range(9))


def rand_apikey(r):
    kind = r.choice(["aws", "openai", "github", "google", "jwt", "slack"])
    al = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
    all = al + "abcdefghijklmnopqrstuvwxyz"
    if kind == "aws":
        return "AKIA" + "".join(r.choice(al) for _ in range(16))
    if kind == "openai":
        return "sk-" + "".join(r.choice(all) for _ in range(40))
    if kind == "github":
        return "ghp_" + "".join(r.choice(all) for _ in range(36))
    if kind == "google":
        return "AIza" + "".join(r.choice(all + "-_") for _ in range(35))
    if kind == "slack":
        return "xoxb-" + "".join(r.choice("0123456789") for _ in range(12)) + "-" + "".join(r.choice(all) for _ in range(24))
    return ("eyJ" + "".join(r.choice(all + "_-") for _ in range(20)) + "." +
            "".join(r.choice(all + "_-") for _ in range(24)) + "." +
            "".join(r.choice(all + "_-") for _ in range(22)))


def rand_password(r):
    base = r.choice(["Sup3r", "Adm1n", "Secr3t", "P@ss", "Qwerty", "Vault"])
    return base + "".join(r.choice("!@#$%0123456789") for _ in range(r.randint(4, 8)))


# Carrier templates: {pii} placeholders thay bằng giá trị có nhãn
POS_TEMPLATES = [
    ("config", 'DB_HOST = "{ip}"\nDB_PASSWORD = "{password}"\nADMIN_EMAIL = "{email}"',
     ["ip", "password", "email"]),
    ("config", 'smtp_server: {ip}\nsmtp_user: {email}\nsmtp_pass: {password}\napi_key: {apikey}',
     ["ip", "email", "password", "apikey"]),
    ("log", '[2026-06-12 10:33:01] login attempt from {ip} user {email} failed; contact {phone}',
     ["ip", "email", "phone"]),
    ("code", 'const AWS_ACCESS_KEY = "{apikey}";\nconst dbPass = "{password}"; // FIXME remove',
     ["apikey", "password"]),
    ("incident", 'Sự cố: máy chủ {ip} bị truy cập trái phép. Báo cho {email} hoặc gọi {phone}.',
     ["ip", "email", "phone"]),
    ("chat", 'gửi tạm cái token cho bạn nhé: {apikey} — mật khẩu prod là {password}',
     ["apikey", "password"]),
    ("env", 'OPENAI_API_KEY={apikey}\nSMTP_PASSWORD={password}\nNOTIFY_EMAIL={email}',
     ["apikey", "password", "email"]),
    ("report", 'Phát hiện hard-coded credential trong source: api_key={apikey}, liên hệ dev {email}.',
     ["apikey", "email"]),
    ("log", 'GET /api?user={email}&from={ip} 200 — admin hotline {phone}', ["email", "ip", "phone"]),
    ("code", 'jwt = "{apikey}"\nfallback_password = "{password}"', ["apikey", "password"]),
]

# Hard negatives: KHÔNG có PII thật (chuỗi gây nhiễu)
NEG_TEMPLATES = [
    'Lỗ hổng CVE-2024-3272 (CWE-798) trong {prod} cho phép RCE từ xa.',
    'Nâng cấp {prod} lên phiên bản 192.168 không hợp lệ; dùng v2.34.1 thay thế.',
    'Hash SHA-256: a3f5b2c1d4e6f7081920aabbccddeeff00112233445566778899aabbccddeeff (không phải secret).',
    'Cấu hình mẫu: DB_PASSWORD = "changeme" — placeholder, hãy thay khi triển khai.',
    'Tài liệu tham chiếu CAPEC-66, kỹ thuật T1059, port 8080 mở trên dịch vụ {prod}.',
    'Mức CVSS 9.8 cho lỗ hổng buffer overflow trong {prod} 2024 build 31002.',
    'password = "<your_password_here>" # đặt biến môi trường, đừng hard-code',
    'Phiên bản firmware 10.0.1.2024 của {prod} đã được vá; xem changelog.',
    'Mã định danh giao dịch TXN-2024-0099887766 (không phải số điện thoại).',
    'OWASP Top 10: A03 Injection, A07 Auth Failures — kiểm tra {prod}.',
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=500)  # HĐ nghiệm thu yêu cầu 500 mẫu
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()
    r = random.Random(args.seed)
    OUT.parent.mkdir(parents=True, exist_ok=True)

    n_pos = int(args.n * 0.72)
    n_neg = args.n - n_pos
    rows = []
    gen = {"ip": rand_ipv4, "email": rand_email, "phone": rand_phone,
           "apikey": lambda r: rand_apikey(r), "password": rand_password}

    for i in range(n_pos):
        kind, tmpl, types = POS_TEMPLATES[i % len(POS_TEMPLATES)]
        vals = {}
        for t in set(types):
            vals[t] = gen[t](r)
        text = tmpl.format(**{t: vals[t] for t in vals}, prod=r.choice(PRODUCTS))
        ents = [{"type": "api_key" if t == "apikey" else t, "value": vals[t]} for t in types]
        rows.append({"id": f"pii_pos_{i:04d}", "kind": kind, "text": text, "entities": ents})

    for i in range(n_neg):
        text = NEG_TEMPLATES[i % len(NEG_TEMPLATES)].format(prod=r.choice(PRODUCTS))
        rows.append({"id": f"pii_neg_{i:04d}", "kind": "negative", "text": text, "entities": []})

    r.shuffle(rows)
    with OUT.open("w", encoding="utf-8") as f:
        for x in rows:
            f.write(json.dumps(x, ensure_ascii=False) + "\n")

    import collections
    tc = collections.Counter(e["type"] for x in rows for e in x["entities"])
    print(f"sanitization_pii.jsonl: {len(rows)} mẫu ({n_pos} pos / {n_neg} neg) → {OUT}")
    print("entities theo type:", dict(tc))


if __name__ == "__main__":
    main()
