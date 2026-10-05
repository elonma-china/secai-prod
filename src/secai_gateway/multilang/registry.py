from __future__ import annotations

from .config import MultilangSettings
from .errors import TranslationError

LANGUAGES = (
    ("vi", "Tiếng Việt", "Tiếng Việt", [], "Latn", "ltr"),
    ("zh-Hans", "Trung giản thể", "简体中文", ["zh"], "Hans", "ltr"),
    ("ja", "Nhật", "日本語", [], "Jpan", "ltr"),
    ("ko", "Hàn", "한국어", [], "Kore", "ltr"),
    ("fr", "Pháp", "Français", [], "Latn", "ltr"),
    ("de", "Đức", "Deutsch", [], "Latn", "ltr"),
    ("es", "Tây Ban Nha", "Español", [], "Latn", "ltr"),
    ("ru", "Nga", "Русский", [], "Cyrl", "ltr"),
    ("ar", "Ả Rập", "العربية", [], "Arab", "rtl"),
    ("en", "Anh", "English", [], "Latn", "ltr"),
)


def canonical(code: str) -> str | None:
    key = code.strip(" \t\r\n\f\v").lower()
    if key in ("zh", "zh-hans"):
        return "zh-Hans"
    return key if key in {row[0] for row in LANGUAGES} else None


def resolve_pair(source_code: str, target_code: str,
                 settings: MultilangSettings) -> tuple[str, str]:
    source, target = canonical(source_code), canonical(target_code)
    if not source or not target:
        raise TranslationError("UNSUPPORTED_LANGUAGE")
    if source == target or "vi" not in (source, target):
        raise TranslationError("UNSUPPORTED_PAIR")
    direction = target if source == "vi" else source
    if not settings.enabled or not settings.flag(direction):
        raise TranslationError("LANGUAGE_DISABLED")
    return source, target


def capabilities(settings: MultilangSettings) -> dict:
    languages = [dict(zip(("code", "name", "native_name", "aliases", "script", "direction"), row))
                 for row in LANGUAGES]
    pairs = []
    for code, *_ in LANGUAGES[1:]:
        enabled = settings.enabled and settings.flag(code)
        for source, target in (("vi", code), (code, "vi")):
            pairs.append({"source_lang": source, "target_lang": target,
                          "enabled": enabled, "status": "stable" if enabled else "disabled"})
    return {"languages": languages, "pairs": pairs, "limits": {
        "max_text_chars": settings.max_text_chars,
        "max_source_tokens": settings.max_source_tokens,
        "max_chunks": settings.max_chunks,
        "request_timeout_seconds": settings.request_timeout_seconds,
    }}
