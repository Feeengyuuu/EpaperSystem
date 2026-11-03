"""800 x 480 Bay Alerts page: hazard map on the left, alerts / quakes / state events on the right.

The state events are NASA EONET's with CAL FIRE's active incidents in place of EONET's copies of
the same fires, so a fire carries its acres and containment; FIRMS hotspots, when configured,
mark the Bay map."""

from __future__ import annotations

import re
from datetime import timedelta
from pathlib import Path

from PIL import Image

from utils.doodle_backdrop import paint_doodles
from utils.flat_icons import draw_icon
from utils.local_wordmark import paste_local_wordmark
from utils.page_canvas import PageCanvas

from .map_view import (
    alert_tag_size, california_map_size, draw_alert_tag, nearest_fault, paste_logos, quake_age_class,
    quake_chip_colors, render_bay_map, render_california_map, rgb, source_logo, tone_ink,
)
from .sources import (
    PACIFIC, PAGER_LEVELS, air_today, compact_message, distance_km, intensity_roman, local_now, parse_time,
)


# Pure white or black paper and neutral grey rules: an off-white or tinted ground, or a tinted
# grey, dithers into scattered yellow, blue or green dots on the panel.
DAY = {
    "paper": "#ffffff", "panel": "#efede4", "ink": "#141c22", "muted": "#232d35", "rule": "#a0a0a0",
    "water": "#eef3fb", "water_line": "#2554c7", "relief": "#141c22", "relief_density": 0.42,
    "contour": "#ddd0b4", "contour_index": "#a8875a",
    "fault": "#c81e32", "alert": "#c81e32", "quake_day": "#c81e32", "quake_recent": "#e8a90c",
    # Refined crimson, cobalt, emerald and gold rather than the panel's pure inks (owner's choice);
    # home is a metallic gold (the owner's choice) with a dark house on it.
    "home": "#d4a017", "home_ink": "#1f1600", "ok": "#059669", "minor": "#2554c7", "moderate": "#e8a90c",
    "badge_ink": "#ffffff",
}
NIGHT = {
    "paper": "#000000", "panel": "#182730", "ink": "#eef2ea", "muted": "#c3cdd2", "rule": "#5a5a5a",
    "water": "#14304a", "water_line": "#7aa2ff", "relief": "#eef2ea", "relief_density": 0.26,
    "contour": "#3a3428", "contour_index": "#6e5f44",
    "fault": "#ff6b78", "alert": "#ff6b78", "quake_day": "#ff6b78", "quake_recent": "#fbbf24",
    "home": "#ffd24a", "home_ink": "#1f1600", "ok": "#34d399", "minor": "#7aa2ff", "moderate": "#fbbf24",
    "badge_ink": "#000000",
}
TITLE_WORDMARK = Path(__file__).resolve().parent / "assets" / "title_wordmark.png"
# Quiet technical doodles in each card's colour under its rows, by the card's icon: weather-map
# fronts and isobars, seismograph traces, field-map contours with pines and flames.
DOODLES = {icon: Path(__file__).resolve().parent / "assets" / "doodles" / f"{name}.png"
           for icon, name in (("alert", "alerts"), ("quake", "seismic"), ("fire", "wildfire"))}
SEVERITY = {"Extreme": ("极端", "alert"), "Severe": ("严重", "alert"), "Moderate": ("中等", "moderate"),
            "Minor": ("轻微", "minor")}
WEEKDAYS = "一二三四五六日"
COLUMN = 466
RIGHT = 788
NOTABLE_QUAKE = 3.5
NEARBY_EVENT_KM = 120
NEAR_HOME_KM = 50
# A quake this many people reported feeling is listed even when newer ones would fill the rows.
FELT_NOTABLE = 100
# Spare the Air's mark beside its status chip, this tall; its winter mark in these months.
AIR_LOGO_HEIGHT = 32
WINTER_SEASON = (11, 12, 1, 2)
# The cards' source logos, this tall before each header's detail.
CARD_LOGO_HEIGHT = 18
SAME_FIRE_KM = 15


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


