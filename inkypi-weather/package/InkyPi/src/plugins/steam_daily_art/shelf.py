"""Steam Daily Art's info shelf, and crisp scaling for pixel art.

Store art often paints a third of itself in one flat colour (a field, a sky, the mat around
a wide header). The shelf turns that block, or a soft band at the foot of any other art,
into why the game is on screen: its reasons (on your wishlist, a deal, new, a top seller,
already yours), its price, one line on what it is, its tags and who made it, with the
Steam wordmark. The shelf takes the art's own hue, deep and calm; its words are pure white.

Pixel art drawn at a whole-number scale is brought back to its own pixel grid and
enlarged without smoothing, so its pixels stay square and sharp on the panel.
"""

from __future__ import annotations

import colorsys
from functools import lru_cache
import zlib
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageStat

from utils.app_utils import get_base_ui_font

ASSET_DIR = Path(__file__).resolve().parent / "assets" / "shelf"
REASON_ICONS = ("wishlist", "deal", "new", "top_seller", "spotlight", "coming_soon", "owned", "daily_deal", "steam_icon")
# A flat block: rows whose pixels are nearly all within FLAT_TOLERANCE of the colour at the
# art's foot, at least FLAT_MIN_SHARE of the height.
FLAT_TOLERANCE = 28
FLAT_ROW_SHARE = 0.9
FLAT_MIN_SHARE = 0.2
# Rows of the flat colour left above the shelf, so whatever stands on that ground still does.
GROUND_KEEP = 8
SCRIM_HEIGHT = 146
SCRIM_FADE = 42
MARGIN = 24
ROW_GAP = 8
# Room kept clear above and below the shelf's rows.
SHELF_PADDING = 16
CHIP_HEIGHT = 26
# The head row: the price paid at PRICE_SIZE (about a third larger than the rest of the row),
# Steam's discount box at CUT_SIZE and the struck price at ORIGINAL_SIZE.
HEAD_HEIGHT = 32
PRICE_SIZE = 28
CUT_SIZE = 22
ORIGINAL_SIZE = 15
ICON_SIZE = 18
WORDMARK_HEIGHT = 15
# The developer's logo before its name in the credit line.
STUDIO_LOGO = 18
# A studio avatar whose four corners agree within STUDIO_GROUND_TOLERANCE has a flat ground,
# cut away; a mark left darker than STUDIO_DARK_MARK (lightness) is drawn in white for the dark bar.
STUDIO_GROUND_TOLERANCE, STUDIO_DARK_MARK = 30, 70
# Pixel art: the largest block (2..PIXEL_MAX_BLOCK) at which one grid offset rebuilds the art
# within PIXEL_MAX_ERROR on average with at most PIXEL_MAX_BAD of its pixels off by
# PIXEL_BAD_STEP or more, while the worst offset is PIXEL_CONTRAST times worse (smooth art is
# rebuilt about as well at every offset). True grids leave at most 0.2% bad pixels even
# through JPEG; a false grid on art enlarged by an uneven factor leaves about 1% or more.
PIXEL_MAX_BLOCK = 6
PIXEL_MAX_ERROR = 7.0
PIXEL_BAD_STEP = 48
PIXEL_MAX_BAD = 0.004
PIXEL_CONTRAST = 2.2
PIXEL_SAMPLE = 240
# Hard-edged flat art, by its neighbouring pixels' steps (the brightest channel): Tetromancer's
# store art measures 86-93% flat, 6-10% gentle; Baldur's Gate 3, CS2, Stardew Valley, Hades,
# Celeste, Hollow Knight and Unrailed capsules are at most 77% flat with 19-41% gentle.
HARD_EDGE_SAMPLE = 1000
HARD_EDGE_FLAT_STEP = 8
HARD_EDGE_SHARP_STEP = 48
HARD_EDGE_MIN_FLAT = 0.82
HARD_EDGE_MAX_GENTLE = 0.12
HARD_EDGE_MIN_SHARP = 0.002
WHITE = (255, 255, 255)
# The shelf's ground covers the art at this opacity (of 255), so the picture shows through.
SHELF_OPACITY = 204
# Each tag chip takes its own colour, picked by the tag's words (the same tag keeps its colour
# from game to game unless two tags on one shelf would share it): deep, saturated fills that
# carry white words on the panel.
TAG_COLOURS = (
    (31, 111, 209),
    (214, 84, 22),
    (33, 136, 74),
    (138, 68, 200),
    (200, 44, 104),
    (14, 132, 132),
)
# Steam's own colours for a discount.
DEAL_GREEN = (76, 107, 34)
DEAL_LIME = (190, 238, 17)


