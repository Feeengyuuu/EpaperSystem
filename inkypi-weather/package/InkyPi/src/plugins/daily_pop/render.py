"""800 x 480 Daily Pop page: Bilibili trending on the left, today's anime and Wikipedia's most
read in the middle, today's Earth, Apple's most played and the chess puzzle on the right.

Each card wears its source's colour as a solid bar along its top (titles stay in ink); the
charts mark each entry's move since the previous refresh, the globe follows the time of day.
"""

from __future__ import annotations

import math
from datetime import datetime
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageOps

from utils.doodle_backdrop import paint_doodles
from utils.local_wordmark import paste_local_wordmark
from utils.page_canvas import PageCanvas

from . import sources
from .daily_pop import chart_moves


ASSETS = Path(__file__).resolve().parent / "assets"
DAY = {"paper": "#ffffff", "ink": "#121a20", "muted": "#3a454d", "rule": "#a6a6a6", "up": "#0a8f3c",
       "down": "#d42020", "new": "#d42020", "badge_ink": "#ffffff", "shade": "#121a20"}
NIGHT = {"paper": "#000000", "ink": "#edf1ea", "muted": "#c6d0d5", "rule": "#5a5a5a", "up": "#6fd38f",
         "down": "#ff6b6b", "new": "#ff6b6b", "badge_ink": "#000000", "shade": "#000000"}
# Each source's own colour, as a solid bar on its card.
# Bilibili is its official blue (the wordmark's), as on its logo.
BRANDS = {"bilibili": (0x00, 0xA1, 0xD6), "bangumi": (0xF0, 0x91, 0x99), "wiki": (0x33, 0x66, 0xCC),
          "epic": (0x0B, 0x3D, 0x91), "songs": (0xFA, 0x24, 0x3C), "puzzle": (0x81, 0xB6, 0x4C)}
CARDS = {"bilibili": (12, 58, 276, 468), "bangumi": (284, 58, 520, 312), "wiki": (284, 318, 520, 468),
         "epic": (528, 58, 788, 262), "songs": (528, 268, 788, 372), "puzzle": (528, 378, 788, 468)}
TITLES = {"bilibili": "B站热门", "bangumi": "今日新番", "wiki": "维基热读", "epic": "今日地球",
          "songs": "Apple 热歌", "puzzle": "每日棋题"}
# Each card's official logo (file in assets/logos, height) and the title that follows it.
LOGOS = {"bilibili": (("bilibili_tv", "bilibili"), 19, "热门"), "bangumi": ("bangumi", 18, "今日新番"),
         "wiki": ("wikipedia", 20, "维基热读"), "epic": ("nasa", 20, "今日地球"),
         "songs": ("apple_music", 15, "热歌"), "puzzle": ("chesscom", 17, "每日棋题")}
# Cards whose title leads their row and whose logo stands at its right end, naming the source.
LOGO_RIGHT = {"epic", "songs", "puzzle"}
# Cards whose source colour runs all the way round them, so the area stands out.
RINGED = {"bilibili"}
# Quiet doodles in the source's colour under a card's content.
DOODLES = {"bilibili": "video", "bangumi": "anime", "wiki": "books", "epic": "space", "songs": "music",
           "puzzle": "chess"}
SHORT_NAMES = {"bilibili": "B站", "bangumi": "新番", "wiki": "维基", "epic": "地球", "songs": "热歌",
               "puzzle": "棋题"}
WEEKDAYS = "一二三四五六日"
BAR = 6
# The EPIC globe: pixels darker than this are sky; the mask sits this share inside the limb.
EARTH_SKY_LEVEL = 24
EARTH_LIMB_INSET = 0.012
EARTH_SIZE = 136
# 维基热读: the Wikipedia globe (assets/logos/wiki_globe.png) shaded as a sphere, its centre
# WIKI_GLOBE_X into the card so its left side sinks behind the frame; the titles sit WIKI_ROW
# apart on an orbit WIKI_ORBIT_GAP outside it, the first ones larger.
WIKI_GLOBE_RADIUS = 47
WIKI_GLOBE_X = 18
WIKI_GLOW_PAD = 6
WIKI_ORBIT_GAP = 14
WIKI_ROW = 24
WIKI_BAR = 4
WIKI_TITLE_MIN = 10
WIKI_LIGHT = (0.32, 0.28)
WIKI_SHADE = 0.5
WIKI_NODE_RADII = (8, 7, 7, 6, 6)
WIKI_TITLE_SIZES = (14, 13, 13, 12, 12)
# Bilibili: the lead video's cover runs down behind its title, fading into the page from
# BILI_LEAD_FADE of its height; each row's thumbnail fills the row's height and fades into the
# page across its right side from BILI_THUMB_FADE of its width, the title starting over the fade.
BILI_LEAD_HEIGHT = 176
BILI_LEAD_FADE = 0.56
BILI_THUMB_FADE = 0.58
BILI_THUMB_TITLE = 0.8
BILI_TWO_LINE_SIZE = 14
# The translucent chips over the covers: how much of their fill covers the picture.
CHIP_ALPHA, PILL_ALPHA = 0.8, 0.58


