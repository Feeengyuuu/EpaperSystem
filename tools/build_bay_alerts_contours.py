"""Build the Bay Alerts map's elevation contours from USGS 3DEP.

    python tools/build_bay_alerts_contours.py

Reads the Bay map's projected bounds and size from plugins/bay_alerts/assets/bay-alerts-map.json,
exports 3DEP bare-earth elevation on that grid at SCALE times its size, and writes:
- bay-alerts-map-contours.png: a greyscale mask at the map's size, MINOR on every INTERVAL_M
  contour and 255 on every INDEX_EVERY-th (index) one, nothing over the map's water;
- bay-alerts-map-contours.json: where it came from, the intervals and the hashes.
3DEP is public domain. The device never contacts it.
"""

from __future__ import annotations

import hashlib
import io
import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import numpy
from PIL import Image

REPO_ROOT = Path(__file__).resolve().parents[1]
ASSETS = REPO_ROOT / "inkypi-weather" / "package" / "InkyPi" / "src" / "plugins" / "bay_alerts" / "assets"
ELEVATION = "https://elevation.nationalmap.gov/arcgis/rest/services/3DEPElevation/ImageServer/exportImage"
USER_AGENT = "InkyPi contour build (https://github.com/Feeengyuuu/EpaperSystem)"
SCALE = 4
INTERVAL_M = 100
INDEX_EVERY = 5
MINOR = 150
# The elevation is smoothed this much (export pixels) first, so contours follow the land's
# shape rather than every gully.
SMOOTHING = 2.0


def export_elevation(bounds, size):
    params = {"bbox": f"{bounds['xmin']},{bounds['ymin']},{bounds['xmax']},{bounds['ymax']}",
              "bboxSR": 3857, "imageSR": 3857, "size": f"{size[0]},{size[1]}", "format": "tiff",
              "pixelType": "F32", "noDataInterpretation": "esriNoDataMatchAny",
              "interpolation": "RSP_BilinearInterpolation", "compression": "None", "f": "image"}
    url = ELEVATION + "?" + urlencode(params)
    with urlopen(Request(url, headers={"User-Agent": USER_AGENT}), timeout=180) as response:
        return response.read(), url


def blur(values, sigma):
    """A separable Gaussian blur of a float grid (Pillow blurs no float images)."""
    reach = int(3 * sigma)
    kernel = numpy.exp(-numpy.arange(-reach, reach + 1) ** 2 / (2 * sigma * sigma))
    kernel /= kernel.sum()
    padded = numpy.pad(values, reach, mode="edge")
    rows = numpy.apply_along_axis(lambda row: numpy.convolve(row, kernel, mode="valid"), 1, padded)
    return numpy.apply_along_axis(lambda column: numpy.convolve(column, kernel, mode="valid"), 0, rows)


def contour_mask(metres, levels):
    """True where the band of ``levels`` changes between a pixel and its right or lower neighbour."""
    bands = numpy.floor(metres / levels).astype(numpy.int32)
    edge = numpy.zeros(bands.shape, dtype=bool)
    edge[:, :-1] |= bands[:, :-1] != bands[:, 1:]
    edge[:-1, :] |= bands[:-1, :] != bands[1:, :]
    return edge & (metres > 0)


def main():
    manifest = json.loads((ASSETS / "bay-alerts-map.json").read_text(encoding="utf-8"))
    size = tuple(manifest["image_size"])
    big = (size[0] * SCALE, size[1] * SCALE)
    raw, url = export_elevation(manifest["mercator_bounds"], big)
    with Image.open(io.BytesIO(raw)) as tiff:
        elevation = numpy.asarray(tiff, dtype=numpy.float32)
    if elevation.shape != (big[1], big[0]):
        raise SystemExit(f"3DEP returned {elevation.shape}, expected {(big[1], big[0])}")
    # NoData comes back as a huge float; the Bay tops out near 1,300 m.
    elevation = numpy.where(numpy.isfinite(elevation) & (elevation > -1000) & (elevation < 5000), elevation, 0.0)
    # Land below sea level (the Delta's islands) reads as sea level, so no contour traces its levees.
    smooth = numpy.maximum(blur(elevation, SMOOTHING), 0.0)
    minor = contour_mask(smooth, INTERVAL_M)
    index = contour_mask(smooth, INTERVAL_M * INDEX_EVERY)
    lines = numpy.where(index, 255, numpy.where(minor, MINOR, 0)).astype(numpy.uint8)
    # Down to the map's size, keeping each line's strongest pixel so a line stays one pixel wide.
    lines = lines.reshape(size[1], SCALE, size[0], SCALE).max(axis=(1, 3))
    with Image.open(ASSETS / "bay-alerts-map.png") as layers:
        water = numpy.asarray(layers.convert("RGB").split()[0]) > 127
    lines[water] = 0
    out = ASSETS / "bay-alerts-map-contours.png"
    Image.fromarray(lines, mode="L").save(out, optimize=True)
    record = {
        "schema": 1,
        "image_size": list(size),
        "source": url,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "license": "Public domain (USGS 3D Elevation Program)",
        "interval_m": INTERVAL_M,
        "index_every": INDEX_EVERY,
        "values": {"minor": MINOR, "index": 255},
        "processing": f"3DEP bare earth at {SCALE}x the map's grid, Gaussian {SMOOTHING} px, band edges at "
                      f"{INTERVAL_M} m (index every {INTERVAL_M * INDEX_EVERY} m), max-pooled to the map, "
                      "cleared over the map's water",
        "source_sha256": hashlib.sha256(raw).hexdigest(),
        "sha256": hashlib.sha256(out.read_bytes()).hexdigest(),
    }
    (ASSETS / "bay-alerts-map-contours.json").write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8",
                                                          newline="\n")
    print(out, int((lines == MINOR).sum()), "minor px,", int((lines == 255).sum()), "index px")


if __name__ == "__main__":
    main()
