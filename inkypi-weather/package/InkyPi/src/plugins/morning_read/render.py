"""800 x 480 晨读 page.

Header: the title and today's date. Left, stacked: the word of the day (the word large, its sound
and part of speech, the definition and an example), the quote of the day with its author, and two
short facts. Right: today's comic strip whole (never cropped), with its name, title and alt text.
One colour per card, drawn as a band along its top; a card with nothing to show gives its room to
the others, and with no strip the left column takes the width.
"""

from __future__ import annotations

from PIL import Image

from utils.page_canvas import PageCanvas
from utils.rich_text import fit_box, fitted_lines, rich_lines

from . import sources

DAY = {"paper": (255, 255, 255), "ink": (18, 26, 32), "muted": (70, 80, 88), "rule": (176, 176, 176),
       "track": (226, 229, 233)}
NIGHT = {"paper": (0, 0, 0), "ink": (237, 241, 234), "muted": (190, 198, 204), "rule": (88, 88, 88),
         "track": (44, 48, 54)}
KINDS = {
    "单词": ((37, 84, 199), (110, 150, 255)),
    "名言": ((124, 58, 237), (172, 140, 255)),
    "冷知识": ((5, 150, 105), (52, 211, 153)),
    "漫画": ((217, 119, 6), (255, 178, 64)),
}
WEEKDAYS = sources.WEEKDAYS
HEADER_RULE = 56
BODY_TOP = 64
BOTTOM = 474
LEFT_COLUMN = (10, 398)
COMIC_COLUMN = (406, 790)
GAP = 8
BAND = 5
RADIUS = 6
# The left cards' shares of the column when all three are present.
SHARES = {"word": 178, "quote": 136, "facts": 92}
DEFINITION_SIZES = (15, 14, 13, 12)


def _palette(settings):
    theme = (settings or {}).get("_inkypi_theme") or {}
    night = theme.get("mode", (settings or {}).get("themeMode")) == "night"
    palette = dict(NIGHT if night else DAY)
    for kind, (day, dark) in KINDS.items():
        palette[kind] = dark if night else day
    return palette, night


def _card(canvas, box, kind, title, note=""):
    left, top, right, bottom = box
    canvas.draw.rounded_rectangle(box, radius=RADIUS, outline=canvas.color("rule"), width=1)
    canvas.draw.rounded_rectangle((left, top, right, top + BAND), radius=RADIUS, fill=canvas.color(kind),
                                  corners=(True, True, False, False))
    width = canvas.text((left + 10, top + BAND + 13), title, 14, kind, anchor="lm")
    if note:
        canvas.text((left + 18 + width, top + BAND + 13), note, 11, "muted", anchor="lm", bold=False,
                    width=right - left - 30 - width)
    return top + BAND + 26


def _paragraph(canvas, x, y, text, sizes, width, height, *, ratio=1.4, colour="ink", bold=False):
    """``text`` from (x, y) at the largest of ``sizes`` that fits ``height``; returns the y below it."""
    _size, lines, line_height = fit_box(canvas, text, sizes, width, height, ratio, bold)
    for line, _start in lines:
        canvas.text((x, y), line, _size, colour, anchor="la", bold=bold)
        y += line_height
    return y


