"""Swap punctuation a font cannot draw for a look-alike it can.

Some device fonts lack characters such as the middle dot in "小猪佩奇·完美假期",
which PIL then draws as an empty box. Only characters with known equivalents are
checked, so ordinary text costs nothing.
"""

from __future__ import annotations

# A code point no font maps, so drawing it yields the font's .notdef glyph.
_MISSING_PROBE = "\U000E01EF"

_DOTS = ("·", "・", "‧", "･", "•", "∙", "⋅")
EQUIVALENTS: dict[str, tuple[str, ...]] = {char: _DOTS for char in _DOTS}
EQUIVALENTS.update({
    "—": ("—", "―", "─", "-"),
    "–": ("–", "-"),
    "‘": ("‘", "'"),
    "’": ("’", "'"),
    "“": ("“", '"'),
    "”": ("”", '"'),
})

_support_cache: dict[tuple, bool] = {}
_missing_cache: dict[tuple, tuple] = {}


def _font_key(font) -> tuple:
    path = getattr(font, "path", None)
    if path is None:
        return ("object", id(font))
    return (str(path), getattr(font, "index", 0), getattr(font, "size", None))


def _mask_signature(font, char) -> tuple:
    mask = font.getmask(char)
    return (tuple(mask.size), bytes(mask))


def font_has_glyph(font, char: str) -> bool:
    """True when ``font`` draws ``char`` with a real glyph, not .notdef."""
    key = (_font_key(font), char)
    cached = _support_cache.get(key)
    if cached is not None:
        return cached
    try:
        font_key = _font_key(font)
        missing = _missing_cache.get(font_key)
        if missing is None:
            missing = _mask_signature(font, _MISSING_PROBE)
            _missing_cache[font_key] = missing
        supported = _mask_signature(font, char) != missing
    except (AttributeError, OSError, TypeError, UnicodeError, ValueError):
        supported = True  # cannot tell; keep the original character
    _support_cache[key] = supported
    return supported


def glyph_safe_text(text: str, font) -> str:
    """Return ``text`` with undrawable known punctuation replaced."""
    if not text or not any(char in EQUIVALENTS for char in text):
        return text
    result = []
    for char in text:
        options = EQUIVALENTS.get(char)
        if options is None or font_has_glyph(font, char):
            result.append(char)
            continue
        result.append(next(
            (option for option in options if option != char and font_has_glyph(font, option)),
            " ",
        ))
    return "".join(result)