def _rgb(value):
    if isinstance(value, str) and value.startswith("#") and len(value) == 7:
        return tuple(int(value[index:index + 2], 16) for index in (1, 3, 5))
    return value


def _palette(settings):
    theme = (settings or {}).get("_inkypi_theme") or {}
    palette = NIGHT if theme.get("mode", (settings or {}).get("themeMode")) == "night" else DAY
    return {key: _rgb(value) for key, value in palette.items()}


def count_text(value):
    """3576212 -> "357.6万", 123456789 -> "1.2亿"."""
    value = int(value or 0)
    if value >= 100_000_000:
        return f"{value / 100_000_000:.1f}亿"
    if value >= 10_000:
        return f"{value / 10_000:.1f}万"
    return str(value)


@lru_cache(maxsize=16)
def _logo(file_name, height, night):
    """An official logo (assets/logos), its dark-page version by night when there is one; a
    tuple of names sets its parts side by side (Bilibili's TV icon, then its wordmark)."""
    if isinstance(file_name, tuple):
        parts = [part for part in (_logo(name, height, night) for name in file_name) if part is not None]
        if not parts:
            return None
        gap = max(2, height // 4)
        art = Image.new("RGBA", (sum(part.width for part in parts) + gap * (len(parts) - 1), height), (0, 0, 0, 0))
        x = 0
        for part in parts:
            art.paste(part, (x, 0), part)
            x += part.width + gap
        return art
    for name in ((f"{file_name}_night", file_name) if night else (file_name,)):
        path = ASSETS / "logos" / f"{name}.png"
        if path.is_file():
            with Image.open(path) as source:
                art = source.convert("RGBA")
            return art.resize((max(1, round(art.width * height / art.height)), height), Image.Resampling.LANCZOS)
    return None


def _card(canvas, name, note="", *, title=None, logo=True):
    """The card's frame, its source colour along the top and its title row (led by the source's
    official logo); returns the content top."""
    left, top, right, bottom = CARDS[name]
    ringed = name in RINGED
    canvas.draw.rounded_rectangle((left, top, right, bottom), radius=6, width=2 if ringed else 1,
                                  outline=BRANDS[name] if ringed else canvas.color("rule"))
    canvas.draw.rounded_rectangle((left, top, right, top + BAR), radius=6, fill=BRANDS[name],
                                  corners=(True, True, False, False))
    middle = top + BAR + 12
    x = left + 10
    file_name, height, logo_title = LOGOS.get(name, (None, 0, None))
    art = _logo(file_name, height, sum(canvas.color("paper")) < 200) if logo and file_name else None
    end = right - 10
    if art is not None and name in LOGO_RIGHT:
        # The title leads the row; the logo closes it where the source's name was written.
        canvas.image.paste(art, (end - art.width, round(middle - art.height / 2)), art)
        end -= art.width + 6
    elif art is not None:
        canvas.image.paste(art, (x, round(middle - art.height / 2)), art)
        x += art.width + 6
    used = canvas.text((x, middle), title or (logo_title if art is not None else TITLES[name]), 14, anchor="lm")
    if note:
        width = end - x - used - 10 if name in LOGO_RIGHT else right - left - 110
        canvas.text((end, middle), note, 11, "muted", anchor="rm", bold=False, width=width)
    return top + BAR + 24


def _doodles(canvas, name, top):
    """Lay the card's doodles in its source colour under the content it is about to draw."""
    left, _top, right, bottom = CARDS[name]
    paint_doodles(canvas.image, (left + 2, top - 4, right - 2, bottom - 2), ASSETS / "doodles" / f"{DOODLES[name]}.png",
                  BRANDS[name], radius=4, offset=(left, top))


def _paste_fit(canvas, picture, box, *, centering=(0.5, 0.5), radius=5):
    if picture is None:
        canvas.draw.rounded_rectangle(box, radius=radius, fill=canvas.color("rule"))
        return
    x0, y0, x1, y1 = [int(value) for value in box]
    size = (max(1, x1 - x0), max(1, y1 - y0))
    tile = ImageOps.fit(picture, size, method=Image.Resampling.LANCZOS, centering=centering)
    mask = Image.new("L", size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, size[0] - 1, size[1] - 1), radius=radius, fill=255)
    canvas.image.paste(tile, (x0, y0), mask)


