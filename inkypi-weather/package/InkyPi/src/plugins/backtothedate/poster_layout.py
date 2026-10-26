"""BacktotheDate's page: the poster (or three) on the theme's paper with what is known about it.

The poster comes first: its blank paper margins are trimmed and it is drawn as large as the
page allows, the details squeezed round it. One poster takes the left with a narrow column at
the right (the date and theme, the Chinese title, the pinyin and English
titles, the designer with their years, publisher, size and holding, a short account of the
theme from Chinese Wikipedia, a few tags, the sources), or, when that makes it larger (a wide
poster), the top with a strip of the same details below. Three portraits stand side by side
on one shelf, each captioned with its Chinese title, year and designer. Sections that do not
fit are left out, last first; a poster without stored details shows its title alone.

Every page is headed 宣传画廊, lettered like a 1958-1966 poster headline (generated art) and
followed by the Little Red Book's cover and a quotation of Mao's in a brush hand (a red double
rule where there is no room for one): over the poster when the poster leaves room above it,
else at the top of the details column, so a tall poster is not made smaller for it. The
quotation is drawn by lot from those that fit, the same one for the same poster.
"""

from __future__ import annotations

import random
import re
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageOps

from plugins.backtothedate.poster_meta import decode_photo, split_chinese
from plugins.backtothedate.quotations import QUOTATIONS
from utils.app_utils import get_base_ui_font
from utils.fonts import load_font
from utils.local_wordmark import paste_local_wordmark

# All text is Microsoft YaHei (the base UI font), the titles in its bold.
PALETTES = {
    "day": {"paper": (243, 234, 216), "ink": (40, 28, 20), "muted": (112, 92, 72), "accent": (160, 82, 45),
            "rule": (206, 188, 160), "shadow": (214, 198, 172)},
    "night": {"paper": (23, 16, 11), "ink": (238, 226, 208), "muted": (176, 154, 130), "accent": (226, 154, 102),
              "rule": (82, 63, 48), "shadow": (8, 5, 3)},
}
MARGIN = 12
GAP = 12
MIN_COLUMN = 190
MIN_STRIP = 66
SHELF_CAPTION = 44
TITLE_ART = Path(__file__).resolve().parent / "assets" / "title_wordmark.png"
TITLE_TEXT = "宣传画廊"
# The title's height, and the band it takes with the space under it.
TITLE_H = 34
TITLE_BAND = TITLE_H + 8
# The lettering's vermilion, for the double rule after it.
TITLE_RED = (200, 30, 30)
# The quotation beside the title: the Little Red Book's cover (a photograph, its ground cut
# away) BOOK_H tall, then Zhi Mang Xing kept to the quotations' characters, at the largest size
# that fits on one line, else at a smaller one on two, signed after its last line with a brush
# dash and Mao's signature SIGNATURE_SCALE times the writing's size; with less than
# QUOTE_MIN_ROOM beside the title, the double rule instead.
QUOTE_FONT = Path(__file__).resolve().parent / "assets" / "ZhiMangXing-Quotations.ttf"
BOOK_ART = Path(__file__).resolve().parent / "assets" / "little_red_book.png"
SIGNATURE_ART = Path(__file__).resolve().parent / "assets" / "mao_signature.png"
QUOTE_ONE_LINE, QUOTE_TWO_LINES = (26, 24, 22, 20, 18), (19, 18, 16, 15)
BOOK_H = TITLE_H + 6
SIGNATURE_DASH, SIGNATURE_SCALE, SIGNATURE_GAP = "——", 1.45, 6
QUOTE_MIN_ROOM = 150
_CJK = re.compile(r"[⺀-鿿＀-￯]")


def palette(mode):
    return PALETTES["night" if mode == "night" else "day"]


def _ui(size, bold=False):
    return get_base_ui_font(size, bold=bold)


def _title(size):
    return _ui(size, bold=True)


