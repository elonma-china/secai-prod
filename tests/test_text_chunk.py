"""split_for_translation must be loss-less (rejoin == original) and respect the budget."""
from __future__ import annotations

import pytest

from secai_gateway.api.text_chunk import split_for_translation


def _rejoin(chunks):
    return "".join(c + s for c, s in chunks)


@pytest.mark.parametrize("text", [
    "",
    "ngắn gọn một dòng",
    "\n\n".join(f"Đoạn số {i} nói về một lỗ hổng bảo mật nghiêm trọng." for i in range(20)),
    "A" * 100 + "\n\n" + "B" * 100,
    "Câu một. Câu hai! Câu ba? " * 200,                 # one huge paragraph → sentence split
    "x" * 20000,                                          # one huge token → hard split
    "para1\n\n\n\npara2 with blank between",             # empty paragraph in the middle
])
def test_rejoin_is_lossless(text):
    chunks = split_for_translation(text, budget=500)
    assert _rejoin(chunks) == text


def test_short_input_is_single_chunk():
    assert split_for_translation("hello world", budget=6000) == [("hello world", "")]


def test_every_chunk_within_budget():
    text = "\n\n".join(f"Đoạn {i}: " + "nội dung bảo mật " * 40 for i in range(15))
    budget = 800
    chunks = split_for_translation(text, budget=budget)
    assert len(chunks) > 1
    assert all(len(c) <= budget for c, _ in chunks)
    assert _rejoin(chunks) == text


def test_paragraph_boundaries_preferred():
    # small paragraphs get grouped, split points land on \n\n (not mid-sentence)
    text = "\n\n".join(f"Đoạn {i}." for i in range(30))
    chunks = split_for_translation(text, budget=60)
    assert all(sep in ("\n\n", "") for _, sep in chunks)
    assert _rejoin(chunks) == text
