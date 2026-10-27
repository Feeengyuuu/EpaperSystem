"""Transparent source attribution stays in the footer for both themes."""

import hashlib
import json

from PIL import Image, ImageChops, ImageDraw
import pytest

from plugins.orbital_signal import source_footer as footer
from plugins.orbital_signal.orbital_signal import DAY_PALETTE, NIGHT_PALETTE
from utils.app_utils import get_base_ui_font


def test_official_tsd_asset_matches_its_source_manifest_and_is_transparent():
    path = footer.ASSETS / footer.SOURCE_LOGOS["space_devs"]
    source = json.loads(path.with_suffix(".source.json").read_text(encoding="utf-8"))
    assert source["source_url"] == "https://thespacedevs.com/"
    assert source["asset_url"].endswith("/thespacedevs/images/round_logo.svg")
    assert hashlib.sha256(path.read_bytes()).hexdigest() == source["sha256"]
    with Image.open(path) as image:
        assert image.mode == "RGBA" and image.size == (64, 64)
        assert image.getchannel("A").getextrema() == (0, 255)
        assert all(image.getpixel(point)[3] == 0 for point in ((0, 0), (63, 0), (0, 63), (63, 63)))


@pytest.mark.parametrize("palette", [DAY_PALETTE, NIGHT_PALETTE], ids=["day", "night"])
def test_footer_preserves_transparency_full_notice_and_main_content(monkeypatch, palette):
    image = Image.new("RGB", (800, 480), (93, 47, 72))
    before = image.copy()
    # The existing 3 px separator centered at y=448 already covers row 447.
    # Compare the new footer against that shared rule, not a blank canvas.
    ImageDraw.Draw(before).line((0, 448, 800, 448), fill=palette["rule"], width=3)
    text_calls, logos = [], []
    original_text = ImageDraw.ImageDraw.text
    original_paste = Image.Image.paste

    def text(draw, xy, value, *args, **kwargs):
        bounds = draw.textbbox(xy, value, font=kwargs["font"])
        text_calls.append((value, bounds))
        return original_text(draw, xy, value, *args, **kwargs)

    def paste(target, im, box=None, mask=None):
        if target is image:
            logos.append((im.copy(), box))
        return original_paste(target, im, box, mask)

    monkeypatch.setattr(ImageDraw.ImageDraw, "text", text)
    monkeypatch.setattr(Image.Image, "paste", paste)
    footer.draw_footer(image, (0, 448, 800, 480), palette,
                       get_base_ui_font(10, True), get_base_ui_font(9, True))
    assert ImageChops.difference(image, before).crop((0, 0, 800, 448)).getbbox() is None
    assert [value for value, _ in text_calls] == ["SOURCES", "The Space Devs", "CROWD PROBABILITY", "NOT ADVICE"]
    assert len(logos) == 2
    for _, bounds in text_calls:
        assert 17 <= bounds[0] < bounds[2] <= 783 and 452 <= bounds[1] < bounds[3] <= 479
    assert text_calls[1][1][2] < 450 < text_calls[2][1][0]
    for logo, (x, y) in logos:
        assert logo.height <= 22 and 452 <= y <= y + logo.height <= 479
        transparent = [(px, py) for py in range(logo.height) for px in range(logo.width)
                       if logo.getpixel((px, py))[3] == 0]
        assert transparent
        assert all(image.getpixel((x + px, y + py)) == palette["paper"] for px, py in transparent)


def test_brand_marks_use_native_tsd_colors_and_theme_polymarket_ink():
    tsd_day = footer.source_logo("space_devs", (22, 22), DAY_PALETTE["ink"])
    tsd_night = footer.source_logo("space_devs", (22, 22), NIGHT_PALETTE["ink"])
    assert tsd_day.tobytes() == tsd_night.tobytes()
    for palette in (DAY_PALETTE, NIGHT_PALETTE):
        logo = footer.source_logo("polymarket", (120, 16), palette["ink"])
        assert logo.height == 16
        assert all(pixel[:3] == palette["ink"] for pixel in logo.get_flattened_data() if pixel[3])


def test_missing_source_asset_keeps_names_and_notice_readable(monkeypatch):
    values = []
    original_text = ImageDraw.ImageDraw.text

    def unavailable(_name):
        raise OSError("missing source asset")

    def text(draw, xy, value, *args, **kwargs):
        values.append(value)
        return original_text(draw, xy, value, *args, **kwargs)

    monkeypatch.setattr(footer, "_asset", unavailable)
    monkeypatch.setattr(ImageDraw.ImageDraw, "text", text)
    image = Image.new("RGB", (800, 480))
    footer.draw_footer(image, (0, 448, 800, 480), NIGHT_PALETTE,
                       get_base_ui_font(10, True), get_base_ui_font(9, True))
    assert values == ["SOURCES", "The Space Devs", "Polymarket", "CROWD PROBABILITY", "NOT ADVICE"]
