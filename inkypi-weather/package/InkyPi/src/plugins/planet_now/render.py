"""环球此刻: an 800 x 480 page of four themed cards on white (or black) paper.

Top left, the sky over the Bay: GOES-West's GeoColor view centred on home, the rain radar's
verdict for the ring it checks and EPIC's whole Earth. Top right, California's grid: the
renewable share and today's supply stacked by source under the day-ahead demand. Bottom left,
what America is searching (Google Trends, with each search's headline) and Wikipedia's
most-read article. Bottom right, the wider card, space: Perseverance's newest picture, the ISS
over a world map with its track and everyone in orbit by station.
Each card has its own colour, generated icon and band; small text stays in plain ink.
"""

from __future__ import annotations

import math
from collections import Counter
from datetime import datetime
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageOps

from utils.local_wordmark import paste_header_art, paste_local_wordmark
from utils.page_canvas import PageCanvas
from utils.safe_image import ImageLimits, safe_open_image

from .sources import (
    HOME, ISS_PERIOD_MIN, LIGHT_KM_S, PACIFIC, RAIN_RADIUS_KM, decode_image, iss_ascending, iss_ground_track,
    iss_next_switch, local_now, mars_distance_km, ocean_name, sky_pixels, sky_position, subsolar_point, sun_elevation,
)

ASSETS = Path(__file__).resolve().parent / "assets"
_LIMITS = ImageLimits(max_bytes=1024 * 1024, max_width=1024, max_height=1024, max_pixels=1024 * 1024)
WEEKDAYS = "一二三四五六日"
DAY = {
    "paper": "#ffffff", "ink": "#121a20", "muted": "#3b4650", "rule": "#999999", "panel": "#eef1f4",
    "earth": "#2554c7", "grid": "#059669", "trends": "#c81e32", "space": "#6d28d9", "gold": "#e8a90c",
    "chip": "#121a20", "chip_ink": "#ffffff", "rain": "#2563eb", "dry": "#059669",
}
NIGHT = {
    "paper": "#000000", "ink": "#edf1ea", "muted": "#c6d0d5", "rule": "#5a5a5a", "panel": "#161c22",
    "earth": "#7aa2ff", "grid": "#34d399", "trends": "#ff6b78", "space": "#b794f6", "gold": "#fbbf24",
    "chip": "#05090c", "chip_ink": "#edf1ea", "rain": "#60a5fa", "dry": "#34d399",
}
# The supply chart's sources, bottom to top, with their colours (day, night).
FUEL_COLORS = {
    "nuclear": ("#7c3aed", "#a78bfa"), "other": ("#92704f", "#c4a484"), "hydro": ("#2563eb", "#60a5fa"),
    "wind": ("#14b8a6", "#5eead4"), "solar": ("#eab308", "#fde047"), "batteries": ("#22c55e", "#86efac"),
    "gas": ("#94a3b8", "#64748b"), "imports": ("#cbd5e1", "#475569"),
}
FUEL_ORDER = ("nuclear", "other", "hydro", "wind", "solar", "batteries", "gas", "imports")
FUEL_LABELS = {"solar": "太阳能", "wind": "风电", "hydro": "水电", "gas": "燃气", "imports": "进口", "batteries": "电池",
               "nuclear": "核电", "other": "其他"}
# The mix bar's order: the renewables first, then the rest.
MIX_ORDER = ("solar", "wind", "hydro", "other", "nuclear", "batteries", "gas", "imports")
HEADER = 28
CARD_BAND = 5
# The space card reaches left under the sky card, wider than the searches beside it.
CARDS = {"earth": (12, 58, 394, 264), "grid": (406, 58, 788, 264), "trends": (12, 270, 300, 474),
         "space": (312, 270, 788, 474)}
SECTION_ART = {"earth": "section-earth", "grid": "section-grid", "trends": "section-trends", "space": "section-space"}
# Each source's official logo in assets/logos (tools/build_planet_now_logos.py): the height it
# is set at in a card's header and its name in words should the file be missing.
SOURCE_LOGOS = {"noaa": (18, "NOAA"), "caiso": (16, "CAISO"), "apple_music": (12, "Apple Music"),
                "google_trends": (14, "Google Trends"), "nasa": (18, "NASA")}
# Places marked on the satellite crop: (name, lat, lon, label side); a None side is a sea's
# name with no dot.
SKY_PLACES = (("旧金山", 37.7793, -122.4193, "left"), ("奥克兰", 37.8044, -122.2712, "right"),
              ("圣何塞", 37.3382, -121.8863, "right"), ("太平洋", 37.36, -122.86, None))
