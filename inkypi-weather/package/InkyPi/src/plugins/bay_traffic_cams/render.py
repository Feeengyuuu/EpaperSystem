"""800 x 480 Bay Traffic Cams page, laid out like a traffic operations centre: the main camera
large on the left with its last three hourly stills beneath (a time-lapse up to now), four
cameras on other roads on the right.

Every camera is labelled the way the road signs it: its official route shield, a direction
plate and a green guide sign with the place, then a navigation arrow the way it lies from
home and how long ago the still was taken; one that stopped updating is marked 画面停更.
Each still carries a CCTV-style camera tag.
"""

from __future__ import annotations

from datetime import datetime
from functools import lru_cache
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
# Used only when a route has no shield file: Interstate blue, state route green, US route black.
ROUTE_COLOURS = {"I": (0x00, 0x3F, 0x87), "SR": (0x1E, 0x7B, 0x46), "US": (0x24, 0x24, 0x24)}
# Guide signs are MUTCD green with white legend and border.
GUIDE_GREEN = (0x00, 0x6B, 0x3F)
WHITE = (255, 255, 255)
BLACK = (0, 0, 0)
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


@lru_cache(maxsize=64)
def _art(folder, name, height):
    path = ASSETS / folder / f"{name}.png"
    if not path.is_file():
        return None
    with Image.open(path) as source:
        art = source.convert("RGBA")
    return art.resize((max(1, round(art.width * height / art.height)), height), Image.Resampling.LANCZOS)