def trim_margins(image, share=0.015):
    """The scan without the blank paper round it: from each edge, rows/columns where fewer than
    ``share`` of the pixels differ from the paper (the corners' colour) are dropped. Keeps at
    least 60% of each side."""
    small = image.convert("L")
    small.thumbnail((400, 400))
    width, height = small.size
    corners = sorted(small.getpixel(point) for point in ((0, 0), (width - 1, 0), (0, height - 1), (width - 1, height - 1)))
    paper = (corners[1] + corners[2]) / 2
    mask = small.point(lambda value: 255 if abs(value - paper) > 28 else 0)

    def busy(box):
        region = mask.crop(box)
        return sum(region.histogram()[255:]) / max(1, region.width * region.height) >= share

    top = next((y for y in range(height) if busy((0, y, width, y + 1))), 0)
    bottom = next((y for y in range(height - 1, -1, -1) if busy((0, y, width, y + 1))), height - 1) + 1
    left = next((x for x in range(width) if busy((x, top, x + 1, bottom))), 0)
    right = next((x for x in range(width - 1, -1, -1) if busy((x, top, x + 1, bottom))), width - 1) + 1
    if right - left < 0.6 * width or bottom - top < 0.6 * height:
        return image
    scale_x, scale_y = image.width / width, image.height / height
    pad = 2
    return image.crop((max(0, round((left - pad) * scale_x)), max(0, round((top - pad) * scale_y)),
                       min(image.width, round((right + pad) * scale_x)), min(image.height, round((bottom + pad) * scale_y))))


def wrap(draw, text, font, width, max_lines):
    """Lines of ``text`` within ``width``: Latin breaks at spaces, Chinese anywhere; the last
    line ends in an ellipsis when text is left over."""
    tokens = re.findall(r"[⺀-鿿＀-￯]|[^\s⺀-鿿＀-￯]+|\s+", text or "")
    lines, line = [], ""
    for token in tokens:
        candidate = line + token
        if draw.textlength(candidate.strip(), font=font) <= width or not line.strip():
            line = candidate
            continue
        lines.append(line.strip())
        line = "" if token.isspace() else token
        if len(lines) == max_lines:
            break
    if len(lines) < max_lines and line.strip():
        lines.append(line.strip())
    elif len(lines) == max_lines and (line.strip() or len("".join(lines)) < len((text or "").replace(" ", ""))):
        last = lines[-1]
        while last and draw.textlength(last + "…", font=font) > width:
            last = last[:-1]
        lines[-1] = last.rstrip() + "…"
    return lines


def _poster(canvas, image, box, colors):
    """The poster contained in ``box`` with a soft offset shadow and a hairline; its box."""
    x0, y0, x1, y1 = box
    fitted = ImageOps.contain(image.convert("RGB"), (max(1, x1 - x0), max(1, y1 - y0)), method=Image.LANCZOS)
    draw = ImageDraw.Draw(canvas)
    draw.rectangle((x0 + 4, y0 + 4, x0 + fitted.width + 3, y0 + fitted.height + 3), fill=colors["shadow"])
    canvas.paste(fitted, (x0, y0))
    draw.rectangle((x0, y0, x0 + fitted.width - 1, y0 + fitted.height - 1), outline=colors["rule"])
    return x0, y0, x0 + fitted.width, y0 + fitted.height


def _designer_line(meta):
    parts = []
    for designer in meta.get("designers") or []:
        name = " ".join(part for part in (designer.get("zh"), designer.get("name")) if part)
        if designer is (meta.get("designers") or [None])[0] and meta.get("designer_years"):
            name += f" · {meta['designer_years']}"
        parts.append(name)
    return "、".join(parts)


def _publisher_line(meta):
    name, chinese, rest = split_chinese(meta.get("publisher") or "")
    return " · ".join(part for part in (chinese, rest) if part) if chinese else name


def _holding_line(meta):
    return " · ".join(part for part in (meta.get("call_nr"), meta.get("collection")) if part)


def _theme_name(meta):
    if meta.get("theme_zh"):
        return meta["theme_zh"]
    themes = meta.get("themes") or []
    return themes[0].get("name") if themes else ""


def _contained(size, room):
    scale = min(room[0] / size[0], room[1] / size[1])
    return max(1, round(size[0] * scale)), max(1, round(size[1] * scale))


@lru_cache(maxsize=16)
def _quote_font(size):
    return load_font(str(QUOTE_FONT), size)