STATIONS = {"tiangong": "天宫", "iss": "国际空间站"}
COUNTRY_NAMES = {
    "US": "美国", "CA": "加拿大", "MX": "墨西哥", "BR": "巴西", "AR": "阿根廷", "CL": "智利", "PE": "秘鲁", "CO": "哥伦比亚",
    "GB": "英国", "FR": "法国", "DE": "德国", "ES": "西班牙", "IT": "意大利", "RU": "俄罗斯", "UA": "乌克兰", "TR": "土耳其",
    "CN": "中国", "JP": "日本", "KR": "韩国", "IN": "印度", "ID": "印度尼西亚", "AU": "澳大利亚", "NZ": "新西兰",
    "EG": "埃及", "ZA": "南非", "NG": "尼日利亚", "SA": "沙特阿拉伯", "IR": "伊朗", "KZ": "哈萨克斯坦", "MN": "蒙古",
    "PH": "菲律宾", "VN": "越南", "TH": "泰国", "MY": "马来西亚", "PK": "巴基斯坦", "DZ": "阿尔及利亚", "LY": "利比亚",
    "SD": "苏丹", "ET": "埃塞俄比亚", "KE": "肯尼亚", "CD": "刚果（金）", "AO": "安哥拉", "MA": "摩洛哥", "GL": "格陵兰",
    "EC": "厄瓜多尔", "BO": "玻利维亚", "VE": "委内瑞拉", "PY": "巴拉圭", "UY": "乌拉圭", "GY": "圭亚那", "SR": "苏里南",
    "CU": "古巴", "GT": "危地马拉", "HN": "洪都拉斯", "NI": "尼加拉瓜", "CR": "哥斯达黎加", "PA": "巴拿马", "SV": "萨尔瓦多",
    "BZ": "伯利兹", "DO": "多米尼加", "HT": "海地", "JM": "牙买加", "BS": "巴哈马", "PT": "葡萄牙", "IE": "爱尔兰",
    "NL": "荷兰", "BE": "比利时", "CH": "瑞士", "AT": "奥地利", "PL": "波兰", "CZ": "捷克", "SK": "斯洛伐克", "HU": "匈牙利",
    "RO": "罗马尼亚", "BG": "保加利亚", "GR": "希腊", "RS": "塞尔维亚", "HR": "克罗地亚", "BA": "波黑", "AL": "阿尔巴尼亚",
    "BY": "白俄罗斯", "LT": "立陶宛", "LV": "拉脱维亚", "EE": "爱沙尼亚", "FI": "芬兰", "SE": "瑞典", "NO": "挪威",
    "DK": "丹麦", "IS": "冰岛", "MD": "摩尔多瓦", "GE": "格鲁吉亚", "AM": "亚美尼亚", "AZ": "阿塞拜疆", "UZ": "乌兹别克斯坦",
    "TM": "土库曼斯坦", "KG": "吉尔吉斯斯坦", "TJ": "塔吉克斯坦", "AF": "阿富汗", "IQ": "伊拉克", "SY": "叙利亚",
    "JO": "约旦", "IL": "以色列", "LB": "黎巴嫩", "YE": "也门", "OM": "阿曼", "AE": "阿联酋", "QA": "卡塔尔", "KW": "科威特",
    "BD": "孟加拉国", "NP": "尼泊尔", "LK": "斯里兰卡", "MM": "缅甸", "LA": "老挝", "KH": "柬埔寨", "KP": "朝鲜",
    "TW": "中国台湾", "PG": "巴布亚新几内亚", "FJ": "斐济", "TN": "突尼斯", "ML": "马里", "NE": "尼日尔", "TD": "乍得",
    "MR": "毛里塔尼亚", "SN": "塞内加尔", "GN": "几内亚", "CI": "科特迪瓦", "GH": "加纳", "BF": "布基纳法索", "CM": "喀麦隆",
    "CF": "中非", "CG": "刚果（布）", "GA": "加蓬", "SS": "南苏丹", "SO": "索马里", "UG": "乌干达", "TZ": "坦桑尼亚",
    "RW": "卢旺达", "ZM": "赞比亚", "ZW": "津巴布韦", "MZ": "莫桑比克", "MW": "马拉维", "MG": "马达加斯加", "NA": "纳米比亚",
    "BW": "博茨瓦纳", "ER": "厄立特里亚", "DJ": "吉布提", "EH": "西撒哈拉", "AQ": "南极洲",
}


def _rgb(value):
    if isinstance(value, str) and value.startswith("#") and len(value) == 7:
        return tuple(int(value[index:index + 2], 16) for index in (1, 3, 5))
    return value


def _palette(settings):
    theme = settings.get("_inkypi_theme") or {}
    night = theme.get("mode", settings.get("themeMode")) == "night"
    palette = {key: _rgb(value) for key, value in (NIGHT if night else DAY).items()}
    palette["night"] = night
    palette["fuels"] = {name: _rgb(colors[1 if night else 0]) for name, colors in FUEL_COLORS.items()}
    return palette


@lru_cache(maxsize=16)
def _asset(name, height):
    path = ASSETS / f"{name}.png"
    if not path.is_file():
        return None
    with safe_open_image(path, limits=_LIMITS) as source:
        image = source.convert("RGBA")
    return ImageOps.contain(image, (height * 2, height), Image.Resampling.LANCZOS)


@lru_cache(maxsize=16)
def _logo(name, night):
    """A source's official logo at its header height in its published colours, the brand's own
    dark-page version by night when there is one; None without the file."""
    height = SOURCE_LOGOS[name][0]
    for stem in ((f"{name}_night", name) if night else (name,)):
        path = ASSETS / "logos" / f"{stem}.png"
        if path.is_file():
            with safe_open_image(path, limits=_LIMITS) as source:
                image = source.convert("RGBA")
            return image.resize((max(1, round(image.width * height / image.height)), height),
                                Image.Resampling.LANCZOS)
    return None


def _paste(canvas, image, xy, mask=None):
    if image is None:
        return 0
    canvas.image.paste(image, (round(xy[0]), round(xy[1])), mask if mask is not None else
                       (image if image.mode == "RGBA" else None))
    return image.width


