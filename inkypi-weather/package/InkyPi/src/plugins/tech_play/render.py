"""800 x 480 Tech & Play page, laid out as outlined cards with coloured accent bars.

Left: service status tiles and numbered arXiv papers. Right: Epic giveaways, upcoming
releases, and per-game speedrun world records with box art, trophies and runner flags.
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageOps

from utils.flat_icons import draw_icon
from utils.local_wordmark import paste_header_art, paste_local_wordmark
from utils.page_canvas import PageCanvas
from utils.safe_image import ImageLimits, safe_open_image

from .sources import epic_end_label, flag_url, local_now, parse_time, run_time


ASSETS = Path(__file__).resolve().parent / "assets"
_LIMITS = ImageLimits(max_bytes=256 * 1024, max_width=512, max_height=512, max_pixels=512 * 512)
# Pure white or black paper and neutral grey rules: an off-white or tinted ground, or a tinted
# grey, dithers into scattered yellow, blue or green dots on the panel. Accents are refined
# crimson, cobalt, emerald and gold rather than the panel's pure inks (owner's choice).
DAY = {
    "paper": "#ffffff", "panel": "#ebe8de", "ink": "#121a20", "muted": "#222c34", "rule": "#999999",
    "ok": "#059669", "minor": "#e8a90c", "major": "#c81e32", "maintenance": "#2554c7", "unknown": "#8a949b",
    "accent": "#2554c7", "gold": "#e8a90c", "badge_ink": "#ffffff", "band": "#121a20",
    "status_bar": "#059669", "papers_bar": "#c81e32", "epic_bar": "#121a20", "upcoming_bar": "#2554c7",
    "speed_bar": "#e8a90c",
}
NIGHT = {
    "paper": "#000000", "panel": "#1c2a35", "ink": "#edf1ea", "muted": "#c6d0d5", "rule": "#5a5a5a",
    "ok": "#34d399", "minor": "#fbbf24", "major": "#ff6b78", "maintenance": "#7aa2ff", "unknown": "#7d8b94",
    "accent": "#7aa2ff", "gold": "#fbbf24", "badge_ink": "#000000", "band": "#05090c",
    "status_bar": "#34d399", "papers_bar": "#ff6b78", "epic_bar": "#edf1ea", "upcoming_bar": "#7aa2ff",
    "speed_bar": "#fbbf24",
}
STATUS_TEXT = {"none": "正常", "minor": "轻微", "major": "故障", "critical": "严重", "maintenance": "维护",
               "unknown": "未知"}
STATUS_ICON = {"none": "ok", "minor": "alert", "major": "outage", "critical": "outage",
               "maintenance": "maintenance", "unknown": "info"}
WEEKDAYS = "一二三四五六日"
LEFT_CARD = (12, 58, 268, 244)
# The wire from a service's name to its state keeps this gap at both ends and needs this room (px).
LINK_GAP, LINK_MIN = 8, 34
PAPER_CARD = (12, 250, 268, 468)
EPIC_CARD = (280, 58, 788, 196)
UPCOMING_CARD = (280, 202, 788, 314)
SPEED_CARD = (280, 320, 788, 468)
HEADER = 28
CARD_BAND = 5
# Each card's generated icon, by its colour key.
SECTION_ART = {"status_bar": "section-status", "papers_bar": "section-papers", "epic_bar": "section-free",
               "upcoming_bar": "section-upcoming", "speed_bar": "section-speed"}


def _rgb(value):
    if isinstance(value, str) and value.startswith("#") and len(value) == 7:
        return tuple(int(value[index:index + 2], 16) for index in (1, 3, 5))
    return value


def _palette(settings):
    theme = settings.get("_inkypi_theme") or {}
    palette = NIGHT if theme.get("mode", settings.get("themeMode")) == "night" else DAY
    return {key: _rgb(value) for key, value in palette.items()}


def _logo(name, height, max_width=None):
    path = ASSETS / f"logo-{name}.png"
    if not path.is_file():
        return None
    with safe_open_image(path, limits=_LIMITS) as source:
        image = source.convert("RGBA")
    return ImageOps.contain(image, (max_width or height * 3, height), Image.Resampling.LANCZOS)


@lru_cache(maxsize=32)
def _asset_icon(name, height):
    """A generated icon from assets/ (``name`` without .png) ``height`` px tall, or None."""
    path = ASSETS / f"{name}.png"
    if not path.is_file():
        return None
    with safe_open_image(path, limits=_LIMITS) as source:
        image = source.convert("RGBA")
    return image.resize((max(1, round(image.width * height / image.height)), int(height)), Image.Resampling.LANCZOS)


def _paste(canvas, image, xy):
    if image is None:
        return 0
    if image.mode == "RGBA":
        canvas.image.paste(image, (round(xy[0]), round(xy[1])), image)
    else:
        canvas.image.paste(image, (round(xy[0]), round(xy[1])))
    return image.width


def days_until(stamp, now):
    """'今天', '明天' or 'N 天后' for a date ahead (by calendar day); "" once it has passed."""
    days = (stamp.astimezone(now.tzinfo).date() - now.date()).days
    return "" if days < 0 else "今天" if days == 0 else "明天" if days == 1 else f"{days} 天后"


def time_left(stamp, now):
    """'剩 N 天' (or hours on the last day) until a deadline; "" once it has passed."""
    seconds = (stamp - now).total_seconds()
    if seconds <= 0:
        return ""
    return f"剩 {int(seconds // 86400)} 天" if seconds >= 86400 else f"剩 {max(1, int(seconds // 3600))} 小时"


def _corner_chip(canvas, right, top, text, fill, color="badge_ink"):
    """A solid tag on a picture's top right corner, the counterpart of the 免费领 tag on its left."""
    width = canvas.width(text, 12) + 12
    canvas.draw.rectangle((right - width, top, right, top + 18), fill=canvas.color(fill))
    canvas.text((right - width / 2, top + 9), text, 12, color, anchor="mm")
    return width


