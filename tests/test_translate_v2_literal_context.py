"""Literal-integrity must judge technical tokens, not the prose next to them.

Regression for TOKEN_INTEGRITY_FAILED at block 2/18 on the 1000-word fixtures
(FILE-vi-en, FILE-vi-fr, FILE-ja-vi, FILE-ko-vi, FILE-zh-Hans-vi, 2026-10-09):
block index 2 is the only block holding literals, and the old check re-ran
``literal_spans`` on the translated text, where the version pattern absorbs the
preceding ASCII word ("from 198.51.100.23", "IP 198.51.100.23", "bản 3.4.1").
Every resend failed identically, so the retry budget could not help.
"""
import re

import pytest

from secai_gateway.translation.service import translate_blocks
from secai_gateway.translation.types import DocumentBlock, TranslationError

_MARK = re.compile(r"⟦T[0-9a-f]{24}:\d+⟧")


def block(text):
    return DocumentBlock(id="b000002", kind="paragraph", order=2, text=text, metadata={})


class Model:
    """Perfectly placeholder-preserving model with a fixed target rendering."""

    model_context_tokens = 8192
    literal_integrity_max_retries = 2

    def __init__(self, template):
        self.template = template
        self.calls = 0

    async def checkpoint(self, *_args, **_kwargs):
        pass

    async def translate(self, text, source_lang, target_lang):
        self.calls += 1
        marks = _MARK.findall(text)
        return self.template.format(*marks)


VI = ("Đội vận hành phát hiện lưu lượng bất thường từ địa chỉ 198.51.100.23 cố gắng khai thác "
      "lỗ hổng CVE-2024-21412 trên máy chủ chạy phiên bản phần mềm 3.4.1. Kẻ tấn công gửi "
      "yêu cầu tới https://portal.noi-bo.example.com/api/v1/upload nhằm tải tệp.")
JA = ("運用チームが198.51.100.23というアドレスからの異常なトラフィックを検出し、バージョン3.4.1の"
      "ソフトウェアのCVE-2024-21412を悪用。https://portal.noi-bo.example.com/api/v1/upload へ送信。")


@pytest.mark.parametrize("source,src,tgt,template", [
    (VI, "vi", "en", "The team detected abnormal traffic from address {0} exploiting {1} on a "
                     "server running software version 3.4.1. The attacker sent requests to {2}."),
    (VI, "vi", "fr", "L'équipe a détecté un trafic anormal provenant de l'adresse {0} exploitant {1} "
                     "sur un serveur exécutant la version 3.4.1. L'attaquant a envoyé des requêtes à {2}."),
    (JA, "ja", "vi", "Nhóm vận hành phát hiện lưu lượng bất thường từ địa chỉ IP {0}, phần mềm "
                     "phiên bản 3.4.1 bị khai thác {1}. Gửi tới {2}."),
], ids=["FILE-vi-en", "FILE-vi-fr", "FILE-ja-vi"])
@pytest.mark.asyncio
async def test_natural_prose_next_to_literal_is_accepted(source, src, tgt, template):
    model = Model(template)
    result = await translate_blocks((block(source),), src, tgt, context=model)
    out = result[0].translated_text
    for literal in ("198.51.100.23", "CVE-2024-21412", "https://portal.noi-bo.example.com/api/v1/upload"):
        assert out.count(literal) == 1
    assert model.calls == 1


@pytest.mark.parametrize("invented", ["CVE-2025-99999", "198.51.100.24", "version 3.4.2",
                                      "https://evil.example.com/x"])
@pytest.mark.asyncio
async def test_invented_or_mutated_token_beside_literal_still_rejected(invented):
    model = Model("Traffic from {0} exploiting {1} via {2}. Also " + invented + " now.")
    with pytest.raises(TranslationError) as exc:
        await translate_blocks((block(VI),), "vi", "en", context=model)
    assert exc.value.code == "TOKEN_INTEGRITY_FAILED"
    assert model.calls == 3
