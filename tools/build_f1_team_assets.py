"""Bundle the F1 teams' logos and the header's grid of cars for the sports dashboard's F1 panel.

    python tools/build_f1_team_assets.py

Logos are the teams' official marks from formula1.com (the colour mark and the white one, which
the panel uses on dark ground when the colour mark is too dark there). The cars are top-down
illustrations of each team's 2026 car, generated from formula1.com's official side view of it with
GPT Image 2.5 (Creative Claw), as are the finishing places' numerals; the generated PNGs are
fetched from their permanent URLs. Writes plugins/sports_dashboard/assets/logos/f1_teams/<team>.png
(+ <team>_light.png) and assets/decor/f1_grid/<team>.png and place_<n>.png, with the sources in
assets/decor/f1_grid/f1_grid.source.json.
"""

from __future__ import annotations

import io
import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen

from PIL import Image

REPO_ROOT = Path(__file__).resolve().parents[1]
ASSETS = REPO_ROOT / "inkypi-weather" / "package" / "InkyPi" / "src" / "plugins" / "sports_dashboard" / "assets"
LOGOS = ASSETS / "logos" / "f1_teams"
CARS = ASSETS / "decor" / "f1_grid"
USER_AGENT = "InkyPi asset build (https://github.com/Feeengyuuu/EpaperSystem)"
MEDIA = "https://media.formula1.com/image/upload/f_png/{transform}/q_auto/v1740000001/common/f1/2026/{slug}/2026{slug}{name}.webp"
CDN = "https://cdn.creativeclaw.co/u/e25dbd7a/images/{}.png"
# team key (as the panel names it) -> (formula1.com slug, generated top-down car)
TEAMS = {
    "mclaren": ("mclaren", "df145afb-6a3a-4a3c-8f25-2f5234d8065a"),
    "ferrari": ("ferrari", "a4da941f-b42c-4991-b48f-b92d82f11f6e"),
    "red_bull": ("redbullracing", "9bedab4b-5fdb-4a43-ae5e-69b4a3b340d1"),
    "mercedes": ("mercedes", "308d4a6e-a3b8-405e-93d2-1fb256dc4495"),
    "aston_martin": ("astonmartin", "1fd7c92a-766b-4cb4-89bc-ae79cfbf89ae"),
    "alpine": ("alpine", "cfff8a5b-30d0-4af3-aa15-01f41bad8a7b"),
    "williams": ("williams", "d22ef0c5-7780-4fd2-b427-d6f1b8619d6b"),
    "racing_bulls": ("racingbulls", "3ba9b824-7f68-4400-8a6e-481a7cbd73c1"),
    "haas": ("haasf1team", "0defdd9b-9186-4a68-9376-37b87e889751"),
    "audi": ("audi", "7d2dcc27-6baa-40b3-82b2-dc6ddc126915"),
    "cadillac": ("cadillac", "fa81f0bd-f481-4b95-a058-0151612d049d"),
}
# The finishing places' numerals 1-5 (gold, silver, bronze, then white), generated as one row.
PLACES = "bdd0c0f8-27ba-461e-9a36-937aba9d0b65"
PLACE_NAMES = ("place_1", "place_2", "place_3", "place_4", "place_5")
LOGO_HEIGHT = 64
CAR_HEIGHT = 48
PLACE_HEIGHT = 48


def fetch(url, timeout=60):
    with urlopen(Request(url, headers={"User-Agent": USER_AGENT}), timeout=timeout) as response:
        return response.read()


def trimmed(data, threshold=24):
    """The image cropped to its visible pixels, the faint haze (alpha up to ``threshold``) cleared."""
    image = Image.open(io.BytesIO(data)).convert("RGBA") if isinstance(data, bytes) else data
    alpha = image.getchannel("A").point(lambda value: 0 if value <= threshold else value)
    image.putalpha(alpha)
    box = alpha.getbbox()
    return image.crop(box) if box else image


def split_row(data):
    """A generated row of shapes split on its transparent gaps, left to right."""
    sheet = trimmed(data)
    alpha = sheet.getchannel("A")
    filled = [alpha.crop((x, 0, x + 1, sheet.height)).getbbox() is not None for x in range(sheet.width)]
    spans, start = [], None
    for x, on in enumerate([*filled, False]):
        if on and start is None:
            start = x
        elif not on and start is not None:
            spans.append((start, x))
            start = None
    return [trimmed(sheet.crop((x0, 0, x1, sheet.height))) for x0, x1 in spans]


def save(image, path, height, max_width=None):
    width = round(image.width * height / image.height)
    if max_width and width > max_width:
        width, height = max_width, round(image.height * max_width / image.width)
    path.parent.mkdir(parents=True, exist_ok=True)
    image.resize((width, height), Image.Resampling.LANCZOS).save(path, optimize=True)


def main():
    sources = {}
    for key, (slug, car) in TEAMS.items():
        logo_url = MEDIA.format(transform="c_fit,h_256", slug=slug, name="logo")
        light_url = MEDIA.format(transform="c_fit,h_256", slug=slug, name="logolight")
        save(trimmed(fetch(logo_url)), LOGOS / f"{key}.png", LOGO_HEIGHT, 160)
        save(trimmed(fetch(light_url)), LOGOS / f"{key}_light.png", LOGO_HEIGHT, 160)
        car_url = CDN.format(car)
        save(trimmed(fetch(car_url)), CARS / f"{key}.png", CAR_HEIGHT)
        sources[key] = {
            "logo": logo_url,
            "logo_light": light_url,
            "car": car_url,
            "car_reference": MEDIA.format(transform="c_fit,w_1400", slug=slug, name="carright"),
        }
        print(key)
    places_url = CDN.format(PLACES)
    split = split_row(fetch(places_url))
    if len(split) != len(PLACE_NAMES):
        raise SystemExit(f"expected {len(PLACE_NAMES)} numerals, found {len(split)}")
    for name, numeral in zip(PLACE_NAMES, split):
        save(numeral, CARS / f"{name}.png", PLACE_HEIGHT)
    manifest = {
        "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "logos": "Official team marks from formula1.com (colour and white variants).",
        "cars": ("Top-down 2026 cars generated with image/gpt-image-2.5-sunburst (Creative Claw, 1536x512, "
                 "transparent) from formula1.com's official side view of each car, nose to the right."),
        "teams": sources,
        "places": {"source": places_url, "files": list(PLACE_NAMES),
                   "note": "Italic motorsport numerals: 1 gold, 2 silver, 3 bronze, 4 and 5 white (tinted to the ink by day)"},
    }
    (CARS / "f1_grid.source.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                                              encoding="utf-8", newline="\n")


if __name__ == "__main__":
    main()
