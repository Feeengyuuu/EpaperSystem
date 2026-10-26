"""Bundle the generated header art: flying comics, flying dollar bills, the Fremont wordmark.

    python tools/build_flying_header_art.py

All three were generated with GPT Image 2.5 (Creative Claw, transparent) and are fetched from
their permanent URLs. A generated row of flying things is split into its pieces (each comic
or bill with the wind streaks at its edge) and laid out again along a gentle wave at the
shape its header slot has (or kept as drawn when its pieces touch). The wordmark is trimmed, and gets a night version whose dark
lettering takes the night page's ink while its orange accents stay.

Writes plugins/gcd_comic_covers/assets/header_flying_comics.png,
plugins/stocktracker/assets/header_flying_bills.png and
plugins/weather/art/place/fremont-california(-night).png, each with a .source.json beside it.
"""

from __future__ import annotations

import io
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen

from PIL import Image

REPO_ROOT = Path(__file__).resolve().parents[1]
PLUGINS = REPO_ROOT / "inkypi-weather" / "package" / "InkyPi" / "src" / "plugins"
USER_AGENT = "InkyPi asset build (https://github.com/Feeengyuuu/EpaperSystem)"
CDN = "https://cdn.creativeclaw.co/u/e25dbd7a/images/{}.png"
MODEL = "image/gpt-image-2.5-sunburst (Creative Claw, 2112x704, transparent)"
# name -> (generated image, output, width : height of its header slot, output height, right edge
# kept in the generated row); a ratio of None keeps the row as drawn (its pieces touch through
# coins and streaks), up to that edge: the first four bills fill the Robinhood header's slot.
ROWS = {
    "comics": ("dea36c63-2194-49a0-8094-6b1424596f71", PLUGINS / "gcd_comic_covers" / "assets" / "header_flying_comics.png",
               9.0, 120, None),
    "bills": ("dbaac4be-3be4-4f1e-865c-8c989eec8a75", PLUGINS / "stocktracker" / "assets" / "header_flying_bills.png",
              None, 96, 1490),
}
WORDMARK = ("30685ed5-5a62-432d-a58c-4b818e7c1d67", PLUGINS / "weather" / "art" / "place" / "fremont-california.png", 96)
# The night page's ink for the wordmark's dark lettering (weather_modern.css --ink by night).
NIGHT_INK = (238, 240, 244)
ALPHA_FLOOR = 24
# A piece is at least this share of the row's opaque pixels; smaller ones are its wind streaks.
PIECE_SHARE = 0.03
# The wave the pieces ride along, as a share of the row's height.
WAVE = 0.08


def fetch(url, timeout=60):
    with urlopen(Request(url, headers={"User-Agent": USER_AGENT}), timeout=timeout) as response:
        return Image.open(io.BytesIO(response.read())).convert("RGBA")


def cleared(image):
    """The image with its faint haze (alpha up to ALPHA_FLOOR) cleared."""
    alpha = image.getchannel("A").point(lambda value: 0 if value <= ALPHA_FLOOR else value)
    image = image.copy()
    image.putalpha(alpha)
    return image


def components(mask):
    """Connected opaque regions of a 1-bit-ish mask: [(pixel count, set of (x, y))]."""
    width, height = mask.size
    pixels = mask.load()
    seen = set()
    found = []
    for x in range(width):
        for y in range(height):
            if not pixels[x, y] or (x, y) in seen:
                continue
            stack, region = [(x, y)], set()
            seen.add((x, y))
            while stack:
                cx, cy = stack.pop()
                region.add((cx, cy))
                for nx, ny in ((cx + 1, cy), (cx - 1, cy), (cx, cy + 1), (cx, cy - 1)):
                    if 0 <= nx < width and 0 <= ny < height and pixels[nx, ny] and (nx, ny) not in seen:
                        seen.add((nx, ny))
                        stack.append((nx, ny))
            found.append((len(region), region))
    return found


