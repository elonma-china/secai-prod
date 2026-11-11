"""Tool-less, literal-preserving block translation."""
from __future__ import annotations

import asyncio
import re
import secrets
import unicodedata
from functools import lru_cache

from lingua import Language, LanguageDetectorBuilder
import regex

from .technical_tokens import (ProtectedText, _CREDENTIAL, _CREDENTIAL_WEAK, _IP, _VERSION, _canonical_markers, literal_spans, protect_literals,
                               restore_literals_with_ranges)
from .types import (DocumentBlock, ProcessingContext, SourceLang, TargetLang,
                    TranslatedBlock, TranslationError)

_PLACEHOLDER = re.compile(r"⟦T[0-9a-f]{24}:\d+⟧")
_LANGUAGES = {
    "vi": Language.VIETNAMESE, "en": Language.ENGLISH, "zh": Language.CHINESE,
    "ja": Language.JAPANESE, "ko": Language.KOREAN, "fr": Language.FRENCH,
    "de": Language.GERMAN, "es": Language.SPANISH, "ru": Language.RUSSIAN,
    "ar": Language.ARABIC,
}
_LANGUAGE_CODES = {language: code for code, language in _LANGUAGES.items()}


def _boundary_graphemes(text: str) -> tuple[str, ...]:
    """Return normalized base characters so combining marks stay with their letters."""
    bases = []
    for grapheme in regex.findall(r"\X", text):
        normalized = unicodedata.normalize("NFC", grapheme)
        bases.append(next((char for char in normalized
                           if not unicodedata.category(char).startswith("M")), normalized[0]))
    return tuple(bases)


def _word_joiner_at_seam(left: tuple[str, ...], right: tuple[str, ...]) -> bool:
    if len(left) < 2 or not left[-2].isalnum():
        return False
    last = left[-1]
    name = unicodedata.name(last, "")
    if (unicodedata.category(last) == "Pc" or "HYPHEN" in name or
            "SOLIDUS" in name or "SLASH" in name):
        return True
    if "APOSTROPHE" in name or "SINGLE QUOTATION MARK" in name:
        # An apostrophe inside a word is not a quotation delimiter. A final
        # matching delimiter, however, must not consume the next word.
        quote_depth = 0
        straight_quotes = 0
        for index, char in enumerate(left):
            interior = (0 < index < len(left) - 1 and left[index - 1].isalpha()
                        and left[index + 1].isalpha())
            if char == "‘":
                quote_depth += 1
            elif char == "’" and quote_depth and not interior:
                quote_depth -= 1
                if index == len(left) - 1:
                    return False
            elif char == "'" and not interior:
                straight_quotes += 1
        if last == "'" and straight_quotes >= 2 and straight_quotes % 2 == 0:
            return False
        prefix = []
        for char in reversed(left[:-1]):
            if not char.isalpha():
                break
            prefix.append(char)
        suffix = []
        for char in right:
            if not char.isalpha():
                break
            suffix.append(char)
        stem = "".join(reversed(prefix)).casefold()
        ending = "".join(suffix).casefold()
        # French elisions and English contractions are bounded forms; an
        # arbitrary one-letter word after a quote is not an apostrophe suffix.
        return bool(ending) and (stem in {"l", "d", "j", "m", "t", "s", "c", "n", "qu"}
                                 or (ending in {"s", "d", "m", "re", "ve", "ll"})
                                 or (ending == "t" and stem.endswith("n")))
    return False


def _spaced_target_needs_separator(previous: str, core: str) -> bool:
    right = _boundary_graphemes(core)
    if core.startswith("⟦"):
        right_word = True
    else:
        right_word = right[0].isalnum()
    if not right_word:
        return False
    if previous.endswith("⟧"):
        return True
    left = _boundary_graphemes(previous)
    last = left[-1]
    category = unicodedata.category(last)
    if last.isalnum():
        return True
    if category in {"Ps", "Pi"} or unicodedata.name(last, "").startswith("INVERTED "):
        return False
    if _word_joiner_at_seam(left, right):
        return False
    if category.startswith("P"):
        # Straight quotes can open or close; an empty quoted span opens.
        return last not in {'"', "'"} or any(char.isalnum() for char in left[:-1])
    return False


