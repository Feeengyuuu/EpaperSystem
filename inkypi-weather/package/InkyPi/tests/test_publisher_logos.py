import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from plugins.base_plugin import publisher_logos  # noqa: E402
from plugins.base_plugin.publisher_logos import NAMES, PUBLISHER_LOGO_DIR, logo_key, publisher_logo  # noqa: E402

NIGHT_INK = (238, 236, 230)


def test_publishers_and_magazines_are_found_by_the_names_sources_give():
    assert [logo_key(name) for name in ("Marvel", "DC Comics", "National Periodical Publications", "Boom! Studios",
                                        "Kodansha Comics", "講談社", "Glénat", "D.C. Thomson & Co. Ltd", "VIZ Media",
                                        "Rolling Stone", "Rolling Stone Australia", "The New Yorker", "TIME",
                                        "People Special Editions")] == [
        "marvel", "dc", "dc", "boom", "kodansha", "kodansha", "glenat", "dc_thomson", "viz",
        "rolling_stone", "rolling_stone", "new_yorker", "time", "people"]
    # A title only begins with a magazine's name when that magazine says so: no TIME for Time Out,
    # no Variety for a puzzle book.
    assert [logo_key(name) for name in ("Time Out", "Variety Puzzles and Games", "Life & Style", "Unknown", "")] == [
        None, None, None, None, None]


def test_every_named_logo_has_its_art_and_every_art_its_names():
    assert {path.stem for path in PUBLISHER_LOGO_DIR.glob("*.png")} == set(NAMES)
    assert (PUBLISHER_LOGO_DIR / "publisher_logos.source.json").is_file()


def test_a_logo_fits_its_box_and_inks_only_its_dark_parts_at_night():
    time = publisher_logo("TIME", (64, 13))
    assert time.height <= 13 and time.width <= 64
    # TIME's red stays red by night; Kodansha's black lettering takes the page's ink.
    night_time = publisher_logo("TIME", (64, 13), NIGHT_INK)
    assert NIGHT_INK not in {pixel[:3] for pixel in night_time.get_flattened_data() if pixel[3] == 255}
    night_kodansha = publisher_logo("Kodansha", (64, 13), NIGHT_INK)
    assert NIGHT_INK in {pixel[:3] for pixel in night_kodansha.get_flattened_data() if pixel[3] == 255}
    # A badge (white letters on its own black box) stays as printed.
    assert publisher_logos._is_badge("casterman") and not publisher_logos._is_badge("kodansha")
    assert publisher_logo("Casterman", (64, 13), NIGHT_INK).tobytes() == publisher_logo("Casterman", (64, 13)).tobytes()
    assert publisher_logo("Nobody Press", (64, 13)) is None
