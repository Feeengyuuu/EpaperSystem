"""Source marks keep the complete attribution and never touch score semantics."""

import hashlib
import json

from PIL import Image, ImageChops, ImageDraw
import pytest

from plugins.sports_dashboard.common import _ACTIVE_COLORS, DAY_COLORS, DEEP_NIGHT_COLORS
from plugins.sports_dashboard.source_credits import SOURCE_LOGO_DIR
from tests.test_sports_dashboard import _plugin


@pytest.mark.parametrize("colors", [DAY_COLORS, DEEP_NIGHT_COLORS], ids=["day", "night"])
@pytest.mark.parametrize("label,text_parts", [
    ("ESPN CACHE 10:42", [" CACHE 10:42"]),
    ("MLB LIVE", [" LIVE"]),
    ("CSL ESPN STALE 10:42", [" ", " STALE 10:42"]),
    ("FD+ESPN", ["+"]),
    ("FOOTBALL DATA", [" DATA"]),
    ("API+ESPN", ["+"]),
    ("ESPN/API-F 赔率", ["/", " 赔率"]),
    ("ESPN/BET365", ["/"]),
    ("ESPN/365", ["/"]),
    ("ESPN/DK", ["/"]),
    ("OPENF1 LIVE", [" LIVE"]),
    ("VALVE TRACK", [" TRACK"]),
])
def test_source_wordmarks_preserve_all_other_text_inside_the_slot(monkeypatch, colors, label, text_parts):
    plugin = _plugin()
    image = Image.new("RGB", (220, 30), colors["panel"])
    before = image.copy()
    drawn = []
    original = plugin._draw_text_in_box

    def capture(draw, box, text, *args, **kwargs):
        drawn.append(text)
        return original(draw, box, text, *args, **kwargs)

    monkeypatch.setattr(plugin, "_draw_text_in_box", capture)
    token = _ACTIVE_COLORS.set(colors)
    try:
        assert plugin._draw_source_credit(image, ImageDraw.Draw(image), (5, 7, 210, 22), label, size=10)
    finally:
        _ACTIVE_COLORS.reset(token)
    assert drawn == text_parts
    bounds = ImageChops.difference(before, image).getbbox()
    assert bounds and 5 <= bounds[0] < bounds[2] <= 210 and 7 <= bounds[1] < bounds[3] <= 22
    assert image.getpixel((5, 7)) == colors["panel"]  # No backing rectangle.


@pytest.mark.parametrize("label", ["FIRST PITCH", "TIPOFF", "LIVE BOX", "CUSTOM CACHE", "ESPNBET LIVE", "JOLPICA LIVE"])
def test_non_provider_terms_and_unknown_providers_are_left_for_the_text_fallback(monkeypatch, label):
    plugin = _plugin()
    monkeypatch.setattr(plugin, "_load_local_logo", lambda *_a, **_k: pytest.fail("No brand should be inferred"))
    image = Image.new("RGB", (120, 20), "black")
    assert not plugin._draw_source_credit(image, ImageDraw.Draw(image), (0, 0, 120, 20), label)
    assert image.getbbox() is None


def test_missing_or_crowded_source_mark_does_not_partially_paint_the_line(monkeypatch):
    plugin = _plugin()
    image = Image.new("RGB", (120, 20), "black")
    assert not plugin._draw_source_credit(
        image, ImageDraw.Draw(image), (0, 0, 18, 15), "ESPN STALE 10:42", size=10,
    )
    assert image.getbbox() is None
    monkeypatch.setattr(plugin, "_load_local_logo", lambda *_a, **_k: None)
    assert not plugin._draw_source_credit(image, ImageDraw.Draw(image), (0, 0, 120, 20), "ESPN CACHE")
    assert image.getbbox() is None


