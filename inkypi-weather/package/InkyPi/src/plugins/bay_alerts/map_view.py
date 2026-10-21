"""Offline Bay Area hazard map: water-lined coast, active faults, quakes, alert areas.

The layered raster in assets/ is a one-time USGS public-domain export (see the JSON
manifests). Rendering never contacts a map service; markers sit on their true
coordinates and anything outside the map is reported rather than clamped.
"""

from __future__ import annotations

import json
import math
from datetime import timedelta
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter

from utils.flat_icons import draw_icon
from utils.safe_image import ImageLimits, safe_open_image

from .sources import parse_time


ASSETS = Path(__file__).resolve().parent / "assets"
BAY_MAP = "bay-alerts-map"
CA_MAP = "california-map"
_RADIUS = 6378137.0
_LIMITS = ImageLimits(max_bytes=512 * 1024, max_width=1024, max_height=1024, max_pixels=1024 * 1024)
# 4x4 Bayer thresholds: relief becomes an ordered stipple the panel shows without noise.
_BAYER = (0, 8, 2, 10, 12, 4, 14, 6, 3, 11, 1, 9, 15, 7, 13, 5)
CITIES = (
    ("旧金山", 37.7749, -122.4194), ("奥克兰", 37.8044, -122.2712), ("圣何塞", 37.3382, -121.8863),
    ("弗里蒙特", 37.5485, -121.9886), ("圣罗莎", 38.4405, -122.7144), ("纳帕", 38.2975, -122.2869),
    ("核桃溪", 37.9101, -122.0652), ("帕洛阿尔托", 37.4419, -122.1430), ("圣克鲁兹", 36.9741, -122.0308),
    ("瓦列霍", 38.1041, -122.2566), ("利弗莫尔", 37.6819, -121.7680), ("半月湾", 37.4636, -122.4286),
    ("圣拉斐尔", 37.9735, -122.5311),
)


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


def project(lat, lon, manifest, size):
    """Pixel for a coordinate on this map, or None when missing or outside it."""
    try:
        lat, lon = float(lat), float(lon)
    except (TypeError, ValueError):
        return None
    if not (math.isfinite(lat) and math.isfinite(lon) and -85 < lat < 85 and -180 <= lon <= 180):
        return None
    bounds = manifest["mercator_bounds"]
    x = _RADIUS * math.radians(lon)
    y = _RADIUS * math.log(math.tan(math.pi / 4 + math.radians(lat) / 2))
    if not (bounds["xmin"] <= x <= bounds["xmax"] and bounds["ymin"] <= y <= bounds["ymax"]):
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


def _hatch(polygon, size):
    """Diagonal hatching clipped to an alert polygon."""
    mask = Image.new("L", size, 0)
    ImageDraw.Draw(mask).polygon(polygon, fill=255)
    lines = Image.new("L", size, 0)
    lines_draw = ImageDraw.Draw(lines)
    for offset in range(-size[1], size[0], 7):
        lines_draw.line((offset, size[1], offset + size[1], 0), fill=255, width=1)
    return ImageChops.multiply(mask, lines)


