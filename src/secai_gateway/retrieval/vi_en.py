"""Vietnamese → English security vocabulary, for the BM25 side of knowledge retrieval only.

The knowledge chunks are English. A Vietnamese question shares no tokens with them, so the
sparse (BM25) leg returns nothing and retrieval silently degrades to dense-only. Appending the
English terms of the Vietnamese security words found in the query lets BM25 match. Dense and
rerank still see the original question.
"""
from __future__ import annotations

import re
import unicodedata


def fold(text: str) -> str:
    """Lower-case, strip diacritics (đ → d): 'Mật khẩu' and 'mat khau' compare equal."""
    nfd = unicodedata.normalize("NFD", (text or "").lower().replace("đ", "d"))
    return "".join(c for c in nfd if not unicodedata.combining(c))


# (folded Vietnamese pattern, English terms appended to the BM25 query)
_GLOSSARY: tuple[tuple[str, str], ...] = (
    (r"quen mat khau", "forgot password reset"), (r"mat khau", "password credential"),
    (r"xac thuc da yeu to|da yeu to", "multifactor authentication mfa"), (r"xac thuc", "authentication"),
    (r"phan quyen|kiem soat truy cap", "authorization access control"), (r"ma hoa|luu tru du lieu nhay cam", "encryption cryptographic storage"),
    (r"quan ly khoa", "key management"), (r"bi mat|secret", "secrets management"),
    (r"xu ly loi", "error handling"), (r"nhat ky", "logging"), (r"lo hong", "vulnerability"),
    (r"phong (?:chong|tranh|ngua)|ngan (?:chan|ngua)", "prevention defense"), (r"khac phuc|va loi", "remediation mitigation"),
    (r"phien (?:dang nhap|lam viec)?", "session management"), (r"dau vao|kiem tra du lieu", "input validation"),
    (r"tai len|upload", "file upload"), (r"chuoi cung ung", "supply chain"), (r"phu thuoc|thu vien", "dependency"),
    (r"thong tin nhay cam|lo thong tin", "sensitive information disclosure"), (r"tan cong", "attack"),
    (r"gia mao yeu cau", "request forgery"), (r"chen ma|tiem ma", "injection"), (r"chong", "prevention"),
    (r"bao mat", "security"), (r"an toan", "secure"), (r"huong dan", "guide"), (r"dich vu web", "web service"),
    (r"mo hinh de doa", "threat modeling"), (r"container", "container docker"), (r"khong tin cay", "untrusted"),
    (r"chu ky so|chung chi", "certificate signature"), (r"gioi han toc do", "rate limiting"),
)
_COMPILED = tuple((re.compile(p), en) for p, en in _GLOSSARY)


def expand_for_bm25(query: str) -> str:
    """The query plus English terms for any Vietnamese security words in it (unchanged if none).
    Works on diacritic-stripped text, so unaccented Vietnamese ("cach phong chong") expands too."""
    folded = fold(query)
    extra = [en for rx, en in _COMPILED if rx.search(folded)]
    return f"{query} {' '.join(extra)}" if extra else query