def pieces(row, scale=4):
    """The row's pieces left to right, each with the small parts (streaks, pages) nearest it."""
    small = row.getchannel("A").resize((row.width // scale, row.height // scale), Image.Resampling.BOX)
    mask = small.point(lambda value: 255 if value > ALPHA_FLOOR else 0)
    regions = components(mask)
    total = sum(count for count, _region in regions)
    big = [region for count, region in regions if count >= total * PIECE_SHARE]
    bits = [region for count, region in regions if count < total * PIECE_SHARE]
    boxes = [(min(x for x, _y in region), max(x for x, _y in region)) for region in big]
    groups = [set(region) for region in big]
    for bit in bits:
        right = max(x for x, _y in bit)
        middle = sum(x for x, _y in bit) / len(bit)
        # A wind streak sits just left of the piece it trails; anything else joins the nearest.
        ahead = [index for index, (left, _r) in enumerate(boxes) if left >= right - 4]
        index = min(ahead, key=lambda i: boxes[i][0] - right) if ahead else \
            min(range(len(boxes)), key=lambda i: abs((boxes[i][0] + boxes[i][1]) / 2 - middle))
        groups[index] |= bit
    result = []
    for group in sorted(groups, key=lambda region: min(x for x, _y in region)):
        keep = Image.new("L", small.size, 0)
        keep_pixels = keep.load()
        for x, y in group:
            keep_pixels[x, y] = 255
        keep = keep.resize(row.size, Image.Resampling.NEAREST)
        piece = row.copy()
        piece.putalpha(Image.composite(row.getchannel("A"), Image.new("L", row.size, 0), keep))
        result.append(piece.crop(piece.getchannel("A").getbbox()))
    return result


def wave_row(parts, ratio, height):
    """The pieces spread evenly across a ``ratio`` : 1 strip ``height`` px tall, riding a wave."""
    tallest = max(part.height for part in parts)
    scale = height * (1 - 2 * WAVE) / tallest
    parts = [part.resize((max(1, round(part.width * scale)), max(1, round(part.height * scale))), Image.Resampling.LANCZOS)
             for part in parts]
    width = round(height * ratio)
    gap = (width - sum(part.width for part in parts)) / max(1, len(parts) - 1)
    strip = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    x = 0.0
    for index, part in enumerate(parts):
        lift = math.sin(index * 1.3) * WAVE * height
        strip.alpha_composite(part, (round(x), round((height - part.height) / 2 + lift)))
        x += part.width + gap
    return strip.crop(strip.getchannel("A").getbbox())


def night_wordmark(mark):
    """The wordmark with its dark, colourless lettering in NIGHT_INK; its orange stays."""
    night = mark.copy()
    pixels = night.load()
    for y in range(night.height):
        for x in range(night.width):
            red, green, blue, alpha = pixels[x, y]
            if alpha and max(red, green, blue) - min(red, green, blue) < 48 and max(red, green, blue) < 140:
                pixels[x, y] = (*NIGHT_INK, alpha)
    return night


def record(path, source, note):
    path.with_suffix(".source.json").write_text(json.dumps({
        "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": source, "model": MODEL, "note": note,
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")


def main():
    for name, (image_id, out, ratio, height, right_edge) in ROWS.items():
        row = cleared(fetch(CDN.format(image_id)))
        if right_edge:
            row = row.crop((0, 0, right_edge, row.height))
        if ratio is None:
            parts = [row.crop(row.getchannel("A").getbbox())]
            strip = parts[0].resize((round(parts[0].width * height / parts[0].height), height), Image.Resampling.LANCZOS)
        else:
            parts = pieces(row)
            strip = wave_row(parts, ratio, height)
        out.parent.mkdir(parents=True, exist_ok=True)
        strip.save(out, optimize=True)
        record(out, CDN.format(image_id), f"{len(parts)} pieces of a generated row laid along a wave at {ratio}:1"
               if ratio else "The generated row as drawn, trimmed")
        print(name, len(parts), strip.size)
    image_id, out, height = WORDMARK
    mark = cleared(fetch(CDN.format(image_id)))
    mark = mark.crop(mark.getchannel("A").getbbox())
    mark = mark.resize((round(mark.width * height / mark.height), height), Image.Resampling.LANCZOS)
    out.parent.mkdir(parents=True, exist_ok=True)
    mark.save(out, optimize=True)
    night_out = out.with_name(out.stem + "-night.png")
    night_wordmark(mark).save(night_out, optimize=True)
    record(out, CDN.format(image_id), "Minimalist place wordmark; the -night file has its dark lettering in the night ink")
    print("wordmark", mark.size)


if __name__ == "__main__":
    main()