def _paste_fade(canvas, picture, box, *, fade_from, across=False, radius=5, centering=(0.5, 0.4)):
    """``picture`` fitted to ``box``, its near corners rounded, fading into the page from
    ``fade_from`` of its height (of its width when ``across``) to its far edge."""
    x0, y0, x1, y1 = [int(value) for value in box]
    size = (max(1, x1 - x0), max(1, y1 - y0))
    if picture is None:
        picture = Image.new("RGB", size, canvas.color("rule"))
    tile = ImageOps.fit(picture.convert("RGB"), size, method=Image.Resampling.LANCZOS, centering=centering)
    mask = Image.new("L", size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, size[0] - 1 + radius * across, size[1] - 1 + radius * (not across)),
                                           radius=radius, fill=255)
    length = size[0] if across else size[1]
    start = round(length * fade_from)
    ramp = Image.new("L", (length, 1), 255)
    for index in range(start, length):
        t = (index - start) / max(1, length - 1 - start)
        ramp.putpixel((index, 0), round(255 * (1 - t * t * (3 - 2 * t))))
    ramp = ramp.resize(size) if across else ramp.rotate(-90, expand=True).resize(size)
    canvas.image.paste(tile, (x0, y0), ImageChops.multiply(mask, ramp))


def _glass(canvas, box, fill, alpha, radius):
    """A translucent rounded chip, borderless: ``fill`` over whatever lies beneath at ``alpha``."""
    x0, y0, x1, y1 = [round(value) for value in box]
    mask = Image.new("L", (x1 - x0 + 1, y1 - y0 + 1), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, x1 - x0, y1 - y0), radius=radius, fill=round(255 * alpha))
    canvas.image.paste(fill, (x0, y0, x1 + 1, y1 + 1), mask)


def _move_badge(canvas, xy, move, *, anchor_right=False):
    """NEW in a red pill, or a small triangle and the places gained or lost; returns its width."""
    x, y = xy
    if move == "new":
        width = canvas.width("NEW", 9) + 10
        left = x - width if anchor_right else x
        canvas.draw.rounded_rectangle((left, y - 7, left + width, y + 7), radius=7, fill=canvas.color("new"))
        canvas.text((left + width / 2, y), "NEW", 9, "badge_ink", anchor="mm")
        return width
    if not isinstance(move, int) or move == 0:
        return 0
    tone = "up" if move > 0 else "down"
    label = str(abs(move))
    width = 9 + canvas.width(label, 10)
    left = x - width if anchor_right else x
    points = ((left, y + 3), (left + 7, y + 3), (left + 3.5, y - 4)) if move > 0 else \
        ((left, y - 3), (left + 7, y - 3), (left + 3.5, y + 4))
    canvas.draw.polygon(points, fill=canvas.color(tone))
    canvas.text((left + 9, y), label, 10, tone, anchor="lm")
    return width


def _rank_chip(canvas, xy, rank, fill):
    """The rank as a translucent chip of the source's colour over the cover."""
    x, y = xy
    _glass(canvas, (x, y, x + 20, y + 18), fill, CHIP_ALPHA, 6)
    canvas.text((x + 10, y + 9), str(rank), 11, (255, 255, 255), anchor="mm")


# --- Bilibili ------------------------------------------------------------------------------------

