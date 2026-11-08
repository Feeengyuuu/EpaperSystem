"""Fetch the official marks 游戏优惠 (Game Deals) prints for its store, its price source and its review sources.

    python tools/build_game_deals_logos.py [key ...]

Steam's is the 2016 logo (round mark and STEAM lettering, black) from Wikimedia Commons, public
domain; steam_mark is its round mark alone, cut from the same file at the first clear column after
it, for the review rows. CheapShark's are the shark mascot and the "CheapShark" lettering its own
site header serves (its trademark, shown to name the source). The lettering is drawn white with a
grey shadow for a dark header, so only its white strokes are kept, closed into solid letters, as a
black mask the page tints with its ink. Metacritic's is its roundel from Wikimedia Commons (public
domain), for the critics' scores. Each is cropped
to its visible pixels and scaled to at most 64 px tall into the plugin's assets/; logos.source.json
records where each came from. With keys, only those are rebuilt and the record keeps the others.
"""

from __future__ import annotations

import hashlib
import io
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests
from PIL import Image, ImageFilter

REPO_ROOT = Path(__file__).resolve().parents[1]
OUT = REPO_ROOT / "inkypi-weather" / "package" / "InkyPi" / "src" / "plugins" / "game_deals" / "assets"
HEADERS = {"User-Agent": "Mozilla/5.0 InkyPi asset build (https://github.com/Feeengyuuu/EpaperSystem)"}
STEAM = ("https://upload.wikimedia.org/wikipedia/commons/thumb/c/c1/Steam_2016_logo_black.svg/960px-Steam_2016_logo_black.svg.png",
         "https://commons.wikimedia.org/wiki/File:Steam_2016_logo_black.svg", "Public domain (Wikimedia Commons)")
CHEAPSHARK_MARK = ("https://www.cheapshark.com/img/logo_image.png", "https://www.cheapshark.com/",
                   "CheapShark's trademark, shown to name the price source")
CHEAPSHARK_LETTERS = ("https://www.cheapshark.com/img/footer_text.png", "https://www.cheapshark.com/",
                      "CheapShark's trademark, shown to name the price source")
METACRITIC_ROUNDEL = ("https://upload.wikimedia.org/wikipedia/commons/thumb/e/e1/Metacritic_logo_Roundel.svg/"
                      "500px-Metacritic_logo_Roundel.svg.png",
                      "https://commons.wikimedia.org/wiki/File:Metacritic_logo_Roundel.svg", "Public domain (Wikimedia Commons)")
MAX_HEIGHT = 64


def _fetch(url):
    response = requests.get(url, headers=HEADERS, timeout=30)
    response.raise_for_status()
    return response.content, Image.open(io.BytesIO(response.content)).convert("RGBA")


def _fit(image):
    image = image.crop(image.getchannel("A").point(lambda value: 255 if value > 16 else 0).getbbox())
    if image.height > MAX_HEIGHT:
        image = image.resize((round(image.width * MAX_HEIGHT / image.height), MAX_HEIGHT), Image.Resampling.LANCZOS)
    return image


def _letters(image):
    """The white strokes of the lettering, closed into solid letters, black on transparent."""
    red, green, blue, alpha = image.split()
    light = Image.merge("RGB", (red, green, blue)).convert("L").point(lambda value: 255 if value > 150 else 0)
    strokes = Image.composite(light, Image.new("L", image.size, 0), alpha.point(lambda value: 255 if value > 128 else 0))
    strokes = strokes.filter(ImageFilter.MaxFilter(3)).filter(ImageFilter.MinFilter(3))
    mask = Image.new("RGBA", image.size, (0, 0, 0, 255))
    mask.putalpha(strokes)
    return mask


def _round_mark(image):
    """Steam's round mark alone: the logo up to its first fully clear column after the mark."""
    alpha = image.getchannel("A").point(lambda value: 255 if value > 16 else 0)
    left, top, right, bottom = alpha.getbbox()
    columns = [any(alpha.getpixel((x, y)) for y in range(top, bottom)) for x in range(left, right)]
    start = next(index for index, filled in enumerate(columns) if filled)
    end = next(index for index, filled in enumerate(columns[start:], start) if not filled)
    return image.crop((left + start, top, left + end, bottom))


def main(keys=()):
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / "logos.source.json"
    previous = json.loads(path.read_text(encoding="utf-8")).get("logos", {}) if keys and path.is_file() else {}
    record = {"built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "logos": previous}
    for key, (url, page, licence), shape in (("steam_logo", STEAM, None), ("steam_mark", STEAM, _round_mark),
                                             ("cheapshark_mark", CHEAPSHARK_MARK, None),
                                             ("cheapshark_letters", CHEAPSHARK_LETTERS, _letters),
                                             ("metacritic_mark", METACRITIC_ROUNDEL, None)):
        if keys and key not in keys:
            continue
        raw, image = _fetch(url)
        logo = _fit(shape(image) if shape else image)
        target = OUT / f"{key}.png"
        logo.save(target, optimize=True)
        record["logos"][key] = {"source": url, "page": page, "licence": licence,
                                "source_sha256": hashlib.sha256(raw).hexdigest(), "size": list(logo.size),
                                "sha256": hashlib.sha256(target.read_bytes()).hexdigest()}
        print(key, logo.size)
    record["logos"]["cheapshark_letters"]["processing"] = (
        "white strokes kept (luminance > 150), the shadow dropped, closed 3 px, black on transparent: tinted with the page's ink")
    (OUT / "logos.source.json").write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8", newline="\n")


if __name__ == "__main__":
    main(set(sys.argv[1:]))
