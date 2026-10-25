"""Sky Radar's 800 x 480 view, laid out like the tools controllers and airline operations use.

- A header with the traffic count by phase and Zulu time beside local time.
- An airport board for SFO, SJC and OAK under their logos: the METAR's flight category in its standard colour
  (VFR green, MVFR blue, IFR red, LIFR magenta), wind, visibility or ceiling and temperature,
  with the FAA's status for the airport.
- A radar scope on a chart of the Bay (USGS water and relief, OurAirports runways, airports and
  navaids): range rings, bearing ticks, SFO's extended final approach course, each target
  with its history trail and an ATC data block (callsign; altitude in hundreds of feet and
  ground speed in tens of knots) on a leader line.
- Flight progress strips for the nearest flights: the airline's wordmark, callsign, a phase
  bar and icon, an estimated landing or take-off time for flights to or from the Bay's three
  airports, the route between the two airports' logos (or their countries' flags) with codes
  and cities, type, registration, altitude, speed and distance.
"""

from __future__ import annotations

import json
import math
from datetime import timedelta, timezone
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter

from utils.local_wordmark import paste_local_wordmark
from utils.safe_image import ImageLimits, safe_open_image

from .flight_radar import (
    DEFAULT_CENTER_LABEL, DEFAULT_LATITUDE, DEFAULT_LONGITUDE, DEFAULT_RADIUS_NM, FAA_AIRPORTS, faa_label,
)


ASSETS = Path(__file__).resolve().parent / "assets"
_LOGO_LIMITS = ImageLimits(max_bytes=256 * 1024, max_width=512, max_height=512, max_pixels=512 * 512)
AIRPORT_CITIES = {"SFO": "旧金山", "SJC": "圣何塞", "OAK": "奥克兰"}
CATEGORY_ROLES = {"VFR": "green", "MVFR": "blue", "IFR": "red", "LIFR": "magenta"}
PHASES = {"arrival": ("进港", "blue"), "departure": ("离港", "red"), "ground": ("地面", "plane_ground"),
          "overflight": ("飞越", "amber")}
# The phase icon's aircraft: its bearing (0 = up) and whether a runway line sits under it.
PHASE_ICONS = {"departure": (45, True), "arrival": (135, True), "ground": (90, True), "overflight": (90, False)}
SFO = (37.6213, -122.3790)
# The Bay airports the strips time flights to and from.
BAY_AIRPORTS = {"SFO": SFO, "OAK": (37.7126, -122.2197), "SJC": (37.3639, -121.9289)}
# An arrival flies about this much farther than the straight line (the approach) plus a final minute.
APPROACH_FACTOR, APPROACH_MINUTES = 1.15, 2
# SFO's runways 28L/28R: arrivals fly the extended centreline in from the east-south-east.
SFO_FINAL_BEARING = 118
SFO_FINAL_NM = 14
HEADER_H = 46
BOARD = (16, 52, 784, 92)
SCOPE = (16, 98, 456, 468)
STRIPS = (466, 98, 784, 468)
MAX_DATA_BLOCKS = 10
# The airline wordmark on a strip matches the callsign line's height.
STRIP_LOGO_HEIGHT = 18
# At night, dark logo parts (luminance below NIGHT_INK_BELOW) that are not reds (red channel below
# NIGHT_RED_FROM) - navy, black, deep blue - turn to the theme's ink instead of sitting on a plate.
NIGHT_INK_BELOW = 90
NIGHT_RED_FROM = 120
CHART = ASSETS / "chart"
_CHART_LIMITS = ImageLimits(max_bytes=2 * 1024 * 1024, max_width=2048, max_height=2048, max_pixels=2048 * 2048)
# Places named on the chart, in the page's language, and home.
CHART_PLACES = (("旧金山", 37.7793, -122.4193), ("奥克兰", 37.8044, -122.2712), ("圣何塞", 37.3382, -121.8863),
                ("帕洛阿尔托", 37.4419, -122.1430), ("海沃德", 37.6688, -122.0808), ("圣马特奥", 37.5630, -122.3255),
                ("核桃溪", 37.9101, -122.0652))
HOME = (37.5485, -121.9886)
EXPLICIT_MAPS = {"google", "google_static", "image", "static_image", "local", "local_image", "local_calibrated",
                 "none", "off", "false"}
WHITE = (255, 255, 255)


def render_pro(plugin, image, snapshot, theme, now, settings, device_config):
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, image.width, image.height), fill=theme["bg"])
    _header(plugin, image, draw, snapshot, theme, now)
    _board(plugin, image, draw, snapshot, theme)
    _scope(plugin, image, snapshot, theme, settings, device_config)
    _strips(plugin, image, snapshot, theme, now)


def _fit(draw, text, font, width):
    text = str(text or "")
    if draw.textlength(text, font=font) <= width:
        return text
    while text and draw.textlength(text + "…", font=font) > width:
        text = text[:-1]
    return text.rstrip(" ·-") + "…"


