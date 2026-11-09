"""800 x 480 时光书签 page.

Header: the title, today's date and the clock. Left: the literature clocks (a classical Chinese
sentence, then an English line, the words that name the time in the card's colour) above the
day, week, month and year bars. Right: the moon (its disc as moon_phase draws it, the phase, the
next principal phases and the day's moon and sun rise and set) above the days until the next
holidays. One colour per card, drawn as a band along its top; a card with nothing to show leaves
its room to the card beneath it.
"""

from __future__ import annotations

from PIL import Image

from plugins.moon_phase.moon_phase import render_moon_disc
from utils.page_canvas import PageCanvas
from utils.rich_text import draw_rich, fitted_lines, rich_lines

from . import sources

# The moon's dark side is a mid grey by day (a black disc would read as a hole in the white page)
# and a faint one by night, so a new moon still shows its round.
DAY = {"paper": (255, 255, 255), "ink": (18, 26, 32), "muted": (70, 80, 88), "rule": (176, 176, 176),
       "track": (226, 229, 233), "moon_light": 236, "moon_shadow": 104, "soft_rule": (120, 120, 116)}
NIGHT = {"paper": (0, 0, 0), "ink": (237, 241, 234), "muted": (190, 198, 204), "rule": (88, 88, 88),
         "track": (44, 48, 54), "moon_light": 238, "moon_shadow": 44, "soft_rule": (128, 130, 136)}
# One colour per card (day, night).
KINDS = {
    "报时": ((67, 56, 202), (165, 150, 255)),
    "月相": ((217, 119, 6), (255, 178, 64)),
    "进度": ((5, 150, 105), (52, 211, 153)),
    "倒数": ((200, 30, 50), (255, 96, 112)),
}
WEEKDAYS = sources.WEEKDAYS
HEADER_RULE = 56
BODY_TOP = 64
LEFT = (10, 466)
RIGHT = (474, 790)
CLOCK_BOTTOM = 322
MOON_BOTTOM = 300
BAND = 5
RADIUS = 6
MOON_SIZE = 112
BAR_HEIGHT = 10


def _palette(settings):
    theme = (settings or {}).get("_inkypi_theme") or {}
    night = theme.get("mode", (settings or {}).get("themeMode")) == "night"
    palette = dict(NIGHT if night else DAY)
    for kind, (day, dark) in KINDS.items():
        palette[kind] = dark if night else day
    return palette, night


def _card(canvas, box, kind, title, note=""):
    """A card's frame, its colour as a band along its top and its title; returns the body's top."""
    left, top, right, bottom = box
    canvas.draw.rounded_rectangle(box, radius=RADIUS, outline=canvas.color("rule"), width=1)
    canvas.draw.rounded_rectangle((left, top, right, top + BAND), radius=RADIUS, fill=canvas.color(kind),
                                  corners=(True, True, False, False))
    width = canvas.text((left + 10, top + BAND + 13), title, 14, kind, anchor="lm")
    if note:
        canvas.text((left + 18 + width, top + BAND + 13), note, 11, "muted", anchor="lm", bold=False,
                    width=right - left - 30 - width)
    return top + BAND + 26


def _source(book, author, chinese):
    if chinese:
        return f"——{author}《{book}》" if book else (f"——{author}" if author else "")
    return f"— {book}, {author}" if book and author else (f"— {book or author}" if book or author else "")


# --- cards -----------------------------------------------------------------------------------------

ZH_SIZES = (27, 25, 23, 21, 19, 17, 16, 15)
EN_SIZES = (19, 18, 17, 16, 15, 14, 13, 12)
CREDIT_HEIGHT = 24
DIVIDER = 14


def _block(canvas, line, sizes, width, max_lines, line_ratio):
    """[(size, lines, block height)] for one clock line at each size that fits ``max_lines``."""
    blocks = []
    for size in sizes:
        lines = rich_lines(canvas, line["quote"], size, width, max_lines)
        if lines is not None:
            credit = CREDIT_HEIGHT if line.get("book") or line.get("author") else 0
            blocks.append((size, lines, round(size * line_ratio) * len(lines) + credit))
    return blocks


def _cut(canvas, line, size, width, max_lines, line_ratio):
    """A line too long for every size: the smallest, cut with an ellipsis after ``max_lines``."""
    size, lines = fitted_lines(canvas, line["quote"], (size,), width, max_lines)
    return size, lines, round(size * line_ratio) * len(lines) + CREDIT_HEIGHT