def _cjk_target_drops_source_space(previous: str, core: str) -> bool:
    """Remove only a plain-space seam with clear CJK typography on both sides."""
    if previous.endswith("⟧") or core.startswith("⟦"):
        return False
    left, right = _boundary_graphemes(previous), _boundary_graphemes(core)
    last, first = left[-1], right[0]
    def wide_closer(char: str) -> bool:
        category = unicodedata.category(char)
        return (category.startswith("P") and category not in {"Ps", "Pi"} and
                unicodedata.east_asian_width(char) in {"W", "F"})

    if (last.isalpha() and first.isalpha() and
            all(unicodedata.east_asian_width(char) in {"W", "F"} for char in (last, first))):
        return True
    if wide_closer(last):
        return True
    # Fullwidth leading punctuation attaches even after a Latin term.
    return wide_closer(first)


@lru_cache(maxsize=1)
def _detector():
    return LanguageDetectorBuilder.from_languages(*_LANGUAGES.values()).with_low_accuracy_mode().build()


def _prose(text: str) -> str:
    spans = literal_spans(text)
    cursor = 0
    fragments: list[str] = []
    for start, end in spans:
        fragments.append(text[cursor:start])
        cursor = end
    fragments.append(text[cursor:])
    return " ".join(fragments)


def detect_source_language(blocks: tuple[DocumentBlock, ...]) -> TargetLang | None:
    """Pinned offline Lingua 2.0.2 detector; None means literal-only identity input."""
    prose = [_prose(block.text) for block in blocks if block.kind != "literal"]
    corpus = "\n".join(prose)
    letters = sum(char.isalpha() for char in corpus)
    if letters == 0:
        return None
    if letters < 8:
        raise TranslationError("LANGUAGE_DETECTION_FAILED",
                               "Source language could not be determined; specify source_lang.")
    detector = _detector()
    scores = detector.compute_language_confidence_values(corpus)
    if not scores or scores[0].value < 0.8 or (len(scores) > 1 and scores[0].value - scores[1].value < 0.5):
        raise TranslationError("LANGUAGE_DETECTION_FAILED",
                               "Source language could not be determined; specify source_lang.")
    primary = scores[0].language
    competing = 0
    weighted = 0
    for value in prose:
        amount = sum(char.isalpha() for char in value)
        if amount < 8:
            continue
        item_scores = detector.compute_language_confidence_values(value)
        if not item_scores or item_scores[0].value < 0.8:
            continue
        weighted += amount
        if item_scores[0].language != primary:
            competing += amount
    if weighted and competing / weighted >= 0.25:
        raise TranslationError("LANGUAGE_DETECTION_FAILED",
                               "Source language could not be determined; specify source_lang.")
    return _LANGUAGE_CODES[primary]


def _technical_cores(text: str, skip_ranges: tuple[tuple[int, int], ...] = ()) -> list[str]:
    """Technical tokens in ``text`` without the prose word a version match absorbs.

    ``_VERSION`` treats any preceding ASCII word as a product name, so the span
    found around a literal depends on the target-language prose next to it
    ("from 198.51.100.23", "IP 198.51.100.23", "bản 3.4.1"). Comparing whole
    spans between source and translation therefore failed deterministically on
    correct output; the number itself is the token that must not change.
    """
    cores: list[str] = []
    for start, end in literal_spans(text, weak_labels=False, prose_secrets_only=True):
        # Text restored from a placeholder is already verified by count/order in
        # restore_literals; a span that merely overlaps it (a translated label such
        # as "password:" glued to the restored value) is not a new literal.
        if any(start < b and end > a for a, b in skip_ranges):
            continue
        token = text[start:end]
        match = _VERSION.fullmatch(token)
        if match:
            token = token[match.end("product") + 1:]
        cores.append(token)
    return cores


def _occurs_as_token(core: str, source: str) -> bool:
    """True when ``core`` appears in ``source`` as a whole token, not a fragment."""
    return re.search(r"(?<![A-Za-z0-9_.:/\\-])" + re.escape(core) +
                     r"(?![A-Za-z0-9_:/\\-]|\.[A-Za-z0-9])", source) is not None


def _chunks(text: str, maximum: int = 3000) -> tuple[str, ...]:
    """Split at word boundaries without cutting a technical placeholder."""
    pieces: list[str] = []
    for segment in re.split("(" + _PLACEHOLDER.pattern + ")", text):
        if _PLACEHOLDER.fullmatch(segment):
            pieces.append(segment)
        else:
            pieces.extend(re.findall(r"\s+|[^\s]+", segment))
    out: list[str] = []
    current = ""
    for piece in pieces:
        if len(piece) > maximum:
            if current:
                out.append(current)
                current = ""
            if _PLACEHOLDER.fullmatch(piece):
                out.append(piece)
                continue
            current_piece = ""
            for grapheme in regex.findall(r"\X", piece):
                if len(current_piece) + len(grapheme) > maximum:
                    out.append(current_piece)
                    current_piece = ""
                current_piece += grapheme
            if current_piece:
                out.append(current_piece)
            continue
        if len(current) + len(piece) > maximum:
            out.append(current)
            current = ""
        current += piece
    if current:
        out.append(current)
    return tuple(out)