def _phase(plugin, plane, center):
    if plane.get("on_ground"):
        return "ground"
    flow = plugin._aircraft_list_flow(plane, center)
    return flow if flow in ("arrival", "departure") else "overflight"


def _altitude(plugin, plane):
    if plane.get("on_ground"):
        return "地面"
    value = plugin._number(plane.get("altitude_ft"))
    if value is None:
        return "高度 --"
    value = max(0.0, value)  # barometric altitude reads a little below zero on the runway
    return f"FL{round(value / 100):03d}" if value >= 18000 else f"{int(round(value, -2)):,} ft"


# --- header and airport board ------------------------------------------------------------------

def _header(plugin, image, draw, snapshot, theme, now):
    draw.rectangle((0, 0, 800, HEADER_H), fill=theme["header_bg"])
    draw.rectangle((0, HEADER_H - 3, 800, HEADER_H), fill=theme["header_rule"])
    if not paste_local_wordmark(image, ASSETS / "title_wordmark.png", (12, 1, 214, 31), align="left"):
        draw.text((16, 3), "SKY RADAR", font=plugin._font(22, "bold"), fill=theme["header_ink"])
    aircraft = snapshot.get("aircraft") or []
    center = snapshot.get("center") or {}
    label = center.get("label") or DEFAULT_CENTER_LABEL
    source = snapshot.get("source_label") or "ADS-B"
    draw.text((16, 32), f"{label} TRACON · {source} · {len(aircraft)} TRACKS", font=plugin._font(9, "bold"),
              fill=theme["header_muted"])
    zulu = now.astimezone(timezone.utc).strftime("%H%MZ")
    zulu_font = plugin._font(24, "bold")
    draw.text((784, 5), zulu, font=zulu_font, fill=theme["header_ink"], anchor="ra")
    local_x = 784 - draw.textlength(zulu, font=zulu_font) - 10
    draw.text((local_x, 9), now.strftime("%H:%M"), font=plugin._font(13, "bold"), fill=theme["header_ink"], anchor="ra")
    draw.text((local_x, 27), now.strftime("%Z").strip() or "LOCAL", font=plugin._font(9, "bold"),
              fill=theme["header_muted"], anchor="ra")
    warning = str(snapshot.get("warning") or "").upper()
    if warning in ("STALE CACHE", "NO DATA"):
        draw.text((local_x - 60, 27), warning, font=plugin._font(9, "bold"), fill=theme["amber"], anchor="ra")
    # The traffic picture by phase, and the highest flight level, in the middle of the band.
    counts = {"arrival": 0, "departure": 0, "ground": 0, "overflight": 0}
    for plane in aircraft:
        counts[_phase(plugin, plane, center)] += 1
    highest = max((plugin._number(plane.get("altitude_ft")) or 0 for plane in aircraft
                   if not plane.get("on_ground")), default=0)
    x = 238
    font, small = plugin._font(15, "bold"), plugin._font(9, "bold")
    for key in ("arrival", "departure", "ground"):
        name, role = PHASES[key]
        draw.rounded_rectangle((x, 12, x + 4, 34), radius=2, fill=theme[role])
        draw.text((x + 9, 9), str(counts[key]), font=font, fill=theme["header_ink"])
        draw.text((x + 9, 28), name, font=small, fill=theme["header_muted"])
        x += 64
    if highest:
        draw.text((x + 4, 9), f"FL{round(highest / 100):03d}", font=font, fill=theme["header_ink"])
        draw.text((x + 4, 28), "最高高度层", font=small, fill=theme["header_muted"])


def _wind(weather):
    direction, speed, gust = weather.get("wind_dir"), weather.get("wind_kt"), weather.get("gust_kt")
    if speed is None:
        return "风 --"
    if not speed:
        return "静风"
    heading = "VRB" if direction == "VRB" or direction is None else f"{int(direction):03d}°"
    return f"{heading}/{int(speed)}KT" + (f" G{int(gust)}" if gust else "")