def clock_layout(canvas, zh, en, width, room):
    """(zh block, en block) as large as fits ``room`` together, the Chinese line a size or more
    above the English; a block is (size, lines, height) or None."""
    zh_blocks = (_block(canvas, zh, ZH_SIZES, width, 7, 1.45) or [_cut(canvas, zh, ZH_SIZES[-1], width, 5, 1.45)]
                 if zh else [])
    # A cut English line could lose the very words that name the time: beside the Chinese one it
    # shows whole or not at all.
    en_blocks = _block(canvas, en, EN_SIZES, width, 7, 1.4) if en else []
    if en and not en_blocks and not zh:
        en_blocks = [_cut(canvas, en, EN_SIZES[-1], width, 5, 1.4)]
    if not zh_blocks:
        return None, next((block for block in en_blocks if block[2] <= room), en_blocks[-1] if en_blocks else None)
    if not en_blocks:
        return next((block for block in zh_blocks if block[2] <= room), zh_blocks[-1]), None
    for zh_block in zh_blocks:
        for en_block in en_blocks:
            if en_block[0] < zh_block[0] - 2 and zh_block[2] + DIVIDER + en_block[2] <= room:
                return zh_block, en_block
    # Too long for both: the Chinese line keeps its place, the English one is left out.
    return next((block for block in zh_blocks if block[2] <= room), zh_blocks[-1]), None


def _clock_text(canvas, left, right, y, line, block, chinese):
    size, lines, _height = block
    ratio = 1.45 if chinese else 1.4
    span = sources.highlight_span(line["quote"], line["time"])
    y = draw_rich(canvas, left + 12, y, lines, size, round(size * ratio), span, "报时")
    credit = _source(line["book"], line["author"], chinese)
    if credit:
        canvas.text((right - 12, y + 4), credit, 12 if chinese else 11, "muted", anchor="ra", bold=False,
                    width=right - left - 24)
        y += CREDIT_HEIGHT
    return y


def _clocks(canvas, box, clocks):
    left, top, right, bottom = box
    y = _card(canvas, box, "报时", "文学报时 · 书里的此刻")
    zh, en = clocks.get("zh"), clocks.get("en")
    if not zh and not en:
        canvas.text(((left + right) / 2, (y + bottom) / 2), "此刻书里没有这一分钟", 14, "muted", anchor="mm")
        return
    room = bottom - y - 8
    zh_block, en_block = clock_layout(canvas, zh, en, right - left - 24, room)
    used = sum(block[2] for block in (zh_block, en_block) if block) + (DIVIDER if zh_block and en_block else 0)
    # What the lines leave over goes above and between them, so they stand in the card's middle.
    spare = max(0, room - used)
    y += spare // 3
    if zh_block:
        y = _clock_text(canvas, left, right, y, zh, zh_block, True)
    if zh_block and en_block:
        y += spare // 3
        canvas.rule((left + 12, y + 2, right - 12, y + 2), "track", 1)
        y += DIVIDER
    if en_block:
        _clock_text(canvas, left, right, y, en, en_block, False)


