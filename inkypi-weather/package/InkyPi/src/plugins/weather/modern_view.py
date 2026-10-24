"""What the modern Weather page shows, computed from the template parameters.

Everything the page draws as a gauge or chart is worked out here in page pixels, so
the browser only lays out text and inline SVG: the sun's place on today's arc, the
wind's compass bearing and Beaufort force, the air-quality and UV scales, the
24-hour curve with rain bars and night bands, and the week's temperature ranges.
Labels are Chinese; values keep the provider's units.
"""

from __future__ import annotations

import hashlib
import math
import os
import random
import re
from datetime import datetime, timedelta

CONDITIONS = {
    "01": ("晴", "clear"), "02": ("少云", "cloudy"), "022": ("晴间多云", "cloudy"), "03": ("多云", "cloudy"),
    "04": ("阴", "cloudy"), "09": ("阵雨", "rain"), "10": ("雨", "rain"), "11": ("雷阵雨", "thunder"),
    "13": ("雪", "snow"), "48": ("雾", "fog"), "50": ("雾", "fog"), "51": ("毛毛雨", "rain"),
    "53": ("小雨", "rain"), "56": ("冻毛毛雨", "rain"), "57": ("冻雨", "rain"), "71": ("小雪", "snow"),
    "73": ("中雪", "snow"), "77": ("雪粒", "snow"),
}
# Scenes that differ by day and night; the others read the same at any hour.
SCENES_WITH_NIGHT = {"clear", "cloudy"}
WEEKDAYS = {"Mon": "周一", "Tue": "周二", "Wed": "周三", "Thu": "周四", "Fri": "周五", "Sat": "周六", "Sun": "周日"}
WEEKDAY_NAMES = "一二三四五六日"
WIND_DIRECTIONS = ("北", "东北", "东", "东南", "南", "西南", "西", "西北")
# Upper bounds of Beaufort forces 0-11 in m/s; 12 above the last.
BEAUFORT_MS = (0.3, 1.6, 3.4, 5.5, 8.0, 10.8, 13.9, 17.2, 20.8, 24.5, 28.5, 32.7)
AQI_LABELS = ("优", "良", "中等", "差", "很差", "极差")
AQI_COLOURS = ("#2e9e5b", "#9ccc3c", "#f2b705", "#e8701a", "#c62828", "#7b1fa2")
UV_BANDS = ((3, "弱", "无需防晒"), (6, "中等", "注意防晒"), (8, "强", "需要防晒"), (11, "很强", "避免暴晒"),
            (math.inf, "极强", "避免外出"))
MOON_NAMES = {"newmoon": "新月", "waxingcrescent": "蛾眉月", "firstquarter": "上弦月", "waxinggibbous": "盈凸月",
              "fullmoon": "满月", "waninggibbous": "亏凸月", "lastquarter": "下弦月", "waningcrescent": "残月"}
# The 24-hour chart's drawing box (page pixels) and the curve's band within it.
CHART_SIZE = (500, 128)
CHART_PAD_X = 16
CURVE_TOP, CURVE_BOTTOM = 26, 70
RAIN_BASE, RAIN_HEIGHT = 96, 22
LABEL_STEP = 3


# Each card's generated icon heading its title (with the card's own colour in the CSS).
TILE_ICONS = ("sun", "wind", "air", "uv", "humidity", "life", "hourly", "week")