def render_bay_map(size, palette, fonts, *, quakes, alerts, home, home_label, now):
    manifest = read_manifest(BAY_MAP)
    if tuple(size) != tuple(manifest["image_size"]):
        raise ValueError("Bay Alerts map is drawn at its exported size only")
    water, faults, shade = _layers(BAY_MAP, manifest)
    with safe_open_image(ASSETS / f"{BAY_MAP}-waterlines.png", limits=_LIMITS) as source:
        waterlines = source.convert("L")
    image = Image.new("RGB", size, palette["paper"])
    image.paste(palette["relief"], mask=_stipple(shade, palette["relief_density"]))
    image.paste(palette["water"], mask=water)
    image.paste(palette["water_line"], mask=waterlines)
    image.paste(palette["fault"], mask=faults)
    draw = ImageDraw.Draw(image)

    for alert in alerts:
        ring = alert.get("polygon")
        if not ring:
            continue
        points = [project(lat, lon, manifest, size) for lon, lat in ring]
        if any(point is None for point in points):
            points = [point for point in points if point is not None]
        if len(points) >= 3:
            image.paste(palette["alert"], mask=_hatch(points, size))
            draw.line(points + [points[0]], fill=palette["alert"], width=2)

    occupied, markers = [], []
    order = sorted(quakes, key=lambda quake: (quake_age_class(quake, now) != "old",
                                               quake_age_class(quake, now) == "day", quake.get("time") or ""))
    for quake in order:
        point = project(quake.get("lat"), quake.get("lon"), manifest, size)
        markers.append({"id": quake.get("id"), "visible": point is not None})
        if point is None:
            continue
        x, y = point
        radius = quake_radius(quake.get("mag", 1.5))
        box = (x - radius, y - radius, x + radius, y + radius)
        age = quake_age_class(quake, now)
        if age == "day":
            draw.ellipse(box, fill=palette["quake_day"], outline=palette["ink"], width=1)
        elif age == "recent":
            draw.ellipse(box, fill=palette["quake_recent"], outline=palette["ink"], width=1)
        else:
            draw.ellipse(box, outline=palette["ink"], width=1)
        if radius >= 5:
            occupied.append(box)

    strongest = max(quakes, key=lambda quake: quake.get("mag", 0), default=None)
    if strongest is not None:
        point = project(strongest.get("lat"), strongest.get("lon"), manifest, size)
        if point is not None:
            label = f"M{strongest['mag']:.1f}"
            font = fonts(13)
            x, y = point
            radius = quake_radius(strongest["mag"])
            box = draw.textbbox((x + radius + 3, y - 8), label, font=font, stroke_width=2)
            draw.text((x + radius + 3, y - 8), label, font=font, fill=palette["ink"],
                      stroke_width=2, stroke_fill=palette["paper"])
            occupied.append(box)

    home_point = project(home[0], home[1], manifest, size)
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

    label_font = fonts(12, False)
    labels = []
    for name, lat, lon in CITIES:
        point = project(lat, lon, manifest, size)
        if point is None:
            continue
        x, y = point
        width = draw.textlength(name, font=label_font)
        for dx, dy in ((5, -7), (-width - 5, -7), (-width / 2, 3), (-width / 2, -18), (5, 2), (-width - 5, 2)):
            box = draw.textbbox((x + dx, y + dy), name, font=label_font, stroke_width=2)
            inside = box[0] >= 2 and box[1] >= 2 and box[2] <= size[0] - 2 and box[3] <= size[1] - 22
            if inside and not any(_overlaps(box, other) for other in occupied + labels):
                draw.ellipse((x - 2, y - 2, x + 2, y + 2), fill=palette["ink"])
                draw.text((x + dx, y + dy), name, font=label_font, fill=palette["ink"],
                          stroke_width=2, stroke_fill=palette["paper"])
                labels.append(box)
                break

    _draw_north_arrow(draw, palette, fonts, (size[0] - 24, 10))
    _draw_scale_bar(draw, palette, fonts, manifest, size, home[0])
    _draw_legend(draw, palette, fonts, size)
    image.info["map_markers"] = markers
    image.info["map_labels"] = len(labels)
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


def _draw_legend(draw, palette, fonts, size):
    top = size[1] - 18
    draw.rectangle((0, top, size[0], size[1]), fill=palette["paper"])
    draw.line((0, top, size[0], top), fill=palette["rule"], width=1)
    font = fonts(11, False)
    x, y = 8, top + 9
    for kind, label in (("day", "24 小时"), ("recent", "3 天"), ("old", "7 天内")):
        box = (x, y - 5, x + 10, y + 5)
        if kind == "day":
            draw.ellipse(box, fill=palette["quake_day"], outline=palette["ink"])
        elif kind == "recent":
            draw.ellipse(box, fill=palette["quake_recent"], outline=palette["ink"])
        else:
            draw.ellipse(box, outline=palette["ink"])
        draw.text((x + 14, y), label, font=font, fill=palette["ink"], anchor="lm")
        x += 22 + draw.textlength(label, font=font) + 10
    draw.line((x, y, x + 20, y), fill=palette["fault"], width=2)
    draw.text((x + 25, y), "活动断层", font=font, fill=palette["ink"], anchor="lm")
    draw.text((size[0] - 8, y), "地图 USGS", font=font, fill=palette["ink"], anchor="rm")


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
