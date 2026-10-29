"""One page for the cover plugins (Daily Art, Magazine Covers, GCD Comic Covers).

A header names the page with what is on it and the date; up to four works stand side by
side on one shelf line, each framed with a soft shadow and as large as the page allows,
over a compact wall label of three lines (a numbered title; its date chip, first fact and
colour swatches; then its other facts run together); Daily Art adds a timeline that
places each work in its century. Everything stays inside the 8 px inset the theme chrome
frames.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageOps

from utils.app_utils import get_base_ui_font
from utils.safe_image import safe_open_image

MARGIN = 14
# The works get every row the header and label leave: the page ends BOTTOM px above its edge.
BOTTOM = 10
HEADER_TOP, HEADER_RULE = 11, 47
COLUMN_GAP = 12
# A label's three lines: the title, the chip line, then the rest of its facts.
TITLE_ROW, CHIP_ROW, FACT_ROW = 19, 19, 15
LABEL_H = TITLE_ROW + CHIP_ROW + FACT_ROW
# A label is as wide as its work, but never narrower than this.
LABEL_MIN_W = 220
TIMELINE_H = 30
SHADOW = 4
# A wide work standing on its foot leaves its column's top empty; a gap at least this tall
# takes up to FILLER_PER_GAP more works, under a FILLER_HEAD row heading, each titled under it.
FILLER_MIN_H, FILLER_PER_GAP, FILLER_HEAD, FILLER_CAPTION, FILLER_GAP = 120, 2, 18, 16, 10
# A page's own wordmark stands this tall in its header.
WORDMARK_H = 30
# Header art keeps this far from the subtitle and the date.
HEADER_ART_GAP = 12
# Sources named by their logos (assets/source_logos, tools/build_source_logos.py): in the header
# after its subtitle, HEADER_SOURCE_H tall, and at the head of a label's last line, LABEL_SOURCE_H.
SOURCE_LOGO_DIR = Path(__file__).with_name("assets") / "source_logos"
SOURCE_LOGOS = {
    "the met": "met", "the metropolitan museum of art": "met", "art institute of chicago": "aic",
    "europeana": "europeana", "harvard art museums": "harvard", "comic vine": "comicvine",
    "grand comics database": "gcd", "gcd": "gcd", "internet archive": "internet_archive",
    "wikimedia commons": "wikimedia_commons", "library of congress": "loc", "美国国会图书馆": "loc", "gallica": "gallica",
    "cleveland museum of art": "cleveland", "v&a": "vam", "victoria and albert museum": "vam", "smk": "smk",
    "smk – statens museum for kunst": "smk", "statens museum for kunst": "smk",
}
HEADER_SOURCE_H, LABEL_SOURCE_H = 18, 13
DAY = {"paper": (249, 246, 239), "ink": (24, 24, 28), "muted": (104, 100, 94), "rule": (206, 200, 189),
       "card": (255, 255, 255), "frame": (40, 38, 36)}
NIGHT = {"paper": (20, 21, 25), "ink": (238, 236, 230), "muted": (160, 156, 148), "rule": (70, 70, 76),
         "card": (32, 33, 38), "frame": (214, 210, 200)}
_YEAR = re.compile(r"(\d{3,4})")
_CENTURY = re.compile(r"(\d{1,2})(?:st|nd|rd|th)\s+century", re.IGNORECASE)


@dataclass
class Label:
    """A work's wall label: its title, up to three fact lines, a chip and where it is from."""

    title: str
    lines: tuple = ()
    chip: str = ""
    source: str = ""
    year: int | None = None
    # A paragraph (a cover's headline) wrapped into whatever room the label has left.
    body: str = ""
    extra: dict = field(default_factory=dict)
    # Where the work comes from, shown by its logo at the head of the last line (its name in text
    # when there is no logo for it).
    source_logo: str = ""


def year_of(text):
    """The year a date string names ("ca. 1750", "1780-85", "19th century", "500 B.C."), or None."""
    text = str(text or "")
    bce = bool(re.search(r"\bB\.?\s?C\.?(?:E\.?)?\b", text, re.IGNORECASE))
    match = _YEAR.search(text)
    if match:
        year = int(match.group(1))
        return -year if bce else year
    century = _CENTURY.search(text)
    if century:
        year = (int(century.group(1)) - 1) * 100 + 50
        return -year if bce else year
    return None


