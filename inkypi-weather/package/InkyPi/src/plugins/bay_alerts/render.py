"""800 x 480 Bay Alerts page: hazard map on the left, alerts / quakes / state events on the right."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

from PIL import Image

from utils.flat_icons import draw_icon
from utils.local_wordmark import paste_local_wordmark
from utils.page_canvas import PageCanvas

from .map_view import california_map_size, quake_age_class, render_bay_map, render_california_map
from .sources import PACIFIC, distance_km, local_now, parse_time


# Pure white or black paper and neutral grey rules: an off-white or tinted ground, or a tinted
# grey, dithers into scattered yellow, blue or green dots on the panel.
DAY = {
    "paper": "#ffffff", "panel": "#efede4", "ink": "#141c22", "muted": "#232d35", "rule": "#a0a0a0",
    "water": "#eef3fb", "water_line": "#1f5fbf", "relief": "#141c22", "relief_density": 0.42,
    "fault": "#e02020", "alert": "#d0251f", "quake_day": "#e02020", "quake_recent": "#ffde00",
    # Greens and yellows sit near the panel's native inks so fills stay solid after dithering.
    "home": "#1f5fbf", "ok": "#00af2d", "minor": "#1f5fbf", "moderate": "#ffde00", "badge_ink": "#ffffff",
}
NIGHT = {
    "paper": "#000000", "panel": "#182730", "ink": "#eef2ea", "muted": "#c3cdd2", "rule": "#5a5a5a",
    "water": "#14304a", "water_line": "#7fb8ff", "relief": "#eef2ea", "relief_density": 0.26,
    "fault": "#ff6b6b", "alert": "#ff6b6b", "quake_day": "#ff5a5a", "quake_recent": "#ffd84a",
    "home": "#7fb8ff", "ok": "#6fd38f", "minor": "#7fb8ff", "moderate": "#ffd84a", "badge_ink": "#000000",
}
TITLE_WORDMARK = Path(__file__).resolve().parent / "assets" / "title_wordmark.png"
SEVERITY = {"Extreme": ("极端", "alert"), "Severe": ("严重", "alert"), "Moderate": ("中等", "moderate"),
            "Minor": ("轻微", "minor")}
WEEKDAYS = "一二三四五六日"
COLUMN = 466
RIGHT = 788
NOTABLE_QUAKE = 3.5
NEARBY_EVENT_KM = 120


def _palette(settings):
    theme = settings.get("_inkypi_theme") or {}
    palette = NIGHT if theme.get("mode", settings.get("themeMode")) == "night" else DAY
    return {key: _rgb(value) for key, value in palette.items()}


def _rgb(value):
    if isinstance(value, str) and value.startswith("#") and len(value) == 7:
        return tuple(int(value[index:index + 2], 16) for index in (1, 3, 5))
    return value


def ago(stamp, now):
    seconds = max(0, (now - stamp).total_seconds())
    if seconds < 3600:
        return f"{max(1, int(seconds // 60))} 分钟前"
    if seconds < 86400:
        return f"{int(seconds // 3600)} 小时前"
    return f"{int(seconds // 86400)} 天前"


def until(stamp, now):
    local = stamp.astimezone(PACIFIC)
    day = "今天" if local.date() == now.date() else "明天" if local.date() == (now + timedelta(days=1)).date() \
        else f"{local:%m/%d} 周{WEEKDAYS[local.weekday()]}"
    return f"至 {day} {local:%H:%M}"


def attention_items(payload, now):
    alerts = payload["alerts"]["data"].get("items", [])
    quakes = payload["quakes"]["data"].get("items", [])
    events = payload["events"]["data"].get("items", [])
    home = payload["home"]
    strong = [quake for quake in quakes if quake.get("mag", 0) >= NOTABLE_QUAKE
              and quake_age_class(quake, now) == "day"]
    nearby_fires = [event for event in events if event.get("kind") == "fire"
                    and distance_km(home[0], home[1], event["lat"], event["lon"]) <= NEARBY_EVENT_KM]
    return len(alerts) + len(strong) + len(nearby_fires)


def _header(canvas, payload, now):
    pasted = paste_local_wordmark(canvas.image, TITLE_WORDMARK, (12, 6, 212, 50), align="left")
    if pasted:
        title_right = pasted[2]
    else:
        title_right = 16 + canvas.text((16, 27), "湾区要紧事", 30, anchor="lm")
    canvas.text((title_right + 14, 30), "地震 · 气象预警 · 自然事件", 14, "muted", anchor="lm", bold=False)
    stamp = f"{now:%m.%d} 周{WEEKDAYS[now.weekday()]} {now:%H:%M}"
    date_width = canvas.text((RIGHT, 27), stamp, 16, anchor="rm")
    count = attention_items(payload, now)
    text = "一切正常" if count == 0 else f"{count} 项需留意"
    fill = "ok" if count == 0 else "alert"
    pill_width = canvas.width(text, 15) + 44
    left = RIGHT - date_width - 16 - pill_width
    if count == 0:
        # All clear stays quiet: an outlined pill with ink text.
        canvas.draw.rounded_rectangle((left, 13, left + pill_width, 41), radius=14, fill=canvas.color("paper"),
                                      outline=canvas.color(fill), width=2)
        draw_icon(canvas.draw, "ok", (left + 9, 18), 18, canvas.color(fill), canvas.color("paper"))
        canvas.text((left + 33, 27), text, 15, "ink", anchor="lm")
    else:
        canvas.draw.rounded_rectangle((left, 13, left + pill_width, 41), radius=14, fill=canvas.color(fill))
        draw_icon(canvas.draw, "alert", (left + 9, 18), 18, canvas.color("badge_ink"), canvas.color(fill))
        canvas.text((left + 33, 27), text, 15, "badge_ink", anchor="lm")
    canvas.rule((12, 52, RIGHT, 52), "ink", 2)


# The right column's three cards share COLUMN_TOP..COLUMN_BOTTOM; their heights follow
# what each has to show (plan_column). Every card has a 32 px title row, then rows.
COLUMN_TOP = 60
COLUMN_BOTTOM = 467
CARD_GAP = 6
COLUMN_SPACE = COLUMN_BOTTOM - COLUMN_TOP - 2 * CARD_GAP
ALERT_ROW = 43
QUAKE_ROW = 21
EVENT_ROW = 38
# The event card's California minimap grows with the card up to this width, which
# still leaves room for a line like "12 天前 · 距家 625 km · 13,260 英亩".
EVENT_MAP_MAX_WIDTH = 88
MORE_LINE = 14
MAX_ROWS = {"alerts": 6, "quakes": 10, "events": 6}
CARD_ORDER = ("alerts", "quakes", "events")


def _card_height(name, rows, total):
    """Height of a card showing `rows` of its `total` items."""
    if name == "alerts":
        # No alert (or no data) is one compact line; each alert row is 43 px.
        if rows == 0:
            return 80
        return 79 + ALERT_ROW * (rows - 1) + (MORE_LINE if total > rows else 0)
    if name == "quakes":
        # The three statistics, then one line per quake (or a "none" line).
        return 76 + QUAKE_ROW * max(rows, 1)
    # The California minimap sets the smallest height; two events fit beside it.
    return max(111, 70 + EVENT_ROW * (rows - 1))


def plan_column(counts, space=COLUMN_SPACE):
    """Rows and heights for the alert, quake and event cards.

    Every card starts at its smallest useful size. Alerts come first: each live alert
    gets a row while the cards below keep their smallest size, so a busy warning day
    squeezes quakes and events. The space left goes to quakes and events one row at a
    time, in turn, and whatever is still left pads those two.
    """
    caps = {name: min(counts.get(name, 0), MAX_ROWS[name]) for name in CARD_ORDER}
    rows = {"alerts": min(caps["alerts"], 1), "quakes": min(caps["quakes"], 1), "events": min(caps["events"], 2)}

    def heights():
        return {name: _card_height(name, rows[name], counts.get(name, 0)) for name in CARD_ORDER}

    def grow(name):
        if rows[name] >= caps[name]:
            return False
        rows[name] += 1
        if sum(heights().values()) <= space:
            return True
        rows[name] -= 1
        return False

    while grow("alerts"):
        pass
    growing = ["quakes", "events"]
    while growing:
        growing = [name for name in growing if grow(name)]
    planned = heights()
    spare = max(0, space - sum(planned.values()))
    planned["quakes"] += spare // 2
    planned["events"] += spare - spare // 2
    return rows, planned


def _column_boxes(payload):
    """Card boxes and row counts for this payload's right column."""
    counts = {
        name: 0 if payload[name]["state"] == "unavailable" else len(payload[name]["data"].get("items", []))
        for name in CARD_ORDER
    }
    rows, heights = plan_column(counts)
    boxes, top = {}, COLUMN_TOP
    for name in CARD_ORDER:
        boxes[name] = (COLUMN, top, RIGHT, top + heights[name])
        top += heights[name] + CARD_GAP
    return boxes, rows