def fits(canvas, text, sizes, width, height, ratio=1.4):
    """Whether ``text`` fits ``height`` whole at one of ``sizes``."""
    return any(rich_lines(canvas, text, size, width, int(height // round(size * ratio))) for size in sizes)


def first_sentence(text):
    """The text up to its first full stop, or all of it."""
    text = " ".join(str(text or "").split())
    for index, char in enumerate(text):
        if char in ".!?。！？" and (index + 1 == len(text) or text[index + 1] == " "):
            return text[:index + 1]
    return text


def _word(canvas, box, word):
    left, top, right, bottom = box
    y = _card(canvas, box, "单词", "今日单词", word.get("source") or "")
    width = right - left - 24
    size = 32
    while size > 20 and canvas.width(word["word"], size) > width * 0.62:
        size -= 1
    word_w = canvas.text((left + 12, y + size * 0.85), word["word"], size, anchor="ls")
    x = left + 20 + word_w
    if word.get("part_of_speech"):
        x += canvas.pill((x, y + size * 0.85 - 18), word["part_of_speech"], 11, fill="单词", height=18, pad=6) + 8
    if word.get("phonetic"):
        canvas.text((x, y + size * 0.85), word["phonetic"], 12, "muted", anchor="ls", bold=False, width=right - x - 10)
    y += size + 6
    example = word.get("example") or ""
    example_room = 34 if example else 0
    definition = word.get("definition") or ""
    room = bottom - y - 6 - example_room
    # A definition too long for the card keeps its first sentence.
    if not fits(canvas, definition, DEFINITION_SIZES, width, room):
        definition = first_sentence(definition)
    y = _paragraph(canvas, left + 12, y, definition, DEFINITION_SIZES, width, room)
    if example and bottom - y > 20:
        _paragraph(canvas, left + 12, y + 4, f"例：{example}", (12, 11), width, bottom - y - 8, ratio=1.35,
                   colour="muted")


def _quote(canvas, box, quote):
    left, top, right, bottom = box
    y = _card(canvas, box, "名言", "每日名言", quote.get("source") or "")
    width = right - left - 24
    author = quote.get("author") or ""
    y = _paragraph(canvas, left + 12, y, f"“{quote['text']}”", (17, 16, 15, 14, 13, 12), width,
                   bottom - y - (24 if author else 6))
    if author:
        canvas.text((right - 12, min(y + 2, bottom - 21)), f"— {author}", 12, "名言", anchor="ra", width=width)


def _facts(canvas, box, facts):
    left, top, right, bottom = box
    y = _card(canvas, box, "冷知识", "冷知识")
    width = right - left - 34
    share = (bottom - y - 4) / max(1, len(facts))
    for index, fact in enumerate(facts):
        top_y = y + share * index
        canvas.draw.ellipse((left + 12, top_y + 6, left + 18, top_y + 12), fill=canvas.color("冷知识"))
        _paragraph(canvas, left + 24, top_y, fact["text"], (14, 13, 12, 11), width, share - 4, ratio=1.32)


def comic_box(picture_size, area):
    """Where the strip goes in ``area``: as large as it fits whole, centred."""
    left, top, right, bottom = area
    scale = min((right - left) / picture_size[0], (bottom - top) / picture_size[1])
    width, height = max(1, round(picture_size[0] * scale)), max(1, round(picture_size[1] * scale))
    x, y = left + (right - left - width) // 2, top + (bottom - top - height) // 2
    return x, y, width, height


def _comic(canvas, box, comic, picture):
    left, top, right, bottom = box
    y = _card(canvas, box, "漫画", "今日漫画", comic.get("name") or "")
    title = comic.get("title") or ""
    if title:
        canvas.text((left + 12, y + 8), title, 15, anchor="lm", width=right - left - 24)
        y += 20
    caption = comic.get("caption") or ""
    caption_lines = fitted_lines(canvas, caption, (11,), right - left - 24, 3)[1] if caption else []
    caption_h = len(caption_lines) * 15 + (6 if caption_lines else 0)
    x, image_y, width, height = comic_box(picture.size, (left + 8, y + 4, right - 8, bottom - 6 - caption_h))
    canvas.image.paste(picture.convert("RGB").resize((width, height), Image.Resampling.LANCZOS), (x, image_y))
    text_y = image_y + height + 6
    for line, _start in caption_lines:
        canvas.text((left + 12, text_y), line, 11, "muted", anchor="la", bold=False)
        text_y += 15


def _header(canvas, now):
    width = canvas.text((14, 28), "晨读", 30, anchor="lm")
    canvas.text((26 + width, 34), "单词 · 名言 · 冷知识 · 漫画", 12, "muted", anchor="lm", bold=False)
    canvas.text((790, 28), f"{now.month}月{now.day}日 周{WEEKDAYS[now.weekday()]}", 20, anchor="rm")
    canvas.rule((10, HEADER_RULE, 790, HEADER_RULE), "ink", 2)


def render_page(dimensions, payload, settings, now, comic_picture=None):
    palette, _night = _palette(settings)
    canvas = PageCanvas(palette, family=(settings or {}).get("fontFamily") or "Microsoft YaHei")
    _header(canvas, now)
    words = ((payload.get("words") or {}).get("data")) or {}
    facts = (((payload.get("facts") or {}).get("data")) or {}).get("facts") or []
    comic = ((payload.get("comic") or {}).get("data")) or {}
    cards = []
    if (words.get("word") or {}).get("word"):
        cards.append(("word", words["word"]))
    if (words.get("quote") or {}).get("text"):
        cards.append(("quote", words["quote"]))
    if facts:
        cards.append(("facts", facts))
    has_comic = comic_picture is not None and bool(comic)
    right = LEFT_COLUMN[1] if has_comic else COMIC_COLUMN[1]
    if cards:
        total = sum(SHARES[name] for name, _data in cards)
        room = BOTTOM - BODY_TOP - GAP * (len(cards) - 1)
        y = BODY_TOP
        for index, (name, data) in enumerate(cards):
            height = room * SHARES[name] / total
            box = (LEFT_COLUMN[0], round(y), right, BOTTOM if index == len(cards) - 1 else round(y + height))
            {"word": _word, "quote": _quote, "facts": _facts}[name](canvas, box, data)
            y += height + GAP
    if has_comic:
        left = COMIC_COLUMN[0] if cards else LEFT_COLUMN[0]
        _comic(canvas, (left, BODY_TOP, COMIC_COLUMN[1], BOTTOM), comic, comic_picture)
    if not cards and not has_comic:
        canvas.text((400, 260), "今天的晨读还没取到", 18, "muted", anchor="mm")
    result = canvas.image
    if tuple(dimensions) != (800, 480):
        result = result.resize(tuple(dimensions), Image.Resampling.LANCZOS)
    return result
