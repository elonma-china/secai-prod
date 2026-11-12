"""Vietnamese output such as "Mật khẩu là một chuỗi dài" is ordinary prose, not a leaked secret.
Re-scanning a translation must not treat the word after "mật khẩu là" as an invented credential,
while a secret-looking value (digit/symbol) the source never contained must still be rejected."""
import asyncio

import pytest

from secai_gateway.translation.service import translate_blocks
from secai_gateway.translation.technical_tokens import literal_spans
from secai_gateway.translation.types import DocumentBlock, TranslationError


class Ctx:
    def __init__(self, reply):
        self.reply = reply

    async def checkpoint(self, *_a, **_k):
        pass

    async def translate(self, _text, _s, _t):
        return self.reply


def run(source, reply):
    block = DocumentBlock(id="b1", kind="paragraph", order=0, text=source, metadata={})
    return asyncio.run(translate_blocks((block,), "en", "vi", context=Ctx(reply)))[0].translated_text


def test_plain_word_after_mat_khau_la_is_not_a_credential_in_a_translation():
    out = run("The password is a long random string.", "Mật khẩu là một chuỗi ngẫu nhiên dài.")
    assert out == "Mật khẩu là một chuỗi ngẫu nhiên dài."


def test_invented_secret_after_mat_khau_la_is_still_rejected():
    with pytest.raises(TranslationError) as exc:
        run("The password is a long random string.", "Mật khẩu là Zq81!kd.")
    assert exc.value.code == "TOKEN_INTEGRITY_FAILED"


def test_source_side_masking_of_vietnamese_password_is_unchanged():
    text = "Mật khẩu là hunter2 trên máy chủ."
    spans = literal_spans(text)
    assert [text[a:b] for a, b in spans] == ["hunter2"]
    # a plain word is still masked on the source side (over-masking is lossless)
    assert [text2[a:b] for text2 in ["Mật khẩu là một chuỗi."] for a, b in literal_spans(text2)] == ["một"]