def _card(canvas, box, bar, icon, title, detail, source):
    """Outlined card with a coloured accent bar, icon, title and a right-aligned source detail."""
    left, top, right, _bottom = box
    canvas.draw.rounded_rectangle(box, radius=6, outline=canvas.color("rule"), width=1)
    canvas.draw.rectangle((left + 8, top + 8, left + 12, top + 22), fill=canvas.color(bar))
    draw_icon(canvas.draw, icon, (left + 18, top + 7), 16, canvas.color("ink"), canvas.color("paper"))
    width = canvas.text((left + 40, top + 15), title, 15, anchor="lm")
    if source.get("state") == "stale_cache":
        detail = "缓存 · " + detail
    elif source.get("state") == "unavailable":
        detail = "暂不可用"
    canvas.text((right - 8, top + 15), detail, 11, "muted", anchor="rm", bold=False, width=right - left - width - 60)
    canvas.rule((left + 8, top + 28, right - 8, top + 28))


def _alerts_card(canvas, payload, now, box, rows):
    source = payload["alerts"]
    items = source["data"].get("items", [])
    counties = source["data"].get("counties") or payload["counties"]
    _card(canvas, box, "alert", "alert", "气象预警", f"NWS · {len(items)} 条", source)
    top = box[1]
    left, right = box[0] + 10, box[2] - 10
    if source["state"] == "unavailable":
        canvas.text((left, top + 52), "天气局数据暂不可用", 16, "muted", anchor="lm")
        return
    if not items:
        # All clear takes one compact line, leaving the space to the cards below.
        draw_icon(canvas.draw, "ok", (left + 2, top + 37), 32, canvas.color("ok"), canvas.color("paper"))
        canvas.text((left + 44, top + 46), "湾区暂无生效预警", 16, anchor="lm")
        canvas.text((left + 44, top + 66), f"覆盖 {len(counties)} 个县 · 天气局实时数据", 11, "muted",
                    anchor="lm", bold=False)
        return
    for index, alert in enumerate(items[:rows]):
        y = top + 34 + index * ALERT_ROW
        label, tone = SEVERITY.get(alert.get("severity"), ("提示", "minor"))
        icon_color = canvas.color("alert") if tone == "alert" else canvas.color("ink")
        draw_icon(canvas.draw, alert.get("kind"), (left, y + 2), 26, icon_color, canvas.color("paper"))
        badge_ink = (20, 20, 20) if tone == "moderate" else "badge_ink"
        pill_width = canvas.width(label, 12) + 14
        canvas.draw.rounded_rectangle((right - pill_width, y + 1, right, y + 19), radius=9, fill=canvas.color(tone))
        canvas.text((right - pill_width / 2, y + 10), label, 12, badge_ink, anchor="mm")
        canvas.text((left + 34, y + 10), alert.get("name") or alert.get("event"), 16, anchor="lm",
                    width=right - left - 44 - pill_width)
        ends = parse_time(alert.get("ends"))
        detail = (until(ends, now) + " · " if ends else "") + "、".join(alert.get("counties", []))
        canvas.text((left + 34, y + 29), detail, 12, "muted", anchor="lm", bold=False, width=right - left - 34)
    if len(items) > rows:
        canvas.text((right, box[3] - 4), f"另有 {len(items) - rows} 条", 11, "muted", anchor="rb", bold=False)


