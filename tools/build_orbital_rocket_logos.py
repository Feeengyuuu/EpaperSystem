"""Fetch the official rocket and mission logos Orbital Signal shows beside a launch.

    python tools/build_orbital_rocket_logos.py [key ...]    # only the named logos when given

Most are Wikimedia Commons files, rendered by Commons to a PNG (transparency kept). Rivada
Space Networks' is the logo its own site header shows, an SVG drawn by a headless Chrome or
Edge (tools/build_planet_now_logos.py). Each logo is cropped to its visible pixels, scaled to
at most 96 px tall and written to the plugin's assets/rockets/<key>.png. rockets.source.json
records where each came from, its licence and hashes. Colours are untouched: the page shows
them as they are. A lockup marked "emblem" keeps only what stands above its first clear
band: its symbol without the lettering under it, or its name without the line under it.
"""

from __future__ import annotations

import hashlib
import io
import json
import sys
from pathlib import Path

import requests
from PIL import Image

from build_planet_now_logos import _rasterize

REPO_ROOT = Path(__file__).resolve().parents[1]
OUT = (REPO_ROOT / "inkypi-weather" / "package" / "InkyPi" / "src" / "plugins" / "orbital_signal"
       / "assets" / "rockets")
API = "https://commons.wikimedia.org/w/api.php"
HEADERS = {"User-Agent": "EpaperSystem-OrbitalSignal/1.0 (https://github.com/Feeengyuuu/EpaperSystem)"}
SITE_HEADERS = {"User-Agent": "Mozilla/5.0 InkyPi asset build (https://github.com/Feeengyuuu/EpaperSystem)"}
# key: (Commons file, what it marks, how much of it to keep)
LOGOS = {
    "falcon9": ("File:Falcon 9 logo.svg", "rocket", "whole"),
    "falcon_heavy": ("File:Falcon Heavy logo.svg", "rocket", "whole"),
    "electron": ("File:Electron rocket logo.svg", "rocket", "whole"),
    "neutron": ("File:Neutron rocket logo.svg", "rocket", "whole"),
    "vulcan": ("File:Vulcan logo.svg", "rocket", "whole"),
    "atlas": ("File:Atlas V logo.svg", "rocket", "emblem"),
    "sls": ("File:SLS-black (1079346706222).png", "rocket", "whole"),
    "h3": ("File:H3 logo.svg", "rocket", "whole"),
    "starlink": ("File:Starlink Logo 2024.svg", "mission", "emblem"),
    "amazon_leo": ("File:Amazon Leo logo.svg", "mission", "whole"),
    "ussf": ("File:United States Space Force logo.svg", "mission", "emblem"),
    "nro": ("File:NRO.svg", "mission", "whole"),
    "artemis": ("File:Artemis program (original without wordmark).svg", "mission", "whole"),
    "starliner": ("File:Boeing Starliner logo.svg", "mission", "whole"),
}
# key: (the file on the operator's own site, the page that shows it, what it marks, how much
# of it to keep, why the page may show it)
SITE_LOGOS = {
    "rivada": ("https://rivadaspace.com/wp-content/uploads/2025/01/logo-dark.svg", "https://rivadaspace.com/",
               "mission", "emblem",
               "Rivada Space Networks' logo from its own site header, shown to name its missions"),
}
MAX_HEIGHT = 96


def fetch(title):
    response = requests.get(API, params={
        "action": "query", "titles": title, "prop": "imageinfo", "format": "json",
        "iiprop": "url|extmetadata", "iiurlwidth": 640,
    }, headers=HEADERS, timeout=30)
    response.raise_for_status()
    page = next(iter(response.json()["query"]["pages"].values()))
    info = page["imageinfo"][0]
    image = requests.get(info["thumburl"], headers=HEADERS, timeout=30)
    image.raise_for_status()
    licence = (info.get("extmetadata") or {}).get("LicenseShortName", {}).get("value", "")
    return image.content, info["descriptionurl"], info["thumburl"], licence


def fetch_site(url):
    response = requests.get(url, headers=SITE_HEADERS, timeout=30)
    response.raise_for_status()
    return response.content


def _visible(logo):
    return logo.crop(logo.getchannel("A").point(lambda value: 255 if value > 16 else 0).getbbox())


def emblem(logo):
    """What stands above a stacked lockup's first clear band: its symbol, or its first line."""
    alpha = logo.getchannel("A")
    rows = [any(alpha.getpixel((x, y)) > 16 for x in range(0, logo.width, 2)) for y in range(logo.height)]
    gap = max(3, logo.height // 40)
    for y in range(1, logo.height - gap):
        if rows[y - 1] and not any(rows[y:y + gap]):
            return logo.crop((0, 0, logo.width, y))
    return logo


def _write(key, logo, keep):
    """The logo cropped, cut to its emblem when asked, scaled and saved; its size and hash."""
    logo = _visible(logo.convert("RGBA"))
    if keep == "emblem":
        logo = _visible(emblem(logo))
    if logo.height > MAX_HEIGHT:
        logo = logo.resize((round(logo.width * MAX_HEIGHT / logo.height), MAX_HEIGHT),
                           Image.Resampling.LANCZOS)
    target = OUT / f"{key}.png"
    logo.save(target, optimize=True)
    return {"sha256": hashlib.sha256(target.read_bytes()).hexdigest(), "size": list(logo.size)}


def main(keys=()):
    OUT.mkdir(parents=True, exist_ok=True)
    record_path = OUT / "rockets.source.json"
    record = {"fetched_from": "Wikimedia Commons (file rendered to PNG by Commons); an operator's own site where named",
              "logos": {}}
    if keys and record_path.is_file():
        # Only the named logos are fetched again; the others keep their record.
        record["logos"] = json.loads(record_path.read_text(encoding="utf-8")).get("logos", {})
    unknown = [key for key in keys if key not in LOGOS and key not in SITE_LOGOS]
    if unknown:
        raise SystemExit(f"Unknown logo keys: {', '.join(unknown)}")
    for key, (title, role, keep) in LOGOS.items():
        if keys and key not in keys:
            continue
        data, page, url, licence = fetch(title)
        entry = {"role": role, "keep": keep, "file": title, "page": page, "rendered": url, "licence": licence,
                 "source_sha256": hashlib.sha256(data).hexdigest()}
        record["logos"][key] = {**entry, **_write(key, Image.open(io.BytesIO(data)), keep)}
        print(key, record["logos"][key]["size"], licence)
    for key, (url, page, role, keep, licence) in SITE_LOGOS.items():
        if keys and key not in keys:
            continue
        data = fetch_site(url)
        logo = _rasterize(data) if url.lower().split("?")[0].endswith(".svg") else Image.open(io.BytesIO(data))
        entry = {"role": role, "keep": keep, "source": url, "page": page, "licence": licence,
                 "source_sha256": hashlib.sha256(data).hexdigest()}
        record["logos"][key] = {**entry, **_write(key, logo, keep)}
        print(key, record["logos"][key]["size"], licence)
    # Records of logos the page no longer ships go with their files.
    record["logos"] = {key: entry for key, entry in record["logos"].items() if (OUT / f"{key}.png").is_file()}
    record_path.write_text(json.dumps(record, indent=1, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")


if __name__ == "__main__":
    main(tuple(sys.argv[1:]))