@lru_cache(maxsize=8)
def load_wordmark(path):
    """A page's wordmark asset (RGBA), or None when it cannot be read."""
    try:
        with safe_open_image(Path(path)) as source:
            return source.convert("RGBA")
    except (OSError, ValueError):
        return None


def palette(dark):
    return NIGHT if dark else DAY


@lru_cache(maxsize=16)
def _source_art(key):
    try:
        with safe_open_image(SOURCE_LOGO_DIR / f"{key}.png") as source:
            return source.convert("RGBA")
    except (OSError, ValueError):
        return None


def source_logo(name, colors, height):
    """The logo of the source called ``name`` ``height`` px tall, its near-black parts in the
    page's ink; None when there is none for it."""
    key = SOURCE_LOGOS.get(str(name or "").strip().casefold())
    art = _source_art(key) if key else None
    return _inked_wordmark(art, colors, height) if art is not None else None


def _font(size, bold=False):
    return get_base_ui_font(int(size), bold=bold)


def _clip(draw, text, font, width):
    text = str(text or "")
    if draw.textlength(text, font=font) <= width:
        return text
    while text and draw.textlength(text + "…", font=font) > width:
        text = text[:-1]
    return text.rstrip() + "…" if text else ""


def _wrap(draw, text, font, width, max_lines):
    words, lines, current = str(text or "").split(), [], ""
    for word in words:
        trial = f"{current} {word}".strip()
        if draw.textlength(trial, font=font) <= width or not current:
            current = trial
        else:
            lines.append(current)
            current = word
        if len(lines) == max_lines:
            break
    if current and len(lines) < max_lines:
        lines.append(current)
    if not lines:
        return []
    rest = " ".join(words)
    if " ".join(lines) != rest:
        lines[-1] = _clip(draw, lines[-1] + " …", font, width)
    return [_clip(draw, line, font, width) for line in lines]


def columns(count, dimensions, *, timeline=False, fact_rows=1):
    """[(image box, label box)] for ``count`` works on the page, their labels ``fact_rows`` fact
    lines deep."""
    width, height = dimensions
    count = max(1, int(count))
    col_w = (width - 2 * MARGIN - (count - 1) * COLUMN_GAP) / count
    label_h = LABEL_H + (fact_rows - 1) * FACT_ROW
    label_top = height - BOTTOM - label_h - (TIMELINE_H if timeline else 0)
    boxes = []
    for index in range(count):
        left = round(MARGIN + index * (col_w + COLUMN_GAP))
        right = round(MARGIN + index * (col_w + COLUMN_GAP) + col_w)
        boxes.append(((left, HEADER_RULE + 7, right, label_top - 5), (left, label_top, right, label_top + label_h)))
    return boxes


def art_sizes(count, dimensions, *, timeline=False, fact_rows=1):
    """The most each of ``count`` works can fill on the page: the size a cover is contained in,
    so a plugin can resize its bank images to it once, ahead of the page."""
    return [(right - left - SHADOW, bottom - top - SHADOW)
            for (left, top, right, bottom), _label in columns(count, dimensions, timeline=timeline,
                                                              fact_rows=fact_rows)]


