"""The cover pages' own typefaces.

Each page is set in the type of the place its covers come from:

- "gallery" (Daily Art), a museum's wall label: Spectral, a work's title in its bold italic, as
  titles are set, and its facts in the semibold (sturdy enough for the panel's small type);
  Chinese in Noto Serif SC.
- "comic" (GCD Comic Covers), comic lettering: Bangers for titles, dates and figures, Comic Neue
  for the facts; Chinese in ZCOOL KuaiLe.
- "newsstand" (Magazine Covers), a masthead's Didone over a geometric sans: Bodoni Moda for
  titles (Chinese ones in Noto Serif SC Black), Jost for everything else.

A face stands in for the page's UI font at a size of its own (``scale``), so the two read as one
size, and on the UI font's baseline, so no line moves. A character it lacks comes from the
style's Chinese face, then from the UI font, glyph by glyph. A role whose font cannot be read
is set in the UI font. The font files and their licences are in assets/cover_fonts.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from plugins.sports_dashboard.esports_font_runs import EsportsFont
from utils.app_utils import get_base_ui_font
from utils.fonts import load_font

# What a page's type is for: a work's title; its facts and the other running text; the date and
# headings; white type on a colour (date chips, badges, the numbers on discs).
TITLE, TEXT, STRONG, CHIP = "title", "text", "strong", "chip"

COVER_FONTS = Path(__file__).with_name("assets") / "cover_fonts"
STATIC_FONTS = Path(__file__).resolve().parents[2] / "static" / "fonts"
BODONI_MODA = Path(__file__).resolve().parents[1] / "stocktracker" / "assets" / "fonts" / "BodoniModa-Variable.ttf"


@dataclass(frozen=True)
class Face:
    """A font file, its size against the UI font's and, for a variable font, its axis values in
    the font's own order."""

    path: Path
    scale: float = 1.0
    axes: tuple = ()


@dataclass(frozen=True)
class Role:
    """A role's Latin face, the face its Chinese is set in (the UI font's when None) and the
    weight of the UI font behind them."""

    latin: Face
    chinese: Face | None = None
    bold: bool = False


SERIF_SC = Face(STATIC_FONTS / "NotoSerifSC-SemiBold-Nature.ttf")
SERIF_SC_BLACK = Face(STATIC_FONTS / "NotoSerifSC-Black-Headline.ttf")
# ZCOOL KuaiLe draws small in its em: a little larger, it matches the lettering beside it.
KUAILE = Face(COVER_FONTS / "ZCOOLKuaiLe-Regular.ttf", 1.12)
JOST = STATIC_FONTS / "Jost.ttf"

STYLES = {
    "gallery": {
        TITLE: Role(Face(COVER_FONTS / "Spectral-BoldItalic.ttf", 1.2), SERIF_SC, bold=True),
        TEXT: Role(Face(COVER_FONTS / "Spectral-SemiBold.ttf", 1.12), SERIF_SC),
        STRONG: Role(Face(COVER_FONTS / "Spectral-Bold.ttf", 1.12), SERIF_SC, bold=True),
        # A chip's small white Chinese stays in the UI font's bold: a serif's thin strokes break up there.
        CHIP: Role(Face(COVER_FONTS / "Spectral-Bold.ttf", 1.15), bold=True),
    },
    "comic": {
        TITLE: Role(Face(COVER_FONTS / "Bangers-Regular.ttf", 1.3), KUAILE, bold=True),
        TEXT: Role(Face(COVER_FONTS / "ComicNeue-Bold.ttf", 1.12), KUAILE),
        STRONG: Role(Face(COVER_FONTS / "Bangers-Regular.ttf", 1.25), KUAILE, bold=True),
        CHIP: Role(Face(COVER_FONTS / "Bangers-Regular.ttf", 1.25), KUAILE, bold=True),
    },
    "newsstand": {
        # Bodoni Moda's axes are weight, then optical size: the text cut keeps its hairlines on the panel.
        TITLE: Role(Face(BODONI_MODA, 1.15, (800, 11)), SERIF_SC_BLACK, bold=True),
        TEXT: Role(Face(JOST, 1.1, (500,))),
        STRONG: Role(Face(JOST, 1.1, (700,)), bold=True),
        CHIP: Role(Face(JOST, 1.12, (700,)), bold=True),
    },
}


def _open(face, size):
    font = load_font(str(face.path), max(1, round(size * face.scale)))
    if face.axes:
        font.set_variation_by_axes(list(face.axes))
    return font


class CoverType:
    """A page's fonts by role and size, opened once each for the page being drawn."""

    def __init__(self, style):
        self.roles = STYLES.get(style, {})
        self._fonts = {}

    def font(self, role, size):
        key = (role, int(size))
        if key not in self._fonts:
            self._fonts[key] = self._build(*key)
        return self._fonts[key]

    def _build(self, role, size):
        spec = self.roles.get(role)
        fallback = get_base_ui_font(size, bold=spec.bold if spec is not None else role != TEXT)
        if spec is None:
            return fallback
        try:
            if spec.chinese is not None:
                fallback = EsportsFont(_open(spec.chinese, size), fallback)
            return EsportsFont(_open(spec.latin, size), fallback)
        except (OSError, ValueError):
            return get_base_ui_font(size, bold=spec.bold)