def _earth_disc(image, side):
    """EPIC's Earth cropped to its lit disc and scaled to ``side``, transparent outside a smooth
    circle just inside the limb; None without an image."""
    if image is None:
        return None
    rgb = image.convert("RGB")
    box = rgb.convert("L").point(lambda value: 255 if value > 24 else 0).getbbox()
    if box:
        x0, y0, x1, y1 = box
        middle_x, middle_y, radius = (x0 + x1) / 2, (y0 + y1) / 2, max(x1 - x0, y1 - y0) / 2
        rgb = rgb.crop((round(middle_x - radius), round(middle_y - radius), round(middle_x + radius),
                        round(middle_y + radius)))
    disc = rgb.resize((side, side), Image.Resampling.LANCZOS).convert("RGBA")
    scale = 4
    mask = Image.new("L", (side * scale, side * scale), 0)
    ImageDraw.Draw(mask).ellipse((scale, scale, (side - 1) * scale, (side - 1) * scale), fill=255)
    disc.putalpha(mask.resize((side, side), Image.Resampling.LANCZOS))
    return disc


def _rounded(image, radius):
    mask = Image.new("L", image.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, image.width - 1, image.height - 1), radius=radius, fill=255)
    return mask


def _chip(canvas, xy, text, *, size=11, fill="chip", color="chip_ink", anchor="left"):
    """A small solid tag; ``anchor`` says whether ``xy`` is its left or right end. Returns its width."""
    width = canvas.width(text, size) + 12
    x, y = xy
    left = x if anchor == "left" else x - width
    canvas.draw.rounded_rectangle((left, y, left + width, y + size + 7), radius=(size + 7) // 2,
                                  fill=canvas.color(fill))
    canvas.text((left + width / 2, y + (size + 7) / 2), text, size, color, anchor="mm")
    return width


def _card(canvas, key, title, detail="", source=None):
    """A card in its own colour: a band across the top, a frame and header rule, its generated
    icon and title, and at the right its source's official logo (SOURCE_LOGOS) before a detail."""
    left, top, right, _bottom = box = CARDS[key]
    colour = canvas.color(key)
    canvas.draw.rounded_rectangle(box, radius=7, outline=colour, width=2)
    canvas.draw.rounded_rectangle((left, top, right, top + CARD_BAND), radius=7, fill=colour,
                                  corners=(True, True, False, False))
    cursor = left + 8 + _paste(canvas, _asset(SECTION_ART[key], 24), (left + 8, top + 4)) + 6
    cursor += canvas.text((cursor, top + 17), title, 15, anchor="lm") + 8
    logo = _logo(source, canvas.palette["night"]) if source else None
    if source and logo is None:
        detail = " · ".join(part for part in (SOURCE_LOGOS[source][1], detail.lstrip("· ")) if part)
    x = right - 8
    if detail:
        room = x - cursor - (logo.width + 5 if logo is not None else 0)
        x -= canvas.text((x, top + 17), detail, 11, "muted", anchor="rm", bold=False, width=room) + 5
    if logo is not None:
        _paste(canvas, logo, (x - logo.width, top + 17 - logo.height / 2))
    canvas.rule((left + 8, top + HEADER + 2, right - 8, top + HEADER + 2), colour)


def _local_time(iso):
    try:
        return datetime.fromisoformat(str(iso)).astimezone(PACIFIC)
    except (TypeError, ValueError):
        return None


# --- the sky over the Bay ------------------------------------------------------------------------

def _earth(canvas, payload):
    goes, rain, epic = (payload.get(name, {}).get("data") or {} for name in ("goes", "rain", "epic"))
    taken = _local_time(goes.get("taken_at"))
    _card(canvas, "earth", "湾区上空", "GOES-West 卫星云图", source="noaa")
    left, top, right, bottom = CARDS["earth"]
    box = (left + 8, top + HEADER + 6, right - 8, bottom - 6)
    size = (box[2] - box[0], box[3] - box[1])
    picture = decode_image(goes.get("image"))
    if picture is None:
        canvas.draw.rounded_rectangle(box, radius=6, fill=canvas.color("panel"))
        canvas.text(((box[0] + box[2]) / 2, (box[1] + box[3]) / 2), "卫星图暂不可用", 14, "muted", anchor="mm")
        return
    picture = ImageOps.fit(picture, size, Image.Resampling.LANCZOS)
    scale_x, scale_y = size[0] / 366, size[1] / 166
    _paste(canvas, picture, box[:2], _rounded(picture, 6))
    light, dark = (236, 240, 245), (10, 14, 20)
    home = sky_position(*HOME)
    hx, hy = box[0] + home[0] * scale_x, box[1] + home[1] * scale_y
    # The ring the rain check covers, dashed round home.
    radius = sky_pixels(RAIN_RADIUS_KM) * scale_x
    for start in range(0, 360, 15):
        canvas.draw.arc((hx - radius, hy - radius, hx + radius, hy + radius), start, start + 8, fill=light, width=1)
    canvas.text((hx + radius + 3, hy), f"{RAIN_RADIUS_KM} km", 9, light, anchor="lm", bold=False, stroke=dark)
    for label, lat, lon, side in SKY_PLACES:
        x, y = sky_position(lat, lon)
        x, y = box[0] + x * scale_x, box[1] + y * scale_y
        if side is None:
            canvas.text((x, y), label, 10, light, anchor="mm", bold=False, stroke=dark)
            continue
        canvas.draw.ellipse((x - 2.5, y - 2.5, x + 2.5, y + 2.5), fill=light, outline=dark)
        canvas.text((x - 5, y) if side == "left" else (x + 5, y), label, 10, light,
                    anchor="rm" if side == "left" else "lm", stroke=dark)
    # Home: a ringed dot and its label.
    canvas.draw.ellipse((hx - 6, hy - 6, hx + 6, hy + 6), outline=(255, 255, 255), width=2)
    canvas.draw.ellipse((hx - 3, hy - 3, hx + 3, hy + 3), fill=canvas.color("gold"))
    _chip(canvas, (hx + 9, hy - 9), "家", size=10, fill=(18, 26, 32), color=(255, 255, 255))
    if taken is not None:
        _chip(canvas, (box[0] + 6, box[1] + 6), f"GOES-18 · {taken:%H:%M}", fill=(18, 26, 32), color=(255, 255, 255))
    if rain:
        coverage = float(rain.get("coverage") or 0)
        text, fill = ((f"{RAIN_RADIUS_KM} km 内无雨", "dry") if coverage < 0.01 else
                      (f"{RAIN_RADIUS_KM} km 内降雨 {coverage:.0%}", "rain"))
        _chip(canvas, (box[2] - 6, box[1] + 6), text, fill=fill, color=(255, 255, 255), anchor="right")
    # EPIC's whole Earth at the lower right, cut from the black of space and set straight on the map.
    disc = _earth_disc(decode_image(epic.get("image")), 62)
    if disc is not None:
        side = disc.width
        x, y = box[2] - side - 8, box[3] - side - 18
        _paste(canvas, disc, (x, y), disc.getchannel("A"))
        when = str(epic.get("date") or "")[5:].replace("-", "/")
        _chip(canvas, (x + side / 2 + 26, box[3] - 18), f"地球全貌 {when}", size=9, fill=(18, 26, 32),
              color=(255, 255, 255), anchor="right")


