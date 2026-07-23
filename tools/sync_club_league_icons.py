#!/usr/bin/env python3
"""Build the five current club-league badge assets from pinned public sources."""

from __future__ import annotations

import hashlib
import io
import json
import urllib.request
from pathlib import Path

from PIL import Image


REPO_ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIR = (
    REPO_ROOT
    / "inkypi-weather"
    / "package"
    / "InkyPi"
    / "src"
    / "plugins"
    / "sports_dashboard"
    / "assets"
    / "logos"
    / "club_leagues"
)
ASSET_VERSION = "2026-07-22-current-brand-v1"
CANVAS_SIZE = 128
CONTENT_SIZE = 112
USER_AGENT = "InkyPi club-league asset sync/1.0"

SOURCES = {
    "PL": {
        "filename": "pl.png",
        "brand_version": "Premier League current lion-head mark",
        "url": "https://a.espncdn.com/i/leaguelogos/soccer/500/23.png",
        "source_sha256": "42e2fdced44ff542fa734409667c733841a7bcb903b526c85d12aac78aff2464",
        "crop": (138, 20, 363, 305),
    },
    "PD": {
        "filename": "pd.png",
        "brand_version": "LALIGA current LL symbol",
        "url": (
            "https://assets.laliga.com/assets/logos/LL_RGB_h_color/"
            "LL_RGB_h_color.png"
        ),
        "source_sha256": "08826931042ecda5773c0b713bfa46e6854b87e1d5f3e7a62ef3ffa33eb9464d",
    },
    "BL1": {
        "filename": "bl1.png",
        "brand_version": "Bundesliga current kicker mark",
        "url": "https://a.espncdn.com/i/leaguelogos/soccer/500/10.png",
        "source_sha256": "463b74682c9030631d8fc19a1535875496009a8f296775323127e2a2365094d0",
    },
    "SA": {
        "filename": "sa.png",
        "brand_version": "Serie A Enilive current A mark",
        "url": (
            "https://images.legaseriea.it/image/private/t_q_good/"
            "v1766422496/prd/assets/icons/nav-serieaeni-v2_qoj76t.png"
        ),
        "source_sha256": "4d32132805970749ffdd390a0cff6706ca43a418e11d0c8b8946cd26617866da",
        "crop": (18, 5, 121, 119),
        "drop_near_white": True,
    },
    "FL1": {
        "filename": "fl1.png",
        "brand_version": "Ligue 1 McDonald's 2024+ monogram",
        "url": (
            "https://upload.wikimedia.org/wikipedia/commons/thumb/7/7b/"
            "Logo_Ligue_1_McDonald%27s_2024.svg/"
            "330px-Logo_Ligue_1_McDonald%27s_2024.svg.png"
        ),
        "source_sha256": "c4777c0653c5a6481127c02b782d18065e2f11da89c1a18fdc641ffdb60d85bc",
        "crop": (83, 0, 288, 290),
        "recolor_rgb": (255, 255, 255),
    },
}


def _download(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read()


def _build_icon(
    data: bytes,
    *,
    crop=None,
    recolor_rgb=None,
    drop_near_white=False,
) -> Image.Image:
    with Image.open(io.BytesIO(data)) as source:
        icon = source.convert("RGBA")
    if crop:
        icon = icon.crop(tuple(crop))
    if drop_near_white:
        pixels = icon.load()
        for y in range(icon.height):
            for x in range(icon.width):
                red, green, blue, alpha = pixels[x, y]
                if alpha and min(red, green, blue) >= 235:
                    pixels[x, y] = (red, green, blue, 0)
    bbox = icon.getbbox()
    if not bbox:
        raise ValueError("source icon has no visible pixels")
    icon = icon.crop(bbox)
    if recolor_rgb:
        alpha = icon.getchannel("A")
        solid = Image.new("RGBA", icon.size, (*recolor_rgb, 255))
        solid.putalpha(alpha)
        icon = solid
    icon.thumbnail((CONTENT_SIZE, CONTENT_SIZE), Image.Resampling.LANCZOS)
    canvas = Image.new("RGBA", (CANVAS_SIZE, CANVAS_SIZE), (0, 0, 0, 0))
    canvas.paste(
        icon,
        ((CANVAS_SIZE - icon.width) // 2, (CANVAS_SIZE - icon.height) // 2),
        icon,
    )
    return canvas


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    manifest = {"version": ASSET_VERSION, "icons": {}}
    for league_code, source in SOURCES.items():
        data = _download(source["url"])
        source_sha256 = hashlib.sha256(data).hexdigest()
        if source_sha256 != source["source_sha256"]:
            raise RuntimeError(
                f"{league_code} source hash changed: "
                f"expected {source['source_sha256']}, got {source_sha256}"
            )
        icon = _build_icon(
            data,
            crop=source.get("crop"),
            recolor_rgb=source.get("recolor_rgb"),
            drop_near_white=source.get("drop_near_white", False),
        )
        output_path = OUTPUT_DIR / source["filename"]
        icon.save(output_path, format="PNG", optimize=True, compress_level=9)
        manifest["icons"][league_code] = {
            "filename": source["filename"],
            "brand_version": source["brand_version"],
            "source_url": source["url"],
            "source_sha256": source_sha256,
            "output_sha256": hashlib.sha256(output_path.read_bytes()).hexdigest(),
        }

    manifest_path = OUTPUT_DIR / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(f"Wrote {len(SOURCES)} icons and {manifest_path}")


if __name__ == "__main__":
    main()