def _board(plugin, image, draw, snapshot, theme):
    metar = snapshot.get("metar") or {}
    faa = snapshot.get("airports")
    x0, y0, x1, y1 = BOARD
    gap = 8
    width = (x1 - x0 - gap * (len(FAA_AIRPORTS) - 1)) / len(FAA_AIRPORTS)
    code_font, city_font = plugin._font(14, "bold"), plugin._font(9, "normal")
    chip_font, line_font = plugin._font(9, "bold"), plugin._font(9, "bold")
    for index, code in enumerate(FAA_AIRPORTS):
        bx = x0 + index * (width + gap)
        bx1 = bx + width
        draw.rounded_rectangle((bx, y0, bx1, y1), radius=6, fill=theme["panel"], outline=theme["line"], width=1)
        logo = airport_logo(code, 16, 64)
        if logo is not None:
            logo = on_panel(logo, theme["panel"], theme)
            image.paste(logo, (round(bx + 8), y0 + 5), logo)
        else:
            draw.text((bx + 8, y0 + 3), code, font=code_font, fill=theme["ink"])
        draw.text((bx + 8, y0 + 24), f"K{code} · {AIRPORT_CITIES.get(code, code)}", font=city_font, fill=theme["muted"])
        weather = metar.get(code) or {}
        category = weather.get("category") or ""
        cx = bx + 80
        if category:
            chip_w = draw.textlength(category, font=chip_font) + 12
            draw.rounded_rectangle((cx, y0 + 5, cx + chip_w, y0 + 19), radius=7,
                                   fill=theme[CATEGORY_ROLES.get(category, "muted")])
            draw.text((cx + chip_w / 2, y0 + 12), category, font=chip_font, fill=WHITE, anchor="mm")
        draw.text((cx, y0 + 24), _wind(weather) if weather else "METAR --", font=line_font, fill=theme["ink"])
        # The FAA's word on the airport, and visibility, ceiling and temperature under it.
        if isinstance(faa, dict):
            problem = faa.get(code) or {}
            status = faa_label(problem)
            role = {"closed": "red", "stop": "red", "gdp": "amber", "delay": "amber"}.get(problem.get("kind"), "green")
            status_w = draw.textlength(status, font=chip_font)
            draw.ellipse((bx1 - 8 - status_w - 11, y0 + 8, bx1 - 8 - status_w - 4, y0 + 15), fill=theme[role])
            draw.text((bx1 - 8, y0 + 5), status, font=chip_font, fill=theme["ink"], anchor="ra")
        if weather:
            ceiling = weather.get("ceiling_ft")
            parts = [f"云底 {int(ceiling):,}" if ceiling is not None and ceiling < 5000 else
                     f"能见 {weather.get('visibility') or '--'}SM"]
            if weather.get("temp_c") is not None:
                parts.append(f"{weather['temp_c']:.0f}°C")
            draw.text((bx1 - 8, y0 + 24), " · ".join(parts), font=line_font, fill=theme["muted"], anchor="ra")


# --- the radar scope ---------------------------------------------------------------------------

def _scope(plugin, image, snapshot, theme, settings, device_config):
    draw = ImageDraw.Draw(image)
    x1, y1, x2, y2 = SCOPE
    draw.rounded_rectangle(SCOPE, radius=7, fill=theme["panel"], outline=theme["line"], width=1)
    inner = (x1 + 4, y1 + 4, x2 - 4, y2 - 4)
    cx, cy = (inner[0] + inner[2]) / 2, (inner[1] + inner[3]) / 2 + 4
    radius_px = min(inner[2] - inner[0], inner[3] - inner[1]) / 2 - 12
    center = snapshot.get("center") or {}
    center_lat = plugin._number(center.get("lat")) or DEFAULT_LATITUDE
    center_lon = plugin._number(center.get("lon")) or DEFAULT_LONGITUDE
    radius_nm = plugin._number(snapshot.get("radius_nm")) or DEFAULT_RADIUS_NM
    display_nm = plugin._display_radius(settings, radius_nm)
    mode = str(settings.get("mapMode") or "").strip().lower()
    charted = mode not in EXPLICIT_MAPS and _chart_layer(plugin, image, inner, (center_lat, center_lon), display_nm,
                                                          (cx, cy), radius_px, theme)
    if not charted:
        plugin._draw_map_layer(draw, image, inner, center_lat, center_lon, display_nm, cx, cy, radius_px, theme,
                               settings, device_config)
    _dim_outside(image, inner, (cx, cy), radius_px, theme)
    draw = ImageDraw.Draw(image)
    _rings(plugin, draw, (cx, cy), radius_px, display_nm, theme)
    _final_approach(plugin, draw, (cx, cy), radius_px, display_nm, center_lat, center_lon, theme)

    plotted = []
    for plane in snapshot.get("aircraft") or []:
        point = plugin._project_plane(plane, center_lat, center_lon, display_nm, cx, cy, radius_px)
        if point:
            plotted.append((plane, point))
    label = center.get("label") or DEFAULT_CENTER_LABEL
    overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
    trail_draw = ImageDraw.Draw(overlay)
    for plane, (px, py) in plotted:
        plugin._draw_plane_trail(trail_draw, px, py, plane, cx, cy, label, center_lat, center_lon, display_nm,
                                 radius_px, theme)
    if plotted:
        image.paste(Image.alpha_composite(image.convert("RGBA"), overlay).convert("RGB"))
    draw = ImageDraw.Draw(image)
    for plane, (px, py) in plotted:
        plugin._draw_plane_marker(draw, px, py, plane, theme)
    _data_blocks(plugin, draw, plotted, inner, theme)

    title = plugin._font(10, "bold")
    draw.text((inner[0] + 6, inner[1] + 4), f"{label} TRACON", font=title, fill=theme["ink"],
              stroke_width=2, stroke_fill=theme["panel"])
    draw.text((inner[2] - 6, inner[1] + 4), f"{int(display_nm)} NM", font=title, fill=theme["ink"], anchor="ra",
              stroke_width=2, stroke_fill=theme["panel"])
    _legend(plugin, draw, inner, theme)
    if not plotted:
        draw.text((cx, cy), "NO TRAFFIC", font=plugin._font(16, "bold"), fill=theme["muted"], anchor="mm")


