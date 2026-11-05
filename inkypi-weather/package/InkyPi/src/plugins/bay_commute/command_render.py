"""Transport/coastal command layout, with source labels and optional nearby cameras."""

from __future__ import annotations

from datetime import datetime, time, timedelta
from functools import lru_cache
import math
from pathlib import Path

from PIL import Image, ImageColor, ImageDraw, ImageOps

from utils.app_utils import get_base_ui_font
from utils.fonts import load_font
from .map_view import draw_marker, render_map, source_logo
from .render import ASSETS, DIRECTIONS, FACILITIES, KINDS, draw_water, paste_sticker, source_label, tide_now
from .direction_plaques import draw_direction
from .sources import (
    BART_LATE_SECONDS, COMPASS, PACIFIC, coming_trains, local_now, next_tides, parse_time, rank_roads, road_state,
    select_roads,
)
from .traffic_icons import draw_icon


DAY = dict(paper="#ffffff", ink="#152839", muted="#43586a", rule="#b4bdc6", blue="#005b9c",
           pale="#eaf2f8", orange="#bd450b", soft="#faf0e8", planned="#596779", chart="#dcebf5",
           water_mid="#b9daea", water_deep="#8fc2dc", foam="#ffffff",
           crimson="#c81e32", emerald="#059669")
NIGHT = dict(paper="#080e14", ink="#eef4f9", muted="#b3c5d2", rule="#465562", blue="#7cc9fa",
             pale="#102538", orange="#ffb67c", soft="#301e14", planned="#bac8d6", chart="#102a3d",
             water_mid="#1f4a66", water_deep="#275a7c", foam="#cfe8f7",
             crimson="#ff6b78", emerald="#34d399")
PLUGIN_ROOT = Path(__file__).resolve().parent.parent
# The tide card's readouts in a display face (the bundled Space Grotesk, SIL OFL) and the weight
# set on it as a variable font (None for a static one); Inter stands in without it.
DISPLAY_FACE = (PLUGIN_ROOT.parent / "static" / "fonts" / "SpaceGrotesk-VF.ttf", 700)
# The next high and low tides (each time with its height beside it), the tide now (measured
# against the prediction when the station reports), the tide chart, then the buoy's row and
# BART's departures and service notes beneath it, down the right column.
NEXT_TITLE_Y, NEXT_VALUE_Y = 128, 143
READOUT_Y, CHART_HEADER_Y = 183, 203
CHART = (536, 218, 782, 301)
BUOY_Y = 327
BART_Y = 349
BART_ROWS, BART_ROW_STEP = 3, 15
# BART's service notes (advisories, elevators) under its departures: one line this tall each, an
# advisory taking up to two.
NOTE_STEP, ADVISORY_LINES = 12, 2
# The observed water's delta from the prediction reads "as predicted" within this many feet.
LEVEL_EVEN_FT = 0.1
# The travel-time sign in the header, between the title and the Caltrans mark: a dark plate like the
# sign itself, the route's shield and direction at its left, its three lines at its right.
SIGN_BOX = (250, 7, 512, 58)
SIGN_SHIELDS = {"I-680", "I-880", "SR-84"}
# A destination's leader to its times is drawn only with at least this much room (px); the
# station's stop ring before its name is this wide.
BART_LEADER_MIN = 14
STATION_RING = 12
# With the next train's length in the reading, the row draws that train in place of the leader,
# heading right for its times over a rail that runs back to its cars label, speed streaks behind it
# in the TRAIN_LEAD kept after the label: one car drawn for each it runs (BART up to ten; eBART one
# or two), the front car longer by its slanted nose. One scale for every train on the page, so a
# longer train reads longer: the widest car (CAR_WIDTHS, widest first) that lets the tightest row
# keep its destination whole, else in short ("Dublin"), else TRAIN_NAME_MIN of it; past that
# narrower, down to CAR_FLOOR. Each car width has its body's height (TRAIN_HEIGHTS), no taller
# than the rows' spacing allows.
TRAIN_MAX_CARS = 10
CAR_WIDTHS = range(12, 6, -1)
CAR_FLOOR = 5
TRAIN_HEIGHTS = {5: 8, 6: 10, 7: 12, 8: 12, 9: 13, 10: 13, 11: 14, 12: 14}
TRAIN_LEAD = 10
TRAIN_NAME_MIN = 36
# BART's own line colours, as its lettered bullets print them, for each train's stripe.
BART_LINE_COLOURS = {"red": "#ed1c24", "orange": "#faa61a", "yellow": "#ffe600", "blue": "#009ad9",
                     "green": "#50b848"}
# The column (and its divider) end here, above the footer rule.
BART_BOTTOM = 448
PLACE_MARKS = {"san jose": ("san-jose.png", 31, ""),
               "redwood city": ("redwood-city.png", 18, ""),
               "alamo": ("contra-costa-seal.png", 18, "Contra Costa")}


@lru_cache(maxsize=128)
def font(size, role="body", bold=True):
    if role == "display":
        path, weight = DISPLAY_FACE
        try:
            face = load_font(path, size)
            if weight:
                face.set_variation_by_axes([weight])
            return face
        except OSError:
            role = "number"
    if role == "number":
        path = PLUGIN_ROOT / "flight_radar/assets/fonts" / ("Inter-Bold.ttf" if bold else "Inter-SemiBold.ttf")
        try:
            return load_font(path, size)
        except OSError:
            pass
    if role == "bart":
        # BART's own typeface (bart.gov sets its pages in Hind), a Latin subset in assets/fonts.
        try:
            return load_font(ASSETS / "fonts" / ("Hind-Bold.ttf" if bold else "Hind-SemiBold.ttf"), size)
        except OSError:
            role = "number"
            return font(size, role, bold)
    if role == "road":
        path = PLUGIN_ROOT / "bay_traffic_cams/assets/fonts/RG2014D.ttf"
        try:
            for em in range(size, size * 2 + 1):
                face = load_font(path, em)
                box = face.getbbox("H")
                if box[3] - box[1] >= size:
                    return face
        except OSError:
            pass
    return get_base_ui_font(size, bold=bold)


