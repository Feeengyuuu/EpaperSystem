#!/usr/bin/env python3
"""Download official EWC game logos for the sports_dashboard EWC sidebar.

The competition slugs and logo URLs are parsed from the official EWC
competitions page. Runtime rendering uses the generated PNG files, not live
network image fetches.
"""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, unquote, urljoin, urlparse
from urllib.request import Request, urlopen

REPO_ROOT = Path(__file__).resolve().parents[1]
INKYPI_ROOT = REPO_ROOT / "inkypi-weather/package/InkyPi"
PC_PACKAGES = INKYPI_ROOT / ".pc-packages"
if PC_PACKAGES.exists():
    import sys

    sys.path.insert(0, str(PC_PACKAGES))

from PIL import Image

DEFAULT_COMPETITIONS_URL = "https://esportsworldcup.com/en/competitions/2026"
DEFAULT_OUTPUT_DIR = INKYPI_ROOT / "src/plugins/sports_dashboard/assets/logos/ewc_games"
MANIFEST_NAME = "manifest.json"
USER_AGENT = "EpaperSystem/1.0 (EWC game logo cache; local project)"

GAME_NAME_OVERRIDES = {
    "apex-legends": "Apex Legends",
    "cod-blackops": "Call of Duty: Black Ops 7",
    "cod-warzone": "Call of Duty: Warzone",
    "chess": "Chess",
    "crossfire": "Crossfire",
    "cs2": "Counter-Strike 2",
    "dota2": "Dota 2",
    "eafc": "EA Sports FC 26",
    "fatal-fury": "Fatal Fury",
    "fortnite": "Fortnite",
    "free-fire": "Free Fire",
    "honor-of-kings": "Honor of Kings",
    "league-of-legends": "League of Legends",
    "mlbb": "Mobile Legends: Bang Bang",
    "mlbb-women": "MLBB Women",
    "overwatch": "Overwatch 2",
    "pmwc": "PUBG Mobile World Cup",
    "pubg-battlegrounds": "PUBG",
    "rainbow-six-siege": "Rainbow Six Siege X",
    "rocket-league": "Rocket League",
    "street-fighter6": "Street Fighter 6",
    "teamfight-tactics": "Teamfight Tactics",
    "tekken8": "TEKKEN 8",
    "trackmania": "Trackmania",
    "valorant": "VALORANT",
}


def request_bytes(url: str, accept: str = "*/*", timeout: int = 45) -> tuple[bytes, str]:
    req = Request(url, headers={"User-Agent": USER_AGENT, "Accept": accept})
    with urlopen(req, timeout=timeout) as response:
        return response.read(), response.headers.get("Content-Type", "")


def request_text(url: str) -> str:
    data, _content_type = request_bytes(url, accept="text/html,application/xhtml+xml")
    return data.decode("utf-8", "replace")


def decode_logo_url(raw_src: str, source_url: str) -> str:
    src = html.unescape(str(raw_src or "")).strip()
    if not src:
        return ""
    absolute = urljoin(source_url, src)
    parsed = urlparse(absolute)
    if parsed.path.endswith("/_next/image"):
        nested = parse_qs(parsed.query).get("url", [""])[0]
        if nested:
            return unquote(nested)
    return absolute


def last_competition_logo_url(card_html: str, source_url: str) -> str:
    img_tags = re.findall(r"<img\b[^>]*?>", card_html or "", flags=re.IGNORECASE | re.DOTALL)
    for tag in reversed(img_tags):
        alt_match = re.search(r"alt=(?P<quote>[\"'])(?P<alt>.*?)(?P=quote)", tag, flags=re.IGNORECASE | re.DOTALL)
        alt = html.unescape(alt_match.group("alt")) if alt_match else ""
        if alt and "competition logo" not in alt.lower():
            continue
        src_match = re.search(r"(?:src|data-src)=(?P<quote>[\"'])(?P<src>.*?)(?P=quote)", tag, flags=re.IGNORECASE | re.DOTALL)
        if src_match:
            return decode_logo_url(src_match.group("src"), source_url)
    return ""


def parse_competition_cards(page_html: str, source_url: str) -> list[dict[str, str]]:
    pattern = re.compile(
        r"href=(?P<quote>[\"'])(?P<href>(?:https?://[^\"']+)?/en/competitions/2026/(?P<slug>[^\"'/?#]+))(?P=quote)",
        re.IGNORECASE,
    )
    cards: list[dict[str, str]] = []
    seen: set[str] = set()
    for match in pattern.finditer(page_html):
        slug = match.group("slug").strip().lower()
        if not slug or slug in seen:
            continue
        prefix = page_html[max(0, match.start() - 2400) : match.start()]
        logo_url = last_competition_logo_url(prefix, source_url)
        if not logo_url:
            continue
        href = match.group("href")
        cards.append(
            {
                "slug": slug,
                "title": GAME_NAME_OVERRIDES.get(slug, title_from_slug(slug)),
                "page_url": href if href.startswith("http") else urljoin(source_url, href),
                "source_url": logo_url,
                "filename": f"{slug}.png",
            }
        )
        seen.add(slug)
    return cards


def title_from_slug(slug: str) -> str:
    words = [part for part in re.split(r"[-_]+", slug) if part]
    return " ".join(word.upper() if word in {"cs2", "eafc", "pmwc"} else word.capitalize() for word in words)


