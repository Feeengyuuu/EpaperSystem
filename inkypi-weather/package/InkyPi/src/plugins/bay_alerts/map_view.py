"""Offline Bay Area hazard map: water-lined coast, active faults, quakes, alert areas.

The layered raster in assets/ is a one-time USGS public-domain export (see the JSON
manifests); the NWS alert zones and named fault traces beside it are one-time exports
too (tools/build_bay_alerts_overlays.py). Rendering never contacts a map service; markers
sit on their true coordinates and anything outside the map is reported rather than clamped.
"""

from __future__ import annotations

import json
import math
from datetime import timedelta
from pathlib import Path

from PIL import Image, ImageChops, ImageColor, ImageDraw, ImageFilter

from utils.flat_icons import draw_icon
from utils.safe_image import ImageLimits, safe_open_image

from .sources import BAY_COUNTIES, parse_time


ASSETS = Path(__file__).resolve().parent / "assets"
BAY_MAP = "bay-alerts-map"
CA_MAP = "california-map"
ZONES = "bay-alerts-zones"
FAULTS = "bay-alerts-faults"
_OVERLAY_BYTES = 256 * 1024
# NWS county zone of each Bay county ("Alameda" -> "CAC001"), for alerts that name no zone.
COUNTY_ZONES = {name: "CAC" + code[3:] for code, name in BAY_COUNTIES.items()}
# Shore hazards mark only the land this many pixels (~1.2 km) from the water.
COASTAL_WORDS = ("coastal", "surf", "beach", "rip current", "tsunami", "sneaker wave")
SHORE_BAND = 5
HOME_RINGS_KM = (10, 25, 50)
NEAR_FAULT_KM = 8
SHOCK_MAGNITUDE = 3.0
# The map legend's source logos (USGS, NWS), this tall.
LEGEND_LOGO_HEIGHT = 13
EDGE_GROUP_PX = 40
# Where the near-home box may go, in order: open ocean off the Peninsula, then the top-left corner.
SUMMARY_CORNERS = tuple((4, top) for top in range(176, 300, 6)) + ((4, 4),)
_RADIUS = 6378137.0
_LIMITS = ImageLimits(max_bytes=512 * 1024, max_width=1024, max_height=1024, max_pixels=1024 * 1024)
# 4x4 Bayer thresholds: relief becomes an ordered stipple the panel shows without noise.
_BAYER = (0, 8, 2, 10, 12, 4, 14, 6, 3, 11, 1, 9, 15, 7, 13, 5)
# Place labels in priority order: (name, lat, lon, font size). Lower ones are skipped
# when they would collide with a marker or an earlier label.
CITIES = (
    ("旧金山", 37.7749, -122.4194, 12), ("奥克兰", 37.8044, -122.2712, 12), ("圣何塞", 37.3382, -121.8863, 12),
    ("伯克利", 37.8716, -122.2727, 12), ("弗里蒙特", 37.5485, -121.9886, 12), ("海沃德", 37.6688, -122.0808, 12),
    ("圣马特奥", 37.5630, -122.3255, 12), ("帕洛阿尔托", 37.4419, -122.1430, 12), ("山景城", 37.3861, -122.0839, 12),
    ("核桃溪", 37.9101, -122.0652, 12), ("瓦列霍", 38.1041, -122.2566, 12), ("圣拉斐尔", 37.9735, -122.5311, 12),
    ("利弗莫尔", 37.6819, -121.7680, 12), ("半月湾", 37.4636, -122.4286, 12), ("康科德", 37.9780, -122.0311, 12),
    ("里士满", 37.9358, -122.3477, 12),
    ("红木城", 37.4852, -122.2364, 10), ("普莱森顿", 37.6624, -121.8747, 10), ("圣拉蒙", 37.7799, -121.9780, 10),
    ("米尔皮塔斯", 37.4323, -121.8996, 10), ("圣利安德罗", 37.7249, -122.1561, 10),
    ("南旧金山", 37.6547, -122.4077, 10), ("森尼韦尔", 37.3688, -122.0363, 10), ("马丁内斯", 38.0194, -122.1341, 10),
    ("索萨利托", 37.8591, -122.4853, 10), ("太平洋城", 37.6138, -122.4869, 10), ("都柏林", 37.7022, -121.9358, 10),
)
# Bridge decks as (name, [(lat, lon), ...]); each was checked to span water on the map.
# Only the best-known ones are named; the northern ones would crowd the Carquinez towns.
NAMED_BRIDGES = {"金门大桥", "海湾大桥", "圣马特奥大桥", "邓巴顿大桥"}
BRIDGES = (
    ("金门大桥", ((37.8100, -122.4775), (37.8330, -122.4800))),
    ("海湾大桥", ((37.7885, -122.3885), (37.8095, -122.3640), (37.8255, -122.3050))),
    ("里士满大桥", ((37.9500, -122.5000), (37.9280, -122.3900))),
    ("圣马特奥大桥", ((37.5720, -122.2800), (37.6250, -122.1100))),
    ("邓巴顿大桥", ((37.4985, -122.1180), (37.5130, -122.0560))),
    ("卡奎内斯大桥", ((38.0587, -122.2230), (38.0695, -122.2240))),
    ("本尼西亚大桥", ((38.0270, -122.1160), (38.0510, -122.1370))),
)
AIRPORTS = (("SFO", 37.6213, -122.3790), ("OAK", 37.7126, -122.2197), ("SJC", 37.3639, -121.9289))
# Fault names: (name, lat, lon near the trace, side). The label snaps to the nearest
# mapped fault trace, follows its direction and sits beside it on `side` (+1 / -1).
FAULT_LABELS = (
    ("圣安德烈亚斯断层", 37.56, -122.40, 1),
    ("海沃德断层", 37.78, -122.17, -1),
    ("卡拉韦拉斯断层", 37.60, -121.87, -1),
)
EDGE_MARKERS_MAX = 4


def read_manifest(name):
    path = ASSETS / f"{name}.json"
    if path.stat().st_size > 8192:
        raise ValueError("map manifest exceeds its size bound")
    manifest = json.loads(path.read_text(encoding="utf-8"))
    bounds = manifest.get("mercator_bounds", {})
    if (manifest.get("schema") != 1 or manifest.get("projection") != "EPSG:3857"
            or any(not isinstance(bounds.get(key), (int, float)) or not math.isfinite(bounds[key])
                   for key in ("xmin", "ymin", "xmax", "ymax"))
            or bounds["xmin"] >= bounds["xmax"] or bounds["ymin"] >= bounds["ymax"]):
        raise ValueError("map manifest has invalid projected bounds")
    return manifest