def with_fires(payload):
    """The payload with CAL FIRE's incidents merged into the state events: an EONET fire within
    SAME_FIRE_KM of an incident is that incident, and the card lists the incident."""
    fires_source = payload.get("fires") or {"state": "unavailable", "data": {}}
    fires = fires_source["data"].get("items", []) if fires_source.get("state") != "unavailable" else []
    events_source = payload["events"]
    if not fires:
        return payload
    events = events_source["data"].get("items", []) if events_source["state"] != "unavailable" else []
    kept = [event for event in events if event.get("kind") != "fire" or all(
        distance_km(event["lat"], event["lon"], fire["lat"], fire["lon"]) > SAME_FIRE_KM for fire in fires)]
    merged = sorted(fires + kept, key=lambda item: item.get("date") or "", reverse=True)
    state = events_source["state"] if events_source["state"] != "unavailable" else fires_source["state"]
    return {**payload, "events": {**events_source, "state": state, "calfire": True,
                                  "data": {**events_source["data"], "items": merged}}}


def attention_items(payload, now):
    alerts = payload["alerts"]["data"].get("items", [])
    quakes = payload["quakes"]["data"].get("items", [])
    events = payload["events"]["data"].get("items", [])
    home = payload["home"]
    strong = [quake for quake in quakes if quake.get("mag", 0) >= NOTABLE_QUAKE
              and quake_age_class(quake, now) == "day"]
    nearby_fires = [event for event in events if event.get("kind") == "fire"
                    and distance_km(home[0], home[1], event["lat"], event["lon"]) <= NEARBY_EVENT_KM]
    air = spare_the_air(payload, now)
    return len(alerts) + len(strong) + len(nearby_fires) + (1 if air and air["alert"] else 0)


def spare_the_air(payload, now):
    source = payload.get("air") or {}
    if source.get("state") in (None, "unavailable", "disabled"):
        return None
    return air_today(source.get("data"), now)


def air_mark(air, now):
    """Which of Spare the Air's own marks names the status: the orange winter one through its
    wood-burning season (November to February) or on a winter alert, else the blue one."""
    return "spare_the_air_winter" if air.get("winter") or now.month in WINTER_SEASON else "spare_the_air"


def _air_chip(canvas, air, left, right, now):
    """The Air District's Spare the Air status for today from ``left``: the programme's own round
    mark (its colours as printed, straight on the paper), then a chip with the status, an emerald
    outline and ink text on a day without an alert, solid crimson on an alert day (a winter one
    bans burning wood), with tomorrow's alert named when one is called. The mark's absence puts
    the programme's name in the chip."""
    logo = source_logo(air_mark(air, now), AIR_LOGO_HEIGHT)
    name = "" if logo is not None else "Spare the Air · "
    if air["alert"]:
        text = name + "警报 · " + ("今日禁止烧柴" if air.get("winter") else "今日空气不佳")
    else:
        text = name + "今日无警报" + (" · 明日警报" if air.get("tomorrow_alert") else "")
    mark_w = logo.width + 6 if logo is not None else 0
    width = min(right - left - mark_w, canvas.width(text, 12) + (20 if logo is not None else 36))
    if width < 80:
        return False
    if logo is not None:
        canvas.image.paste(logo, (round(left), round(27 - logo.height / 2)), logo)
        logo.close()
    chip_left = left + mark_w
    box = (chip_left, 15, chip_left + width, 39)
    text_left = chip_left + (10 if not name else 28)
    if air["alert"]:
        canvas.draw.rounded_rectangle(box, radius=12, fill=canvas.color("alert"))
        if name:
            draw_icon(canvas.draw, "haze", (chip_left + 8, 19), 16, canvas.color("badge_ink"), canvas.color("alert"))
        canvas.text((text_left, 27), text, 12, "badge_ink", anchor="lm", width=chip_left + width - 6 - text_left)
    else:
        canvas.draw.rounded_rectangle(box, radius=12, fill=canvas.color("paper"), outline=canvas.color("ok"), width=2)
        if name:
            draw_icon(canvas.draw, "haze", (chip_left + 8, 19), 16, canvas.color("ok"), canvas.color("paper"))
        canvas.text((text_left, 27), text, 12, anchor="lm", width=chip_left + width - 6 - text_left)
    return True