def build_view(params, now, *, art_dir=None):
    """The page's view model; ``params`` are the Weather template parameters."""
    params = params or {}
    points = {str(point.get("label")): point for point in params.get("data_points") or []}
    sun = _sun(params.get("astronomy"), now)
    code = _icon_code(params.get("current_day_icon"))
    condition, scene = CONDITIONS.get(code, ("", "cloudy"))
    if scene in SCENES_WITH_NIGHT:
        scene = f"{scene}-{'night' if sun.get('night') else 'day'}"
    hourly = _hourly(params.get("hourly_forecast") or [], now, params.get("astronomy"))
    feels = _number(params.get("feels_like"))
    units = str(params.get("units") or "metric")
    forecast = list(params.get("forecast") or [])
    today = forecast[0] if forecast else {}
    view = {
        "date": f"{now.month}月{now.day}日 周{WEEKDAY_NAMES[now.weekday()]}",
        "updated": str(params.get("last_refresh_time") or "")[-5:],
        "condition": condition,
        "scene": scene,
        "scene_image": _scene_path(art_dir, scene),
        "high": today.get("high"),
        "low": today.get("low"),
        "summary": _summary(hourly),
        "tips": _tips(feels, units, hourly, points.get("UV Index")),
        "sun": sun,
        "moon": _moon(today),
        "wind": _wind(points.get("Wind"), units),
        "aqi": _aqi(points.get("Air Quality")),
        "uv": _uv(points.get("UV Index")),
        "humidity": _humidity(points.get("Humidity")),
        "pressure": _plain(points.get("Pressure")),
        "visibility": _plain(points.get("Visibility")),
        "hourly": hourly,
        "week": _week(forecast[1:8]),
    }
    celsius = _celsius(feels, units)
    view["best"] = _best_window(hourly, units)
    view["indices"] = _life_indices(view, scene, celsius, forecast[1:3])
    view["mascot"] = _mascot(view, scene, art_dir, celsius)
    view["effect"] = _page_effect(scene, now)
    view["icons"] = _tile_icons(art_dir)
    return view


# Current conditions ----------------------------------------------------------------

def _icon_code(path):
    name = os.path.basename(str(path or "")).split(".")[0]
    return re.sub(r"[dn]$", "", name)


def _tile_icons(art_dir):
    """{card: its generated icon (art/icon-<card>.png)} for the cards whose icon is present."""
    if not art_dir:
        return {}
    paths = {name: os.path.join(art_dir, f"icon-{name}.png") for name in TILE_ICONS}
    return {name: path for name, path in paths.items() if os.path.isfile(path)}


def _scene_path(art_dir, scene):
    if not art_dir:
        return ""
    path = os.path.join(art_dir, f"{scene}.jpg")
    return path if os.path.isfile(path) else ""


def _number(value):
    try:
        return float(str(value).replace("≥", "").strip())
    except (TypeError, ValueError):
        return None


def _celsius(value, units):
    if value is None:
        return None
    if units == "imperial":
        return (value - 32) * 5 / 9
    if units == "standard":
        return value - 273.15
    return value


def _summary(hourly):
    points = hourly.get("points") or []
    if not points:
        return ""
    warmest = max(points, key=lambda point: point["temperature"])
    parts = [f"{warmest['time']} 最暖 {warmest['temperature']}°"]
    wet = next((point for point in points if point["rain_chance"] >= 40), None)
    if wet:
        parts.append(f"{wet['time']} 起可能有雨（{wet['rain_chance']}%）")
    else:
        parts.append("未来 24 小时无雨")
    return "，".join(parts)


def _tips(feels, units, hourly, uv_point):
    tips = []
    celsius = _celsius(feels, units)
    if celsius is not None:
        for limit, tip in ((5, "羽绒服"), (10, "厚外套"), (16, "夹克卫衣"), (22, "薄外套"), (28, "T 恤")):
            if celsius < limit:
                tips.append(tip)
                break
        else:
            tips.append("短袖")
    chances = [point["rain_chance"] for point in hourly.get("points") or []]
    tips.append("记得带伞" if chances and max(chances) >= 40 else "不用带伞")
    uv = _number((uv_point or {}).get("measurement"))
    if uv is not None:
        tips.append("注意防晒" if uv >= 3 else "无需防晒")
    return tips


# Gauges -------------------------------------------------------------------------------

