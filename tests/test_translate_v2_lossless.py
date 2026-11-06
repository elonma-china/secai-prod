"""Lossless v2 (file/image) and v3 (text) translation: nothing redacted, deleted or falsely rejected."""
from __future__ import annotations

import asyncio
import re
from io import BytesIO
from pathlib import Path
from zipfile import ZipFile

import pytest
from PIL import Image

from secai_gateway.multilang.config import EXPECTED_MODEL
from secai_gateway.multilang.literals import protect
from secai_gateway.multilang.provider import GenerationResult
from secai_gateway.multilang.service import TranslationRequest, translate as translate_v3
from secai_gateway.translation.service import translate_blocks
from secai_gateway.translation.settings import TranslationSettings
from secai_gateway.translation.technical_tokens import literal_spans, protect_literals, restore_literals
from secai_gateway.translation.types import DocumentBlock, TranslationError

from tests.multilang.test_core import count, settings as ml_settings

_FIXTURE_DIRS = (Path(__file__).resolve().parents[1] / "module-map/translate/fixtures/files",
                 Path("/home/ict/AIEngines/secai/secai-agent-gateway/module-map/translate/fixtures/files"))
_FIXTURE = next((d / "sensitive-vi.txt" for d in _FIXTURE_DIRS if (d / "sensitive-vi.txt").exists()), None)
LITERALS = [
    "Zk9#mPq2!vL7", "Tr0ub4dor&3_demo", "sk_live_51HqDemo0000AbCdEf1234567890",
    "eyJhbGciOiJIUzI1NiJ9.demo.payload123", "S3cr3t_Pass", "admin_demo", "soc.team@example.com",
    "0912 345 678", "203.0.113.45", "CVE-2024-21412",
    "https://portal.noi-bo.example.com/login?next=/admin",
    "postgres://dbuser:S3cr3t_Pass@10.0.0.12:5432/appdb",
]
_LABELS = [("Tài khoản:", "Account:"), ("mật khẩu:", "password:"), (" với ", " with "),
           ("Khóa API của dịch vụ giám sát là", "The monitoring API key is"),
           ("số điện thoại", "phone number"), ("Chuỗi kết nối cơ sở dữ liệu:", "Database connection string:"),
           ("Mã lỗ hổng liên quan là", "Related vulnerability:"), ("và token:", "and token:")]


def _fake_mt(text: str) -> str:
    for a, b in _LABELS:
        text = text.replace(a, b)
    return text


class _Ctx:
    model_context_tokens = 8192
    literal_integrity_max_retries = 2

    def __init__(self, fn=_fake_mt):
        self.fn, self.calls = fn, 0

    async def checkpoint(self, *a, **k):
        pass

    async def translate(self, chunk, source, target):
        self.calls += 1
        return self.fn(chunk)


def _v2(text, fn=_fake_mt, source="vi", target="en"):
    ctx = _Ctx(fn)
    block = DocumentBlock(id="b", kind="paragraph", order=0, text=text, metadata={})
    return asyncio.run(translate_blocks((block,), source, target, context=ctx))[0].translated_text, ctx


@pytest.mark.skipif(_FIXTURE is None, reason="sensitive-vi.txt fixture not present")
def test_v2_sensitive_fixture_all_lines_translate_with_literals_verbatim():
    lines = _FIXTURE.read_text(encoding="utf8").split("\n")
    out_lines = []
    for line in lines:
        if not line.strip():
            continue
        out, ctx = _v2(line)
        assert ctx.calls == 1, line  # no integrity retries needed
        out_lines.append(out)
        for lit in LITERALS:
            if lit in line:
                assert lit in out, (lit, out)
    joined = "\n".join(out_lines)
    for lit in LITERALS:
        assert lit in joined
    assert "Account:" in joined and "password:" in joined
    assert "with " in joined  # "với" was not eaten by the ssh command rule


@pytest.mark.skipif(_FIXTURE is None, reason="sensitive-vi.txt fixture not present")
def test_v3_sensitive_fixture_literals_verbatim():
    class Echo:
        async def generate(self, request):
            return GenerationResult("译文 " + request.user, "stop", EXPECTED_MODEL)

    text = _FIXTURE.read_text(encoding="utf8")
    result = asyncio.run(translate_v3(TranslationRequest(text, "vi", "zh"), llm=Echo(),
                                      settings=ml_settings(), token_count=count))
    for lit in LITERALS:
        assert lit in result.translated_text, lit