def listed_quakes(items, rows):
    """The quakes to list, newest first: M2.0 and up before smaller ones fill the rest."""
    notable = [item for item in items if item.get("mag", 0) >= 2.0][:rows]
    chosen = notable + [item for item in items if item.get("mag", 0) < 2.0][:rows - len(notable)]
    return [item for item in items if any(item is pick for pick in chosen)]


def _quakes_card(canvas, payload, now, box, rows):
    source = payload["quakes"]
    data = source["data"]
    items = data.get("items", [])
    radius = data.get("radius_km") or payload["radius_km"]
    _card(canvas, box, "ink", "quake", "地震", f"USGS · 半径 {radius} km", source)
    top = box[1]
    left, right = box[0] + 10, box[2] - 10
    if source["state"] == "unavailable":
        canvas.text((left, top + 58), "地震数据暂不可用", 16, "muted", anchor="lm")
        return
    day_count = sum(1 for item in items if quake_age_class(item, now) == "day")
    strongest = max(items, key=lambda item: item.get("mag", 0), default=None)
    stats = (("24 小时", f"{day_count} 次"), ("7 天", f"{len(items)} 次"),
             ("7 天最大", f"M{strongest['mag']:.1f}" if strongest else "—"))
    gap = 8
    width = (right - left - gap * 2) / 3
    for index, (label, value) in enumerate(stats):
        x = left + index * (width + gap)
        canvas.draw.rounded_rectangle((x, top + 34, x + width, top + 66), radius=5, outline=canvas.color("rule"))
        canvas.text((x + 8, top + 42), label, 10, "muted", anchor="lm", bold=False)
        canvas.text((x + 8, top + 57), value, 16, anchor="lm")
    if not items:
        canvas.text((left, top + 81), f"过去 7 天半径 {radius} km 内没有地震", 12, "muted", anchor="lm", bold=False)
        return
    for index, quake in enumerate(listed_quakes(items, rows)):
        y = top + 81 + index * QUAKE_ROW
        age = quake_age_class(quake, now)
        fill = canvas.color("quake_day" if age == "day" else "quake_recent" if age == "recent" else "panel")
        canvas.draw.rounded_rectangle((left, y - 8, left + 40, y + 8), radius=3, fill=fill)
        # Yellow stays a light ground in both themes, so its label is always dark.
        label_color = "badge_ink" if age == "day" else (20, 20, 20) if age == "recent" else "ink"
        canvas.text((left + 20, y), f"M{quake['mag']:.1f}", 12, label_color, anchor="mm")
        meta = f"{ago(parse_time(quake['time']), now)} · {quake.get('distance_km', 0):.0f} km"
        meta_width = canvas.text((right, y), meta, 11, "muted", anchor="rm", bold=False)
        canvas.text((left + 48, y), quake.get("place"), 12, anchor="lm",
                    width=right - left - 56 - meta_width)


