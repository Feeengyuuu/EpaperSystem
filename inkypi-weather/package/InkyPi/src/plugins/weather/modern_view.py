"""What the modern Weather page shows, computed from the template parameters.

Everything the page draws as a gauge or chart is worked out here in page pixels, so
the browser only lays out text and inline SVG: the sun's place on today's arc, the
wind's compass bearing and Beaufort force, the air-quality and UV scales, the
24-hour curve with rain bars and night bands, and the week's temperature ranges.
Labels are Chinese; values keep the provider's units.
"""

from __future__ import annotations

import math
import os
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
    return view


# Current conditions ----------------------------------------------------------------

def _icon_code(path):
    name = os.path.basename(str(path or "")).split(".")[0]
    return re.sub(r"[dn]$", "", name)


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
        "night": night,
        "progress": max(0.0, min(1.0, progress)),
    }
    # The arc's own box: centre (60, 58), radius 46 in a 120 x 66 svg.
    angle = math.pi * (1 - sun["progress"])
    sun["x"] = round(60 + 46 * math.cos(angle), 1)
    sun["y"] = round(58 - 46 * math.sin(angle), 1)
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