def ago(stamp, now):
    seconds = max(0, (now - stamp).total_seconds())
    if seconds < 3600:
        return f"{max(1, int(seconds // 60))} 分钟前"
    if seconds < 86400:
        return f"{int(seconds // 3600)} 小时前"
    return f"{int(seconds // 86400)} 天前"


@lru_cache(maxsize=8)
def _section_art(name, height):
    """A card's generated section icon (assets/section-<card>.png)."""
    path = ASSETS / f"{name}.png"
    if not path.is_file():
        return None
    with safe_open_image(path, limits=_LIMITS) as source:
        image = source.convert("RGBA")
    return ImageOps.contain(image, (height * 2, height), Image.Resampling.LANCZOS)


def _card(canvas, box, bar, title, *, logo=None, icon=None, detail="", chip=None):
    """A card in its own colour: a solid band across the top, a frame and header rule in that
    colour, the source's official logo (or a generated icon on the band where there is none),
    the title and a detail."""
    left, top, right, bottom = box
    colour = canvas.color(bar)
    canvas.draw.rounded_rectangle(box, radius=7, outline=colour, width=2)
    canvas.draw.rounded_rectangle((left, top, right, top + CARD_BAND), radius=7, fill=colour,
                                  corners=(True, True, False, False))
    cursor = left + 8
    # A card with its source's official logo shows only that; the generated icon stands in for one.
    art = _section_art(SECTION_ART.get(bar, ""), 26) if logo is None else None
    if art is not None:
        cursor += _paste(canvas, art, (cursor, top + 2)) + 5
    elif icon:
        draw_icon(canvas.draw, icon, (cursor, top + 7), 16, canvas.color("ink"), canvas.color("paper"))
        cursor += 22
    if logo is not None:
        cursor += _paste(canvas, logo, (cursor, top + 16 - logo.height / 2)) + 6
    cursor += canvas.text((cursor, top + 16), title, 15, anchor="lm") + 8
    if chip:
        width = canvas.width(chip, 11) + 12
        canvas.draw.rounded_rectangle((cursor, top + 8, cursor + width, top + 24), radius=8,
                                      outline=canvas.color("ink"), width=1)
        canvas.text((cursor + width / 2, top + 16), chip, 11, anchor="mm")
        cursor += width + 8
    if detail:
        canvas.text((right - 8, top + 16), detail, 11, "muted", anchor="rm", bold=False, width=right - 8 - cursor)
    canvas.rule((left + 8, top + HEADER, right - 8, top + HEADER), colour)


