"""800 x 480 Bay Traffic Cams page: the main camera large on the left with its last three
hourly stills beneath (a time-lapse up to now), four cameras on other roads on the right.

Every still carries its road, direction, how long ago it was taken and an arrow the way it
lies from home; one that stopped updating is marked 画面停更. The road's shield colour runs
as a solid bar under each road name.
"""

from __future__ import annotations

import math
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageDraw, ImageOps

from utils.local_wordmark import paste_local_wordmark
from utils.page_canvas import PageCanvas

from . import sources


ASSETS = Path(__file__).resolve().parent / "assets"
DAY = {"paper": "#ffffff", "ink": "#121a20", "muted": "#3a454d", "rule": "#a6a6a6", "live": "#d42020",
       "stale": "#8a5a00", "shade": "#121a20"}
NIGHT = {"paper": "#000000", "ink": "#edf1ea", "muted": "#c6d0d5", "rule": "#5a5a5a", "live": "#ff6b6b",
         "stale": "#f0b44a", "shade": "#000000"}
# Interstate shields are blue, state routes green, US routes black: each label's bar follows its road.
ROUTE_COLOURS = {"I": (0x00, 0x3F, 0x87), "SR": (0x1E, 0x7B, 0x46), "US": (0x24, 0x24, 0x24)}
MAIN = (12, 58, 452, 372)
STRIP = (12, 380, 452, 470)
SIDE = (464, 58, 788, 470)
# A still older than this is marked as not updating.
STALE_MINUTES = 30
WEEKDAYS = "一二三四五六日"


def _rgb(value):
    if isinstance(value, str) and value.startswith("#") and len(value) == 7:
        return tuple(int(value[index:index + 2], 16) for index in (1, 3, 5))
    return value


def _palette(settings):
    theme = (settings or {}).get("_inkypi_theme") or {}
    palette = NIGHT if theme.get("mode", (settings or {}).get("themeMode")) == "night" else DAY
    return {key: _rgb(value) for key, value in palette.items()}


def route_colour(route):
    prefix = str(route or "").split("-", 1)[0].upper()
    return ROUTE_COLOURS.get(prefix, ROUTE_COLOURS["SR"])


