import pytest
from PIL import Image, ImageChops, ImageDraw

from plugins.orbital_signal.market_style import (
    BRAND_FADE_HEIGHT, POLY_BLUE, market_background_color, market_header_palette, market_palette,
)
from plugins.orbital_signal.orbital_signal import DAY_PALETTE, NIGHT_PALETTE, OrbitalSignal


def contrast(a, b):
    def luminance(rgb):
        channels = [v / 255 for v in rgb]
        linear = [v / 12.92 if v <= .04045 else ((v + .055) / 1.055) ** 2.4 for v in channels]
        return sum(value * weight for value, weight in zip(linear, (.2126, .7152, .0722)))
    high, low = sorted((luminance(a), luminance(b)), reverse=True)
    return (high + .05) / (low + .05)


@pytest.mark.parametrize("palette", [DAY_PALETTE, NIGHT_PALETTE], ids=["day", "night"])
def test_market_brand_keeps_small_text_and_semantic_moves_readable(palette):
    local = market_palette(palette)
    for role in ("ink", "muted", "green", "red"):
        assert contrast(local[role], local["paper"]) >= 4.5
    heading = market_header_palette(local)
    for offset in range(BRAND_FADE_HEIGHT + 1):
        assert contrast(heading["ink"], market_background_color(local, offset)) >= 4.5
    assert market_background_color(local, BRAND_FADE_HEIGHT) == local["paper"]
    assert market_background_color(local, 0) not in (POLY_BLUE, local["paper"])
    # Every colored category badge uses this foreground; it must stay legible as well.
    for role in ("blue", "green", "red", "orange"):
        assert contrast(local["white"], local[role]) >= 4.5


@pytest.mark.parametrize("palette", [DAY_PALETTE, NIGHT_PALETTE], ids=["day", "night"])
def test_market_panel_cannot_recolor_launch_region_or_shared_header_footer(monkeypatch, palette):
    plugin = OrbitalSignal({"id": "orbital_signal"})
    monkeypatch.setattr(plugin, "_market_mark", lambda *_args: None)
    monkeypatch.setattr(plugin, "_paste_event_art", lambda *_args: None)
    image = Image.new("RGB", (800, 480), (31, 83, 107))
    before = image.copy()
    original_palette = dict(palette)
    market = {"title": "SAMPLE MARKET", "category": "ECONOMY", "probability": .58, "heat": 70,
              "leader": "UNCHANGED", "volume_24h": 120000, "change_24h": -.15}
    plugin._draw_markets(ImageDraw.Draw(image), (462, 58, 800, 448), [market], palette, None, image=image)
    difference = ImageChops.difference(image, before)
    assert difference.crop((0, 0, 462, 480)).getbbox() is None
    assert difference.crop((0, 0, 800, 58)).getbbox() is None
    assert difference.crop((0, 448, 800, 480)).getbbox() is None
    assert palette == original_palette
    assert image.getpixel((463, 59)) == market_background_color(market_palette(palette), 1)
    assert image.getpixel((799, 447)) == market_palette(palette)["paper"]
    image.close()
    before.close()