# --- California's grid ---------------------------------------------------------------------------

def _minutes(text):
    try:
        hour, minute = (int(part) for part in str(text).split(":")[:2])
    except ValueError:
        return None
    return hour * 60 + minute


def _grid(canvas, payload):
    """The renewable share over the supply mix as a bar and a table (colour, source, a bar for its
    share, GW, per cent), beside today's stacked supply with its numbers on it."""
    grid = payload.get("grid", {}).get("data") or {}
    now = grid.get("now") or {}
    _card(canvas, "grid", "加州电网", f"{now.get('time', '--:--')} · 虚线为预测用电" if now else "", source="caiso")
    left, top, right, bottom = CARDS["grid"]
    if not grid.get("series"):
        canvas.text(((left + right) / 2, (top + bottom) / 2 + 10), "电网数据暂不可用", 14, "muted", anchor="mm")
        return
    supply = sum(float(now.get(name) or 0) for name in FUEL_ORDER) or 1.0
    share = float(grid.get("renewable_share") or 0)
    x, x1 = left + 10, left + 186
    y = top + HEADER + 6
    width = canvas.text((x, y - 2), f"{share:.0%}", 30, "grid", anchor="la")
    canvas.text((x + width + 6, y + 6), "可再生能源", 11, anchor="la")
    canvas.text((x + width + 6, y + 21), f"{share * supply / 1000:.1f} / {supply / 1000:.1f} GW", 9, "muted",
                anchor="la", bold=False)
    # The mix as one bar, then as a table that is also the chart's legend.
    rows = sorted(((name, float(now.get(name) or 0)) for name in FUEL_ORDER), key=lambda row: -row[1])
    bar_y, cursor = y + 38, float(x)
    for name in MIX_ORDER:
        part = (x1 - x) * float(now.get(name) or 0) / supply
        if part > 0:
            canvas.draw.rectangle((cursor, bar_y, cursor + part, bar_y + 7), fill=canvas.palette["fuels"][name])
            cursor += part
    canvas.draw.rectangle((x, bar_y, x1, bar_y + 7), outline=canvas.color("ink"), width=1)
    top_share = max((mw for _name, mw in rows), default=1) / supply or 1
    row_y = bar_y + 15
    for name, mw in [row for row in rows if row[1] > 0][:6]:
        portion = mw / supply
        canvas.draw.rectangle((x, row_y + 1, x + 8, row_y + 9), fill=canvas.palette["fuels"][name])
        canvas.text((x + 12, row_y + 5), FUEL_LABELS[name], 10, anchor="lm")
        bar_x0, bar_x1 = x + 48, x + 100
        canvas.draw.rectangle((bar_x0, row_y + 3, bar_x1, row_y + 7), fill=canvas.color("panel"))
        canvas.draw.rectangle((bar_x0, row_y + 3, bar_x0 + (bar_x1 - bar_x0) * portion / top_share, row_y + 7),
                              fill=canvas.palette["fuels"][name])
        canvas.text((x + 140, row_y + 5), f"{mw / 1000:.1f}", 10, anchor="rm")
        canvas.text((x1, row_y + 5), f"{portion:.0%}", 9, "muted", anchor="rm", bold=False)
        row_y += 15
    canvas.draw.line((x1 + 8, top + HEADER + 8, x1 + 8, bottom - 8), fill=canvas.color("rule"), width=1)
    _supply_chart(canvas, grid, (x1 + 18, top + HEADER + 10, right - 10, bottom - 22))


def _chart_tag(canvas, xy, text, fill, color, box, anchor="mm"):
    """A small solid label on the chart, kept inside ``box``."""
    width = canvas.width(text, 9) + 8
    x, y = xy
    left = min(max(box[0] + 1, x - width / 2 if anchor == "mm" else x), box[2] - width - 1)
    canvas.draw.rounded_rectangle((left, y - 7, left + width, y + 7), radius=3, fill=canvas.color(fill))
    canvas.text((left + width / 2, y), text, 9, color, anchor="mm")