@pytest.mark.parametrize("source", ["ESPN LIVE", "ESPN CACHE", "ESPN STALE"])
def test_nba_compact_header_dispatches_the_actual_source_and_keeps_the_game_state(monkeypatch, source):
    plugin = _plugin()
    marks, pills = [], []
    monkeypatch.setattr(plugin, "_draw_source_credit", lambda _im, _draw, box, text, **_kw: marks.append((box, text)) or True)
    monkeypatch.setattr(plugin, "_draw_status_pill", lambda _draw, _x, _y, status, *_a: pills.append(status))
    image = Image.new("RGB", (556, 60))
    plugin._draw_nba_compact_header(image, ImageDraw.Draw(image), (0, 0, 555, 59), source, "LIVE", True)
    assert marks[0][1] == source
    assert pills == ["LIVE"]


def test_mlb_hub_header_does_not_turn_first_pitch_into_a_provider(monkeypatch):
    plugin = _plugin()
    labels = []
    original = plugin._draw_source_credit

    def capture(image, draw, box, label, **kwargs):
        labels.append(label)
        return original(image, draw, box, label, **kwargs)

    monkeypatch.setattr(plugin, "_draw_source_credit", capture)
    image = Image.new("RGB", (556, 60))
    plugin._draw_standalone_sport_header(image, ImageDraw.Draw(image), 0, 0, 555, "MLB", {"status": "NEXT"}, "HUB LIVE")
    assert labels == ["FIRST PITCH"]


def test_openf1_asset_is_the_official_provider_mark_with_transparent_padding_removed():
    manifest = json.loads((SOURCE_LOGO_DIR / "manifest.json").read_text(encoding="utf-8"))["logos"]["openf1"]
    path = SOURCE_LOGO_DIR / "openf1.png"
    assert manifest["official_site"] == "https://openf1.org/"
    assert manifest["source_url"] == "https://raw.githubusercontent.com/br-g/openf1/main/documentation/images/logo.png"
    assert hashlib.sha256(path.read_bytes()).hexdigest() == manifest["sha256"]
    with Image.open(path) as image:
        assert image.mode == "RGBA"
        assert image.getchannel("A").getextrema() == (0, 255)
        assert image.width / image.height > 3.5


@pytest.mark.parametrize("key", ["csl", "football_data", "api_sports", "bet365", "draftkings"])
def test_new_source_marks_are_recorded_official_art_with_transparent_ground(key):
    manifest = json.loads((SOURCE_LOGO_DIR / "manifest.json").read_text(encoding="utf-8"))["logos"][key]
    path = SOURCE_LOGO_DIR / f"{key}.png"
    assert manifest["official_site"].startswith("https://")
    assert hashlib.sha256(path.read_bytes()).hexdigest() == manifest["sha256"]
    with Image.open(path) as image:
        assert image.mode == "RGBA" and image.height <= 64
        alpha = image.getchannel("A")
        # Cropped to its ink: every edge touches the artwork, and no ground is left
        # behind the lettering (bet365's green tile is the logo itself).
        assert alpha.getbbox() == (0, 0, image.width, image.height)
        if key != "bet365":
            assert alpha.getextrema()[0] == 0


@pytest.mark.parametrize("colors", [DAY_COLORS, DEEP_NIGHT_COLORS], ids=["day", "night"])
def test_neutral_lettering_takes_the_ink_where_it_would_vanish(colors):
    from plugins.sports_dashboard.source_credits import _inked_neutrals

    on_light = colors is DAY_COLORS
    mark = Image.new("RGBA", (3, 1), (0, 0, 0, 0))
    mark.putpixel((0, 0), (250, 250, 250, 255))  # white lettering
    mark.putpixel((1, 0), (20, 20, 20, 255))  # black lettering
    mark.putpixel((2, 0), (0, 150, 70, 255))  # a brand colour
    inked = _inked_neutrals(mark, colors["text"], on_light)
    expected_white = (*colors["text"], 255) if on_light else (250, 250, 250, 255)
    expected_black = (20, 20, 20, 255) if on_light else (*colors["text"], 255)
    assert inked.getpixel((0, 0)) == expected_white
    assert inked.getpixel((1, 0)) == expected_black
    assert inked.getpixel((2, 0)) == (0, 150, 70, 255)