def _two_lines(draw, text, font, width, tail=0):
    """``text`` in two lines within ``width``, the second leaving ``tail`` for the signature,
    broken after the comma nearest its middle (else anywhere); None when it does not fit."""
    breaks = [index + 1 for index, char in enumerate(text[:-1]) if char in "，、；："] or list(range(1, len(text)))
    for cut in sorted(breaks, key=lambda index: abs(len(text) / 2 - index)):
        lines = [text[:cut], text[cut:]]
        if draw.textlength(lines[0], font=font) <= width and draw.textlength(lines[1], font=font) + tail <= width:
            return lines
    return None


@lru_cache(maxsize=16)
def _signature(height, ink):
    """Mao's signature ``height`` px tall in ``ink``, or None without the file."""
    try:
        with Image.open(SIGNATURE_ART) as source:
            strokes = source.convert("RGBA").getchannel("A")
    except (OSError, ValueError):
        return None
    strokes = strokes.resize((max(1, round(strokes.width * height / strokes.height)), height), Image.LANCZOS)
    signature = Image.new("RGBA", strokes.size, (*ink, 255))
    signature.putalpha(strokes)
    return signature


def _signed(draw, font):
    """(dash font, dash width, signature height) after a quotation in ``font``, and the width
    they take after its last line."""
    dash_font = _quote_font(max(12, font.size - 4))
    dash_w = draw.textlength(SIGNATURE_DASH, font=dash_font)
    height = round(font.size * SIGNATURE_SCALE)
    signature = _signature(height, (0, 0, 0))
    width = SIGNATURE_GAP + dash_w + 2 + signature.width if signature is not None else 0
    return (dash_font, dash_w, height), width


def _quotation_layout(draw, text, width, two_line_sizes):
    """(font, lines) for a quotation, signed, in ``width`` beside the title: one line at a
    QUOTE_ONE_LINE size, else two at one of ``two_line_sizes``; None when it does not fit."""
    if QUOTE_FONT.is_file():
        for size in QUOTE_ONE_LINE:
            font = _quote_font(size)
            if draw.textlength(text, font=font) + _signed(draw, font)[1] <= width:
                return font, [text]
        for size in two_line_sizes:
            font = _quote_font(size)
            lines = _two_lines(draw, text, font, width, _signed(draw, font)[1])
            if lines:
                return font, lines
    return None


@lru_cache(maxsize=4)
def _book(height):
    """The Little Red Book's cover ``height`` px tall, or None without the file."""
    try:
        with Image.open(BOOK_ART) as source:
            book = source.convert("RGBA")
    except (OSError, ValueError):
        return None
    return book.resize((max(1, round(book.width * height / book.height)), height), Image.LANCZOS)


def _draw_quotation(canvas, box, colors, seed):
    """The Little Red Book and a quotation drawn by ``seed`` from those that fit ``box`` beside
    it; False when none does."""
    x0, y0, x1, y1 = box
    draw = ImageDraw.Draw(canvas)
    book = _book(BOOK_H)
    book_w = book.width + 10 if book is not None else 0
    order = [text for text, _source in QUOTATIONS]
    random.Random(seed).shuffle(order)
    # Drawn from those that fit at a readable size; the smallest sizes only when none does.
    layout = next((found for sizes in (QUOTE_TWO_LINES[:2], QUOTE_TWO_LINES[2:]) for text in order
                   if (found := _quotation_layout(draw, text, x1 - x0 - book_w, sizes))), None)
    if layout is None:
        return False
    middle = (y0 + y1) / 2
    if book is not None:
        top = round(middle - book.height / 2)
        shadow = Image.new("RGBA", book.size, (*colors["shadow"], 255))
        canvas.paste(shadow, (x0 + 2, top + 2), book)
        canvas.paste(book, (x0, top), book)
    font, lines = layout
    pitch = font.size + 2
    top = middle - pitch * (len(lines) - 1) / 2
    for index, line in enumerate(lines):
        # Twice, a pixel apart: the hand's hairline strokes given a brush's weight on e-paper.
        for nudge in (0, 1):
            draw.text((x0 + book_w + nudge, top + index * pitch), line, font=font, fill=colors["ink"], anchor="lm")
    # Signed after the last line: a brush dash, then his signature.
    (dash_font, dash_w, height), _width = _signed(draw, font)
    signature = _signature(height, tuple(colors["ink"]))
    if signature is not None:
        x = x0 + book_w + draw.textlength(lines[-1], font=font) + SIGNATURE_GAP
        y = top + (len(lines) - 1) * pitch
        for nudge in (0, 1):
            draw.text((x + nudge, y), SIGNATURE_DASH, font=dash_font, fill=colors["ink"], anchor="lm")
        for nudge in (0, 1):
            canvas.paste(signature, (round(x + dash_w + 2) + nudge, round(y - signature.height / 2)), signature)
    return True


