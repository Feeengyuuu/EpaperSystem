"""Fetch the official rocket and mission logos Orbital Signal shows beside a launch.

    python tools/build_orbital_rocket_logos.py

Each logo is the Wikimedia Commons file named below, rendered by Commons to a PNG
(transparency kept), cropped to its visible pixels, scaled to at most 96 px tall and
written to the plugin's assets/rockets/<key>.png. rockets.source.json records the file
page, licence and hashes. Colours are untouched: the page shows them as they are. A lockup
marked "emblem" keeps only its symbol, above the lettering, for use beside the printed name.
"""

from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path

import requests
from PIL import Image

REPO_ROOT = Path(__file__).resolve().parents[1]
OUT = (REPO_ROOT / "inkypi-weather" / "package" / "InkyPi" / "src" / "plugins" / "orbital_signal"
       / "assets" / "rockets")
API = "https://commons.wikimedia.org/w/api.php"
HEADERS = {"User-Agent": "EpaperSystem-OrbitalSignal/1.0 (https://github.com/Feeengyuuu/EpaperSystem)"}
# key: (Commons file, what it marks, how much of it to keep)
LOGOS = {
    "falcon9": ("File:Falcon 9 logo.svg", "rocket", "whole"),
    "falcon_heavy": ("File:Falcon Heavy logo.svg", "rocket", "whole"),
    "electron": ("File:Electron rocket logo.svg", "rocket", "whole"),
    "vulcan": ("File:Vulcan logo.svg", "rocket", "whole"),
    "starlink": ("File:Starlink Logo 2024.svg", "mission", "emblem"),
    "kuiper": ("File:Kuiper Systems logo.svg", "mission", "whole"),
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


def emblem(logo):
    """The symbol above a stacked lockup's lettering: everything above the first clear band."""
    alpha = logo.getchannel("A")
    rows = [any(alpha.getpixel((x, y)) > 16 for x in range(0, logo.width, 2)) for y in range(logo.height)]
    gap = max(3, logo.height // 40)
    for y in range(1, logo.height - gap):
        if rows[y - 1] and not any(rows[y:y + gap]):
            return logo.crop((0, 0, logo.width, y))
    return logo


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    record = {"fetched_from": "Wikimedia Commons (file rendered to PNG by Commons)", "logos": {}}
    for key, (title, role, keep) in LOGOS.items():
        data, page, url, licence = fetch(title)
        logo = Image.open(io.BytesIO(data)).convert("RGBA")
        logo = logo.crop(logo.getchannel("A").point(lambda value: 255 if value > 16 else 0).getbbox())
        if keep == "emblem":
            logo = emblem(logo)
            logo = logo.crop(logo.getchannel("A").point(lambda value: 255 if value > 16 else 0).getbbox())
        if logo.height > MAX_HEIGHT:
            logo = logo.resize((round(logo.width * MAX_HEIGHT / logo.height), MAX_HEIGHT),
                               Image.Resampling.LANCZOS)
        target = OUT / f"{key}.png"
        logo.save(target, optimize=True)
        record["logos"][key] = {
            "role": role, "keep": keep, "file": title, "page": page, "rendered": url, "licence": licence,
            "source_sha256": hashlib.sha256(data).hexdigest(),
            "sha256": hashlib.sha256(target.read_bytes()).hexdigest(), "size": list(logo.size),
        }
        print(key, logo.size, licence)
    (OUT / "rockets.source.json").write_text(json.dumps(record, indent=1, ensure_ascii=False) + "\n",
                                             encoding="utf-8", newline="\n")


if __name__ == "__main__":
    main()
