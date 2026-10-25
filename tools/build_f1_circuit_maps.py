"""Bundle each F1 circuit's shape for the sports dashboard's race band.

    python tools/build_f1_circuit_maps.py

The track lines come from Tomislav Bacinger's f1-circuits GeoJSON (MIT licence, unofficial;
https://github.com/bacinger/f1-circuits). Each circuit is projected to metres round its middle,
simplified (Ramer-Douglas-Peucker), turned a quarter when much taller than wide, and scaled so its
longer side is 1, with y pointing down, then
written to plugins/sports_dashboard/assets/decor/f1_circuits.json under Jolpica's circuitId.
Neither the start line nor the direction of travel is reliable in the source, so only the shape is
kept.
"""

from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen

REPO_ROOT = Path(__file__).resolve().parents[1]
OUT = (REPO_ROOT / "inkypi-weather" / "package" / "InkyPi" / "src" / "plugins" / "sports_dashboard" / "assets" / "decor"
       / "f1_circuits.json")
SOURCE = "https://raw.githubusercontent.com/bacinger/f1-circuits/master/f1-circuits.geojson"
USER_AGENT = "InkyPi asset build (https://github.com/Feeengyuuu/EpaperSystem)"
# Jolpica circuitId -> the GeoJSON feature's id.
CIRCUITS = {
    "albert_park": "au-1953", "americas": "us-2012", "bahrain": "bh-2002", "baku": "az-2016",
    "catalunya": "es-1991", "hungaroring": "hu-1986", "imola": "it-1953", "interlagos": "br-1940",
    "jeddah": "sa-2021", "losail": "qa-2004", "madring": "es-2026", "marina_bay": "sg-2008",
    "miami": "us-2022", "monaco": "mc-1929", "monza": "it-1922", "red_bull_ring": "at-1969",
    "rodriguez": "mx-1962", "sepang": "my-1999", "shanghai": "cn-2004", "silverstone": "gb-1948",
    "spa": "be-1925", "suzuka": "jp-1962", "vegas": "us-2023", "villeneuve": "ca-1978",
    "yas_marina": "ae-2009", "zandvoort": "nl-1948",
}
# Simplification tolerance as a share of the circuit's longer side.
TOLERANCE = 0.004


def fetch(url, timeout=60):
    with urlopen(Request(url, headers={"User-Agent": USER_AGENT}), timeout=timeout) as response:
        return response.read()


def simplified(points, tolerance):
    """Ramer-Douglas-Peucker on (x, y) points."""
    if len(points) < 3:
        return points
    (x0, y0), (x1, y1) = points[0], points[-1]
    length = math.hypot(x1 - x0, y1 - y0)
    distances = [abs((x1 - x0) * (y0 - y) - (x0 - x) * (y1 - y0)) / length if length else math.hypot(x - x0, y - y0)
                 for x, y in points[1:-1]]
    index = max(range(len(distances)), key=distances.__getitem__)
    if distances[index] <= tolerance:
        return [points[0], points[-1]]
    split = index + 1
    return simplified(points[:split + 1], tolerance)[:-1] + simplified(points[split:], tolerance)


def shape(coordinates):
    """Lon/lat to points scaled so the longer side is 1, y down, closed; a circuit much taller than
    wide is turned a quarter to lie across the band's wide space."""
    lat0 = math.radians(sum(lat for _lon, lat in coordinates) / len(coordinates))
    metres = [(lon * math.cos(lat0) * 111_320, -lat * 110_540) for lon, lat in coordinates]
    if metres[0] != metres[-1]:
        metres.append(metres[0])
    xs, ys = [x for x, _y in metres], [y for _x, y in metres]
    if max(ys) - min(ys) > 1.2 * (max(xs) - min(xs)):
        metres = [(-y, x) for x, y in metres]
        xs, ys = [x for x, _y in metres], [y for _x, y in metres]
    span = max(max(xs) - min(xs), max(ys) - min(ys))
    scaled = [((x - min(xs)) / span, (y - min(ys)) / span) for x, y in metres]
    return [[round(x, 4), round(y, 4)] for x, y in simplified(scaled, TOLERANCE)]


def main():
    features = {feature["properties"]["id"]: feature for feature in json.loads(fetch(SOURCE))["features"]}
    circuits = {}
    for circuit, feature_id in sorted(CIRCUITS.items()):
        feature = features[feature_id]
        geometry = feature["geometry"]
        coordinates = geometry["coordinates"] if geometry["type"] == "LineString" else geometry["coordinates"][0]
        circuits[circuit] = {"source_id": feature_id, "points": shape(coordinates)}
        print(circuit, len(coordinates), "->", len(circuits[circuit]["points"]))
    payload = {
        "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": SOURCE,
        "license": "MIT (c) 2019-2025 Tomislav Bacinger; unofficial, not endorsed by Formula One Licensing B.V.",
        "note": "Shape only, longer side 1, y down; the start line and direction are not in the data.",
        "circuits": circuits,
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8",
                   newline="\n")


if __name__ == "__main__":
    main()
