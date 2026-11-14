"""LiveRadar restores its old regular/bold UI glyphs without changing font lifetimes."""

import gc
from pathlib import Path
import weakref

import pytest
from PIL import Image, ImageDraw

from plugins.live_radar import readable_fonts
from plugins.live_radar.live_radar import LiveRadar
from plugins.sports_dashboard.render_fonts import render_font_scope
from utils.app_utils import get_base_ui_font


SAMPLE = "不死鸟总监 | Café 151K viewers · 1h54m"


def _raster(font):
    image = Image.new("L", (600, 64), 0)
    ImageDraw.Draw(image).text((5, 5), SAMPLE, font=font, fill=255)
    return image.tobytes()


@pytest.mark.parametrize("size, weight", [
    (8, "bold"), (9, "normal"), (9, "bold"), (13, "bold"), (14, "bold"), (18, "bold"), (20, "normal"),
])
def test_readable_fonts_match_the_original_ui_chain_at_every_size(size, weight):
    # The exact pre-23662e14 font selection, including real bold below 14px.
    expected = get_base_ui_font(int(size), bold=weight == "bold")
    actual = readable_fonts.readable_font(size, weight)

    assert actual.covers(SAMPLE) == expected.covers(SAMPLE)
    assert Path(actual.path) == Path(expected.path)
    assert actual.getbbox(SAMPLE) == expected.getbbox(SAMPLE)
    assert _raster(actual) == _raster(expected)


@pytest.mark.parametrize("payload", [None, b"invalid font"])
def test_missing_or_corrupt_private_font_keeps_the_existing_public_ui_fallback(tmp_path, monkeypatch, payload):
    monkeypatch.setenv("INKYPI_DATA_DIR", str(tmp_path))
    path = tmp_path / "fonts" / "msyhbd.ttf"
    if payload is not None:
        path.parent.mkdir()
        path.write_bytes(payload)
    font = readable_fonts.readable_font(14, "bold")
    fallback = get_base_ui_font(14, bold=True)

    assert font.covers(SAMPLE)
    assert Path(font.path) == Path(fallback.path)
    assert Path(font.path) != path
    assert font.getlength("不死鸟总监") == fallback.getlength("不死鸟总监")
    assert any(_raster(font))


def test_font_is_released_when_the_render_stops_using_it():
    with render_font_scope():
        font = readable_fonts.readable_font(14, "bold")
        assert readable_fonts.readable_font(14, "bold") is font
        reference = weakref.ref(font)
        del font
        gc.collect()
        assert reference() is None
    assert reference() is None


def test_live_radar_routes_text_through_its_original_ui_font():
    font = LiveRadar._font(14, "bold")

    assert Path(font.path) == Path(get_base_ui_font(14, bold=True).path)
    assert _raster(font) == _raster(readable_fonts.readable_font(14, "bold"))
