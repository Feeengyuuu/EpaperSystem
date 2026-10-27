"""Official source marks in attribution lines, without rewriting their status text."""

from math import ceil
from pathlib import Path
import re

from PIL import Image


SOURCE_LOGO_DIR = Path(__file__).parent / "assets" / "logos" / "sources"
SOURCE_MARKS = {
    "ESPN": "espn", "MLB": "mlb", "PANDASCORE": "pandascore", "VALVE": "valve", "OPENF1": "openf1",
    # The CSL is the competition the ESPN data covers, like Valve for CS2 events.
    "CSL": "csl",
    "FD": "football_data", "FOOTBALL": "football_data",
    "API-F": "api_sports", "API": "api_sports",
    # Bookmakers behind the odds, as the club cards abbreviate them.
    "BET365": "bet365", "365": "bet365", "DK": "draftkings",
}
# Longest first, so API-F wins over API.
SOURCE_TOKEN = re.compile(
    r"(?<![\w-])(" + "|".join(re.escape(token) for token in sorted(SOURCE_MARKS, key=len, reverse=True))
    + r")(?![\w-])",
    re.IGNORECASE,
)
# Marks drawn as tall as the caption type (badges and stacked marks); the rest at 80 percent.
FULL_HEIGHT_MARKS = {"mlb", "csl", "api_sports", "bet365", "draftkings"}
# Marks whose neutral lettering is drawn for one ground: white or light grey for a dark one,
# black for a light one. Those strokes take the page's ink where they would vanish; the brand
# colours stay as published.
NEUTRAL_LETTERING_MARKS = {"csl", "football_data", "api_sports", "draftkings"}

# A stroke is neutral when its channels differ by at most this; it vanishes on a light page above
# NEUTRAL_LIGHT and on a dark page below NEUTRAL_DARK.
NEUTRAL_SPREAD, NEUTRAL_LIGHT, NEUTRAL_DARK = 40, 150, 110


def draw_source_credit(plugin, image, draw, box, label, color, *, size=9, min_size=7,
                       align="left", on_light=False, ink=(20, 30, 40)):
    """Replace recognized brands only; callers retain their plain-text fallback.

    Call this only for source/attribution fields. In particular, a league's
    section title is not evidence that it supplies the data. A missing mark or
    an overfull line returns False before drawing anything, so status, time and
    bookmaker suffixes are never silently discarded to make a logo fit.
    """
    parts = SOURCE_TOKEN.split(str(label or ""))
    if len(parts) == 1:
        return False
    x1, y1, x2, y2 = map(int, box)
    width, height = x2 - x1, y2 - y1
    if width <= 0 or height < 6:
        return False
    for font_size in range(size, min_size - 1, -1):
        font = plugin._font(font_size, True)
        runs = []
        total_width = 0
        for index, part in enumerate(parts):
            if not part:
                continue
            if index % 2:
                brand = part.upper()
                key = SOURCE_MARKS[brand]
                mark_height = min(height, max(6, font_size if key in FULL_HEIGHT_MARKS else round(font_size * .8)))
                mark = plugin._load_local_logo(
                    str(SOURCE_LOGO_DIR / f"{key}.png"),
                    (mark_height * 9, mark_height), alpha_threshold=8,
                )
                if mark is None:
                    return False
                if key in NEUTRAL_LETTERING_MARKS:
                    mark = _inked_neutrals(mark, ink, on_light)
                runs.append((brand, mark, mark.width))
                total_width += mark.width
            else:
                bounds = draw.textbbox((0, 0), part, font=font)
                if bounds[3] - bounds[1] > height:
                    break
                run_width = ceil(plugin._text_width(draw, part, font))
                runs.append((None, part, run_width))
                total_width += run_width
        else:
            if total_width > width:
                continue
            cursor = x1 + (width - total_width if align == "right" else
                           (width - total_width) // 2 if align == "center" else 0)
            for brand, content, run_width in runs:
                if brand:
                    top = y1 + (height - content.height) // 2
                    if brand in {"PANDASCORE", "OPENF1"} and on_light:
                        with Image.new("RGBA", content.size, ink) as monochrome:
                            with content.getchannel("A") as alpha:
                                monochrome.putalpha(alpha)
                            image.paste(monochrome, (cursor, top), monochrome)
                    else:
                        image.paste(content, (cursor, top), content)
                else:
                    plugin._draw_text_in_box(draw, (cursor, y1, cursor + run_width, y2), content, font, color)
                cursor += run_width
            return True
    return False


def _inked_neutrals(mark, ink, on_light):
    """``mark`` with its neutral strokes that would vanish on the page in ``ink``.

    On a light page that is white and light-grey lettering (made for a dark ground);
    on a dark page, black and dark-grey lettering. Coloured strokes are left alone.
    """
    mark = mark.copy()
    pixels = mark.load()
    for y in range(mark.height):
        for x in range(mark.width):
            red, green, blue, alpha = pixels[x, y]
            if not alpha or max(red, green, blue) - min(red, green, blue) > NEUTRAL_SPREAD:
                continue
            lightness = (red + green + blue) / 3
            if (on_light and lightness > NEUTRAL_LIGHT) or (not on_light and lightness < NEUTRAL_DARK):
                pixels[x, y] = (*ink[:3], alpha)
    return mark