def _supply_chart(canvas, grid, box):
    """Today's supply stacked by source (to now) under the day-ahead demand (dashed), with the
    numbers set on it: supply now at the now line, the demand now as a dot, the evening peak
    at its point on the forecast; the hours beneath."""
    x0, y0, x1, y1 = box
    series = grid.get("series") or []
    forecast = grid.get("forecast") or []
    totals = [sum(point.get(name, 0) for name in FUEL_ORDER) for point in series]
    ceiling = max([*totals, *(item["mw"] for item in forecast), 1]) * 1.22

    def px(minutes):
        return x0 + (x1 - x0) * minutes / 1440

    def py(mw):
        return y1 - (y1 - y0) * mw / ceiling

    canvas.draw.rectangle(box, fill=canvas.color("panel"))
    for hour in (6, 12, 18):
        canvas.draw.line((px(hour * 60), y0, px(hour * 60), y1), fill=canvas.color("rule"), width=1)
    points = [(m, point) for m, point in ((_minutes(point.get("time")), point) for point in series) if m is not None]
    base = [0.0] * len(points)
    for name in FUEL_ORDER:
        top_line = [b + point.get(name, 0) for b, (_m, point) in zip(base, points)]
        if any(value > b for value, b in zip(top_line, base)):
            polygon = [(px(m), py(value)) for (m, _p), value in zip(points, top_line)]
            polygon += [(px(m), py(b)) for (m, _p), b in reversed(list(zip(points, base)))]
            canvas.draw.polygon(polygon, fill=canvas.palette["fuels"][name])
        base = top_line
    line = [(px(m), py(item["mw"])) for m, item in ((_minutes(item.get("time")), item) for item in forecast)
            if m is not None]
    for index in range(0, len(line) - 1, 2):
        canvas.draw.line((*line[index], *line[index + 1]), fill=canvas.color("ink"), width=2)
    canvas.draw.rectangle(box, outline=canvas.color("rule"), width=1)
    if points:
        now_x = px(points[-1][0])
        supply_y = py(base[-1])
        canvas.draw.line((now_x, y0 + 14, now_x, y1), fill=canvas.color("grid"), width=2)
        canvas.draw.ellipse((now_x - 3, supply_y - 3, now_x + 3, supply_y + 3), fill=canvas.color("grid"),
                            outline=canvas.color("paper"))
        _chart_tag(canvas, (now_x, y0 + 8), f"此刻 {base[-1] / 1000:.1f} GW", "grid", (255, 255, 255), box)
        demand = (grid.get("demand") or [{}])[-1].get("mw")
        if demand:
            demand_y = py(demand)
            canvas.draw.ellipse((now_x - 3, demand_y - 3, now_x + 3, demand_y + 3), fill=canvas.color("paper"),
                                outline=canvas.color("ink"), width=2)
    peak = grid.get("peak") or {}
    peak_minutes = _minutes(peak.get("time"))
    if peak and peak_minutes is not None:
        peak_x, peak_y = px(peak_minutes), py(peak["mw"])
        canvas.draw.ellipse((peak_x - 3, peak_y - 3, peak_x + 3, peak_y + 3), fill=canvas.color("ink"))
        _chart_tag(canvas, (peak_x, peak_y - 11), f"晚峰 {peak['mw'] / 1000:.1f} GW {peak.get('time')}", "ink", "paper",
                   box)
    for hour, label in ((0, "0"), (6, "6"), (12, "12"), (18, "18"), (24, "24时")):
        canvas.text((min(px(hour * 60), x1 - 2), y1 + 2), label, 9, "muted", bold=False,
                    anchor="ra" if hour == 24 else "ma" if hour else "la")


# --- what America is searching ------------------------------------------------------------------

def _traffic(value):
    value = int(value or 0)
    if value >= 10000:
        return f"{value // 10000}万+"
    if value >= 1000:
        return f"{value // 1000}千+"
    return f"{value}+" if value else ""


SMALL_WORDS = {"a", "an", "and", "at", "by", "for", "in", "of", "on", "or", "the", "to", "vs"}
ACRONYMS = {"nfl", "nba", "mlb", "nhl", "mls", "epl", "ufc", "wwe", "ncaa", "fc", "cf", "sc", "afc", "nfc", "usa", "us",
            "uk", "ai", "f1", "tv", "fbi", "doj", "cia", "gop", "nasa", "ceo", "ipo", "psg", "ucl", "wnba"}


def _search_title(text):
    """A search as a headline: words capitalised, short joining words lower, known acronyms upper,
    anything already cased or with digits left alone."""
    words = []
    for index, word in enumerate(str(text or "").split()):
        if word in ACRONYMS:
            word = word.upper()
        elif word.islower() and word.isalpha() and (index == 0 or word not in SMALL_WORDS):
            word = word[:1].upper() + word[1:]
        words.append(word)
    return " ".join(words)