# (A) -----------------------------------------------------------------------------------
def test_a_label_translated_next_to_restored_value_is_not_an_invented_literal():
    out, ctx = _v2("mật khẩu: Zk9#mPq2!vL7 là bí mật")
    assert "password:" not in out or "Zk9#mPq2!vL7" in out
    assert "Zk9#mPq2!vL7" in out and ctx.calls == 1


def test_a_invented_literal_in_prose_still_fails():
    with pytest.raises(TranslationError) as exc:
        _v2("Máy chủ chính là 10.0.0.5 ở đây", fn=lambda t: t.replace("Máy chủ chính là", "Main host 10.9.9.9 is"))
    assert exc.value.code == "TOKEN_INTEGRITY_FAILED"


def test_a_invented_literal_adjacent_to_restored_one_still_fails():
    # Overlap is judged per span: an invented, separate IP is not hidden by a restored one.
    with pytest.raises(TranslationError):
        _v2("Máy chủ 10.0.0.5 ở đây", fn=lambda t: "Host 10.9.9.9 and " + t.replace("Máy chủ", "Server"))


# (B) -----------------------------------------------------------------------------------
def _roundtrip_literals(source):
    spans = literal_spans(source)
    return [source[a:b] for a, b in spans]


def test_b_generic_url_scheme_is_one_literal():
    uri = "postgres://dbuser:S3cr3t_Pass@10.0.0.12:5432/appdb"
    assert _roundtrip_literals(f"Chuỗi: {uri}") == [uri]
    assert _roundtrip_literals("mongodb+srv://u:p@h.example.com/db ok")[0].startswith("mongodb+srv://")
    p = protect_literals(f"x {uri}")
    assert restore_literals(p.text, p) == f"x {uri}"


def test_b_credential_value_excludes_trailing_punctuation():
    assert "admin_demo" in _roundtrip_literals("Tài khoản: admin_demo, mật khẩu: abc;")
    assert "admin_demo," not in _roundtrip_literals("Tài khoản: admin_demo, x")
    assert "abc" in _roundtrip_literals("mật khẩu: abc; hết")


def test_b_command_rule_does_not_swallow_vietnamese_word():
    source = "ssh root@203.0.113.45 -p 2222 với password=x"
    masked = protect_literals(source).text
    assert " với " in masked


@pytest.mark.parametrize("source,literal", [
    ("liên hệ soc.team@example.com nhé", "soc.team@example.com"),
    ("gọi 0912 345 678.", "0912 345 678"),
    ("pass: Abc123!", "Abc123!"), ("pw=hunter2 xong", "hunter2"), ("user: root", "root"),
    ("login: bob", "bob"), ("mật khẩu là Abc@123 nhé", "Abc@123"), ("mk: s3cret", "s3cret"),
])
def test_b_new_patterns_mask_values(source, literal):
    assert literal in _roundtrip_literals(source)


def test_b_weak_labels_not_enforced_on_output():
    out, _ = _v2("Người dùng: Máy chủ chính", fn=lambda t: t.replace("Người dùng:", "User:").replace("Máy chủ chính", "Primary server"))
    assert out == "User: Primary server"


# (C) -----------------------------------------------------------------------------------
def _v3_literals(text):
    return protect(text).literals


def test_c_overlapping_matches_merge_into_one_span():
    url = "https://a.com/x?token=abc123def"
    assert _v3_literals(f"xem {url} đi") == (url,)
    assert protect("mật khẩu: Abc@123 ok").literals == ("Abc@123",)
    q = protect("Abc@123 và 456")
    assert q.restore(q.masked) == "Abc@123 và 456"


def test_c_v3_trailing_punctuation_not_in_literal():
    assert protect("xem https://a.example.com/p, rồi").literals == ("https://a.example.com/p",)


# (D) -----------------------------------------------------------------------------------
@pytest.mark.parametrize("source", ["  indented line\n\nSecond line here.", "\n\nStart after blank line.",
                                    "Trailing blanks here.\n\n", "    def faire():\n        retourne vrai\n"])