def _header(canvas, payload, now):
    pasted = paste_local_wordmark(canvas.image, TITLE_WORDMARK, (12, 6, 212, 50), align="left")
    if pasted:
        title_right = pasted[2]
    else:
        title_right = 16 + canvas.text((16, 27), "湾区要紧事", 30, anchor="lm")
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
    # Between the title and the pill: the Air District's Spare the Air status for today, or the
    # page's subject when the feed has nothing for today.
    air = spare_the_air(payload, now)
    if not air or not _air_chip(canvas, air, title_right + 14, left - 12, now):
        canvas.text((title_right + 14, 30), "地震 · 气象预警 · 自然事件", 14, "muted", anchor="lm", bold=False)
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
# The forecasters' key messages under the alerts, in the weather card: a label row, then each
# message's lines (wrapped, at most KEY_MESSAGE_WRAP each), shown only with KEY_LINES_MIN lines
# to spare once the quakes have two rows.
KEY_HEAD, KEY_LINE = 24, 13
KEY_LINES_MIN, KEY_LINES_MAX, KEY_MESSAGE_WRAP = 2, 6, 2
KEY_TEXT_SIZE = 11


def key_height(lines):
    return KEY_HEAD + KEY_LINE * lines + 2 if lines else 0


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
    squeezes quakes and events. With key messages to show (``counts["key_lines"]``, the lines
    they wrap to), the quakes then get a second row and the messages as many lines as fit
    (none below KEY_LINES_MIN), drawn in the alerts card. The space left goes to quakes and
    events one row at a time, in turn, and whatever is still left pads those two.
    """
    caps = {name: min(counts.get(name, 0), MAX_ROWS[name]) for name in CARD_ORDER}
    caps["key_lines"] = min(counts.get("key_lines", 0), KEY_LINES_MAX)
    rows = {"alerts": min(caps["alerts"], 1), "quakes": min(caps["quakes"], 1), "events": min(caps["events"], 2),
            "key_lines": 0}

    def heights():
        planned = {name: _card_height(name, rows[name], counts.get(name, 0)) for name in CARD_ORDER}
        planned["alerts"] += key_height(rows["key_lines"])
        return planned

    def grow(name, step=1):
        if rows[name] + step > caps[name]:
            return False
        rows[name] += step
        if sum(heights().values()) <= space:
            return True
        rows[name] -= step
        return False

    while grow("alerts"):
        pass
    if caps["key_lines"] >= KEY_LINES_MIN:
        grow("quakes")
        if grow("key_lines", KEY_LINES_MIN):
            while grow("key_lines"):
                pass
    growing = ["quakes", "events"]
    while growing:
        growing = [name for name in growing if grow(name)]
    planned = heights()
    spare = max(0, space - sum(planned.values()))
    planned["quakes"] += spare // 2
    planned["events"] += spare - spare // 2
    return rows, planned


def _column_boxes(payload, key_lines=0):
    """Card boxes and row counts for this payload's right column."""
    counts = {
        name: 0 if payload[name]["state"] == "unavailable" else len(payload[name]["data"].get("items", []))
        for name in CARD_ORDER
    }
    counts["key_lines"] = key_lines
    rows, heights = plan_column(counts)
    boxes, top = {}, COLUMN_TOP
    for name in CARD_ORDER:
        boxes[name] = (COLUMN, top, RIGHT, top + heights[name])
        top += heights[name] + CARD_GAP
    return boxes, rows