def _bilibili(canvas, payload, images):
    data = payload["bilibili"]["data"]
    videos = data.get("videos") or []
    history = (payload.get("history") or {}).get("bilibili") or {}
    moves = chart_moves(videos, history.get("previous"), "title") if history.get("previous") else {}
    previous_views = {row.get("title"): row.get("views") for row in history.get("previous") or []}
    top = _card(canvas, "bilibili", "热门推荐 TOP 6")
    _doodles(canvas, "bilibili", top)
    left, _top, right, bottom = CARDS["bilibili"]
    if not videos:
        canvas.text(((left + right) / 2, (top + bottom) / 2), "B站数据暂不可用", 14, "muted", anchor="mm")
        return
    lead = videos[0]
    # The cover runs on down behind the title, fading into the page under it.
    cover_box = (left + 6, top + 2, right - 6, top + 2 + BILI_LEAD_HEIGHT)
    _paste_fade(canvas, images.get(sources.safe_image_url(lead.get("cover"))), cover_box, fade_from=BILI_LEAD_FADE)
    _rank_chip(canvas, (cover_box[0] + 6, cover_box[1] + 6), 1, BRANDS["bilibili"])
    views = f"{count_text(lead.get('views'))}播放"
    gain = _view_gain(lead, previous_views)
    pill = views + (f" · +{gain}" if gain else "")
    pill_w = canvas.width(pill, 10) + 14
    pill_y = cover_box[1] + round(BILI_LEAD_HEIGHT * BILI_LEAD_FADE) - 26
    _glass(canvas, (cover_box[0] + 6, pill_y, cover_box[0] + 6 + pill_w, pill_y + 18), canvas.color("shade"), PILL_ALPHA, 9)
    canvas.text((cover_box[0] + 6 + pill_w / 2, pill_y + 9), pill, 10, (255, 255, 255), anchor="mm")
    _move_badge(canvas, (cover_box[2] - 6, cover_box[1] + 15), moves.get(lead.get("title")), anchor_right=True)
    size, lines = _fitted_title(canvas, lead.get("title"), right - left - 20, 44, largest=18, smallest=13)
    y = cover_box[3] - 4 - len(lines) * (size + 5) + 5
    for line in lines:
        canvas.text((left + 10, y), line, size, stroke="paper")
        y += size + 5
    meta = " · ".join(part for part in (lead.get("owner"), lead.get("section"), lead.get("reason")) if part)
    canvas.text((left + 10, cover_box[3] + 3), meta, 10, "muted", bold=False, width=right - left - 20)
    row_top = cover_box[3] + 23
    row_h = (bottom - 6 - row_top) / 3
    for index, video in enumerate(videos[1:4], start=2):
        y0 = row_top + (index - 2) * row_h
        canvas.rule((left + 8, y0 - 3, right - 8, y0 - 3))
        # The thumbnail fills the row and fades into the page across its right side; the title
        # starts over the fade, the views line at the row's foot, all clear of the next rule.
        thumb_h = int(row_h - 6)
        thumb = (left + 8, int(y0 + 1), left + 8 + round(thumb_h * 16 / 9), int(y0 + 1) + thumb_h)
        _paste_fade(canvas, images.get(sources.safe_image_url(video.get("cover"))), thumb, fade_from=BILI_THUMB_FADE,
                    across=True, radius=4)
        _rank_chip(canvas, (thumb[0] + 3, thumb[1] + 3), index, BRANDS["bilibili"])
        text_x = thumb[0] + round((thumb[2] - thumb[0]) * BILI_THUMB_TITLE)
        meta_y = y0 + row_h - 19
        # The glyphs of a title's last line reach a fifth of its size below its pitch.
        title_h = meta_y - 7 - y0
        # A short title is set larger and centred over its views line; a long one wraps smaller.
        size, lines = _fitted_title(canvas, video.get("title"), right - text_x - 10, title_h, largest=16, smallest=11)
        if len(lines) > 1 and size > BILI_TWO_LINE_SIZE:
            # Two-line titles share one size, so the rows keep one rhythm.
            size, lines = _fitted_title(canvas, video.get("title"), right - text_x - 10, title_h,
                                        largest=BILI_TWO_LINE_SIZE, smallest=11)
        block = len(lines) * (size + 4) - 4
        for number, line in enumerate(lines):
            canvas.text((text_x, y0 + (title_h - block) / 2 + number * (size + 4)), line, size, stroke="paper")
        x = text_x
        x += canvas.text((x, meta_y), f"{count_text(video.get('views'))}播放", 10, "muted", bold=False, stroke="paper") + 6
        gain = _view_gain(video, previous_views)
        if gain:
            x += canvas.text((x, meta_y), f"+{gain}", 10, "up", stroke="paper") + 6
        _move_badge(canvas, (right - 10, meta_y + 6), moves.get(video.get("title")), anchor_right=True)