_MIN_RECOVERY_CHARS = 40


def _split_in_half(text: str) -> tuple[str, str] | None:
    """Split ``text`` near its midpoint at a word boundary, never inside a placeholder."""
    pieces = re.findall(r"\s+|[^\s]+", text)
    if len(pieces) < 2:
        return None
    target = len(text) / 2
    running = 0
    best_index = 0
    best_diff = float("inf")
    for index, piece in enumerate(pieces[:-1]):
        running += len(piece)
        diff = abs(running - target)
        if diff < best_diff:
            best_diff = diff
            best_index = index
    left = "".join(pieces[:best_index + 1])
    right = "".join(pieces[best_index + 1:])
    if not left or not right:
        return None
    return left, right


def _is_truncated_prefix(expected: list[str], found: list[str]) -> bool:
    """True only for the early-stop signature: a non-empty, in-order, unbroken
    prefix of the expected markers and nothing else. Any other shape (zero
    markers, a duplicate, a reorder, an invented marker) is a different kind of
    corruption that recovery-by-splitting must not paper over; it still has to
    reach the strict check in ``translate_blocks`` and fail loudly.
    """
    return 0 < len(found) < len(expected) and found == expected[:len(found)]


_PUBLIC_IDENTIFIER = re.compile(r"(?:CVE-\d{4}-\d{4,7}|CWE-\d{1,5})", re.I)


_SAFE_VERSION_CORE = re.compile(r"v?\d+\.\d+(?:\.\d+)?(?:[-+][A-Za-z0-9_.]+)?")


def _is_public_identifier(literal: str) -> bool:
    """Non-sensitive literals the model may see verbatim: product versions and CVE/CWE ids.

    ``_VERSION`` also matches "host 10.0.0.5", so a version only counts when its number is a
    plain X.Y[.Z] and the literal holds no IP or credential. Credentials, IPs, URLs, paths and
    commands are never exposed to the model.
    """
    if _PUBLIC_IDENTIFIER.fullmatch(literal):
        return True
    match = _VERSION.fullmatch(literal)
    if not match:
        return False
    core = literal[match.end("product") + 1:]
    return bool(_SAFE_VERSION_CORE.fullmatch(core) and not _IP.search(literal)
                and not _CREDENTIAL.search(literal) and not _CREDENTIAL_WEAK.search(literal))


def _is_dropped_subsequence(expected: list[str], found: list[str]) -> bool:
    """Strictly fewer markers than expected, all in expected order: the model *dropped*
    some (observed: "a <CVSS v3.1> base score" is silently absorbed into the prose).
    Duplicated, reordered or invented markers are not a drop and still fail strictly."""
    if len(found) >= len(expected):
        return False
    remaining = iter(expected)
    return all(marker in remaining for marker in found)


def _remask_public(response: str, expected: list[str],
                   exposed: dict[str, str], prefix: str) -> str | None:
    """Put the markers back for literals shown verbatim; None if any is not found intact,
    in order, as a whole token (the literal must survive untouched)."""
    response = _canonical_markers(response, prefix)
    cursor = 0
    out: list[str] = []
    for marker in expected:
        literal = exposed.get(marker)
        if literal is None:
            position = response.find(marker, cursor)
            if position < 0:
                return None
            out.extend((response[cursor:position], marker))
            cursor = position + len(marker)
            continue
        boundary = (r"(?<![A-Za-z0-9_.:/\\-])", r"(?![A-Za-z0-9_:/\\-]|\.[A-Za-z0-9])")
        match = re.compile(boundary[0] + re.escape(literal) + boundary[1]).search(response, cursor)
        if match is None:
            # _VERSION absorbs the preceding word ("Version 2.0.13") into the literal and the model
            # may translate or move it; the number itself must still come back intact.
            version = _VERSION.fullmatch(literal)
            if version:
                core = literal[version.end("product") + 1:]
                match = re.compile(boundary[0] + re.escape(core) + boundary[1]).search(response, cursor)
        if match is None:
            return None
        out.extend((response[cursor:match.start()], marker))
        cursor = match.end()
    out.append(response[cursor:])
    result = "".join(out)
    return result if _PLACEHOLDER.findall(result) == expected else None