@lru_cache(maxsize=1)
def _chart():
    """(manifest, RGB layers, features) of the bundled chart, or None."""
    try:
        manifest = json.loads((CHART / "chart.json").read_text(encoding="utf-8"))
        features = json.loads((CHART / "features.json").read_text(encoding="utf-8"))
        with safe_open_image(CHART / "chart.png", limits=_CHART_LIMITS) as source:
            layers = source.convert("RGB")
    except (OSError, ValueError):
        return None
    return manifest, layers, features


def _chart_layer(plugin, image, box, center, display_nm, centre, radius, theme):
    """Paint the chart under the scope and draw its runways, airports, navaids and places; False
    when the scope reaches past the bundled chart."""
    chart = _chart()
    if chart is None:
        return False
    manifest, layers, features = chart
    x0, y0, x1, y1 = (int(value) for value in box)
    size = (x1 - x0, y1 - y0)
    scale = radius / display_nm
    lat0, lon0 = center
    cos0 = math.cos(math.radians(lat0))

    def geo(px, py):
        return lat0 + (centre[1] - py) / scale / 60, lon0 + (px - centre[0]) / scale / (60 * cos0)

    bounds = manifest["bounds_wgs84"]
    north, west = geo(x0, y0)
    south, east = geo(x1, y1)
    if west < bounds["west"] or east > bounds["east"] or south < bounds["south"] or north > bounds["north"]:
        return False
    width, height = layers.size
    crop = (round((west - bounds["west"]) / (bounds["east"] - bounds["west"]) * width),
            round((bounds["north"] - north) / (bounds["north"] - bounds["south"]) * height),
            round((east - bounds["west"]) / (bounds["east"] - bounds["west"]) * width),
            round((bounds["north"] - south) / (bounds["north"] - bounds["south"]) * height))
    water, shade, _unused = layers.crop(crop).resize(size, Image.Resampling.BILINEAR).split()
    land = Image.new("RGB", size, theme["panel"])
    hills = Image.new("RGB", size, plugin._blend_color(theme["ink"], theme["panel"], 0.30))
    land = Image.composite(hills, land, shade.point(lambda value: min(255, value * 2)))
    sea = Image.composite(Image.new("RGB", size, theme["map_water"]), land, water)
    shore = ImageChops.subtract(water.point(lambda value: 255 if value > 127 else 0),
                                water.point(lambda value: 255 if value > 127 else 0).filter(ImageFilter.MinFilter(3)))
    sea.paste(theme["map_water_line"], mask=shore)
    image.paste(sea, (x0, y0))
    draw = ImageDraw.Draw(image)

    def at(lat, lon):
        return plugin._project_geo(lat, lon, lat0, lon0, display_nm, centre[0], centre[1], radius)

    label_font, small = plugin._font(8, "bold"), plugin._font(7, "bold")
    for name, lat, lon in CHART_PLACES:
        point = at(lat, lon)
        if point and x0 < point[0] < x1 and y0 < point[1] < y1:
            draw.text(point, name, font=plugin._city_font(8, "normal"), fill=theme["muted"], anchor="mm")
    ocean = at(lat0 - 0.12, lon0 - 0.48)
    if ocean:
        draw.text(ocean, "太平洋", font=plugin._city_font(10, "bold"), fill=theme["map_water_line"], anchor="mm")
    sizes = {"large_airport": 3, "medium_airport": 2, "small_airport": 2}
    kinds = {airport["ident"]: airport for airport in features.get("airports") or []}
    for runway in features.get("runways") or []:
        a, b = at(*runway["ends"][:2]), at(*runway["ends"][2:])
        airport = kinds.get(runway["airport"]) or {}
        if a and b:
            draw.line((*a, *b), fill=theme["ink"], width=sizes.get(airport.get("type"), 2))
    for airport in kinds.values():
        point = at(airport["lat"], airport["lon"])
        if point and x0 < point[0] < x1 and y0 < point[1] < y1:
            label = airport.get("iata") or airport["ident"]
            big = airport.get("type") == "large_airport"
            draw.text((point[0] + 7, point[1] + 5), label, font=plugin._font(9 if big else 8, "bold"), fill=theme["ink"],
                      anchor="la", stroke_width=2, stroke_fill=theme["panel"])
    for navaid in features.get("navaids") or []:
        point = at(navaid["lat"], navaid["lon"])
        if not point or not (x0 < point[0] < x1 and y0 < point[1] < y1):
            continue
        _navaid_symbol(draw, point, navaid["type"], theme)
        label = f"{navaid['ident']} {navaid.get('frequency') or ''}".strip()
        draw.text((point[0] + 7, point[1] - 7), label, font=small, fill=theme["blue"], anchor="ld",
                  stroke_width=2, stroke_fill=theme["panel"])
    home = at(*HOME)
    if home and x0 < home[0] < x1 and y0 < home[1] < y1:
        hx, hy = home
        draw.ellipse((hx - 3, hy - 3, hx + 3, hy + 3), fill=theme["amber"], outline=theme["ink"])
        draw.text((hx + 5, hy), "家", font=plugin._city_font(9, "bold"), fill=theme["ink"], anchor="lm",
                  stroke_width=2, stroke_fill=theme["panel"])
    return True