def find_chrome() -> str:
    env_value = os.environ.get("CHROME_PATH")
    candidates = [Path(env_value)] if env_value else []
    candidates.extend(
        [
            Path("C:/Program Files (x86)/Google/Chrome/Application/chrome.exe"),
            Path("C:/Program Files/Google/Chrome/Application/chrome.exe"),
            Path("C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe"),
            Path("C:/Program Files/Microsoft/Edge/Application/msedge.exe"),
            Path.home() / "AppData/Local/ms-playwright/chromium-1217/chrome-win64/chrome.exe",
        ]
    )
    for candidate in candidates:
        if candidate and candidate.exists():
            return str(candidate)
    found = shutil.which("chrome") or shutil.which("msedge")
    if found:
        return found
    raise RuntimeError("No Chrome/Edge executable found; set CHROME_PATH to rasterize SVG logos")


def rasterize_svg(data: bytes, output_path: Path, chrome_path: str | None = None) -> None:
    chrome = chrome_path or find_chrome()
    with tempfile.TemporaryDirectory(prefix="ewc-logo-") as temp_dir_name:
        temp_dir = Path(temp_dir_name)
        svg_path = temp_dir / "source.svg"
        html_path = temp_dir / "render.html"
        raw_png = temp_dir / "raw.png"
        user_data_dir = temp_dir / "profile"
        svg_path.write_bytes(data)
        svg_uri = svg_path.as_uri()
        html_path.write_text(
            """
<!doctype html>
<html>
<head>
<meta charset=\"utf-8\">
<style>
html, body { margin: 0; width: 1024px; height: 512px; background: transparent; overflow: hidden; }
body { display: flex; align-items: center; justify-content: center; }
img { max-width: 920px; max-height: 420px; object-fit: contain; }
</style>
</head>
<body><img src=\"%s\" /></body>
</html>
""".strip()
            % svg_uri,
            encoding="utf-8",
        )
        cmd = [
            chrome,
            "--headless",
            "--disable-gpu",
            "--disable-background-networking",
            "--disable-extensions",
            "--no-first-run",
            "--no-default-browser-check",
            "--hide-scrollbars",
            "--allow-file-access-from-files",
            "--default-background-color=00000000",
            "--window-size=1024,512",
            "--virtual-time-budget=1000",
            f"--user-data-dir={user_data_dir}",
            f"--screenshot={raw_png}",
            html_path.as_uri(),
        ]
        subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=20)
        normalize_and_save_png(raw_png.read_bytes(), output_path)


def transparent_logo(image: Image.Image) -> Image.Image:
    logo = image.convert("RGBA")
    alpha = logo.getchannel("A")
    if alpha.getextrema()[0] < 255:
        return logo
    width, height = logo.size
    if width < 2 or height < 2:
        return logo
    pixels = logo.load()
    corners = [pixels[0, 0][:3], pixels[width - 1, 0][:3], pixels[0, height - 1][:3], pixels[width - 1, height - 1][:3]]
    background = max(set(corners), key=corners.count)
    if max(background) < 245:
        return logo
    tolerance = 20
    for y in range(height):
        for x in range(width):
            red, green, blue, old_alpha = pixels[x, y]
            if all(abs(channel - bg) <= tolerance for channel, bg in zip((red, green, blue), background)):
                pixels[x, y] = (red, green, blue, 0)
            else:
                pixels[x, y] = (red, green, blue, old_alpha)
    return logo


def normalize_and_save_png(data: bytes, output_path: Path) -> None:
    from io import BytesIO

    with Image.open(BytesIO(data)) as source:
        logo = transparent_logo(source)
    bbox = logo.getbbox()
    if bbox:
        logo = logo.crop(bbox)
    logo.thumbnail((512, 512), Image.Resampling.LANCZOS)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    logo.save(output_path, format="PNG", optimize=True)


def write_logo(card: dict[str, str], output_dir: Path, chrome_path: str | None = None) -> dict[str, str]:
    data, content_type = request_bytes(
        card["source_url"],
        accept="image/avif,image/webp,image/apng,image/png,image/svg+xml,image/*,*/*;q=0.8",
    )
    output_path = output_dir / card["filename"]
    is_svg = "svg" in content_type.lower() or data.lstrip().startswith(b"<svg") or data.lstrip().startswith(b"<?xml")
    if is_svg:
        rasterize_svg(data, output_path, chrome_path)
        source_format = "svg"
    else:
        normalize_and_save_png(data, output_path)
        source_format = content_type.split(";")[0] or "image"
    return {**card, "content_type": content_type, "source_format": source_format, "path": card["filename"]}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default=DEFAULT_COMPETITIONS_URL)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--chrome-path", default=os.environ.get("CHROME_PATH"))
    parser.add_argument("--clean", action="store_true", help="Remove existing PNG logos before downloading")
    args = parser.parse_args()

    output_dir = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    if args.clean:
        for png_path in output_dir.glob("*.png"):
            png_path.unlink()

    page_html = request_text(args.url)
    cards = parse_competition_cards(page_html, args.url)
    if not cards:
        raise RuntimeError("No EWC competition logos found")

    games = {}
    for card in cards:
        item = write_logo(card, output_dir, args.chrome_path)
        games[item["slug"]] = item
        print(f"{item['slug']}: {item['path']} <- {item['source_url']}")

    manifest = {
        "source_page": args.url,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "count": len(games),
        "games": games,
    }
    (output_dir / MANIFEST_NAME).write_text(json.dumps(manifest, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {len(games)} logos to {output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())