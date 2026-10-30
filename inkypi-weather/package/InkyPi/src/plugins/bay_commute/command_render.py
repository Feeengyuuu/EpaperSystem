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
from .render import ASSETS, FACILITIES, KINDS, draw_water, paste_sticker, source_label, tide_now
from .direction_plaques import draw_direction
from .sources import (
    COMPASS, PACIFIC, coming_trains, local_now, next_tides, parse_time, rank_roads, road_state, select_roads,
)
from .traffic_icons import draw_icon


DAY = dict(paper="#ffffff", ink="#152839", muted="#43586a", rule="#b4bdc6", blue="#005b9c",
           pale="#eaf2f8", orange="#bd450b", soft="#faf0e8", planned="#596779", chart="#dcebf5",
           water_mid="#b9daea", water_deep="#8fc2dc", foam="#ffffff")
NIGHT = dict(paper="#080e14", ink="#eef4f9", muted="#b3c5d2", rule="#465562", blue="#7cc9fa",
             pale="#102538", orange="#ffb67c", soft="#301e14", planned="#bac8d6", chart="#102a3d",
             water_mid="#1f4a66", water_deep="#275a7c", foam="#cfe8f7")
PLUGIN_ROOT = Path(__file__).resolve().parent.parent
# The tide card's readouts in a display face (the bundled Space Grotesk, SIL OFL) and the weight
# set on it as a variable font (None for a static one); Inter stands in without it.
DISPLAY_FACE = (PLUGIN_ROOT.parent / "static" / "fonts" / "SpaceGrotesk-VF.ttf", 700)
# The tide chart, then the buoy's row and BART's departures beneath it, down the right column.
CHART = (536, 274, 782, 344)
BUOY_Y = 369
BART_Y = 391
BART_ROWS, BART_ROW_STEP = 3, 14
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


def tide_chart(page, data, now):
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
    found = tide_now(rows, now)
    if found:
        height, rising = found
        page.text((512, 238), "预测此刻", 11, "muted")
        page.text((575, 235), f"{height:.1f} ft", 16, "blue", role="display")
        page.text((784, 238), "涨潮 ↑" if rising else "退潮 ↓", 11, "blue", align="right")
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
        page.text((x, 131), title, 12, "muted")
        row = upcoming[key]
        if row:
            stamp = parse_time(row["time"]).astimezone(PACIFIC)
            page.text((x, 151), stamp.strftime("%H:%M"), 32, "blue", role="display", width=132, min_size=27)
            page.text((x, 189), f"{row['height']:.1f}", 21, "blue", role="display")
            page.text((x + 58, 197), "ft", 11, "muted", role="number", bold=False)
            page.text((x + (124 if key == "H" else 132), 199), stamp.strftime("%m/%d"), 10,
                      "muted", role="number", align="right", bold=False)
        else:
            page.text((x, 164), "暂无预测", 16, "muted")
    page.rule((642, 131, 642, 213))
    page.text((512, 260), now.strftime("%m/%d") + " · 24H 预测曲线", 10, "muted", bold=False)
    page.text((784, 260), "MLLW / ft", 10, "muted", role="number", align="right", bold=False)
    tide_chart(page, data, now)
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


def bart(page, payload, now):
    """BART's next trains from the chosen station: its logo and name, then each destination with
    its line's colour and its next departures as clock times."""
    source = payload.get("bart") or {}
    data = source.get("data") or {}
    page.rule((512, BART_Y - 6, 784, BART_Y - 6))
    page.logo("bart", 512, BART_Y - 1, 14)
    # "Warm Springs/South Fremont" reads as its first name, set like BART sets it.
    name = str(data.get("station") or "BART").split("/")[0].strip()
    page.text((538, BART_Y - 1), name, 13, role="bart", width=170)
    note = "缓存" if source.get("state") == "stale_cache" else "实时发车"
    page.text((784, BART_Y + 1), note, 10, "muted", align="right", bold=False)
    lines = coming_trains(data, now)[:BART_ROWS]
    if not lines:
        page.text((512, BART_Y + 22), "暂无发车信息" if data else "BART 发车暂不可用", 10, "muted", bold=False)
        return
    # One destination a row, in BART's typeface: the line's colour, where it goes, its next trains.
    # The rows share the column's height under the station evenly, however many trains are running.
    band_top, band_bottom = BART_Y + 14, BART_BOTTOM
    step = (band_bottom - band_top) / len(lines)
    for index, line in enumerate(lines):
        y = round(band_top + index * step + (step - BART_ROW_STEP) / 2)
        page.draw.ellipse((512, y + 2, 520, y + 10), fill=line["colour"])
        times = " · ".join(stamp.astimezone(PACIFIC).strftime("%H:%M") for stamp in line["departures"][:3])
        times_w = page.draw.textlength(times, font=font(12, "bart"))
        page.text((784, y), times, 12, "blue", role="bart", align="right")
        page.text((525, y), line["destination"], 12, role="bart", width=272 - 27 - times_w, bold=False)


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
