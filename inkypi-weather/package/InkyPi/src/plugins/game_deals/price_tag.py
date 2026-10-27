"""Gold currency and green engraved prices, with an optional device-only font."""

import os
from pathlib import Path

from PIL import Image, ImageDraw

from utils.app_utils import get_base_ui_font
from utils.fonts import load_font


PRIVATE_FONT_NAME = "game-deals-price.ttf"
REQUIRED_GLYPHS = "0123456789$.,"
ASSET = Path(__file__).with_name("assets") / "price-tag-graphite-gold.png"
FALLBACK_FONT = Path(__file__).parents[1] / "stocktracker/assets/fonts/BodoniModa-Variable.ttf"
MONEY_GREEN = ((203, 255, 200), (104, 237, 134))
CHAMPAGNE_GOLD = ((255, 229, 164), (189, 140, 49))
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


def _gradient_text(image, origin, text, font, colors):
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
    """Keep font variants and the small artwork owned by just this page render."""

    def __init__(self):
        self._face = _price_face()
        self._fonts = {}
        try:
            with Image.open(ASSET) as source:
                self._art = source.convert("RGBA")
        except (OSError, ValueError):
            self._art = None

    def _fit(self, amount, width, height):
        gap, margin = 2 * SCALE, 6 * SCALE
        for size in range(26 * SCALE, 0, -1):
            font = self._fonts.get(size)
            if font is None:
                font = self._face.font_variant(size=size)
                self._fonts[size] = font
            offset = font.getlength("$") + gap
            dollar, digits = font.getbbox("$", anchor="ls"), font.getbbox(amount, anchor="ls")
            left = min(0, dollar[0], offset + digits[0])
            right = max(dollar[2], offset + digits[2], offset + font.getlength(amount))
            top, bottom = min(dollar[1], digits[1]), max(dollar[3], digits[3])
            if right - left <= width - margin * 2 and bottom - top <= height - 10 * SCALE:
                return font, offset, (left, top, right, bottom)
        return font, offset, (left, top, right, bottom)

    def render(self, amount, size):
        """Retain every digit and decimal point; shrink the type inside its slot."""
        amount = str(amount)
        width, height = (int(value) * SCALE for value in size)
        if self._art is not None:
            image = self._art.resize((width, height), Image.Resampling.LANCZOS)
        else:
            image = Image.new("RGBA", (width, height))
            ImageDraw.Draw(image).rounded_rectangle((0, 0, width - 1, height - 1), radius=3 * SCALE,
                                                    fill=(39, 42, 41), outline=(184, 159, 105), width=SCALE)
        font, offset, (left, top, right, bottom) = self._fit(amount, width, height)
        x = (width - (right - left)) / 2 - left
        baseline = (height - (bottom - top)) / 2 - top
        _gradient_text(image, (x, baseline), "$", font, CHAMPAGNE_GOLD)
        _gradient_text(image, (x + offset, baseline), amount, font, MONEY_GREEN)
        result = image.resize(tuple(map(int, size)), Image.Resampling.LANCZOS)
        result.info["price_text"] = "$" + amount
        result.info["price_font_size"] = font.size / SCALE
        result.info["price_ink_bounds"] = ((x + left) / SCALE, (baseline + top) / SCALE,
                                           (x + right) / SCALE, (baseline + bottom) / SCALE)
        return result