# --- Left column -----------------------------------------------------------------------------------

def _status(canvas, payload):
    source = payload["status"]
    services = source["data"].get("services", [])
    healthy = sum(1 for service in services if service.get("indicator") == "none")
    detail = f"{healthy}/{len(services)} 正常" if services else "暂不可用"
    if source["state"] == "stale_cache":
        detail = "缓存 · " + detail
    _card(canvas, LEFT_CARD, "status_bar", "服务状态", icon="pulse", detail=detail)
    left, right = LEFT_CARD[0] + 8, LEFT_CARD[2] - 8
    if not services:
        canvas.text((left, 120), "状态页暂不可用", 14, "muted", anchor="lm")
        return
    for index, service in enumerate(services[:5]):
        top = LEFT_CARD[1] + HEADER + 4 + index * 31
        indicator = service.get("indicator") or "unknown"
        tone = "major" if indicator == "critical" else indicator if indicator in STATUS_TEXT else "unknown"
        tone = "ok" if tone == "none" else tone
        canvas.draw.rounded_rectangle((left, top, left + 4, top + 26), radius=2, fill=canvas.color(tone))
        logo = _logo(service["id"], 18, max_width=22)
        if logo is not None:
            _paste(canvas, logo, (left + 19 - logo.width / 2, top + 13 - logo.height / 2))
        label = STATUS_TEXT.get(indicator, "未知")
        label_width = canvas.text((right, top + 9), label, 12, anchor="rm")
        draw_icon(canvas.draw, STATUS_ICON.get(indicator, "info"), (right - label_width - 17, top + 2), 14,
                  canvas.color(tone), canvas.color("paper"))
        status_left = right - label_width - 17
        name_width = status_left - LINK_MIN - (left + 34)
        incident = service.get("incident")
        name_y = top + (8 if incident else 13)
        drawn = canvas.text((left + 34, name_y), service["name"], 13, anchor="lm", width=name_width)
        if incident:
            canvas.text((left + 34, top + 21), incident, 11, "muted", anchor="lm", bold=False,
                        width=right - left - 34)
        # A wire from the name to its state: live current when it is up, a break when it is down.
        _status_link(canvas, left + 34 + drawn + LINK_GAP, status_left - LINK_GAP, name_y, indicator, tone)
        if index < len(services[:5]) - 1:
            canvas.rule((left + 34, top + 29, right, top + 29))


def _status_link(canvas, x0, x1, y, indicator, tone):
    """The service's link to its state, drawn as a wire in the state's colour.

    Up: one continuous wire between two plugged nodes with current chevrons running along it.
    Degraded or in maintenance: the wire runs through a resistor zigzag. Down: the wire is cut in
    the middle, its two ends bent apart with sparks, its nodes left open. Unknown: a faint dotted
    line."""
    if x1 - x0 < LINK_MIN:
        return
    draw, colour = canvas.draw, canvas.color(tone)
    mid = (x0 + x1) / 2
    r = 3

    def node(x, filled=True):
        draw.ellipse((x - r, y - r, x + r, y + r), fill=colour if filled else canvas.color("paper"), outline=colour,
                     width=2)

    if indicator == "none":
        draw.line((x0 + r, y, x1 - r, y), fill=colour, width=2)
        for x in (x0 + (x1 - x0) * share for share in (0.36, 0.64)):
            draw.line(((x - 3, y - 4), (x + 1, y), (x - 3, y + 4)), fill=colour, width=2)
        node(x0)
        node(x1)
    elif indicator in {"minor", "maintenance"}:
        zig = min(18, (x1 - x0) / 3)
        draw.line((x0 + r, y, mid - zig / 2, y), fill=colour, width=2)
        draw.line((mid + zig / 2, y, x1 - r, y), fill=colour, width=2)
        steps = 6
        points = [(mid - zig / 2 + zig * index / steps, y + (0 if index in (0, steps) else (-4 if index % 2 else 4)))
                  for index in range(steps + 1)]
        draw.line(points, fill=colour, width=2)
        node(x0)
        node(x1)
    elif indicator in {"major", "critical"}:
        gap = 7
        draw.line((x0 + r, y, mid - gap - 4, y), fill=colour, width=2)
        draw.line(((mid - gap - 4, y), (mid - gap, y + 4)), fill=colour, width=2)
        draw.line((mid + gap + 4, y, x1 - r, y), fill=colour, width=2)
        draw.line(((mid + gap + 4, y), (mid + gap, y - 4)), fill=colour, width=2)
        for dx, dy in ((-3, -6), (0, -7), (3, -6)):
            draw.line((mid + dx * 0.5, y - 2, mid + dx, y + dy), fill=colour, width=1)
        node(x0, filled=False)
        node(x1, filled=False)
    else:
        for x in range(round(x0), round(x1), 4):
            draw.point((x, y), fill=colour)


