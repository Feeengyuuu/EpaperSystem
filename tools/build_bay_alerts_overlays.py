"""One-time export of the Bay Alerts map overlays: NWS alert zones and named active faults.

The plugin never contacts these services at runtime. This script downloads the public-domain
shapes once, keeps what the map (zones) or the quake radius (faults) can use, simplifies them
and writes two compact JSON assets next to the map raster:

    python tools/build_bay_alerts_overlays.py [--work-dir .tmp/bay-alerts-overlays]

Zones: NWS forecast / fire-weather zones (CAZ...) of the San Francisco (MTR) and Sacramento
(STO) offices and the Bay Area county zones (CAC...), clipped to the map frame. Alerts name
these zones in ``affectedZones``. Faults: historic and Holocene traces of the region's named
faults from the USGS Quaternary Fault and Fold Database, so a quake can name its nearest fault.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import time
from datetime import datetime, timezone
from pathlib import Path

import requests


REPO_ROOT = Path(__file__).resolve().parents[1]
ASSETS = REPO_ROOT / "inkypi-weather" / "package" / "InkyPi" / "src" / "plugins" / "bay_alerts" / "assets"
USER_AGENT = "InkyPi BayAlerts/1.0 (personal e-paper display)"
NWS = "https://api.weather.gov"
QFAULTS = "https://earthquake.usgs.gov/arcgis/rest/services/haz/Qfaults/MapServer/21/query"
OFFICES = ("MTR", "STO")
BAY_COUNTY_ZONES = ("CAC001", "CAC013", "CAC041", "CAC055", "CAC075", "CAC081", "CAC085", "CAC087",
                    "CAC095", "CAC097")
ZONE_MARGIN = 0.03  # degrees kept beyond the map frame, so clipped edges stay off the map
ZONE_TOLERANCE = 0.0012  # ~0.4 map pixel
# Covers the default 150 km quake radius around the middle of the Bay.
FAULT_BOX = (-123.8, 36.1, -120.2, 39.0)
# Traces are kept as the grid cells they cross, which is all "nearest fault" needs.
FAULT_CELL = 0.005  # ~0.5 km
FAULT_AGES = {"historic", "latest Quaternary"}
# Only the region's well-known faults: a quake near San Ramon should name the Calaveras fault
# that people know, not a minor strand a few hundred metres closer.
FAULT_NAMES = {
    "San Andreas fault zone": "圣安德烈亚斯断层", "Hayward fault zone": "海沃德断层",
    "Calaveras fault zone": "卡拉韦拉斯断层", "Rodgers Creek fault": "罗杰斯溪断层",
    "San Gregorio fault zone": "圣格雷戈里奥断层", "Maacama fault zone": "马卡马断层",
    "Greenville fault zone": "格林维尔断层", "Green Valley fault": "格林谷断层", "Concord fault": "康科德断层",
    "Sargent fault zone": "萨金特断层", "Ortigalita fault zone": "奥蒂加利塔断层", "West Napa fault": "西纳帕断层",
    "Hunting Creek-Berryessa fault system": "贝里耶萨断层", "Quien Sabe fault": "基恩萨贝断层",
    "Zayante-Vergeles fault zone": "扎扬特断层", "Monte Vista-Shannon fault zone": "蒙特维斯塔断层",
    "Bartlett Springs fault system": "巴特利特泉断层", "Konocti Bay fault zone": "科诺克蒂湾断层",
    "Collayomi fault zone": "科拉约米断层",
}


def _get(session, url, params, cache):
    if cache.exists():
        return json.loads(cache.read_text(encoding="utf-8"))
    response = session.get(url, params=params, timeout=60)
    response.raise_for_status()
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(response.text, encoding="utf-8")
    time.sleep(0.2)
    return response.json()


def _simplify(points, tolerance):
    """Douglas-Peucker on a list of (x, y)."""
    if len(points) < 3:
        return list(points)
    keep = [False] * len(points)
    keep[0] = keep[-1] = True
    stack = [(0, len(points) - 1)]
    while stack:
        first, last = stack.pop()
        (x0, y0), (x1, y1) = points[first], points[last]
        length = math.hypot(x1 - x0, y1 - y0)
        best, index = 0.0, None
        for position in range(first + 1, last):
            x, y = points[position]
            if length:
                distance = abs((x1 - x0) * (y0 - y) - (x0 - x) * (y1 - y0)) / length
            else:
                distance = math.hypot(x - x0, y - y0)
            if distance > best:
                best, index = distance, position
        if index is not None and best > tolerance:
            keep[index] = True
            stack.extend(((first, index), (index, last)))
    return [point for point, kept in zip(points, keep) if kept]


def _clip(ring, box):
    """Sutherland-Hodgman clip of a closed ring to an axis-aligned box (west, south, east, north)."""
    west, south, east, north = box
    edges = (
        (lambda p: p[0] >= west, lambda a, b: (west, a[1] + (b[1] - a[1]) * (west - a[0]) / (b[0] - a[0]))),
        (lambda p: p[0] <= east, lambda a, b: (east, a[1] + (b[1] - a[1]) * (east - a[0]) / (b[0] - a[0]))),
        (lambda p: p[1] >= south, lambda a, b: (a[0] + (b[0] - a[0]) * (south - a[1]) / (b[1] - a[1]), south)),
        (lambda p: p[1] <= north, lambda a, b: (a[0] + (b[0] - a[0]) * (north - a[1]) / (b[1] - a[1]), north)),
    )
    points = list(ring)
    for inside, cross in edges:
        if not points:
            break
        source, points = points, []
        previous = source[-1]
        for current in source:
            if inside(current):
                if not inside(previous):
                    points.append(cross(previous, current))
                points.append(current)
            elif inside(previous):
                points.append(cross(previous, current))
            previous = current
    return points


def _polygons(geometry):
    if not isinstance(geometry, dict):
        return []
    if geometry.get("type") == "Polygon":
        return [geometry["coordinates"]]
    if geometry.get("type") == "MultiPolygon":
        return geometry["coordinates"]
    return []


def _rounded(points):
    return [[round(x, 3), round(y, 3)] for x, y in points]


def map_frame():
    manifest = json.loads((ASSETS / "bay-alerts-map.json").read_text(encoding="utf-8"))
    bounds = manifest["mercator_bounds"]

    def to_wgs84(x, y):
        return math.degrees(x / 6378137.0), math.degrees(2 * math.atan(math.exp(y / 6378137.0)) - math.pi / 2)

    west, south = to_wgs84(bounds["xmin"], bounds["ymin"])
    east, north = to_wgs84(bounds["xmax"], bounds["ymax"])
    return west, south, east, north


def build_zones(session, work):
    west, south, east, north = map_frame()
    box = (west - ZONE_MARGIN, south - ZONE_MARGIN, east + ZONE_MARGIN, north + ZONE_MARGIN)
    wanted = []
    listing = _get(session, f"{NWS}/zones", {"area": "CA", "type": "forecast"}, work / "zones-forecast.json")
    for feature in listing.get("features", []):
        props = feature.get("properties") or {}
        if set(props.get("cwa") or []) & set(OFFICES):
            wanted.append(("forecast", props["id"]))
    wanted += [("county", zone) for zone in BAY_COUNTY_ZONES]
    zones, sources = {}, []
    for kind, zone in wanted:
        url = f"{NWS}/zones/{kind}/{zone}"
        feature = _get(session, url, None, work / f"zone-{kind}-{zone}.json")
        polygons = []
        for polygon in _polygons(feature.get("geometry")):
            rings = []
            for ring in polygon:
                clipped = _clip([tuple(point[:2]) for point in ring], box)
                simple = _simplify(clipped, ZONE_TOLERANCE)
                if len(simple) >= 3:
                    rings.append(_rounded(simple))
                elif not rings:
                    break  # the exterior is gone, so its holes go too
            if rings:
                polygons.append(rings)
        if polygons:
            zones[zone] = {"name": (feature.get("properties") or {}).get("name", zone)[:80], "polygons": polygons}
            sources.append(url)
    return {"schema": 1, "frame_wgs84": [round(value, 4) for value in (west, south, east, north)],
            "zones": dict(sorted(zones.items())), "sources": sources}


def build_faults(session, work):
    params = {
        "where": "1=1", "geometry": ",".join(str(value) for value in FAULT_BOX),
        "geometryType": "esriGeometryEnvelope", "inSR": 4326, "spatialRel": "esriSpatialRelIntersects",
        "outFields": "fault_name,age", "returnGeometry": "true", "outSR": 4326, "f": "geojson",
    }
    features, offset = [], 0
    while True:
        page = _get(session, QFAULTS, {**params, "resultOffset": offset}, work / f"qfaults-{offset}.json")
        batch = page.get("features", [])
        features += batch
        if not batch or not (page.get("exceededTransferLimit")
                             or (page.get("properties") or {}).get("exceededTransferLimit")):
            break
        offset += len(batch)
    traces = {}
    for feature in features:
        props = feature.get("properties") or {}
        name = FAULT_NAMES.get(props.get("fault_name"))
        geometry = feature.get("geometry") or {}
        if not name or props.get("age") not in FAULT_AGES:
            continue
        lines = geometry.get("coordinates") or []
        if geometry.get("type") == "LineString":
            lines = [lines]
        elif geometry.get("type") != "MultiLineString":
            continue
        cells = traces.setdefault(name, set())
        for line in lines:
            for (x0, y0), (x1, y1) in zip(line, line[1:]):
                steps = max(1, math.ceil(max(abs(x1 - x0), abs(y1 - y0)) / (FAULT_CELL / 2)))
                for step in range(steps + 1):
                    x, y = x0 + (x1 - x0) * step / steps, y0 + (y1 - y0) * step / steps
                    if FAULT_BOX[0] <= x <= FAULT_BOX[2] and FAULT_BOX[1] <= y <= FAULT_BOX[3]:
                        cells.add((round((x - FAULT_BOX[0]) / FAULT_CELL), round((y - FAULT_BOX[1]) / FAULT_CELL)))
    faults = [{"name": name, "cells": [value for cell in sorted(cells) for value in cell]}
              for name, cells in sorted(traces.items())]
    return {"schema": 1, "origin_wgs84": list(FAULT_BOX[:2]), "cell_degrees": FAULT_CELL,
            "box_wgs84": list(FAULT_BOX), "ages": sorted(FAULT_AGES), "faults": faults, "sources": [QFAULTS]}


def _write(name, data, license_text):
    data.update({
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "license": license_text, "attribution": "NWS" if "zones" in data else "USGS",
    })
    path = ASSETS / name
    path.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8", newline="\n")
    print(path, path.stat().st_size, hashlib.sha256(path.read_bytes()).hexdigest())


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--work-dir", type=Path, default=REPO_ROOT / ".tmp" / "bay-alerts-overlays")
    args = parser.parse_args()
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT, "Accept": "application/geo+json, application/json"})
    _write("bay-alerts-zones.json", build_zones(session, args.work_dir),
           "Public domain (NOAA National Weather Service zones)")
    _write("bay-alerts-faults.json", build_faults(session, args.work_dir),
           "Public domain (USGS Quaternary Fault and Fold Database)")


if __name__ == "__main__":
    main()