def _card(canvas, box, bar, icon, title, detail, source, logos=()):
    """A card in its own colour: a frame, a solid band along the top and the header rule in it,
    then the section icon, title and a right-aligned detail, the sources' ``logos`` before it
    (their names in the detail when a logo is missing)."""
    left, top, right, bottom = box
    colour = canvas.color(bar)
    canvas.draw.rounded_rectangle(box, radius=7, outline=colour, width=2)
    canvas.draw.rounded_rectangle((left, top, right, top + 5), radius=7, fill=colour, corners=(True, True, False, False))
    if icon in DOODLES:
        doodles = (left + 2, top + 30, right - 2, bottom - 2)
        paint_doodles(canvas.image, doodles, DOODLES[icon], colour, radius=5, offset=doodles[:2])
    draw_icon(canvas.draw, icon, (left + 10, top + 7), 18, colour, canvas.color("paper"))
    width = canvas.text((left + 34, top + 16), title, 16, anchor="lm")
    if source.get("state") == "stale_cache":
        detail = "缓存 · " + detail
    elif source.get("state") == "unavailable":
        detail = "暂不可用"
    detail_right = right - 8
    if logos:
        names, logos = logos
        text_width = canvas.width(detail, 11, bold=False) if detail else 0
        paper, ink = rgb(canvas.palette["paper"]), rgb(canvas.palette["ink"])
        placed = paste_logos(canvas.image, logos, right - 8 - text_width - (6 if detail else 0), top + 16,
                             CARD_LOGO_HEIGHT, ink if sum(paper) < sum(ink) else None)
        if placed is None:
            detail = " · ".join(part for part in (names, detail) if part)
    canvas.text((detail_right, top + 16), detail, 11, "muted", anchor="rm", bold=False, width=right - left - width - 60)
    canvas.rule((left + 8, top + 28, right - 8, top + 28), colour)


def _alerts_card(canvas, payload, now, box, rows, key_lines=0):
    source = payload["alerts"]
    items = source["data"].get("items", [])
    counties = source["data"].get("counties") or payload["counties"]
    _card(canvas, box, "alert", "alert", "气象预警", f"{len(items)} 条", source, ("NWS", ("nws",)))
    _key_messages(canvas, payload, box, key_lines, now)
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
    tag_width = alert_tag_size(canvas.font(12))[0]
    for index, alert in enumerate(items[:rows]):
        y = top + 34 + index * ALERT_ROW
        label, tone = SEVERITY.get(alert.get("severity"), ("提示", "minor"))
        # The same tag marks the alert's area on the map.
        draw_alert_tag(canvas.draw, canvas.palette, canvas.font(12), (left, y + 1), index + 1, alert.get("kind"), tone)
        pill_width = canvas.width(label, 12) + 14
        canvas.draw.rounded_rectangle((right - pill_width, y + 1, right, y + 19), radius=9, fill=canvas.color(tone))
        canvas.text((right - pill_width / 2, y + 10), label, 12, tone_ink(tone, canvas.palette), anchor="mm")
        text_left = left + tag_width + 8
        canvas.text((text_left, y + 10), alert.get("name") or alert.get("event"), 16, anchor="lm",
                    width=right - text_left - 10 - pill_width)
        ends = parse_time(alert.get("ends"))
        detail = (until(ends, now) + " · " if ends else "") + "、".join(alert.get("counties", []))
        canvas.text((text_left, y + 29), detail, 12, "muted", anchor="lm", bold=False, width=right - text_left)
    if len(items) > rows:
        canvas.text((right, box[3] - 4 - key_height(key_lines)), f"另有 {len(items) - rows} 条", 11, "muted",
                    anchor="rb", bold=False)


def key_message_wraps(canvas, payload):
    """Each of the forecasters' key messages, shortened and wrapped to the card's width (at most
    KEY_MESSAGE_WRAP lines each); [] without them."""
    source = payload.get("key_messages") or {}
    if source.get("state") in (None, "unavailable", "disabled"):
        return []
    width = RIGHT - 10 - (COLUMN + 10) - 10
    return [canvas.wrap(compact_message(message), KEY_TEXT_SIZE, width, KEY_MESSAGE_WRAP, bold=False)
            for message in (source.get("data") or {}).get("messages") or []]


def allot_key_lines(wraps, lines):
    """How many lines each message gets of ``lines``: one each in order while they last, then the
    rest in order up to each message's own length."""
    allot, left = [0] * len(wraps), lines
    for index in range(len(wraps)):
        if left <= 0:
            break
        allot[index], left = 1, left - 1
    for index, wrapped in enumerate(wraps):
        extra = max(0, min(len(wrapped) - allot[index], left)) if allot[index] else 0
        allot[index], left = allot[index] + extra, left - extra
    return allot


