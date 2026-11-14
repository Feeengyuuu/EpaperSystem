import json
import sys
from pathlib import Path

from PIL import Image
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from plugins.ticketmaster_events import venue_logos  # noqa: E402
from plugins.ticketmaster_events.venue_logos import paste_venue_logo, venue_logo_path  # noqa: E402

DAY_PAPER, DAY_INK = (232, 238, 235), (18, 26, 32)


def test_venues_are_found_by_the_names_ticketmaster_gives_them():
    assert venue_logo_path("Cobb's Comedy Club").name == "cobbs_comedy_club.png"
    assert venue_logo_path("The Midway").name == venue_logo_path("Midway SF").name == "the_midway.png"
    assert venue_logo_path("Maples Pavilion").name == "stanford_athletics.png"
    # The sports page's arenas are reused from its own folder.
    assert venue_logo_path("Chase Center").parent == venue_logos.SPORTS_VENUE_DIR
    assert venue_logo_path("Some Brewery Back Room") is None


def test_every_venue_logo_has_its_source_and_names():
    record = json.loads((venue_logos.VENUE_DIR / "venues.source.json").read_text(encoding="utf-8"))["logos"]
    assert {path.stem for path in venue_logos.VENUE_DIR.glob("*.png")} == set(record)
    assert all(entry["names"] and entry["source"] for entry in record.values())


@pytest.mark.parametrize("name", ["The Ritz", "The Ritz - San Jose", "The Castro", "Castro Theatre"])
def test_ritz_and_castro_have_readable_wordmarks_with_their_original_colours(name):
    assert venue_logos.venue_is_wordmark(name)
    page = Image.new("RGB", (120, 30), "white")
    width = paste_venue_logo(page, name, 2, 15, (108, 14), DAY_INK, DAY_PAPER)
    pixels = list(page.crop((2, 8, 2 + width, 22)).get_flattened_data())
    if "Ritz" in name:
        # White lettering remains on its original black ground, including in the day theme.
        assert any(max(pixel) < 30 for pixel in pixels)
        assert any(min(pixel) > 230 for pixel in pixels)
    else:
        assert width >= 90  # the full wordmark must not be crushed into a 34 px icon slot
        assert any(r > 200 and g < 100 and b < 120 for r, g, b in pixels)


def test_a_line_logo_takes_the_ink_where_it_would_vanish_and_a_badge_stays_as_printed():
    page = Image.new("RGB", (120, 40), DAY_PAPER)
    width = paste_venue_logo(page, "The Midway", 4, 20, (60, 16), DAY_INK, DAY_PAPER)
    assert 0 < width <= 60
    # The Midway's white lettering shows dark (the page's ink) on the light page, not white.
    pixels = list(page.crop((4, 12, 4 + width, 28)).get_flattened_data())
    assert any(max(pixel) < 80 for pixel in pixels) and not any(min(pixel) > 250 for pixel in pixels)
    badge = Image.new("RGB", (60, 40), DAY_PAPER)
    paste_venue_logo(badge, "San Jose Center for the Performing Arts", 4, 20, (20, 20), DAY_INK, DAY_PAPER)
    # Its gold disc is kept, not swapped for ink.
    assert any(r > 180 and g > 120 and b < 80 for r, g, b in badge.crop((4, 10, 24, 30)).get_flattened_data())