def compose(images, labels, dimensions, *, title, subtitle="", date_text="", accent=(0, 70, 200), dark=False,
            timeline=False, logo=None, wordmark=None, style="gallery", header_art=None, sources=(), fact_rows=1,
            fillers=(), filler_heading=""):
    """The gallery page for ``images`` (already loaded) and their ``labels``.

    ``style`` sets the page's backdrop over the works' own colour wash: "gallery" lights each
    work with a spotlight, "newsstand" stands them on a shelf, "comic" lays a halftone.
    ``wordmark`` (RGBA) replaces the lettered title; its black parts take the page's ink.
    ``header_art`` (RGBA, transparent) fills the header between the subtitle and the date.
    ``sources`` are named after the subtitle by their logos (in text when one has none).
    ``fact_rows`` 2 gives each label a second fact line (the body, then its source facts).
    ``fillers`` ((image, label), ...) are more works for the room a wide work leaves above it,
    under ``filler_heading``.
    """
    colors = palette(dark)
    width, height = dimensions
    page = Image.new("RGB", (width, height), colors["paper"])
    boxes = columns(len(images), dimensions, timeline=timeline, fact_rows=fact_rows)
    _backdrop(page, images, boxes, colors, style, accent, dark)
    draw = ImageDraw.Draw(page)
    years = [label.year for label in labels if isinstance(label.year, int)]
    span = ""
    if len(years) >= 2 and max(years) > min(years):
        span = f"跨越 {_year_text(min(years))}–{_year_text(max(years))} · {max(years) - min(years)} 年"
    _header(page, draw, title, subtitle, date_text, accent, colors, width, logo, wordmark, header_art,
            sources=sources, span=span)
    fillers = list(fillers)
    for index, (image, label, (image_box, label_box)) in enumerate(zip(images, labels, boxes), start=1):
        # A wide work stands on its foot when more works fill the room above it; otherwise it is
        # centred in its slot rather than leaving that room empty.
        room = image_box[3] - image_box[1] - SHADOW - _contained_size(image, image_box)[1] - FILLER_GAP
        filled = bool(fillers) and room >= FILLER_MIN_H
        frame = _framed(page, image, image_box, colors, centre=not filled)
        if filled:
            taken, fillers = fillers[:FILLER_PER_GAP], fillers[FILLER_PER_GAP:]
            _fill_gap(page, draw, (image_box[0], image_box[1], image_box[2], frame[1] - FILLER_GAP), taken,
                      filler_heading, accent, colors)
        label_box = _under(frame, label_box)
        swatch_w = _swatches(draw, image, (label_box[2], label_box[1] + TITLE_ROW + 4), colors)
        _label(draw, label, label_box, index, accent, colors, numbered=timeline, reserve=swatch_w + 6, page=page,
               fact_rows=fact_rows)
    if timeline:
        _timeline(draw, labels, (MARGIN, height - BOTTOM - TIMELINE_H + 6, width - MARGIN, height - BOTTOM), accent,
                  colors)
    return page