def key_clock(data, now):
    """When the key messages were last updated, as a local clock ("14:28"), from their "Updated at
    228 PM PST" line, else the discussion's issue time."""
    match = re.fullmatch(r"(\d{1,2})(\d{2})\s+([AP])M\s+\w+", str(data.get("updated") or ""))
    if match:
        hour = int(match.group(1)) % 12 + (12 if match.group(3) == "P" else 0)
        return f"{hour:02d}:{match.group(2)}"
    issued = parse_time(data.get("issued"))
    return issued.astimezone(PACIFIC).strftime("%H:%M") if issued else ""


def _key_messages(canvas, payload, box, lines, now):
    """The forecasters' key messages at the foot of the weather card: a label row with the office
    and when they were updated (they are the forecasters' own English), then a bullet per message."""
    if not lines:
        return
    data = (payload.get("key_messages") or {}).get("data") or {}
    left, right = box[0] + 10, box[2] - 10
    top = box[3] - key_height(lines)
    for x in range(left, right, 4):
        canvas.draw.point((x, top + 2), fill=canvas.color("rule"))
    draw_icon(canvas.draw, "comment", (left, top + 7), 14, canvas.color("alert"), canvas.color("paper"))
    canvas.text((left + 19, top + 14), "预报员要点", 12, anchor="lm")
    clock = key_clock(data, now)
    canvas.text((right, top + 14), " · ".join(part for part in ("旧金山湾区预报台", clock and clock + " 更新", "英文原文")
                                              if part), 10, "muted", anchor="rm", bold=False, width=200)
    width = right - left - 10
    y = top + KEY_HEAD
    for message, count in zip(data.get("messages") or [], allot_key_lines(key_message_wraps(canvas, payload), lines)):
        if not count:
            continue
        canvas.draw.ellipse((left + 1, y + 4, left + 5, y + 8), fill=canvas.color("alert"))
        for line in canvas.wrap(compact_message(message), KEY_TEXT_SIZE, width, count, bold=False):
            canvas.text((left + 10, y + 6), line, KEY_TEXT_SIZE, anchor="lm", bold=False)
            y += KEY_LINE


def quake_tone(magnitude):
    """The colour a quake's size earns: red from M3.5, yellow from M2.5, none below."""
    return "alert" if magnitude >= NOTABLE_QUAKE else "moderate" if magnitude >= 2.5 else None


def focus_quake(items, now):
    """The map callout for the week's strongest quake: when, how far from home, which fault."""
    strongest = max(items, key=lambda item: item.get("mag", 0), default=None)
    if strongest is None:
        return None
    # Where it struck first (USGS's place, in Chinese), then when and how far from home.
    lines = [str(strongest["place"])] if strongest.get("place") else []
    lines.append(f"{ago(parse_time(strongest['time']), now)} · 距家 {strongest.get('distance_km', 0):.0f} km")
    fault = nearest_fault(strongest.get("lat"), strongest.get("lon"))
    if fault:
        name, km = fault
        lines.append(f"{name}旁" if km < 1.5 else f"距{name} {km:.0f} km")
    felt = felt_text(strongest)
    if felt:
        lines.append(felt)
    return {"id": strongest.get("id"), "title": "7 天最大", "lines": lines,
            "tone": quake_tone(strongest.get("mag", 0))}


def felt_text(quake):
    """How a quake was felt, from USGS's own reports: "522 人有感 · 烈度 IV" ("Did You Feel It?"
    reports and their highest intensity, else ShakeMap's), with its PAGER alert and a tsunami
    flag when it has them; "" for one nobody reported."""
    parts = []
    if quake.get("felt"):
        parts.append(f"{quake['felt']:,} 人有感")
    intensity = intensity_roman(quake.get("cdi") or quake.get("mmi"))
    if intensity and (quake.get("felt") or quake.get("mmi")):
        parts.append(f"烈度 {intensity}")
    if quake.get("alert") in PAGER_LEVELS:
        parts.append(f"PAGER {PAGER_LEVELS[quake['alert']]}")
    if quake.get("tsunami"):
        parts.append("海啸信息")
    return " · ".join(parts)


