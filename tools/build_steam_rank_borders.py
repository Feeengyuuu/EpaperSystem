"""Cut the Steam rank ladder's promotion borders out of their generated sheet.

    python tools/build_steam_rank_borders.py <sheet.png>

The sheet is one GPT Image 2.5 (Sunburst) generation on a transparent background: ten
tier borders in two rows of five, iron to challenger, each round an empty centre. Every
border is cropped to its visible pixels, scaled to at most 256 px and written to the
plugin's assets/rank/<tier>.png; borders.json records where each empty centre lies so the
renderer can print the level number inside it.
"""

from __future__ import annotations

import json
import sys
from collections import deque
from pathlib import Path

from PIL import Image

REPO_ROOT = Path(__file__).resolve().parents[1]
PLUGIN = REPO_ROOT / "inkypi-weather" / "package" / "InkyPi" / "src" / "plugins" / "steam_profile_dashboard"
OUT = PLUGIN / "assets" / "rank"
TIERS = ("iron", "bronze", "silver", "gold", "platinum",
         "emerald", "diamond", "master", "grandmaster", "challenger")
COLUMNS, ROWS = 5, 2
VISIBLE_ALPHA = 24
MAX_SIDE = 256


def enclosed_hole(alpha):
    """The largest transparent region that does not reach the image edge: (cx, cy, radius)."""
    width, height = alpha.size
    pixels = alpha.load()
    seen = bytearray(width * height)
    best = None
    x = width // 2
    for y0 in range(height):
        if pixels[x, y0] >= VISIBLE_ALPHA or seen[y0 * width + x]:
            continue
        queue, region, edge = deque([(x, y0)]), [], False
        seen[y0 * width + x] = 1
        while queue:
            px, py = queue.popleft()
            region.append((px, py))
            if px in (0, width - 1) or py in (0, height - 1):
                edge = True
            for nx, ny in ((px + 1, py), (px - 1, py), (px, py + 1), (px, py - 1)):
                if 0 <= nx < width and 0 <= ny < height and not seen[ny * width + nx] \
                        and pixels[nx, ny] < VISIBLE_ALPHA:
                    seen[ny * width + nx] = 1
                    queue.append((nx, ny))
        if not edge and (best is None or len(region) > len(best)):
            best = region
    if not best:
        return None
    xs, ys = [p[0] for p in best], [p[1] for p in best]
    left, right, top, bottom = min(xs), max(xs), min(ys), max(ys)
    return ((left + right + 1) / 2, (top + bottom + 1) / 2, min(right - left + 1, bottom - top + 1) / 2)


def main(sheet_path):
    sheet = Image.open(sheet_path).convert("RGBA")
    cell_w, cell_h = sheet.width / COLUMNS, sheet.height / ROWS
    OUT.mkdir(parents=True, exist_ok=True)
    manifest = {}
    for index, key in enumerate(TIERS):
        column, row = index % COLUMNS, index // COLUMNS
        cell = sheet.crop((round(column * cell_w), round(row * cell_h),
                           round((column + 1) * cell_w), round((row + 1) * cell_h)))
        visible = cell.getchannel("A").point(lambda value: 255 if value >= VISIBLE_ALPHA else 0)
        border = cell.crop(visible.getbbox())
        border.thumbnail((MAX_SIDE, MAX_SIDE), Image.Resampling.LANCZOS)
        hole = enclosed_hole(border.getchannel("A"))
        if hole is None:
            raise SystemExit(f"{key}: no empty centre found")
        border.save(OUT / f"{key}.png", optimize=True)
        manifest[key] = {"size": list(border.size), "hole": [round(value, 1) for value in hole]}
        print(key, border.size, manifest[key]["hole"])
    (OUT / "borders.json").write_text(json.dumps(manifest, indent=1) + "\n", encoding="utf-8", newline="\n")
    (OUT / "borders.source.json").write_text(json.dumps({
        "generator": "Creative Claw image/gpt-image-2.5-sunburst, 1536x1024, transparent background",
        "job": "487eaf42-2bb2-4dbf-8ec2-3378c9656d4c",
        "brief": "Ten ranked-tier promotion borders, iron to challenger, each round an empty centre for "
                 "the level number, progressively more ornate; cut by tools/build_steam_rank_borders.py.",
    }, ensure_ascii=False, indent=1) + "\n", encoding="utf-8", newline="\n")


if __name__ == "__main__":
    main(sys.argv[1])