async def _translate_chunk_safe(chunk: str, source_lang: SourceLang, target_lang: TargetLang,
                                 context: ProcessingContext, prefix: str,
                                 literals: tuple[str, ...] = ()) -> str:
    """Translate one chunk, recovering from a small quantized model's tendency to
    stop generating early on input that is almost entirely masked technical
    literals (shell commands, JSON) with little actual prose to translate, and from
    silently dropping a placeholder it cannot fit into the sentence.

    A response that trails off mid-way through the expected markers is a
    truncation signature specific to *this* chunk, not a model-noise fluke that
    a same-input retry reliably fixes (see the TOKEN_INTEGRITY_FAILED note in
    ``translate_blocks``). Splitting the chunk at a word boundary and
    translating each half independently shrinks the window the model has to
    run out of steam in, and bottoms out at a size where a technical-only
    fragment is simply kept as-is rather than failing the whole document.

    A dropped marker for a *public identifier* (product version, CVE/CWE id) is
    additionally recovered by one retry that shows only those identifiers verbatim
    and re-masks them after verifying they came back intact and in order.
    """
    response = await context.translate(chunk, source_lang, target_lang)
    expected = _PLACEHOLDER.findall(chunk)
    if not expected:
        return response
    found = _PLACEHOLDER.findall(_canonical_markers(response, prefix))
    if found == expected:
        return response
    truncated = _is_truncated_prefix(expected, found)
    if literals and (truncated or _is_dropped_subsequence(expected, found)):
        indexes = {marker: int(marker.rsplit(":", 1)[1][:-1]) for marker in expected}
        exposed = {marker: literals[index] for marker, index in indexes.items()
                   if index < len(literals) and _is_public_identifier(literals[index])}
        if exposed:
            visible = chunk
            for marker, literal in exposed.items():
                visible = visible.replace(marker, literal)
            retry = await context.translate(visible, source_lang, target_lang)
            restored = _remask_public(retry, expected, exposed, prefix)
            if restored is not None:
                return restored
    # The model moved markers (clause re-ordering, or a swapped role such as "from A to B"). Order
    # is never trusted blindly: translate each half on its own so markers cannot change places.
    reordered = len(found) == len(expected) and found != expected and sorted(found) == sorted(expected)
    if not (truncated or reordered):
        return response  # any other shape must still reach the strict check and fail loudly
    if len(chunk) <= _MIN_RECOVERY_CHARS:
        return chunk
    halves = _split_in_half(chunk)
    if halves is None:
        return chunk
    left, right = halves
    left_out = await _translate_chunk_safe(left, source_lang, target_lang, context, prefix, literals)
    right_out = await _translate_chunk_safe(right, source_lang, target_lang, context, prefix, literals)
    return left_out + right_out


