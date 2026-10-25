"""Cut the hand-painted name strokes for the Steam profile rail out of their generated sheet.

    python tools/build_steam_name_strokes.py <sheet.png>

The sheet is one GPT Image 2.5 (Sunburst) generation on a transparent background: four
white brush swipes stacked in rows. Each row is cropped to its visible pixels and written to
the plugin's assets/name_strokes/stroke_<n>.png at most 512 px wide; the renderer paints
one under the persona name in the hue of the game being played.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from PIL import Image

REPO_ROOT = Path(__file__).resolve().parents[1]
OUT = (REPO_ROOT / "inkypi-weather" / "package" / "InkyPi" / "src" / "plugins" / "steam_profile_dashboard"
       / "assets" / "name_strokes")
VISIBLE_ALPHA = 24
MAX_WIDTH = 512


def rows_of_paint(alpha):
    """(top, bottom) bands of rows that hold paint, separated by clear rows."""
    width, height = alpha.size
    pixels = alpha.load()
    painted = [any(pixels[x, y] >= VISIBLE_ALPHA for x in range(0, width, 2)) for y in range(height)]
    bands, start = [], None
    for y, filled in enumerate(painted + [False]):
        if filled and start is None:
            start = y
        elif not filled and start is not None:
            if y - start > height // 30:
                bands.append((start, y))
            start = None
    return bands


def main(sheet_path):
    sheet = Image.open(sheet_path).convert("RGBA")
    bands = rows_of_paint(sheet.getchannel("A"))
    if len(bands) != 4:
        raise SystemExit(f"expected four strokes, found {len(bands)} bands: {bands}")
    OUT.mkdir(parents=True, exist_ok=True)
    for index, (top, bottom) in enumerate(bands, start=1):
        band = sheet.crop((0, top, sheet.width, bottom))
        stroke = band.crop(band.getchannel("A").point(lambda value: 255 if value >= VISIBLE_ALPHA else 0).getbbox())
        if stroke.width > MAX_WIDTH:
            stroke = stroke.resize((MAX_WIDTH, round(stroke.height * MAX_WIDTH / stroke.width)),
                                   Image.Resampling.LANCZOS)
        # Only the paint's coverage matters: the renderer supplies the colour.
        white = Image.new("RGBA", stroke.size, (255, 255, 255, 0))
        white.putalpha(stroke.getchannel("A"))
        white.save(OUT / f"stroke_{index}.png", optimize=True)
        print(index, white.size)
    (OUT / "name_strokes.source.json").write_text(json.dumps({
        "generator": "Creative Claw image/gpt-image-2.5-sunburst, 1024x1536, transparent background",
        "job": "b20bc0a5-ab3a-4652-b510-e875f04ce462",
        "brief": "Four hand-painted white brush swipes, cut by tools/build_steam_name_strokes.py; "
                 "the renderer tints one in the hue of the game being played.",
    }, indent=1) + "\n", encoding="utf-8", newline="\n")


if __name__ == "__main__":
    main(sys.argv[1])