def _navaid_symbol(draw, point, kind, theme):
    """Chart symbols: a VOR's hexagon (in a square with DME, with three tabs as a VORTAC or
    TACAN), an NDB's dotted ring."""
    x, y = point
    colour = theme["blue"]
    if kind.startswith("NDB"):
        for angle in range(0, 360, 30):
            rad = math.radians(angle)
            draw.point((x + 5 * math.cos(rad), y + 5 * math.sin(rad)), fill=colour)
        draw.ellipse((x - 1.5, y - 1.5, x + 1.5, y + 1.5), fill=colour)
        return
    hexagon = [(x + 5 * math.cos(math.radians(angle)), y + 5 * math.sin(math.radians(angle))) for angle in range(0, 360, 60)]
    draw.polygon(hexagon, outline=colour)
    draw.ellipse((x - 1, y - 1, x + 1, y + 1), fill=colour)
    if kind == "VOR-DME":
        draw.rectangle((x - 6, y - 5, x + 6, y + 5), outline=colour)
    elif kind in ("VORTAC", "TACAN"):
        for angle in (90, 210, 330):
            rad = math.radians(angle)
            draw.line((x + 4 * math.cos(rad), y - 4 * math.sin(rad), x + 8 * math.cos(rad), y - 8 * math.sin(rad)),
                      fill=colour, width=3)


def _dim_outside(image, box, centre, radius, theme):
    """Fade the map outside the scope's circle toward the panel, so the scope reads as round."""
    x0, y0, x1, y1 = (int(value) for value in box)
    region = image.crop((x0, y0, x1, y1))
    faded = Image.blend(region, Image.new("RGB", region.size, theme["panel"]), 0.55)
    mask = Image.new("L", region.size, 255)
    cx, cy = centre[0] - x0, centre[1] - y0
    ImageDraw.Draw(mask).ellipse((cx - radius, cy - radius, cx + radius, cy + radius), fill=0)
    image.paste(Image.composite(faded, region, mask), (x0, y0))


def _rings(plugin, draw, centre, radius, display_nm, theme):
    """Range rings every 5 NM (10 beyond 30) labelled at their north-east, and bearing ticks on
    the outer ring: every 10 degrees, longer and numbered every 30."""
    cx, cy = centre
    step = 5 if display_nm <= 30 else 10
    scale = radius / display_nm
    font = plugin._font(8, "bold")
    nm = step
    while nm < display_nm - 0.1:
        r = nm * scale
        draw.ellipse((cx - r, cy - r, cx + r, cy + r), outline=theme["ring"], width=1)
        draw.text((cx + r * 0.707 + 2, cy - r * 0.707 - 2), str(nm), font=font, fill=theme["muted"], anchor="ld",
                  stroke_width=2, stroke_fill=theme["panel"])
        nm += step
    draw.ellipse((cx - radius, cy - radius, cx + radius, cy + radius), outline=theme["ink"], width=2)
    for bearing in range(0, 360, 10):
        angle = math.radians(bearing)
        dx, dy = math.sin(angle), -math.cos(angle)
        inner = radius - (9 if bearing % 30 == 0 else 4)
        draw.line((cx + dx * inner, cy + dy * inner, cx + dx * radius, cy + dy * radius), fill=theme["ink"], width=1)
        if bearing % 30 == 0:
            text = "N" if bearing == 0 else f"{bearing:03d}"
            draw.text((cx + dx * (radius - 17), cy + dy * (radius - 17)), text, font=font, fill=theme["ink"], anchor="mm",
                      stroke_width=2, stroke_fill=theme["panel"])


def _final_approach(plugin, draw, centre, radius, display_nm, center_lat, center_lon, theme):
    """SFO's extended runway centreline for 28L/R: dashed, ticked every 2 NM, labelled at its end."""
    point = plugin._project_geo(SFO[0], SFO[1], center_lat, center_lon, display_nm, centre[0], centre[1], radius)
    if point is None:
        return
    scale = radius / display_nm
    angle = math.radians(SFO_FINAL_BEARING)
    dx, dy = math.sin(angle), -math.cos(angle)
    colour = theme["blue"]
    for start in range(0, SFO_FINAL_NM * 2, 2):
        a, b = start / 2, min(SFO_FINAL_NM, start / 2 + 0.6)
        draw.line((point[0] + dx * a * scale, point[1] + dy * a * scale, point[0] + dx * b * scale,
                   point[1] + dy * b * scale), fill=colour, width=2)
    for nm in range(2, SFO_FINAL_NM + 1, 2):
        tx, ty = point[0] + dx * nm * scale, point[1] + dy * nm * scale
        draw.line((tx - dy * 3, ty + dx * 3, tx + dy * 3, ty - dx * 3), fill=colour, width=1)
    end = (point[0] + dx * (SFO_FINAL_NM + 1) * scale, point[1] + dy * (SFO_FINAL_NM + 1) * scale)
    draw.text(end, "28L/R", font=plugin._font(8, "bold"), fill=colour, anchor="lm", stroke_width=2,
              stroke_fill=theme["panel"])