def _trends(canvas, payload, images):
    trends = (payload.get("trends", {}).get("data") or {}).get("items") or []
    wiki = (payload.get("wiki", {}).get("data") or {}).get("articles") or []
    songs = (payload.get("apple", {}).get("data") or {}).get("songs") or []
    if songs:
        _card(canvas, "trends", "美国热度", f"热歌 · {songs[0]['name']}", source="apple_music")
    else:
        _card(canvas, "trends", "美国热度", source="google_trends")
    left, top, right, bottom = CARDS["trends"]
    if not trends:
        canvas.text(((left + right) / 2, (top + bottom) / 2), "热搜暂不可用", 14, "muted", anchor="mm")
        return
    # Five searches as a table: rank, picture, the search over its outlet and headline, and the
    # search volume with a bar on a log scale.
    x0, x1 = left + 10, right - 10
    rows = trends[:5]
    top_volume = math.log10(max(1000, *(int(item.get("traffic") or 0) for item in rows)))
    for index, item in enumerate(rows):
        y = top + HEADER + 8 + index * 26
        if index:
            canvas.draw.line((x0, y - 2, x1, y - 2), fill=canvas.color("rule"), width=1)
        canvas.text((x0 + 7, y + 11), str(index + 1), 15, "trends" if index < 3 else "muted", anchor="mm")
        thumb_box = (x0 + 18, y, x0 + 42, y + 24)
        thumb = images.get(item.get("picture"))
        if thumb is not None:
            small = ImageOps.fit(thumb, (24, 24), Image.Resampling.LANCZOS)
            _paste(canvas, small, thumb_box[:2], _rounded(small, 3))
        else:
            canvas.draw.rounded_rectangle(thumb_box, radius=3, fill=canvas.color("panel"))
            canvas.text((x0 + 30, y + 12), (item.get("title") or "?")[:1].upper(), 12, "trends", anchor="mm")
        text_x, column = x0 + 48, x1 - 40
        volume = int(item.get("traffic") or 0)
        if volume:
            canvas.text((x1, y + 7), _traffic(volume), 11, anchor="rm")
            share = max(0.12, math.log10(volume) / top_volume)
            canvas.draw.rectangle((x1 - 34, y + 18, x1, y + 21), fill=canvas.color("panel"))
            canvas.draw.rectangle((x1 - 34, y + 18, x1 - 34 + round(34 * share), y + 21), fill=canvas.color("trends"))
        canvas.text((text_x, y + 7), _search_title(item.get("title")), 12, anchor="lm", width=column - text_x)
        source, headline = item.get("source") or "", item.get("news") or ""
        line_x = text_x
        if source:
            line_x += canvas.text((line_x, y + 20), canvas.fit(source, 9, 72), 9, "trends", anchor="lm") + 4
        if headline:
            canvas.text((line_x, y + 20), headline, 10, "muted", anchor="lm", bold=False, width=column - line_x)
    if wiki:
        # Wikipedia's most-read article on one line under the searches.
        article = wiki[0]
        y = bottom - 28
        canvas.draw.line((x0, y - 4, x1, y - 4), fill=canvas.color("rule"), width=1)
        text_x = x0
        thumb = images.get(article.get("thumbnail"))
        if thumb is not None:
            small = ImageOps.fit(thumb, (22, 22), Image.Resampling.LANCZOS, centering=(0.5, 0.3))
            _paste(canvas, small, (x0, y), _rounded(small, 4))
            text_x += 27
        text_x += _chip(canvas, (text_x, y + 2), "维基热读", size=10, fill="ink", color="paper") + 6
        views = int(article.get("views") or 0)
        count = f"{views / 10000:.1f}万" if views else ""
        count_w = canvas.width(count, 10, False) + 6 if count else 0
        if count:
            canvas.text((x1, y + 11), count, 10, "muted", anchor="rm", bold=False)
        canvas.text((text_x, y + 11), article.get("title") or "", 12, anchor="lm", width=x1 - count_w - text_x)


# --- space ---------------------------------------------------------------------------------------

@lru_cache(maxsize=2)
def _world(width, height, land, sea):
    path = ASSETS / "world_land.png"
    with safe_open_image(path, limits=ImageLimits(max_bytes=512 * 1024, max_width=1024, max_height=512,
                                                   max_pixels=1024 * 512)) as source:
        mask = source.convert("L").resize((width, height), Image.Resampling.LANCZOS)
    return Image.composite(Image.new("RGB", (width, height), land), Image.new("RGB", (width, height), sea), mask)


def _night_map(world, now, night_ink, sun):
    """The world map with the night side shaded towards ``night_ink`` and twilight in between."""
    width, height = world.size
    shade = Image.new("L", (width, height), 0)
    pixels = shade.load()
    for row in range(height):
        lat = 90 - (row + 0.5) * 180 / height
        for column in range(width):
            elevation = sun_elevation(lat, (column + 0.5) * 360 / width - 180, sun)
            pixels[column, row] = 0 if elevation > 0 else 110 if elevation < -6 else round(110 * -elevation / 6)
    return Image.composite(Image.new("RGB", world.size, night_ink), world, shade)


def _photo_shade(canvas, box, top):
    """Darken the photo's top or bottom edge with a gradient for the text laid over it."""
    x0, y0, x1, y1 = (round(value) for value in box)
    height = y1 - y0
    ramp = Image.linear_gradient("L").resize((x1 - x0, height))
    if not top:
        ramp = ramp.transpose(Image.Transpose.FLIP_TOP_BOTTOM)
    ramp = ramp.point(lambda value: round(value * 0.82))
    region = canvas.image.crop((x0, y0, x1, y1))
    canvas.image.paste(Image.composite(region, Image.new("RGB", region.size, (8, 10, 14)), ramp), (x0, y0))