def _papers(canvas, payload, now):
    source = payload["arxiv"]
    papers = source["data"].get("papers", [])
    _card(canvas, PAPER_CARD, "papers_bar", "新论文", logo=_logo("arxiv", 18), chip=payload["arxiv_category"],
          detail=f"最新 {len(papers)} 篇" if papers else "")
    left, right = PAPER_CARD[0] + 8, PAPER_CARD[2] - 8
    if not papers:
        canvas.text((left, 300), "论文列表暂不可用", 14, "muted", anchor="lm")
        return
    for index, paper in enumerate(papers[:3]):
        top = PAPER_CARD[1] + HEADER + 6 + index * 61
        canvas.draw.rounded_rectangle((left, top + 1, left + 18, top + 19), radius=3, fill=canvas.color("ink"))
        canvas.text((left + 9, top + 10), str(index + 1), 12, "paper", anchor="mm")
        for line_index, line in enumerate(canvas.wrap(paper["title"], 13, right - left - 26, 2)):
            canvas.text((left + 26, top + line_index * 16), line, 13, anchor="la")
        chip = paper.get("id") or ""
        chip_width = canvas.width(chip, 10, False) + 10 if chip else 0
        if chip:
            canvas.draw.rounded_rectangle((right - chip_width, top + 36, right, top + 50), radius=3,
                                          outline=canvas.color("rule"), width=1)
            canvas.text((right - chip_width / 2, top + 43), chip, 10, anchor="mm", bold=False)
        authors = paper.get("authors") or []
        who = authors[0] + (" 等" if paper.get("author_count", 1) > 1 else "") if authors else ""
        stamp = parse_time(paper.get("time"))
        canvas.text((left + 26, top + 43), f"{who} · {ago(stamp, now) if stamp else ''}", 11, "muted",
                    anchor="lm", bold=False, width=right - left - 32 - chip_width)
        if index < 2:
            for x in range(left, right, 6):
                canvas.draw.line((x, top + 57, x + 2, top + 57), fill=canvas.color("rule"), width=1)


# --- Right column ----------------------------------------------------------------------------------

def _inked(logo, ink):
    """A one-colour dark mark recoloured to ``ink``, its coloured parts kept."""
    logo = logo.copy()
    pixels = logo.load()
    for y in range(logo.height):
        for x in range(logo.width):
            r, g, b, a = pixels[x, y]
            if a and max(r, g, b) < 110 and max(r, g, b) - min(r, g, b) < 40:
                pixels[x, y] = (*ink[:3], a)
    return logo


def _game_icon(canvas, payload, images, name, xy, size):
    """The game's small Steam icon at ``xy`` with rounded corners; returns the room it takes."""
    url = ((payload.get("icons") or {}).get("data") or {}).get("icons", {}).get(name)
    icon = images.get(url) if url else None
    if icon is None:
        return 0
    icon = ImageOps.fit(icon.convert("RGB"), (size, size), Image.Resampling.LANCZOS)
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, size - 1, size - 1), radius=max(2, size // 5), fill=255)
    canvas.image.paste(icon, (round(xy[0]), round(xy[1])), mask)
    return size + 4


def _round_corners(canvas, box, radius):
    """An image's corners rounded off into the card's paper, so it sits in the page rather than
    boxed in by a hard line."""
    x0, y0, x1, y1 = (round(value) for value in box)
    region = canvas.image.crop((x0, y0, x1 + 1, y1 + 1))
    mask = Image.new("L", region.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, region.width - 1, region.height - 1), radius=radius, fill=255)
    paper = Image.new(region.mode, region.size, canvas.color("paper"))
    canvas.image.paste(Image.composite(region, paper, mask), (x0, y0))


