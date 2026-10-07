"""Generate the web UI brand icons from the logo path data.

Writes src/static/icons/logo.svg, favicon-32.png, apple-touch-icon.png,
icon-192.png and icon-512.png. The paths match the inline logo macro in
templates/components/icons.html.

Usage: python tools/generate_brand_icons.py
"""
from __future__ import annotations

from pathlib import Path
import re

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
ICONS = ROOT / "inkypi-weather" / "package" / "InkyPi" / "src" / "static" / "icons"
VIEW_W, VIEW_H = 108.8, 75.0
LAYERS = (
    ("#7EDCDF", "M77.6 0H87.6C88.9 0 90.6 0.8 91.6 1.9L106.9 17.6L88.6 26.9C85.2 26.6 82.4 23.9 82.4 20.4V5.4C82.4 2.4 80.3 0 77.6 0Z"),
    ("#0E8C82", "M106.9 17.6C108.1 19.4 108.8 21.4 108.8 23.4V35.4C108.8 33 107.1 31 104.6 30.4L88.6 26.9Z"),
    ("#18B8AC", "M14 0H77.6C80.3 0 82.4 2.4 82.4 5.4V20.4C82.4 23.9 85.2 26.6 88.6 26.9L104.6 30.4C107.1 31 108.8 33 108.8 35.4V63C108.8 69.6 103.4 75 96.8 75H13C5.8 75 0 69.2 0 62V14C0 6.3 6.3 0 14 0Z"
                "M14 10.6H77.7V20.4C77.7 26.5 82 31.6 87.6 32.6L96.1 34.2C97.1 34.4 97.8 35.3 97.8 36.4V62.6C97.8 64.2 96.6 65.4 95 65.4H13.8C12.2 65.4 10.9 64.1 10.9 62.5V13.6C10.9 11.9 12.2 10.6 14 10.6Z"),
)
SUPERSAMPLE = 8


def subpaths(path: str):
    """Flatten an absolute M/H/V/L/C/Z path into polygons."""
    tokens = re.findall(r"[MHVLCZ]|-?\d*\.?\d+", path)
    polygons, current, x, y, index, command = [], [], 0.0, 0.0, 0, None
    while index < len(tokens):
        token = tokens[index]
        if token.isalpha():
            command = token
            index += 1
            if command == "Z":
                polygons.append(current)
                current = []
            continue
        if command == "M":
            x, y = float(tokens[index]), float(tokens[index + 1])
            current = [(x, y)]
            index += 2
        elif command == "H":
            x = float(token)
            current.append((x, y))
            index += 1
        elif command == "V":
            y = float(token)
            current.append((x, y))
            index += 1
        elif command == "L":
            x, y = float(tokens[index]), float(tokens[index + 1])
            current.append((x, y))
            index += 2
        elif command == "C":
            x1, y1, x2, y2, x3, y3 = (float(value) for value in tokens[index:index + 6])
            for step in range(1, 17):
                t = step / 16
                u = 1 - t
                current.append((
                    u ** 3 * x + 3 * u * u * t * x1 + 3 * u * t * t * x2 + t ** 3 * x3,
                    u ** 3 * y + 3 * u * u * t * y1 + 3 * u * t * t * y2 + t ** 3 * y3,
                ))
            x, y = x3, y3
            index += 6
        else:
            raise ValueError(f"unsupported path command {command}")
    return polygons


def render(size: int, logo_width: float, background: str | None) -> Image.Image:
    big = size * SUPERSAMPLE
    scale = logo_width * big / VIEW_W
    offset_x = (big - VIEW_W * scale) / 2
    offset_y = (big - VIEW_H * scale) / 2
    canvas = Image.new("RGBA", (big, big), background or (0, 0, 0, 0))
    for color, path in LAYERS:
        mask = Image.new("L", (big, big), 0)
        draw = ImageDraw.Draw(mask)
        for number, polygon in enumerate(subpaths(path)):
            points = [(offset_x + px * scale, offset_y + py * scale) for px, py in polygon]
            # Even-odd: the second subpath of the frame is the hole.
            draw.polygon(points, fill=255 if number == 0 else 0)
        canvas.paste(Image.new("RGBA", (big, big), color), (0, 0), mask)
    return canvas.resize((size, size), Image.LANCZOS)


def svg() -> str:
    evenodd = ' fill-rule="evenodd"'
    paths = "".join(
        f'<path fill="{color}"{evenodd if color == "#18B8AC" else ""} d="{path}"/>'
        for color, path in LAYERS
    )
    return f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="-2 -19 112.8 112.8">{paths}</svg>\n'


def main():
    ICONS.mkdir(parents=True, exist_ok=True)
    (ICONS / "logo.svg").write_text(svg(), encoding="utf-8", newline="\n")
    render(32, 1.0, None).save(ICONS / "favicon-32.png", optimize=True)
    for name, size in (("apple-touch-icon.png", 180), ("icon-192.png", 192), ("icon-512.png", 512)):
        render(size, 0.66, "#FFFFFF").convert("RGB").save(ICONS / name, optimize=True)
    print("wrote", sorted(path.name for path in ICONS.glob("*") if path.suffix in {".png", ".svg"}))


if __name__ == "__main__":
    main()
