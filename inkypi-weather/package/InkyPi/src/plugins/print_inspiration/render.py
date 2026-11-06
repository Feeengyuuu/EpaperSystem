"""800 x 480 打印灵感 page.

Header: the page's title wordmark, Printables' mark and name, and the date. Left: the week's most
liked model leads with its picture, its name (in Chinese with the original under it), summary,
category, maker and its likes, downloads, makes, rating and comments; the next four stand in cards
beneath, picture over name and counts. Right: the week's most printed models (most "makes") and
the all-time classics, a small picture beside each name and its counts. The cards carry a quiet
doodle of a maker's workshop in Printables' orange.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from PIL import Image, ImageDraw, ImageOps

from utils.doodle_backdrop import paint_doodles
from utils.flat_icons import draw_icon
from utils.local_wordmark import paste_local_wordmark
from utils.page_canvas import PageCanvas


ASSETS = Path(__file__).resolve().parent / "assets"
TITLE = ASSETS / "title_wordmark.png"
DOODLES = ASSETS / "doodles" / "workshop.png"
DOODLE_STRENGTH = {"day": 0.18, "night": 0.28}
# Printables' orange (its brand, from its official mark) for the solid marks: badges, chips, icons.
ORANGE = (0xFA, 0x68, 0x31)
DAY = {"paper": "#ffffff", "ink": "#121a20", "muted": "#3a454d", "rule": "#b4b4b4", "lead": "#fff4ec",
       "card": "#ffffff", "track": "#eef0f2"}
NIGHT = {"paper": "#000000", "ink": "#edf1ea", "muted": "#c6d0d5", "rule": "#5a5a5a", "lead": "#1f1209",
         "card": "#0b0b0b", "track": "#24282d"}
# Printables' mark (the folded P) in its 24 x 24 drawing, as two polygons.
MARK = (((3.678, 4.8), (12, 9.6), (12, 19.2), (20.322, 14.4), (20.322, 4.8), (12, 0)),
        ((12, 19.2), (3.678, 14.4), (3.678, 24)))
WEEKDAYS = "一二三四五六日"
LOCAL_TZ = ZoneInfo("America/Los_Angeles")
HEADER = (8, 4, 792, 54)
LEAD = (10, 60, 522, 262)
GRID = (10, 270, 522, 472)
CARDS = 4
CARD_GAP = 8
RIGHT = (530, 60, 790, 472)
MAKES_ROWS = 5
CLASSIC_ROWS = 3
ROW_HEIGHT = 44
SECTION_HEIGHT = 24
LEAD_PICTURE = (276, 190)
SUMMARY_LINES = 3


def _rgb(value):
    if isinstance(value, str) and value.startswith("#") and len(value) == 7:
        return tuple(int(value[index:index + 2], 16) for index in (1, 3, 5))
    return value


def _palette(settings):
    theme = (settings or {}).get("_inkypi_theme") or {}
    palette = NIGHT if theme.get("mode", (settings or {}).get("themeMode")) == "night" else DAY
    return {key: _rgb(value) for key, value in palette.items()}


def _night(canvas):
    return sum(canvas.color("paper")) < 200


def count_text(value):
    """1520 -> "1.5k", 75 -> "75", 23400 -> "23k"."""
    value = int(value or 0)
    if value >= 10000:
        return f"{value // 1000}k"
    return f"{value / 1000:.1f}k" if value >= 1000 else str(value)


def shown_name(model, chinese):
    """The model's name and summary as the page prints them (Chinese when translated) and whether
    they were translated."""
    translated = (chinese or {}).get(model["id"]) or {}
    return (translated.get("title") or model["name"], translated.get("summary") or model["summary"],
            bool(translated.get("title")))


def draw_mark(canvas, x, y, size, color=ORANGE):
    """Printables' folded-P mark, ``size`` px tall, its top left at (x, y)."""
    scale = size / 24
    for polygon in MARK:
        canvas.draw.polygon([(x + px * scale, y + py * scale) for px, py in polygon], fill=color)