def _cover(image, size):
    return ImageOps.fit(image, size, Image.Resampling.LANCZOS, centering=(0.5, 0.4))


def _epic(canvas, payload, images, now):
    data = payload["epic"]["data"]
    current, upcoming = data.get("current", []), data.get("upcoming", [])
    later = "、".join(item["title"] for item in upcoming[:2])
    _card(canvas, EPIC_CARD, "epic_bar", "本周免费", logo=_logo("epic", 20),
          detail=f"下周：{later}" if later else "")
    cards = [(item, False) for item in current[:2]] + [(item, True) for item in upcoming[:2 - len(current[:2])]]
    left, right = EPIC_CARD[0] + 8, EPIC_CARD[2] - 8
    if not cards:
        canvas.text((left, 140), "本周暂无免费游戏" if payload["epic"]["state"] != "unavailable"
                    else "Epic 数据暂不可用", 15, "muted", anchor="lm")
        return
    top, bottom = EPIC_CARD[1] + HEADER + 4, EPIC_CARD[3] - 8
    width = (right - left - 10) // 2
    for index, (item, is_next) in enumerate(cards):
        x = left + index * (width + 10)
        image = images.get(item.get("image"))
        if image is not None:
            canvas.image.paste(_cover(image, (width, bottom - top)), (x, top))
        else:
            canvas.draw.rectangle((x, top, x + width, bottom), fill=canvas.color("panel"))
            draw_icon(canvas.draw, "gift", (x + width / 2 - 18, top + 18), 36, canvas.color("ink"),
                      canvas.color("panel"))
        canvas.draw.rectangle((x, bottom - 22, x + width, bottom), fill=canvas.color("band"))
        end, start = parse_time(item.get("end")), parse_time(item.get("start"))
        meta = (f"{start.astimezone(now.tzinfo):%m/%d} 开始" if is_next and start else
                epic_end_label(end, now) if end else "")
        # The deadline is what matters here: bold on the band, and the days left on the cover's corner
        # (crimson on the last day).
        meta_width = canvas.text((x + width - 6, bottom - 11), meta, 12, (255, 255, 255), anchor="rm")
        left_text = time_left(end, now) if end and not is_next else ""
        if left_text:
            last_day = "小时" in left_text
            _corner_chip(canvas, x + width, top, left_text, "major" if last_day else "gold",
                         "badge_ink" if last_day else (20, 20, 20))
        title_x = x + 6 + _game_icon(canvas, payload, images, item["title"], (x + 6, bottom - 19), 16)
        canvas.text((title_x, bottom - 11), item["title"], 13, (255, 255, 255), anchor="lm",
                    width=x + width - meta_width - 10 - title_x)
        tag = "下周" if is_next else "免费领"
        tag_width = canvas.width(tag, 12) + 12
        canvas.draw.rectangle((x, top, x + tag_width, top + 18),
                              fill=canvas.color("maintenance" if is_next else "major"))
        canvas.text((x + tag_width / 2, top + 9), tag, 12, "badge_ink", anchor="mm")
        _round_corners(canvas, (x, top, x + width, bottom), 8)


