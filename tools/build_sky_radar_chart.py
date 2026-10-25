"""Build Sky Radar's chart basemap and aeronautical features around SFO.

    python tools/build_sky_radar_chart.py

Writes to plugins/flight_radar/assets/chart/:
- chart.png: water (red channel) and shaded-relief darkness (green channel) from USGS The
  National Map, exported in the scope's own projection (equirectangular, longitudes scaled by
  the cosine of SFO's latitude) at PIXELS_PER_NM;
- features.json: runways (both ends), airports and navaids within the chart from OurAirports;
- chart.json: the manifest (bounds, scale, sources, licences, hashes).
Both sources are public domain. The device never contacts them.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from PIL import Image, ImageFilter

REPO_ROOT = Path(__file__).resolve().parents[1]
OUT = REPO_ROOT / "inkypi-weather" / "package" / "InkyPi" / "src" / "plugins" / "flight_radar" / "assets" / "chart"
CENTER = (37.6213, -122.3790)  # SFO
HALF_WIDTH_NM, HALF_HEIGHT_NM = 32.0, 27.0
PIXELS_PER_NM = 12
USGS = "https://basemap.nationalmap.gov/arcgis/rest/services/{service}/MapServer/export"
OURAIRPORTS = "https://davidmegginson.github.io/ourairports-data/{name}.csv"
AIRPORT_TYPES = {"large_airport", "medium_airport", "small_airport"}
NAVAID_TYPES = {"VOR", "VOR-DME", "VORTAC", "NDB", "NDB-DME", "TACAN"}
USER_AGENT = "InkyPi chart build (https://github.com/Feeengyuuu/EpaperSystem)"


def fetch(url, timeout=90):
    with urlopen(Request(url, headers={"User-Agent": USER_AGENT}), timeout=timeout) as response:
        return response.read()


def bounds():
    lat0, lon0 = CENTER
    dlat = HALF_HEIGHT_NM / 60
    dlon = HALF_WIDTH_NM / (60 * math.cos(math.radians(lat0)))
    return {"west": lon0 - dlon, "east": lon0 + dlon, "south": lat0 - dlat, "north": lat0 + dlat}


def export(service, box, size):
    params = {"bbox": f"{box['west']:.5f},{box['south']:.5f},{box['east']:.5f},{box['north']:.5f}",
              "bboxSR": 4326, "imageSR": 4326, "size": f"{size[0]},{size[1]}", "format": "png32",
              "transparent": "true", "f": "image"}
    url = USGS.format(service=service) + "?" + urlencode(params)
    return Image.open(io.BytesIO(fetch(url))).convert("RGBA"), url


def inside(lat, lon, box, margin=0.05):
    return (box["south"] - margin <= lat <= box["north"] + margin
            and box["west"] - margin <= lon <= box["east"] + margin)


def number(value):
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def read_csv(name):
    return list(csv.DictReader(io.StringIO(fetch(OURAIRPORTS.format(name=name), timeout=180).decode("utf-8"))))


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    box = bounds()
    size = (round(2 * HALF_WIDTH_NM * PIXELS_PER_NM), round(2 * HALF_HEIGHT_NM * PIXELS_PER_NM))
    water, water_url = export("USGSHydroCached", box, size)
    relief, relief_url = export("USGSShadedReliefOnly", box, size)
    # Water where the hydrography layer paints; relief darkness from the hillshade's luminance.
    water_mask = water.getchannel("A").point(lambda value: 255 if value > 96 else 0)
    # An opening drops creeks and the marsh symbols a few pixels wide: the scope keeps the Bay,
    # the ocean and the reservoirs.
    water_mask = water_mask.filter(ImageFilter.MinFilter(5)).filter(ImageFilter.MaxFilter(5))
    shade = Image.new("L", size, 0)
    relief_rgb = relief.convert("RGB").convert("L")
    alpha = relief.getchannel("A")
    shade.paste(relief_rgb.point(lambda value: max(0, 235 - value)), mask=alpha)
    chart = Image.merge("RGB", (water_mask, shade, Image.new("L", size, 0)))
    chart.save(OUT / "chart.png", optimize=True)

    airports = {}
    for row in read_csv("airports"):
        lat, lon = number(row.get("latitude_deg")), number(row.get("longitude_deg"))
        if row.get("type") in AIRPORT_TYPES and lat is not None and lon is not None and inside(lat, lon, box):
            airports[row["ident"]] = {"ident": row["ident"], "iata": row.get("iata_code") or "",
                                      "name": row.get("name") or "", "type": row["type"],
                                      "lat": round(lat, 5), "lon": round(lon, 5)}
    runways = []
    for row in read_csv("runways"):
        if row.get("airport_ident") not in airports or row.get("closed") == "1":
            continue
        ends = [number(row.get(key)) for key in ("le_latitude_deg", "le_longitude_deg", "he_latitude_deg", "he_longitude_deg")]
        if None in ends:
            continue
        runways.append({"airport": row["airport_ident"], "ends": [round(value, 5) for value in ends],
                        "width_ft": number(row.get("width_ft")) or 75, "length_ft": number(row.get("length_ft")) or 0,
                        "idents": [row.get("le_ident") or "", row.get("he_ident") or ""]})
    navaids = []
    for row in read_csv("navaids"):
        lat, lon = number(row.get("latitude_deg")), number(row.get("longitude_deg"))
        if row.get("type") in NAVAID_TYPES and lat is not None and lon is not None and inside(lat, lon, box):
            frequency = number(row.get("frequency_khz"))
            navaids.append({"ident": row.get("ident") or "", "name": row.get("name") or "", "type": row["type"],
                            "lat": round(lat, 5), "lon": round(lon, 5),
                            "frequency": (f"{frequency / 1000:.1f}" if frequency and frequency > 10000
                                          else f"{frequency:.0f}" if frequency else "")})
    with_runways = {runway["airport"] for runway in runways}
    features = {"airports": [airport for ident, airport in sorted(airports.items()) if ident in with_runways],
                "runways": runways, "navaids": sorted(navaids, key=lambda item: item["ident"])}
    (OUT / "features.json").write_text(json.dumps(features, indent=1) + "\n", encoding="utf-8")

    manifest = {
        "schema": 1, "projection": "equirectangular, longitude scaled by cos(center latitude)",
        "center": list(CENTER), "pixels_per_nm": PIXELS_PER_NM, "image_size": list(size), "bounds_wgs84": box,
        "channels": {"R": "water", "G": "shaded_relief_darkness"},
        "sources": [water_url, relief_url] + [OURAIRPORTS.format(name=name) for name in ("airports", "runways", "navaids")],
        "license": "Public domain (USGS The National Map; OurAirports data)",
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "sha256": {name: hashlib.sha256((OUT / name).read_bytes()).hexdigest() for name in ("chart.png", "features.json")},
    }
    (OUT / "chart.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"size": size, "airports": len(features["airports"]), "runways": len(runways),
                      "navaids": len(navaids)}))


if __name__ == "__main__":
    main()