def test_d_edge_whitespace_preserved(source):
    class Echo:
        async def generate(self, request):
            return GenerationResult("  这是一段中文译文内容，用于测试。" * 3 + "\n", "stop", EXPECTED_MODEL)

    result = asyncio.run(translate_v3(TranslationRequest(source, "vi", "zh"), llm=Echo(),
                                      settings=ml_settings(), token_count=count))
    lead = source[:len(source) - len(source.lstrip())]
    trail = source[len(source.rstrip()):]
    assert result.translated_text.startswith(lead + "这是")  # model's own edge whitespace dropped
    assert result.translated_text.endswith("。" + trail)


# (E) -----------------------------------------------------------------------------------
def _docx(body_xml: str) -> bytes:
    ns = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
    buf = BytesIO()
    with ZipFile(buf, "w") as z:
        z.writestr("[Content_Types].xml",
                   '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                   '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                   '<Default Extension="xml" ContentType="application/xml"/>'
                   '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>')
        z.writestr("_rels/.rels",
                   '<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                   '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>')
        z.writestr("word/document.xml", f'<?xml version="1.0"?><w:document {ns}><w:body>{body_xml}</w:body></w:document>')
    return buf.getvalue()


def _extract_docx(tmp_path, body):
    from secai_gateway.translation import documents
    path = tmp_path / "t.docx"
    path.write_bytes(_docx(body))
    fn = getattr(documents, "extract_docx")
    return fn(path, TranslationSettings(_env_file=None))


def test_e_no_break_hyphen_kept(tmp_path):
    body = ("<w:p><w:r><w:t>CVE</w:t><w:noBreakHyphen/><w:t>2024</w:t><w:noBreakHyphen/>"
            "<w:t>1234 here</w:t></w:r></w:p>")
    doc = _extract_docx(tmp_path, body)
    assert [b.text for b in doc.blocks] == ["CVE-2024-1234 here"]


def test_e_sdt_content_control_surfaces_warning(tmp_path):
    body = ("<w:p><w:r><w:t>visible</w:t></w:r></w:p>"
            "<w:sdt><w:sdtContent><w:p><w:r><w:t>inside</w:t></w:r></w:p></w:sdtContent></w:sdt>")
    doc = _extract_docx(tmp_path, body)
    assert any(w.code == "UNSUPPORTED_PART" and "w:sdt" in w.message for w in doc.warnings)


# (F) -----------------------------------------------------------------------------------
def test_f_transparent_png_composited_on_white(tmp_path):
    from secai_gateway.translation.ocr_preprocess import preprocess_image
    src = tmp_path / "t.png"
    image = Image.new("RGBA", (40, 30), (0, 0, 0, 0))
    for x in range(10, 20):
        image.putpixel((x, 15), (0, 0, 0, 255))  # opaque black "text"
    image.save(src)
    out = asyncio.run(preprocess_image(src, work_dir=tmp_path / "w"))
    with Image.open(out.path) as prepared:
        gray = prepared.convert("L")
        assert gray.getpixel((0, 0)) > 200  # transparent -> white, not black
        assert gray.getextrema()[0] < 60  # text still dark


# (G) -----------------------------------------------------------------------------------
def test_g_nonce_deterministic_per_salt_and_unguessable_without():
    text = "Kiểm tra CVE-2024-1234 tại 10.0.0.1"
    salt = b"s" * 16
    assert protect(text, salt).masked == protect(text, salt).masked
    assert protect_literals(text, salt).text == protect_literals(text, salt).text
    assert protect(text, salt).masked != protect(text, b"t" * 16).masked
    assert protect(text, salt).masked != protect(text + "!", salt).masked.replace("!", "")
    assert protect(text).masked != protect(text).masked  # no salt -> random
    assert protect_literals(text).text != protect_literals(text).text


def test_g_v2_retry_prompts_identical_within_request():
    seen = []

    def fn(chunk):
        seen.append(chunk)
        return _fake_mt(chunk) if len(seen) > 1 else "garbled"  # first reply drops placeholders

    out, ctx = _v2("Máy chủ 10.0.0.5 ở đây", fn=fn)
    assert ctx.calls == 2 and seen[0] == seen[1] and "10.0.0.5" in out