def read_overlay(name):
    """A bounded JSON overlay (alert zones or fault traces) from assets/."""
    path = ASSETS / f"{name}.json"
    if path.stat().st_size > _OVERLAY_BYTES:
        raise ValueError("map overlay exceeds its size bound")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("schema") != 1:
        raise ValueError("map overlay has an unknown schema")
    return data


def project(lat, lon, manifest, size, *, clip=True):
    """Pixel for a coordinate on this map, or None when missing or (with clip) outside it."""
    try:
        lat, lon = float(lat), float(lon)
    except (TypeError, ValueError):
        return None
    if not (math.isfinite(lat) and math.isfinite(lon) and -85 < lat < 85 and -180 <= lon <= 180):
        return None
    bounds = manifest["mercator_bounds"]
    x = _RADIUS * math.radians(lon)
    y = _RADIUS * math.log(math.tan(math.pi / 4 + math.radians(lat) / 2))
    if clip and not (bounds["xmin"] <= x <= bounds["xmax"] and bounds["ymin"] <= y <= bounds["ymax"]):
        return None
    return ((x - bounds["xmin"]) / (bounds["xmax"] - bounds["xmin"]) * size[0],
            (bounds["ymax"] - y) / (bounds["ymax"] - bounds["ymin"]) * size[1])


def _layers(name, manifest):
    with safe_open_image(ASSETS / f"{name}.png", limits=_LIMITS) as source:
        if list(source.size) != manifest["image_size"]:
            raise ValueError("map raster does not match its manifest")
        return source.convert("RGB").split()


def _stipple(shade, density):
    tile = Image.new("L", (4, 4))
    tile.putdata([int((value + 0.5) * 16) for value in _BAYER])
    bayer = Image.new("L", shade.size)
    for top in range(0, shade.size[1], 4):
        for left in range(0, shade.size[0], 4):
            bayer.paste(tile, (left, top))
    scaled = shade.point(lambda value: int(value * density))
    return ImageChops.subtract(scaled, bayer).point(lambda value: 255 if value > 0 else 0)


def _overlaps(first, second):
    return first[0] < second[2] and first[2] > second[0] and first[1] < second[3] and first[3] > second[1]


def quake_radius(magnitude):
    return max(2.5, 2.5 + (float(magnitude) - 1.5) * 2.4)


def quake_age_class(quake, now):
    stamp = parse_time(quake.get("time"))
    if stamp is None:
        return "old"
    age = now - stamp
    return "day" if age <= timedelta(hours=24) else "recent" if age <= timedelta(days=3) else "old"


def _draw_bridges(draw, palette, manifest, size):
    """Bridge decks as ink lines with a paper edge, so they read across the water."""
    decks = []
    for name, points in BRIDGES:
        pixels = [project(lat, lon, manifest, size) for lat, lon in points]
        if any(pixel is None for pixel in pixels):
            continue
        draw.line(pixels, fill=palette["paper"], width=4)
        draw.line(pixels, fill=palette["ink"], width=2)
        decks.append((name, pixels))
    return decks


def _draw_plane(draw, x, y, color):
    """A 12 px plane seen from above, nose up."""
    draw.polygon([(x, y - 6), (x + 1.5, y - 4), (x + 1.5, y - 1), (x + 6, y + 1.5), (x + 6, y + 3),
                  (x + 1.5, y + 1.5), (x + 1.5, y + 4), (x + 3, y + 5.5), (x + 3, y + 6.5), (x, y + 5.5),
                  (x - 3, y + 6.5), (x - 3, y + 5.5), (x - 1.5, y + 4), (x - 1.5, y + 1.5), (x - 6, y + 3),
                  (x - 6, y + 1.5), (x - 1.5, y - 1), (x - 1.5, y - 4)], fill=color)


def _fault_frame(faults, anchor, size, search=30, reach=24):
    """Nearest mapped fault pixel to `anchor` and the trace's direction there (unit vector)."""
    pixels = faults.load()
    ax, ay = int(anchor[0]), int(anchor[1])
    best = None
    for y in range(max(0, ay - search), min(size[1], ay + search + 1)):
        for x in range(max(0, ax - search), min(size[0], ax + search + 1)):
            if pixels[x, y]:
                distance = (x - anchor[0]) ** 2 + (y - anchor[1]) ** 2
                if best is None or distance < best[0]:
                    best = (distance, x, y)
    if best is None:
        return None
    _distance, fx, fy = best
    nearby = [(x, y) for y in range(max(0, fy - reach), min(size[1], fy + reach + 1))
              for x in range(max(0, fx - reach), min(size[0], fx + reach + 1))
              if pixels[x, y] and (x - fx) ** 2 + (y - fy) ** 2 <= reach * reach]
    if len(nearby) < 6:
        return None
    mean_x = sum(x for x, _ in nearby) / len(nearby)
    mean_y = sum(y for _, y in nearby) / len(nearby)
    sxx = sum((x - mean_x) ** 2 for x, _ in nearby)
    syy = sum((y - mean_y) ** 2 for _, y in nearby)
    sxy = sum((x - mean_x) * (y - mean_y) for x, y in nearby)
    angle = 0.5 * math.atan2(2 * sxy, sxx - syy)
    return (fx, fy), (math.cos(angle), math.sin(angle))