def _page_title(canvas, box, colors, seed=""):
    """The page's title at the left of ``box`` (the lettering, or bold text without it), then the
    Little Red Book and a quotation drawn by ``seed``, or a red double rule to the box's right
    where no quotation fits."""
    x0, y0, x1, y1 = box
    draw = ImageDraw.Draw(canvas)
    pasted = paste_local_wordmark(canvas, TITLE_ART, (x0, y0, min(x1, x0 + 512), y1), align="left")
    if pasted:
        right = pasted[2]
    else:
        font = _title(TITLE_H - 10)
        draw.text((x0, (y0 + y1) / 2), TITLE_TEXT, font=font, fill=colors["accent"], anchor="lm")
        right = x0 + round(draw.textlength(TITLE_TEXT, font=font))
    if x1 - right >= QUOTE_MIN_ROOM and _draw_quotation(canvas, (right + 12, y0, x1, y1), colors, seed):
        return
    if x1 - right >= 40:
        middle = (y0 + y1) // 2
        draw.rectangle((right + 10, middle - 3, x1 - 1, middle - 1), fill=TITLE_RED)
        draw.line((right + 10, middle + 2, x1 - 1, middle + 2), fill=TITLE_RED, width=1)


def _quotation_seed(posters):
    """The posters' identity, so the same poster keeps the same quotation through redraws."""
    return "|".join(str((poster or {}).get("url") or (poster or {}).get("title") or "") for poster in posters)


def render_single(poster, image, dimensions, mode="day", extras=()):
    colors = palette(mode)
    width, height = dimensions
    canvas = Image.new("RGB", dimensions, colors["paper"])
    image = trim_margins(image)
    seed = _quotation_seed([poster])
    beside = _contained(image.size, (width - 2 * MARGIN - MIN_COLUMN - GAP, height - 2 * MARGIN))
    above = _contained(image.size, (width - 2 * MARGIN, height - 2 * MARGIN - TITLE_BAND - MIN_STRIP - GAP))
    if above[0] * above[1] > beside[0] * beside[1] * 1.05:
        left = (width - above[0]) // 2
        _page_title(canvas, (left, MARGIN, left + above[0], MARGIN + TITLE_H), colors, seed)
        top = MARGIN + TITLE_BAND
        box = _poster(canvas, image, (left, top, left + above[0], top + above[1]), colors)
        _info_strip(canvas, poster, (MARGIN, box[3] + GAP, width - MARGIN, height - MARGIN), colors)
        return canvas
    room = height - 2 * MARGIN - TITLE_BAND
    column_top = MARGIN
    if beside[1] <= room:
        # A poster held by its width leaves room above it: the title heads it, the poster
        # sits centred in the rest.
        _page_title(canvas, (MARGIN, MARGIN, MARGIN + beside[0], MARGIN + TITLE_H), colors, seed)
        top = MARGIN + TITLE_BAND + (room - beside[1]) // 2
    else:
        top = (height - beside[1]) // 2
        column_top += TITLE_BAND
    box = _poster(canvas, image, (MARGIN, top, MARGIN + beside[0], top + beside[1]), colors)
    if column_top > MARGIN:
        _page_title(canvas, (box[2] + GAP, MARGIN, width - MARGIN, MARGIN + TITLE_H), colors, seed)
    _info_column(canvas, poster, (box[2] + GAP, column_top, width - MARGIN, height - MARGIN), colors, extras)
    return canvas