def _picture(canvas, picture, box, radius=6):
    """``picture`` cropped to fill ``box`` with rounded corners (a quiet tile when it is missing)."""
    x1, y1, x2, y2 = (int(value) for value in box)
    size = (max(1, x2 - x1), max(1, y2 - y1))
    if picture is None:
        canvas.draw.rounded_rectangle(box, radius=radius, fill=canvas.color("track"))
        draw_mark(canvas, (x1 + x2) / 2 - min(size) * 0.18, (y1 + y2) / 2 - min(size) * 0.2, min(size) * 0.4,
                  canvas.color("rule"))
        return
    fitted = ImageOps.fit(picture.convert("RGB"), size, Image.Resampling.LANCZOS)
    mask = Image.new("L", size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, size[0] - 1, size[1] - 1), radius=radius, fill=255)
    canvas.image.paste(fitted, (x1, y1), mask)


def _badge(canvas, x, y, rank, size=20):
    canvas.draw.ellipse((x, y, x + size, y + size), fill=ORANGE)
    canvas.text((x + size / 2, y + size / 2), str(rank), max(9, size - 8), (255, 255, 255), anchor="mm")


def _nozzle(draw, x, y, size, color):
    """A printer's hot end extruding: the block, its tapered nozzle and a drop of filament (a "make")."""
    s = size
    draw.rectangle((x + s * 0.22, y, x + s * 0.78, y + s * 0.38), fill=color)
    draw.polygon([(x + s * 0.3, y + s * 0.38), (x + s * 0.7, y + s * 0.38), (x + s * 0.56, y + s * 0.66),
                  (x + s * 0.44, y + s * 0.66)], fill=color)
    draw.ellipse((x + s * 0.38, y + s * 0.74, x + s * 0.62, y + s * 0.98), fill=color)


def _icon(canvas, kind, x, y, size):
    if kind == "nozzle":
        _nozzle(canvas.draw, x, y, size, ORANGE)
    else:
        draw_icon(canvas.draw, kind, (x, y), size, ORANGE, canvas.color("paper"))


def _stat(canvas, x, middle, kind, value, size=12, icon=12):
    """An icon in Printables' orange and its count after it; returns the width it took."""
    _icon(canvas, kind, x, middle - icon / 2, icon)
    return icon + 3 + canvas.text((x + icon + 3, middle), value, size, anchor="lm")


def _chip(canvas, x, middle, text, size=10, fill=ORANGE, color=(255, 255, 255)):
    width = canvas.width(text, size) + 12
    canvas.draw.rounded_rectangle((x, middle - 8, x + width, middle + 8), radius=8, fill=fill)
    canvas.text((x + width / 2, middle), text, size, color, anchor="mm")
    return width


def _date_text(value):
    """"10月25日" for a model's ISO publication time, else ""."""
    try:
        published = datetime.fromisoformat(str(value)).astimezone(LOCAL_TZ)
    except (TypeError, ValueError):
        return ""
    return f"{published.month}月{published.day}日"


def _header(canvas, now):
    x1, y1, x2, y2 = HEADER
    placed = paste_local_wordmark(canvas.image, TITLE, (x1 + 2, y1, x1 + 210, y2), align="left")
    left = (placed[2] if placed else x1 + canvas.text((x1, (y1 + y2) / 2), "打印灵感", 26, anchor="lm")) + 14
    draw_mark(canvas, left, y1 + 9, 20)
    canvas.text((left + 22, y1 + 19), "Printables", 16, anchor="lm")
    canvas.text((left, y1 + 40), "本周 3D 打印模型热门 · 社区点赞与打印数", 10, "muted", anchor="lm", bold=False)
    canvas.text((x2, y1 + 19), f"{now:%m.%d} 周{WEEKDAYS[now.weekday()]}", 18, anchor="rm")
    canvas.text((x2, y1 + 40), f"更新 {now:%H:%M}", 10, "muted", anchor="rm", bold=False)
    canvas.rule((x1, y2 + 2, x2, y2 + 2))