def _progress(canvas, box, rows):
    left, top, right, bottom = box
    y = _card(canvas, box, "进度", "时光进度")
    step = (bottom - y - 4) / max(1, len(rows))
    bar_left, bar_right = left + 52, right - 150
    for index, (label, share, note) in enumerate(rows):
        middle = y + step * index + step / 2
        canvas.text((left + 12, middle), label, 13, anchor="lm")
        canvas.draw.rounded_rectangle((bar_left, middle - BAR_HEIGHT / 2, bar_right, middle + BAR_HEIGHT / 2),
                                      radius=BAR_HEIGHT // 2, fill=canvas.color("track"))
        share = max(0.0, min(1.0, float(share)))
        if share > 0:
            filled = bar_left + max(BAR_HEIGHT, (bar_right - bar_left) * share)
            canvas.draw.rounded_rectangle((bar_left, middle - BAR_HEIGHT / 2, filled, middle + BAR_HEIGHT / 2),
                                          radius=BAR_HEIGHT // 2, fill=canvas.color("进度"))
        canvas.text((bar_right + 8, middle), f"{share * 100:.0f}%", 13, anchor="lm")
        canvas.text((right - 10, middle), note, 11, "muted", anchor="rm", bold=False, width=96)


def _moon(canvas, box, moon, sky):
    left, top, right, bottom = box
    y = _card(canvas, box, "月相", "月相与日月出没")
    disc = render_moon_disc(MOON_SIZE, moon["fraction"], canvas.palette)
    canvas.image.paste(disc, (left + 10, y + 2), disc)
    x = left + MOON_SIZE + 22
    canvas.text((x, y + 4), moon["name"], 24, anchor="la")
    canvas.text((x, y + 38), f"照亮 {moon['illumination'] * 100:.0f}%  ·  月龄 {moon['age']:.1f} 天", 12,
                "muted", anchor="la", bold=False, width=right - x - 8)
    row_y = y + 60
    for moment, name in moon["next"][:3]:
        canvas.text((x, row_y), name, 12, "月相", anchor="la")
        canvas.text((x + 34, row_y), f"{moment:%m.%d} 周{WEEKDAYS[moment.weekday()]} {moment:%H:%M}", 12,
                    anchor="la", bold=False, width=right - x - 42)
        row_y += 18
    data = (sky or {}).get("data") or {}
    rows = [("月出", data.get("moonrise")), ("月落", data.get("moonset")),
            ("日出", data.get("sunrise")), ("日落", data.get("sunset"))]
    shown = [(label, value) for label, value in rows if value]
    if not shown:
        return
    base = max(row_y + 8, y + MOON_SIZE + 10)
    canvas.rule((left + 10, base, right - 10, base), "track", 1)
    column = (right - left - 20) / 4
    for index, (label, value) in enumerate(rows):
        cx = left + 10 + column * index + column / 2
        canvas.text((cx, base + 14), label, 11, "muted", anchor="mm", bold=False)
        canvas.text((cx, base + 33), value or "—", 16, anchor="mm")


def _countdowns(canvas, box, rows):
    left, top, right, bottom = box
    y = _card(canvas, box, "倒数", "倒数日")
    if not rows:
        canvas.text(((left + right) / 2, (y + bottom) / 2), "近期没有节日", 13, "muted", anchor="mm")
        return
    step = (bottom - y - 4) / len(rows)
    for index, (day, name, days, own) in enumerate(rows):
        middle = y + step * index + step / 2
        canvas.text((left + 12, middle - 7), name, 15, "倒数" if own else "ink", anchor="lm",
                    width=right - left - 120)
        canvas.text((left + 12, middle + 11), f"{day:%Y.%m.%d} 周{WEEKDAYS[day.weekday()]}", 11, "muted",
                    anchor="lm", bold=False)
        if days == 0:
            canvas.text((right - 12, middle), "就是今天", 16, "倒数", anchor="rm")
            continue
        unit = "天后" if days > 0 else "天前"
        unit_w = canvas.text((right - 12, middle + 4), unit, 11, "muted", anchor="rs", bold=False)
        canvas.text((right - 16 - unit_w, middle + 6), str(abs(days)), 24, "倒数", anchor="rs")


def _header(canvas, now):
    width = canvas.text((14, 28), "时光书签", 30, anchor="lm")
    canvas.text((26 + width, 34), "此刻 · 月相 · 进度 · 倒数", 12, "muted", anchor="lm", bold=False)
    clock_w = canvas.text((790, 28), f"{now:%H:%M}", 32, anchor="rm")
    canvas.text((780 - clock_w, 30), f"{now.year}年{now.month}月{now.day}日 周{WEEKDAYS[now.weekday()]}", 15,
                "muted", anchor="rm")
    canvas.rule((10, HEADER_RULE, 790, HEADER_RULE), "ink", 2)


def render_page(dimensions, payload, settings, now):
    palette, _night = _palette(settings)
    canvas = PageCanvas(palette, family=(settings or {}).get("fontFamily") or "Microsoft YaHei")
    _header(canvas, now)
    _clocks(canvas, (LEFT[0], BODY_TOP, LEFT[1], CLOCK_BOTTOM), payload.get("clocks") or {})
    _progress(canvas, (LEFT[0], CLOCK_BOTTOM + 8, LEFT[1], 474), payload.get("progress") or [])
    _moon(canvas, (RIGHT[0], BODY_TOP, RIGHT[1], MOON_BOTTOM), payload["moon"], payload.get("sky"))
    _countdowns(canvas, (RIGHT[0], MOON_BOTTOM + 8, RIGHT[1], 474), payload.get("countdowns") or [])
    result = canvas.image
    if tuple(dimensions) != (800, 480):
        result = result.resize(tuple(dimensions), Image.Resampling.LANCZOS)
    return result
