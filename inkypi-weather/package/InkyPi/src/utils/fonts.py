"""Load fonts with one layout engine, even small-text spacing and glyph-safe text.

Every font goes through :func:`load_font`, which always uses Pillow's BASIC
layout. The device has libraqm and development machines usually do not, so a
default engine renders the same text differently on each; with BASIC a local
render is pixel-identical to the device's.

Microsoft YaHei (the base UI font) gets :class:`SystemFont`, which keeps the
font but fixes two rendering faults:

* Spacing. YaHei's hinting moves glyph ink sideways at small sizes while Pillow
  places glyphs on whole pixels, so Latin text drawn as-is gets uneven gaps
  ("Ja son", "LAT EST"). Wide (CJK / full-width) runs keep the crisp 1x hinted
  raster; narrow runs are rasterised at ``SUPERSAMPLE`` times the size and
  box-filtered back, which follows the font's natural spacing.
* Missing glyphs. Characters the font only draws as its .notdef box (YaHei
  Bold's middle dot, zero-width marks, emoji) are replaced by an equivalent the
  same font has, or dropped when they are invisible or pictographic.
"""

from __future__ import annotations

import math
import threading
import unicodedata
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageChops, ImageFont

from utils.glyph_text import EQUIVALENTS as _PUNCTUATION_EQUIVALENTS

BASIC = ImageFont.Layout.BASIC
SUPERSAMPLE = 4
MAX_EVEN_SPACING_SIZE = 48
# Box-filtered coverage, lifted slightly so supersampled runs carry the same
# ink as the hinted raster on average (measured over 10-24 px YaHei, both weights).
COVERAGE_GAMMA = 1.2
_WIDE_ADVANCE_RATIO = 0.9
_PROBE_SIZE = 32
# A noncharacter no font maps: its raster is the font's .notdef glyph.
_MISSING_PROBE = "\uffff"
# Plugins probe glyph support by comparing a character's raster with one of
# these; they must keep drawing .notdef, so safe_text never rewrites them.
_NOTDEF_PROBES = frozenset("\ufffd\uffff\U0010ffff")

SYSTEM_FONT_FILES = frozenset(
    {"msyh.ttf", "msyh.ttc", "msyhbd.ttf", "msyhbd.ttc", "msyhl.ttc"}
)

_SPACES = frozenset(
    "\t\u00a0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008"
    "\u2009\u200a\u2028\u2029\u202f\u205f"
)
_EQUIVALENTS: dict[str, tuple[str, ...]] = dict(_PUNCTUATION_EQUIVALENTS)
_EQUIVALENTS.update(
    {
        "\u00b7": ("\u2027", "\uff65", "\u30fb", "\u2022", "."),
        "\u2219": ("\u00b7", "\u2027", "\uff65", "\u2022"),
        "\u22c5": ("\u00b7", "\u2027", "\uff65", "\u2022"),
        "\u0387": ("\u00b7", "\u2027", "\uff65"),
        "\u2010": ("-",),
        "\u2011": ("-",),
        "\u2012": ("\u2013", "-"),
        "\u2212": ("-",),
        "\u2e3a": ("\u2014\u2014", "\u2014"),
        "\u2e3b": ("\u2014\u2014\u2014", "\u2014"),
        "\u201b": ("\u2018", "'"),
        "\u201f": ("\u201c", '"'),
        "\u22ef": ("\u2026", "..."),
        "\u25b8": ("\u25b6", "\u25ba", "\u203a", ">"),
        "\u25b6": ("\u25ba", "\u25b8", "\u203a", ">"),
        "\u25c2": ("\u25c0", "\u25c4", "\u2039", "<"),
        "\u25c0": ("\u25c4", "\u25c2", "\u2039", "<"),
        "\u2713": ("\u221a",),
        "\u2714": ("\u221a",),
        "\u2715": ("\u00d7", "x"),
        "\u2716": ("\u00d7", "x"),
    }
)
_DROPPED_CATEGORIES = frozenset({"Cc", "Cf", "Cn", "Co", "Cs", "Mn", "Me"})
_SYMBOL_CATEGORIES = frozenset({"So", "Sk"})
_COVERAGE_LUT = [
    round(255 * (1 - (1 - value / 255) ** COVERAGE_GAMMA)) for value in range(256)
]


