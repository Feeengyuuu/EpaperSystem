from __future__ import annotations

import argparse
import json
from pathlib import Path

from PIL import Image


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = REPO_ROOT / "inkypi-weather" / "package" / "InkyPi" / "src" / "plugins" / "flight_radar" / "sfo_bay_map_crop.png"

SFO_LAT = 37.62131
SFO_LON = -122.37896

# Pixel estimate from the user-provided 1303x1065 Bay Area screenshot.
# It points to the SFO runway complex east of Millbrae and south of San Bruno.
DEFAULT_SFO_PIXEL_FRACTION_X = 552 / 1303
DEFAULT_SFO_PIXEL_FRACTION_Y = 590 / 1065

# Current horizontal radar inner panel is 492x334. Keeping this aspect means
# the crop can be resized without stretching when rendered on the 800x480 frame.
DEFAULT_TARGET_ASPECT = 492 / 334


def _largest_centered_crop(width: int, height: int, center_x: float, center_y: float, aspect: float) -> tuple[int, int, int, int]:
    half_w = max(1.0, min(center_x, width - center_x))
    half_h = max(1.0, min(center_y, height - center_y))

    crop_w = half_w * 2
    crop_h = crop_w / aspect
    if crop_h / 2 > half_h:
        crop_h = half_h * 2
        crop_w = crop_h * aspect

    left = int(round(center_x - crop_w / 2))
    top = int(round(center_y - crop_h / 2))
    right = int(round(center_x + crop_w / 2))
    bottom = int(round(center_y + crop_h / 2))

    if left < 0:
        right -= left
        left = 0
    if top < 0:
        bottom -= top
        top = 0
    if right > width:
        left -= right - width
        right = width
    if bottom > height:
        top -= bottom - height
        bottom = height

    left = max(0, left)
    top = max(0, top)
    right = min(width, right)
    bottom = min(height, bottom)
    return left, top, right, bottom


def build_map_crop(source: Path, output: Path, sfo_pixel: tuple[float, float] | None, aspect: float) -> dict:
    image = Image.open(source).convert("RGB")
    width, height = image.size
    if sfo_pixel is None:
        center_x = width * DEFAULT_SFO_PIXEL_FRACTION_X
        center_y = height * DEFAULT_SFO_PIXEL_FRACTION_Y
    else:
        center_x, center_y = sfo_pixel

    crop_box = _largest_centered_crop(width, height, center_x, center_y, aspect)
    crop = image.crop(crop_box)
    output.parent.mkdir(parents=True, exist_ok=True)
    crop.save(output)

    return {
        "source": str(source),
        "output": str(output),
        "source_size": [width, height],
        "crop_box": list(crop_box),
        "crop_size": list(crop.size),
        "sfo_coordinate": [SFO_LAT, SFO_LON],
        "sfo_pixel": [round(center_x, 2), round(center_y, 2)],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Crop a Bay Area map screenshot for the FlightRadar SFO background.")
    parser.add_argument("source", type=Path, help="Path to the original Bay Area map screenshot.")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="Output PNG path.")
    parser.add_argument("--sfo-pixel", nargs=2, type=float, metavar=("X", "Y"), help="Override the SFO pixel in the source image.")
    parser.add_argument("--aspect", type=float, default=DEFAULT_TARGET_ASPECT, help="Target crop aspect ratio.")
    args = parser.parse_args()

    result = build_map_crop(args.source, args.output, tuple(args.sfo_pixel) if args.sfo_pixel else None, args.aspect)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