def _mars_facts(canvas, mars, now, box):
    """Text laid into the photo over soft dark gradients: Earth's distance from Mars and the light
    time across it at the top, the sol, rover and crater, camera and Earth date at the bottom."""
    x0, y0, x1, y1 = box
    distance = mars_distance_km(now)
    white, soft = (255, 255, 255), (214, 220, 230)
    _photo_shade(canvas, (x0, y0, x1, y0 + 40), top=True)
    _photo_shade(canvas, (x0, y1 - 62, x1, y1), top=False)
    canvas.text((x0 + 7, y0 + 6), f"距地球 {distance / 1e8:.2f} 亿km", 10, white, anchor="la")
    canvas.text((x0 + 7, y0 + 21), f"光行 {distance / LIGHT_KM_S / 60:.0f} 分钟", 9, soft, anchor="la", bold=False)
    canvas.draw.rounded_rectangle((x0 + 7, y1 - 47, x0 + 10, y1 - 8), radius=1, fill=canvas.color("space"))
    canvas.text((x0 + 15, y1 - 48), f"火星日 {mars.get('sol')}", 13, white, anchor="la", width=x1 - x0 - 20)
    canvas.text((x0 + 15, y1 - 30), "毅力号 · 杰泽罗", 9, soft, anchor="la", bold=False, width=x1 - x0 - 20)
    taken = _local_time(str(mars.get("taken_utc") or "").replace("Z", "") + ("+00:00" if mars.get("taken_utc") else ""))
    camera = str(mars.get("camera") or "").split(" ")[0]
    canvas.text((x0 + 15, y1 - 17), " · ".join(part for part in (camera, f"{taken:%m/%d}" if taken else "") if part), 9,
                soft, anchor="la", bold=False, width=x1 - x0 - 20)


def _space(canvas, payload, images, now):
    mars = payload.get("mars", {}).get("data") or {}
    iss = payload.get("iss", {}).get("data") or {}
    _card(canvas, "space", "太空近况", "· wheretheiss.at", source="nasa")
    left, top, right, bottom = CARDS["space"]
    crew_left = right - 176
    photo_box = (left + 8, top + HEADER + 6, left + 120, bottom - 6)
    size = (photo_box[2] - photo_box[0], photo_box[3] - photo_box[1])
    photo = images.get(mars.get("image"))
    if photo is not None:
        photo = ImageOps.fit(photo, size, Image.Resampling.LANCZOS)
        _paste(canvas, photo, photo_box[:2], _rounded(photo, 6))
        _mars_facts(canvas, mars, now, photo_box)
    else:
        canvas.draw.rounded_rectangle(photo_box, radius=6, fill=canvas.color("panel"))
        canvas.text(((photo_box[0] + photo_box[2]) / 2, (photo_box[1] + photo_box[3]) / 2), "火星照片暂缺", 12, "muted",
                    anchor="mm")
    # The ISS over a world map with the night side shaded: its orbit's ground track, the Sun
    # overhead, home, and when it next crosses into sunlight or the Earth's shadow.
    mx0, my0 = photo_box[2] + 10, photo_box[1]
    mw = crew_left - 10 - mx0
    mh = mw // 2
    land = (190, 196, 204) if canvas.palette["night"] else (196, 204, 214)
    sea = (30, 38, 48) if canvas.palette["night"] else (238, 242, 247)
    night = (6, 10, 16) if canvas.palette["night"] else (92, 102, 120)
    sun = subsolar_point(now)
    world = _night_map(_world(mw, mh, land, sea), now, night, sun)
    _paste(canvas, world, (mx0, my0), _rounded(world, 5))

    def at(lat, lon):
        return mx0 + (lon + 180) / 360 * mw, my0 + (90 - lat) / 180 * mh

    sx, sy = at(*sun)
    canvas.draw.ellipse((sx - 4, sy - 4, sx + 4, sy + 4), fill=canvas.color("gold"), outline=canvas.color("ink"))
    hx, hy = at(*HOME)
    canvas.draw.ellipse((hx - 2.5, hy - 2.5, hx + 2.5, hy + 2.5), fill=canvas.color("paper"), outline=canvas.color("ink"))
    if iss:
        fetched = _local_time(iss.get("time")) or now
        elapsed = max(0.0, (now - fetched).total_seconds() / 60)
        track = iss_ground_track(iss["lat"], iss["lon"], iss_ascending(iss), [minute / 2 for minute in range(-90, 111)])
        # The past 45 minutes thin, the next 55 bold, broken where the track wraps round the map.
        for future in (False, True):
            run = []
            for minute, lat, lon in track:
                if (minute >= 0) != future:
                    continue
                point = at(lat, lon)
                if run and abs(point[0] - run[-1][0]) > mw / 2:
                    if len(run) > 1:
                        canvas.draw.line(run, fill=canvas.color("space"), width=2 if future else 1)
                    run = []
                run.append(point)
            if len(run) > 1:
                canvas.draw.line(run, fill=canvas.color("space"), width=2 if future else 1)
        x, y = at(iss["lat"], iss["lon"])
        canvas.draw.ellipse((x - 6, y - 6, x + 6, y + 6), fill=canvas.color("paper"), outline=canvas.color("space"), width=2)
        canvas.draw.line((x - 9, y, x + 9, y), fill=canvas.color("gold"), width=3)
        canvas.draw.rectangle((x - 2, y - 2, x + 2, y + 2), fill=canvas.color("ink"))
        country = str(iss.get("country") or "")
        place = (COUNTRY_NAMES.get(country, country) if country and country != "??" else
                 ocean_name(iss["lat"], iss["lon"]))
        ty = my0 + mh + 6
        canvas.text((mx0, ty), f"国际空间站 · {'日照中' if iss.get('daylight') else '地影中'}", 12, "space", anchor="la")
        canvas.text((mx0, ty + 17), f"{place}上空", 14, anchor="la", width=mw)
        canvas.text((mx0, ty + 37), f"高度 {iss.get('altitude', 0):.0f} km · {iss.get('velocity', 0):,.0f} km/h", 10,
                    "muted", anchor="la", bold=False, width=mw)
        switch = iss_next_switch(iss, fetched)
        if switch and switch[0] - elapsed >= 1:
            minutes, sunrise = switch[0] - elapsed, switch[1]
            canvas.text((mx0, ty + 52), f"{minutes:.0f} 分钟后{'迎来日出' if sunrise else '进入地影'} · {ISS_PERIOD_MIN:.0f} 分钟一圈",
                        10, anchor="la", bold=False, width=mw)
    _crew(canvas, payload.get("people", {}).get("data") or {}, images, now, (crew_left, my0, right - 8, bottom - 6))