def find_flat_band(image, min_share=FLAT_MIN_SHARE):
    """(top row, colour) of the flat colour block at the art's foot, or None."""
    rgb = image.convert("RGB")
    width, height = rgb.size
    if width < 8 or height < 8:
        return None
    foot = rgb.crop((0, height - 6, width, height)).resize((max(1, width // 4), 2), Image.Resampling.NEAREST)
    colour = max(foot.getcolors(maxcolors=foot.width * foot.height))[1]
    red, green, blue = ImageChops.difference(rgb, Image.new("RGB", rgb.size, colour)).split()
    near = ImageChops.lighter(ImageChops.lighter(red, green), blue).point(
        lambda value: 255 if value <= FLAT_TOLERANCE else 0
    )
    rows = near.resize((1, height), Image.Resampling.BOX).get_flattened_data()
    top = height
    while top > 0 and rows[top - 1] >= 255 * FLAT_ROW_SHARE:
        top -= 1
    return (top, colour) if height - top >= height * min_share else None


def palette(colour):
    """The shelf's colours from the art's: its hue deep for the ground, a little lighter for
    the chips, mid-tone for the rim; greys (and black or white mats) stay neutral."""
    hue, _light, sat = colorsys.rgb_to_hls(*(channel / 255 for channel in colour))
    sat = min(sat, 0.5) if sat > 0.12 else 0.0

    def tone(lightness, saturation=sat):
        return tuple(round(channel * 255) for channel in colorsys.hls_to_rgb(hue, lightness, saturation))

    return {"base": tone(0.12), "chip": tone(0.22), "rim": tone(0.46, min(0.6, sat + 0.1) if sat else 0.0)}


def pixel_grid(image):
    """(block, x offset, y offset) when the art is pixel art enlarged by a whole number, else None."""
    rgb = image.convert("RGB")
    side = min(rgb.width, rgb.height, PIXEL_SAMPLE)
    left, top = (rgb.width - side) // 2, (rgb.height - side) // 2
    sample = rgb.crop((left, top, left + side, top + side))
    for block in range(PIXEL_MAX_BLOCK, 1, -1):
        errors = []
        for x_offset in range(block):
            for y_offset in range(block):
                cells = (side - max(x_offset, y_offset)) // block
                if cells < 8:
                    continue
                tile = sample.crop((x_offset, y_offset, x_offset + cells * block, y_offset + cells * block))
                rebuilt = tile.resize((cells, cells), Image.Resampling.BOX).resize(tile.size, Image.Resampling.NEAREST)
                difference = ImageChops.difference(tile, rebuilt)
                red, green, blue = difference.split()
                steps = ImageChops.lighter(ImageChops.lighter(red, green), blue).histogram()
                bad = sum(steps[PIXEL_BAD_STEP:]) / max(1, sum(steps))
                errors.append((sum(ImageStat.Stat(difference).mean) / 3, bad, x_offset, y_offset))
        if not errors:
            continue
        best, worst = min(errors), max(errors)
        if best[0] <= PIXEL_MAX_ERROR and best[1] <= PIXEL_MAX_BAD and worst[0] >= best[0] * PIXEL_CONTRAST:
            return block, (left + best[2]) % block, (top + best[3]) % block
    return None


def is_hard_edged(image):
    """Whether the art is flat colour with hard edges, as pixel art is even when it was
    enlarged by an uneven factor: neighbouring pixels mostly equal, few gentle steps, some
    sharp ones. Painted and photographic art is full of gentle steps; a smooth gradient has
    no sharp ones."""
    rgb = image.convert("RGB")
    if rgb.width > HARD_EDGE_SAMPLE:
        left = (rgb.width - HARD_EDGE_SAMPLE) // 2
        rgb = rgb.crop((left, 0, left + HARD_EDGE_SAMPLE, rgb.height))
    if rgb.width < 16:
        return False
    red, green, blue = ImageChops.difference(
        rgb.crop((0, 0, rgb.width - 1, rgb.height)), rgb.crop((1, 0, rgb.width, rgb.height))
    ).split()
    steps = ImageChops.lighter(ImageChops.lighter(red, green), blue).histogram()
    total = sum(steps)
    flat = sum(steps[:HARD_EDGE_FLAT_STEP]) / total
    gentle = sum(steps[HARD_EDGE_FLAT_STEP:HARD_EDGE_SHARP_STEP]) / total
    sharp = sum(steps[HARD_EDGE_SHARP_STEP:]) / total
    return flat >= HARD_EDGE_MIN_FLAT and gentle <= HARD_EDGE_MAX_GENTLE and sharp >= HARD_EDGE_MIN_SHARP


def to_native(image, grid):
    """The pixel art at its own size: each block averaged into one pixel (JPEG noise goes too)."""
    block, x_offset, y_offset = grid
    rgb = image.convert("RGB")
    columns, rows = (rgb.width - x_offset) // block, (rgb.height - y_offset) // block
    region = rgb.crop((x_offset, y_offset, x_offset + columns * block, y_offset + rows * block))
    return region.resize((columns, rows), Image.Resampling.BOX)


@lru_cache(maxsize=16)
def _font(size, bold):
    """The pages' UI face: YaHei from the device's font folder ($INKYPI_DATA_DIR/fonts) first,
    then the bundled Chinese fallbacks, so the shelf's Chinese never falls to a Latin face."""
    return get_base_ui_font(size, bold=bold)


@lru_cache(maxsize=16)
def _asset(name, height, ink=None):
    path = ASSET_DIR / f"{name}.png"
    if not path.is_file():
        return None
    with Image.open(path) as source:
        image = source.convert("RGBA")
    image = image.resize((max(1, round(image.width * height / image.height)), height), Image.Resampling.LANCZOS)
    if ink is not None:
        inked = Image.new("RGBA", image.size, (*ink, 255))
        inked.putalpha(image.getchannel("A"))
        image = inked
    return image


def _fit(draw, text, font, width):
    """The text cut with an ellipsis to fit ``width``."""
    text = str(text or "")
    if draw.textlength(text, font=font) <= width:
        return text
    while text and draw.textlength(text + "...", font=font) > width:
        text = text[:-1]
    return f"{text.rstrip()}..." if text else ""


def shelf_top(height, band=None):
    """Where the shelf begins: just under the flat block's ground line, else where the band
    fading in at the foot starts."""
    if band is not None:
        return min(height - 40, band[0] + GROUND_KEEP)
    return height - SCRIM_HEIGHT


def card(image, shelf_height):
    """Titled art kept whole above the shelf: scaled to the height the shelf leaves and centred
    on a mat in its own deep hue. Returns (image, band), the band starting where the art ends."""
    rgb = image.convert("RGB")
    width, height = rgb.size
    art_height = max(1, height - shelf_height)
    art_width = max(1, round(width * art_height / height))
    mat = palette(rgb.resize((1, 1), Image.Resampling.BOX).getpixel((0, 0)))["base"]
    canvas = Image.new("RGB", (width, height), mat)
    canvas.paste(rgb.resize((art_width, art_height), Image.Resampling.LANCZOS), ((width - art_width) // 2, 0))
    return canvas, (art_height - GROUND_KEEP, mat)


def draw_shelf(image, info, band=None):
    """The art with its info shelf: on its flat block (``band`` = (top, colour)) or, without
    one, on a band fading in at its foot. ``info``: reasons [(icon, label)], price
    {discount, final, original} or {"label"}, line, tags [str], credit."""
    canvas = image.convert("RGB").copy()
    width, height = canvas.size
    if band is not None:
        _top, colour = band
        start = shelf_top(height, band)
        colours = palette(colour)
        canvas.paste(colours["base"], (0, start, width, height), Image.new("L", (width, height - start), SHELF_OPACITY))
        ImageDraw.Draw(canvas).rectangle((0, start, width, start + 2), fill=colours["rim"])
        content_top = start + 3
    else:
        start = shelf_top(height)
        foot = canvas.crop((0, height - SCRIM_HEIGHT, width, height)).resize((1, 1), Image.Resampling.BOX)
        colours = palette(foot.getpixel((0, 0)))
        mask = Image.new("L", (1, SCRIM_HEIGHT))
        for row in range(SCRIM_HEIGHT):
            share = min(1.0, row / SCRIM_FADE)
            mask.putpixel((0, row), round(SHELF_OPACITY * share * share * (3 - 2 * share)))
        canvas.paste(colours["base"], (0, start, width, height), mask.resize((width, SCRIM_HEIGHT)))
        content_top = start + SCRIM_FADE - 16
    draw = ImageDraw.Draw(canvas)
    rows = _rows(info)
    room = height - content_top
    while rows and sum(row_height for _kind, row_height in rows) + ROW_GAP * (len(rows) - 1) > room - SHELF_PADDING:
        rows.pop()
    if not rows:
        return canvas
    used = sum(row_height for _kind, row_height in rows) + ROW_GAP * (len(rows) - 1)
    y = content_top + max(8, (room - used) // 2)
    for kind, row_height in rows:
        if kind == "head":
            _draw_head(canvas, draw, info, y, width, colours)
        elif kind == "line":
            font = _font(21, True)
            draw.text((MARGIN, y - 1), _fit(draw, info.get("line"), font, width - 2 * MARGIN), font=font, fill=WHITE)
        else:
            _draw_foot(canvas, draw, info, y, width, colours)
        y += row_height + ROW_GAP
    return canvas


def rows_height(info):
    """How tall the shelf's rows stand together, gaps included."""
    rows = _rows(info)
    return sum(row_height for _kind, row_height in rows) + ROW_GAP * max(0, len(rows) - 1)


def band_fits(height, band, info):
    """The flat block when every row of the shelf fits on it, else None: the band fading in at
    the foot (or a card) then holds them all."""
    if band is None:
        return None
    room = height - shelf_top(height, band) - 3 - SHELF_PADDING
    return band if room >= rows_height(info) else None


def _rows(info):
    rows = []
    if info.get("reasons") or info.get("price"):
        rows.append(("head", HEAD_HEIGHT))
    if info.get("line"):
        rows.append(("line", 28))
    if info.get("tags") or info.get("credit"):
        rows.append(("foot", 24))
    return rows


def _text_y(draw, text, font, top, height):
    """The y that centres ``text`` in the row ``height`` tall from ``top``."""
    box = draw.textbbox((0, 0), text, font=font)
    return top + (height - (box[3] - box[1])) / 2 - box[1]


def _draw_head(canvas, draw, info, y, width, colours):
    """The reasons as chips at the left (icon and words); at the right the price, the price
    paid largest, after Steam's discount box and the struck price."""
    right = width - MARGIN
    price = info.get("price") or {}
    final_font = _font(PRICE_SIZE, True)
    if price.get("discount"):
        final = str(price.get("final") or "")
        final_w = draw.textlength(final, font=final_font)
        draw.text((right - final_w, _text_y(draw, final, final_font, y, HEAD_HEIGHT)), final, font=final_font, fill=WHITE)
        cut = f"-{int(price['discount'])}%"
        cut_font = _font(CUT_SIZE, True)
        cut_w = draw.textlength(cut, font=cut_font) + 14
        box = (right - final_w - 12 - cut_w, y + 2, right - final_w - 12, y + HEAD_HEIGHT - 2)
        draw.rectangle(box, fill=DEAL_GREEN)
        draw.text((box[0] + 7, _text_y(draw, cut, cut_font, y, HEAD_HEIGHT)), cut, font=cut_font, fill=DEAL_LIME)
        right = box[0] - 10
        original = str(price.get("original") or "")
        if original:
            small = _font(ORIGINAL_SIZE, False)
            original_w = draw.textlength(original, font=small)
            draw.text((right - original_w, _text_y(draw, original, small, y, HEAD_HEIGHT)), original, font=small, fill=WHITE)
            middle = y + HEAD_HEIGHT // 2
            draw.line((right - original_w, middle, right, middle), fill=WHITE, width=1)
            right -= original_w + 10
    elif price.get("label") or price.get("final"):
        label = str(price.get("label") or price.get("final"))
        label_w = draw.textlength(label, font=final_font)
        draw.text((right - label_w, _text_y(draw, label, final_font, y, HEAD_HEIGHT)), label, font=final_font, fill=WHITE)
        right -= label_w + 14
    x = MARGIN
    chip_top = y + (HEAD_HEIGHT - CHIP_HEIGHT) // 2
    chip_font = _font(14, True)
    for icon_name, label in info.get("reasons") or []:
        icon = _asset(icon_name, ICON_SIZE) if icon_name in REASON_ICONS else None
        text_w = draw.textlength(label, font=chip_font)
        chip_w = 12 + (ICON_SIZE + 5 if icon else 0) + text_w + 12
        if x + chip_w > right:
            break
        draw.rounded_rectangle((x, chip_top, x + chip_w, chip_top + CHIP_HEIGHT), radius=CHIP_HEIGHT // 2,
                               fill=colours["chip"])
        text_x = x + 12
        if icon:
            canvas.paste(icon, (int(text_x), chip_top + (CHIP_HEIGHT - icon.height) // 2), icon)
            text_x += ICON_SIZE + 5
        draw.text((text_x, _text_y(draw, label, chip_font, chip_top, CHIP_HEIGHT)), label, font=chip_font, fill=WHITE)
        x += chip_w + 8


def _fit_credit(draw, credit, font, width):
    """The credit line in ``width``: its parts in reading order joined by " / ", the readiest to
    give way dropped first while it is too wide, then cut. ``credit`` is a string or a list of
    (text, give) with give 0 never dropped."""
    if isinstance(credit, str):
        return _fit(draw, credit, font, width)
    parts = [(str(text), int(give)) for text, give in credit or [] if text]
    while parts:
        line = " / ".join(text for text, _give in parts)
        if draw.textlength(line, font=font) <= width or max(give for _text, give in parts) == 0:
            return _fit(draw, line, font, width)
        drop = max(range(len(parts)), key=lambda index: (parts[index][1], index))
        parts.pop(drop)
    return ""


def _studio_mark(logo):
    """The studio's logo at the credit's size, as it is published (its own square)."""
    return logo.convert("RGB").resize((STUDIO_LOGO, STUDIO_LOGO), Image.Resampling.LANCZOS)


def _studio_cutout(logo):
    """The studio's logo without the flat ground its avatar square is published on: when the four
    corners share one colour, that colour is cut away from the edges in, and a mark left dark is
    drawn in white for the dark bar. None when the avatar has no flat ground (a photo)."""
    size = STUDIO_LOGO * 4
    art = logo.convert("RGB").resize((size, size), Image.Resampling.LANCZOS)
    corners = [art.getpixel(point) for point in ((0, 0), (size - 1, 0), (0, size - 1), (size - 1, size - 1))]
    if max(max(channel) - min(channel) for channel in zip(*corners)) > STUDIO_GROUND_TOLERANCE:
        return None
    ground = tuple(sum(channel) // 4 for channel in zip(*corners))
    near = Image.new("L", art.size, 0)
    near.putdata([255 if max(abs(a - b) for a, b in zip(pixel, ground)) <= STUDIO_GROUND_TOLERANCE else 0
                  for pixel in art.get_flattened_data()])
    for corner in ((0, 0), (size - 1, 0), (0, size - 1), (size - 1, size - 1)):
        if near.getpixel(corner) == 255:
            ImageDraw.floodfill(near, corner, 128)
    alpha = near.point(lambda value: 0 if value == 128 else 255)
    if not alpha.getbbox() or alpha.histogram()[255] < size * size * 0.04:
        return None
    kept = [pixel for pixel, keep in zip(art.get_flattened_data(), alpha.get_flattened_data()) if keep]
    lightness = sum(0.299 * r + 0.587 * g + 0.114 * b for r, g, b in kept) / len(kept)
    mark = Image.new("RGB", art.size, WHITE) if lightness < STUDIO_DARK_MARK else art
    mark = mark.convert("RGBA")
    mark.putalpha(alpha.filter(ImageFilter.GaussianBlur(1)))
    return mark.resize((STUDIO_LOGO, STUDIO_LOGO), Image.Resampling.LANCZOS)


@lru_cache(maxsize=1)
def _studio_mask():
    mask = Image.new("L", (STUDIO_LOGO * 4, STUDIO_LOGO * 4), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, STUDIO_LOGO * 4 - 1, STUDIO_LOGO * 4 - 1), radius=12, fill=255)
    return mask.resize((STUDIO_LOGO, STUDIO_LOGO), Image.Resampling.LANCZOS)


def tag_colours(tags):
    """A colour per tag, by its words, none repeated on one shelf while colours remain."""
    used, colours = set(), []
    for tag in tags:
        index = zlib.crc32(str(tag).encode("utf-8")) % len(TAG_COLOURS)
        while index in used and len(used) < len(TAG_COLOURS):
            index = (index + 1) % len(TAG_COLOURS)
        used.add(index)
        colours.append(TAG_COLOURS[index])
    return colours


def _draw_foot(canvas, draw, info, y, width, colours):
    """The tags as chips in their own colours at the left; who made it and the Steam wordmark
    at the right."""
    right = width - MARGIN
    mark = _asset("steam_wordmark", WORDMARK_HEIGHT, WHITE)
    if mark is not None:
        canvas.paste(mark, (right - mark.width, y + (24 - mark.height) // 2), mark)
        right -= mark.width + 14
    credit_font = _font(13, False)
    tag_font = _font(13, True)
    tags_w = sum(draw.textlength(tag, font=tag_font) + 18 + 6 for tag in info.get("tags") or [])
    logo = info.get("studio_logo")
    logo_w = STUDIO_LOGO + 6 if logo is not None else 0
    budget = max(0, right - MARGIN - min(tags_w, (right - MARGIN) * 0.55) - 12 - logo_w)
    credit = _fit_credit(draw, info.get("credit"), credit_font, budget)
    if credit:
        credit_w = draw.textlength(credit, font=credit_font)
        draw.text((right - credit_w, _text_y(draw, credit, credit_font, y, 24)), credit, font=credit_font, fill=WHITE)
        right -= credit_w
        if logo is not None:
            right -= 6 + STUDIO_LOGO
            # The mark alone where its avatar has a flat ground; a photo keeps its rounded square.
            cutout = _studio_cutout(logo)
            if cutout is not None:
                canvas.paste(cutout, (int(right), y + (24 - STUDIO_LOGO) // 2), cutout)
            else:
                canvas.paste(_studio_mark(logo), (int(right), y + (24 - STUDIO_LOGO) // 2), _studio_mask())
        right -= 14
    x = MARGIN
    tags = list(info.get("tags") or [])
    for tag, fill in zip(tags, tag_colours(tags)):
        chip_w = draw.textlength(tag, font=tag_font) + 18
        if x + chip_w > right:
            break
        draw.rounded_rectangle((x, y + 1, x + chip_w, y + 23), radius=5, fill=fill)
        draw.text((x + 9, _text_y(draw, tag, tag_font, y + 1, 22)), tag, font=tag_font, fill=WHITE)
        x += chip_w + 6
