"""Sale prices set straight on the page (no tag behind them): a champagne-gold dollar sign and
the digits in classic greenback green, with an optional device-only font."""

import os
from pathlib import Path

from PIL import Image, ImageDraw

from utils.app_utils import get_base_ui_font
from utils.fonts import load_font


PRIVATE_FONT_NAME = "game-deals-price.ttf"
REQUIRED_GLYPHS = "0123456789$.,"
FALLBACK_FONT = Path(__file__).parents[1] / "stocktracker/assets/fonts/BodoniModa-Variable.ttf"
# The fallback's weight and optical size (its axes), heavy enough to hold its colour on the page.
FALLBACK_AXES = [800, 28]
# Top-to-bottom gradients by theme: the digits in the classic greenback green of the bill's back
# (a bright mint by night on the dark page), the dollar sign in champagne gold beside them.
SALE_GREEN = {"day": ((84, 150, 82), (28, 98, 52)), "night": ((132, 238, 128), (60, 208, 104))}
DOLLAR_GOLD = {"day": ((226, 190, 98), (160, 112, 30)), "night": ((250, 226, 156), (222, 176, 76))}
# By day an engraved face's hairlines would show the pale page through the type: a deeper edge a
# pixel wide round every stroke (digits, dollar sign) fills them as dark engraving (the dark page
# does that by night).
ENGRAVING = {"day": ((14, 62, 32), (104, 70, 14)), "night": (None, None)}
# The room kept round the type inside its slot, at the working scale.
MARGIN, HEADROOM = 2, 3
SCALE = 4


def _price_face():
    """The private file never ships in the repository or a release archive."""
    data = os.environ.get("INKYPI_DATA_DIR", "").strip()
    candidates = []
    if data:
        private = Path(data) / "fonts" / PRIVATE_FONT_NAME
        try:
            if not private.is_symlink() and private.is_file() and 0 < private.stat().st_size <= 2 * 1024 * 1024:
                candidates.append(private)
        except OSError:
            pass
    candidates.append(FALLBACK_FONT)
    for path in candidates:
        try:
            face = load_font(path, 26 * SCALE)
            if face.covers(REQUIRED_GLYPHS):
                return face
        except (OSError, ValueError):
            continue
    return get_base_ui_font(26 * SCALE, bold=True)


def _gradient_text(image, origin, text, font, colors, edge=None):
    if edge is not None:
        rim = Image.new("L", image.size, 0)
        ImageDraw.Draw(rim).text(origin, text, font=font, fill=255, anchor="ls", stroke_width=SCALE)
        image.paste((*edge, 255), (0, 0), rim)
    mask = Image.new("L", image.size, 0)
    ImageDraw.Draw(mask).text(origin, text, font=font, fill=255, anchor="ls")
    bounds = mask.getbbox()
    if bounds is None:
        return
    paint = Image.new("RGBA", image.size)
    draw = ImageDraw.Draw(paint)
    top, bottom = colors
    for y in range(bounds[1], bounds[3]):
        ratio = (y - bounds[1]) / max(1, bounds[3] - bounds[1] - 1)
        color = tuple(round(a + (b - a) * ratio) for a, b in zip(top, bottom))
        draw.line((bounds[0], y, bounds[2], y), fill=(*color, 255))
    image.paste(paint, (0, 0), mask)


class PriceTags:
    """Keep font variants owned by just this page render, in the page's ``night`` or day colours."""

    def __init__(self, night=False):
        self._face = _price_face()
        self._axes = FALLBACK_AXES if Path(getattr(self._face, "path", "") or "") == FALLBACK_FONT else None
        self._fonts = {}
        mode = "night" if night else "day"
        self._colors = DOLLAR_GOLD[mode], SALE_GREEN[mode]
        self._edges = ENGRAVING[mode]

    def _fit(self, amount, width, height):
        gap, margin = 2 * SCALE, MARGIN * SCALE
        for size in range(30 * SCALE, 0, -1):
            font = self._fonts.get(size)
            if font is None:
                font = self._face.font_variant(size=size)
                if self._axes:
                    font.set_variation_by_axes(self._axes)
                self._fonts[size] = font
            offset = font.getlength("$") + gap
            dollar, digits = font.getbbox("$", anchor="ls"), font.getbbox(amount, anchor="ls")
            left = min(0, dollar[0], offset + digits[0])
            right = max(dollar[2], offset + digits[2], offset + font.getlength(amount))
            top, bottom = min(dollar[1], digits[1]), max(dollar[3], digits[3])
            if right - left <= width - margin * 2 and bottom - top <= height - 2 * HEADROOM * SCALE:
                return font, offset, (left, top, right, bottom)
        return font, offset, (left, top, right, bottom)

    def render(self, amount, size):
        """Retain every digit and decimal point; shrink the type inside its slot, left-aligned on a
        transparent ground so it sits straight on the page."""
        amount = str(amount)
        width, height = (int(value) * SCALE for value in size)
        image = Image.new("RGBA", (width, height))
        font, offset, (left, top, right, bottom) = self._fit(amount, width, height)
        x = MARGIN * SCALE - left
        baseline = (height - (bottom - top)) / 2 - top
        dollar, digits = self._colors
        _gradient_text(image, (x, baseline), "$", font, dollar, self._edges[1])
        _gradient_text(image, (x + offset, baseline), amount, font, digits, self._edges[0])
        result = image.resize(tuple(map(int, size)), Image.Resampling.LANCZOS)
        result.info["price_text"] = "$" + amount
        result.info["price_font_size"] = font.size / SCALE
        result.info["price_ink_bounds"] = ((x + left) / SCALE, (baseline + top) / SCALE,
                                           (x + right) / SCALE, (baseline + bottom) / SCALE)
        return result