def _avatar(canvas, picture, xy, side, ring):
    x, y = xy
    canvas.draw.ellipse((x - 2, y - 2, x + side + 1, y + side + 1), fill=canvas.color(ring))
    if picture is None:
        canvas.draw.ellipse((x, y, x + side - 1, y + side - 1), fill=canvas.color("panel"))
        return
    face = ImageOps.fit(picture, (side, side), Image.Resampling.LANCZOS, centering=(0.5, 0.25))
    mask = Image.new("L", (side, side), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, side - 1, side - 1), fill=255)
    _paste(canvas, face, (x, y), mask)


def _crew_name(person):
    """The name under a portrait: the Chinese one when Wikipedia has it, else the surname."""
    return person.get("zh_name") or (str(person.get("name") or "").split() or [""])[-1]


def _crew(canvas, people, images, now, box):
    """Everyone in orbit: the count with each country's number by its flag, then each station's
    crew as portraits (the commander's ringed in gold) with a flag badge and a name under each,
    beneath the station's name and its ISS expedition, or its spacecraft and day in orbit."""
    stations = people.get("stations") or []
    if not stations:
        return
    x0, y0, x1, y1 = box
    canvas.draw.line((x0 - 6, y0, x0 - 6, y1), fill=canvas.color("rule"), width=1)
    label_w = canvas.text((x0, y0 + 10), "在轨", 11, "muted", anchor="lm", bold=False)
    canvas.text((x0 + label_w + 3, y0 + 10), str(people.get("total", 0)), 17, "space", anchor="lm")
    tally = Counter(person.get("country") for station in stations for person in station.get("crew") or []
                    if person.get("country"))
    x = x1
    for code, number in reversed(tally.most_common(4)):
        x -= canvas.text((x, y0 + 10), str(number), 10, anchor="rm") + 2
        flag = _asset(f"flags/{code}", 9)
        if flag is not None:
            x -= flag.width
            _paste(canvas, flag, (x, y0 + 6))
        x -= 6
    cell_w, cell_h, side, y = 42, 36, 22, y0 + 22
    per_row = max(1, (x1 - x0) // cell_w)
    for station in stations:
        crew = station.get("crew") or []
        rows = -(-len(crew) // per_row)
        if y + 14 + rows * cell_h > y1 + 2:
            break
        name = STATIONS.get(station.get("station"), str(station.get("station") or ""))
        launched = int(station.get("launched") or 0)
        days = int((now.timestamp() - launched) // 86400 + 1) if launched else 0
        crafts = list(dict.fromkeys(person.get("craft") for person in crew if person.get("craft")))
        expedition = station.get("expedition")
        note = (f"远征 {expedition}" if expedition else
                " · ".join(part for part in (crafts[0] if len(crafts) == 1 else "", f"第{days}天" if days > 0 else "")
                           if part) or f"{len(crew)} 人")
        name_w = canvas.text((x0, y + 6), name, 11, anchor="lm")
        canvas.text((x1, y + 6), note, 10, "muted", anchor="rm", bold=False, width=x1 - x0 - name_w - 6)
        y += 14
        for index, person in enumerate(crew):
            row, column = divmod(index, per_row)
            cell_x, cell_y = x0 + column * cell_w, y + row * cell_h
            ax = cell_x + (cell_w - side) / 2
            _avatar(canvas, images.get(person.get("image")), (ax, cell_y + 2), side,
                    "gold" if person.get("commander") else "paper")
            flag = _asset(f"flags/{person.get('country')}", 8) if person.get("country") else None
            if flag is not None:
                fx, fy = ax + side - flag.width + 4, cell_y + side - 4
                canvas.draw.rectangle((fx - 1, fy - 1, fx + flag.width, fy + flag.height), fill=canvas.color("paper"))
                _paste(canvas, flag, (fx, fy))
            canvas.text((cell_x + cell_w / 2, cell_y + 30), _crew_name(person), 9, anchor="mm", width=cell_w - 2)
        y += rows * cell_h + 2


def render_page(dimensions, payload, settings, now, images):
    now = local_now(now)
    canvas = PageCanvas(_palette(settings), family=settings.get("fontFamily") or "Microsoft YaHei")
    pasted = paste_local_wordmark(canvas.image, ASSETS / "wordmark.png", (12, 4, 222, 50), align="left")
    title_right = pasted[2] if pasted else 16 + canvas.text((16, 27), "环球此刻", 30, anchor="lm")
    subtitle_right = title_right + 12 + canvas.text((title_right + 12, 30), "卫星 · 电网 · 热度 · 太空", 14, "muted",
                                                    anchor="lm", bold=False)
    date_left = 788 - canvas.text((788, 27), f"{now:%m.%d} 周{WEEKDAYS[now.weekday()]} {now:%H:%M}", 16, anchor="rm")
    paste_header_art(canvas.image, ASSETS / "header_art.png", subtitle_right, date_left)
    canvas.rule((12, 52, 788, 52), "ink", 2)
    _earth(canvas, payload)
    _grid(canvas, payload)
    _trends(canvas, payload, images)
    _space(canvas, payload, images, now)
    result = canvas.image
    if tuple(dimensions) != (800, 480):
        result = result.resize(tuple(dimensions), Image.Resampling.LANCZOS)
    return result
