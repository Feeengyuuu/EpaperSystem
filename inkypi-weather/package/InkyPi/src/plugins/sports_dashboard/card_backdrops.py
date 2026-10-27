"""Stadium art behind each sport's featured card, and a quiet pattern under its lists.

Each scene is halftone ink on a cream ground: the colour gathers at the edges
and along the foot, and the middle stays pale so the card's own type reads on
top. A scene cover-fits the card's inner area and keeps its foot (the pitch,
the court or the stage). The list pattern is a sparse scatter of small sport
doodles, tiled and faded, that only takes the plain paper and panel pixels of
a list column already drawn, so its rows, rules, type and logos stay on top.

By day the cream ground becomes the theme panel; at night the ink glows
faintly on the dark panel and the light parts of the art fall back to it.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageOps

CARD_BACKDROP_DIR = Path(__file__).resolve().parent / "assets" / "decor" / "card_backdrops"
CARD_BACKDROP_KINDS = ("football", "basketball", "esports", "baseball", "gridiron", "golf", "motorsport", "tactical")
LIST_BACKDROP_KINDS = ("football", "basketball", "esports", "baseball", "gridiron", "golf", "motorsport")
# The art for each lower-left sport, by its scorebug spec name.
SPORT_BACKDROP_KINDS = {
    "NBA": "basketball",
    "WNBA": "basketball",
    "MLB": "baseball",
    "NFL": "gridiron",
    "NCAA": "gridiron",
    "PGA": "golf",
    "F1": "motorsport",
}
# The cream the art is printed on; it becomes the theme panel.
CARD_BACKDROP_GROUND = (254, 251, 233)
# A card this many times wider (for its height) than its scene is a band.
CARD_BACKDROP_BAND_RATIO = 1.4
# Where a band's slice of the scene sits, from the top (0) to the foot (1).
CARD_BACKDROP_BAND_CENTRE = 0.6
# Share of each ink's strength that glows on the night panel.
CARD_BACKDROP_NIGHT_GLOW = 0.55
# How much of the list pattern shows: enough to notice, faint enough to read through.
LIST_BACKDROP_STRENGTH = 0.5
# Width of a card shell's left accent stripe (with its outline) and outline.
CARD_BACKDROP_STRIPE = 9
CARD_BACKDROP_OUTLINE = 2


def card_backdrop_box(x1, y1, x2, y2):
    """The inner area of a card shell drawn at (x1, y1, x2, y2): inside the
    outline and to the right of the accent stripe."""
    inset = CARD_BACKDROP_OUTLINE
    return x1 + CARD_BACKDROP_STRIPE, y1 + inset, x2 - inset, y2 - inset


def paint_card_backdrop(image, box, kind, panel, radius=3):
    """Fill ``box`` (inclusive) with ``kind``'s scene toned for ``panel``.

    The right-hand corners follow the card's rounded outline. Returns False
    when nothing is drawn: an unknown kind, a box too small, or missing art.
    """
    size = _box_size(box)
    if kind not in CARD_BACKDROP_KINDS or min(size) < 8:
        return False
    try:
        scene = _card_backdrop_scene(kind, size, _rgb(panel))
    except OSError:
        return False
    image.paste(scene, (int(box[0]), int(box[1])), _card_backdrop_mask(size, radius))
    return True


def paint_list_backdrop(image, box, kind, panel, grounds):
    """Lay ``kind``'s faded doodle pattern under the list already drawn in ``box``.

    Only pixels exactly one of the ``grounds`` colours (the page's paper and
    panel) take the pattern. Returns False when nothing is drawn.
    """
    size = _box_size(box)
    if kind not in LIST_BACKDROP_KINDS or min(size) < 8:
        return False
    try:
        pattern = _list_backdrop_pattern(kind, size, _rgb(panel))
    except OSError:
        return False
    x1, y1 = int(box[0]), int(box[1])
    region = image.crop((x1, y1, x1 + size[0], y1 + size[1])).convert("RGB")
    image.paste(pattern, (x1, y1), _ground_mask(region, [_rgb(ground) for ground in grounds]))
    return True


@lru_cache(maxsize=8)
def _backdrop_art(name):
    with Image.open(CARD_BACKDROP_DIR / f"{name}.png") as art:
        return art.convert("RGB")


@lru_cache(maxsize=8)
def _card_backdrop_scene(kind, size, panel):
    art = _backdrop_art(kind)
    # A card about as wide as the scene keeps its foot; a long low band takes
    # the stands and horizon instead, so its type sits on pale ground.
    wide = size[0] / size[1] > CARD_BACKDROP_BAND_RATIO * art.width / art.height
    centering = (0.5, CARD_BACKDROP_BAND_CENTRE if wide else 1.0)
    return _toned(ImageOps.fit(art, size, Image.LANCZOS, centering=centering), panel)


@lru_cache(maxsize=8)
def _list_backdrop_pattern(kind, size, panel):
    tile = _backdrop_art(f"list_{kind}")
    canvas = Image.new("RGB", size)
    for top in range(0, size[1], tile.height):
        for left in range(0, size[0], tile.width):
            canvas.paste(tile, (left, top))
    return Image.blend(Image.new("RGB", size, panel), _toned(canvas, panel), LIST_BACKDROP_STRENGTH)


def _toned(art, panel):
    if _is_dark(panel):
        return _night_tones(art, panel)
    return _day_tones(art, panel)


def _day_tones(art, panel):
    # Multiply: the ground's channels scale onto the panel's, inks keep their hue.
    bands = [
        band.point(lambda value, scale=tone / ground: min(255, round(value * scale)))
        for band, tone, ground in zip(art.split(), panel, CARD_BACKDROP_GROUND)
    ]
    return Image.merge("RGB", bands)


def _night_tones(art, panel):
    # Ink density is the deepest channel's distance below the ground.
    densities = [
        band.point(lambda value, ground=ground: max(0, round((ground - value) * 255 / ground)))
        for band, ground in zip(art.split(), CARD_BACKDROP_GROUND)
    ]
    density = ImageChops.lighter(ImageChops.lighter(densities[0], densities[1]), densities[2])
    ink = ImageChops.multiply(art, Image.merge("RGB", (density, density, density)))
    glow = ink.point(lambda value: round(value * CARD_BACKDROP_NIGHT_GLOW))
    return ImageChops.add(glow, Image.new("RGB", art.size, panel))


def _ground_mask(region, grounds):
    mask = Image.new("L", region.size, 0)
    for ground in grounds:
        red, green, blue = ImageChops.difference(region, Image.new("RGB", region.size, ground)).split()
        spread = ImageChops.lighter(ImageChops.lighter(red, green), blue)
        mask = ImageChops.lighter(mask, spread.point(lambda value: 255 if value == 0 else 0))
    return mask


@lru_cache(maxsize=8)
def _card_backdrop_mask(size, radius):
    mask = Image.new("L", size, 0)
    draw = ImageDraw.Draw(mask)
    draw.rounded_rectangle((0, 0, size[0] - 1, size[1] - 1), radius=radius, fill=255)
    # The left edge meets the accent stripe square.
    draw.rectangle((0, 0, radius, size[1] - 1), fill=255)
    return mask


def _box_size(box):
    x1, y1, x2, y2 = (int(value) for value in box)
    return x2 - x1 + 1, y2 - y1 + 1


def _rgb(colour):
    return tuple(int(value) for value in colour[:3])


def _is_dark(rgb):
    red, green, blue = rgb
    return 0.299 * red + 0.587 * green + 0.114 * blue < 128