def _draw_rotated_label(image, text, font_2x, centre, direction, fill, halo):
    """Text along `direction`, drawn at 2x and thresholded so it stays solid ink on the panel.

    Returns small boxes along the text line for label collision checks.
    """
    left, top, right, bottom = font_2x.getbbox(text)
    width, height = right - left + 16, bottom - top + 16
    body, ring = Image.new("L", (width, height), 0), Image.new("L", (width, height), 0)
    origin = (8 - left, 8 - top)
    ImageDraw.Draw(ring).text(origin, text, font=font_2x, fill=255, stroke_width=5, stroke_fill=255)
    ImageDraw.Draw(body).text(origin, text, font=font_2x, fill=255)
    degrees = -math.degrees(math.atan2(direction[1], direction[0]))
    if degrees > 90:
        degrees -= 180
    elif degrees < -90:
        degrees += 180
    layers = []
    for layer in (ring, body):
        turned = layer.rotate(degrees, resample=Image.Resampling.BICUBIC, expand=True)
        half = (max(1, turned.width // 2), max(1, turned.height // 2))
        layers.append(turned.resize(half, Image.Resampling.LANCZOS).point(lambda value: 255 if value >= 110 else 0))
    ring, body = layers
    x0, y0 = round(centre[0] - ring.width / 2), round(centre[1] - ring.height / 2)
    image.paste(halo, (x0, y0), ring)
    image.paste(fill, (x0, y0), body)
    length = (right - left) / 2
    return [(centre[0] + direction[0] * step - 6, centre[1] + direction[1] * step - 6,
             centre[0] + direction[0] * step + 6, centre[1] + direction[1] * step + 6)
            for step in range(int(-length / 2), int(length / 2) + 1, 8)]


def _draw_fault_labels(image, faults, palette, fonts, manifest, size):
    boxes = []
    for name, lat, lon, side in FAULT_LABELS:
        anchor = project(lat, lon, manifest, size)
        frame = _fault_frame(faults, anchor, size) if anchor is not None else None
        if frame is None:
            continue
        (fx, fy), (dx, dy) = frame
        centre = (fx - dy * side * 9, fy + dx * side * 9)
        boxes.extend(_draw_rotated_label(image, name, fonts(20), centre, (dx, dy),
                                         palette["fault"], palette["paper"]))
    return boxes


def _free_box(width, height, corners, occupied, frame):
    """The first box of this size, by its top-left corner in `corners`, inside `frame` and clear of `occupied`."""
    for x, y in corners:
        box = (x, y, x + width, y + height)
        if (box[0] >= frame[0] and box[1] >= frame[1] and box[2] <= frame[2] and box[3] <= frame[3]
                and not any(_overlaps(box, other) for other in occupied)):
            return box
    return None


def _around(x, y, width, height, gaps):
    """Top-left corners that put a box beside the point (x, y), nearest first."""
    corners = []
    for gap in gaps:
        corners += [(x + gap, y - height / 2), (x - gap - width, y - height / 2), (x + gap, y - gap - height),
                    (x - gap - width, y - gap - height), (x + gap, y + gap), (x - gap - width, y + gap),
                    (x - width / 2, y - gap - height), (x - width / 2, y + gap)]
    return corners


def _clamped(corner, width, height, frame):
    x = min(max(corner[0], frame[0]), frame[2] - width)
    y = min(max(corner[1], frame[1]), frame[3] - height)
    return (x, y, x + width, y + height)


def _leader(draw, point, box, colour, gap=0):
    """A short line from a marker's rim (`gap` from its centre) to the nearest edge of its label box."""
    x = min(max(point[0], box[0]), box[2])
    y = min(max(point[1], box[1]), box[3])
    length = math.hypot(x - point[0], y - point[1])
    if length > gap + 4:
        start = (point[0] + (x - point[0]) * gap / length, point[1] + (y - point[1]) * gap / length)
        draw.line((start[0], start[1], x, y), fill=colour, width=1)


def quake_chip_colors(age, palette):
    """Fill and label colour of a quake's magnitude chip: the same on the map and in the quake list."""
    if age == "day":
        return palette["quake_day"], palette["badge_ink"]
    if age == "recent":
        # Yellow stays a light ground in both themes, so its label is always dark.
        return palette["quake_recent"], (20, 20, 20)
    return palette["panel"], palette["ink"]


def tone_ink(tone, palette):
    """Label colour on a severity colour: dark on yellow, the badge ink otherwise."""
    return (20, 20, 20) if tone == "moderate" else palette["badge_ink"]


def alert_tag_size(font):
    return round(26 + font.getlength("0")), 18


def draw_alert_tag(draw, palette, font, xy, number, kind, tone):
    """The alert's tag: its hazard icon and number on the severity colour. The card row and the
    alert's area on the map carry the same tag."""
    width, height = alert_tag_size(font)
    x, y = xy
    fill, ink = palette[tone], tone_ink(tone, palette)
    draw.rounded_rectangle((x, y, x + width, y + height), radius=5, fill=fill)
    draw_icon(draw, kind, (x + 4, y + 3), 12, ink, fill)
    draw.text((x + width - 6, y + height / 2), str(number), font=font, fill=ink, anchor="rm")
    return (x, y, x + width, y + height)


def _alert_mask(alert, zones, manifest, size):
    """Where an alert applies: its own polygon, else its NWS zones, else its whole counties."""
    if alert.get("polygon"):
        shapes = [[alert["polygon"]]]
    else:
        ids = [zone for zone in alert.get("zones") or [] if zone in zones]
        if not ids:
            ids = [COUNTY_ZONES[name] for name in alert.get("counties") or [] if COUNTY_ZONES.get(name) in zones]
        shapes = [polygon for zone in ids for polygon in zones[zone]["polygons"]]
    mask = Image.new("L", size, 0)
    for polygon in shapes:
        rings = [[project(lat, lon, manifest, size, clip=False) for lon, lat in ring] for ring in polygon]
        rings = [ring for ring in rings if len(ring) >= 3 and None not in ring]
        if not rings:
            continue
        # A ring with holes is drawn on its own, so a hole never erases another zone.
        layer = mask if len(rings) == 1 else Image.new("L", size, 0)
        layer_draw = ImageDraw.Draw(layer)
        for index, ring in enumerate(rings):
            layer_draw.polygon(ring, fill=0 if index else 255)
        if layer is not mask:
            mask = ImageChops.lighter(mask, layer)
    return mask


def _hatch_lines(size, direction, spacing=6):
    """Parallel lines across the map, slanting one way or the other for directions 0 and 1,
    then upright (2) and level (3)."""
    lines = Image.new("L", size, 0)
    draw = ImageDraw.Draw(lines)
    width, height = size
    if direction % 4 == 0:
        for offset in range(-height, width, spacing):
            draw.line((offset, height, offset + height, 0), fill=255)
    elif direction % 4 == 1:
        for offset in range(-height, width, spacing):
            draw.line((offset, 0, offset + height, height), fill=255)
    elif direction % 4 == 2:
        for x in range(0, width, spacing):
            draw.line((x, 0, x, height), fill=255)
    else:
        for y in range(0, height, spacing):
            draw.line((0, y, width, y), fill=255)
    return lines


def is_coastal(alert):
    text = str(alert.get("event") or "").lower()
    return any(word in text for word in COASTAL_WORDS)


def _draw_alert_areas(image, palette, alerts, water, manifest, size):
    """Each alert's area in its severity colour, alerts crossing where they overlap.

    A shore hazard (coastal flood, surf, rip current...) marks the shore of its zones as a
    dense band; any other alert hatches its zones' land. Returns (alert, mask) for the
    alerts that touch the map.
    """
    try:
        zones = read_overlay(ZONES)["zones"]
    except (OSError, ValueError, KeyError, TypeError):
        zones = {}
    land = ImageChops.invert(water)
    coast = water.filter(ImageFilter.MaxFilter(5))
    shore = None
    areas = []
    for index, alert in enumerate(alerts):
        mask = ImageChops.multiply(_alert_mask(alert, zones, manifest, size), land)
        if mask.getbbox() is None:
            continue
        colour = palette[alert.get("tone") or "alert"]
        if is_coastal(alert):
            if shore is None:
                shore = ImageChops.subtract(water.filter(ImageFilter.MaxFilter(SHORE_BAND * 2 + 1)), water)
            band = ImageChops.multiply(mask, shore)
            if band.getbbox() is not None:
                image.paste(colour, mask=ImageChops.multiply(band, _hatch_lines(size, index, spacing=3)))
                areas.append((alert, band))
                continue
        image.paste(colour, mask=ImageChops.multiply(mask, _hatch_lines(size, index, spacing=7)))
        # Its edge on land; along the shore the coastline already draws it.
        edge = ImageChops.subtract(mask, mask.filter(ImageFilter.MinFilter(3)))
        image.paste(colour, mask=ImageChops.subtract(edge, coast))
        areas.append((alert, mask))
    return areas


def _tag_spot(mask, width, height, occupied, frame):
    """A tag box on the alert's area: the solid part nearest its middle that is clear of markers."""
    small = mask.resize((mask.width // 4, mask.height // 4), Image.Resampling.BOX)
    pixels = small.load()
    for level in (250, 128, 1):
        cells = [(x * 4 + 2, y * 4 + 2) for y in range(small.height) for x in range(small.width)
                 if pixels[x, y] >= level]
        if not cells:
            continue
        middle_x = sum(x for x, _ in cells) / len(cells)
        middle_y = sum(y for _, y in cells) / len(cells)
        cells.sort(key=lambda cell: (cell[0] - middle_x) ** 2 + (cell[1] - middle_y) ** 2)
        box = _free_box(width, height, [(x - width / 2, y - height / 2) for x, y in cells[:400]], occupied, frame)
        if box:
            return box
    return None


def _draw_home_rings(draw, palette, manifest, size, home):
    """Thin grey circles 10, 25 and 50 km from home, so a distance has a scale on the map."""
    centre = project(home[0], home[1], manifest, size, clip=False)
    if centre is None:
        return []
    bounds = manifest["mercator_bounds"]
    metres_per_pixel = (bounds["xmax"] - bounds["xmin"]) / size[0] * math.cos(math.radians(home[0]))
    rings = []
    for km in HOME_RINGS_KM:
        radius = km * 1000 / metres_per_pixel
        x, y = centre
        draw.ellipse((x - radius, y - radius, x + radius, y + radius), outline=palette["rule"], width=1)
        rings.append((km, centre, radius))
    return rings


def _label_rings(draw, palette, fonts, rings, occupied, frame):
    font = fonts(10)
    placed = []
    for km, (x, y), radius in rings:
        text = f"{km} km"
        width, height = draw.textlength(text, font=font) + 4, 13
        for degrees in (-35, 35, -60, -90, -120, -145, 145, 90):
            px = x + radius * math.cos(math.radians(degrees))
            py = y + radius * math.sin(math.radians(degrees))
            box = _free_box(width, height, [(px - width / 2, py - height / 2)], occupied + placed, frame)
            if box:
                draw.text((px, py), text, font=font, fill=palette["ink"], anchor="mm",
                          stroke_width=2, stroke_fill=palette["paper"])
                placed.append(box)
                break
    return placed


def nearest_fault(lat, lon, max_km=NEAR_FAULT_KM):
    """(name, km) of the nearest named active fault within `max_km` of the point, else None."""
    try:
        data = read_overlay(FAULTS)
        west, south = data["origin_wgs84"]
        cell = float(data["cell_degrees"])
        faults = data["faults"]
        lat, lon = float(lat), float(lon)
    except (OSError, ValueError, KeyError, TypeError):
        return None
    scale = math.cos(math.radians(lat))
    best = None
    for fault in faults:
        cells = fault.get("cells") or []
        for index in range(0, len(cells) - 1, 2):
            dx = (west + cells[index] * cell - lon) * scale
            dy = south + cells[index + 1] * cell - lat
            distance = dx * dx + dy * dy
            if best is None or distance < best[0]:
                best = (distance, fault.get("name"))
    if best is None:
        return None
    km = math.sqrt(best[0]) * 111.2
    return (best[1], km) if best[1] and km <= max_km else None


def _edge_groups(quakes, manifest, size, rank):
    """Quakes beyond the map, bundled by where their arrow meets the frame (most important first).

    `rank(quake)` orders them; None leaves a quake out.
    """
    centre_x, centre_y = size[0] / 2, (size[1] - 22) / 2
    left, top, right, bottom = 10, 10, size[0] - 10, size[1] - 46
    candidates = []
    for quake in quakes:
        order = rank(quake)
        if order is None or project(quake.get("lat"), quake.get("lon"), manifest, size) is not None:
            continue
        point = project(quake.get("lat"), quake.get("lon"), manifest, size, clip=False)
        if point is None:
            continue
        dx, dy = point[0] - centre_x, point[1] - centre_y
        scale = min(bound / delta for bound, delta in ((right - centre_x, dx), (left - centre_x, dx),
                                                       (bottom - centre_y, dy), (top - centre_y, dy))
                    if delta and bound / delta > 0)
        length = math.hypot(dx, dy)
        x, y = centre_x + dx * scale, centre_y + dy * scale
        side = ("right" if abs(x - right) < 0.5 else "left" if abs(x - left) < 0.5
                else "bottom" if abs(y - bottom) < 0.5 else "top")
        candidates.append((order, quake, (x, y), (dx / length, dy / length), side))
    candidates.sort(key=lambda item: item[0])
    groups = []
    for _order, quake, edge, direction, side in candidates:
        group = next((group for group in groups if math.dist(group["edge"], edge) <= EDGE_GROUP_PX), None)
        if group:
            group["quakes"].append(quake)
        else:
            groups.append({"quake": quake, "edge": edge, "direction": direction, "side": side, "quakes": [quake]})
    return groups[:EDGE_MARKERS_MAX]


def _edge_corners(edge, side, width, height):
    """Top-left corners for an edge arrow's label: beside the arrow along the frame, else just inside it."""
    x, y = edge
    if side in ("top", "bottom"):
        row = y - height / 2
        inside = y - 10 - height if side == "bottom" else y + 10
        corners = [(x - 10 - width, row), (x + 10, row), (x - width / 2, inside), (x - width - 2, inside),
                   (x + 2, inside)]
        # Then further along the frame, then stacked inward from it.
        corners += [corner for step in range(1, 9) for corner in ((x - 10 - width - step * 12, row),
                                                                  (x + 10 + step * 12, row))]
        lift = -(height + 4) if side == "bottom" else height + 4
        return corners + [corner for step in range(1, 5) for corner in ((x - 10 - width, row + step * lift),
                                                                        (x + 10, row + step * lift))]
    column = x - 10 - width if side == "right" else x + 10
    inside = x + 8 - width if side == "right" else x - 8
    corners = [(column, y - height / 2), (column, y + 10), (column, y - 10 - height), (inside, y + 10),
               (inside, y - 10 - height)]
    return corners + [corner for step in range(1, 11) for corner in ((column, y - height / 2 - step * 12),
                                                                     (column, y - height / 2 + step * 12))]


def _draw_arrow(draw, palette, edge, direction, fill):
    (x, y), (ux, uy) = edge, direction
    tip, back = (x + ux * 6, y + uy * 6), (x - ux * 6, y - uy * 6)
    draw.polygon([tip, (back[0] - uy * 6, back[1] + ux * 6), (back[0] + uy * 6, back[1] - ux * 6)],
                 fill=fill, outline=palette["ink"])
    return (x - 8, y - 8, x + 8, y + 8)


def _chip_width(draw, font, quake):
    return draw.textlength(f"M{float(quake.get('mag', 0)):.1f}", font=font) + 8


def _draw_chip(draw, palette, font, xy, quake, now):
    """A quake's magnitude chip, coloured by its age like the list's."""
    fill, ink = quake_chip_colors(quake_age_class(quake, now), palette)
    width = _chip_width(draw, font, quake)
    x, y = xy
    draw.rounded_rectangle((x, y, x + width, y + 15), radius=3, fill=fill, outline=palette["ink"])
    draw.text((x + width / 2, y + 7.5), f"M{float(quake.get('mag', 0)):.1f}", font=font, fill=ink, anchor="mm")
    return (x, y, x + width, y + 15)


def _callout_size(draw, fonts, quake, focus):
    head = draw.textlength(f"M{float(quake.get('mag', 0)):.1f}", font=fonts(16))
    width = max([head + 5 + draw.textlength(focus["title"], font=fonts(10))] +
                [draw.textlength(line, font=fonts(10, False)) for line in focus["lines"]])
    return round(width + 14), 24 + 13 * len(focus["lines"])


def _draw_callout(draw, palette, fonts, box, quake, focus):
    """The headline quake's box: magnitude, then when, how far from home and the nearest fault."""
    tone = focus.get("tone")
    if tone:
        ink = tone_ink(tone, palette)
        draw.rounded_rectangle(box, radius=5, fill=palette[tone])
    else:
        ink = palette["ink"]
        draw.rounded_rectangle(box, radius=5, fill=palette["paper"], outline=palette["ink"], width=2)
    x, y = box[0] + 7, box[1]
    head = f"M{float(quake.get('mag', 0)):.1f}"
    width = draw.textlength(head, font=fonts(16))
    draw.text((x, y + 12), head, font=fonts(16), fill=ink, anchor="lm")
    draw.text((x + width + 5, y + 13), focus["title"], font=fonts(10), fill=ink, anchor="lm")
    for index, line in enumerate(focus["lines"]):
        draw.text((x, y + 29 + 13 * index), line, font=fonts(10, False), fill=ink, anchor="lm")


def _draw_summary(draw, palette, fonts, lines, occupied, frame):
    """The near-home box: what happened inside the outer ring."""
    title, rows = lines[0], lines[1:]
    width = max([draw.textlength(title, font=fonts(11)) + 16] +
                [draw.textlength(row, font=fonts(10, False)) for row in rows]) + 14
    height = 22 + 14 * len(rows)
    box = _free_box(width, height, SUMMARY_CORNERS, occupied, frame)
    if box is None:
        return None
    draw.rounded_rectangle(box, radius=5, fill=palette["paper"], outline=palette["rule"], width=1)
    x, y = box[0] + 7, box[1]
    draw_icon(draw, "home", (x, y + 5), 11, palette["home"], palette["paper"])
    draw.text((x + 15, y + 11), title, font=fonts(11), fill=palette["ink"], anchor="lm")
    for index, row in enumerate(rows):
        draw.text((x, y + 27 + 14 * index), row, font=fonts(10, False), fill=palette["ink"], anchor="lm")
    return box


def _dashed(draw, start, end, colour, dash=5, gap=3, width=2):
    length = math.dist(start, end)
    if not length:
        return
    position = 0.0
    while position < length:
        first, last = position / length, min(1.0, (position + dash) / length)
        draw.line((start[0] + (end[0] - start[0]) * first, start[1] + (end[1] - start[1]) * first,
                   start[0] + (end[0] - start[0]) * last, start[1] + (end[1] - start[1]) * last),
                  fill=colour, width=width)
        position += dash + gap


def _draw_fires(draw, palette, manifest, size, fires, hotspots, occupied):
    """FIRMS hotspots as small flame-coloured squares and CAL FIRE incidents inside the map as
    ringed fire icons; returns how many of each landed on the map."""
    shown = {"hotspots": 0, "incidents": 0}
    for spot in hotspots:
        point = project(spot.get("lat"), spot.get("lon"), manifest, size)
        if point is None:
            continue
        x, y = point
        draw.rectangle((x - 2.5, y - 2.5, x + 2.5, y + 2.5), fill=palette["quake_recent"], outline=palette["alert"])
        shown["hotspots"] += 1
    for fire in fires:
        point = project(fire.get("lat"), fire.get("lon"), manifest, size)
        if point is None:
            continue
        x, y = point
        draw.ellipse((x - 10, y - 10, x + 10, y + 10), fill=palette["paper"], outline=palette["alert"], width=2)
        draw_icon(draw, "fire", (x - 7, y - 7), 14, palette["alert"], palette["paper"])
        occupied.append((x - 11, y - 11, x + 11, y + 11))
        shown["incidents"] += 1
    return shown


def render_bay_map(size, palette, fonts, *, quakes, alerts, home, home_label, now, listed=(), focus=None,
                   summary=None, fires=(), hotspots=()):
    """The Bay map with the page's live data drawn on it.

    `alerts` carry the card's "number" and "tone"; numbered ones get the card's tag on their area.
    `listed` are the ids the quake card lists: their magnitude chips show on the map too.
    `focus` ({"id", "title", "detail", "tone"}) is the quake the callout explains.
    `summary` is the near-home box's lines (title first).
    `fires` (CAL FIRE incidents) and `hotspots` (FIRMS detections) mark the map where they fall.
    """
    manifest = read_manifest(BAY_MAP)
    if tuple(size) != tuple(manifest["image_size"]):
        raise ValueError("Bay Alerts map is drawn at its exported size only")
    water, faults, shade = _layers(BAY_MAP, manifest)
    with safe_open_image(ASSETS / f"{BAY_MAP}-waterlines.png", limits=_LIMITS) as source:
        waterlines = source.convert("L")
    image = Image.new("RGB", size, palette["paper"])
    image.paste(palette["relief"], mask=_stipple(shade, palette["relief_density"]))
    image.paste(palette["water"], mask=water)
    areas = _draw_alert_areas(image, palette, alerts, water, manifest, size)
    image.paste(palette["water_line"], mask=waterlines)
    image.paste(palette["fault"], mask=faults)
    draw = ImageDraw.Draw(image)
    frame = (2, 2, size[0] - 2, size[1] - 20)
    rings = _draw_home_rings(draw, palette, manifest, size, home)

    decks = _draw_bridges(draw, palette, manifest, size)
    # The north arrow and the scale bar are drawn last, but their corners are kept free.
    occupied = [(size[0] - 26, 6, size[0] - 6, 44), (4, size[1] - 46, 132, size[1] - 22)]
    occupied += _draw_fault_labels(image, faults, palette, fonts, manifest, size)
    for _code, lat, lon in AIRPORTS:
        point = project(lat, lon, manifest, size)
        if point is not None:
            draw.ellipse((point[0] - 8, point[1] - 8, point[0] + 8, point[1] + 8), fill=palette["paper"])
            _draw_plane(draw, point[0], point[1], palette["ink"])
            occupied.append((point[0] - 8, point[1] - 8, point[0] + 8, point[1] + 8))

    listed = set(listed)
    focus_id = focus.get("id") if focus else None
    focus_quake = next((quake for quake in quakes if focus_id is not None and quake.get("id") == focus_id), None)

    def rank(quake):
        age = quake_age_class(quake, now)
        if quake is focus_quake:
            order = 0
        elif quake.get("id") in listed:
            order = 1
        elif age != "old":
            order = 2 if age == "day" else 3
        else:
            return None
        return (order, -float(quake.get("mag", 0)))

    fire_marks = _draw_fires(draw, palette, manifest, size, fires, hotspots, occupied)
    groups = _edge_groups(quakes, manifest, size, rank)
    home_point = project(home[0], home[1], manifest, size)
    focus_point = project(focus_quake.get("lat"), focus_quake.get("lon"), manifest, size) if focus_quake else None
    if home_point is not None and focus_point is not None:
        # A dashed ink line from home to the headline quake: the distance its callout gives.
        # (Ink, not the quake's colour: a red line would read as one more fault.)
        _dashed(draw, home_point, focus_point, palette["ink"])

    # Small quake dots and town dots stay free for place names, but the live labels avoid them.
    markers, dots, small_dots = [], {}, []
    for _name, lat, lon, _font_size in CITIES:
        point = project(lat, lon, manifest, size)
        if point is not None:
            small_dots.append((point[0] - 3, point[1] - 3, point[0] + 3, point[1] + 3))
    order = sorted(quakes, key=lambda quake: (quake_age_class(quake, now) != "old",
                                               quake_age_class(quake, now) == "day", float(quake.get("mag", 0)),
                                               quake.get("time") or ""))
    for quake in order:
        point = project(quake.get("lat"), quake.get("lon"), manifest, size)
        markers.append((quake, {"id": quake.get("id"), "visible": point is not None}))
        if point is None:
            continue
        x, y = point
        radius = quake_radius(quake.get("mag", 1.5))
        age = quake_age_class(quake, now)
        colour = palette["quake_day"] if age == "day" else palette["quake_recent"] if age == "recent" else None
        if float(quake.get("mag", 0)) >= SHOCK_MAGNITUDE:
            # Shock rings: a strong quake reads at a glance.
            for gap, width in ((4, 2), (9, 1)):
                draw.ellipse((x - radius - gap, y - radius - gap, x + radius + gap, y + radius + gap),
                             outline=colour or palette["ink"], width=width)
        box = (x - radius, y - radius, x + radius, y + radius)
        draw.ellipse(box, fill=colour, outline=palette["ink"], width=1)
        dots[id(quake)] = (point, radius)
        (occupied if radius >= 5 else small_dots).append(box)

    if home_point is not None:
        x, y = home_point
        draw.ellipse((x - 11, y - 11, x + 11, y + 11), fill=palette["paper"], outline=palette["home"], width=2)
        draw_icon(draw, "home", (x - 7, y - 8), 14, palette["home"], palette["paper"])
        occupied.append((x - 12, y - 12, x + 12, y + 12))
        if home_label:
            font = fonts(12)
            box = draw.textbbox((x + 14, y - 8), home_label, font=font, stroke_width=2)
            draw.text((x + 14, y - 8), home_label, font=font, fill=palette["home"],
                      stroke_width=2, stroke_fill=palette["paper"])
            occupied.append(box)

    # Live data goes down before place names, which then avoid it: the near-home box, alert tags,
    # edge arrows, the headline callout and magnitude chips.
    summary_box = _draw_summary(draw, palette, fonts, summary, occupied + small_dots, frame) if summary else None
    if summary_box:
        occupied.append(summary_box)
    tag_font = fonts(12)
    tag_width, tag_height = alert_tag_size(tag_font)
    tags = []
    for alert, mask in areas:
        if not alert.get("number"):
            continue
        box = _tag_spot(mask, tag_width, tag_height, occupied + small_dots, frame)
        if box is None:
            continue
        draw.rounded_rectangle((box[0] - 1, box[1] - 1, box[2] + 1, box[3] + 1), radius=6, fill=palette["paper"])
        draw_alert_tag(draw, palette, tag_font, box[:2], alert["number"], alert.get("kind"), alert.get("tone"))
        occupied.append(box)
        tags.append({"number": alert["number"], "box": box})

    chip_font = fonts(11)
    callout, edge_ids = None, set()
    for group in groups:
        edge_ids.update(id(quake) for quake in group["quakes"])
        fill, _ink = quake_chip_colors(quake_age_class(group["quake"], now), palette)
        occupied.append(_draw_arrow(draw, palette, group["edge"], group["direction"], fill))
    if focus_quake is not None and id(focus_quake) in dots:
        (x, y), radius = dots[id(focus_quake)]
        width, height = _callout_size(draw, fonts, focus_quake, focus)
        corners = _around(x, y, width, height, (radius + 12, radius + 32))
        box = _free_box(width, height, corners, occupied + small_dots, frame) or _clamped(
            corners[0], width, height, frame)
        _leader(draw, (x, y), box, palette["ink"], gap=radius + 1)
        _draw_callout(draw, palette, fonts, box, focus_quake, focus)
        occupied.append(box)
        callout = box
    for group in groups:
        lead, (x, y) = group["quake"], group["edge"]
        if lead is focus_quake:
            width, height = _callout_size(draw, fonts, lead, focus)
        else:
            extra = f"{float(lead.get('distance_km', 0)):.0f} km"
            if len(group["quakes"]) > 1:
                extra += f" · {len(group['quakes'])} 次"
            width = _chip_width(draw, chip_font, lead) + 8 + draw.textlength(extra, font=fonts(10, False))
            height = 15
        corners = _edge_corners((x, y), group["side"], width, height)
        box = _free_box(width, height, corners, occupied + small_dots, frame) or _clamped(
            corners[0], width, height, frame)
        _leader(draw, (x, y), box, palette["ink"], gap=8)
        if lead is focus_quake:
            _draw_callout(draw, palette, fonts, box, lead, focus)
            callout = box
        else:
            chip = _draw_chip(draw, palette, chip_font, box[:2], lead, now)
            draw.text((chip[2] + 4, chip[1] + 7.5), extra, font=fonts(10, False), fill=palette["ink"],
                      anchor="lm", stroke_width=2, stroke_fill=palette["paper"])
        occupied.append(box)

    chips = set()
    for quake in sorted(quakes, key=lambda quake: -float(quake.get("mag", 0))):
        if quake is focus_quake or id(quake) not in dots:
            continue
        if quake.get("id") not in listed and float(quake.get("mag", 0)) < SHOCK_MAGNITUDE:
            continue
        (x, y), radius = dots[id(quake)]
        width = _chip_width(draw, chip_font, quake)
        others = occupied + [dot for dot in small_dots if not _overlaps(dot, (x - 1, y - 1, x + 1, y + 1))]
        box = _free_box(width, 15, _around(x, y, width, 15, (radius + 3, radius + 12)), others, frame)
        if box is None:
            continue
        _leader(draw, (x, y), box, palette["ink"], gap=radius + 1)
        occupied.append(_draw_chip(draw, palette, chip_font, box[:2], quake, now))
        chips.add(id(quake))

    occupied += _label_rings(draw, palette, fonts, rings, occupied + small_dots, frame)
    for quake, marker in markers:
        marker["edge"] = id(quake) in edge_ids
        marker["chip"] = id(quake) in chips or quake is focus_quake

    labels = []

    def place_label(name, x, y, font, dot=True, gap=5, own=None):
        width = draw.textlength(name, font=font)
        lift = font.size * 0.6
        others = [box for box in occupied + labels if box != own]
        for dx, dy in ((gap, -lift), (-width - gap, -lift), (-width / 2, 3), (-width / 2, -lift * 2 - 6),
                       (gap, 2), (-width - gap, 2)):
            box = draw.textbbox((x + dx, y + dy), name, font=font, stroke_width=2)
            inside = box[0] >= 2 and box[1] >= 2 and box[2] <= size[0] - 2 and box[3] <= size[1] - 22
            if inside and not any(_overlaps(box, other) for other in others):
                # A town dot never sits on a quake: the quake marks the spot.
                if dot and not any(math.dist((x, y), point) <= radius + 2 for point, radius in dots.values()):
                    draw.ellipse((x - 2, y - 2, x + 2, y + 2), fill=palette["ink"])
                draw.text((x + dx, y + dy), name, font=font, fill=palette["ink"],
                          stroke_width=2, stroke_fill=palette["paper"])
                labels.append(box)
                return True
        return False

    # Major cities, then airport codes, then smaller towns, then bridge names.
    for name, lat, lon, font_size in CITIES:
        point = project(lat, lon, manifest, size)
        if point is not None and font_size >= 12:
            place_label(name, point[0], point[1], fonts(font_size, False))
    for code, lat, lon in AIRPORTS:
        point = project(lat, lon, manifest, size)
        if point is not None:
            plane = (point[0] - 8, point[1] - 8, point[0] + 8, point[1] + 8)
            place_label(code, point[0], point[1], fonts(10), dot=False, gap=10, own=plane)
    for name, lat, lon, font_size in CITIES:
        point = project(lat, lon, manifest, size)
        if point is not None and font_size < 12:
            place_label(name, point[0], point[1], fonts(font_size, False))
    bridge_font = fonts(10, False)
    for name, pixels in decks:
        if name not in NAMED_BRIDGES:
            continue
        middle = pixels[len(pixels) // 2] if len(pixels) > 2 else (
            (pixels[0][0] + pixels[-1][0]) / 2, (pixels[0][1] + pixels[-1][1]) / 2)
        place_label(name, middle[0], middle[1], bridge_font, dot=False, gap=6)

    _draw_north_arrow(draw, palette, fonts, (size[0] - 24, 10))
    _draw_scale_bar(draw, palette, fonts, manifest, size, home[0])
    _draw_legend(image, draw, palette, fonts, size, [alert.get("tone") for alert, _mask in areas])
    image.info["map_markers"] = [marker for _quake, marker in markers]
    image.info["map_labels"] = len(labels)
    image.info["alert_tags"] = tags
    image.info["map_callout"] = callout
    image.info["map_summary"] = summary_box
    image.info["home_rings"] = [km for km, _centre, _radius in rings]
    image.info["fire_marks"] = fire_marks
    return image


def _draw_north_arrow(draw, palette, fonts, origin):
    x, y = origin
    draw.polygon([(x + 8, y), (x + 15, y + 18), (x + 8, y + 14), (x + 1, y + 18)], fill=palette["ink"])
    draw.text((x + 8, y + 20), "N", font=fonts(11), fill=palette["ink"], anchor="mt",
              stroke_width=2, stroke_fill=palette["paper"])


def _draw_scale_bar(draw, palette, fonts, manifest, size, latitude):
    bounds = manifest["mercator_bounds"]
    meters_per_pixel = (bounds["xmax"] - bounds["xmin"]) / size[0] * math.cos(math.radians(latitude))
    length = 20000 / meters_per_pixel
    left, bottom = 10, size[1] - 30
    draw.rectangle((left - 3, bottom - 15, left + length + 34, bottom + 5), fill=palette["paper"])
    draw.line((left, bottom, left + length, bottom), fill=palette["ink"], width=2)
    for x in (left, left + length / 2, left + length):
        draw.line((x, bottom - 4, x, bottom), fill=palette["ink"], width=1)
    draw.text((left + length + 4, bottom + 1), "20 km", font=fonts(11, False), fill=palette["ink"], anchor="ls")


def _draw_legend(image, draw, palette, fonts, size, alert_tones=()):
    top = size[1] - 18
    draw.rectangle((0, top, size[0], size[1]), fill=palette["paper"])
    draw.line((0, top, size[0], top), fill=palette["rule"], width=1)
    font = fonts(11, False)
    x, y = 8, top + 9
    for kind, label in (("day", "24 小时"), ("recent", "3 天"), ("old", "7 天")):
        box = (x, y - 5, x + 10, y + 5)
        if kind == "day":
            draw.ellipse(box, fill=palette["quake_day"], outline=palette["ink"])
        elif kind == "recent":
            draw.ellipse(box, fill=palette["quake_recent"], outline=palette["ink"])
        else:
            draw.ellipse(box, outline=palette["ink"])
        draw.text((x + 14, y), label, font=font, fill=palette["ink"], anchor="lm")
        x += 14 + draw.textlength(label, font=font) + 10
    draw.ellipse((x, y - 6, x + 12, y + 6), outline=palette["rule"], width=1)
    draw.text((x + 16, y), "距家", font=font, fill=palette["ink"], anchor="lm")
    x += 16 + draw.textlength("距家", font=font) + 10
    if alert_tones:
        swatch = Image.new("L", (12, 12), 0)
        ImageDraw.Draw(swatch).rectangle((0, 0, 11, 11), fill=255)
        hatch = ImageChops.multiply(swatch, _hatch_lines((12, 12), 0, spacing=4))
        image.paste(palette[alert_tones[0] or "alert"], (int(x), int(y - 6)), hatch)
        draw.rectangle((x, y - 6, x + 11, y + 5), outline=palette[alert_tones[0] or "alert"])
        draw.text((x + 16, y), "预警区", font=font, fill=palette["ink"], anchor="lm")
        x += 16 + draw.textlength("预警区", font=font) + 10
    draw.line((x, y, x + 16, y), fill=palette["fault"], width=2)
    draw.text((x + 20, y), "活动断层", font=font, fill=palette["ink"], anchor="lm")
    # The map's sources by their logos (the faults and quakes USGS', the warning zones NWS').
    paper, ink = rgb(palette["paper"]), rgb(palette["ink"])
    if paste_logos(image, ("usgs", "nws"), size[0] - 8, y, LEGEND_LOGO_HEIGHT, ink if sum(paper) < sum(ink) else None) is None:
        draw.text((size[0] - 8, y), "USGS · NWS", font=font, fill=palette["ink"], anchor="rm")


def rgb(colour):
    """(r, g, b) of a palette colour given as "#rrggbb" or as a tuple."""
    return tuple(ImageColor.getrgb(colour) if isinstance(colour, str) else colour)[:3]


def source_logo(name, height, ink=None):
    """A data source's official logo (assets/logos/<name>.png, tools/build_source_logos.py)
    ``height`` tall; with ``ink`` (a dark page), its black parts take that colour while its
    coloured parts stay as printed. None without the file."""
    try:
        with Image.open(ASSETS / "logos" / f"{name}.png") as source:
            logo = source.convert("RGBA")
    except (OSError, ValueError):
        return None
    logo = logo.resize((max(1, round(logo.width * height / logo.height)), height), Image.Resampling.LANCZOS)
    if ink is not None:
        pixels = logo.load()
        for y in range(logo.height):
            for x in range(logo.width):
                red, green, blue, alpha = pixels[x, y]
                if alpha and max(red, green, blue) < 110 and max(red, green, blue) - min(red, green, blue) < 40:
                    pixels[x, y] = (*ink, alpha)
    return logo


def paste_logos(image, names, right, middle, height, ink=None, gap=4):
    """The named logos side by side ending at ``right``, centred on ``middle``; the left edge of
    the row, or None (nothing drawn) when any of them is missing."""
    logos = [source_logo(name, height, ink) for name in names]
    if not logos or any(logo is None for logo in logos):
        return None
    x = right - sum(logo.width for logo in logos) - gap * (len(logos) - 1)
    left = x
    for logo in logos:
        image.paste(logo, (round(x), round(middle - logo.height / 2)), logo)
        x += logo.width + gap
    return left


def california_map_size(max_width, max_height):
    """The largest minimap size within the box that keeps the map's proportions."""
    native_width, native_height = read_manifest(CA_MAP)["image_size"]
    scale = min(max_width / native_width, max_height / native_height)
    return max(1, round(native_width * scale)), max(1, round(native_height * scale))


def render_california_map(size, palette, events):
    manifest = read_manifest(CA_MAP)
    water, land, _unused = _layers(CA_MAP, manifest)
    native = tuple(manifest["image_size"])
    size = tuple(size)
    water = water.resize(size, Image.Resampling.NEAREST)
    land = land.resize(size, Image.Resampling.NEAREST)
    image = Image.new("RGB", size, palette["panel"])
    image.paste(palette["water"], mask=water)
    image.paste(palette["paper"], mask=land)
    image.paste(palette["ink"], mask=ImageChops.subtract(land, land.filter(ImageFilter.MinFilter(3))))
    draw = ImageDraw.Draw(image)
    scale = (size[0] / native[0], size[1] / native[1])
    placed = []
    for event in reversed(events):
        point = project(event.get("lat"), event.get("lon"), manifest, native)
        if point is None:
            continue
        x, y = point[0] * scale[0], point[1] * scale[1]
        draw_icon(draw, event.get("kind"), (x - 6, y - 6), 12, palette["alert"], palette["paper"])
        placed.append(event.get("id"))
    image.info["event_markers"] = placed
    return image
