"""A model that moves placeholders (clause re-ordering or a role swap such as "from A to B")
is never accepted blindly: the chunk is split so each marker stays in its own sentence,
and the strict in-order check is unchanged."""
import asyncio
import re

import pytest

from secai_gateway.translation.service import translate_blocks
from secai_gateway.translation.technical_tokens import protect_literals, restore_literals
from secai_gateway.translation.types import DocumentBlock, TranslationError

MARKER = re.compile(r"⟦T[0-9a-f]{24}:\d+⟧")
SOURCE = "Contact ciso@example.org if the threshold exceeds 10.0.0.5 during the quarterly review of every system."


class Ctx:
    def __init__(self, fn):
        self.fn, self.calls = fn, []

    async def checkpoint(self, *_a, **_k):
        pass

    async def translate(self, text, _s, _t):
        self.calls.append(text)
        return self.fn(text)


def run(fn):
    block = DocumentBlock(id="b1", kind="paragraph", order=0, text=SOURCE, metadata={})
    ctx = Ctx(fn)
    return asyncio.run(translate_blocks((block,), "en", "vi", context=ctx)), ctx


def test_reordered_markers_are_recovered_by_splitting_without_changing_places():
    def model(text):
        found = MARKER.findall(text)
        if len(found) >= 2:  # the model swaps the two markers when it sees both at once
            return "Liên hệ " + found[1] + " nếu vượt " + found[0] + "."
        return "Dịch: " + text

    (out,), ctx = run(model)
    assert out.translated_text.index("ciso@example.org") < out.translated_text.index("10.0.0.5")
    assert len(ctx.calls) > 1  # the chunk was split


def test_restore_still_rejects_reversed_markers():
    protected = protect_literals("CVE-2024-1234 and CVE-2024-1235")
    first, second = MARKER.findall(protected.text)
    with pytest.raises(TranslationError) as exc:
        restore_literals(second + first, protected)
    assert exc.value.code == "TOKEN_INTEGRITY_FAILED"