@lru_cache(maxsize=64)
def _nav_arrow(bearing, size):
    """The navigation arrow turned to a bearing (0 = north, up), in a size x size square."""
    art = _art("icons", "nav_arrow", 64)
    if art is None:
        return None
    side = max(art.size)
    square = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    square.alpha_composite(art, ((side - art.width) // 2, (side - art.height) // 2))
    turned = square.rotate(-bearing, resample=Image.Resampling.BICUBIC, expand=True)
    return ImageOps.contain(turned, (size, size), method=Image.Resampling.LANCZOS)


def _paste(canvas, art, xy):
    canvas.image.paste(art, (round(xy[0]), round(xy[1])), art)


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


def _badge(canvas, xy, text, fill, size=11, *, right=False):
    x, y = xy
    width = canvas.width(text, size) + 12
    left = x - width if right else x
    canvas.draw.rounded_rectangle((left, y, left + width, y + size + 8), radius=(size + 8) // 2, fill=fill)
    canvas.text((left + width / 2, y + (size + 8) / 2), text, size, WHITE, anchor="mm")
    return width


def _cam_tag(canvas, xy, number, camera):
    """A CCTV-style tag on the still: "CAM 01 · TVB11", white on a dark plate."""
    text = f"CAM {number:02d}" + (f" · {camera['id']}" if camera.get("id") else "")
    width = canvas.width(text, 9) + 10
    x, y = xy
    canvas.draw.rectangle((x, y, x + width, y + 15), fill=(18, 18, 18))
    canvas.text((x + 5, y + 7.5), text, 9, WHITE, anchor="lm")


def _shield(canvas, route, xy, height):
    """The route's official shield (or a coloured tab when there is none); returns its width."""
    x, y = xy
    art = _art("shields", route, height)
    if art is not None:
        _paste(canvas, art, (x, y))
        return art.width
    width = canvas.width(route, height - 9) + 10
    canvas.draw.rounded_rectangle((x, y, x + width, y + height), radius=4, fill=route_colour(route))
    canvas.text((x + width / 2, y + height / 2), route, height - 9, WHITE, anchor="mm")
    return width


def _direction_plate(canvas, direction, xy, height):
    """The white cardinal plate posted with a shield ("南向"); returns its width (0 without one)."""
    label = sources.direction_label(direction) if direction else ""
    if not label:
        return 0
    x, y = xy
    size = height - 6
    width = canvas.width(label, size) + 8
    canvas.draw.rounded_rectangle((x, y, x + width, y + height), radius=2, fill=WHITE, outline=BLACK, width=1)
    canvas.text((x + width / 2, y + height / 2), label, size, BLACK, anchor="mm")
    return width


def _guide_sign(canvas, text, xy, height, max_width):
    """A green guide sign with a white border and legend, as wide as its text allows."""
    x, y = xy
    size = height - 7
    legend = canvas.fit(text, size, max(10, max_width - 10))
    width = min(max_width, canvas.width(legend, size) + 10)
    if width < 24:
        return 0
    canvas.draw.rounded_rectangle((x, y, x + width, y + height), radius=3, fill=GUIDE_GREEN)
    canvas.draw.rounded_rectangle((x + 1.5, y + 1.5, x + width - 1.5, y + height - 1.5), radius=2, outline=WHITE,
                                  width=1)
    canvas.text((x + 5, y + height / 2), legend, size, WHITE, anchor="lm")
    return width


def _where(canvas, camera, right, middle, size, *, compass=True):
    """The navigation arrow the way the camera lies from home, then "西南 2.4 公里", ending at
    ``right``; returns where it starts."""
    text = f"{camera['distance_km']:.1f} 公里"
    if compass:
        text = f"{sources.compass_label(camera.get('bearing', 0))} {text}"
    left = right - canvas.width(text, size, bold=False)
    canvas.text((right, middle), text, size, "muted", anchor="rm", bold=False)
    arrow = _nav_arrow(int(camera.get("bearing", 0)) % 360, size + 8)
    if arrow is not None:
        left -= arrow.width + 3
        _paste(canvas, arrow, (left, middle - arrow.height / 2))
    return left


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
    still_bottom = y1 - 30
    _still(canvas, frame, (x0, y0, x1, still_bottom), radius=8)
    age, stale = age_label(frame, now)
    if frame is not None and not stale:
        _badge(canvas, (x0 + 10, y0 + 10), "● LIVE", canvas.color("live"), 11)
    if stale:
        _badge(canvas, (x1 - 10, y0 + 10), f"画面停更 · {age}", canvas.color("stale"), 11, right=True)
    elif age:
        _badge(canvas, (x1 - 10, y0 + 10), age, canvas.color("shade"), 11, right=True)
    if frame is not None:
        _cam_tag(canvas, (x0 + 8, still_bottom - 23), 1, camera)
    top = y1 - 25
    x = x0 + _shield(canvas, camera["route"], (x0, top), 25) + 5
    x += _direction_plate(canvas, camera["direction"], (x, top + 3), 19)
    where_left = _where(canvas, camera, x1, top + 12.5, 12)
    _guide_sign(canvas, camera["place"], (x + 6, top + 2), 21, where_left - x - 14)


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
    icon = _art("icons", "timelapse", 40)
    if icon is not None:
        _paste(canvas, icon, (x0 + 30 - icon.width / 2, y0 + 2))
    canvas.text((x0 + 30, y0 + 54), "缩时回放", 11, anchor="mm")
    canvas.text((x0 + 30, y0 + 70), f"过去 {len(history)} 小时", 9, "muted", anchor="mm", bold=False)
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
        if frame is not None:
            _cam_tag(canvas, (cx + 5, cy + 5), index + 2, camera)
        # The still's age sits on it, as on the main view, so the guide sign gets the whole width.
        age, stale = age_label(frame, now)
        if stale:
            _badge(canvas, (cx + cell_w - 5, cy + cell_h - 70), "画面停更", canvas.color("stale"), 9, right=True)
        elif age and frame is not None:
            _badge(canvas, (cx + cell_w - 5, cy + cell_h - 70), age, canvas.color("shade"), 9, right=True)
        top = cy + cell_h - 45
        x = cx + _shield(canvas, camera["route"], (cx, top), 21) + 4
        _direction_plate(canvas, camera["direction"], (x, top + 2), 17)
        _where(canvas, camera, cx + cell_w, top + 10.5, 10)
        _guide_sign(canvas, camera["place"], (cx, top + 25), 19, cell_w)


def render_page(dimensions, payload, settings, now):
    canvas = PageCanvas(_palette(settings), family=(settings or {}).get("fontFamily") or "Microsoft YaHei")
    placed = paste_local_wordmark(canvas.image, ASSETS / "title_wordmark.png", (10, 2, 250, 50), align="left")
    title_right = placed[2] if placed else 14 + canvas.text((14, 26), "湾区路况", 28, anchor="lm")
    date_left = 788 - canvas.text((788, 26), f"{now:%m.%d} 周{WEEKDAYS[now.weekday()]} {now:%H:%M}", 16, anchor="rm")
    signal = _art("icons", "signal", 34)
    if signal is not None:
        date_left -= signal.width + 10
        _paste(canvas, signal, (date_left, 26 - signal.height / 2))
    x = title_right + 14
    cctv = _art("icons", "cctv", 28)
    if cctv is not None:
        _paste(canvas, cctv, (x, 26 - cctv.height / 2))
        x += cctv.width + 8
    count = len(payload["cameras"])
    note = f"Caltrans 实时监控 · 离家最近 {count} 个镜头" if count else "Caltrans 实时监控"
    canvas.text((x, 26), note, 13, "muted", anchor="lm", bold=False, width=date_left - x - 12)
    canvas.rule((12, 52, 788, 52), "ink", 2)
    _main(canvas, payload, now)
    _timelapse(canvas, payload, now)
    _side(canvas, payload, now)
    result = canvas.image
    if tuple(dimensions) != (800, 480):
        result = result.resize(tuple(dimensions), Image.Resampling.LANCZOS)
    return result
