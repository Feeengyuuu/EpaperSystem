"""Official CS2 marks remain local, bounded and separate from score attribution."""

import hashlib
import json
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw
import pytest

from plugins.sports_dashboard.common import _ACTIVE_COLORS, DAY_COLORS, DEEP_NIGHT_COLORS
from plugins.sports_dashboard.esports_render import ESPORTS_SOURCE_LOGO_DIR
from tests.test_sports_dashboard import _plugin


@pytest.mark.parametrize("colors", [DAY_COLORS, DEEP_NIGHT_COLORS], ids=["day", "night"])
@pytest.mark.parametrize("attribution", [False, True], ids=["header", "credit"])
def test_provider_mark_and_label_stay_inside_their_existing_slot(colors, attribution, monkeypatch):
    plugin = _plugin()
    labels = []
    original = plugin._draw_text_in_box

    def record(draw, box, text, *args, **kwargs):
        labels.append(text)
        return original(draw, box, text, *args, **kwargs)

    monkeypatch.setattr(plugin, "_draw_text_in_box", record)
    image = Image.new("RGB", (160, 40), colors["panel"])
    before = image.copy()
    box = (8, 10, 148, 21 if attribution else 26)
    token = _ACTIVE_COLORS.set(colors)
    try:
        assert plugin._draw_cs2_provider_label(
            image, ImageDraw.Draw(image), box, "PANDASCORE CACHE", colors["muted"], attribution=attribution,
        )
    finally:
        _ACTIVE_COLORS.reset(token)
    changed = ImageChops.difference(image, before).getbbox()
    assert changed is not None
    assert box[0] <= changed[0] < changed[2] <= box[2]
    assert box[1] <= changed[1] < changed[3] <= box[3]
    assert labels == ["Source:" if attribution else "CACHE"]


@pytest.mark.parametrize("colors", [DAY_COLORS, DEEP_NIGHT_COLORS], ids=["day", "night"])
def test_wordmark_transparency_reveals_the_panel_without_changing_the_cached_white_art(colors, monkeypatch):
    plugin = _plugin()
    path = str(Path(ESPORTS_SOURCE_LOGO_DIR) / "pandascore.png")
    cached = plugin._load_local_logo(path, (74, 12), alpha_threshold=8)
    cached_pixels = cached.tobytes()
    image = Image.new("RGB", (100, 30), colors["panel"])
    pasted = []
    original_paste = image.paste

    def record(art, xy, mask=None):
        pasted.append((art.copy(), xy))
        return original_paste(art, xy, mask)

    monkeypatch.setattr(image, "paste", record)
    token = _ACTIVE_COLORS.set(colors)
    try:
        assert plugin._draw_cs2_provider_label(image, ImageDraw.Draw(image), (4, 4, 96, 24), "PANDASCORE", colors["muted"])
    finally:
        _ACTIVE_COLORS.reset(token)
    assert len(pasted) == 1
    art, (left, top) = pasted[0]
    transparent = [(x, y) for y in range(art.height) for x in range(art.width) if art.getpixel((x, y))[3] == 0]
    assert transparent  # The real wordmark has gaps and counters, not an opaque rectangle.
    assert all(image.getpixel((left + x, top + y)) == colors["panel"] for x, y in transparent)
    assert art.getchannel("A").tobytes() == cached.getchannel("A").tobytes()
    if colors is DAY_COLORS:
        assert {pixel[:3] for pixel in art.get_flattened_data() if pixel[3] == 255} == {colors["text"]}
    else:
        assert art.tobytes() == cached_pixels
    assert cached.tobytes() == cached_pixels


@pytest.mark.parametrize("source", ["HLTV", "Valve", "OpenDota", "PandaScoreMirror", ""])
def test_unknown_or_vendor_names_are_never_relabelled_as_pandascore(source, monkeypatch):
    plugin = _plugin()
    monkeypatch.setattr(plugin, "_load_local_logo", lambda *_a, **_k: pytest.fail("unexpected logo lookup"))
    image = Image.new("RGB", (150, 20))
    assert not plugin._draw_cs2_provider_label(image, ImageDraw.Draw(image), (0, 0, 150, 20), source, (255, 255, 255))
    assert image.getbbox() is None


def test_missing_provider_art_preserves_the_named_source_credit(monkeypatch):
    plugin = _plugin()
    labels = []
    monkeypatch.setattr(plugin, "_load_local_logo", lambda *_a, **_k: None)
    monkeypatch.setattr(plugin, "_draw_text_in_box", lambda _d, _b, text, *_a, **_k: labels.append(text))
    image = Image.new("RGB", (200, 30))
    plugin._draw_cs2_source_credit(
        image, ImageDraw.Draw(image), (0, 0, 190, 11), {"series": "CS"}, "PandaScore",
        "SOURCE: PANDASCORE", plugin._font(8), (255, 255, 255),
    )
    assert labels == ["SOURCE: PANDASCORE"]


@pytest.mark.parametrize("state", ["PANDASCORE STALE PARTIAL", "PANDASCORE AUTH LIMIT"])
def test_crowded_status_returns_to_text_instead_of_dropping_the_warning(state):
    plugin = _plugin()
    image = Image.new("RGB", (80, 20))
    assert not plugin._draw_cs2_provider_label(image, ImageDraw.Draw(image), (0, 0, 80, 16), state, (255, 255, 255))
    assert image.getbbox() is None


@pytest.mark.parametrize("title,expected", [("RECENT", True), ("UPCOMING", False)])
def test_valve_scope_mark_is_only_used_for_empty_recent_results(title, expected, monkeypatch):
    plugin = _plugin()
    scopes = []
    texts = []
    monkeypatch.setattr(plugin, "_draw_cs2_empty_recent", lambda *_a: scopes.append(True) or True)
    monkeypatch.setattr(plugin, "_draw_valve_ti_empty_slot_filler", lambda *_a: None)
    image = Image.new("RGB", (800, 480))
    draw = ImageDraw.Draw(image)
    original = draw.text

    def record(xy, text, *args, **kwargs):
        texts.append(text)
        return original(xy, text, *args, **kwargs)

    monkeypatch.setattr(draw, "text", record)
    plugin._draw_valve_esports_recent_rows(
        image, draw, 556, 244, 374, [],
        {"series": "CS", "auto_follow": True, "schedule_state": "stale"}, (255, 0, 0), title=title,
    )
    assert bool(scopes) is expected
    if not expected:
        assert "Schedule unavailable" in texts


def test_official_assets_have_valid_provenance_alpha_and_fit_the_decoder_budget():
    folder = Path(ESPORTS_SOURCE_LOGO_DIR)
    manifest = json.loads((folder / "manifest.json").read_text())["logos"]
    plugin = _plugin()
    for name in ("pandascore", "valve"):
        path = folder / f"{name}.png"
        entry = manifest[name]
        assert hashlib.sha256(path.read_bytes()).hexdigest() == entry["sha256"]
        assert entry["official_site"].startswith("https://www.")
        with Image.open(path) as original:
            assert original.mode == "RGBA"
            assert original.getchannel("A").getextrema() == (0, 255)
            logo = plugin._load_local_logo(str(path), (74, 14), alpha_threshold=8)
            assert logo is not None
            bbox = original.getbbox()
            ratio = (bbox[2] - bbox[0]) / (bbox[3] - bbox[1])
            assert abs(logo.width - logo.height * ratio) <= ratio  # At most one raster row of rounding.
            assert logo.width <= 74 and logo.height <= 14