def _fitted_title(canvas, text, width, height, *, largest, smallest):
    """The largest size from ``largest`` down at which the whole title fits ``width`` in the lines
    ``height`` holds (a line is size + 4), and its lines; the smallest size's wrap when none does."""
    for size in range(largest, smallest - 1, -1):
        rows = max(1, (height + 4) // (size + 4))
        lines = canvas.wrap(text, size, width, rows + 1)
        if len(lines) <= rows:
            return size, lines
    return smallest, canvas.wrap(text, smallest, width, max(1, (height + 4) // (smallest + 4)))


def _view_gain(video, previous_views):
    before = previous_views.get(video.get("title"))
    if not isinstance(before, int):
        return ""
    gain = int(video.get("views") or 0) - before
    return count_text(gain) if gain > 0 else ""


# --- Bangumi -------------------------------------------------------------------------------------

def _bangumi(canvas, payload, images, now):
    data = payload["bangumi"]["data"]
    shows = data.get("shows") or []
    note = f"周{WEEKDAYS[now.weekday()]} · 共 {data['count']} 部" if data.get("count") else ""
    top = _card(canvas, "bangumi", note)
    _doodles(canvas, "bangumi", top)
    left, _top, right, bottom = CARDS["bangumi"]
    if not shows:
        canvas.text(((left + right) / 2, (top + bottom) / 2), "番组数据暂不可用", 14, "muted", anchor="mm")
        return
    cell_w = (right - left - 16) / 3
    cell_h = (bottom - 6 - top) / 2
    for index, show in enumerate(shows[:6]):
        column, row = index % 3, index // 3
        x0 = left + 8 + column * cell_w
        y0 = top + row * cell_h
        cover = (int(x0 + 3), int(y0 + 2), int(x0 + cell_w - 3), int(y0 + cell_h - 30))
        _paste_fit(canvas, images.get(sources.safe_image_url(show.get("cover"))), cover, centering=(0.5, 0.2))
        if show.get("score"):
            label = f"{show['score']:.1f}"
            width = canvas.width(label, 10) + 10
            canvas.draw.rounded_rectangle((cover[2] - width - 3, cover[1] + 3, cover[2] - 3, cover[1] + 18), radius=7,
                                          fill=BRANDS["bangumi"])
            canvas.text((cover[2] - 3 - width / 2, cover[1] + 10.5), label, 10, (255, 255, 255), anchor="mm")
        canvas.text((x0 + 3, cover[3] + 4), show.get("name"), 11, width=cell_w - 6)
        canvas.text((x0 + 3, cover[3] + 18), f"{count_text(show.get('watching'))}在追", 9, "muted", bold=False,
                    width=cell_w - 6)


# --- Wikipedia most read -------------------------------------------------------------------------

@lru_cache(maxsize=4)
def wiki_globe(diameter, night):
    """Wikipedia's puzzle globe shaded as a lit sphere: light from the upper left, the far side
    in shade, a soft highlight and a thin blue glow round it. An RGBA square with WIKI_GLOW_PAD
    around the ``diameter`` disc; None without the file."""
    try:
        with Image.open(ASSETS / "logos" / "wiki_globe.png") as source:
            art = source.convert("RGBA")
    except OSError:
        return None
    art = art.resize((diameter, max(1, round(art.height * diameter / art.width))), Image.Resampling.LANCZOS)
    pad, size = WIKI_GLOW_PAD, diameter + 2 * WIKI_GLOW_PAD
    globe = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    # The logo's round foot sits on the circle; its missing pieces stay open at the top.
    globe.alpha_composite(art, (pad, pad + diameter - art.height))
    light = Image.radial_gradient("L").resize((diameter * 2, diameter * 2), Image.Resampling.BILINEAR)
    lit = (round(diameter * (1 - WIKI_LIGHT[0])), round(diameter * (1 - WIKI_LIGHT[1])))
    light = light.crop((lit[0], lit[1], lit[0] + diameter, lit[1] + diameter))
    shade = light.point(lambda value: 255 - round(value * WIKI_SHADE))
    rgb = Image.merge("RGB", globe.crop((pad, pad, pad + diameter, pad + diameter)).split()[:3])
    rgb = ImageChops.multiply(rgb, Image.merge("RGB", (shade, shade, shade)))
    glint = Image.new("L", (diameter, diameter), 0)
    spot = diameter * WIKI_LIGHT[0], diameter * WIKI_LIGHT[1]
    ImageDraw.Draw(glint).ellipse((spot[0] - diameter * 0.16, spot[1] - diameter * 0.12,
                                   spot[0] + diameter * 0.16, spot[1] + diameter * 0.12), fill=150)
    glint = glint.filter(ImageFilter.GaussianBlur(diameter * 0.07))
    rgb = Image.composite(Image.new("RGB", rgb.size, (255, 255, 255)), rgb, glint)
    disc = globe.getchannel("A").crop((pad, pad, pad + diameter, pad + diameter))
    glow = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    ImageDraw.Draw(glow).ellipse((pad - 1, pad - 1, pad + diameter, pad + diameter), outline=(*BRANDS["wiki"], 255),
                                 width=3 if night else 2)
    glow = glow.filter(ImageFilter.GaussianBlur(2.2))
    sphere = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    sphere.alpha_composite(glow)
    body = rgb.convert("RGBA")
    body.putalpha(disc)
    sphere.alpha_composite(body, (pad, pad))
    return sphere


def _wiki(canvas, payload):
    """The most read set round the Wikipedia globe: a lit sphere sunk into the card's left
    edge, a thin orbit round it and the five titles in order along the orbit, the first one
    largest; each one's views under it."""
    articles = (payload["wiki"]["data"].get("articles") or [])[:5]
    top = _card(canvas, "wiki", "中文维基 · 昨日阅读量")
    _doodles(canvas, "wiki", top)
    left, _top, right, bottom = CARDS["wiki"]
    if not articles:
        canvas.text(((left + right) / 2, (top + bottom) / 2), "维基数据暂不可用", 14, "muted", anchor="mm")
        return
    night = sum(canvas.color("paper")) < 200
    centre = (left + WIKI_GLOBE_X, (top + bottom - 6) / 2)
    sphere = wiki_globe(WIKI_GLOBE_RADIUS * 2, night)
    if sphere is not None:
        x = round(centre[0] - sphere.width / 2)
        y = round(centre[1] - sphere.height / 2)
        # Only the part inside the card's frame shows: the globe rises from its edge.
        cut = max(0, left + 1 - x)
        visible = sphere.crop((cut, 0, sphere.width, sphere.height))
        canvas.image.paste(visible, (x + cut, y), visible)
    orbit = WIKI_GLOBE_RADIUS + WIKI_ORBIT_GAP
    # The rows keep an even step down the card; each sits where the orbit crosses its height.
    rows = [centre[1] + (index - (len(articles) - 1) / 2) * WIKI_ROW for index in range(len(articles))]
    span = math.degrees(math.asin(min(1.0, max(abs(y - centre[1]) for y in rows) / orbit)))
    canvas.draw.arc((centre[0] - orbit, centre[1] - orbit, centre[0] + orbit, centre[1] + orbit),
                    -span - 8, span + 8, fill=BRANDS["wiki"], width=1)
    outer = orbit + 7
    canvas.draw.arc((centre[0] - outer, centre[1] - outer, centre[0] + outer, centre[1] + outer),
                    -span - 4, span + 4, fill=canvas.color("rule"), width=1)
    most = max(article.get("views") or 0 for article in articles) or 1
    for index, (article, y) in enumerate(zip(articles, rows)):
        node = (centre[0] + math.sqrt(max(0.0, orbit ** 2 - (y - centre[1]) ** 2)), y)
        radius = WIKI_NODE_RADII[index]
        lead = index < 3
        canvas.draw.ellipse((node[0] - radius, node[1] - radius, node[0] + radius, node[1] + radius),
                            fill=BRANDS["wiki"] if lead else canvas.color("paper"),
                            outline=BRANDS["wiki"] if lead else canvas.color("rule"), width=1)
        canvas.text(node, str(index + 1), radius + 3, (255, 255, 255) if lead else "muted", anchor="mm")
        text_x = node[0] + radius + 5
        # The title has the whole row; a long one steps down in size before it would be cut.
        width = right - 8 - text_x
        size = next((size for size in range(WIKI_TITLE_SIZES[index], WIKI_TITLE_MIN - 1, -1)
                     if canvas.width(article.get("title") or "", size) <= width), WIKI_TITLE_MIN)
        canvas.text((text_x, y - 6), article.get("title"), size, anchor="lm", width=width)
        # Yesterday's reads in a small box at the end of the bar that draws them.
        views = count_text(article.get("views"))
        box_w = canvas.width(views, 9, bold=False) + 8
        box = (right - 8 - box_w, y, right - 8, y + 11)
        canvas.draw.rounded_rectangle(box, radius=3, outline=BRANDS["wiki"] if lead else canvas.color("rule"), width=1)
        canvas.text(((box[0] + box[2]) / 2, (box[1] + box[3]) / 2), views, 9, "ink" if lead else "muted",
                    anchor="mm", bold=False)
        # Yesterday's reads as a bar on a track: the most read fills it, the rest in proportion.
        track = (text_x, y + 4, box[0] - 4, y + 4 + WIKI_BAR)
        canvas.draw.rounded_rectangle(track, radius=WIKI_BAR / 2, fill=canvas.color("rule"))
        share = max(0.04, (article.get("views") or 0) / most)
        fill = (track[0], track[1], track[0] + (track[2] - track[0]) * share, track[3])
        canvas.draw.rounded_rectangle(fill, radius=WIKI_BAR / 2,
                                      fill=BRANDS["wiki"] if lead else canvas.color("muted"))


# --- Earth ---------------------------------------------------------------------------------------

def earth_disc(picture, size):
    """The globe alone, cut cleanly out of its black sky: an RGBA ``size`` square.

    The disc is found by brightness (the frame's margin is not always even), and the round
    mask sits a little inside its limb so no dark rim of space is left around it.
    """
    gray = picture.convert("L").point(lambda value: 255 if value > EARTH_SKY_LEVEL else 0)
    box = gray.getbbox() or (0, 0, picture.width, picture.height)
    disc = picture.crop(box).convert("RGB")
    side = max(disc.size)
    square = Image.new("RGB", (side, side), (0, 0, 0))
    square.paste(disc, ((side - disc.width) // 2, (side - disc.height) // 2))
    square = square.resize((size, size), Image.Resampling.LANCZOS)
    scale = 4
    mask = Image.new("L", (size * scale, size * scale), 0)
    inset = size * scale * EARTH_LIMB_INSET
    ImageDraw.Draw(mask).ellipse((inset, inset, size * scale - 1 - inset, size * scale - 1 - inset), fill=255)
    square.putalpha(mask.resize((size, size), Image.Resampling.LANCZOS))
    return square


def _epic(canvas, payload, images, now):
    data = payload["epic"]["data"]
    frames = data.get("frames") or []
    top = _card(canvas, "epic", "EPIC · DSCOVR")
    _doodles(canvas, "epic", top)
    left, _top, right, bottom = CARDS["epic"]
    # The frame the plugin found a picture for (the nearest cached one when now's is missing).
    index = payload.get("epic_frame_index")
    if index is None or not 0 <= index < len(frames):
        index = sources.epic_frame_index(data, now)
    frame = frames[index] if index is not None else None
    picture = images.get(sources.safe_image_url(payload.get("epic_frame_url")))
    size = min(EARTH_SIZE, bottom - top - 22)
    globe_box = (left + 10, top + 2, left + 10 + size, top + 2 + size)
    if picture is not None:
        disc = earth_disc(picture, size)
        canvas.image.paste(disc, globe_box[:2], disc)
    else:
        canvas.draw.ellipse(globe_box, outline=canvas.color("rule"), width=2)
    if frames:
        # The day's frames as dots under the globe, the shown one filled: it turns with the clock.
        step = (globe_box[2] - globe_box[0]) / max(1, len(frames))
        for number in range(len(frames)):
            cx = globe_box[0] + step * (number + 0.5)
            radius = 3.2 if number == index else 2
            fill = BRANDS["epic"] if number == index else canvas.color("rule")
            canvas.draw.ellipse((cx - radius, bottom - 11 - radius, cx + radius, bottom - 11 + radius), fill=fill)
    text_x = globe_box[2] + 12
    width = right - 10 - text_x
    if frame:
        taken = datetime.fromisoformat(frame["taken"]).astimezone(sources.PACIFIC)
        # EPIC publishes a day or two late: the newest set, the frame nearest this time of day.
        canvas.text((text_x, top + 10), f"{taken:%H:%M}", 18, width=width)
        canvas.text((text_x, top + 30), f"{taken:%m/%d} 太平洋时间", 10, "muted", bold=False, width=width)
        canvas.text((text_x, top + 54), sources.facing(frame["lat"], frame["lon"]), 13, width=width)
        canvas.text((text_x, top + 72), f"中心 {frame['lat']:.0f}°, {frame['lon']:.0f}°", 10, "muted", bold=False,
                    width=width)
        canvas.text((text_x, top + 96), f"第 {index + 1}/{len(frames)} 帧", 12, width=width)
        canvas.text((text_x, top + 113), "随时刻转动", 10, "muted", bold=False, width=width)
        canvas.text((text_x, top + 128), "L1 点 · 150 万公里", 10, "muted", bold=False, width=width)
    else:
        canvas.text((text_x, top + 40), "地球影像暂不可用", 12, "muted", width=width)


# --- Apple Music ---------------------------------------------------------------------------------

def _songs(canvas, payload, images):
    songs = payload["songs"]["data"].get("songs") or []
    history = (payload.get("history") or {}).get("songs") or {}
    moves = chart_moves(songs, history.get("previous"), "name") if history.get("previous") else {}
    chart = payload["songs"]["data"].get("chart")
    if chart == "itunes":
        top = _card(canvas, "songs", "美国 · 热销榜", title="iTunes 热歌", logo=False)
    else:
        top = _card(canvas, "songs", "美国 · 播放最多")
    _doodles(canvas, "songs", top)
    left, _top, right, bottom = CARDS["songs"]
    if not songs:
        canvas.text(((left + right) / 2, (top + bottom) / 2), "榜单暂不可用", 13, "muted", anchor="mm")
        return
    row_h = (bottom - 4 - top) / 3
    for index, song in enumerate(songs[:3]):
        y0 = top + index * row_h
        art = (left + 10, int(y0 + 2), left + 10 + int(row_h - 4), int(y0 + row_h - 2))
        _paste_fit(canvas, images.get(sources.safe_image_url(song.get("artwork"))), art, radius=3)
        x = art[2] + 8
        middle = y0 + row_h / 2
        # Name, then its artist greyed on the same line, so three songs fit the short card.
        name_w = canvas.text((x, middle), f"{index + 1}. {song.get('name')}", 12, anchor="lm", width=right - x - 96)
        canvas.text((x + name_w + 6, middle + 1), song.get("artist"), 10, "muted", anchor="lm", bold=False,
                    width=right - x - name_w - 6 - 40)
        _move_badge(canvas, (right - 10, middle), moves.get(song.get("name")), anchor_right=True)


# --- Chess ---------------------------------------------------------------------------------------

def _puzzle(canvas, payload, images):
    puzzle = payload["puzzle"]["data"]
    previous = ((payload.get("history") or {}).get("puzzle") or {}).get("previous") or {}
    top = _card(canvas, "puzzle")
    _doodles(canvas, "puzzle", top)
    left, _top, right, bottom = CARDS["puzzle"]
    if not puzzle.get("fen"):
        canvas.text(((left + right) / 2, (top + bottom) / 2), "棋题暂不可用", 13, "muted", anchor="mm")
        return
    side = bottom - top - 4
    board = (left + 10, top, left + 10 + side, top + side)
    _paste_fit(canvas, images.get(sources.safe_image_url(puzzle.get("image"))), board, radius=2)
    x = board[2] + 10
    width = right - 10 - x
    mover = "白先" if puzzle.get("to_move") == "w" else "黑先"
    goal = f"{puzzle['mate_in']} 步杀" if puzzle.get("mate_in") else "找出最佳着法"
    canvas.text((x, top + 2), f"{mover} · {goal}", 13, width=width)
    canvas.text((x, top + 20), puzzle.get("title"), 10, "muted", bold=False, width=width)
    # Yesterday's answer is revealed once today's puzzle replaces it, on one line inside the card.
    answer = previous.get("solution")
    label_w = canvas.text((x, top + 36), "昨日答案" if answer else "答案明日揭晓", 10, width=width)
    if answer:
        canvas.text((x + label_w + 6, top + 36), answer, 10, "muted", bold=False, width=width - label_w - 6)


def _source_key(canvas, left, right):
    """The header's key: each source's colour square and short name, as far as there is room."""
    x = left
    for name in ("bilibili", "bangumi", "wiki", "epic", "songs", "puzzle"):
        label = SHORT_NAMES[name]
        width = 14 + canvas.width(label, 11, bold=False)
        if x + width > right:
            break
        canvas.draw.rounded_rectangle((x, 21, x + 10, 31), radius=2, fill=BRANDS[name])
        canvas.text((x + 14, 26), label, 11, "muted", anchor="lm", bold=False)
        x += width + 10


def render_page(dimensions, payload, settings, now, images):
    canvas = PageCanvas(_palette(settings), family=(settings or {}).get("fontFamily") or "Microsoft YaHei")
    placed = paste_local_wordmark(canvas.image, ASSETS / "title_wordmark.png", (10, 2, 230, 50), align="left")
    title_right = placed[2] if placed else 14 + canvas.text((14, 26), "今日热榜", 28, anchor="lm")
    date_left = 788 - canvas.text((788, 26), f"{now:%m.%d} 周{WEEKDAYS[now.weekday()]} {now:%H:%M}", 16, anchor="rm")
    _source_key(canvas, title_right + 16, date_left - 14)
    canvas.rule((12, 52, 788, 52), "ink", 2)
    _bilibili(canvas, payload, images)
    _bangumi(canvas, payload, images, now)
    _wiki(canvas, payload)
    _epic(canvas, payload, images, now)
    _songs(canvas, payload, images)
    _puzzle(canvas, payload, images)
    result = canvas.image
    if tuple(dimensions) != (800, 480):
        result = result.resize(tuple(dimensions), Image.Resampling.LANCZOS)
    return result
