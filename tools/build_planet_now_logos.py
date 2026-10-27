"""Fetch the official logos 环球此刻 (Planet Now) prints where each card names its source.

    python tools/build_planet_now_logos.py

Commons logos are rendered to PNG by Commons; California ISO's are the SVGs its own site
header uses (the light-page and dark-page versions), rasterized by a headless Chrome or Edge
with a transparent background (set CHROME_PATH to pick one). Each is cropped to its visible
pixels, scaled to at most 64 px tall and written to the plugin's assets/logos/<key>.png;
logos.source.json records where each came from, its licence and hashes. Colours are left as
published; a ``_night`` file is the brand's own version for dark pages.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

import requests
from PIL import Image

REPO_ROOT = Path(__file__).resolve().parents[1]
OUT = (REPO_ROOT / "inkypi-weather" / "package" / "InkyPi" / "src" / "plugins" / "planet_now" / "assets"
       / "logos")
SCRATCH = REPO_ROOT / ".tmp" / "planet-now-logos"
API = "https://commons.wikimedia.org/w/api.php"
HEADERS = {"User-Agent": "EpaperSystem-PlanetNow/1.0 (https://github.com/Feeengyuuu/EpaperSystem)"}
# key: Commons file
COMMONS = {
    "noaa": "File:NOAA logo.svg",
    "nasa": "File:NASA logo.svg",
    "apple_music": "File:Apple Music logo.svg",
    "google_trends": "File:Google Trends.png",
}
# key: (SVG on the brand's own site, the page it is used on)
SITE_SVGS = {
    "caiso": ("https://www.caiso.com/theme/dist/assets/www-logo-caiso.svg", "https://www.caiso.com/"),
    "caiso_night": ("https://www.caiso.com/theme/dist/assets/www-logo-caiso-dark.svg", "https://www.caiso.com/"),
}
# Apple publishes its Music wordmark in black for light grounds and in white for dark ones.
WHITE_NIGHT = {"apple_music": "apple_music_night"}
MAX_HEIGHT = 64


def _commons(title):
    response = requests.get(API, params={
        "action": "query", "titles": title, "prop": "imageinfo", "format": "json",
        "iiprop": "url|extmetadata", "iiurlwidth": 640,
    }, headers=HEADERS, timeout=30)
    response.raise_for_status()
    info = next(iter(response.json()["query"]["pages"].values()))["imageinfo"][0]
    url = info.get("thumburl") or info["url"]
    image = requests.get(url, headers=HEADERS, timeout=30)
    image.raise_for_status()
    licence = (info.get("extmetadata") or {}).get("LicenseShortName", {}).get("value", "")
    return image.content, info["descriptionurl"], url, licence


def _browser():
    candidates = [os.environ.get("CHROME_PATH") or "",
                  "C:/Program Files/Google/Chrome/Application/chrome.exe",
                  "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe",
                  "C:/Program Files/Microsoft/Edge/Application/msedge.exe"]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return candidate
    found = shutil.which("chrome") or shutil.which("msedge")
    if not found:
        raise RuntimeError("No Chrome or Edge found; set CHROME_PATH to rasterize the SVG logos")
    return found


def _rasterize(svg):
    """The SVG drawn 8x on a transparent page by a headless browser."""
    SCRATCH.mkdir(parents=True, exist_ok=True)
    # The browser can still hold its profile when the folder is removed.
    with tempfile.TemporaryDirectory(dir=SCRATCH, ignore_cleanup_errors=True) as folder:
        folder = Path(folder)
        (folder / "logo.svg").write_bytes(svg)
        (folder / "page.html").write_text(
            "<!doctype html><style>html,body{margin:0;background:transparent;overflow:hidden}"
            "img{display:block;width:1824px;height:536px;object-fit:contain}</style>"
            "<img src='logo.svg'>", encoding="utf-8")
        shot = folder / "shot.png"
        subprocess.run([_browser(), "--headless=new", "--disable-gpu", "--no-first-run", "--hide-scrollbars",
                        "--allow-file-access-from-files", "--default-background-color=00000000",
                        "--window-size=1824,536", f"--user-data-dir={folder / 'profile'}",
                        f"--screenshot={shot}", (folder / "page.html").as_uri()],
                       check=True, capture_output=True, timeout=60)
        # Edge's launcher returns before its browser process has written the screenshot.
        deadline = time.monotonic() + 30
        size = -1
        while time.monotonic() < deadline and not (shot.is_file() and shot.stat().st_size == size):
            size = shot.stat().st_size if shot.is_file() else -1
            time.sleep(0.5)
        with Image.open(shot) as image:
            return image.convert("RGBA")


def _trim(logo):
    logo = logo.crop(logo.getchannel("A").point(lambda value: 255 if value > 16 else 0).getbbox())
    if logo.height > MAX_HEIGHT:
        logo = logo.resize((round(logo.width * MAX_HEIGHT / logo.height), MAX_HEIGHT), Image.Resampling.LANCZOS)
    return logo


def _save(key, logo, entry, record):
    target = OUT / f"{key}.png"
    logo.save(target, optimize=True)
    record["logos"][key] = {**entry, "sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
                            "size": list(logo.size)}
    print(key, logo.size, entry.get("licence", ""))


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    record = {"logos": {}}
    for key, title in COMMONS.items():
        data, page, url, licence = _commons(title)
        logo = _trim(Image.open(io.BytesIO(data)).convert("RGBA"))
        entry = {"file": title, "page": page, "rendered": url, "licence": licence,
                 "source_sha256": hashlib.sha256(data).hexdigest(), "modified": "cropped and scaled only"}
        _save(key, logo, entry, record)
        if key in WHITE_NIGHT:
            white = Image.new("RGBA", logo.size, (255, 255, 255, 255))
            white.putalpha(logo.getchannel("A"))
            _save(WHITE_NIGHT[key], white, {**entry, "modified": "white on dark pages, as the brand publishes it"},
                  record)
    for key, (url, page) in SITE_SVGS.items():
        response = requests.get(url, headers={**HEADERS, "User-Agent": "Mozilla/5.0 " + HEADERS["User-Agent"]},
                                timeout=30)
        response.raise_for_status()
        logo = _trim(_rasterize(response.content))
        _save(key, logo, {"file": url, "page": page, "rendered": "headless browser, transparent background",
                          "licence": "the brand's own logo (its trademark), shown to name the source",
                          "source_sha256": hashlib.sha256(response.content).hexdigest(),
                          "modified": "cropped and scaled only"}, record)
    (OUT / "logos.source.json").write_text(json.dumps(record, indent=1, ensure_ascii=False) + "\n",
                                           encoding="utf-8", newline="\n")


if __name__ == "__main__":
    main()