def _info_strip(canvas, poster, box, colors):
    """The details in a strip under a wide poster: the titles at the left, the designer,
    publisher and holding in the middle, the theme's background at the right."""
    draw = ImageDraw.Draw(canvas)
    x0, y0, x1, y1 = box
    meta = dict((poster or {}).get("meta") or {})
    title_en = meta.get("title_en") or str((poster or {}).get("title") or "")
    third = (x1 - x0 - 2 * GAP) / 3
    left_w = round(third * 1.15)
    y = y0
    kicker = "  ·  ".join(part for part in (meta.get("date"), _theme_name(meta)) if part)
    if kicker:
        font = _ui(10, bold=True)
        draw.text((x0, y), wrap(draw, kicker, font, left_w, 1)[0], font=font, fill=colors["accent"])
        y += 14
    if meta.get("title_zh"):
        for size in (20, 18, 16):
            font = _title(size)
            lines = wrap(draw, meta["title_zh"], font, left_w, 1)
            if not lines[0].endswith("…"):
                break
        draw.text((x0, y), lines[0], font=font, fill=colors["ink"])
        y += size + 4
        if title_en and y + 13 <= y1:
            font = _ui(10)
            draw.text((x0, y), wrap(draw, title_en, font, left_w, 1)[0], font=font, fill=colors["ink"])
    elif title_en:
        font = _ui(14, bold=True)
        for line in wrap(draw, title_en, font, left_w, 2):
            draw.text((x0, y), line, font=font, fill=colors["ink"])
            y += 18
    middle = x0 + left_w + GAP
    middle_w = round(third * 0.95)
    rows = [(label, value) for label, value in (("设计", _designer_line(meta)), ("出版", _publisher_line(meta)),
                                                ("馆藏", _holding_line(meta) or meta.get("size"))) if value]
    label_font, value_font = _ui(9, bold=True), _ui(10)
    y = y0 + 1
    for label, value in rows:
        if y + 13 > y1:
            break
        draw.text((middle, y + 1), label, font=label_font, fill=colors["accent"])
        draw.text((middle + 26, y), wrap(draw, value, value_font, middle_w - 26, 1)[0], font=value_font,
                  fill=colors["ink"])
        y += 15
    right = middle + middle_w + GAP
    right_w = x1 - right
    summary = meta.get("theme_summary")
    y = y0 + 1
    if summary:
        draw.text((right, y), "时代背景", font=_ui(9, bold=True), fill=colors["accent"])
        y += 13
        font = _ui(9)
        for line in wrap(draw, summary, font, right_w, max(1, int((y1 - y - 11) // 12))):
            draw.text((right, y), line, font=font, fill=colors["ink"])
            y += 12
    sources = ["chineseposters.net"] + (["中文维基百科"] if summary else [])
    footer = " · ".join(sources)
    font = _ui(8)
    draw.text((x1 - draw.textlength(footer, font=font), y1 - 10), footer, font=font, fill=colors["muted"])


def _info_column(canvas, poster, box, colors, extras=()):
    draw = ImageDraw.Draw(canvas)
    x0, y0, x1, y1 = box
    width = x1 - x0
    meta = dict((poster or {}).get("meta") or {})
    title_en = meta.get("title_en") or str((poster or {}).get("title") or "")
    footer_font = _ui(8)
    footer_y = y1 - 10
    sources = ["chineseposters.net"] + (["中文维基百科"] if meta.get("theme_summary") else [])
    draw.text((x0, footer_y), " · ".join(sources), font=footer_font, fill=colors["muted"])
    limit = footer_y - 8
    y = y0

    kicker = "  ·  ".join(part for part in (meta.get("date"), _theme_name(meta)) if part)
    if kicker:
        font = _ui(10, bold=True)
        for line in wrap(draw, kicker, font, width, 1):
            draw.text((x0, y), line, font=font, fill=colors["accent"])
        y += 14
        draw.line((x0, y, x0 + 30, y), fill=colors["accent"], width=2)
        y += 7

    title_zh = meta.get("title_zh")
    if title_zh:
        for size in (22, 20, 18, 16):
            font = _title(size)
            lines = wrap(draw, title_zh, font, width, 3)
            if not lines[-1].endswith("…") or size == 16:
                break
        for line in lines:
            draw.text((x0, y), line, font=font, fill=colors["ink"])
            y += size + 4
        if meta.get("pinyin"):
            font = _ui(9)
            for line in wrap(draw, meta["pinyin"], font, width, 2):
                draw.text((x0, y), line, font=font, fill=colors["muted"])
                y += 12
        if title_en:
            font = _ui(10)
            for line in wrap(draw, title_en, font, width, 2):
                draw.text((x0, y), line, font=font, fill=colors["ink"])
                y += 13
    elif title_en:
        font = _ui(16, bold=True)
        for line in wrap(draw, title_en, font, width, 3):
            draw.text((x0, y), line, font=font, fill=colors["ink"])
            y += 21

    rows = [(label, value) for label, value in (("设计", _designer_line(meta)), ("年代", meta.get("date") if not kicker else ""),
                                                ("出版", _publisher_line(meta)), ("尺寸", meta.get("size")),
                                                ("馆藏", _holding_line(meta))) if value]
    if rows and y + 20 < limit:
        y += 4
        draw.line((x0, y, x1, y), fill=colors["rule"], width=1)
        y += 6
        label_font, value_font = _ui(9, bold=True), _ui(10)
        for label, value in rows:
            lines = wrap(draw, value, value_font, width - 28, 2)
            if y + 13 * len(lines) > limit:
                break
            draw.text((x0, y + 1), label, font=label_font, fill=colors["accent"])
            for line in lines:
                draw.text((x0 + 28, y), line, font=value_font, fill=colors["ink"])
                y += 13
            y += 2

    summary = meta.get("theme_summary")
    if summary and y + 40 < limit:
        y += 3
        draw.line((x0, y, x1, y), fill=colors["rule"], width=1)
        y += 6
        heading = "时代背景" + (f" · {meta['theme_zh']}" if meta.get("theme_zh") else "")
        draw.text((x0, y), wrap(draw, heading, _ui(10, bold=True), width, 1)[0], font=_ui(10, bold=True),
                  fill=colors["accent"])
        y += 15
        font = _ui(9)
        room = max(1, int((limit - y - 20) // 12))
        for line in wrap(draw, summary, font, width, min(room, 8)):
            draw.text((x0, y), line, font=font, fill=colors["ink"])
            y += 12

    tags = [tag for tag in meta.get("tags") or [] if _CJK.search(tag)][:4]
    if tags and y + 18 < limit:
        y += 5
        font = _ui(9)
        x = x0
        for tag in tags:
            tag_width = draw.textlength(tag, font=font) + 10
            if x + tag_width > x1:
                break
            draw.rounded_rectangle((x, y, x + tag_width, y + 15), radius=7, outline=colors["accent"], width=1)
            draw.text((x + 5, y + 1), tag, font=font, fill=colors["ink"])
            x += tag_width + 4
        y += 15
    _fill_room(canvas, (x0, y + 10, x1, limit), meta, extras, colors)


def _fill_room(canvas, box, meta, extras, colors):
    """Fill the column's spare room down to the footer with pictures, each captioned: the
    theme's photo from Wikipedia, then other banked posters ("另见"). They are packed in rows
    (in this order) the way that covers the most of the room, none under 60px tall."""
    x0, y0, x1, y1 = box
    if y1 - y0 < 74 or x1 - x0 < 100:
        return
    items = []
    photo = decode_photo(meta.get("theme_photo"))
    if photo is not None:
        items.append((photo, f"{meta.get('theme_zh') or '时代背景'} · 维基百科配图"))
    for other, image in extras or ():
        other_meta = dict((other or {}).get("meta") or {})
        title = other_meta.get("title_zh") or other_meta.get("title_en") or str((other or {}).get("title") or "")
        items.append((trim_margins(image), "另见 · " + " · ".join(part for part in (title, other_meta.get("date")) if part)))
    rows = pack_rows([image.width / image.height for image, _caption in items], (x1 - x0, y1 - y0))
    draw = ImageDraw.Draw(canvas)
    font = _ui(9)
    y = y0
    for row in rows:
        x = x0
        for index, size in row:
            image, caption = items[index]
            placed = _poster(canvas, image, (x, y, x + size[0], y + size[1]), colors)
            draw.text((x, placed[3] + 3), wrap(draw, caption, font, size[0], 1)[0], font=font, fill=colors["muted"])
            x += size[0] + PACK_GAP
        y += max(size[1] for _index, size in row) + PACK_CAPTION + PACK_GAP


PACK_GAP = 8
PACK_CAPTION = 14
PACK_MIN_HEIGHT = 60


def pack_rows(aspects, room):
    """[[(index, (w, h)), ...] per row] for pictures of these aspects (width/height) in a room
    (w, h): the first k of them, k as large as helps, split in consecutive rows, each row as
    tall as the room's width allows, all shrunk together to fit its height (a caption under
    each row); the arrangement covering the most area, [] when nothing is 60px tall."""
    width, height = room
    best, best_area = [], 0.0
    for count in range(1, len(aspects) + 1):
        for cuts in range(1 << (count - 1)):
            rows, row = [], [0]
            for index in range(1, count):
                if cuts >> (index - 1) & 1:
                    rows.append(row)
                    row = []
                row.append(index)
            rows.append(row)
            heights = [(width - PACK_GAP * (len(row) - 1)) / sum(aspects[i] for i in row) for row in rows]
            fixed = len(rows) * PACK_CAPTION + (len(rows) - 1) * PACK_GAP
            scale = min(1.0, (height - fixed) / sum(heights)) if height > fixed else 0.0
            heights = [value * scale for value in heights]
            if not heights or min(heights) < PACK_MIN_HEIGHT:
                continue
            area = sum(aspects[i] * heights[r] ** 2 for r, row in enumerate(rows) for i in row)
            if area > best_area * 1.02:
                best_area = area
                best = [[(i, (max(1, int(aspects[i] * heights[r])), max(1, int(heights[r])))) for i in row]
                        for r, row in enumerate(rows)]
    return best


def render_shelf(items, dimensions, mode="day"):
    """Three posters on one shelf under the page's title, each captioned below: Chinese title,
    year and designer."""
    colors = palette(mode)
    width, height = dimensions
    canvas = Image.new("RGB", dimensions, colors["paper"])
    draw = ImageDraw.Draw(canvas)
    _page_title(canvas, (MARGIN, MARGIN, width - MARGIN, MARGIN + TITLE_H), colors,
                _quotation_seed([poster for poster, _image in items]))
    count = len(items)
    gap = 10
    column = (width - 2 * MARGIN - gap * (count - 1)) / count
    trimmed = [trim_margins(image) for _poster, image in items]
    room_h = height - 2 * MARGIN - TITLE_BAND - SHELF_CAPTION
    sizes = [_contained(image.size, (int(column), room_h)) for image in trimmed]
    shelf = MARGIN + TITLE_BAND + max(size[1] for size in sizes)
    for index, ((poster, _image), image, size) in enumerate(zip(items, trimmed, sizes)):
        left = MARGIN + index * (column + gap)
        x = round(left + (column - size[0]) / 2)
        _poster(canvas, image, (x, shelf - size[1], x + size[0], shelf), colors)
        meta = dict((poster or {}).get("meta") or {})
        y = shelf + 7
        title_zh = meta.get("title_zh")
        if title_zh:
            font = _title(15)
            draw.text((left, y), wrap(draw, title_zh, font, column, 1)[0], font=font, fill=colors["ink"])
            y += 19
        else:
            font = _ui(11, bold=True)
            draw.text((left, y), wrap(draw, meta.get("title_en") or str((poster or {}).get("title") or ""), font,
                                      column, 1)[0], font=font, fill=colors["ink"])
            y += 16
        designer = (meta.get("designers") or [{}])[0]
        detail = " · ".join(part for part in (meta.get("date"), designer.get("zh") or designer.get("name"),
                                              _theme_name(meta)) if part)
        bottom = height - MARGIN + 4
        if detail and y + 13 <= bottom:
            font = _ui(10)
            for line in wrap(draw, detail, font, column, 1):
                draw.text((left, y + 1), line, font=font, fill=colors["accent"])
            y += 15
        # Whatever room is left under the shelf takes the rest of the details.
        font = _ui(9)
        for text, lines in ((_publisher_line(meta), 1), (" · ".join(part for part in (meta.get("size"), meta.get("call_nr")) if part), 1),
                            (meta.get("theme_summary"), 6)):
            if not text:
                continue
            room = int((bottom - y) // 12)
            for line in wrap(draw, text, font, column, min(lines, room)) if room > 0 else ():
                draw.text((left, y), line, font=font, fill=colors["ink"])
                y += 12
    return canvas