def _upcoming(canvas, payload, images, now):
    data = payload["upcoming"]["data"]
    items = data.get("items", [])
    source_name = data.get("source") or ""
    logo = _logo("igdb" if source_name == "IGDB" else "steam", 18)
    if source_name == "IGDB" and logo is not None:
        # IGDB's logo is black lettering: it takes the page's ink, so it stays bright at night.
        logo = _inked(logo, canvas.color("ink"))
    detail = "IGDB 期待度排行" if source_name == "IGDB" else "Steam 热门即将推出" if source_name else ""
    _card(canvas, UPCOMING_CARD, "upcoming_bar", "即将发售", logo=logo, detail=detail)
    left, right = UPCOMING_CARD[0] + 8, UPCOMING_CARD[2] - 8
    if not items:
        canvas.text((left, 260), "新作列表暂不可用", 14, "muted", anchor="lm")
        return
    count, gap = 4, 8
    width = (right - left - gap * (count - 1)) // count
    top = UPCOMING_CARD[1] + HEADER + 4
    for index, item in enumerate(items[:count]):
        x = left + index * (width + gap)
        box = (x, top, x + width, top + 46)
        image = images.get(item.get("image"))
        if image is not None:
            canvas.image.paste(_cover(image, (width, 46)), (x, top))
        else:
            canvas.draw.rectangle(box, fill=canvas.color("panel"))
            draw_icon(canvas.draw, "rocket", (x + width / 2 - 12, top + 11), 24, canvas.color("ink"),
                      canvas.color("panel"))
        # How soon it comes out, on the cover's corner: crimson for today or tomorrow.
        stamp = parse_time(item.get("date"))
        soon = days_until(stamp, now) if stamp else ""
        if soon:
            _corner_chip(canvas, x + width, top, soon, "major" if soon in ("今天", "明天") else "upcoming_bar")
        _round_corners(canvas, box, 6)
        name_x = x + _game_icon(canvas, payload, images, item["name"], (x, top + 49), 14)
        canvas.text((name_x, top + 56), item["name"], 12, anchor="lm", width=x + width - name_x)
        when = (f"{stamp.astimezone(now.tzinfo):%m/%d} 周{WEEKDAYS[stamp.astimezone(now.tzinfo).weekday()]}"
                if stamp else (item.get("date_text") or "日期待定"))
        calendar_mark = _asset_icon("icon-calendar", 13)
        if calendar_mark is not None:
            _paste(canvas, calendar_mark, (x, top + 63))
        else:
            draw_icon(canvas.draw, "calendar", (x, top + 64), 11, canvas.color("ink"), canvas.color("paper"))
        # The release date in bold ink; the platforms after it, muted.
        date_width = canvas.text((x + 15, top + 70), when, 12, anchor="lm", width=width - 15)
        platforms = "/".join(item.get("platforms") or [])
        if platforms and width - 15 - date_width > 24:
            canvas.text((x + 19 + date_width, top + 70), platforms, 10, "muted", anchor="lm", bold=False,
                        width=width - 19 - date_width)


def _initials(name):
    """'DOP 4: Draw One Part' -> 'DOP4'; 'Super Mario 64' -> 'SM64'."""
    words = re.findall(r"[A-Za-z0-9]+", str(name or "").split(":")[0])
    if len(words) == 1:
        return words[0][:4].upper()
    return "".join(word if word.isdigit() or word.isupper() else word[0] for word in words)[:4].upper() or "?"


def coverage_label(data, now):
    """'本周' once a full week of runs is counted, otherwise the honest '近 N 小时/天'."""
    since = parse_time(data.get("since"))
    if since is None:
        return ""
    span = (now - since).total_seconds()
    if span >= 7 * 86400 - 3600:
        return "本周"
    if span >= 2 * 86400:
        return f"近 {int(span // 86400)} 天"
    return f"近 {max(1, int(span // 3600))} 小时"


