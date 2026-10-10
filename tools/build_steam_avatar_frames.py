"""Build the Steam console's rotating avatar-frame assets from source artwork.

Usage:
    python -I tools/build_steam_avatar_frames.py <source dir> [<output dir>]

The source directory holds square RGBA PNGs named ``NN_标题.png``. For each one
this writes ``frame_NN.png`` (the overlay at display size) and ``mask_NN.png``
(where the avatar photo shows through) plus a ``frames.json`` manifest.

The mask is the frame's enclosed opening. Frames drawn as open shapes (a C, a
torn edge, three matches) are closed with the smallest morphological closing
that encloses the centre; failing that, the convex hull bounds the opening.
The mask reaches a pixel under the frame's inner edge so no gap shows.
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw


DISPLAY_SIZE = 178
WORK_SIZE = DISPLAY_SIZE * 2
BODY_ALPHA = 64
CLOSING_RADII = (0, 1, 2, 3, 4, 6, 8, 11, 15, 20, 26, 34, 44)
MIN_OPENING_SHARE = 0.08
TUCK_RADIUS = 2
NAME_PATTERN = re.compile(r"^(\d{2})_(.+)\.png$")
DEFAULT_OUTPUT = (
    Path(__file__).resolve().parents[1]
    / "inkypi-weather/package/InkyPi/src/plugins/steam_profile_dashboard/assets/avatar_frames"
)


def _shift_or(mask, offsets):
    padded = np.pad(mask, 1)
    height, width = mask.shape
    result = mask.copy()
    for dy, dx in offsets:
        result |= padded[1 + dy:1 + dy + height, 1 + dx:1 + dx + width]
    return result


SQUARE = tuple((dy, dx) for dy in (-1, 0, 1) for dx in (-1, 0, 1) if dy or dx)
CROSS = ((-1, 0), (1, 0), (0, -1), (0, 1))


def dilate(mask, radius):
    # Alternating square and cross steps approximate a disc (an octagon).
    for step in range(radius):
        mask = _shift_or(mask, SQUARE if step % 2 == 0 else CROSS)
    return mask


def erode(mask, radius):
    return ~dilate(~mask, radius)


def close(mask, radius):
    if radius == 0:
        return mask
    # Pad so shapes touching the canvas edge do not erode against it.
    padded = np.pad(mask, radius)
    closed = erode(dilate(padded, radius), radius)
    return closed[radius:-radius, radius:-radius]


def reachable(wall, seed):
    """Pixels 4-connected to ``seed`` (row, column) without crossing ``wall``."""
    # Pillow may share the array buffer; copy so the fill is read back.
    canvas = Image.fromarray((wall * 255).astype(np.uint8)).copy()
    ImageDraw.floodfill(canvas, (seed[1], seed[0]), 128, thresh=0)
    return np.asarray(canvas) == 128


def outside(wall):
    """Pixels reachable from the canvas border without crossing ``wall``."""
    return reachable(np.pad(wall, 1), (0, 0))[1:-1, 1:-1]


def convex_hull(mask):
    ys, xs = np.nonzero(mask)
    points = sorted(set(zip(xs.tolist(), ys.tolist())))

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    lower, upper = [], []
    for point in points:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], point) <= 0:
            lower.pop()
        lower.append(point)
    for point in reversed(points):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], point) <= 0:
            upper.pop()
        upper.append(point)
    hull = Image.new("L", mask.shape[::-1], 0)
    ImageDraw.Draw(hull).polygon(lower[:-1] + upper[:-1], fill=255)
    return np.asarray(hull) > 0


def opening_mask(alpha):
    """Return (filled region, method) for a WORK_SIZE alpha array."""
    body = alpha > BODY_ALPHA
    size = body.shape[0]
    centre = (size // 2, size // 2)
    minimum = MIN_OPENING_SHARE * body.size
    for radius in CLOSING_RADII:
        closed = close(body, radius)
        filled = ~outside(closed)
        interior = filled & ~closed
        if interior[centre] and interior.sum() >= minimum:
            return filled, f"closing-{radius}"
    filled = convex_hull(body)
    if not (filled & ~body)[centre]:
        raise ValueError("frame has no opening around its centre")
    return filled, "convex-hull"


def build_one(source_path, frame_id, output_dir):
    with Image.open(source_path) as opened:
        if opened.width != opened.height:
            raise ValueError(f"{source_path.name}: frame artwork must be square")
        source = opened.convert("RGBA")
    frame = source.resize((DISPLAY_SIZE, DISPLAY_SIZE), Image.Resampling.LANCZOS)
    # LANCZOS rings faintly past hard edges; drop the imperceptible alpha.
    frame.putalpha(frame.getchannel("A").point(lambda value: 0 if value < 8 else value))

    work_alpha = np.asarray(source.resize((WORK_SIZE, WORK_SIZE), Image.Resampling.LANCZOS).getchannel("A"))
    filled, method = opening_mask(work_alpha)
    body = work_alpha > BODY_ALPHA
    # Only the main opening shows the photo, never pockets inside the frame body.
    opening = reachable(~(filled & ~body), (WORK_SIZE // 2, WORK_SIZE // 2))
    tucked = filled & dilate(opening, TUCK_RADIUS)
    mask = Image.fromarray((tucked * 255).astype(np.uint8)).resize(
        (DISPLAY_SIZE, DISPLAY_SIZE), Image.Resampling.BOX,
    )

    frame.save(output_dir / f"frame_{frame_id}.png", optimize=True)
    mask.save(output_dir / f"mask_{frame_id}.png", optimize=True)
    return method, mask.getbbox()


def main(argv):
    if len(argv) not in (2, 3):
        print(__doc__, file=sys.stderr)
        return 2
    source_dir = Path(argv[1])
    output_dir = Path(argv[2]) if len(argv) == 3 else DEFAULT_OUTPUT
    output_dir.mkdir(parents=True, exist_ok=True)
    entries = []
    for source_path in sorted(source_dir.glob("*.png")):
        match = NAME_PATTERN.match(source_path.name)
        if match is None:
            raise ValueError(f"unexpected frame file name: {source_path.name}")
        frame_id, title = match.groups()
        method, bbox = build_one(source_path, frame_id, output_dir)
        digest = hashlib.sha256(source_path.read_bytes()).hexdigest()
        entries.append({"id": frame_id, "title": title, "source_sha256": digest})
        print(f"{frame_id} {title}: {method}, avatar box {bbox}")
    if not entries:
        raise ValueError(f"no frame artwork in {source_dir}")
    manifest = {"version": 1, "size": DISPLAY_SIZE, "frames": entries}
    (output_dir / "frames.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n",
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
