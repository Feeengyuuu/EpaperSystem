"""Bundle Steam Daily Art's info-shelf icons and the official Steam marks.

    python tools/build_steam_shelf_icons.py

The eight reason icons (wishlist, deal, new, top seller, spotlight, coming soon, in your library,
daily deal) were generated as one transparent row with GPT Image 2.5 (Creative Claw) and are split
on their transparent gaps. The Steam icon and wordmark are Valve's marks as kept on Wikimedia
Commons (public domain there), rasterised by Commons. Writes
plugins/steam_daily_art/assets/shelf/<name>.png with the sources in shelf.source.json.
"""

from __future__ import annotations

import io
import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen

from PIL import Image

REPO_ROOT = Path(__file__).resolve().parents[1]
SHELF = REPO_ROOT / "inkypi-weather" / "package" / "InkyPi" / "src" / "plugins" / "steam_daily_art" / "assets" / "shelf"
USER_AGENT = "InkyPi asset build (https://github.com/Feeengyuuu/EpaperSystem)"
ICON_ROW = "https://cdn.creativeclaw.co/u/e25dbd7a/images/82c1be97-a71f-4a69-824a-619b324dbdd0.png"
ICON_NAMES = ("wishlist", "deal", "new", "top_seller", "spotlight", "coming_soon", "owned", "daily_deal")
COMMONS = "https://upload.wikimedia.org/wikipedia/commons/thumb/{path}/{name}/{width}px-{name}.png"
# Commons serves its thumbnails only at its standard widths (250, 330, 500, ...).
MARKS = {
    "steam_icon": ("8/83", "Steam_icon_logo.svg", 250),
    "steam_wordmark": ("c/c1", "Steam_2016_logo_black.svg", 500),
}
# Twice the shelf's 18px icons and 20px wordmark, so the plugin scales them down.
ICON_HEIGHT = 36
MARK_HEIGHT = 40


def fetch(url, timeout=60):
    with urlopen(Request(url, headers={"User-Agent": USER_AGENT}), timeout=timeout) as response:
        return response.read()


def trimmed(image, threshold=24):
    """The image cropped to its visible pixels, the faint haze (alpha up to ``threshold``) cleared."""
    image = image.convert("RGBA")
    alpha = image.getchannel("A").point(lambda value: 0 if value <= threshold else value)
    image.putalpha(alpha)
    box = alpha.getbbox()
    return image.crop(box) if box else image


def split_row(image, min_gap=20):
    """A row of shapes split on its transparent gaps, left to right; parts closer than
    ``min_gap`` (the megaphone's sound lines) stay with their shape."""
    sheet = trimmed(image)
    alpha = sheet.getchannel("A")
    filled = [alpha.crop((x, 0, x + 1, sheet.height)).getbbox() is not None for x in range(sheet.width)]
    spans, start = [], None
    for x, on in enumerate([*filled, False]):
        if on and start is None:
            start = x
        elif not on and start is not None:
            if spans and start - spans[-1][1] < min_gap:
                spans[-1] = (spans[-1][0], x)
            else:
                spans.append((start, x))
            start = None
    return [trimmed(sheet.crop((x0, 0, x1, sheet.height))) for x0, x1 in spans]


def save(image, path, height):
    width = max(1, round(image.width * height / image.height))
    path.parent.mkdir(parents=True, exist_ok=True)
    image.resize((width, height), Image.Resampling.LANCZOS).save(path, optimize=True)


def main():
    icons = split_row(Image.open(io.BytesIO(fetch(ICON_ROW))))
    if len(icons) != len(ICON_NAMES):
        raise SystemExit(f"expected {len(ICON_NAMES)} icons, found {len(icons)}")
    for name, icon in zip(ICON_NAMES, icons):
        save(icon, SHELF / f"{name}.png", ICON_HEIGHT)
        print(name, icon.size)
    marks = {}
    for key, (path, name, width) in MARKS.items():
        url = COMMONS.format(path=path, width=width, name=name)
        save(trimmed(Image.open(io.BytesIO(fetch(url)))), SHELF / f"{key}.png", MARK_HEIGHT)
        marks[key] = {"source": url, "page": f"https://commons.wikimedia.org/wiki/File:{name}", "license": "Public domain (Wikimedia Commons)"}
        print(key)
    manifest = {
        "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "icons": {"source": ICON_ROW, "files": list(ICON_NAMES),
                  "note": "Generated with image/gpt-image-2.5-sunburst (Creative Claw), transparent, one row split on its gaps"},
        "marks": marks,
    }
    (SHELF / "shelf.source.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                                             encoding="utf-8", newline="\n")


if __name__ == "__main__":
    main()