def age_label(frame, now):
    """(label, stale) for a still: "2分钟前", or its age once it stops updating."""
    stamp = (frame or {}).get("taken") or (frame or {}).get("fetched")
    try:
        taken = datetime.fromisoformat(str(stamp))
    except (TypeError, ValueError):
        return "", False
    minutes = max(0, int((now - taken).total_seconds() // 60))
    text = "刚刚" if minutes < 1 else f"{minutes}分钟前" if minutes < 60 else f"{minutes // 60}小时前"
    return text, minutes >= STALE_MINUTES


def _still(canvas, frame, box, radius=6):
    x0, y0, x1, y1 = [int(value) for value in box]
    size = (max(1, x1 - x0), max(1, y1 - y0))
    if frame is None:
        canvas.draw.rounded_rectangle(box, radius=radius, fill=canvas.color("rule"))
        canvas.text(((x0 + x1) / 2, (y0 + y1) / 2), "暂无画面", 12, "paper", anchor="mm")
        return
    source = frame["image"]
    width, height = source.size
    # Caltrans' own date bar is cut off: each still carries its age on the page instead.
    view = source.crop((0, 0, width, height - round(height * sources.CAPTION_BAR)))
    tile = ImageOps.fit(view, size, method=Image.Resampling.LANCZOS)
    mask = Image.new("L", size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, size[0] - 1, size[1] - 1), radius=radius, fill=255)
    canvas.image.paste(tile, (x0, y0), mask)


def _arrow(canvas, center, degrees, size, fill):
    """A small solid arrow pointing the way from home to the camera (0 degrees = up, north)."""
    cx, cy = center
    angle = math.radians(degrees)
    points = ((0, -size), (size * 0.8, size * 0.85), (0, size * 0.4), (-size * 0.8, size * 0.85))
    turned = [(cx + x * math.cos(angle) - y * math.sin(angle), cy + x * math.sin(angle) + y * math.cos(angle))
              for x, y in points]
    canvas.draw.polygon(turned, fill=fill)


def _where(canvas, camera, xy, size, *, right=False, compass=True):
    """An arrow the way the camera lies from home, then "东北 2.4 公里" (the compass name
    left out where room is short). Returns its width."""
    text = f"{camera['distance_km']:.1f} 公里"
    if compass:
        text = f"{sources.compass_label(camera.get('bearing', 0))} {text}"
    arrow = size + 2
    width = canvas.width(text, size, bold=False) + arrow + 4
    x, y = xy
    left = x - width if right else x
    _arrow(canvas, (left + arrow / 2, y), camera.get("bearing", 0), arrow * 0.55, canvas.color("ink"))
    canvas.text((left + arrow + 4, y), text, size, "muted", anchor="lm", bold=False)
    return width


def _badge(canvas, xy, text, fill, size=11, *, right=False):
    x, y = xy
    width = canvas.width(text, size) + 12
    left = x - width if right else x
    canvas.draw.rounded_rectangle((left, y, left + width, y + size + 8), radius=(size + 8) // 2, fill=fill)
    canvas.text((left + width / 2, y + (size + 8) / 2), text, size, (255, 255, 255), anchor="mm")
    return width


def _road(canvas, camera, xy, size):
    """The road and direction on a bar in the road's shield colour. Returns its width."""
    x, y = xy
    road = f"{camera['route']} {sources.direction_label(camera['direction'])}"
    width = canvas.width(road, size) + 14
    canvas.draw.rounded_rectangle((x, y, x + width, y + size + 8), radius=4, fill=route_colour(camera["route"]))
    canvas.text((x + 7, y + (size + 8) / 2), road, size, (255, 255, 255), anchor="lm")
    return width


def _main(canvas, payload, now):
    cameras = payload["cameras"]
    frames = payload["frames"]
    x0, y0, x1, y1 = MAIN
    if not cameras:
        canvas.draw.rounded_rectangle(MAIN, radius=8, outline=canvas.color("rule"))
        canvas.text(((x0 + x1) / 2, (y0 + y1) / 2), "附近摄像头暂不可用", 16, "muted", anchor="mm")
        return
    camera = cameras[0]
    frame = frames.get(camera["image"])
    _still(canvas, frame, (x0, y0, x1, y1 - 30), radius=8)
    age, stale = age_label(frame, now)
    if frame is not None and not stale:
        _badge(canvas, (x0 + 10, y0 + 10), "● LIVE", canvas.color("live"), 11)
    if stale:
        _badge(canvas, (x1 - 10, y0 + 10), f"画面停更 · {age}", canvas.color("stale"), 11, right=True)
    elif age:
        _badge(canvas, (x1 - 10, y0 + 10), age, canvas.color("shade"), 11, right=True)
    top = y1 - 24
    road = _road(canvas, camera, (x0, top), 13)
    where = _where(canvas, camera, (x1, top + 10.5), 12, right=True)
    canvas.text((x0 + road + 8, top + 10.5), camera["place"], 13, anchor="lm", width=x1 - x0 - road - where - 18)


def _lapse_label(frame, hours):
    """ "1小时前 · 09:45" from when the still was taken, or just the hour it stands for."""
    stamp = (frame or {}).get("taken")
    try:
        taken = sources.local_now(datetime.fromisoformat(str(stamp)))
    except (TypeError, ValueError):
        return f"{hours}小时前"
    return f"{hours}小时前 · {taken:%H:%M}"


def _timelapse(canvas, payload, now):
    """The main camera's hourly stills, oldest first, leading up to the live view above."""
    cameras = payload["cameras"]
    x0, y0, x1, y1 = STRIP
    history = list(reversed(cameras[0].get("history") or [])) if cameras else []
    canvas.text((x0, y0 + 2), "缩时", 13)
    canvas.text((x0, y0 + 20), f"过去 {len(history)} 小时", 10, "muted", bold=False)
    if not history:
        return
    left = x0 + 66
    gap = 8
    width = (x1 - left - gap * (len(history) - 1)) / len(history)
    for index, url in enumerate(history):
        cell_x = left + index * (width + gap)
        frame = payload["frames"].get(url)
        _still(canvas, frame, (cell_x, y0, cell_x + width, y1 - 16), radius=5)
        label = _lapse_label(frame, len(history) - index)
        canvas.text((cell_x + width / 2, y1 - 7), label, 10, "muted", anchor="mm", bold=False, width=width)
    # The lapse runs up into the live view: an arrow from the strip to the main still.
    arrow_x = x0 + 30
    canvas.draw.line((arrow_x, y1 - 8, arrow_x, y0 + 40), fill=canvas.color("ink"), width=2)
    canvas.draw.polygon(((arrow_x - 5, y0 + 44), (arrow_x + 5, y0 + 44), (arrow_x, y0 + 36)), fill=canvas.color("ink"))


def _side(canvas, payload, now):
    cameras = payload["cameras"][1:5]
    x0, y0, x1, y1 = SIDE
    cell_w = (x1 - x0 - 10) / 2
    cell_h = (y1 - y0 - 10) / 2
    for index, camera in enumerate(cameras):
        column, row = index % 2, index // 2
        cx = x0 + column * (cell_w + 10)
        cy = y0 + row * (cell_h + 10)
        frame = payload["frames"].get(camera["image"])
        _still(canvas, frame, (cx, cy, cx + cell_w, cy + cell_h - 48), radius=6)
        top = cy + cell_h - 44
        _road(canvas, camera, (cx, top), 11)
        age, stale = age_label(frame, now)
        if stale:
            _badge(canvas, (cx + cell_w, top), "画面停更", canvas.color("stale"), 9, right=True)
        elif age:
            canvas.text((cx + cell_w, top + 9.5), age, 10, "muted", anchor="rm", bold=False)
        place_y = top + 31
        where = _where(canvas, camera, (cx + cell_w, place_y), 10, right=True, compass=False)
        canvas.text((cx, place_y), camera["place"], 11, anchor="lm", width=cell_w - where - 6)


def render_page(dimensions, payload, settings, now):
    canvas = PageCanvas(_palette(settings), family=(settings or {}).get("fontFamily") or "Microsoft YaHei")
    placed = paste_local_wordmark(canvas.image, ASSETS / "title_wordmark.png", (10, 2, 250, 50), align="left")
    title_right = placed[2] if placed else 14 + canvas.text((14, 26), "湾区路况", 28, anchor="lm")
    date_left = 788 - canvas.text((788, 26), f"{now:%m.%d} 周{WEEKDAYS[now.weekday()]} {now:%H:%M}", 16, anchor="rm")
    count = len(payload["cameras"])
    note = f"Caltrans 实时摄像头 · 离家最近 {count} 个镜头" if count else "Caltrans 实时摄像头"
    canvas.text((title_right + 16, 26), note, 13, "muted", anchor="lm", bold=False, width=date_left - title_right - 32)
    canvas.rule((12, 52, 788, 52), "ink", 2)
    _main(canvas, payload, now)
    _timelapse(canvas, payload, now)
    _side(canvas, payload, now)
    result = canvas.image
    if tuple(dimensions) != (800, 480):
        result = result.resize(tuple(dimensions), Image.Resampling.LANCZOS)
    return result