def _sun(astronomy, now):
    """Today's sunrise and sunset, daylight length, and the sun's place on a 0-1 arc."""
    astronomy = astronomy or {}
    try:
        sunrise = datetime.fromisoformat(str(astronomy.get("sunrise")))
        sunset = datetime.fromisoformat(str(astronomy.get("sunset")))
    except (TypeError, ValueError):
        return {"night": False}
    local_now = now.astimezone(sunrise.tzinfo) if now.tzinfo else now
    span = max(1.0, (sunset - sunrise).total_seconds())
    progress = (local_now - sunrise).total_seconds() / span
    night = not 0 <= progress <= 1
    minutes = int(span // 60)
    sun = {
        "rise": sunrise.strftime("%H:%M"),
        "set": sunset.strftime("%H:%M"),
        "daylight": f"{minutes // 60}小时{minutes % 60:02d}分",
        "daylight_short": f"{minutes // 60}:{minutes % 60:02d}",
        "night": night,
        "progress": max(0.0, min(1.0, progress)),
    }
    # The arc's own box: centre (60, 58), radius 46 in a 120 x 66 svg.
    angle = math.pi * (1 - sun["progress"])
    sun["x"] = round(60 + 46 * math.cos(angle), 1)
    sun["y"] = round(58 - 46 * math.sin(angle), 1)
    # A countdown to whichever comes next: today's sunset, or the next sunrise.
    if not night:
        target, label = sunset, "距日落"
    else:
        target = sunrise if local_now < sunrise else sunrise + timedelta(days=1)
        label = "距日出"
    left = max(0, int((target - local_now).total_seconds() // 60))
    sun["countdown"] = f"{label} {left // 60}小时{left % 60:02d}分"
    return sun


def _moon(today):
    icon = os.path.basename(str((today or {}).get("moon_phase_icon") or "")).split(".")[0]
    name = MOON_NAMES.get(icon, "")
    return {"name": name, "icon": (today or {}).get("moon_phase_icon") or "",
            "percent": (today or {}).get("moon_phase_pct")} if name else {}


def _wind(point, units):
    point = point or {}
    speed = _number(point.get("measurement"))
    if speed is None:
        return {}
    degrees = _number(point.get("degrees")) or 0.0
    metres = speed * 0.44704 if units == "imperial" else speed
    force = next((index for index, limit in enumerate(BEAUFORT_MS) if metres < limit), 12)
    direction = WIND_DIRECTIONS[int(((degrees % 360) + 22.5) // 45) % 8]
    return {
        "speed": f"{speed:.1f}".rstrip("0").rstrip(".") if speed < 10 else f"{speed:.0f}",
        "unit": point.get("unit") or "",
        # The arrow points where the wind blows to.
        "heading": round((degrees + 180) % 360),
        "name": "无风" if force == 0 else f"{direction}风 {force} 级",
        "force": force,
    }


def _aqi(point):
    point = point or {}
    level = point.get("level")
    if level is None:
        return {}
    level = max(0, min(len(AQI_LABELS) - 1, int(level)))
    # The needle sits in the middle of its band on a five-band half dial.
    bands = 5
    fraction = (min(level, bands - 1) + 0.5) / bands
    angle = math.pi * (1 - fraction)
    return {
        "value": point.get("measurement"),
        "label": AQI_LABELS[level],
        "colour": AQI_COLOURS[level],
        # Black on the light bands (good, fair, moderate), white on the dark ones.
        "ink": "#111418" if level <= 2 else "#ffffff",
        "needle_x": round(50 + 30 * math.cos(angle), 1),
        "needle_y": round(46 - 30 * math.sin(angle), 1),
        "segments": [_arc_segment(50, 46, 38, index / bands, (index + 1) / bands, AQI_COLOURS[index])
                     for index in range(bands)],
    }


def _arc_segment(cx, cy, radius, start, end, colour):
    """An svg path for part of an upper half circle, ``start``/``end`` running 0-1 left to right."""
    a0, a1 = math.pi * (1 - start), math.pi * (1 - end)
    x0, y0 = cx + radius * math.cos(a0), cy - radius * math.sin(a0)
    x1, y1 = cx + radius * math.cos(a1), cy - radius * math.sin(a1)
    return {"d": f"M{x0:.1f},{y0:.1f} A{radius},{radius} 0 0,1 {x1:.1f},{y1:.1f}", "colour": colour}


def _uv(point):
    value = _number((point or {}).get("measurement"))
    if value is None:
        return {}
    label, advice = next((label, advice) for limit, label, advice in UV_BANDS if value < limit)
    return {"value": f"{value:.0f}" if value >= 1 or value == 0 else f"{value:.1f}", "label": label,
            "advice": advice, "marker": round(min(1.0, value / 11) * 100, 1)}


def _humidity(point):
    value = _number((point or {}).get("measurement"))
    if value is None:
        return {}
    feel = "干燥" if value < 30 else "舒适" if value <= 65 else "潮湿"
    return {"value": int(round(value)), "feel": feel, "fill": round(max(0.0, min(100.0, value)), 1)}


def _plain(point):
    point = point or {}
    value = point.get("measurement")
    if value in (None, "N/A"):
        return {}
    return {"value": value, "unit": point.get("unit") or ""}


# 24 hours -----------------------------------------------------------------------------

def _hourly(rows, now, astronomy):
    rows = [row for row in rows if _number(row.get("temperature")) is not None][:24]
    if len(rows) < 2:
        return {}
    width, height = CHART_SIZE
    temps = [_number(row.get("temperature")) for row in rows]
    low, high = min(temps), max(temps)
    span = max(1.0, high - low)
    step = (width - 2 * CHART_PAD_X) / (len(rows) - 1)
    sunrise, sunset = _sun_times(astronomy)
    start = now.replace(minute=0, second=0, microsecond=0)
    points = []
    for index, (row, temp) in enumerate(zip(rows, temps)):
        x = CHART_PAD_X + index * step
        y = CURVE_BOTTOM - (temp - low) / span * (CURVE_BOTTOM - CURVE_TOP)
        chance = int(round(max(0.0, min(1.0, _number(row.get("precipitation")) or 0.0)) * 100))
        moment = start + timedelta(hours=index)
        points.append({
            "x": round(x, 1), "y": round(y, 1), "temperature": int(round(temp)),
            "time": str(row.get("time") or ""), "icon": str(row.get("icon") or "").replace("\\", "/"),
            "rain_chance": chance, "rain_height": round(chance / 100 * RAIN_HEIGHT, 1),
            "night": _is_night(moment, sunrise, sunset), "label": index % LABEL_STEP == 0,
        })
    path = _smooth_path([(point["x"], point["y"]) for point in points])
    area = f"{path} L{points[-1]['x']},{RAIN_BASE - RAIN_HEIGHT - 2} L{points[0]['x']},{RAIN_BASE - RAIN_HEIGHT - 2} Z"
    return {
        "width": width, "height": height, "path": path, "area": area, "points": points,
        "night_bands": _night_bands(points, step, width), "bar_width": round(max(2.0, step * 0.62), 1),
        "rain_base": RAIN_BASE, "high": int(round(high)), "low": int(round(low)),
        "any_rain": any(point["rain_chance"] >= 10 for point in points),
    }


def _sun_times(astronomy):
    try:
        return (datetime.fromisoformat(str((astronomy or {}).get("sunrise"))),
                datetime.fromisoformat(str((astronomy or {}).get("sunset"))))
    except (TypeError, ValueError):
        return None, None


def _is_night(moment, sunrise, sunset):
    if sunrise is None or sunset is None:
        return moment.hour < 6 or moment.hour >= 18
    local = moment.astimezone(sunrise.tzinfo) if moment.tzinfo else moment.replace(tzinfo=sunrise.tzinfo)
    day = local.date() - sunrise.date()
    rise, set_ = sunrise + day, sunset + day
    return not rise <= local < set_


def _night_bands(points, step, width):
    """Shaded spans over the night hours; a night at either end reaches the chart's edge."""
    bands, start, end = [], None, 0.0
    last = len(points) - 1
    for index, point in enumerate(points + [None]):
        if point is not None and point["night"]:
            if start is None:
                start = 0.0 if index == 0 else point["x"] - step / 2
            end = float(width) if index == last else point["x"] + step / 2
        elif start is not None:
            bands.append({"x": round(start, 1), "width": round(end - start, 1)})
            start = None
    return bands


def _smooth_path(points):
    """A Catmull-Rom curve through ``points`` as cubic Bezier segments."""
    if not points:
        return ""
    parts = [f"M{points[0][0]:.1f},{points[0][1]:.1f}"]
    for index in range(len(points) - 1):
        p0 = points[max(0, index - 1)]
        p1, p2 = points[index], points[index + 1]
        p3 = points[min(len(points) - 1, index + 2)]
        c1 = (p1[0] + (p2[0] - p0[0]) / 6, p1[1] + (p2[1] - p0[1]) / 6)
        c2 = (p2[0] - (p3[0] - p1[0]) / 6, p2[1] - (p3[1] - p1[1]) / 6)
        parts.append(f"C{c1[0]:.1f},{c1[1]:.1f} {c2[0]:.1f},{c2[1]:.1f} {p2[0]:.1f},{p2[1]:.1f}")
    return " ".join(parts)


# Week ---------------------------------------------------------------------------------

def _week(days):
    days = [day for day in days if _number(day.get("high")) is not None and _number(day.get("low")) is not None]
    if not days:
        return []
    low = min(_number(day["low"]) for day in days)
    high = max(_number(day["high"]) for day in days)
    span = max(1.0, high - low)
    week = []
    for index, day in enumerate(days):
        start = (_number(day["low"]) - low) / span * 100
        end = (_number(day["high"]) - low) / span * 100
        week.append({
            "name": "明天" if index == 0 else WEEKDAYS.get(str(day.get("day")), str(day.get("day") or "")),
            "icon": day.get("icon") or "", "high": day.get("high"), "low": day.get("low"),
            "bar_left": round(start, 1), "bar_width": round(max(4.0, end - start), 1),
        })
    return week


# Fun: the mascot's line, life indices, the best hours out and the page's weather effect ----

MASCOTS = {"clear-day": "sunny", "clear-night": "night", "cloudy-night": "night", "cloudy-day": "cloudy",
           "rain": "rain", "thunder": "thunder", "fog": "fog", "snow": "snow"}
RAINY_ICON_CODES = {"09", "10", "11", "13", "51", "53", "56", "57", "71", "73", "77"}
# Comfortable hours out: daylight, a feels-like between these, and little chance of rain.
BEST_TEMP_C = (14, 27)
BEST_MAX_RAIN = 20


def _best_window(hourly, units):
    """The longest run of comfortable daylight hours in the next 24, with its chart span."""
    points = hourly.get("points") or []
    if len(points) < 2:
        return {}
    step = points[1]["x"] - points[0]["x"]
    best, run = [], []
    for point in points + [None]:
        celsius = _celsius(point["temperature"], units) if point else None
        good = (point is not None and not point["night"] and point["rain_chance"] < BEST_MAX_RAIN
                and BEST_TEMP_C[0] <= celsius <= BEST_TEMP_C[1])
        if good:
            run.append(point)
            continue
        if len(run) > len(best):
            best = run
        run = []
    if len(best) < 2:
        return {}
    return {"start": best[0]["time"], "end": best[-1]["time"],
            "x": round(best[0]["x"] - step / 2, 1), "width": round(best[-1]["x"] - best[0]["x"] + step, 1)}


def _life_indices(view, scene, celsius, next_days):
    """Four everyday ratings, one to five stars, from the same readings the page shows."""
    points = (view.get("hourly") or {}).get("points") or []
    rain6 = max([point["rain_chance"] for point in points[:6]] or [0])
    rain24 = max([point["rain_chance"] for point in points] or [0])
    aqi_level = AQI_LABELS.index(view["aqi"]["label"]) if view.get("aqi") else 0
    force = (view.get("wind") or {}).get("force") or 0
    uv = _number((view.get("uv") or {}).get("value")) or 0
    night = bool((view.get("sun") or {}).get("night"))
    humidity = (view.get("humidity") or {}).get("value") or 0
    base = scene.split("-")[0]

    def comfort_penalty():
        if celsius is None:
            return 0
        return 2 if celsius < 5 or celsius > 30 else 1 if celsius < 10 or celsius > 25 else 0

    run = 5 - (3 if rain6 >= 50 else 1 if rain6 >= 20 else 0) - comfort_penalty() \
        - (2 if aqi_level >= 3 else 1 if aqi_level == 2 else 0) - (1 if force >= 6 else 0)
    dry = {"clear": 5, "cloudy": 3, "fog": 2}.get(base, 1) - (1 if humidity > 80 else 0)
    if rain24 >= 40:
        dry = 1
    if night:
        dry = min(dry, 2)
    rainy_days = any(_icon_code(day.get("icon")) in RAINY_ICON_CODES for day in next_days or [])
    wash = (1 if rain24 >= 40 else 2 if rainy_days else 5) - (1 if aqi_level >= 3 else 0)
    outdoor = 5 - (3 if rain6 >= 50 else 1 if rain6 >= 20 else 0) - comfort_penalty() \
        - (1 if uv >= 8 else 0) - (1 if aqi_level >= 3 else 0) - (1 if force >= 6 else 0) - (1 if night else 0)
    return [{"name": name, "stars": max(1, min(5, value))}
            for name, value in (("跑步", run), ("晾晒", dry), ("洗车", wash), ("户外", outdoor))]


def _mascot(view, scene, art_dir, celsius):
    key = MASCOTS.get(scene, "cloudy")
    path = os.path.join(art_dir, f"mascot-{key}.png") if art_dir else ""
    return {"key": key, "image": path if path and os.path.isfile(path) else "", "line": _mascot_line(view, scene, celsius)}


def _mascot_line(view, scene, celsius):
    """What the mascot says: the most useful thing about the next few hours, first match wins."""
    points = (view.get("hourly") or {}).get("points") or []
    soon = next((point for point in points[1:7] if point["rain_chance"] >= 50), None)
    sun = view.get("sun") or {}
    best = view.get("best") or {}
    uv = _number((view.get("uv") or {}).get("value")) or 0
    aqi_label = (view.get("aqi") or {}).get("label")
    force = (view.get("wind") or {}).get("force") or 0
    if scene == "thunder":
        return "打雷啦！待在室内吧"
    if scene == "rain":
        return "下雨了，路上慢点哦"
    if soon:
        return f"{soon['time']} 要下雨，带伞哦"
    if scene == "snow":
        return "下雪啦！小心路滑"
    if scene == "fog":
        return "雾大，开车慢一点"
    if aqi_label in AQI_LABELS[3:]:
        return "空气不好，少开窗"
    if uv >= 6 and not sun.get("night"):
        return "紫外线强，记得防晒"
    if force >= 5:
        return "风大，帽子抓紧啦"
    if celsius is not None and celsius <= 6:
        return "好冷！多穿一件"
    if celsius is not None and celsius >= 30:
        return "好热！多喝点水"
    if sun.get("night"):
        return f"晚安～明早 {sun.get('rise', '')} 日出"
    if best:
        return f"{best['start']} 后出门正好！"
    return "今天也要开心呀！"


def _page_effect(scene, now):
    """Weather marks across the header band, placed the same way all day."""
    seed = int(hashlib.sha256(f"{scene}{now:%Y%m%d}".encode()).hexdigest()[:8], 16)
    rng = random.Random(seed)
    kind = {"clear-day": "rays", "clear-night": "stars", "cloudy-night": "stars", "cloudy-day": "clouds",
            "rain": "rain", "thunder": "rain", "snow": "snow", "fog": "fog"}.get(scene, "clouds")
    marks = []
    if kind == "rays":
        for index in range(7):
            angle = math.radians(188 + index * 9)
            marks.append({"shape": "ray", "points": f"800,0 {800 + 520 * math.cos(angle - 0.03):.0f},{-520 * math.sin(angle - 0.03):.0f} "
                                                     f"{800 + 520 * math.cos(angle + 0.03):.0f},{-520 * math.sin(angle + 0.03):.0f}"})
    elif kind == "stars":
        for _ in range(34):
            marks.append({"shape": "star", "x": rng.randint(300, 790), "y": rng.randint(4, 38), "r": rng.choice((1, 1, 1.5, 2))})
    elif kind == "clouds":
        for index in range(4):
            marks.append({"shape": "cloud", "x": 330 + index * 120 + rng.randint(-20, 20), "y": rng.randint(12, 26)})
    elif kind == "rain":
        for _ in range(46):
            x, y = rng.randint(300, 800), rng.randint(-4, 34)
            marks.append({"shape": "drop", "x1": x, "y1": y, "x2": x - 5, "y2": y + 11})
        if scene == "thunder":
            marks.append({"shape": "bolt", "points": "742,2 732,20 741,20 730,40 754,15 744,15 752,2"})
    elif kind == "snow":
        for _ in range(26):
            marks.append({"shape": "flake", "x": rng.randint(300, 790), "y": rng.randint(5, 37), "r": rng.choice((2, 3, 3.5))})
    elif kind == "fog":
        for index in range(3):
            y = 10 + index * 11
            marks.append({"shape": "wave", "d": " ".join(
                f"{'M' if step == 0 else 'L'}{300 + step * 20},{y + 3 * math.sin(step * 0.9 + index):.1f}"
                for step in range(26))})
    return {"kind": kind, "marks": marks}
