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


def _hatch(polygon, size):
    """Diagonal hatching clipped to an alert polygon."""
    mask = Image.new("L", size, 0)
    ImageDraw.Draw(mask).polygon(polygon, fill=255)
    lines = Image.new("L", size, 0)
    lines_draw = ImageDraw.Draw(lines)
    for offset in range(-size[1], size[0], 7):
        lines_draw.line((offset, size[1], offset + size[1], 0), fill=255, width=1)
    return ImageChops.multiply(mask, lines)


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


def _draw_edge_markers(draw, palette, fonts, quakes, manifest, size, now):
    """Recent quakes beyond the map: an arrow on the frame pointing at each, with its magnitude.

    They are live data, so they are drawn before place names, which then avoid them.
    Returns the ids shown and the boxes they cover.
    """
    candidates = []
    for quake in quakes:
        age = quake_age_class(quake, now)
        if age == "old" or project(quake.get("lat"), quake.get("lon"), manifest, size) is not None:
            continue
        point = project(quake.get("lat"), quake.get("lon"), manifest, size, clip=False)
        if point is not None:
            candidates.append((age != "day", -float(quake.get("mag", 0)), quake, point))
    candidates.sort(key=lambda item: item[:2])
    centre_x, centre_y = size[0] / 2, (size[1] - 22) / 2
    left, top, right, bottom = 10, 10, size[0] - 10, size[1] - 46
    font = fonts(11)
    drawn, ids = [], []
    for _late, _mag, quake, (x, y) in candidates:
        if len(drawn) >= EDGE_MARKERS_MAX:
            break
        dx, dy = x - centre_x, y - centre_y
        scales = [bound / delta for bound, delta in ((right - centre_x, dx), (left - centre_x, dx),
                                                    (bottom - centre_y, dy), (top - centre_y, dy))
                  if delta and bound / delta > 0]
        scale = min(scales)
        edge_x, edge_y = centre_x + dx * scale, centre_y + dy * scale
        length = math.hypot(dx, dy)
        ux, uy = dx / length, dy / length
        label = f"M{float(quake['mag']):.1f}"
        text_x, text_y = edge_x - ux * 22, edge_y - uy * 14
        box = draw.textbbox((text_x, text_y), label, font=font, anchor="mm", stroke_width=2)
        marker = (min(box[0], edge_x - 8), min(box[1], edge_y - 8), max(box[2], edge_x + 8), max(box[3], edge_y + 8))
        if any(_overlaps(marker, other) for other in drawn):
            continue
        fill = palette["quake_day"] if quake_age_class(quake, now) == "day" else palette["quake_recent"]
        tip = (edge_x + ux * 6, edge_y + uy * 6)
        back = (edge_x - ux * 6, edge_y - uy * 6)
        draw.polygon([tip, (back[0] - uy * 6, back[1] + ux * 6), (back[0] + uy * 6, back[1] - ux * 6)],
                     fill=fill, outline=palette["ink"])
        draw.text((text_x, text_y), label, font=font, fill=palette["ink"], anchor="mm",
                  stroke_width=2, stroke_fill=palette["paper"])
        drawn.append(marker)
        ids.append(quake.get("id"))
    return ids, drawn


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

    decks = _draw_bridges(draw, palette, manifest, size)
    occupied = _draw_fault_labels(image, faults, palette, fonts, manifest, size)
    for _code, lat, lon in AIRPORTS:
        point = project(lat, lon, manifest, size)
        if point is not None:
            draw.ellipse((point[0] - 8, point[1] - 8, point[0] + 8, point[1] + 8), fill=palette["paper"])
            _draw_plane(draw, point[0], point[1], palette["ink"])
            occupied.append((point[0] - 8, point[1] - 8, point[0] + 8, point[1] + 8))
    markers = []
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

    edge_ids, edge_boxes = _draw_edge_markers(draw, palette, fonts, quakes, manifest, size, now)
    occupied.extend(edge_boxes)
    for marker in markers:
        marker["edge"] = marker["id"] in edge_ids

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
                if dot:
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
