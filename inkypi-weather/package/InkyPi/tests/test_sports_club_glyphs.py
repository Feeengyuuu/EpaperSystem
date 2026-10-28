"""Football metadata must remain readable with the device's bold UI font."""

from datetime import datetime, timezone

from PIL import Image, ImageDraw
import pytest

from plugins.sports_dashboard.common import (
    DAY_COLORS,
    DEEP_NIGHT_COLORS,
    _ACTIVE_COLORS,
)
from plugins.sports_dashboard.sports_dashboard import SportsDashboard


def _mls_event(**changes):
    event = {
        "league_code": "MLS",
        "start_utc": datetime(2026, 10, 14, 23, 30, tzinfo=timezone.utc),
        "status": "SCHEDULED",
        "home_name": "Columbus Crew",
        "away_name": "Orlando City SC",
        "broadcast": "Apple TV",
        "venue": "ScottsMiracle-Gro Field",
    }
    event.update(changes)
    return event


@pytest.mark.parametrize(
    ("broadcast", "venue", "expected"),
    [
        ("Apple TV", "ScottsMiracle-Gro Field", "Apple TV | ScottsMiracle-Gro Field"),
        ("Apple TV", "", "Apple TV"),
        ("", "ScottsMiracle-Gro Field", "ScottsMiracle-Gro Field"),
        ("", "", ""),
    ],
)
def test_club_metadata_uses_font_safe_separator_without_dangling_delimiters(
    broadcast, venue, expected,
):
    event = _mls_event(broadcast=broadcast, venue=venue)
    original = dict(event)

    assert SportsDashboard._club_focus_schedule_labels(event, event["start_utc"])[1] == expected
    adapted = SportsDashboard._club_football_worldcup_event(event, timezone.utc)
    assert adapted["block"] == (expected or "美职联")
    assert event == original


def test_club_separator_change_preserves_punctuation_inside_provider_names():
    event = _mls_event(venue="Jean·Bouin", matchday=21)
    adapted = SportsDashboard._club_football_worldcup_event(event, timezone.utc)

    assert adapted["block"] == "第21轮 | Apple TV | Jean·Bouin"
    assert adapted["venue"] == event["venue"] == "Jean·Bouin"


@pytest.mark.parametrize("palette", [DAY_COLORS, DEEP_NIGHT_COLORS], ids=["day", "night"])
def test_mls_upcoming_row_draws_supported_separator_in_both_themes(monkeypatch, palette):
    plugin = SportsDashboard({"id": "sports_dashboard"})
    # A network without a bundled logo stays named in the row's context line, separator and all.
    adapted = plugin._club_football_worldcup_event(_mls_event(broadcast="TVA Sports", venue="Rice Park"), timezone.utc)
    image = Image.new("RGB", (270, 40), palette["panel"])
    draw = ImageDraw.Draw(image)
    drawn = []
    original_text = draw.text

    def record_text(xy, text, *args, **kwargs):
        drawn.append((str(text), kwargs.get("font")))
        return original_text(xy, text, *args, **kwargs)

    monkeypatch.setattr(draw, "text", record_text)
    monkeypatch.setattr(plugin, "_draw_worldcup_row_lineup", lambda *_args, **_kwargs: None)
    token = _ACTIVE_COLORS.set(palette)
    try:
        plugin._draw_worldcup_mini_match_row(
            image, draw, 0, 269, 0, 33, adapted, "VS", show_time=True,
        )
    finally:
        _ACTIVE_COLORS.reset(token)

    metadata_fonts = [font for text, font in drawn if text == "TVA Sports | Rice Park"]
    assert len(metadata_fonts) == 1
    font = metadata_fonts[0]
    separator = font.getmask("|")
    missing = font.getmask("\U0010ffff")
    assert (separator.size, bytes(separator)) != (missing.size, bytes(missing))


@pytest.mark.parametrize(("venue", "named"), [("Rice Park", True), ("ScottsMiracle-Gro Field", False)])
def test_a_network_with_a_logo_leaves_the_row_line_to_the_ground(monkeypatch, venue, named):
    plugin = SportsDashboard({"id": "sports_dashboard"})
    adapted = plugin._club_football_worldcup_event(_mls_event(venue=venue), timezone.utc)
    image = Image.new("RGB", (270, 40), DAY_COLORS["panel"])
    draw = ImageDraw.Draw(image)
    drawn = []
    original_text = draw.text

    def record_text(xy, text, *args, **kwargs):
        drawn.append(str(text))
        return original_text(xy, text, *args, **kwargs)

    monkeypatch.setattr(draw, "text", record_text)
    monkeypatch.setattr(plugin, "_draw_worldcup_row_lineup", lambda *_args, **_kwargs: None)

    plugin._draw_worldcup_mini_match_row(image, draw, 0, 269, 0, 33, adapted, "VS", show_time=True)

    # Apple TV is its logo after the date; the line keeps the ground alone, as its own logo
    # where it has one (Columbus's green wordmark).
    assert not any("Apple TV" in text for text in drawn)
    assert (venue in drawn) is named
    if not named:
        line = image.crop((40, 0, 230, 14)).get_flattened_data()
        assert any(green > red + 60 and green > blue + 30 for red, green, blue in line)


@pytest.mark.parametrize("round_value", [21, {"number": 21}, {"text": "Round 21"}])
def test_csl_round_metadata_uses_font_safe_separator(round_value):
    assert SportsDashboard._csl_event_block({}, {"round": round_value}) == "中超 | 第21轮"
