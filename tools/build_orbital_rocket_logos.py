"""Fetch the official rocket and mission logos Orbital Signal shows beside a launch.

    python tools/build_orbital_rocket_logos.py

Each logo is the Wikimedia Commons file named below, rendered by Commons to a PNG
(transparency kept), cropped to its visible pixels, scaled to at most 96 px tall and
written to the plugin's assets/rockets/<key>.png. rockets.source.json records the file
page, licence and hashes. Colours are untouched: the page shows them as they are. Only marks
that are not just the name in letters are kept, since the name is printed beside them.
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
# key: (Commons file, what it marks)
LOGOS = {
    "falcon9": ("File:Falcon 9 logo.svg", "rocket"),
    "falcon_heavy": ("File:Falcon Heavy logo.svg", "rocket"),
    "vulcan": ("File:Vulcan logo.svg", "rocket"),
    "starlink": ("File:Starlink Logo.svg", "mission"),
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


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    record = {"fetched_from": "Wikimedia Commons (file rendered to PNG by Commons)", "logos": {}}
    for key, (title, role) in LOGOS.items():
        data, page, url, licence = fetch(title)
        logo = Image.open(io.BytesIO(data)).convert("RGBA")
        logo = logo.crop(logo.getchannel("A").point(lambda value: 255 if value > 16 else 0).getbbox())
        if logo.height > MAX_HEIGHT:
            logo = logo.resize((round(logo.width * MAX_HEIGHT / logo.height), MAX_HEIGHT),
                               Image.Resampling.LANCZOS)
        target = OUT / f"{key}.png"
        logo.save(target, optimize=True)
        record["logos"][key] = {
            "role": role, "file": title, "page": page, "rendered": url, "licence": licence,
            "source_sha256": hashlib.sha256(data).hexdigest(),
            "sha256": hashlib.sha256(target.read_bytes()).hexdigest(), "size": list(logo.size),
        }
        print(key, logo.size, licence)
    (OUT / "rockets.source.json").write_text(json.dumps(record, indent=1, ensure_ascii=False) + "\n",
                                             encoding="utf-8", newline="\n")


if __name__ == "__main__":
    main()