def _data_blocks(plugin, draw, plotted, box, theme):
    """ATC data blocks for the nearest airborne targets: the callsign over the altitude in hundreds
    of feet and the ground speed in tens of knots, on a leader line, placed in the first free
    corner around the target."""
    call_font, data_font = plugin._font(9, "bold"), plugin._font(8, "bold")
    airborne = [(plane, point) for plane, point in plotted if not plane.get("on_ground") and plane.get("callsign")]
    airborne.sort(key=lambda item: plugin._number(item[0].get("distance_nm")) or 999)
    placed = []
    for plane, (px, py) in airborne[:MAX_DATA_BLOCKS]:
        callsign = str(plane.get("callsign") or "").strip()[:8]
        altitude = plugin._number(plane.get("altitude_ft"))
        speed = plugin._number(plane.get("speed_kt"))
        data = f"{round(max(0.0, altitude or 0) / 100):03d} {round((speed or 0) / 10):02d}"
        width = max(draw.textlength(callsign, font=call_font), draw.textlength(data, font=data_font)) + 2
        height = 21
        for ox, oy in ((14, -26), (14, 6), (-14 - width, -26), (-14 - width, 6)):
            bx, by = px + ox, py + oy
            block = (bx, by, bx + width, by + height)
            if (block[0] < box[0] + 2 or block[2] > box[2] - 2 or block[1] < box[1] + 16 or block[3] > box[3] - 16
                    or any(not (block[2] < other[0] or block[0] > other[2] or block[3] < other[1] or block[1] > other[3])
                           for other in placed)):
                continue
            anchor_x = bx if ox > 0 else bx + width
            anchor_y = by + height if oy < 0 else by
            draw.line((px, py, anchor_x, anchor_y), fill=theme["ink"], width=1)
            draw.text((bx, by), callsign, font=call_font, fill=theme["ink"], stroke_width=2, stroke_fill=theme["panel"])
            draw.text((bx, by + 11), data, font=data_font, fill=theme["muted"], stroke_width=2,
                      stroke_fill=theme["panel"])
            placed.append(block)
            break


def _legend(plugin, draw, box, theme):
    font = plugin._font(8, "bold")
    x, y = box[0] + 6, box[3] - 12
    for label, role in (("<10K", "plane_low"), ("10-25K", "plane_cruise"), (">25K", "plane_high"),
                        ("GND", "plane_ground")):
        draw.ellipse((x, y - 3, x + 7, y + 4), fill=theme[role], outline=theme["ink"])
        draw.text((x + 10, y), label, font=font, fill=theme["ink"], anchor="lm", stroke_width=2,
                  stroke_fill=theme["panel"])
        x += draw.textlength(label, font=font) + 22
    draw.text((box[2] - 6, y), "呼号 / 高度 地速", font=font, fill=theme["muted"], anchor="rm", stroke_width=2,
              stroke_fill=theme["panel"])


# --- flight progress strips ----------------------------------------------------------------------

def _bundled_logo(folder, code, width, height):
    path = ASSETS / folder / f"{code}.png"
    if not path.is_file():
        return None
    with safe_open_image(path, limits=_LOGO_LIMITS) as source:
        logo = source.convert("RGBA")
    logo.thumbnail((width, height), Image.Resampling.LANCZOS)
    return logo


def on_panel(mark, panel, theme):
    """A logo as drawn straight on ``panel``: on a dark (night) panel its near-black parts take
    the theme's ink so dark lettering stays readable without a plate; its colours stay. Fully
    opaque marks (flags) are left as they are."""
    if mark is None or sum(panel) >= 330 or mark.getchannel("A").getextrema()[0] == 255:
        return mark
    low = mark.convert("L").point(lambda value: 255 if value < NIGHT_INK_BELOW else 0)
    not_red = mark.getchannel("R").point(lambda value: 255 if value < NIGHT_RED_FROM else 0)
    dark = ImageChops.multiply(low, not_red)
    inked = Image.new("RGBA", mark.size, (*theme["ink"][:3], 255))
    result = Image.composite(inked, mark, dark)
    result.putalpha(mark.getchannel("A"))
    return result


@lru_cache(maxsize=64)
def airline_logo(code, width, height):
    """The airline's official wordmark (transparent, bundled) for an ICAO callsign prefix, fitted
    into width x height, or None."""
    code = str(code or "").upper()
    return _bundled_logo("airlines", code, width, height) if len(code) == 3 and code.isalpha() else None


@lru_cache(maxsize=8)
def airport_logo(code, height, max_width):
    """The airport's official logo (transparent, bundled) at ``height``, or None."""
    return _bundled_logo("airports", str(code or "").upper(), max_width, height)


