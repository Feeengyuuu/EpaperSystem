"""Offline USGS map with truthful Caltrans coordinate markers.

The checked-in raster is a single public-domain USGS National Map export.
Its service-returned EPSG:3857 extent is authoritative; no raster tiles or
provider calls are made while rendering. See assets/bay-map.json for provenance.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageOps

from utils.app_utils import get_base_ui_font
from utils.safe_image import ImageLimits, safe_open_image


ASSETS = Path(__file__).resolve().parent / "assets"
MAP_PATH = ASSETS / "bay-map.png"
MANIFEST_PATH = ASSETS / "bay-map.json"
DEFAULT_SIZE = (184, 282)
_RADIUS = 6378137.0
_LIMITS = ImageLimits(max_bytes=1024 * 1024, max_width=1024, max_height=1024,
                      max_pixels=1024 * 1024)
# Approximate city centres, used only as geographic labels, never closure positions.
_CITY_LABELS = (
    ("Danville", 37.82159, -121.99996, -44, -16),
    ("San Ramon", 37.7805, -121.978, 4, 4),
    ("Fremont", 37.54827, -121.98857, 4, -20),
    ("Newark", 37.52966, -122.04024, -49, -17),
    ("San Jose", 37.33939, -121.89496, 3, 4),
    ("Dumbarton", 37.5052, -122.1169, -47, 11),
)


def read_manifest():
    if MANIFEST_PATH.stat().st_size > 8192:
        raise ValueError("Bay map manifest exceeds its size bound")
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    bounds = manifest.get("mercator_bounds", {})
    if (manifest.get("schema") != 1 or manifest.get("projection") != "EPSG:3857"
            or any(not isinstance(bounds.get(k), (int, float)) or not math.isfinite(bounds[k])
                   for k in ("xmin", "ymin", "xmax", "ymax"))
            or bounds["xmin"] >= bounds["xmax"] or bounds["ymin"] >= bounds["ymax"]):
        raise ValueError("Bay map manifest has invalid projected bounds")
    return manifest


def _viewport(size, manifest):
    width, height = size
    if (type(width) is not int or type(height) is not int
            or not 32 <= width <= 800 or not 32 <= height <= 800):
        raise ValueError("Bay map size must be between 32 and 800 pixels per side")
    source_width, source_height = manifest["image_size"]
    scale = min(width / source_width, height / source_height)
    drawn_width, drawn_height = round(source_width * scale), round(source_height * scale)
    return ((width - drawn_width) // 2, (height - drawn_height) // 2, drawn_width, drawn_height)


def project_coordinate(latitude, longitude, *, size=DEFAULT_SIZE, manifest=None):
    """Return a real on-map pixel, or None for missing/out-of-view coordinates."""
    if isinstance(latitude, bool) or isinstance(longitude, bool):
        return None
    try:
        lat, lon = float(latitude), float(longitude)
    except (TypeError, ValueError):
        return None
    if not (math.isfinite(lat) and math.isfinite(lon) and -85.05112878 <= lat <= 85.05112878
            and -180 <= lon <= 180) or (lat == 0 and lon == 0):
        return None
    manifest = manifest or read_manifest()
    extent = manifest["mercator_bounds"]
    x = _RADIUS * math.radians(lon)
    y = _RADIUS * math.log(math.tan(math.pi / 4 + math.radians(lat) / 2))
    if not (extent["xmin"] <= x <= extent["xmax"] and extent["ymin"] <= y <= extent["ymax"]):
        return None
    left, top, width, height = _viewport(size, manifest)
    return (left + (x - extent["xmin"]) / (extent["xmax"] - extent["xmin"]) * width,
            top + (extent["ymax"] - y) / (extent["ymax"] - extent["ymin"]) * height)


def render_map(rows, size=DEFAULT_SIZE, night=False):
    """Render markers numbered in the supplied road-card order, without network IO.

    Out-of-region and missing coordinates are listed in image.info['map_markers']
    with visible=False; no fallback point is invented or clamped onto the map.
    """
    size = tuple(size)
    manifest = read_manifest()
    left, top, width, height = _viewport(size, manifest)
    paper = (17, 35, 47) if night else (247, 246, 237)
    ink = (244, 246, 229) if night else (26, 39, 41)
    image = Image.new("RGB", size, paper)
    with safe_open_image(MAP_PATH, limits=_LIMITS) as source:
        if list(source.size) != manifest["image_size"]:
            raise ValueError("Bay map raster dimensions do not match its projection manifest")
        if night:
            styled = ImageOps.colorize(ImageOps.grayscale(source), "#d1d8c1", "#102533")
        else:
            styled = Image.blend(source.convert("RGB"), Image.new("RGB", source.size, paper), 0.28)
        scaled = styled.resize((width, height), Image.Resampling.LANCZOS)
        image.paste(scaled, (left, top))
        styled.close()
        scaled.close()
    draw = ImageDraw.Draw(image)
    label_font = get_base_ui_font(10, bold=True)
    marker_boxes = []
    for row in rows[:3]:
        point = project_coordinate(row.get("begin_lat"), row.get("begin_lon"), size=size, manifest=manifest)
        if point:
            marker_boxes.append((point[0] - 10, point[1] - 10, point[0] + 10, point[1] + 10))
    label_boxes = []
    label_records = []

    def overlaps(first, second):
        return first[0] < second[2] and first[2] > second[0] and first[1] < second[3] and first[3] > second[1]

    for label, lat, lon, dx, dy in _CITY_LABELS:
        point = project_coordinate(lat, lon, size=size, manifest=manifest)
        if point is None:
            continue
        x, y = point
        label_width = draw.textlength(label, font=label_font)
        # Move only labels, never the real closure coordinates, to keep names legible.
        offsets = ((dx, dy), (dx + 12, dy), (dx + 24, dy), (dx, dy + 18),
                   (dx, dy - 18), (-label_width - 14, 4), (14, 4), (dx, dy + 32))
        candidate = None
        for offset_x, offset_y in offsets:
            tx = max(left + 3, min(left + width - label_width - 3, x + offset_x))
            ty = max(top + 3, min(top + height - 26, y + offset_y))
            box = draw.textbbox((tx, ty), label, font=label_font, anchor="lt", stroke_width=2)
            candidate = (tx, ty, box)
            if not any(overlaps(box, other) for other in marker_boxes + label_boxes):
                break
        tx, ty, box = candidate
        label_boxes.append(box)
        label_records.append({"label": label, "box": list(box)})
        draw.text((tx, ty), label, font=label_font, fill=ink, anchor="lt",
                  stroke_width=2, stroke_fill=paper)
        draw.ellipse((x - 1, y - 1, x + 1, y + 1), fill=ink)

    # Attribution is drawn before points so a real edge location is never hidden.
    draw.rectangle((0, size[1] - 16, size[0], size[1]), fill=paper)
    attribution = "USGS ‧ The National Map"
    attr_font = get_base_ui_font(11, bold=True)
    draw.text((3, size[1] - 14), attribution, font=attr_font, fill=ink, anchor="lt")
    markers = []
    number_font = get_base_ui_font(12, bold=True)
    for index, row in enumerate(rows[:3], 1):
        point = project_coordinate(row.get("begin_lat"), row.get("begin_lon"), size=size, manifest=manifest)
        marker = {"number": index, "id": row.get("id"), "visible": point is not None,
                  "latitude": row.get("begin_lat"), "longitude": row.get("begin_lon")}
        if point is not None:
            x, y = point
            marker["pixel"] = [round(x, 3), round(y, 3)]
            # The circle stays centred on the actual coordinate, even at an edge.
            draw.ellipse((x - 8, y - 8, x + 8, y + 8), fill=(218, 77, 15), outline="white", width=1)
            draw.text((x, y), str(index), font=number_font, fill="white", anchor="mm")
        else:
            marker["reason"] = ("missing_coordinates" if row.get("begin_lat") is None
                                or row.get("begin_lon") is None else "outside_map")
        markers.append(marker)

    hidden = sum(not marker["visible"] for marker in markers)
    if hidden:
        outside = sum(marker.get("reason") == "outside_map" for marker in markers)
        label = f"{hidden} 处在图外" if outside == hidden else f"{hidden} 处未在图中定位"
        draw.rectangle((0, 0, size[0], 13), fill=paper)
        draw.text((3, 1), label, font=label_font, fill=ink, anchor="lt")
    image.info["map_markers"] = markers
    image.info["markers"] = markers
    image.info["map_labels"] = label_records
    image.info["map_projection"] = manifest["projection"]
    image.info["map_attribution"] = attribution
    image.info["map_bounds"] = manifest["mercator_bounds"]
    return image