def _events_card(canvas, payload, now, box, rows):
    source = payload["events"]
    items = source["data"].get("items", [])
    detail = "NASA EONET" + (f" · 共 {len(items)} 起" if items else "")
    _card(canvas, box, "moderate", "fire", "加州自然事件", detail, source)
    top = box[1] + 32
    # The minimap fills the card's height as far as its width allows, centred in it.
    space = box[3] - 4 - top
    size = california_map_size(EVENT_MAP_MAX_WIDTH, space)
    mini = render_california_map(size, canvas.palette, items)
    canvas.image.paste(mini, (box[0] + 10, top + (space - size[1]) // 2))
    canvas.image.info["event_map_box"] = (box[0] + 10, top + (space - size[1]) // 2, *size)
    left, right = box[0] + 10 + size[0] + 10, box[2] - 10
    if source["state"] == "unavailable":
        canvas.text((left, top + 30), "事件数据暂不可用", 13, "muted", anchor="lm")
        return
    if not items:
        canvas.text((left, top + 24), "暂无进行中的野火或风暴", 13, anchor="lm")
        canvas.text((left, top + 44), "过去 30 天 · 每 3 小时更新", 11, "muted", anchor="lm", bold=False)
        return
    home = payload["home"]
    for index, event in enumerate(items[:rows]):
        y = top + 10 + index * EVENT_ROW
        draw_icon(canvas.draw, event.get("kind"), (left, y - 7), 15, canvas.color("alert"), canvas.color("paper"))
        canvas.text((left + 21, y), event.get("title"), 13, anchor="lm", width=right - left - 21)
        meta = f"{ago(parse_time(event['date']), now)} · 距家 " \
               f"{distance_km(home[0], home[1], event['lat'], event['lon']):.0f} km"
        if event.get("magnitude") and event.get("unit") == "acres":
            meta += f" · {event['magnitude']:,.0f} 英亩"
        canvas.text((left + 21, y + 16), meta, 11, "muted", anchor="lm", bold=False, width=right - left - 21)


def render_page(dimensions, payload, settings, now):
    now = local_now(now)
    canvas = PageCanvas(_palette(settings), family=settings.get("fontFamily") or "Microsoft YaHei")
    _header(canvas, payload, now)
    map_image = render_bay_map(
        (440, 408), canvas.palette, canvas.font,
        quakes=payload["quakes"]["data"].get("items", []),
        alerts=payload["alerts"]["data"].get("items", []),
        home=payload["home"], home_label=payload["home_label"], now=now,
    )
    canvas.image.paste(map_image, (12, 60))
    canvas.image.info["map_markers"] = map_image.info.get("map_markers", [])
    canvas.draw.rectangle((12, 60, 451, 467), outline=canvas.color("ink"), width=1)
    boxes, rows = _column_boxes(payload)
    _alerts_card(canvas, payload, now, boxes["alerts"], rows["alerts"])
    _quakes_card(canvas, payload, now, boxes["quakes"], rows["quakes"])
    _events_card(canvas, payload, now, boxes["events"], rows["events"])
    canvas.image.info["column_cards"] = {name: {"box": boxes[name], "rows": rows[name]} for name in CARD_ORDER}
    result = canvas.image
    if tuple(dimensions) != (800, 480):
        result = result.resize(tuple(dimensions), Image.Resampling.LANCZOS)
    return result
