"""Build the web UI's brand mark and icons (direction A, "Refresh Frame", Steam orange).

    python tools/build_brand_icons.py

One geometry, on a 100-unit grid, makes every file:

- the mark: an ink frame (rounded square) round a paper screen whose top is a refresh band
  in Steam's warm glow (amber #EE9517 to orange #FF5E00, top-left to bottom-right), with two
  fading after-image lines under it - an e-paper refresh caught mid-way;
- the app icon: the same screen full-bleed on ink (iOS and Android mask the corners), opaque.

Writes src/static/icons/logo.svg (the mark, also the browser tab icon), favicon-32.png (the
mark without the after-images), apple-touch-icon.png (180), icon-192.png and icon-512.png, and
the brief reader Worker's src/brand_icons.ts (the same home-screen icon, and a tab icon whose
frame and screen swap on a dark browser).
"""

from __future__ import annotations

import base64
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw

REPO_ROOT = Path(__file__).resolve().parents[1]
ICONS = REPO_ROOT / "inkypi-weather" / "package" / "InkyPi" / "src" / "static" / "icons"
WORKER_ICONS = REPO_ROOT / "cloudflare" / "brief-reader" / "src" / "brand_icons.ts"
INK = (20, 23, 29)          # #14171D
PAPER = (247, 245, 242)     # #F7F5F2
AMBER = (238, 149, 23)      # #EE9517
ORANGE = (255, 94, 0)       # #FF5E00
SUPERSAMPLE = 8

# (frame or None, screen box, screen radius, band bottom, after-image lines as (top, bottom, opacity))
MARK = ((4, 4, 96, 96, 24), (20, 20, 80, 80), 10, 43, ((48, 53, 0.55), (57, 60, 0.28)))
ICON = (None, (22, 22, 78, 78), 9, 43, ((48, 53, 0.5), (57, 60, 0.25)))

SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">
<defs><linearGradient id="refresh" x1="0" y1="0" x2="1" y2="1">
<stop offset="0" stop-color="#EE9517"/><stop offset="1" stop-color="#FF5E00"/></linearGradient></defs>
<rect x="4" y="4" width="92" height="92" rx="24" fill="#14171D"/>
<rect x="20" y="20" width="60" height="60" rx="10" fill="#F7F5F2"/>
<path d="M20 30a10 10 0 0 1 10-10h40a10 10 0 0 1 10 10v13H20z" fill="url(#refresh)"/>
<rect x="20" y="48" width="60" height="5" fill="#FF5E00" opacity="0.55"/>
<rect x="20" y="57" width="60" height="3" fill="#FF5E00" opacity="0.28"/>
</svg>
"""


WORKER_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">'
    "<style>.f{fill:#14171D}.s{fill:#F7F5F2}"
    "@media (prefers-color-scheme:dark){.f{fill:#F7F5F2}.s{fill:#14171D}}</style>"
    '<defs><linearGradient id="refresh" x1="0" y1="0" x2="1" y2="1">'
    '<stop offset="0" stop-color="#EE9517"/><stop offset="1" stop-color="#FF5E00"/></linearGradient></defs>'
    '<rect class="f" x="4" y="4" width="92" height="92" rx="24"/>'
    '<rect class="s" x="20" y="20" width="60" height="60" rx="10"/>'
    '<path d="M20 30a10 10 0 0 1 10-10h40a10 10 0 0 1 10 10v13H20z" fill="url(#refresh)"/>'
    '<rect x="20" y="48" width="60" height="5" fill="#FF5E00" opacity="0.55"/>'
    '<rect x="20" y="57" width="60" height="3" fill="#FF5E00" opacity="0.28"/>'
    "</svg>"
)


def _gradient(size):
    """Amber at the top-left corner to orange at the bottom-right, size x size."""
    step = 256
    diagonal = Image.new("L", (step, step))
    pixels = diagonal.load()
    for y in range(step):
        for x in range(step):
            pixels[x, y] = round(255 * (x + y) / (2 * step - 2))
    diagonal = diagonal.resize(size, Image.Resampling.BILINEAR)
    return Image.composite(Image.new("RGB", size, ORANGE), Image.new("RGB", size, AMBER), diagonal)


def render(spec, size, *, opaque, after_images=True):
    """The mark or icon at ``size`` px, drawn large and scaled down for smooth edges."""
    frame, screen, radius, band_bottom, lines = spec
    big = size * SUPERSAMPLE
    unit = big / 100

    def box(values):
        return [round(value * unit) for value in values]

    image = Image.new("RGBA", (big, big), (*INK, 255) if opaque else (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    if frame is not None:
        x0, y0, x1, y1, r = frame
        draw.rounded_rectangle(box((x0, y0, x1, y1)), radius=round(r * unit), fill=INK)
    sx0, sy0, sx1, sy1 = screen
    draw.rounded_rectangle(box(screen), radius=round(radius * unit), fill=PAPER)
    # The refresh band: the screen's top, its own corners rounded, square at the bottom.
    band_mask = Image.new("L", (big, big), 0)
    mask_draw = ImageDraw.Draw(band_mask)
    mask_draw.rounded_rectangle(box(screen), radius=round(radius * unit), fill=255)
    mask_draw.rectangle(box((sx0, band_bottom, sx1, sy1)), fill=0)
    image.paste(_gradient((big, big)), (0, 0), band_mask)
    if after_images:
        for top, bottom, opacity in lines:
            layer = Image.new("RGBA", (big, big), (0, 0, 0, 0))
            ImageDraw.Draw(layer).rectangle(box((sx0, top, sx1, bottom)), fill=(*ORANGE, round(255 * opacity)))
            image = Image.alpha_composite(image, layer)
    image = image.resize((size, size), Image.Resampling.LANCZOS)
    return image.convert("RGB") if opaque else image


def write_worker_icons():
    """The brief reader serves the same icons from its bundle (strict CSP, no files)."""
    buffer = BytesIO()
    render(ICON, 180, opaque=True).save(buffer, "PNG", optimize=True)
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    rows = [encoded[index:index + 104] for index in range(0, len(encoded), 104)]
    body = " +\n  ".join(f'"{row}"' for row in rows)
    WORKER_ICONS.write_text(
        "// ColoredEpaper Frame mark (Refresh Frame, Steam orange), shared with the InkyPi web UI.\n"
        "// Generated by tools/build_brand_icons.py; do not edit by hand.\n"
        f"export const LOGO_SVG = `{WORKER_SVG}`;\n\n"
        "// 180x180 opaque PNG: iOS fills transparent home-screen icons with black.\n"
        f"const APPLE_TOUCH_ICON_BASE64 =\n  {body};\n\n"
        "export const APPLE_TOUCH_ICON_PNG = Uint8Array.from(atob(APPLE_TOUCH_ICON_BASE64), (char) => char.charCodeAt(0));\n",
        encoding="utf-8", newline="\n")


def main():
    ICONS.mkdir(parents=True, exist_ok=True)
    (ICONS / "logo.svg").write_text(SVG, encoding="utf-8", newline="\n")
    render(MARK, 32, opaque=False, after_images=False).save(ICONS / "favicon-32.png", optimize=True)
    for name, size in (("apple-touch-icon.png", 180), ("icon-192.png", 192), ("icon-512.png", 512)):
        render(ICON, size, opaque=True).save(ICONS / name, optimize=True)
        print(name, size)
    write_worker_icons()
    print(WORKER_ICONS.relative_to(REPO_ROOT))


if __name__ == "__main__":
    main()
