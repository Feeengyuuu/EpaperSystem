"""Fetch the news outlets' official logos 整点新闻 shows beside its lead story.

    python tools/build_news_source_logos.py

Each outlet's logo is the public-domain Commons file named below, rendered to PNG by Commons;
China News Service's is the logo its own site serves (its trademark, shown to name the
source). Each is cropped to its visible pixels and scaled to at most 96 px tall, colours left
as published, and written to the plugin's source_logos/<key>.png; source_logos.source.json
records the file page, licence and hashes.
"""

from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path

import requests
from PIL import Image

REPO_ROOT = Path(__file__).resolve().parents[1]
OUT = REPO_ROOT / "inkypi-weather" / "package" / "InkyPi" / "src" / "plugins" / "daily_ai_news" / "source_logos"
API = "https://commons.wikimedia.org/w/api.php"
HEADERS = {"User-Agent": "EpaperSystem-DailyAINews/1.0 (https://github.com/Feeengyuuu/EpaperSystem)"}
COMMONS = {
    "abc_news": "File:ABC News logo 2021.svg",
    "al_jazeera": "File:Aljazeera eng.svg",
    "bbc_news": "File:BBC News 2022 (Alt).svg",
    "dw": "File:Deutsche Welle Logo.svg",
    "france24": "File:France 24 logo (2013).svg",
    "guardian": "File:The Guardian 2018.svg",
    "npr": "File:NPR new logo.svg",
    "nyt": "File:NewYorkTimes.svg",
    "pbs_newshour": "File:PBS News Hour wordmark (2024 horizontal).svg",
    "rthk": "File:Radio Television Hong Kong Logo.svg",
}
SITES = {"chinanews": ("http://i2.chinanews.com/2014/wap/images/logo.png", "https://www.chinanews.com.cn/m/")}
MAX_HEIGHT = 96


def _commons(title):
    response = requests.get(API, params={
        "action": "query", "titles": title, "prop": "imageinfo", "format": "json",
        "iiprop": "url|extmetadata", "iiurlwidth": 960,
    }, headers=HEADERS, timeout=30)
    response.raise_for_status()
    info = next(iter(response.json()["query"]["pages"].values()))["imageinfo"][0]
    url = info.get("thumburl") or info["url"]
    image = requests.get(url, headers=HEADERS, timeout=30)
    image.raise_for_status()
    licence = (info.get("extmetadata") or {}).get("LicenseShortName", {}).get("value", "")
    return image.content, info["descriptionurl"], url, licence


def _save(key, data, entry, record):
    logo = Image.open(io.BytesIO(data)).convert("RGBA")
    logo = logo.crop(logo.getchannel("A").point(lambda value: 255 if value > 16 else 0).getbbox())
    if logo.height > MAX_HEIGHT:
        logo = logo.resize((round(logo.width * MAX_HEIGHT / logo.height), MAX_HEIGHT), Image.Resampling.LANCZOS)
    target = OUT / f"{key}.png"
    logo.save(target, optimize=True)
    record["logos"][key] = {**entry, "source_sha256": hashlib.sha256(data).hexdigest(),
                            "sha256": hashlib.sha256(target.read_bytes()).hexdigest(), "size": list(logo.size)}
    print(key, logo.size, entry["licence"])


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    record = {"logos": {}}
    for key, title in COMMONS.items():
        data, page, url, licence = _commons(title)
        _save(key, data, {"file": title, "page": page, "rendered": url, "licence": licence}, record)
    for key, (url, page) in SITES.items():
        response = requests.get(url, headers={"User-Agent": "Mozilla/5.0 " + HEADERS["User-Agent"]}, timeout=30)
        response.raise_for_status()
        _save(key, response.content, {"file": url, "page": page,
                                      "licence": "the outlet's own logo (its trademark), shown to name the source"},
              record)
    (OUT / "source_logos.source.json").write_text(json.dumps(record, indent=1, ensure_ascii=False) + "\n",
                                                  encoding="utf-8", newline="\n")


if __name__ == "__main__":
    main()
