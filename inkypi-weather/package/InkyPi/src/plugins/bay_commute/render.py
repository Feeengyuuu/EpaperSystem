"""Pillow implementation of the approved Bay Commute reference, at 800 x 480."""

from __future__ import annotations

import math
from datetime import datetime, time, timedelta
from pathlib import Path

from PIL import Image, ImageDraw, ImageOps
from utils.app_utils import get_base_ui_font, get_font
from utils.local_wordmark import paste_local_wordmark
from utils.safe_image import ImageLimits, safe_open_image

from .sources import PACIFIC, local_now, next_tides, parse_time, rank_roads, road_state, select_roads


DAY = {"paper": "#faf9f3", "ink": "#162630", "muted": "#4c5c67", "rule": "#95a1a8",
       "blue": "#00569c", "chart": "#dcedf1", "orange": "#db4e12", "green": "#006c64",
       "badge_ink": "#ffffff", "red": "#bf282c"}
NIGHT = {"paper": "#101b26", "ink": "#f3f3e6", "muted": "#c5d3da", "rule": "#7f939f",
         "blue": "#81c3ff", "chart": "#193a51", "orange": "#ffaf63", "green": "#7bd1cc",
         "badge_ink": "#132330", "red": "#ed7374"}
DIRECTIONS = {"North": "北向", "South": "南向", "East": "东向", "West": "西向",
              "North / South": "南北双向", "East / West": "东西双向"}
KINDS = {"Full": "全部封闭", "One-Way Traffic": "单向交替通行", "Alternating Lanes": "交替封道",
         "Lane": "车道封闭", "Shoulder": "路肩封闭", "Moving": "移动施工"}
FACILITIES = {"On Ramp": "入口匝道", "Off Ramp": "出口匝道", "Connector": "连接匝道",
              "Ramp": "匝道", "Mainline": "主线", "Conventional Hwy": "道路"}
ASSETS = Path(__file__).with_name("assets")
STATES = {"active": "施工中", "scheduled_now": "计划时段中", "planned": "计划"}
# Each state has its own generated tag (cone, clock or calendar) so the three read apart.
STATUS_TAG_HEIGHT = 24
# The page has no header row: the columns start at the top and its height goes to a
# larger map (at the raster's own aspect), roomier road rows and the tide chart.
TOP = 12
ROW_TOP, ROW_PITCH, ROW_X = 66, 114, 244
MAP_BOX = (18, 66, 214, 328)


class Canvas:
    def __init__(self, settings):
        theme = settings.get("_inkypi_theme") or {}
        self.palette = NIGHT if theme.get("mode", settings.get("themeMode")) == "night" else DAY
        self.image = Image.new("RGB", (800, 480), self.palette["paper"])
        self.draw = ImageDraw.Draw(self.image)
        self.family = str(settings.get("fontFamily") or "Microsoft YaHei")
        self.fonts = {}

    def font(self, size, bold=True):
        key = (size, bold)
        if key not in self.fonts:
            try:
                font = get_font(self.family, size, "bold" if bold else "normal")
            except (KeyError, TypeError, ValueError, OSError):
                font = None
            self.fonts[key] = font or get_base_ui_font(size, bold=bold)
        return self.fonts[key]

    def text(self, xy, text, size=14, color="ink", width=None, bold=True, anchor=None):
        font = self.font(size, bold)
        text = str(text)
        if width is not None and self.draw.textlength(text, font=font) > width:
            while text and self.draw.textlength(text + "…", font=font) > width:
                text = text[:-1]
            text += "…"
        self.draw.text(xy, text, font=font, fill=self.palette.get(color, color), anchor=anchor)

    def rule(self, xy, color="rule", width=1):
        self.draw.line(xy, fill=self.palette[color], width=width)


def date_label(stamp, now):
    stamp = stamp.astimezone(PACIFIC)
    delta = (stamp.date() - now.date()).days
    return "今天" if delta == 0 else "明天" if delta == 1 else stamp.strftime("%m/%d")


def source_label(source, now):
    if source["state"] == "unavailable":
        return "暂不可用"
    fetched = parse_time(source.get("fetched_at"))
    stamp = fetched.astimezone(PACIFIC).strftime("%m/%d %H:%M") if fetched else "未知"
    prefix = "过期缓存 " if source["state"] == "stale_cache" else "更新 "
    return prefix + stamp