class Page:
    def __init__(self, settings):
        theme = settings.get("_inkypi_theme") or {}
        self.mode = "night" if theme.get("mode", settings.get("themeMode")) == "night" else "day"
        self.palette = NIGHT if self.mode == "night" else DAY
        self.image = Image.new("RGB", (800, 480), self.palette["paper"])
        self.draw = ImageDraw.Draw(self.image)
        self.text_bounds = []

    def text(self, xy, text, size=13, color="ink", *, role="body", bold=True, width=None,
             align="left", min_size=None):
        value = str(text or "")
        if any(ord(char) >= 0x2e80 for char in value):
            role = "body"
        size = int(size)
        minimum = size if min_size is None else min_size
        while True:
            face = font(size, role, bold)
            length = self.draw.textlength(value, font=face)
            if width is None or length <= width or size <= minimum:
                break
            size -= 1
        if width is not None and length > width:
            # ASCII fonts need an ASCII ellipsis; Chinese remains on its complete UI face.
            suffix = "..." if role in {"road", "number"} else "…"
            while value and self.draw.textlength(value + suffix, font=face) > width:
                value = value[:-1]
            value += suffix
            length = self.draw.textlength(value, font=face)
        x, y = xy
        x -= length if align == "right" else length / 2 if align == "center" else 0
        self.draw.text((round(x), round(y)), value, font=face,
                       fill=self.palette.get(color, color), anchor="lt")
        self.text_bounds.append((value, self.draw.textbbox((round(x), round(y)), value, font=face, anchor="lt")))
        return length

    def rule(self, box, color="rule", width=1):
        self.draw.line(box, fill=self.palette[color], width=width)

    def logo(self, name, x, y, height):
        ink = ImageColor.getrgb(self.palette["ink"]) if self.mode == "night" else None
        art = source_logo(name, height, ink)
        if art is not None:
            self.image.paste(art, (x, y), art)
            art.close()

    def shield(self, route, x, y):
        # File names are an explicit list, never provider-controlled paths.
        if route not in {"I-680", "I-880", "SR-84"}:
            self.text((x, y + 10), route, 12, role="number", width=43)
            return
        with Image.open(ASSETS / f"{route}.png") as source:
            art = ImageOps.contain(source.convert("RGBA"), (36, 40), Image.Resampling.LANCZOS)
        self.image.paste(art, (x + (36 - art.width) // 2, y), art)
        art.close()


def header(page, now):
    p, d = page.palette, page.draw
    navy = "#143d59" if page.mode == "day" else "#dceefa"
    teal = "#087f8c" if page.mode == "day" else "#72cbd2"
    halo = "#f6fafc" if page.mode == "day" else "#314f61"
    d.line((16, 34, 54, 34), fill=navy, width=3)
    for x in (24, 46):
        d.line((x, 15, x, 38), fill=navy, width=2)
        d.line((x - 3, 17, x + 3, 17), fill=navy)
    d.line([(16, 23), (24, 18), (29, 23), (35, 26), (40, 23), (46, 18), (54, 23)], fill=teal, width=2)
    for x, y in ((28, 23), (31, 25), (35, 26), (39, 24), (42, 22)):
        d.line((x, y, x, 33), fill=navy)
    d.line([(16, 43), (21, 40), (26, 40), (31, 43), (36, 43), (41, 40), (46, 40), (51, 43), (55, 43)], fill=teal, width=2)
    x = 64
    face = font(27)
    for value, color in (("湾区", teal), ("交通观测", navy)):
        d.text((x, 12), value, font=face, fill=color, stroke_width=1, stroke_fill=halo, anchor="lt")
        x += d.textlength(value, font=face)
    page.text((65, 46), "BAY AREA / TRANSPORT + COASTAL", 9, "muted", role="number", bold=False)
    page.logo("caltrans", 525, 20, 18)
    page.text((527, 44), "DISTRICT 04", 10, "muted", role="number", bold=False)
    page.text((784, 16), now.strftime("%m.%d %H:%M"), 22, role="number", align="right")
    weekday = "星期" + "一二三四五六日"[now.weekday()]
    zone = now.tzname() or ""
    zh, en = font(12), font(12, "number", False)
    zw, ew = d.textlength(weekday, font=zh), d.textlength(zone, font=en)
    x = 784 - zw - 17 - ew
    d.text((x, 56), weekday, font=zh, fill=p["muted"], anchor="ls")
    d.line((round(x + zw + 8), 46, round(x + zw + 8), 55), fill=p["muted"])
    d.text((x + zw + 17, 56), zone, font=en, fill=p["muted"], anchor="ls")
    page.rule((16, 65, 784, 65), "ink", 2)


def sign_plate(page, sign, box=SIGN_BOX):
    """One of Caltrans' travel-time signs near home laid out as the sign shows it, straight on the
    page with no plate behind it: the route's shield, its direction and the message's time at the
    left, then each destination with its minutes aligned at the right. Nothing without a current
    sign."""
    if not sign or not sign.get("lines"):
        return
    left, top, right, bottom = box
    d, p = page.draw, page.palette
    strip = left + 44
    route = str(sign.get("route") or "")
    if route in SIGN_SHIELDS:
        with Image.open(ASSETS / f"{route}.png") as source:
            art = ImageOps.contain(source.convert("RGBA"), (26, 24), Image.Resampling.LANCZOS)
        page.image.paste(art, (round(left + (strip - left - art.width) / 2), top + 4), art)
        art.close()
    else:
        page.text(((left + strip) / 2, top + 9), route.replace("SR-", "").replace("US-", ""), 11, "ink",
                  role="number", align="center", width=40)
    direction = DIRECTIONS.get(sign.get("direction"), "")
    if direction:
        page.text(((left + strip) / 2, top + 30), direction, 9, "ink", align="center", width=40)
    shown = parse_time(sign.get("time"))
    if shown:
        page.text(((left + strip) / 2, top + 41), shown.astimezone(PACIFIC).strftime("%H:%M"), 8, "muted",
                  role="number", bold=False, align="center", width=40)
    d.line((strip, top + 6, strip, bottom - 6), fill=p["muted"], width=1)
    lines = sign["lines"][:3]
    pitch = (bottom - top - 8) / 3
    for index, line in enumerate(lines):
        y = round(top + 5 + index * pitch + (pitch - 11) / 2)
        minutes = f"{line['minutes']} MIN"
        width = page.text((right - 9, y), minutes, 10, "ink", role="road", align="right")
        page.text((strip + 9, y), line["to"], 10, "ink", role="road", width=right - 9 - width - 12 - strip - 9)


def section_titles(page):
    face = font(18)
    halo = "#f4f8fb" if page.mode == "day" else "#2b4858"
    for x, parts in ((16, (("道路", "ink"), ("封闭", "orange"))),
                     (551, (("潮汐", "blue"), ("与海况", "ink")))):
        for value, color in parts:
            page.draw.text((x, 78), value, font=face, fill=page.palette[color],
                           stroke_width=1, stroke_fill=halo, anchor="lt")
            x += page.draw.textlength(value, font=face)
    for name, x in (("barricade", 100), ("waves", 652)):
        with Image.open(ASSETS / "details" / f"{name}-{page.mode}.png") as source:
            art = source.convert("RGBA")
        page.image.paste(art, (x, 77), art)
        art.close()


def _place_title(page, row, y):
    title = str(row.get("place") or row.get("begin") or "道路位置未报")
    mark = PLACE_MARKS.get(title.casefold())
    reserved = 41 if mark and mark[1] == 31 else 27 if mark else 0
    width = page.text((276, y + 1), title, 18, role="road", width=184 - reserved, min_size=13)
    if mark:
        filename, max_width, county = mark
        with Image.open(ASSETS / "places" / filename) as source:
            art = source.convert("RGBA")
            if art.getbbox():
                art = art.crop(art.getbbox())
            art = ImageOps.contain(art, (max_width, 18), Image.Resampling.LANCZOS)
        x = round(276 + width + 7)
        page.image.paste(art, (x, y + 2), art)
        if county:
            page.text((x + art.width + 4, y + 8), county, 8, "muted", role="number", bold=False,
                      width=max(0, 460 - x - art.width - 4))
        art.close()


def _camera(page, index, row, entries):
    entry = entries.get(row["id"]) or {}
    image = entry.get("image")
    x, y, w, h = 370, 155 + index * 112, 112, 54
    if image is not None:
        # Caltrans' bottom caption is replaced by an explicit acquisition timestamp.
        cropped = image.crop((0, 0, image.width, round(image.height * (1 - 20 / 260))))
        art = ImageOps.fit(cropped, (w, h), Image.Resampling.LANCZOS)
        cropped.close()
        page.image.paste(art, (x, y))
        art.close()
        distance = entry.get("distance_km", 0)
        tag = f"附近 {distance * 1000:.0f} m" if distance < .1 else f"附近 {distance:.2f} km"
        if entry.get("stale"):
            tag = "缓存 · " + tag
        tw = page.draw.textlength(tag, font=font(8, bold=False))
        page.draw.rectangle((x + 1, y + 1, min(x + w - 2, x + tw + 7), y + 12), fill="#152839")
        page.text((x + 4, y + 2), tag, 8, "#ffffff", bold=False, width=w - 8)
        stamp = parse_time(entry.get("fetched"))
        label = "取 " + stamp.astimezone(PACIFIC).strftime("%m/%d %H:%M") if stamp else "获取时间未报"
    else:
        with Image.open(ASSETS / "details/camera-unavailable-illustration.png") as source:
            art = ImageOps.fit(source.convert("RGB"), (w, h), Image.Resampling.LANCZOS)
        page.image.paste(art, (x, y))
        art.close()
        page.draw.rectangle((x + 1, y + 1, x + 26, y + 13), fill="#152839")
        page.text((x + 4, y + 2), "示意", 9, "#ffffff", bold=False)
        page.draw.rectangle((x + 1, y + h - 17, x + w - 2, y + h - 2), fill="#152839")
        page.text((x + w / 2, y + h - 14), "镜头暂不可用" if entry.get("status") == "unavailable" else "暂无匹配画面",
                  10, "#ffffff", align="center")
        label = "同路向 2 km 内"
    page.draw.rectangle((x, y, x + w - 1, y + h - 1), outline=page.palette["rule"])
    page.text((x + w, y + 57), label, 9, "muted", align="right", bold=False, width=w)


def roads(page, payload, now):
    source = payload["roads"]
    ranked = rank_roads(source.get("data", {}).get("items", []), now)
    rows = select_roads(ranked, now, 3)
    title = f"重点 {len(rows)} 项 / 共 {len(ranked)} 项"
    if source.get("state") == "stale_cache":
        title = "缓存 · " + title
    page.text((482, 83), title, 10, "muted", align="right", width=186)
    raster = render_map(rows, size=(200, 320), night=page.mode == "night")
    page.image.paste(raster, (16, 111))
    page.image.info["bay_map_markers"] = raster.info.get("markers", [])
    raster.close()
    page.text((16, 438), "编号对应右侧道路条目", 10, "muted", bold=False)
    if not rows:
        text = ("道路数据暂不可用" if source.get("state") == "unavailable" else
                "过期缓存中暂无匹配项目" if source.get("state") == "stale_cache" else "所选路线暂无封闭记录")
        page.text((232, 144), text, 17, width=250)
        page.text((232, 184), " / ".join(payload["routes"]), 13, "muted", width=250)
        page.text((232, 216), f"关注未来 {payload.get('window_days', 7)} 天", 13, "muted", width=250)
    labels = {"active": "施工中", "scheduled_now": "计划时段中", "planned": "即将开始"}
    for index, row in enumerate(rows):
        y = 111 + index * 112
        page.shield(row["route"], 230, y)
        _place_title(page, row, y)
        draw_marker(page.draw, 474, y + 11, index + 1, 8, font(11, "number"))
        state = road_state(row, now)
        color = "orange" if state == "active" else "planned"
        label = labels[state]
        width = round(page.draw.textlength(label, font=font(11))) + 30
        page.draw.rectangle((276, y + 27, 276 + width, y + 46),
                            fill=page.palette["soft" if state == "active" else "pale"],
                            outline=page.palette[color])
        icon = draw_icon("active" if state == "active" else "scheduled", 14, page.mode)
        page.image.paste(icon, (281, y + 30), icon)
        icon.close()
        page.text((299, y + 31), label, 11, color)
        draw_direction(page, row.get("direction"), right=482, top=y + 25,
                       interstate=str(row.get("route") or "").startswith("I-"))
        kind = KINDS.get(row.get("kind"), row.get("kind") or "施工")
        facility = FACILITIES.get(row.get("facility"), "")
        if row.get("kind") == "Full" and "匝道" in facility:
            kind = facility + "全封闭"
        symbol = ("entrance_closed" if row.get("facility") == "On Ramp" else
                  "exit_closed" if row.get("facility") == "Off Ramp" else
                  "alternating_traffic" if row.get("kind") == "One-Way Traffic" else
                  "barricade" if row.get("kind") == "Full" else "cone")
        icon = draw_icon(symbol, 16, page.mode)
        page.image.paste(icon, (232, y + 53), icon)
        icon.close()
        page.text((253, y + 54), kind, 13, width=111)
        start, end = parse_time(row.get("start")), parse_time(row.get("end"))
        start_label = start.astimezone(PACIFIC).strftime("%m/%d %H:%M") if start else "未报"
        end_label = "时间未定" if row.get("indefinite") or not end else end.astimezone(PACIFIC).strftime("%m/%d %H:%M")
        for offset, label, value in ((77, "开始", start_label), (96, "结束", end_label)):
            page.text((232, y + offset), label, 9, "muted", bold=False)
            page.text((263, y + offset - 1), value, 11, "orange" if label == "结束" and state == "active" else "ink",
                      role="number", bold=False, width=102)
        _camera(page, index, row, payload.get("nearby_cameras") or {})
        if index < len(rows) - 1:
            page.rule((230, y + 109, 482, y + 109))


def _chart_label(page, x, y, text, high):
    width = page.draw.textlength(text, font=font(10, "number"))
    xx = max(538, min(780 - width, x - width / 2))
    yy = y - 17 if high else y + 6
    if yy < CHART[1] + 1:
        xx, yy = min(780 - width, x + 8), y - 7
    if yy + 12 > CHART[3] - 2:
        xx, yy = min(780 - width, x + 7), y - 13
    page.draw.rounded_rectangle((xx - 2, yy - 1, xx + width + 2, yy + 12), radius=3, fill=page.palette["paper"])
    page.text((xx, yy), text, 10, "blue", role="number")


def level_readout(page, water, rising):
    """The tide now as the station measures it, with how far it stands from the prediction as a
    chip ("+0.4 ft 高于预测"): orange above, blue below, outlined when it is as predicted."""
    page.text((512, READOUT_Y), "实测此刻", 11, "muted")
    width = page.text((575, READOUT_Y - 3), f"{water['height']:.1f} ft", 16, "blue", role="display")
    delta = water["delta"]
    even = abs(delta) < LEVEL_EVEN_FT
    label = "与预测一致" if even else f"{delta:+.1f} ft {'高于' if delta > 0 else '低于'}预测"
    chip_w = page.draw.textlength(label, font=font(10)) + 12
    x = round(575 + width + 8)
    tone = "blue" if even or delta < 0 else "orange"
    box = (x, READOUT_Y - 1, x + chip_w, READOUT_Y + 14)
    if even:
        page.draw.rounded_rectangle(box, radius=7, outline=page.palette[tone], width=1)
        page.text((x + chip_w / 2, READOUT_Y), label, 10, tone, align="center")
    else:
        page.draw.rounded_rectangle(box, radius=7, fill=page.palette[tone])
        page.text((x + chip_w / 2, READOUT_Y), label, 10, "paper", align="center")
    page.text((784, READOUT_Y), "涨潮 ↑" if rising else "退潮 ↓", 11, "blue", align="right")


def tide_chart(page, data, now, water=None):
    begin = datetime.combine(now.date(), time.min, PACIFIC)
    end = datetime.combine(now.date() + timedelta(days=1), time.min, PACIFIC)
    rows = [row for row in data.get("curve", []) if begin <= parse_time(row["time"]) < end]
    if len(rows) < 4:
        page.text((512, (CHART[1] + CHART[3]) / 2 - 8), "今日预测曲线不可用", 15, "muted", width=272)
        return
    left, top, right, bottom = CHART
    low = math.floor(min(row["height"] for row in rows) / 2) * 2
    high = max(low + 2, math.ceil(max(row["height"] for row in rows) / 2) * 2)
    span = end.timestamp() - begin.timestamp()

    def xy(row):
        return (left + (parse_time(row["time"]).timestamp() - begin.timestamp()) / span * (right - left),
                bottom - (row["height"] - low) / (high - low) * (bottom - top))

    points = [xy(row) for row in rows]
    draw_water(page, points, bottom)
    for height in range(low, high + 1, 2):
        y = bottom - (height - low) / (high - low) * (bottom - top)
        page.text((528, y - 5), str(height), 10, "muted", role="number", align="right", bold=False)
        for x in range(left, right, 4):
            page.draw.point((x, round(y)), fill=page.palette["rule"])
    page.draw.line(points, fill=page.palette["blue"], width=3)
    for row in data.get("extremes", []):
        if begin <= parse_time(row["time"]) < end:
            x, y = xy(row)
            # A malformed/out-of-range extreme cannot escape the plot into the readouts.
            y = max(top, min(bottom, y))
            page.draw.ellipse((x - 3, y - 3, x + 3, y + 3), fill=page.palette["blue"])
            _chart_label(page, x, y, parse_time(row["time"]).astimezone(PACIFIC).strftime("%H:%M"), row["type"] == "H")
    # The tide now reads off the whole prediction, so it holds past today's last sample at midnight.
    found = tide_now(data.get("curve", []), now)
    if found and water:
        level_readout(page, water, found[1])
    elif found:
        height, rising = found
        page.text((512, READOUT_Y), "预测此刻", 11, "muted")
        page.text((575, READOUT_Y - 3), f"{height:.1f} ft", 16, "blue", role="display")
        page.text((784, READOUT_Y), "涨潮 ↑" if rising else "退潮 ↓", 11, "blue", align="right")
    else:
        heights = [row["height"] for row in rows]
        page.text((512, READOUT_Y), "今日潮位", 11, "muted")
        page.text((575, READOUT_Y - 3), f"{min(heights):.1f} – {max(heights):.1f} ft", 16, "blue", role="display")
    if found:
        height, _rising = found
        x, y = xy({"time": now.isoformat(), "height": height})
        for yy in range(top, bottom, 4):
            page.draw.point((round(x), yy), fill=page.palette["muted"])
        paste_sticker(page, "boat", (max(left + 10, min(right - 10, x)), max(top + 10, y - 9)), 22)
        tag = now.strftime("%H:%M")
        width = page.draw.textlength(tag, font=font(9, "number"))
        tx = max(left + 3, min(right - width - 3, x - width / 2))
        ty = min(bottom - 12, y + 9)
        page.draw.rounded_rectangle((tx - 3, ty - 1, tx + width + 3, ty + 11), radius=3, fill=page.palette["paper"])
        page.text((tx, ty), tag, 9, "blue", role="number")
    measured = parse_time((water or {}).get("time"))
    if measured and begin <= measured < end:
        # The station's reading as an orange ring where the water actually stood, off the curve
        # by however far it runs from the prediction.
        mx, my = xy({"time": measured.isoformat(), "height": water["height"]})
        my = max(top, min(bottom, my))
        page.draw.ellipse((mx - 4, my - 4, mx + 4, my + 4), fill=page.palette["paper"],
                          outline=page.palette["orange"], width=2)
    page.rule((left, top, left, bottom, right, bottom), "ink")
    for hour in (0, 6, 12, 18, 24):
        stamp = end if hour == 24 else datetime.combine(now.date(), time(hour), PACIFIC)
        x = left + (stamp.timestamp() - begin.timestamp()) / span * (right - left)
        page.text((x, bottom + 4), f"{hour:02d}", 10, "muted", role="number", align="center", bold=False)


def tides(page, payload, settings, now):
    page.logo("noaa", 512, 78, 28)
    station = str(payload["station"])
    name = str(settings.get("stationName") or ("Redwood City" if station == "9414523" else "NOAA"))
    page.text((512, 112), name.upper() + " · " + station, 10, "muted", role="number", bold=False, width=272)
    data = payload["tides"].get("data", {})
    upcoming = next_tides(data, now)
    for key, x, title in (("H", 512, "下一次高潮"), ("L", 652, "下一次低潮")):
        page.text((x, NEXT_TITLE_Y), title, 12, "muted")
        row = upcoming[key]
        right = x + (124 if key == "H" else 132)
        if row:
            stamp = parse_time(row["time"]).astimezone(PACIFIC)
            page.text((right, NEXT_TITLE_Y + 2), stamp.strftime("%m/%d"), 10, "muted", role="number", align="right",
                      bold=False)
            # The time, and its height beside it on the same baseline.
            width = page.text((x, NEXT_VALUE_Y), stamp.strftime("%H:%M"), 27, "blue", role="display", width=86,
                              min_size=23)
            value_w = page.text((x + width + 7, NEXT_VALUE_Y + 9), f"{row['height']:.1f}", 17, "blue", role="display")
            page.text((x + width + 9 + value_w, NEXT_VALUE_Y + 15), "ft", 10, "muted", role="number", bold=False)
        else:
            page.text((x, NEXT_VALUE_Y + 4), "暂无预测", 16, "muted")
    page.rule((642, NEXT_TITLE_Y, 642, NEXT_VALUE_Y + 30))
    page.text((512, CHART_HEADER_Y), now.strftime("%m/%d") + " · 24H 预测曲线", 10, "muted", bold=False)
    water = payload.get("water")
    page.text((784, CHART_HEADER_Y), "MLLW / ft", 10, "muted", role="number", align="right", bold=False)
    if water:
        # The ring's key, left of the datum: the station's measured reading.
        key_x = 784 - page.draw.textlength("MLLW / ft", font=font(10, "number", False)) - 12
        page.text((key_x, CHART_HEADER_Y), "实测", 10, "muted", align="right", bold=False)
        ring_x = key_x - page.draw.textlength("实测", font=font(10, bold=False)) - 8
        page.draw.ellipse((ring_x - 4, CHART_HEADER_Y + 2, ring_x + 4, CHART_HEADER_Y + 10),
                          fill=page.palette["paper"], outline=page.palette["orange"], width=2)
    tide_chart(page, data, now, water)
    buoy = payload.get("buoy") or {}
    measured = buoy.get("data") or {}
    page.rule((512, BUOY_Y - 7, 784, BUOY_Y - 7))
    bart(page, payload, now)
    if buoy.get("state") in {None, "unavailable"} or not measured:
        page.text((512, BUOY_Y), "浮标 46026 · 海况暂不可用", 10, "muted", width=272, bold=False)
        return
    page.text((512, BUOY_Y), "浮标缓存" if buoy.get("state") == "stale_cache" else "浮标 46026", 10, "muted", bold=False)
    if measured.get("wave_ft") is not None:
        page.text((592, BUOY_Y - 2), f"浪 {measured['wave_ft']:.1f} ft", 12, "blue", width=90)
    parts = ([f"{measured['period_s']:.0f} 秒"] if measured.get("period_s") else [])
    if measured.get("swell_dir") in COMPASS:
        parts.append(COMPASS[measured["swell_dir"]])
    page.text((784, BUOY_Y), " · ".join(parts), 10, "muted", align="right", width=99, bold=False)


ADVISORY_KINDS = {"DELAY": "延误", "EMERGENCY": "紧急", "MAINTENANCE": "施工"}
WEEKDAY_SHORT = {"Monday": "Mon", "Tuesday": "Tue", "Wednesday": "Wed", "Thursday": "Thu", "Friday": "Fri",
                 "Saturday": "Sat", "Sunday": "Sun"}


def compact_advisory(text):
    """BART's advisory a little shorter for one or two lines: weekdays abbreviated, ranges with a dash."""
    for day, short in WEEKDAY_SHORT.items():
        text = text.replace(day, short)
    return text.replace("-minute", "-min").replace(" through ", " thru ")


def wrap_latin(page, text, size, width, lines):
    """``text`` broken at spaces into at most ``lines`` lines of ``width`` in BART's face, the last
    ending in an ellipsis when the text runs on."""
    face = font(size, "bart", False)
    words, result = str(text).split(), []
    while words and len(result) < lines:
        line = words.pop(0)
        while words and page.draw.textlength(line + " " + words[0], font=face) <= width:
            line += " " + words.pop(0)
        result.append(line)
    if words and result:
        last = result[-1]
        while last and page.draw.textlength(last + "...", font=face) > width:
            last = last[:-1]
        result[-1] = last.rstrip(" ,;") + "..."
    return result


def _note_x(page, label):
    """Where a note's text starts: after its chip, or after the quiet line's dot."""
    return round(512 + page.draw.textlength(label, font=font(9)) + 10 + 6) if label else 526


def bart_notes(page, payload):
    """BART's service notes for the foot of its block, each (chip label, chip colour, text lines):
    the newest advisory over up to two lines, elevators out of service; one quiet line when there
    is neither."""
    status = (payload.get("bart_status") or {})
    data = status.get("data") or {}
    if status.get("state") in (None, "unavailable") or not data:
        return []
    notes = []
    advisories = data.get("advisories") or []
    if advisories:
        first = advisories[0]
        label = ADVISORY_KINDS.get(first.get("type"), "公告") + (f" {len(advisories)}" if len(advisories) > 1 else "")
        notes.append((label, "crimson", wrap_latin(page, compact_advisory(first["text"]), 10,
                                                   784 - _note_x(page, label), ADVISORY_LINES)))
    elevators = data.get("elevators") or {}
    if elevators.get("out"):
        stations = ", ".join(elevators.get("stations") or [])
        notes.append(("电梯", "orange", [f"{elevators['out']} 部停用" + (f" · {stations}" if stations else "")]))
    if not notes:
        quiet = "全线无延误公告" + (" · 电梯全部运行" if elevators.get("out") == 0 else "")
        notes.append(("", "emerald", [quiet]))
    return notes


def draw_bart_notes(page, notes, top):
    """The notes from ``top`` down: a chip in its colour (a dot for the quiet line), then the text."""
    y = top
    for label, colour, lines in notes:
        x = _note_x(page, label)
        if label:
            page.draw.rounded_rectangle((512, y, x - 6, y + 12), radius=6, fill=page.palette[colour])
            page.text(((512 + x - 6) / 2, y), label, 9, "paper", align="center")
        else:
            page.draw.ellipse((514, y + 3, 520, y + 9), fill=page.palette[colour])
        for line in lines:
            page.text((x, y), line, 10, role="bart", bold=False, width=784 - x)
            y += NOTE_STEP
    return y


def bart(page, payload, now):
    """BART's next trains from the chosen station: its logo and name, then each destination with
    its line's colour, its next train drawn as long as it runs, and its next departures as clock
    times; trains in service at the right of the station and BART's service notes (advisories,
    elevators) under the trains."""
    source = payload.get("bart") or {}
    data = source.get("data") or {}
    page.rule((512, BART_Y - 6, 784, BART_Y - 6))
    page.logo("bart", 512, BART_Y - 1, 14)
    lines = coming_trains(data, now)[:BART_ROWS]
    # How many of the shown trains run late, in red, in place of 实时发车 (缓存 for a kept reading);
    # before it, how many trains BART has in service.
    late = sum(delay >= BART_LATE_SECONDS for line in lines for delay in (line.get("delays") or [])[:3])
    note = "缓存" if source.get("state") == "stale_cache" else f"{late} 班晚点" if late else "实时发车"
    note_w = page.text((784, BART_Y + 1), note, 10, "red" if late and note != "缓存" else "muted", align="right",
                       bold=False)
    trains = ((payload.get("bart_status") or {}).get("data") or {}).get("trains")
    fleet_w = 0
    if trains:
        fleet_w = page.text((784 - note_w - 8, BART_Y + 1), f"在线 {trains} 列 ·", 10, "muted", align="right",
                            bold=False) + 8
    # "Warm Springs/South Fremont" reads as its first name, set like BART sets it, after its stop
    # ring in the colours of the lines that call there.
    name = str(data.get("station") or "BART").split("/")[0].strip()
    colours = list(dict.fromkeys(line["colour"] for line in data.get("lines") or [] if line.get("colour")))
    name_x = 538 + (station_ring(page, 538, BART_Y + 4, colours) + 4 if colours else 0)
    page.text((name_x, BART_Y - 1), name, 13, role="bart", width=min(170, 784 - note_w - fleet_w - 8 - name_x))
    notes = bart_notes(page, payload)
    notes_h = sum(len(note_lines) for _label, _colour, note_lines in notes) * NOTE_STEP
    band_top, band_bottom = BART_Y + 14, BART_BOTTOM - notes_h - (3 if notes else 0)
    lines = lines[:max(1, int((band_bottom - band_top) // BART_ROW_STEP))]
    draw_bart_notes(page, notes, band_bottom + 3)
    if not lines:
        page.text((512, BART_Y + 22), "暂无发车信息" if data else "BART 发车暂不可用", 10, "muted", bold=False)
        return
    # One destination a row, in BART's typeface: the line's colour, where it goes, its next train
    # and its next times.
    # The rows share the column's height under the station evenly, however many trains are running,
    # and every train on the page one scale, set by the tightest row.
    step = (band_bottom - band_top) / len(lines)
    plans = [row_plan(page, line) for line in lines]
    shape = train_shape_for(plans, step)
    for index, (line, plan) in enumerate(zip(lines, plans)):
        y = round(band_top + index * step + (step - BART_ROW_STEP) / 2)
        # The bullet and the train (or the leader and its stop ring) are all level with the middle of
        # the row's capitals and figures (Hind's are 8 px tall at this size).
        middle = y + 4
        # The line's lettered bullet, as BART prints it (its colours stay by night); a dot in the
        # line's colour for one without (the airport shuttle).
        bullet = source_logo(f"bart-{line['line']}", 12) if line.get("line") else None
        if bullet is not None:
            page.image.paste(bullet, (512, middle - bullet.height // 2), bullet)
            bullet.close()
        else:
            page.draw.ellipse((514, middle - 4, 522, middle + 4), fill=line["colour"])
        parts, times_w = plan["parts"], plan["times_w"]
        delays = list(line.get("delays") or []) + [0] * len(parts)
        # The next trains as one right-aligned run, each in blue, a late one in red, parted by dots
        # on the row's middle (Hind's own middle dot sits at its x-height, above the figures').
        x = 784 - times_w
        gap = page.draw.textlength(" · ", font=font(12, "bart"))
        for part_index, (part, delay) in enumerate(zip(parts, delays)):
            if part_index:
                dot = x + gap / 2
                page.draw.ellipse((dot - 1, middle - 1, dot + 1, middle + 1), fill=page.palette["blue"])
                x += gap
            x += page.text((x, y), part, 12, "red" if delay >= BART_LATE_SECONDS else "blue", role="bart")
        # The next train's length after where it goes, as BART's platform signs give it, and that
        # train, its nose just short of the times, its rail back to the label; the destination gives
        # way to it.
        cars = plan["cars"]
        room = train_room(plan, shape) if cars else 272 - 31 - times_w
        name_w = page.text((529, y), fitted_destination(page, line["destination"], room), 12, role="bart",
                           width=room, bold=False)
        if cars:
            page.text((529 + name_w + 4, y - 1), f"{cars}节", 10, "muted", bold=False)
            draw_train(page, round(529 + name_w + plan["cars_w"] + 5), plan["front"], middle, cars,
                       train_colour(line), shape)
            continue
        # Without its length the destination leads to its trains along its line, drawn as BART's
        # map draws a line: a stripe in the line's colour into a stop ring before the times.
        start, stop = 529 + name_w + 6, 784 - times_w - 7
        if stop - start >= BART_LEADER_MIN:
            page.draw.line((start, middle, stop - 3, middle), fill=line["colour"], width=2)
            page.draw.ellipse((stop - 4, middle - 3, stop + 2, middle + 3), fill=page.palette["paper"],
                              outline=line["colour"], width=2)


def train_cars(line):
    """The next train's cars when the reading gives a length BART runs (one to ten), else None."""
    cars = line.get("cars")
    return cars if type(cars) is int and 1 <= cars <= TRAIN_MAX_CARS else None


def train_colour(line):
    """The train's stripe: BART's own colour for the line its next train runs on (a destination two
    lines serve mixes them), else the destination's line (a reading kept from before each train's
    line was), else the feed's colour."""
    for name in (line.get("next_line"), line.get("line")):
        if isinstance(name, str) and name in BART_LINE_COLOURS:
            return BART_LINE_COLOURS[name]
    return line["colour"]


def short_destination(destination):
    """A destination's first name ("Dublin/Pleasanton" reads "Dublin"); the whole without one."""
    return destination.split("/")[0].strip() or destination


def fitted_destination(page, destination, width):
    """The destination as BART names it, or in short when the whole does not fit ``width``; past
    that the row's text cuts it short."""
    if page.draw.textlength(destination, font=font(12, "bart", False)) > width:
        return short_destination(destination)
    return destination


def row_plan(page, line):
    """What a row needs measured before any is drawn: its times and their width, where a train's
    nose stops short of them, the next train's cars and its label's width, and its destination's
    width whole and in short."""
    parts = [stamp.astimezone(PACIFIC).strftime("%H:%M") for stamp in line["departures"][:3]]
    times_w = page.draw.textlength(" · ".join(parts), font=font(12, "bart"))
    cars = train_cars(line)
    destination, face = str(line["destination"]), font(12, "bart", False)
    return {"parts": parts, "times_w": times_w, "front": round(784 - times_w - 6), "cars": cars,
            "cars_w": page.draw.textlength(f"{cars}节", font=font(10, bold=False)) + 4 if cars else 0,
            "full_w": page.draw.textlength(destination, font=face),
            "short_w": page.draw.textlength(short_destination(destination), font=face)}


def car_rows(height):
    """A car's rows from its roof down for a body ``height`` px tall (8 to 14): the roof, the window
    band, the belt under it, the line's stripe (4 px from 10 px tall, 5 at 14), the underframe; the
    ink in 2 px bands from 12 px tall."""
    stripe = 5 if height >= 14 else 4 if height >= 10 else 3
    belt = frame = 2 if height >= 12 else 1
    roof = 2 if height >= 9 else 1
    window = height - roof - belt - stripe - frame
    return ("roof",) * roof + ("window",) * window + ("belt",) * belt + ("stripe",) * stripe + ("frame",) * frame


def train_shape(car_w, tallest=TRAIN_HEIGHTS[12]):
    """A train's shape for cars ``car_w`` wide: (car width, the gap between cars, the car's rows
    from its roof down), its body as tall as that width has it but no taller than ``tallest``."""
    height = max(TRAIN_HEIGHTS[CAR_FLOOR], min(tallest, TRAIN_HEIGHTS[car_w]))
    return car_w, 2 if car_w >= 9 else 1, car_rows(height)


def train_width(cars, shape):
    """How wide a train of ``cars`` cars is drawn in ``shape``, its nose (as long as the rows over
    its stripe, the slope stepping a pixel a row) included."""
    car_w, gap, rows = shape
    return cars * (car_w + gap) - gap + rows.index("stripe")


def train_room(plan, shape):
    """The room a row's destination keeps beside its train in ``shape``, with the train's lead."""
    return plan["front"] + 1 - train_width(plan["cars"], shape) - TRAIN_LEAD - 529 - plan["cars_w"]


def train_shape_for(plans, step):
    """The one shape every train on the page is drawn in: the widest car of CAR_WIDTHS with which
    every row keeps its destination whole. When even the narrowest leaves a row too little, the
    destinations give way for it (in short, then cut to no less than TRAIN_NAME_MIN), and only past
    that do the cars narrow, down to CAR_FLOOR. Its body is no taller than the rows' ``step``
    leaves room for over the rail."""
    tallest = max(TRAIN_HEIGHTS[CAR_FLOOR], int(step) - 5)
    trains = [plan for plan in plans if plan["cars"]]

    def fits(car_w, need):
        shape = train_shape(car_w, tallest)
        return all(train_room(plan, shape) >= need(plan) for plan in trains)

    for car_w in CAR_WIDTHS:
        if fits(car_w, lambda plan: plan["full_w"]):
            return train_shape(car_w, tallest)
    for car_w in range(CAR_WIDTHS[-1], CAR_FLOOR, -1):
        if fits(car_w, lambda plan: min(plan["short_w"], TRAIN_NAME_MIN)):
            return train_shape(car_w, tallest)
    return train_shape(CAR_FLOOR, tallest)


def draw_train(page, start, front, middle, cars, colour, shape):
    """A BART train in side view heading right in ``shape``, the tip of its nose at x ``front``, its
    body centred on ``middle`` (the row's capitals): each car a solid block of ink with a window
    band of paper and a stripe in the line's ``colour`` under it, its roof's corners rounded, a gap
    between cars; the front car's roof and windscreen sloping down into its nose. Two speed streaks
    behind it and a 2 px rail under it, both from no further back than ``start``. Square on the
    pixel grid, the ink pure black by day, so the panel's dither leaves the train's edges clean."""
    car_w, gap, rows = shape
    draw, paper = page.draw, page.palette["paper"]
    ink = "#000000" if page.mode == "day" else page.palette["ink"]
    height, nose, roof = len(rows), rows.index("stripe"), rows.count("roof")
    pillar = 2 if car_w >= 7 else 1
    top = middle - height // 2
    tail = front - train_width(cars, shape) + 1
    for index in range(cars):
        cab = index == cars - 1
        left = tail + index * (car_w + gap)
        right = left + car_w - 1 + (nose if cab else 0)
        for row, part in enumerate(rows):
            # The cab steps down a pixel a row over its nose; the roof's corners round off over its
            # rows, the nose's following the slope.
            edge = right - max(0, nose - row) if cab else right
            inset = roof - row if part == "roof" else 0
            draw.line((left + inset, top + row, edge - (0 if cab else inset), top + row),
                      fill=colour if part == "stripe" else ink)
            if part == "window" and edge - pillar >= left + pillar:
                draw.line((left + pillar, top + row, edge - pillar, top + row), fill=paper)
    # The streaks at the windows and the stripe, a car and a car and a half long, clear of the last
    # car by 3 px and shortened when the destination leaves less room; the rail to past the nose.
    windows = top + rows.index("window") + (rows.count("window") - 2) // 2
    stripe = top + nose + (rows.count("stripe") - 2) // 2
    for row, length in ((windows, car_w), (stripe, car_w + car_w // 2 + 2)):
        begin = max(start, tail - 3 - length)
        if tail - 4 - begin >= 1:
            draw.rectangle((begin, row, tail - 4, row + 1), fill=ink)
    draw.rectangle((min(start, tail), top + height + 1, front + 2, top + height + 2), fill=ink)


def station_ring(page, x, middle, colours):
    """A station's mark as BART's map marks a stop, at x and centred on ``middle``: a ring on the
    paper whose outline the lines that call there share evenly, clockwise from the top (one line,
    one colour all round), drawn at four times the size for round arcs; returns its width."""
    scale, size = 4, STATION_RING
    ring = Image.new("RGBA", (size * scale, size * scale), (0, 0, 0, 0))
    draw = ImageDraw.Draw(ring)
    box = (0, 0, size * scale - 1, size * scale - 1)
    draw.ellipse(box, fill=page.palette["paper"])
    sweep = 360 / len(colours)
    for index, colour in enumerate(colours):
        start = -90 + index * sweep
        draw.arc(box, start, start + sweep, fill=colour, width=3 * scale)
    ring = ring.resize((size, size), Image.Resampling.LANCZOS)
    page.image.paste(ring, (x, round(middle - size / 2)), ring)
    return size


def footer(page, payload, now):
    page.rule((16, 455, 784, 455), "ink")
    roads = payload["roads"]
    recorded = parse_time(roads.get("data", {}).get("source_updated_at"))
    fetched = parse_time(roads.get("fetched_at"))
    if recorded and fetched:
        label = ("道路缓存 " if roads["state"] == "stale_cache" else "道路来源 ")
        label += recorded.astimezone(PACIFIC).strftime("%m/%d %H:%M") + " · 获取 " + fetched.astimezone(PACIFIC).strftime("%H:%M")
    else:
        label = "道路 " + source_label(roads, now)
    page.text((16, 464), label, 10, "muted", bold=False, width=470)
    tides_source = payload["tides"]
    prediction = tides_source.get("data", {}).get("prediction_date", "—").replace("-", "/")[-5:]
    fetched = parse_time(tides_source.get("fetched_at"))
    stamp = fetched.astimezone(PACIFIC).strftime("%m/%d %H:%M") if fetched else "—"
    prefix = "NOAA 缓存 " if tides_source["state"] == "stale_cache" else "NOAA 预测 "
    if tides_source["state"] == "unavailable":
        prefix = "潮汐暂不可用 "
    page.text((784, 464), prefix + prediction + " · 获取 " + stamp + " " + (now.tzname() or ""),
              10, "muted", align="right", width=280, bold=False)


def render_page(dimensions, payload, settings, now):
    now = local_now(now)
    page = Page(settings)
    header(page, now)
    sign_plate(page, payload.get("sign"))
    page.rule((497, 78, 497, 444))
    roads(page, payload, now)
    tides(page, payload, settings, now)
    section_titles(page)
    footer(page, payload, now)
    page.image.info["bay_text_bounds"] = page.text_bounds
    result = page.image
    if tuple(dimensions) != (800, 480):
        result = result.resize(tuple(dimensions), Image.Resampling.LANCZOS)
        page.image.close()
    return result