async def translate_blocks(blocks: tuple[DocumentBlock, ...], source_lang: SourceLang,
                           target_lang: TargetLang, *, context: ProcessingContext
                           ) -> tuple[TranslatedBlock, ...]:
    if not any(block.text.strip() for block in blocks):
        raise TranslationError("NO_TEXT_DETECTED", "No text was detected.")
    if source_lang == "auto":
        resolved = await asyncio.to_thread(detect_source_language, blocks)
        if resolved is None:
            return tuple(TranslatedBlock(source=block, translated_text=block.text) for block in blocks)
        source_lang = resolved
    if source_lang != target_lang and source_lang != "vi" and target_lang != "vi":
        raise TranslationError("UNSUPPORTED_LANGUAGE_PAIR", "Language pair is unsupported.")
    salt = secrets.token_bytes(16)  # secret per request; nonce = HMAC(salt, block text)
    translated: list[TranslatedBlock] = []
    chunk_count = 0
    context_tokens = getattr(context, "model_context_tokens", 32768)
    maximum = min(3000, 6000, (context_tokens - 1024) // 8)
    if maximum < 1:
        raise TranslationError("TRANSLATION_UNAVAILABLE", "Translation model context is too small.")
    # Plan every block first (cheap, CPU only): the chunk budget is enforced before any model call.
    plans: list[tuple[DocumentBlock, ProtectedText | None, tuple[str, ...]]] = []
    for block in blocks:
        if not block.text.strip() or source_lang == target_lang:
            plans.append((block, None, ()))
            continue
        protected = protect_literals(block.text, salt)
        if _PLACEHOLDER.fullmatch(protected.text.strip()):
            plans.append((block, None, ()))
            continue
        chunks = _chunks(protected.text, maximum=maximum)
        chunk_count += len(chunks)
        if chunk_count > getattr(context, "max_chunks", 300):
            raise TranslationError("TEXT_TOO_LONG", "Document needs too many model chunks.")
        plans.append((block, protected, chunks))

    async def translate_one(block: DocumentBlock, protected: ProtectedText, chunks: tuple[str, ...]
                            ) -> TranslatedBlock:
        # A resend can recover when a model sample garbles or drops a placeholder
        # marker. Note: the repeatable 2/18 TOKEN_INTEGRITY_FAILED on the fixtures
        # (FILE-ja-vi/FILE-vi-en/FILE-ko-vi/FILE-vi-fr/FILE-zh-Hans-vi) was not
        # model noise but the context-dependent span comparison fixed in
        # _technical_cores; resending the *same whole chunk* unchanged cannot
        # fix a deterministic check. A chunk whose content is almost entirely
        # masked technical literals (shell/JSON with little prose) is a
        # different, observed-in-production failure: the specialist model
        # stops generating a few tokens in and silently drops the trailing
        # placeholders. _translate_chunk_safe recovers from that signature by
        # splitting the offending chunk and retrying each half before this
        # outer attempt loop ever sees a mismatch for it.
        integrity_max_attempt = getattr(context, "literal_integrity_max_retries", 1)
        for attempt in range(integrity_max_attempt + 1):
            pieces: list[tuple[str, str, str]] = []
            for chunk in chunks:
                if len(chunk) > 6000:
                    pieces.append(("", chunk, ""))  # indivisible technical literal
                    continue
                if not chunk.strip():
                    pieces.append((chunk, "", ""))
                    continue
                try:
                    response = await _translate_chunk_safe(chunk, source_lang, target_lang, context,
                                                            protected._prefix, protected._literals)
                except TranslationError as exc:
                    if exc.retryable and attempt == 0:
                        break
                    raise
                if not response.strip():
                    raise TranslationError("EMPTY_TRANSLATION", "Translation was empty.")
                leading = chunk[:len(chunk) - len(chunk.lstrip())]
                trailing = chunk[len(chunk.rstrip()):]
                pieces.append((leading, response.strip(), trailing))
            else:
                try:
                    joined: list[str] = []
                    pending_whitespace = ""
                    previous = ""
                    for leading, core, trailing in pieces:
                        pending_whitespace += leading
                        if not core:
                            pending_whitespace += trailing
                            continue
                        seam = pending_whitespace
                        if previous:
                            if target_lang in {"zh", "ja"}:
                                if (seam and all(char == " " for char in seam) and
                                        _cjk_target_drops_source_space(previous, core)):
                                    seam = ""
                            elif not seam:
                                if _spaced_target_needs_separator(previous, core):
                                    seam = " "
                        joined.extend((seam, core))
                        pending_whitespace = trailing
                        previous = core
                    joined.append(pending_whitespace)
                    output, restored = restore_literals_with_ranges("".join(joined), protected)
                    if not all(_occurs_as_token(core, block.text)
                               for core in _technical_cores(output, restored)):
                        raise TranslationError("TOKEN_INTEGRITY_FAILED",
                                               "Technical literal integrity check failed.")
                except TranslationError as exc:
                    if exc.code != "TOKEN_INTEGRITY_FAILED" or attempt >= integrity_max_attempt:
                        raise
                    continue
                return TranslatedBlock(source=block, translated_text=output)
        raise TranslationError("TRANSLATION_UNAVAILABLE", "Translation service is unavailable.")

    # Blocks are independent, so they may run concurrently (bounded; the worker's llm_gate still
    # caps real model calls). block_concurrency=1 keeps the historical strictly sequential flow.
    limit = max(1, int(getattr(context, "block_concurrency", 1)))
    gate = asyncio.Semaphore(limit)
    done = 0

    async def run(index: int) -> TranslatedBlock:
        nonlocal done
        block, protected, chunks = plans[index]
        if protected is None:
            result = TranslatedBlock(source=block, translated_text=block.text)
        else:
            async with gate:
                await context.checkpoint("translating", completed=done, total=len(blocks))
                result = await translate_one(block, protected, chunks)
        done += 1
        return result

    tasks = [asyncio.create_task(run(i)) for i in range(len(plans))]
    try:
        translated = list(await asyncio.gather(*tasks))
    except BaseException:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise
    await context.checkpoint("translating", completed=len(blocks), total=len(blocks))
    return tuple(translated)