def near_home_summary(items, now):
    """The map's near-home box: quakes inside the outer distance ring."""
    near = [item for item in items if item.get("distance_km", NEAR_HOME_KM + 1) <= NEAR_HOME_KM]
    day = sum(1 for item in near if quake_age_class(item, now) == "day")
    lines = [f"{NEAR_HOME_KM} km 内", f"24 小时 {day} 次", f"7 天 {len(near)} 次"]
    if near:
        lines.append(f"最大 M{max(item.get('mag', 0) for item in near):.1f}")
    return lines


def listed_quakes(items, rows):
    """The quakes to list, newest first: M2.0 and up before smaller ones fill the rest, and the
    week's most widely felt quake (FELT_NOTABLE reports or more) always among them."""
    notable = [item for item in items if item.get("mag", 0) >= 2.0][:rows]
    chosen = notable + [item for item in items if item.get("mag", 0) < 2.0][:rows - len(notable)]
    felt = max(items, key=lambda item: item.get("felt") or 0, default=None)
    if rows and felt and (felt.get("felt") or 0) >= FELT_NOTABLE and not any(felt is pick for pick in chosen):
        chosen = chosen[:rows - 1] + [felt]
    return [item for item in items if any(item is pick for pick in chosen)]


def _quakes_card(canvas, payload, now, box, rows):
    source = payload["quakes"]
    data = source["data"]
    items = data.get("items", [])
    radius = data.get("radius_km") or payload["radius_km"]
    _card(canvas, box, "home", "quake", "地震", f"半径 {radius} km", source, ("USGS", ("usgs",)))
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
    # The week's strongest quake is the card's headline: its box takes the colour its size earns,
    # and so does the map's callout for it.
    hero_tone = quake_tone(strongest["mag"] if strongest else 0)
    for index, (label, value) in enumerate(stats):
        x = left + index * (width + gap)
        box_xy = (x, top + 34, x + width, top + 66)
        if index == 2 and hero_tone:
            canvas.draw.rounded_rectangle(box_xy, radius=5, fill=canvas.color(hero_tone))
            ink = tone_ink(hero_tone, canvas.palette)
            canvas.text((x + 8, top + 42), label, 10, ink, anchor="lm", bold=False)
            canvas.text((x + 8, top + 57), value, 20, ink, anchor="lm")
            continue
        canvas.draw.rounded_rectangle(box_xy, radius=5, outline=canvas.color("rule"))
        canvas.text((x + 8, top + 42), label, 10, "muted", anchor="lm", bold=False)
        canvas.text((x + 8, top + 57), value, 20, anchor="lm")
    if not items:
        canvas.text((left, top + 81), f"过去 7 天半径 {radius} km 内没有地震", 12, "muted", anchor="lm", bold=False)
        return
    for index, quake in enumerate(listed_quakes(items, rows)):
        y = top + 81 + index * QUAKE_ROW
        # The same chip marks the quake on the map.
        fill, label_color = quake_chip_colors(quake_age_class(quake, now), canvas.palette)
        canvas.draw.rounded_rectangle((left, y - 8, left + 40, y + 8), radius=3, fill=fill)
        canvas.text((left + 20, y), f"M{quake['mag']:.1f}", 12, label_color, anchor="mm")
        meta = f"{ago(parse_time(quake['time']), now)} · {quake.get('distance_km', 0):.0f} km"
        meta_width = canvas.text((right, y), meta, 11, "muted", anchor="rm", bold=False)
        place_left = left + 48
        if quake.get("felt"):
            # People who felt it, as USGS's "Did You Feel It?" counts them: a small people icon
            # and the count.
            draw_icon(canvas.draw, "users", (place_left, y - 6), 12, canvas.color("ink"), canvas.color("paper"))
            place_left += 14 + canvas.text((place_left + 14, y), f"{quake['felt']:,}人", 11, anchor="lm") + 6
        canvas.text((place_left, y), quake.get("place"), 12, anchor="lm",
                    width=right - place_left - 8 - meta_width)


