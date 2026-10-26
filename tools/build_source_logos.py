"""Fetch the official logos the pages print where they name their data sources.

    python tools/build_source_logos.py

- Caltrans: the logo its own site header serves (dot.ca.gov), for the traffic cameras
  (bay_traffic_cams) and the District 4 lane closures (bay_commute).
- Ticketmaster: its wordmark from Wikimedia Commons (public domain), for Local Events.
- USGS: its green logo from Commons (public domain), cut to the mark and the letters (the
  tagline under them is too small to read on the panel), for The National Map's credit.
- NOAA: its round logo from Commons (public domain), for the tide predictions.
- NWS, CAL FIRE and NASA (for EONET): their logos from Commons (public domain), for 湾区要紧事.
  USGS goes there too, for its earthquakes.
- The cover pages (Daily Art, Comic Covers, Magazines, through base_plugin/cover_gallery): The
  Met, the Art Institute of Chicago, Europeana, the Grand Comics Database, the Internet Archive,
  Wikimedia Commons, the Library of Congress and Gallica from Commons; Harvard Art Museums' white
  wordmark from its own site, kept as a black mask the page tints. Comic Vine's logo is the inline
  SVG its site header draws; the site answers scripts with a bot check, so that one was captured
  in a browser and rasterized once (base_plugin/assets/source_logos/comicvine.png).

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
COVERS = PLUGINS / "base_plugin" / "assets" / "source_logos"
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
             [PLUGINS / "bay_commute" / "assets" / "logos", PLUGINS / "bay_alerts" / "assets" / "logos"], 0.66),
    "noaa": ((COMMONS.format(path="7/79/NOAA_logo.svg", name="NOAA_logo.svg"),
              "https://commons.wikimedia.org/wiki/File:NOAA_logo.svg", PUBLIC_DOMAIN),
             [PLUGINS / "bay_commute" / "assets" / "logos"], 1.0),
    "nws": ((COMMONS.format(path="f/ff/US-NationalWeatherService-Logo.svg", name="US-NationalWeatherService-Logo.svg"),
             "https://commons.wikimedia.org/wiki/File:US-NationalWeatherService-Logo.svg", PUBLIC_DOMAIN),
            [PLUGINS / "bay_alerts" / "assets" / "logos"], 1.0),
    "calfire": ((COMMONS.format(path="4/4b/Logo_of_CAL_FIRE.svg", name="Logo_of_CAL_FIRE.svg"),
                 "https://commons.wikimedia.org/wiki/File:Logo_of_CAL_FIRE.svg", PUBLIC_DOMAIN),
                [PLUGINS / "bay_alerts" / "assets" / "logos"], 1.0),
    "nasa": ((COMMONS.format(path="e/e5/NASA_logo.svg", name="NASA_logo.svg"),
              "https://commons.wikimedia.org/wiki/File:NASA_logo.svg", PUBLIC_DOMAIN),
             [PLUGINS / "bay_alerts" / "assets" / "logos"], 1.0),
    "met": (("https://upload.wikimedia.org/wikipedia/commons/thumb/7/73/The_Metropolitan_Museum_of_Art_Logo.svg/"
             "500px-The_Metropolitan_Museum_of_Art_Logo.svg.png",
             "https://commons.wikimedia.org/wiki/File:The_Metropolitan_Museum_of_Art_Logo.svg", PUBLIC_DOMAIN), [COVERS], 1.0),
    "aic": ((COMMONS.format(path="3/32/Art_Institute_of_Chicago_logo.svg", name="Art_Institute_of_Chicago_logo.svg"),
             "https://commons.wikimedia.org/wiki/File:Art_Institute_of_Chicago_logo.svg", PUBLIC_DOMAIN), [COVERS], 1.0),
    "europeana": ((COMMONS.format(path="4/49/Europeana_logo_black.svg", name="Europeana_logo_black.svg"),
                   "https://commons.wikimedia.org/wiki/File:Europeana_logo_black.svg", "CC BY-SA 4.0 (Wikimedia Commons)"),
                  [COVERS], 1.0),
    "harvard": (("https://harvardartmuseums.org/assets/images/HAM-Wordmark-Knockout.png", "https://harvardartmuseums.org/",
                 "Harvard Art Museums' wordmark from its own site, shown to name the source"), [COVERS], "knockout"),
    "gcd": (("https://upload.wikimedia.org/wikipedia/commons/8/8d/Grand_Comics_Database_logo.png",
             "https://commons.wikimedia.org/wiki/File:Grand_Comics_Database_logo.png", PUBLIC_DOMAIN), [COVERS], 1.0),
    "internet_archive": ((COMMONS.format(path="7/71/Internet_Archive_logo_wide_black.svg", name="Internet_Archive_logo_wide_black.svg"),
                          "https://commons.wikimedia.org/wiki/File:Internet_Archive_logo_wide_black.svg", PUBLIC_DOMAIN),
                         [COVERS], 1.0),
    "wikimedia_commons": ((COMMONS.format(path="4/4a/Commons-logo.svg", name="Commons-logo.svg"),
                           "https://commons.wikimedia.org/wiki/File:Commons-logo.svg", "CC BY-SA 3.0 (Wikimedia Commons)"),
                          [COVERS], 1.0),
    "loc": ((COMMONS.format(path="b/be/Logo_of_the_United_States_Library_of_Congress.svg",
                            name="Logo_of_the_United_States_Library_of_Congress.svg"),
             "https://commons.wikimedia.org/wiki/File:Logo_of_the_United_States_Library_of_Congress.svg", PUBLIC_DOMAIN),
            [COVERS], 1.0),
    "gallica": (("https://upload.wikimedia.org/wikipedia/commons/thumb/e/e8/Logo_Gallica_2025.png/500px-Logo_Gallica_2025.png",
                 "https://commons.wikimedia.org/wiki/File:Logo_Gallica_2025.png", PUBLIC_DOMAIN), [COVERS], 1.0),
}


def _knockout(image):
    """White artwork for a dark header as a black mask: the page tints it with its ink."""
    mask = Image.new("RGBA", image.size, (0, 0, 0, 255))
    mask.putalpha(image.getchannel("A"))
    return mask


def _visible(image):
    return image.crop(image.getchannel("A").point(lambda value: 255 if value > 16 else 0).getbbox())


def main():
    records = {}
    for key, ((url, page, licence), folders, keep) in LOGOS.items():
        response = requests.get(url, headers=HEADERS, timeout=30)
        response.raise_for_status()
        art = _visible(Image.open(io.BytesIO(response.content)).convert("RGBA"))
        if keep == "knockout":
            art, keep = _knockout(art), 1.0
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
