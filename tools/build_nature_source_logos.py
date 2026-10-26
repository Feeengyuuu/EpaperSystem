"""Fetch the official logos 湾区德鲁伊 (Nature Nearby) prints where it names its sources.

    python tools/build_nature_source_logos.py

iNaturalist's is the SVG its own site header serves (its trademark, shown to name the
source), rasterized by build_planet_now_logos' headless browser; eBird's is the public-domain
Commons file. Each is cropped to its visible pixels, scaled to at most 64 px tall and written
to the plugin's assets/logos/<key>.png; logos.source.json records where each came from.
"""

from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path

import requests
from PIL import Image

from build_planet_now_logos import HEADERS, _commons, _rasterize, _trim

REPO_ROOT = Path(__file__).resolve().parents[1]
OUT = REPO_ROOT / "inkypi-weather" / "package" / "InkyPi" / "src" / "plugins" / "nature_nearby" / "assets" / "logos"
INATURALIST_SVG = ("https://static.inaturalist.org/sites/1-logo.svg", "https://www.inaturalist.org/")
EBIRD = "File:EBird-2024-logo.svg"


def _save(key, logo, entry, record):
    target = OUT / f"{key}.png"
    logo.save(target, optimize=True)
    record["logos"][key] = {**entry, "sha256": hashlib.sha256(target.read_bytes()).hexdigest(), "size": list(logo.size)}
    print(key, logo.size, entry["licence"])


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    record = {"logos": {}}
    url, page = INATURALIST_SVG
    response = requests.get(url, headers={**HEADERS, "User-Agent": "Mozilla/5.0 " + HEADERS["User-Agent"]}, timeout=30)
    response.raise_for_status()
    _save("inaturalist", _trim(_rasterize(response.content)),
          {"file": url, "page": page, "rendered": "headless browser, transparent background",
           "licence": "the site's own logo (its trademark), shown to name the source",
           "source_sha256": hashlib.sha256(response.content).hexdigest()}, record)
    data, page, rendered, licence = _commons(EBIRD)
    _save("ebird", _trim(Image.open(io.BytesIO(data)).convert("RGBA")),
          {"file": EBIRD, "page": page, "rendered": rendered, "licence": licence,
           "source_sha256": hashlib.sha256(data).hexdigest()}, record)
    (OUT / "logos.source.json").write_text(json.dumps(record, indent=1, ensure_ascii=False) + "\n", encoding="utf-8",
                                           newline="\n")


if __name__ == "__main__":
    main()