class _FaceFacts:
    """Per font file: which characters have real glyphs and which are wide."""

    def __init__(self, path: str, index: int):
        self._lock = threading.Lock()
        self._probe = ImageFont.FreeTypeFont(path, _PROBE_SIZE, index, layout_engine=BASIC)
        self._missing = self._signature(_MISSING_PROBE)
        self._present: dict[str, bool] = {}
        self._wide: dict[str, bool] = {}

    def _signature(self, char: str) -> tuple:
        mask = self._probe.getmask(char)
        return tuple(mask.size), bytes(mask)

    def present(self, char: str) -> bool:
        known = self._present.get(char)
        if known is None:
            with self._lock:
                known = self._signature(char) != self._missing
            self._present[char] = known
        return known

    def wide(self, char: str) -> bool:
        known = self._wide.get(char)
        if known is None:
            with self._lock:
                advance = self._probe.getlength(char)
            known = advance >= _PROBE_SIZE * _WIDE_ADVANCE_RATIO
            self._wide[char] = known
        return known


_facts: dict[tuple[str, int], _FaceFacts] = {}
_facts_lock = threading.Lock()


def _face_facts(path: str, index: int) -> _FaceFacts:
    key = (str(path), int(index))
    facts = _facts.get(key)
    if facts is None:
        with _facts_lock:
            facts = _facts.get(key)
            if facts is None:
                facts = _FaceFacts(*key)
                _facts[key] = facts
    return facts


@lru_cache(maxsize=64)
def _hires_font(path: str, index: int, encoding: str, size: float, variation: tuple | None):
    # Shared because many call sites build a fresh font for every draw.
    font = ImageFont.FreeTypeFont(path, size * SUPERSAMPLE, index, encoding, BASIC)
    _apply_variation(font, variation)
    return font


def _apply_variation(font, variation: tuple | None) -> None:
    if variation is None:
        return
    kind, value = variation
    if kind == "axes":
        ImageFont.FreeTypeFont.set_variation_by_axes(font, list(value))
    else:
        ImageFont.FreeTypeFont.set_variation_by_name(font, value)


def is_system_font_file(path) -> bool:
    return Path(str(path)).name.casefold() in SYSTEM_FONT_FILES


def load_font(path, size, index: int = 0, encoding: str = ""):
    """The only way to open a font file: BASIC layout, YaHei as SystemFont.

    Bare file names keep ``ImageFont.truetype``'s system font directory search.
    """

    if is_system_font_file(path) and Path(str(path)).is_file():
        return SystemFont(str(path), size, index, encoding)
    font = ImageFont.truetype(path, size, index, encoding, layout_engine=BASIC)
    if isinstance(font.path, (str, Path)) and is_system_font_file(font.path):
        return SystemFont(str(font.path), size, index, encoding)
    return font


def _image(core) -> Image.Image:
    return Image.Image()._new(core)


def _paste_max(canvas: Image.Image, glyph: Image.Image, x: int, y: int) -> None:
    left, top = max(0, x), max(0, y)
    right = min(canvas.width, x + glyph.width)
    bottom = min(canvas.height, y + glyph.height)
    if right <= left or bottom <= top:
        return
    piece = glyph.crop((left - x, top - y, right - x, bottom - y))
    box = (left, top, right, bottom)
    canvas.paste(ImageChops.lighter(canvas.crop(box), piece), box)