def _lead(canvas, model, images, chinese, doodle_key):
    x1, y1, x2, y2 = LEAD
    canvas.draw.rounded_rectangle(LEAD, radius=8, fill=canvas.color("lead"), outline=ORANGE, width=2)
    text_left = x1 + 10 + LEAD_PICTURE[0] + 12
    paint_doodles(canvas.image, (text_left - 4, y1 + 3, x2 - 3, y2 - 3), DOODLES, ORANGE,
                  strength=DOODLE_STRENGTH[doodle_key], grounds=[canvas.color("lead")])
    _picture(canvas, images.get(model["image"]), (x1 + 6, y1 + 6, x1 + 6 + LEAD_PICTURE[0], y2 - 6))
    _badge(canvas, x1 + 12, y1 + 12, 1, 24)
    width = x2 - 10 - text_left
    name, summary, translated = shown_name(model, chinese)
    y = y1 + 10
    _chip(canvas, text_left, y + 8, "本周最受欢迎")
    y += 22
    for line in canvas.wrap(name, 17, width, 2):
        canvas.text((text_left, y), line, 17)
        y += 22
    if translated:
        canvas.text((text_left, y), model["name"], 10, "muted", width=width, bold=False)
        y += 15
    for line in canvas.wrap(summary, 11, width, SUMMARY_LINES, bold=False):
        canvas.text((text_left, y), line, 11, "muted", bold=False)
        y += 15
    y += 4
    chips_x = text_left
    if model.get("category"):
        chips_x += _chip(canvas, chips_x, y + 8, canvas.fit(model["category"], 10, width - 60),
                         fill=canvas.color("ink"), color=canvas.color("paper")) + 6
    if model.get("gcode"):
        _chip(canvas, chips_x, y + 8, "含 G-code")
    # When it was published and how many have looked at it and talked about it.
    facts = [text for text in (f"{_date_text(model.get('published'))}发布" if _date_text(model.get("published")) else "",
                               f"{count_text(model.get('views'))} 浏览" if model.get("views") else "",
                               f"{count_text(model.get('comments'))} 评论" if model.get("comments") else "") if text]
    if facts:
        canvas.text((text_left, y + 26), " · ".join(facts), 10, "muted", width=width, bold=False)
    maker_y = y2 - 42
    avatar = images.get(model.get("avatar"))
    maker_x = text_left
    if avatar is not None:
        _picture(canvas, avatar, (maker_x, maker_y - 9, maker_x + 18, maker_y + 9), radius=9)
        maker_x += 23
    canvas.text((maker_x, maker_y), f"by {model.get('maker') or 'Printables'}", 11, anchor="lm", width=x2 - 10 - maker_x)
    x = text_left
    middle = y2 - 16
    for kind, value in (("heart", count_text(model["likes"])), ("download", count_text(model["downloads"])),
                        ("nozzle", count_text(model["makes"])),
                        ("star", f"{model['rating']:.1f}" if model.get("ratings") else "–"),
                        ("comment", count_text(model["comments"]))):
        if x > x2 - 40:
            break
        x += _stat(canvas, x, middle, kind, value) + 10


