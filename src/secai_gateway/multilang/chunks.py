from __future__ import annotations

import re
from typing import Callable

from .errors import TranslationError
from .literals import Protected


SENTENCE_END = re.compile(r"\n{2,}|(?<=[.!?。！？؟])\s+|(?<=[。！？؟])(?=\S)")
ATOMS = re.compile(r"⟦ML-[A-Za-z0-9_-]+-\d+⟧|(?:(?!⟦ML-)\S)+|\s+")


def _pieces(text: str, count: Callable[[str], int], budget: int,
            tokens: tuple[str, ...]) -> list[tuple[str, str]]:
    """Sentence-first split, then words and finally characters; tokens remain whole."""
    units: list[tuple[str, str]] = []
    last = 0
    for match in SENTENCE_END.finditer(text):
        units.append((text[last:match.start()], match.group(0)))
        last = match.end()
    units.append((text[last:], ""))
    result: list[tuple[str, str]] = []
    for unit, sep in units:
        if count(unit) <= budget:
            result.append((unit, sep))
            continue
        parts: list[str] = []
        current = ""
        for atom_match in ATOMS.finditer(unit):
            atom = atom_match.group(0)
            if count(atom) > budget:
                if atom in tokens:
                    raise TranslationError("TOKEN_LIMIT_EXCEEDED")
                atom_parts = list(atom)
            else:
                atom_parts = [atom]
            for piece in atom_parts:
                if count(piece) > budget:
                    raise TranslationError("TOKEN_LIMIT_EXCEEDED")
                if current and count(current + piece) > budget:
                    parts.append(current)
                    current = ""
                current += piece
        if current:
            parts.append(current)
        result.extend((part, sep if i == len(parts) - 1 else "")
                      for i, part in enumerate(parts))
    return result


def split(protected: Protected, *, count: Callable[[str], int], budget: int,
          max_chunks: int) -> list[tuple[str, str, tuple[str, ...]]]:
    pieces = _pieces(protected.masked, count, budget, protected.tokens)
    chunks: list[tuple[str, str, tuple[str, ...]]] = []
    current = ""
    pending_sep = ""

    def append(content: str, sep: str) -> None:
        if content:
            found = tuple(token for token in protected.tokens if token in content)
            chunks.append((content, sep, found))

    for piece, sep in pieces:
        if not piece:
            pending_sep += sep
            continue
        candidate = current + pending_sep + piece
        if current and count(candidate) > budget:
            append(current, pending_sep)
            current, pending_sep = piece, sep
        else:
            current, pending_sep = candidate, sep
    append(current, pending_sep)
    if len(chunks) > max_chunks:
        raise TranslationError("TEXT_TOO_LONG")
    if any(count(content) > budget for content, _, _ in chunks):
        raise TranslationError("TOKEN_LIMIT_EXCEEDED")
    return chunks