class SystemFont(ImageFont.FreeTypeFont):
    """FreeTypeFont with glyph-safe text and evenly spaced narrow runs."""

    def __init__(self, font, size=10, index=0, encoding="", layout_engine=None):
        super().__init__(font, size, index, encoding, BASIC)
        self._variation = None
        self._facts = _face_facts(self.path, self.index)

    def __getstate__(self):
        return [*super().__getstate__(), self._variation]

    def __setstate__(self, state):
        path, size, index, encoding, layout_engine, *rest = state
        SystemFont.__init__(self, path, size, index, encoding, layout_engine)
        variation = rest[0] if rest else None
        _apply_variation(self, variation)
        self._variation = variation

    # -- variable fonts and variants -------------------------------------------------

    def set_variation_by_axes(self, axes):
        super().set_variation_by_axes(axes)
        self._variation = ("axes", tuple(axes))

    def set_variation_by_name(self, name):
        super().set_variation_by_name(name)
        self._variation = ("name", name)

    def font_variant(self, font=None, size=None, index=None, encoding=None, layout_engine=None):
        variant = type(self)(
            self.path if font is None else font,
            self.size if size is None else size,
            self.index if index is None else index,
            self.encoding if encoding is None else encoding,
        )
        if font is None and self._variation is not None:
            _apply_variation(variant, self._variation)
            variant._variation = self._variation
        return variant

    def _hires_font(self) -> ImageFont.FreeTypeFont:
        return _hires_font(self.path, self.index, self.encoding, self.size, self._variation)

    # -- text normalisation ------------------------------------------------------------

    def safe_text(self, text):
        """Replace characters this font would draw as .notdef."""

        if not isinstance(text, str) or (text.isascii() and text.isprintable()):
            return text
        present = self._facts.present
        out = []
        for char in text:
            if char in "\n\r" or char in _NOTDEF_PROBES or present(char):
                out.append(char)
            else:
                out.append(self._substitute(char))
        return "".join(out)

    def _all_present(self, text: str) -> bool:
        return bool(text) and all(self._facts.present(char) for char in text)

    def _substitute(self, char: str) -> str:
        category = unicodedata.category(char)
        if char in _SPACES or category.startswith("Z"):
            return " "
        for option in _EQUIVALENTS.get(char, ()):
            if option != char and self._all_present(option):
                return option
        if category in _DROPPED_CATEGORIES:
            return ""
        compatible = unicodedata.normalize("NFKC", char)
        if compatible != char and self._all_present(compatible):
            return compatible
        base = "".join(
            part
            for part in unicodedata.normalize("NFKD", char)
            if not unicodedata.category(part).startswith("M")
        )
        if base and base != char and self._all_present(base):
            return base
        if category in _SYMBOL_CATEGORIES or ord(char) >= 0x1F000:
            return ""
        return char  # a letter from a script the font lacks; no font fallback by design

    # -- layout --------------------------------------------------------------------------

    def _even(self, mode, direction, features, language) -> bool:
        return (
            self.size <= MAX_EVEN_SPACING_SIZE
            and mode in ("", "L")
            and direction is None
            and features is None
            and language is None
        )

    def _layout(self, text: str, start_x: float = 0.0):
        """Runs as (wide, text, x, width); wide runs start on whole pixels."""

        wide_of = self._facts.wide
        runs: list[list] = []
        for char in text:
            wide = wide_of(char)
            if runs and runs[-1][0] == wide:
                runs[-1][1] += char
            else:
                runs.append([wide, char])
        pen = start_x
        pieces = []
        for wide, run in runs:
            if wide:
                pen = math.floor(pen + 0.5)
                width = ImageFont.FreeTypeFont.getlength(self, run)
            else:
                width = self._hires_font().getlength(run) / SUPERSAMPLE
            pieces.append((wide, run, pen, width))
            pen += width
        return pieces, pen - start_x

    def _piece_box(self, wide: bool, run: str, mode: str, stroke_width: float):
        if wide:
            return ImageFont.FreeTypeFont.getbbox(self, run, mode, None, None, None, stroke_width, "ls")
        box = self._hires_font().getbbox(run, mode, None, None, None, stroke_width * SUPERSAMPLE, "ls")
        return tuple(value / SUPERSAMPLE for value in box)

    def _extent(self, text: str, mode: str, stroke_width: float, anchor, start=(0.0, 0.0)):
        anchor = anchor or "la"
        if len(anchor) != 2 or anchor[0] not in "lmr":
            raise ValueError("invalid anchor")
        pieces, width = self._layout(text, start[0])
        left = top = math.inf
        right = bottom = -math.inf
        for wide, run, x, _ in pieces:
            box = self._piece_box(wide, run, mode, stroke_width)
            left, right = min(left, x + box[0]), max(right, x + box[2])
            top, bottom = min(top, box[1]), max(bottom, box[3])
        dx = {"l": 0.0, "m": -width / 2, "r": -width}[anchor[0]]
        dy = float(start[1])
        if anchor[1] in "ad":
            ascent, descent = self.getmetrics()
            dy += ascent if anchor[1] == "a" else -descent
        elif anchor[1] != "s":
            base = ImageFont.FreeTypeFont.getbbox(self, text, mode, None, None, None, stroke_width, "ls")
            aligned = ImageFont.FreeTypeFont.getbbox(
                self, text, mode, None, None, None, stroke_width, "l" + anchor[1]
            )
            dy += aligned[1] - base[1]
        return pieces, (left + dx, top + dy, right + dx, bottom + dy), dx, dy

    # -- Pillow font interface -----------------------------------------------------------

    def getlength(self, text, mode="", direction=None, features=None, language=None):
        text = self.safe_text(text)
        if not isinstance(text, str) or not self._even(mode, direction, features, language):
            return super().getlength(text, mode, direction, features, language)
        return self._layout(text)[1]

    def getbbox(self, text, mode="", direction=None, features=None, language=None, stroke_width=0, anchor=None):
        text = self.safe_text(text)
        if (
            not isinstance(text, str)
            or not text.strip()
            or "\n" in text
            or not self._even(mode, direction, features, language)
        ):
            return super().getbbox(text, mode, direction, features, language, stroke_width, anchor)
        _, (left, _, right, _), _, _ = self._extent(text, mode, stroke_width, anchor)
        # Only horizontal placement changes; keeping the hinted vertical extent
        # leaves every line height and vertical layout exactly as before.
        hinted = super().getbbox(text, mode, None, None, None, stroke_width, anchor)
        return math.floor(left), hinted[1], math.ceil(right), hinted[3]

    def getmask2(
        self,
        text,
        mode="",
        direction=None,
        features=None,
        language=None,
        stroke_width=0,
        anchor=None,
        ink=0,
        start=None,
        *args,
        **kwargs,
    ):
        text = self.safe_text(text)
        if (
            not isinstance(text, str)
            or not text.strip()
            or "\n" in text
            or not self._even(mode, direction, features, language)
        ):
            return super().getmask2(
                text, mode, direction, features, language, stroke_width, anchor, ink, start, *args, **kwargs
            )
        start = start or (0.0, 0.0)
        pieces, (left, top, right, bottom), dx, dy = self._extent(text, mode, stroke_width, anchor, start)
        x0, y0 = math.floor(left), math.floor(top)
        width, height = math.ceil(right) - x0, math.ceil(bottom) - y0
        canvas = Image.new("L", (max(1, width), max(1, height)), 0)
        if any(not wide for wide, *_ in pieces):
            hires = Image.new("L", (canvas.width * SUPERSAMPLE, canvas.height * SUPERSAMPLE), 0)
            for wide, run, x, _ in pieces:
                if wide:
                    continue
                core, (ox, oy) = self._hires_font().getmask2(
                    run, "L", None, None, None, stroke_width * SUPERSAMPLE, "ls", ink, None, *args, **kwargs
                )
                _paste_max(
                    hires,
                    _image(core),
                    round((x + dx - x0) * SUPERSAMPLE) + ox,
                    round((dy - y0) * SUPERSAMPLE) + oy,
                )
            canvas = hires.reduce(SUPERSAMPLE).point(_COVERAGE_LUT)
        for wide, run, x, _ in pieces:
            if not wide:
                continue
            core, (ox, oy) = ImageFont.FreeTypeFont.getmask2(
                self, run, "L", None, None, None, stroke_width, "ls", ink, None, *args, **kwargs
            )
            _paste_max(canvas, _image(core), round(x + dx - x0) + ox, round(dy - y0) + oy)
        return canvas.im, (x0, y0)