def _card(canvas, box, model, rank, images, chinese):
    x1, y1, x2, y2 = box
    canvas.draw.rounded_rectangle(box, radius=7, fill=canvas.color("card"), outline=canvas.color("rule"), width=1)
    picture_bottom = y1 + int((x2 - x1 - 8) * 0.75) + 4
    _picture(canvas, images.get(model["image"]), (x1 + 4, y1 + 4, x2 - 4, picture_bottom), radius=5)
    _badge(canvas, x1 + 8, y1 + 8, rank, 18)
    name, _summary, _translated = shown_name(model, chinese)
    y = picture_bottom + 5
    for line in canvas.wrap(name, 12, x2 - x1 - 10, 2):
        canvas.text((x1 + 5, y), line, 12)
        y += 16
    if model.get("category"):
        canvas.text((x1 + 5, y + 2), model["category"], 10, "muted", width=x2 - x1 - 10, bold=False)
    # Who printed it and how it rates, over its likes and downloads.
    middle = y2 - 30
    x = x1 + 5
    x += _stat(canvas, x, middle, "nozzle", f"{count_text(model['makes'])} 打印", size=10, icon=11) + 8
    if model.get("ratings"):
        _stat(canvas, x, middle, "star", f"{model['rating']:.1f}", size=10, icon=11)
    middle = y2 - 12
    x = x1 + 5
    x += _stat(canvas, x, middle, "heart", count_text(model["likes"]), size=11, icon=11) + 8
    _stat(canvas, x, middle, "download", count_text(model["downloads"]), size=11, icon=11)


def _section(canvas, y, title, icon):
    x1, _y1, x2, _y2 = RIGHT
    _icon(canvas, icon, x1 + 2, y + 3, 15)
    canvas.text((x1 + 22, y + 10), title, 14, anchor="lm")
    canvas.rule((x1 + 22 + canvas.width(title, 14) + 8, y + 11, x2, y + 11))
    return y + SECTION_HEIGHT


def _row(canvas, y, model, images, chinese, stat):
    x1, _y1, x2, _y2 = RIGHT
    _picture(canvas, images.get(model.get("image_small")) or images.get(model["image"]),
             (x1, y, x1 + 54, y + ROW_HEIGHT - 6), radius=4)
    name, _summary, _translated = shown_name(model, chinese)
    left = x1 + 60
    lines = canvas.wrap(name, 11, x2 - left, 2)
    for index, line in enumerate(lines):
        canvas.text((left, y + index * 13), line, 11)
    middle = y + ROW_HEIGHT - 9
    x = left
    kind, value = stat
    x += _stat(canvas, x, middle, kind, value, size=10, icon=10) + 8
    _stat(canvas, x, middle, "heart", count_text(model["likes"]), size=10, icon=10)


def render_page(size, payload, settings, now, images):
    palette = _palette(settings)
    canvas = PageCanvas(palette, family=str((settings or {}).get("fontFamily") or "Microsoft YaHei"), size=(800, 480))
    doodle_key = "night" if _night(canvas) else "day"
    chinese = payload.get("chinese") or {}
    _header(canvas, now)
    week = list(payload.get("week") or [])
    if week:
        _lead(canvas, week[0], images, chinese, doodle_key)
        x1, y1, x2, y2 = GRID
        width = (x2 - x1 - CARD_GAP * (CARDS - 1)) / CARDS
        for index, model in enumerate(week[1:CARDS + 1]):
            left = x1 + index * (width + CARD_GAP)
            _card(canvas, (round(left), y1, round(left + width), y2), model, index + 2, images, chinese)
    else:
        canvas.text(((LEAD[0] + LEAD[2]) / 2, 260), "Printables 暂不可用", 16, "muted", anchor="mm")
    y = RIGHT[1]
    y = _section(canvas, y, "本周打印最多", "nozzle")
    for model in list(payload.get("makes") or [])[:MAKES_ROWS]:
        _row(canvas, y, model, images, chinese, ("nozzle", f"{count_text(model['makes'])} 次打印"))
        y += ROW_HEIGHT
    y += 4
    y = _section(canvas, y, "历史名作", "trophy")
    for model in list(payload.get("classics") or [])[:CLASSIC_ROWS]:
        if y + ROW_HEIGHT > RIGHT[3] + 2:
            break
        _row(canvas, y, model, images, chinese, ("download", f"{count_text(model['downloads'])} 下载"))
        y += ROW_HEIGHT
    image = canvas.image
    if tuple(size) != image.size:
        image = image.resize(tuple(size), Image.Resampling.LANCZOS)
    return image
