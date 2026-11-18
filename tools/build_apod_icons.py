"""Build the line icons NASAPics x Space Weather draws in place of its labels.

    python tools/build_apod_icons.py [--stroke 2.25]

They are Tabler Icons (https://tabler.io/icons, MIT licence) at the pinned version, fetched from
jsDelivr's copy of the npm package, drawn by PyMuPDF with the stroke given on Tabler's 24-unit
grid and saved white on transparent, SIZE px square, as plugins/apod/assets/icons/<name>.png;
the page scales them down (apod_page._icon). icons.source.json records each icon's Tabler name
and hashes, and TABLER-LICENSE.txt is the package's licence.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import fitz
import requests
from PIL import Image

REPO_ROOT = Path(__file__).resolve().parents[1]
OUT = REPO_ROOT / "inkypi-weather" / "package" / "InkyPi" / "src" / "plugins" / "apod" / "assets" / "icons"
VERSION = "3.52.0"
PACKAGE = f"https://cdn.jsdelivr.net/npm/@tabler/icons@{VERSION}"
HEADERS = {"User-Agent": "epaper-system-asset-builder/1.0"}
SIZE = 96
STROKE = 2.25
# The page's name for each icon and the Tabler outline icon it is.
ICONS = {
    "kp": "magnetic",
    "geomagnetic_storm": "world-bolt",
    "radio_blackout": "broadcast-off",
    "radiation_storm": "radioactive",
    "solar_wind": "sun-wind",
    "magnet": "magnet",
    "flare": "sun-electricity",
    "probability": "percentage",
    "forecast": "calendar-time",
    "neo": "meteor",
    "closest": "focus-2",
    "peak": "arrow-bar-to-up",
    "observed": "eye",
    "fetched": "refresh",
    "alert": "alert-triangle",
    "source": "satellite",
    "telescope": "telescope",
}


def _fetch(path):
    response = requests.get(f"{PACKAGE}/{path}", headers=HEADERS, timeout=30)
    response.raise_for_status()
    return response.content


def rasterize(svg, size=SIZE, stroke=STROKE):
    """The icon white on transparent, ``size`` px square, its lines ``stroke`` wide on the 24 grid."""
    text = svg.decode("utf-8").replace("currentColor", "#000000")
    if 'stroke-width="2"' not in text:
        raise ValueError("not a Tabler outline icon: no stroke-width=\"2\"")
    text = text.replace('stroke-width="2"', f'stroke-width="{stroke:g}"')
    with fitz.open(stream=text.encode("utf-8"), filetype="svg") as document:
        page = document[0]
        scale = size / page.rect.width
        pixmap = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=True)
        coverage = Image.frombytes("RGBA", (pixmap.width, pixmap.height), pixmap.samples).getchannel("A").copy()
    icon = Image.new("RGBA", coverage.size, (255, 255, 255, 0))
    icon.putalpha(coverage)
    return icon


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--stroke", type=float, default=STROKE)
    arguments = parser.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    licence = _fetch("LICENSE")
    (OUT / "TABLER-LICENSE.txt").write_bytes(licence.replace(b"\r\n", b"\n"))
    record = {
        "source": f"Tabler Icons {VERSION} (https://tabler.io/icons), outline set, from {PACKAGE}/icons/outline/",
        "licence": "MIT, TABLER-LICENSE.txt",
        "rendered": f"PyMuPDF, stroke {arguments.stroke:g} on the 24-unit grid, white on transparent, {SIZE} px",
        "icons": {},
    }
    for name, tabler in ICONS.items():
        svg = _fetch(f"icons/outline/{tabler}.svg")
        target = OUT / f"{name}.png"
        rasterize(svg, stroke=arguments.stroke).save(target, optimize=True)
        record["icons"][name] = {"tabler": tabler, "svg_sha256": hashlib.sha256(svg).hexdigest(),
                                 "sha256": hashlib.sha256(target.read_bytes()).hexdigest()}
        print(name, tabler)
    (OUT / "icons.source.json").write_text(json.dumps(record, indent=1, ensure_ascii=False) + "\n", encoding="utf-8",
                                           newline="\n")


if __name__ == "__main__":
    main()