def _events_card(canvas, payload, now, box, rows):
    source = payload["events"]
    items = source["data"].get("items", [])
    # CAL FIRE's incidents and NASA's EONET events, by their logos.
    names, logos = ("CAL FIRE · EONET", ("calfire", "nasa")) if source.get("calfire") else ("NASA EONET", ("nasa",))
    _card(canvas, box, "moderate", "fire", "加州自然事件", f"共 {len(items)} 起" if items else "", source, (names, logos))
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
        chip = 0
        if event.get("contained") is not None:
            # CAL FIRE's containment as a pill: green once mostly held, gold midway, red early.
            percent = event["contained"]
            tone = "ok" if percent >= 75 else "moderate" if percent >= 25 else "alert"
            label = f"控制 {percent}%"
            chip = canvas.width(label, 10) + 10
            canvas.draw.rounded_rectangle((right - chip, y - 8, right, y + 8), radius=8, fill=canvas.color(tone))
            canvas.text((right - chip / 2, y), label, 10, tone_ink(tone, canvas.palette), anchor="mm")
            chip += 4
        canvas.text((left + 21, y), event.get("title"), 13, anchor="lm", width=right - left - 21 - chip)
        meta = f"{ago(parse_time(event['date']), now)} · 距家 " \
               f"{distance_km(home[0], home[1], event['lat'], event['lon']):.0f} km"
        if event.get("magnitude") and event.get("unit") == "acres":
            meta += f" · {event['magnitude']:,.0f} 英亩"
        canvas.text((left + 21, y + 16), meta, 11, "muted", anchor="lm", bold=False, width=right - left - 21)


def render_page(dimensions, payload, settings, now):
    now = local_now(now)
    payload = with_fires(payload)
    canvas = PageCanvas(_palette(settings), family=settings.get("fontFamily") or "Microsoft YaHei")
    _header(canvas, payload, now)
    boxes, rows = _column_boxes(payload, sum(len(lines) for lines in key_message_wraps(canvas, payload)))
    quakes = payload["quakes"]["data"].get("items", []) if payload["quakes"]["state"] != "unavailable" else []
    alerts = []
    for index, alert in enumerate(payload["alerts"]["data"].get("items", [])):
        tone = SEVERITY.get(alert.get("severity"), ("提示", "minor"))[1]
        alerts.append({**alert, "tone": tone, "number": index + 1 if index < rows["alerts"] else None})
    map_image = render_bay_map(
        (440, 408), canvas.palette, canvas.font, quakes=quakes, alerts=alerts,
        home=payload["home"], home_label=payload["home_label"], now=now,
        listed=[quake.get("id") for quake in listed_quakes(quakes, rows["quakes"])],
        focus=focus_quake(quakes, now), summary=near_home_summary(quakes, now),
        fires=[event for event in payload["events"]["data"].get("items", []) if event.get("kind") == "fire"],
        hotspots=((payload.get("hotspots") or {}).get("data") or {}).get("items", []),
    )
    canvas.image.paste(map_image, (12, 60))
    for key in ("map_markers", "alert_tags", "map_callout", "map_summary", "home_rings", "fire_marks"):
        canvas.image.info[key] = map_image.info.get(key)
    canvas.draw.rectangle((12, 60, 451, 467), outline=canvas.color("ink"), width=1)
    _alerts_card(canvas, payload, now, boxes["alerts"], rows["alerts"], rows["key_lines"])
    _quakes_card(canvas, payload, now, boxes["quakes"], rows["quakes"])
    _events_card(canvas, payload, now, boxes["events"], rows["events"])
    canvas.image.info["column_cards"] = {name: {"box": boxes[name], "rows": rows[name]} for name in CARD_ORDER}
    result = canvas.image
    if tuple(dimensions) != (800, 480):
        result = result.resize(tuple(dimensions), Image.Resampling.LANCZOS)
    return result