def _header(page, draw, title, subtitle, date_text, accent, colors, width, logo, wordmark=None, art=None, sources=(),
            span=""):
    x = MARGIN
    if logo is not None:
        page.paste(logo, (x, HEADER_TOP + (28 - logo.height) // 2), logo)
        x += logo.width + 10
    mark = _inked_wordmark(wordmark, colors) if wordmark is not None else None
    if mark is not None:
        page.paste(mark, (x, HEADER_TOP + 30 - mark.height), mark)
        x += mark.width + 14
    else:
        if logo is None:
            draw.rectangle((x, HEADER_TOP + 8, x + 10, HEADER_TOP + 18), fill=accent)
            x += 18
        title_font = _font(22, True)
        draw.text((x, HEADER_TOP - 1), title, font=title_font, fill=colors["ink"])
        x += draw.textlength(title, font=title_font) + 14
    date_font = _font(12, True)
    date_w = draw.textlength(date_text, font=date_font) if date_text else 0
    if date_text:
        draw.text((width - MARGIN - date_w, HEADER_TOP + 8), date_text, font=date_font, fill=colors["ink"])
    sub_font = _font(12)
    limit = width - MARGIN - date_w - 16
    # The subtitle, then its sources by their logos (or names), then the works' span.
    parts = [("text", subtitle)] if subtitle else []
    for name in sources:
        mark = source_logo(name, colors, HEADER_SOURCE_H)
        parts.append(("logo", mark) if mark is not None else ("text", name))
    if span:
        parts.append(("text", span))
    previous = None
    for kind, part in parts:
        if previous is not None:
            joint = " " if kind == "logo" and previous == "logo" else " · "
            draw.text((x, HEADER_TOP + 8), joint, font=sub_font, fill=colors["muted"])
            x += draw.textlength(joint, font=sub_font)
        if kind == "logo":
            if x + part.width > limit:
                break
            page.paste(part, (round(x), HEADER_TOP + 15 - part.height // 2), part)
            x += part.width
        else:
            shown = _clip(draw, part, sub_font, limit - x)
            draw.text((x, HEADER_TOP + 8), shown, font=sub_font, fill=colors["muted"])
            x += draw.textlength(shown, font=sub_font)
        previous = kind
    if art is not None:
        _header_art(page, art, (round(x) + HEADER_ART_GAP, 3, round(width - MARGIN - date_w) - HEADER_ART_GAP, HEADER_RULE - 3))
    draw.line((MARGIN, HEADER_RULE, width - MARGIN, HEADER_RULE), fill=colors["ink"], width=2)


def _header_art(page, art, box):
    """``art`` as large as ``box`` holds, centred in it; left out when the room is too narrow."""
    left, top, right, bottom = box
    room_w, room_h = right - left, bottom - top
    if room_w < 60 or room_h < 12:
        return
    scale = min(room_w / art.width, room_h / art.height)
    fitted = art.resize((max(1, round(art.width * scale)), max(1, round(art.height * scale))), Image.Resampling.LANCZOS)
    page.paste(fitted, (left + (room_w - fitted.width) // 2, top + (room_h - fitted.height) // 2), fitted)


def _contained_size(image, box):
    """The (width, height) a work takes when contained in ``box`` beside its shadow."""
    left, top, right, bottom = box
    room_w, room_h = right - left - SHADOW, bottom - top - SHADOW
    scale = min(room_w / max(1, image.width), room_h / max(1, image.height))
    return round(image.width * scale), round(image.height * scale)


def _framed(page, image, box, colors, centre=False):
    """The work contained in ``box``, standing on its foot (or ``centre``d in the box's height),
    with a hairline frame and soft shadow."""
    left, top, right, bottom = box
    # A bank image already resized to art_sizes() comes back unchanged.
    art = ImageOps.contain(image.convert("RGB"), (right - left - SHADOW, bottom - top - SHADOW),
                           method=Image.Resampling.LANCZOS)
    x = left + (right - left - SHADOW - art.width) // 2
    y = (top + (bottom - top - SHADOW - art.height) // 2) if centre else bottom - SHADOW - art.height
    shadow = Image.new("L", (art.width + 4 * SHADOW, art.height + 4 * SHADOW), 0)
    ImageDraw.Draw(shadow).rectangle((SHADOW, SHADOW, shadow.width - SHADOW, shadow.height - SHADOW), fill=120)
    shadow = shadow.filter(ImageFilter.GaussianBlur(SHADOW / 1.5))
    tone = Image.new("RGB", shadow.size, tuple(max(0, value - 70) for value in colors["paper"]))
    page.paste(tone, (x - SHADOW + 2, y - SHADOW + 3), shadow)
    page.paste(art, (x, y))
    ImageDraw.Draw(page).rectangle((x - 1, y - 1, x + art.width, y + art.height), outline=colors["frame"], width=1)
    return x, y, x + art.width, y + art.height


def _fill_gap(page, draw, box, works, heading, accent, colors):
    """``works`` side by side in ``box``, under a short accent heading, each titled under its frame."""
    left, top, right, bottom = box
    if heading:
        draw.rectangle((left, top + 3, left + 4, top + 15), fill=accent)
        draw.text((left + 10, top + 1), heading, font=_font(12, True), fill=colors["ink"])
        top += FILLER_HEAD
    count = len(works)
    slot_w = (right - left - (count - 1) * COLUMN_GAP) / count
    caption_font = _font(11, True)
    for index, (image, label) in enumerate(works):
        slot_left = round(left + index * (slot_w + COLUMN_GAP))
        slot = (slot_left, top, round(slot_left + slot_w), bottom - FILLER_CAPTION)
        frame = _framed(page, image, slot, colors)
        title = label.title or "-"
        width = max(slot[2] - slot[0], frame[2] - frame[0])
        shown = _clip(draw, title, caption_font, width)
        middle = (frame[0] + frame[2]) / 2
        x = max(slot[0], min(slot[2] - draw.textlength(shown, font=caption_font), middle - draw.textlength(shown, font=caption_font) / 2))
        draw.text((x, bottom - FILLER_CAPTION + 2), shown, font=caption_font, fill=colors["ink"])


def _under(frame, box, min_width=LABEL_MIN_W):
    """The label box squared under the framed work, widened about it to ``min_width`` px
    inside its own column, so a lone narrow work keeps its label beneath it."""
    left, top, right, bottom = box
    x0, x1 = frame[0], frame[2]
    grow = max(0, min_width - (x1 - x0)) / 2
    x0, x1 = max(left, round(x0 - grow)), min(right, round(x1 + grow))
    return x0, top, x1, bottom


def _label(draw, label, box, index, accent, colors, numbered, reserve=0, page=None, fact_rows=1):
    """Three tight lines under a work: its (numbered) title; its chip and first fact, leaving
    ``reserve`` px at the right for the swatches; then its source's logo and its other facts
    run together."""
    left, top, right, _bottom = box
    x = left
    if numbered:
        draw.ellipse((x, top + 1, x + 16, top + 17), fill=accent)
        number_font = _font(10, True)
        number = str(index)
        draw.text((x + 8 - draw.textlength(number, font=number_font) / 2, top + 2), number, font=number_font,
                  fill=(255, 255, 255))
        x += 22
    title_font = _font(14, True)
    draw.text((x, top - 1), _clip(draw, label.title or "-", title_font, right - x), font=title_font, fill=colors["ink"])
    y = top + TITLE_ROW
    fact_font = _font(11)
    x = left
    if label.chip:
        chip_font = _font(10, True)
        chip_w = draw.textlength(label.chip, font=chip_font) + 10
        draw.rounded_rectangle((left, y + 1, left + chip_w, y + 16), radius=4, fill=accent)
        draw.text((left + 5, y + 2), label.chip, font=chip_font, fill=(255, 255, 255))
        x += chip_w + 6
    first = label.lines[0] if label.lines else ""
    if first:
        draw.text((x, y + 1), _clip(draw, first, fact_font, right - reserve - x), font=fact_font, fill=colors["ink"])
    y += CHIP_ROW
    # The source's logo leads the line (its name in text when it has none).
    x = left
    mark = source_logo(label.source_logo, colors, LABEL_SOURCE_H) if label.source_logo and page is not None else None
    if mark is not None:
        page.paste(mark, (x, y + 1 + (FACT_ROW - 2 - mark.height) // 2), mark)
        x += mark.width + 6
    named = label.source_logo if label.source_logo and mark is None else ""
    # Small text stays in solid ink: a grey this size dithers into noise on the panel.
    if fact_rows > 1 and label.body and label.source:
        # Two fact lines: the body beside the logo, the source facts under it.
        rows = (" · ".join(str(part) for part in (*label.lines[1:], label.body, named) if part), label.source)
    elif fact_rows > 1:
        # No body: the facts run on from beside the logo onto the second line, whole facts at a time.
        facts = [str(part) for part in (*label.lines[1:], label.source, named) if part]
        facts = [fact for text in facts for fact in text.split(" · ")]
        first = []
        while facts and draw.textlength(" · ".join([*first, facts[0]]), font=fact_font) <= right - x:
            first.append(facts.pop(0))
        rows = (" · ".join(first), " · ".join(facts)) if first else (" · ".join(facts),)
    else:
        rows = (" · ".join(str(part) for part in (*label.lines[1:], label.body, label.source, named) if part),)
    for row, text in enumerate(rows):
        if text:
            row_x = x if row == 0 else left
            draw.text((row_x, y + row * FACT_ROW), _clip(draw, text, fact_font, right - row_x), font=fact_font,
                      fill=colors["ink"])


def _timeline(draw, labels, box, accent, colors):
    """Each work's year on a century axis, numbered like its label."""
    left, top, right, bottom = box
    years = [label.year for label in labels if isinstance(label.year, int)]
    axis_y = top + 8
    draw.line((left, axis_y, right, axis_y), fill=colors["ink"], width=2)
    if not years:
        return
    start = math.floor((min(years) - 50) / 100) * 100
    end = max(start + 200, math.ceil((max(years) + 50) / 100) * 100)
    end = min(end, 2100)

    def x_of(year):
        return left + (right - left) * (year - start) / max(1, end - start)

    tick_font = _font(9)
    step = 100 if end - start <= 900 else 200
    for year in range(start, end + 1, step):
        x = x_of(year)
        draw.line((x, axis_y - 3, x, axis_y + 3), fill=colors["ink"], width=1)
        text = f"{abs(year)}{' BC' if year < 0 else ''}"
        text_w = draw.textlength(text, font=tick_font)
        draw.text((min(max(left, x - text_w / 2), right - text_w), axis_y + 5), text, font=tick_font,
                  fill=colors["muted"])
    number_font = _font(10, True)
    for index, label in enumerate(labels, start=1):
        if not isinstance(label.year, int):
            continue
        x = x_of(label.year)
        draw.ellipse((x - 8, axis_y - 8, x + 8, axis_y + 8), fill=accent, outline=colors["paper"], width=2)
        number = str(index)
        draw.text((x - draw.textlength(number, font=number_font) / 2, axis_y - 7), number, font=number_font,
                  fill=(255, 255, 255))


def _year_text(year):
    return f"公元前 {-year}" if year < 0 else str(year)


def _inked_wordmark(wordmark, colors, height=WORDMARK_H):
    """The page's wordmark ``height`` px tall, its near-black lettering in the page's ink."""
    mark = wordmark.convert("RGBA")
    box = mark.getchannel("A").point(lambda value: 255 if value > 24 else 0).getbbox()
    mark = mark.crop(box) if box else mark
    mark = mark.resize((max(1, round(mark.width * height / mark.height)), height), Image.Resampling.LANCZOS)
    pixels = mark.load()
    ink = colors["ink"]
    for py in range(mark.height):
        for px in range(mark.width):
            r, g, b, a = pixels[px, py]
            if a and max(r, g, b) < 90 and max(r, g, b) - min(r, g, b) < 40:
                pixels[px, py] = (*ink, a)
    return mark


def _backdrop(page, images, boxes, colors, style, accent, dark):
    """Each work's own colours washed faintly behind its column, then the page's theme:
    spotlights (gallery), a shelf (newsstand) or a halftone (comic)."""
    width, height = page.size
    wash = Image.new("RGB", page.size, colors["paper"])
    for image, (image_box, _label_box) in zip(images, boxes):
        left = max(0, image_box[0] - COLUMN_GAP // 2)
        right = min(width, image_box[2] + COLUMN_GAP // 2)
        tile = ImageOps.fit(image.convert("RGB"), (max(1, right - left), height), method=Image.Resampling.BILINEAR)
        wash.paste(tile.filter(ImageFilter.GaussianBlur(18)), (left, 0))
    wash = ImageEnhance.Color(wash.filter(ImageFilter.GaussianBlur(26))).enhance(0.7)
    page.paste(Image.blend(page, wash, 0.24 if dark else 0.2))
    layer = Image.new("RGBA", page.size, (0, 0, 0, 0))
    pen = ImageDraw.Draw(layer)
    if style == "gallery":
        light = (255, 236, 196) if dark else (255, 255, 255)
        for image_box, _label_box in boxes:
            cx, top = (image_box[0] + image_box[2]) / 2, image_box[1]
            half = (image_box[2] - image_box[0]) * 0.62
            for step in range(12, 0, -1):
                spread = half * step / 12
                pen.ellipse((cx - spread, top - 30 - spread * 0.2, cx + spread, top + spread * 1.5),
                            fill=(*light, round((26 if dark else 34) * (1 - step / 13))))
    elif style == "newsstand":
        for image_box, _label_box in boxes:
            # The shelf runs under the works' feet, clear of the labels below.
            shelf = image_box[3] - SHADOW + 1
            pen.rectangle((image_box[0] - COLUMN_GAP // 2, shelf, image_box[2] + COLUMN_GAP // 2, shelf + 5),
                          fill=(*colors["frame"], 150))
            pen.line((image_box[0] - COLUMN_GAP // 2, shelf, image_box[2] + COLUMN_GAP // 2, shelf),
                     fill=(*colors["card"], 200), width=1)
    elif style == "comic":
        for y in range(HEADER_RULE + 4, height, 9):
            for x in range((y // 9) % 2 * 4, width, 9):
                pen.ellipse((x, y, x + 2, y + 2), fill=(*accent, 34 if dark else 26))
    base = page.convert("RGBA")
    base.alpha_composite(layer)
    page.paste(base.convert("RGB"))


def _swatches(draw, image, corner, colors, count=5):
    """The work's main colours as a strip of squares ending at ``corner`` (right, top); its width."""
    small = image.convert("RGB").resize((48, 48), Image.Resampling.BILINEAR)
    quantized = small.quantize(colors=count, method=Image.Quantize.MEDIANCUT)
    raw = quantized.getpalette()[: count * 3]
    counts = sorted(quantized.getcolors(), reverse=True)
    tones = [tuple(raw[index * 3:index * 3 + 3]) for _count, index in counts[:count]]
    size, gap = 9, 2
    strip = len(tones) * (size + gap) - gap
    x, y = corner[0] - strip, corner[1]
    for tone in tones:
        draw.rectangle((x, y, x + size - 1, y + size - 1), fill=tone, outline=colors["frame"], width=1)
        x += size + gap
    return strip