def draw_shield(canvas, x, y, route):
    """Use source-traceable transparent standard shields, never approximate shapes."""
    assets = {key: Path(__file__).with_name("assets") / (key + ".png")
              for key in ("I-680", "I-880", "SR-84")}
    path = assets.get(route)
    if path is not None and path.is_file():
        limits = ImageLimits(max_bytes=1024 * 1024, max_width=512, max_height=512, max_pixels=512 * 512)
        with safe_open_image(path, limits=limits) as source:
            with source.convert("RGBA") as rgba:
                fitted = ImageOps.contain(rgba, (45, 49), Image.Resampling.LANCZOS)
                canvas.image.paste(fitted, (x + (45 - fitted.width) // 2, y), fitted)
                fitted.close()
    else:
        canvas.text((x, y + 12), route, 14, width=49, anchor="lt")


_STICKER_LIMITS = ImageLimits(max_bytes=256 * 1024, max_width=256, max_height=256, max_pixels=256 * 256)


def paste_sticker(canvas, name, centre, height):
    """Paste assets/sticker_<name>.png centred on ``centre`` at ``height``; returns its box or None."""
    path = ASSETS / f"sticker_{name}.png"
    if not path.is_file():
        return None
    with safe_open_image(path, limits=_STICKER_LIMITS) as source:
        with source.convert("RGBA") as rgba:
            art = rgba.resize((max(1, round(rgba.width * height / rgba.height)), height), Image.Resampling.LANCZOS)
    left, top = round(centre[0] - art.width / 2), round(centre[1] - art.height / 2)
    canvas.image.paste(art, (left, top), art)
    box = (left, top, left + art.width, top + art.height)
    art.close()
    return box


def closure_sticker(row):
    """The closure's kind as a road-works sticker: a barricade for a closed road, the ramp sign
    for a ramp or connector, a cone for lanes and everything else."""
    facility = FACILITIES.get(row.get("facility"), "")
    if "匝道" in facility:
        return "ramp"
    if row.get("kind") == "Full":
        return "barrier"
    return "cone"


def draw_status(canvas, x, y, state):
    """The state's generated tag, or an outlined orange chip when the art is missing; returns its width."""
    path = ASSETS / f"status_{state}.png"
    if path.is_file():
        limits = ImageLimits(max_bytes=256 * 1024, max_width=512, max_height=128, max_pixels=512 * 128)
        with safe_open_image(path, limits=limits) as source:
            with source.convert("RGBA") as rgba:
                width = round(rgba.width * STATUS_TAG_HEIGHT / rgba.height)
                tag = rgba.resize((width, STATUS_TAG_HEIGHT), Image.Resampling.LANCZOS)
                canvas.image.paste(tag, (x, y), tag)
                tag.close()
        return width
    status = STATES[state]
    status_width = int(canvas.draw.textlength(status, font=canvas.font(14))) + 12
    canvas.draw.rounded_rectangle((x, y, x + status_width, y + STATUS_TAG_HEIGHT), radius=3,
                                  outline=canvas.palette["orange"], width=2)
    canvas.text((x + 6, y + 4), status, 14, "orange", anchor="lt")
    return status_width


def draw_roads(canvas, payload, now):
    from .map_view import draw_marker, render_map

    source = payload["roads"]
    data = source.get("data", {})
    ranked = rank_roads(data.get("items", []), now)
    rows = select_roads(ranked, now, 3)
    placed = paste_local_wordmark(canvas.image, ASSETS / "section_roads.png", (16, TOP, 216, TOP + 42), align="left")
    if not placed:
        canvas.text((18, TOP + 3), "道路封闭", 29)
    canvas.text((placed[2] + 12 if placed else 171, TOP + 16), "CALTRANS ‧ D4", 13, "muted")
    map_x, map_y, map_w, map_h = MAP_BOX
    map_image = render_map(rows, size=(map_w, map_h), night=canvas.palette is NIGHT)
    canvas.image.paste(map_image, (map_x, map_y))
    canvas.image.info["bay_map_markers"] = map_image.info.get("markers", [])
    map_image.close()
    canvas.text((18, map_y + map_h + 8), "编号对应道路条目", 12, "muted")
    if not rows:
        text = ("道路数据暂不可用" if source["state"] == "unavailable" else
                "过期缓存中暂无匹配项目" if source["state"] == "stale_cache" else "所选路线暂无封闭记录")
        canvas.text((ROW_X, 111), text, 17, width=450 - ROW_X)
        canvas.text((ROW_X, 156), " / ".join(payload["routes"]), 14, "muted", width=450 - ROW_X)
        canvas.text((ROW_X, 193), f"关注未来 {payload.get('window_days', 7)} 天", 16, "muted", width=450 - ROW_X)
    for index, row in enumerate(rows):
        y = ROW_TOP + index * ROW_PITCH
        draw_shield(canvas, ROW_X, y + 1, row["route"])
        state = road_state(row, now)
        # The entry's number is the map's own marker, so the two read as one.
        draw_marker(canvas.draw, ROW_X + 65, y + 12, index + 1, 11, get_base_ui_font(15, bold=True))
        draw_status(canvas, ROW_X + 82, y + 1, state)
        direction = DIRECTIONS.get(row.get("direction"), row.get("direction") or "方向未报")
        canvas.text((450, y + 4), direction, 13, width=70, anchor="rt")
        kind = KINDS.get(row.get("kind"), row.get("kind") or "施工")
        facility = FACILITIES.get(row.get("facility"), "")
        # A full ramp closure must never be described as closing the entire highway.
        if row.get("kind") == "Full" and "匝道" in facility:
            kind = facility + "全封闭"
        canvas.text((ROW_X + 54, y + 31), row.get("place") or row.get("begin"), 16, width=376 - ROW_X, anchor="lt")
        # What the closure is, at a glance: a barricade, a closed ramp or a cone.
        paste_sticker(canvas, closure_sticker(row), (437, y + 40), 24)
        canvas.text((ROW_X, y + 55), kind, 14, width=448 - ROW_X, anchor="lt")
        start, end = parse_time(row["start"]), parse_time(row.get("end"))
        start = start.astimezone(PACIFIC)
        start_text = date_label(start, now) + " " + start.strftime("%H:%M")
        end_text = "未定" if row.get("indefinite") or not end else date_label(end, now) + " " + end.astimezone(PACIFIC).strftime("%H:%M")
        canvas.text((ROW_X, y + 76), start_text + " → " + end_text, 13, "orange", width=450 - ROW_X, anchor="lt")
        if index < len(rows)-1:
            canvas.rule((ROW_X, y + ROW_PITCH - 10, 450, y + ROW_PITCH - 10))
    if source["state"] == "stale_cache":
        detail = "来源过期 ‧ 缓存位置"
    elif source["state"] == "unavailable":
        detail = "等待道路数据"
    else:
        detail = f"每小时更新 ‧ 共 {len(ranked)} 项"
    canvas.text((ROW_X, 418), detail, 12, "orange" if source["state"] in {"stale_cache", "unavailable"} else "muted",
                width=450 - ROW_X)


def draw_chart(canvas, data, now):
    begin = datetime.combine(now.date(), time.min, PACIFIC)
    end = datetime.combine(now.date()+timedelta(days=1), time.min, PACIFIC)
    rows = [row for row in data.get("curve", []) if begin <= parse_time(row["time"]) < end]
    if len(rows) < 4:
        canvas.text((493, 300), "今日预测曲线不可用", 17, "muted", width=285)
        return
    left, top, right, bottom = 511, 262, 775, 391
    low = math.floor(min(row["height"] for row in rows) / 2) * 2
    high = math.ceil(max(row["height"] for row in rows) / 2) * 2
    high = max(high, low+2)
    span = end.timestamp()-begin.timestamp()
    def xy(row):
        return (left + (parse_time(row["time"]).timestamp()-begin.timestamp())/span*(right-left),
                bottom - (row["height"]-low)/(high-low)*(bottom-top))
    points = [xy(row) for row in rows]
    canvas.draw.polygon([(points[0][0], bottom), *points, (points[-1][0], bottom)], fill=canvas.palette["chart"])
    for height in range(low, high+1, 2):
        y = bottom-(height-low)/(high-low)*(bottom-top)
        canvas.text((502, y), str(height), 12, "muted", anchor="rm")
        canvas.rule((left-3,y,right,y))
    canvas.draw.line(points, fill=canvas.palette["blue"], width=3)
    for row in data.get("extremes", []):
        if begin <= parse_time(row["time"]) < end:
            x,y = xy(row)
            canvas.draw.ellipse((x-3,y-3,x+3,y+3), fill=canvas.palette["blue"])
    canvas.rule((left,top,left,bottom,right,bottom), "ink")
    draw_now_on_tide(canvas, rows, xy, now, (left, top, right, bottom))
    for hour in (0,6,12,18,24):
        stamp = end if hour == 24 else datetime.combine(now.date(), time(hour), PACIFIC)
        x = left+(stamp.timestamp()-begin.timestamp())/span*(right-left)
        canvas.text((x,399), f"{hour:02d}", 12, "muted", anchor="mt")
    canvas.text((492, 240), now.strftime("%m/%d") + " 预测曲线", 12, "muted")
    canvas.text((776, 240), "ft", 12, "muted", anchor="rt")


def tide_now(rows, now):
    """(height now, rising) from the day's curve by linear interpolation; None outside it."""
    stamps = [(parse_time(row["time"]), row["height"]) for row in rows]
    for (start, low), (end, high) in zip(stamps, stamps[1:]):
        if start <= now <= end:
            share = (now - start).total_seconds() / max(1.0, (end - start).total_seconds())
            return low + (high - low) * share, high >= low
    return None


def draw_now_on_tide(canvas, rows, xy, now, box):
    """A dotted line at the present moment, a sailboat riding the curve there and its tide."""
    found = tide_now(rows, now)
    if found is None:
        return
    height, rising = found
    left, top, right, bottom = box
    x, y = xy({"time": now.isoformat(), "height": height})
    for dot_y in range(round(top), round(bottom), 4):
        canvas.draw.point((x, dot_y), fill=canvas.palette["muted"])
    boat = paste_sticker(canvas, "boat", (x, y - 9), 22)
    if boat is None:
        canvas.draw.ellipse((x - 4, y - 4, x + 4, y + 4), fill=canvas.palette["orange"])
    label = f"现在 {height:.1f} ft {'涨潮 ↑' if rising else '退潮 ↓'}"
    width = canvas.draw.textlength(label, font=canvas.font(11))
    label_x = x + 12 if x + 12 + width <= right else x - 12 - width
    label_y = max(top + 2, min(bottom - 16, y - 30))
    canvas.text((label_x, label_y), label, 11, "blue" if rising else "orange", anchor="lt")
    if rising:
        # A gull keeps the boat company while the water comes in.
        paste_sticker(canvas, "gull", (min(right - 10, x + 30), max(top + 8, label_y - 6)), 14)


def draw_tides(canvas, payload, settings, now):
    source, station = payload["tides"], payload["station"]
    data = source.get("data", {})
    station_name = (str(settings.get("stationName") or "").strip()
                    or ("Redwood City" if station == "9414523" else "NOAA " + station))
    canvas.text((488, TOP + 3), station_name.upper(), 13, "muted", width=294)
    if not paste_local_wordmark(canvas.image, ASSETS / "section_tides.png", (486, TOP + 20, 700, TOP + 66), align="left"):
        canvas.text((488, TOP + 23), "潮汐预测", 31)
    upcoming = next_tides(data, now)
    for kind, x, title in (("H",488,"下一次高潮"),("L",642,"下一次低潮")):
        canvas.text((x,94), title, 16)
        row = upcoming[kind]
        if row:
            stamp = parse_time(row["time"]).astimezone(PACIFIC)
            canvas.text((x,117), date_label(stamp, now) + " " + stamp.strftime("%m/%d"), 13, "muted")
            canvas.text((x,136), stamp.strftime("%H:%M"), 32, "blue", width=144)
            canvas.text((x+2,178), f"{row['height']:.1f} ft", 22, "blue", width=143)
        else:
            canvas.text((x,134), "—", 34, "muted")
            canvas.text((x,180), "等待预测", 14, "muted")
    canvas.rule((631,95,631,205))
    draw_chart(canvas, data, now)
    label = "NOAA ‧ MLLW ‧ 英尺"
    if source["state"] == "stale_cache":
        label = "过期预测缓存 ‧ " + data.get("prediction_date", "")
    elif source["state"] == "unavailable":
        label = "潮汐数据暂不可用 ‧ 道路独立更新"
    canvas.text((783, 418), label, 12,
                "orange" if source["state"] in {"stale_cache", "unavailable"} else "muted", anchor="rt")


def render_page(dimensions, payload, settings, now):
    now = local_now(now)
    canvas = Canvas(settings)
    canvas.rule((464, TOP, 464, 434), "ink")
    draw_roads(canvas, payload, now)
    draw_tides(canvas, payload, settings, now)
    canvas.rule((10,444,790,444), "ink", 2)
    roads = payload["roads"]
    recorded = parse_time(roads.get("data", {}).get("source_updated_at"))
    fetched = parse_time(roads.get("fetched_at"))
    if recorded and fetched:
        prefix = "道路缓存 " if roads["state"] == "stale_cache" else "道路来源 "
        road_footer = (prefix + recorded.astimezone(PACIFIC).strftime("%m/%d %H:%M")
                       + " ‧ 获取 " + fetched.astimezone(PACIFIC).strftime("%H:%M"))
    else:
        road_footer = "道路 " + source_label(roads, now)
    canvas.text((18,451), road_footer, 12, width=427)
    tide_source = payload["tides"]
    prediction_date = tide_source.get("data", {}).get("prediction_date", "—").replace("-", "/")[-5:]
    fetched = parse_time(tide_source.get("fetched_at"))
    fetched_text = fetched.astimezone(PACIFIC).strftime("%m/%d %H:%M") if fetched else "—"
    canvas.text((489,451), "预测 " + prediction_date + " ‧ 获取 " + fetched_text, 12, width=295)
    result = canvas.image
    if tuple(dimensions) != (800,480):
        result = result.resize(tuple(dimensions), Image.Resampling.LANCZOS)
    return result
