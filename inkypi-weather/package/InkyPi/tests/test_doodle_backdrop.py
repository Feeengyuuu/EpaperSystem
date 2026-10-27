"""Quiet doodle patterns tinted under page areas (utils.doodle_backdrop)."""

import hashlib
import json
from pathlib import Path

from PIL import Image, ImageDraw
import pytest

from utils.doodle_backdrop import DOODLE_STRENGTH, exact_colour_mask, paint_doodles

PLUGINS = Path(__file__).resolve().parents[1] / "src" / "plugins"
DOODLE_DIRS = [PLUGINS / name / "assets" / "doodles"
               for name in ("daily_wiki_page", "daily_pop", "telegram_digest", "ticketmaster_events")]
DOODLE_DIRS.append(PLUGINS / "live_radar" / "platform_doodles")
PATTERN = DOODLE_DIRS[0] / "photo.png"


@pytest.mark.parametrize("folder", DOODLE_DIRS, ids=lambda folder: folder.relative_to(PLUGINS).parts[0])
def test_every_doodle_tile_is_recorded_with_its_hash(folder):
    record = json.loads((folder / "doodles.source.json").read_text(encoding="utf-8"))["assets"]

    assert sorted(record) == sorted(path.name for path in folder.glob("*.png"))
    for name, entry in record.items():
        assert hashlib.sha256((folder / name).read_bytes()).hexdigest() == entry["sha256"], name
        with Image.open(folder / name) as tile:
            assert tile.mode == "L"
            # The page itself takes no tint: most of a tile is pure paper.
            assert tile.histogram()[255] > 0.7 * tile.width * tile.height


@pytest.mark.parametrize("ground, tint", [((255, 255, 255), (214, 40, 40)), ((0, 0, 0), (96, 214, 220))],
                         ids=["day", "night"])
def test_doodles_take_the_tint_but_stay_faint(ground, tint):
    image = Image.new("RGB", (300, 200), ground)

    assert paint_doodles(image, (10, 10, 289, 189), PATTERN, tint)

    changed = [pixel for pixel in image.get_flattened_data() if pixel != ground]
    assert changed
    limit = DOODLE_STRENGTH * 255 + 1
    for pixel in changed:
        assert all(abs(value - base) <= abs(colour - base) * DOODLE_STRENGTH + 1 and abs(value - base) <= limit
                   for value, base, colour in zip(pixel, ground, tint))
    assert image.getpixel((5, 5)) == ground


def test_grounds_keep_drawn_content_untouched():
    image = Image.new("RGB", (240, 160), (255, 255, 255))
    draw = ImageDraw.Draw(image)
    draw.rectangle((20, 20, 220, 60), fill=(0, 92, 185))
    draw.text((30, 90), "更多要闻 HEADLINE", fill=(0, 0, 0))
    original = image.copy()

    assert paint_doodles(image, (0, 0, 239, 159), PATTERN, (214, 40, 40), strength=1.0, grounds=[(255, 255, 255)])

    pairs = list(zip(original.get_flattened_data(), image.get_flattened_data()))
    assert all(after == before for before, after in pairs if before != (255, 255, 255))
    assert any(after != before for before, after in pairs)


def test_exact_colour_mask_and_missing_or_tiny_boxes():
    region = Image.new("RGB", (4, 1), (255, 255, 255))
    region.putpixel((1, 0), (254, 255, 255))
    region.putpixel((2, 0), (0, 0, 0))

    assert list(exact_colour_mask(region, [(255, 255, 255), (0, 0, 0)]).get_flattened_data()) == [255, 0, 255, 255]

    image = Image.new("RGB", (40, 40), (255, 255, 255))
    assert not paint_doodles(image, (0, 0, 39, 39), PATTERN.with_name("missing.png"), (0, 0, 0))
    assert not paint_doodles(image, (0, 0, 5, 39), PATTERN, (0, 0, 0))
    assert set(image.get_flattened_data()) == {(255, 255, 255)}


def test_every_live_radar_platform_with_a_brand_colour_has_its_doodles():
    from plugins.live_radar.live_radar import PLATFORM_BRAND_COLORS, PLATFORM_DOODLE_FILES

    assert set(PLATFORM_DOODLE_FILES) == set(PLATFORM_BRAND_COLORS)
    assert all(Path(path).is_file() for path in PLATFORM_DOODLE_FILES.values())
