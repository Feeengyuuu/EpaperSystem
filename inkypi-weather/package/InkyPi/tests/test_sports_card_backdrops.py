"""Stadium scenes in the sports featured cards and doodle patterns under their lists."""

import hashlib
import json

from PIL import Image, ImageDraw
import pytest

from plugins.sports_dashboard.card_backdrops import (
    CARD_BACKDROP_DIR,
    CARD_BACKDROP_KINDS,
    LIST_BACKDROP_KINDS,
    SPORT_BACKDROP_KINDS,
    card_backdrop_box,
    paint_card_backdrop,
    paint_list_backdrop,
)
from plugins.sports_dashboard.common import DAY_COLORS, DEEP_NIGHT_COLORS

ART_NAMES = [f"{kind}.png" for kind in CARD_BACKDROP_KINDS] + [f"list_{kind}.png" for kind in LIST_BACKDROP_KINDS]


def _luma(rgb):
    return 0.299 * rgb[0] + 0.587 * rgb[1] + 0.114 * rgb[2]


def _card(colors, kind="football"):
    image = Image.new("RGB", (300, 180), colors["paper"])
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((20, 20, 279, 159), radius=5, fill=colors["panel"], outline=colors["border"], width=2)
    draw.rectangle((21, 21, 28, 158), fill=colors["red"])
    painted = paint_card_backdrop(image, card_backdrop_box(20, 20, 279, 159), kind, colors["panel"])
    return image, painted


def test_every_art_file_is_recorded_with_its_hash():
    record = json.loads((CARD_BACKDROP_DIR / "card_backdrops.source.json").read_text(encoding="utf-8"))["assets"]

    assert sorted(record) == sorted(ART_NAMES)
    for name in ART_NAMES:
        data = (CARD_BACKDROP_DIR / name).read_bytes()
        assert hashlib.sha256(data).hexdigest() == record[name]["sha256"], name
    assert set(SPORT_BACKDROP_KINDS.values()) <= set(CARD_BACKDROP_KINDS)


@pytest.mark.parametrize("kind", CARD_BACKDROP_KINDS)
def test_a_scene_fills_the_card_inside_its_outline_and_stripe(kind):
    image, painted = _card(DAY_COLORS, kind)

    assert painted
    assert image.getpixel((20, 90)) == DAY_COLORS["border"]
    assert image.getpixel((279, 90)) == DAY_COLORS["border"]
    assert image.getpixel((24, 90)) == DAY_COLORS["red"]
    assert image.getpixel((0, 0)) == DAY_COLORS["paper"]
    inside = set(image.crop((30, 23, 276, 156)).get_flattened_data())
    # The scene's colour arrives, and its cream ground becomes the panel itself.
    assert len(inside) > 50
    top_middle = image.getpixel((150, 26))
    assert max(abs(a - b) for a, b in zip(top_middle, DAY_COLORS["panel"])) <= 12


def test_the_night_scene_glows_faintly_on_the_dark_panel():
    image, painted = _card(DEEP_NIGHT_COLORS, "basketball")

    assert painted
    pixels = list(image.crop((30, 23, 276, 156)).get_flattened_data())
    assert sum(_luma(pixel) for pixel in pixels) / len(pixels) < 70
    assert max(_luma(pixel) for pixel in pixels) < 200


def test_a_wide_band_takes_the_pale_horizon_rather_than_the_floor():
    image = Image.new("RGB", (560, 70), DAY_COLORS["panel"])
    assert paint_card_backdrop(image, (0, 0, 559, 59), "basketball", DAY_COLORS["panel"])

    centre = list(image.crop((200, 0, 360, 60)).get_flattened_data())
    assert sum(_luma(pixel) for pixel in centre) / len(centre) > 215


def test_unknown_kinds_and_tiny_boxes_draw_nothing():
    image = Image.new("RGB", (40, 40), DAY_COLORS["panel"])

    assert not paint_card_backdrop(image, (0, 0, 39, 39), None, DAY_COLORS["panel"])
    assert not paint_card_backdrop(image, (0, 0, 5, 39), "football", DAY_COLORS["panel"])
    assert not paint_list_backdrop(image, (0, 0, 39, 39), "tactical", DAY_COLORS["panel"], (DAY_COLORS["panel"],))
    assert set(image.get_flattened_data()) == {DAY_COLORS["panel"]}


@pytest.mark.parametrize("colors", [DAY_COLORS, DEEP_NIGHT_COLORS], ids=["day", "night"])
def test_the_list_pattern_only_takes_plain_ground_and_stays_faint(colors):
    image = Image.new("RGB", (320, 320), colors["panel"])
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, 159, 319), fill=colors["paper"])
    draw.rectangle((40, 40, 280, 70), fill=colors["green"])
    draw.text((60, 120), "UPCOMING", fill=colors["text"])
    original = image.copy()
    grounds = (colors["paper"], colors["panel"])

    assert paint_list_backdrop(image, (0, 0, 319, 319), "football", colors["panel"], grounds)

    pairs = list(zip(original.get_flattened_data(), image.get_flattened_data()))
    assert all(after == before for before, after in pairs if before not in grounds)
    changed = [after for before, after in pairs if after != before]
    assert changed
    panel_luma = _luma(colors["panel"])
    assert all(abs(_luma(pixel) - panel_luma) < 90 for pixel in changed)
