"""Fetch the official logos the pages print where they name their data sources.

    python tools/build_source_logos.py

- Caltrans: the logo its own site header serves (dot.ca.gov), for the traffic cameras
  (bay_traffic_cams) and the District 4 lane closures (bay_commute).
- Ticketmaster: its wordmark from Wikimedia Commons (public domain), for Local Events.
- USGS: its green logo from Commons (public domain), cut to the mark and the letters (the
  tagline under them is too small to read on the panel), for The National Map's credit.
- NOAA: its round logo from Commons (public domain), for the tide predictions.

Each is cropped to its visible pixels and scaled to at most 64 px tall into the plugin's
assets; logos.source.json beside each records where it came from.
"""

from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path

import requests
from PIL import Image

REPO_ROOT = Path(__file__).resolve().parents[1]
PLUGINS = REPO_ROOT / "inkypi-weather" / "package" / "InkyPi" / "src" / "plugins"
HEADERS = {"User-Agent": "Mozilla/5.0 InkyPi asset build (https://github.com/Feeengyuuu/EpaperSystem)"}
COMMONS = "https://upload.wikimedia.org/wikipedia/commons/thumb/{path}/500px-{name}.png"
PUBLIC_DOMAIN = "Public domain (Wikimedia Commons)"
CALTRANS = ("https://dot.ca.gov/-/media/dot-media/images/logos/caltrans/caltranslogo.png", "https://dot.ca.gov/",
            "Caltrans' official logo from its own site header, shown to name the source")
# key -> ((source, page, licence), [plugin asset folders], top share of the art kept)
LOGOS = {
    "caltrans": (CALTRANS, [PLUGINS / "bay_traffic_cams" / "assets" / "logos", PLUGINS / "bay_commute" / "assets" / "logos"], 1.0),
    "ticketmaster": ((COMMONS.format(path="7/7d/TicketMaster_wordmark.svg", name="TicketMaster_wordmark.svg"),
                      "https://commons.wikimedia.org/wiki/File:TicketMaster_wordmark.svg", PUBLIC_DOMAIN),
                     [PLUGINS / "ticketmaster_events" / "assets"], 1.0),
    "usgs": ((COMMONS.format(path="1/1c/USGS_logo_green.svg", name="USGS_logo_green.svg"),
              "https://commons.wikimedia.org/wiki/File:USGS_logo_green.svg", PUBLIC_DOMAIN),
             [PLUGINS / "bay_commute" / "assets" / "logos"], 0.66),
    "noaa": ((COMMONS.format(path="7/79/NOAA_logo.svg", name="NOAA_logo.svg"),
              "https://commons.wikimedia.org/wiki/File:NOAA_logo.svg", PUBLIC_DOMAIN),
             [PLUGINS / "bay_commute" / "assets" / "logos"], 1.0),
}


def _visible(image):
    return image.crop(image.getchannel("A").point(lambda value: 255 if value > 16 else 0).getbbox())


def main():
    records = {}
    for key, ((url, page, licence), folders, keep) in LOGOS.items():
        response = requests.get(url, headers=HEADERS, timeout=30)
        response.raise_for_status()
        art = _visible(Image.open(io.BytesIO(response.content)).convert("RGBA"))
        if keep < 1:
            art = _visible(art.crop((0, 0, art.width, round(art.height * keep))))
        if art.height > 64:
            art = art.resize((round(art.width * 64 / art.height), 64), Image.Resampling.LANCZOS)
        for folder in folders:
            folder.mkdir(parents=True, exist_ok=True)
            target = folder / f"{key}.png"
            art.save(target, optimize=True)
            entry = {"source": url, "page": page, "licence": licence,
                     "source_sha256": hashlib.sha256(response.content).hexdigest(), "size": list(art.size),
                     "sha256": hashlib.sha256(target.read_bytes()).hexdigest()}
            if keep < 1:
                entry["processing"] = f"the top {keep:.0%} kept (the mark and letters, not the tagline)"
            records.setdefault(folder, {})[key] = entry
        print(key, art.size)
    for folder, logos in records.items():
        path = folder / ("logos.source.json" if folder.name == "logos" else "source_logos.source.json")
        existing = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
        existing.setdefault("logos", {}).update(logos)
        path.write_text(json.dumps(existing, indent=2) + "\n", encoding="utf-8", newline="\n")


if __name__ == "__main__":
    main()