def _speedruns(canvas, payload, images, now):
    data = payload["speedrun"]["data"]
    games = data.get("top", [])
    window = coverage_label(data, now)
    detail = f"speedrun.com · {window}新验证 {data.get('total', 0):,} 次" if games else "speedrun.com"
    _card(canvas, SPEED_CARD, "speed_bar", "速通周榜", logo=_logo("speedrun", 18), detail=detail)
    left, right = SPEED_CARD[0] + 8, SPEED_CARD[2] - 8
    if not games:
        canvas.text((left, 400), "正在统计 speedrun.com 新验证的成绩", 13, "muted", anchor="lm")
        return
    gap = 10
    width = (right - left - gap * 2) // 3
    top = SPEED_CARD[1] + HEADER + 4
    for index, game in enumerate(games[:3]):
        x = left + index * (width + gap)
        if index:
            canvas.rule((x - gap / 2, top, x - gap / 2, SPEED_CARD[3] - 8))
        cover = images.get(game.get("cover"))
        box = (x, top, x + 36, top + 48)
        if cover is not None:
            canvas.image.paste(_cover(cover, (36, 48)), (x, top))
        else:
            # No box art anywhere (mobile-only games): a lettered tile in the cover slot.
            canvas.draw.rectangle(box, fill=canvas.color("band"))
            canvas.text((x + 18, top + 20), _initials(game.get("name")), 11, (255, 255, 255), anchor="mm")
            draw_icon(canvas.draw, "trophy", (x + 12, top + 30), 12, canvas.color("gold"), canvas.color("band"))
        _round_corners(canvas, box, 6)
        badge_fill = canvas.color("gold" if index == 0 else "ink")
        canvas.draw.ellipse((x - 5, top - 5, x + 13, top + 13), fill=badge_fill, outline=canvas.color("paper"))
        canvas.text((x + 4, top + 4), str(index + 1), 11, (20, 20, 20) if index == 0 else "paper", anchor="mm")
        for line_index, line in enumerate(canvas.wrap(game.get("name") or "", 12, width - 44, 2)):
            canvas.text((x + 44, top + 1 + line_index * 15), line, 12, anchor="la")
        meta = " · ".join(part for part in (f"{game.get('week_runs', 0)} 次验证", str(game.get("released") or ""))
                          if part)
        canvas.text((x + 44, top + 40), meta, 11, "muted", anchor="lm", bold=False, width=width - 44)
        for run_index, run in enumerate(game.get("runs", [])[:2]):
            y = top + 58 + run_index * 30
            canvas.draw.ellipse((x, y + 3, x + 8, y + 11), fill=canvas.color("speed_bar"), outline=canvas.color("ink"))
            time_text = run_time(run["seconds"])
            time_width = canvas.text((x + width, y + 7), time_text, 12, anchor="rm")
            canvas.text((x + 13, y + 7), run.get("category") or "", 11, anchor="lm", width=width - time_width - 19)
            flag = images.get(flag_url(run.get("country")))
            runner_x = x + 13
            if flag is not None:
                small = ImageOps.contain(flag, (16, 11), Image.Resampling.LANCZOS)
                canvas.image.paste(small, (runner_x, y + 15))
                runner_x += small.width + 4
            verified = parse_time(run.get("verified"))
            when = ago(verified, now) if verified else ""
            when_width = canvas.text((x + width, y + 20), when, 10, "muted", anchor="rm", bold=False)
            canvas.text((runner_x, y + 20), run.get("runner") or "", 11, "muted", anchor="lm", bold=False,
                        width=x + width - when_width - 6 - runner_x)


def render_page(dimensions, payload, settings, now, images):
    now = local_now(now)
    canvas = PageCanvas(_palette(settings), family=settings.get("fontFamily") or "Microsoft YaHei")
    pasted = paste_local_wordmark(canvas.image, ASSETS / "title_wordmark.png", (12, 6, 212, 50), align="left")
    if pasted:
        title_right = pasted[2]
    else:
        title_right = 16 + canvas.text((16, 27), "科技与游戏", 30, anchor="lm")
    subtitle_right = title_right + 14 + canvas.text((title_right + 14, 30), "服务状态 · 免费游戏 · 新作 · 论文 · 速通", 14, "muted",
                                                    anchor="lm", bold=False)
    date_left = 788 - canvas.text((788, 27), f"{now:%m.%d} 周{WEEKDAYS[now.weekday()]} {now:%H:%M}", 16, anchor="rm")
    paste_header_art(canvas.image, ASSETS / "header_art.png", subtitle_right, date_left)
    canvas.rule((12, 52, 788, 52), "ink", 2)
    _status(canvas, payload)
    _papers(canvas, payload, now)
    _epic(canvas, payload, images, now)
    _upcoming(canvas, payload, images, now)
    _speedruns(canvas, payload, images, now)
    result = canvas.image
    if tuple(dimensions) != (800, 480):
        result = result.resize(tuple(dimensions), Image.Resampling.LANCZOS)
    return result
