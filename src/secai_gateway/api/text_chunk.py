"""Split long translate input into rejoinable chunks so a single-shot model never has to
emit a translation longer than its per-turn output-token cap (which would truncate the
tail). Splits on paragraph boundaries, falling back to sentences then a hard cut for a
single oversized paragraph/sentence.

``split_for_translation`` returns ``[(chunk_text, sep_after), …]`` where ``sep_after`` is the
exact original separator to the next chunk. Separators are preserved losslessly, so
``"".join(chunk + sep for chunk, sep in chunks) == text`` — i.e. no character is dropped;
the caller translates each ``chunk_text`` and rejoins with the same seps.
"""
from __future__ import annotations

import re

_SENTENCE_BREAK = re.compile(r"(?<=[.!?…])\s+")


def _hard_split(s: str, budget: int) -> list[str]:
    """Last resort for one sentence longer than the budget: cut on char boundaries."""
    return [s[i:i + budget] for i in range(0, len(s), budget)] or [""]


def _split_paragraph(para: str, budget: int) -> list[tuple[str, str]]:
    """A single over-budget paragraph → (piece, sep_after) list preserving exact separators.
    Splits on sentence-ending whitespace; hard-cuts any sentence still over budget (the
    cut points carry an empty separator, since nothing separated them in the source)."""
    parts: list[tuple[str, str]] = []
    last = 0
    for m in _SENTENCE_BREAK.finditer(para):
        parts.append((para[last:m.start()], m.group(0)))  # sentence text, then its whitespace
        last = m.end()
    parts.append((para[last:], ""))  # trailing sentence
    out: list[tuple[str, str]] = []
    for piece, sep in parts:
        if len(piece) <= budget:
            out.append((piece, sep))
        else:
            subs = _hard_split(piece, budget)
            for i, sub in enumerate(subs):
                out.append((sub, sep if i == len(subs) - 1 else ""))
    return out


def split_for_translation(text: str, budget: int = 6000) -> list[tuple[str, str]]:
    if not text or len(text) <= budget:
        return [(text, "")]

    # 1. Flatten into units carrying the EXACT separator to the next unit, splitting only
    #    the paragraphs (then sentences) that individually exceed the budget.
    paras = text.split("\n\n")
    units: list[tuple[str, str]] = []
    for pi, para in enumerate(paras):
        after = "" if pi == len(paras) - 1 else "\n\n"
        if len(para) <= budget:
            units.append((para, after))
            continue
        pieces = _split_paragraph(para, budget)
        pieces[-1] = (pieces[-1][0], after)  # last piece inherits the paragraph separator
        units.extend(pieces)

    # 2. Greedily pack consecutive units into chunks under the budget. Each unit's own sep
    #    glues it to the next unit inside the chunk; the chunk's trailing sep is the last
    #    packed unit's sep (used to rejoin the translated chunks).
    chunks: list[tuple[str, str]] = []
    cur_text: str | None = None
    cur_sep = ""
    for text_u, sep_u in units:
        if cur_text is None:
            cur_text, cur_sep = text_u, sep_u
        elif len(cur_text) + len(cur_sep) + len(text_u) <= budget:
            cur_text = cur_text + cur_sep + text_u
            cur_sep = sep_u
        else:
            chunks.append((cur_text, cur_sep))
            cur_text, cur_sep = text_u, sep_u
    if cur_text is not None:
        chunks.append((cur_text, cur_sep))
    return chunks