@lru_cache(maxsize=1)
def _route_airports():
    path = ASSETS / "route_airports" / "airports.json"
    try:
        return json.loads(path.read_text(encoding="utf-8")).get("airports") or {}
    except (OSError, ValueError):
        return {}


@lru_cache(maxsize=128)
def route_mark(iata, height, max_width):
    """An airport's bundled logo (Wikidata/Commons) fitted to height x max_width, else its
    country's flag a little smaller, else None."""
    code = str(iata or "").upper()
    if not (len(code) == 3 and code.isalpha()):
        return None
    logo = _bundled_logo("route_airports", code, max_width, height)
    if logo is not None:
        return logo
    country = str((_route_airports().get(code) or {}).get("country") or "")
    return _bundled_logo("route_airports/flags", country, max_width, height - 4) if country.isalpha() else None


def _phase_icon(plugin, draw, box, phase, theme):
    """The phase as a plain icon in its colour: an aircraft climbing, descending, parked or level,
    over a runway line when it is on or near the ground."""
    x0, y0, x1, y1 = box
    _name, role = PHASES[phase]
    bearing, runway = PHASE_ICONS[phase]
    colour = theme[role]
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2 - (2 if runway else 0)
    points = plugin._rotate_plane_points(plugin._material_flight_points(0.95), math.radians(bearing), cx, cy)
    draw.polygon(points, fill=colour)
    if runway:
        draw.line((x0 + 1, y1 - 1, x1 - 1, y1 - 1), fill=colour, width=2)


def _route_line(plugin, image, draw, plane, xy, width, theme, font, small):
    """origin mark + code + city -> destination mark + code + city, ending within ``width``;
    False when the route is unknown."""
    origin, destination = str(plane.get("origin") or "").upper(), str(plane.get("destination") or "").upper()
    if not (origin and destination):
        return False
    x, y = xy
    right = x + width
    parts = []
    for code, city in ((origin, plane.get("origin_city")), (destination, plane.get("destination_city"))):
        name = plugin._localized_city_name(code) or plugin._localized_city_name(city)
        parts.append((code, "" if name == code else name, route_mark(code, 14, 34)))
    arrow_w = 14
    fixed = sum((mark.width + 3 if mark is not None else 0) + draw.textlength(code, font=font) + 3
                for code, _name, mark in parts) + arrow_w
    city_w = max(0, (width - fixed) / 2)
    for index, (code, name, mark) in enumerate(parts):
        if index:
            draw.text((x + 2, y + 8), "→", font=font, fill=theme["muted"], anchor="lm")
            x += arrow_w
        if mark is not None:
            mark = on_panel(mark, theme["panel2"], theme)
            image.paste(mark, (round(x), round(y + 8 - mark.height / 2)), mark)
            x += mark.width + 3
        draw.text((x, y + 8), code, font=font, fill=theme["ink"], anchor="lm")
        x += draw.textlength(code, font=font) + 3
        if name and city_w > 12:
            fitted = _fit(draw, name, small, min(city_w, right - x))
            draw.text((x, y + 8), fitted, font=small, fill=theme["muted"], anchor="lm")
            x += draw.textlength(fitted, font=small) + 2
    return True


def _place(plugin, code, city):
    code = str(code or "").strip().upper()
    name = plugin._localized_city_name(code) if code else plugin._localized_city_name(city)
    if code and name and name != code:
        return f"{name} {code}"
    return name or code


def _route(plugin, plane):
    origin = _place(plugin, plane.get("origin"), plane.get("origin_city"))
    destination = _place(plugin, plane.get("destination"), plane.get("destination_city"))
    if origin and destination:
        return f"{origin} → {destination}"
    return ""


def strip_order(plugin, snapshot):
    """The flights the strips show: airborne ones with a known route first, then the rest of the
    airborne, then those on the ground, nearest first in each."""
    center = snapshot.get("center") or {}

    def key(plane):
        distance = plugin._number(plane.get("distance_nm"))
        return (bool(plane.get("on_ground")), not _route(plugin, plane), distance if distance is not None else 999)

    return sorted(snapshot.get("aircraft") or [], key=key), center


def flight_time(plugin, plane, now):
    """An estimate from position and speed, for flights to or from SFO, OAK or SJC: when an arrival
    should land ("预计 09:31 到"), when a departure took off ("约 09:12 起飞"); "" otherwise.
    ADS-B carries no schedule, so these are estimates and say so."""
    lat, lon = plugin._number(plane.get("lat")), plugin._number(plane.get("lon"))
    speed = plugin._number(plane.get("speed_kt")) or 0
    if lat is None or lon is None or plane.get("on_ground"):
        return ""
    destination = str(plane.get("destination") or "").upper()
    origin = str(plane.get("origin") or "").upper()
    if destination in BAY_AIRPORTS and speed >= 60:
        distance = plugin._distance_nm(lat, lon, *BAY_AIRPORTS[destination])
        minutes = distance * APPROACH_FACTOR / speed * 60 + APPROACH_MINUTES
        if minutes <= 180:
            return f"预计 {(now + timedelta(minutes=minutes)):%H:%M} 到"
    if origin in BAY_AIRPORTS and speed >= 60:
        distance = plugin._distance_nm(lat, lon, *BAY_AIRPORTS[origin])
        # The climb is slower than the speed now: count it at four fifths.
        minutes = distance / (speed * 0.8) * 60 + 1
        if minutes <= 120:
            return f"约 {(now - timedelta(minutes=minutes)):%H:%M} 起飞"
    return ""


def _strips(plugin, image, snapshot, theme, now):
    draw = ImageDraw.Draw(image)
    x0, y0, x1, y1 = STRIPS
    draw.rounded_rectangle(STRIPS, radius=7, fill=theme["panel"], outline=theme["line"], width=1)
    draw.text((x0 + 10, y0 + 6), "FLIGHT STRIPS", font=plugin._font(13, "bold"), fill=theme["ink"])
    planes, center = strip_order(plugin, snapshot)
    draw.text((x1 - 10, y0 + 9), "最近航班 · 航线与时刻", font=plugin._font(9, "bold"), fill=theme["muted"], anchor="ra")
    strip_h, gap, top = 66, 2, y0 + 28
    call_font, route_font = plugin._font(15, "bold"), plugin._city_font(11, "bold")
    meta_font = plugin._font(9, "bold")
    for index, plane in enumerate(planes[:5]):
        sx0, sy0 = x0 + 6, top + index * (strip_h + gap)
        sx1, sy1 = x1 - 6, sy0 + strip_h
        phase = _phase(plugin, plane, center)
        name, role = PHASES[phase]
        draw.rounded_rectangle((sx0, sy0, sx1, sy1), radius=5, fill=theme["panel2"], outline=theme["card_line"], width=1)
        draw.rectangle((sx0 + 1, sy0 + 1, sx0 + 5, sy1 - 1), fill=theme[role])
        callsign = str(plane.get("callsign") or plane.get("hex") or "未知").strip()
        # The airline's wordmark sits on the callsign's line, left of it; the route and the
        # aircraft lines below run from the strip's left edge.
        line_y = sy0 + 13
        logo_left, logo_right = sx0 + 10, sx0 + 74
        logo = airline_logo(callsign[:3], logo_right - logo_left, STRIP_LOGO_HEIGHT)
        if logo is not None:
            logo_x = round((logo_left + logo_right - logo.width) / 2)
            logo = on_panel(logo, theme["panel2"], theme)
            image.paste(logo, (logo_x, round(line_y - logo.height / 2)), logo)
        else:
            draw.text(((logo_left + logo_right) / 2, line_y), callsign[:3], font=plugin._font(14, "bold"),
                      fill=theme["muted"], anchor="mm")
        icon = (sx1 - 28, sy0 + 3, sx1 - 8, sy0 + 23)
        _phase_icon(plugin, draw, icon, phase, theme)
        text_x = sx0 + 82
        lines_x = sx0 + 10
        timing = flight_time(plugin, plane, now)
        timing_font = plugin._city_font(10, "bold")
        timing_w = draw.textlength(timing, font=timing_font) + 8 if timing else 0
        fitted = _fit(draw, callsign, call_font, icon[0] - 6 - text_x - timing_w)
        draw.text((text_x, sy0 + 4), fitted, font=call_font, fill=theme["ink"])
        if timing:
            draw.text((text_x + draw.textlength(fitted, font=call_font) + 8, sy0 + 13), timing, font=timing_font,
                      fill=theme[role], anchor="lm")
        distance = plugin._format_distance(plane.get("distance_nm"))
        draw.text((sx1 - 8, sy0 + 47), distance, font=meta_font, fill=theme["muted"], anchor="ra")
        if not _route_line(plugin, image, draw, plane, (lines_x, sy0 + 26), sx1 - 8 - lines_x, theme,
                           plugin._font(10, "bold"), plugin._city_font(9, "bold")):
            route = f"航线未知 · 应答机 {str(plane.get('hex') or '').upper()}"
            draw.text((lines_x, sy0 + 27), _fit(draw, route, route_font, sx1 - 8 - lines_x), font=route_font,
                      fill=theme["muted"])
        speed = plugin._number(plane.get("speed_kt"))
        pieces = [str(plane.get("type") or "").upper(), str(plane.get("registration") or "").upper(),
                  _altitude(plugin, plane), f"{int(speed)} kt" if speed is not None and not plane.get("on_ground") else ""]
        draw.text((lines_x, sy0 + 46), _fit(draw, " · ".join(piece for piece in pieces if piece), meta_font,
                                            sx1 - 14 - lines_x - draw.textlength(distance, font=meta_font)),
                  font=meta_font, fill=theme["muted"])
    if not planes:
        draw.text(((x0 + x1) / 2, (y0 + y1) / 2), "等待航班数据", font=plugin._font(13, "bold"), fill=theme["muted"],
                  anchor="mm")
