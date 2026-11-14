"""The accepted readable LiveRadar pairing keeps its pixels without resident font faces."""

import gc
from pathlib import Path
import weakref

import pytest
from PIL import Image, ImageDraw

from plugins.live_radar import readable_fonts
from plugins.live_radar.live_radar import LiveRadar
from plugins.sports_dashboard.esports_font_runs import EsportsFont
from plugins.sports_dashboard.render_fonts import render_font_scope
from utils.app_utils import get_base_ui_font
from utils.fonts import load_font


SRC = Path(__file__).resolve().parents[1] / "src"
SAMPLE = "不死鸟总监 | Café 151K viewers · 1h54m"


def _raster(font):
    image = Image.new("L", (600, 64), 0)
    ImageDraw.Draw(image).text((5, 5), SAMPLE, font=font, fill=255)
    return image.tobytes()


@pytest.mark.parametrize("size, weight, cjk_weight, latin_name", [
    (9, "normal", 500, "Inter-SemiBold.ttf"),
    (13, "bold", 500, "Inter-SemiBold.ttf"),
    (14, "bold", 600, "Inter-Bold.ttf"),
    (20, "normal", 500, "Inter-SemiBold.ttf"),
])
def test_readable_fonts_match_the_accepted_preview_pixels(size, weight, cjk_weight, latin_name):
    # These are the accepted preview's fixed face choices, not the production loader.
    cjk = load_font(SRC / "static/fonts/NotoSansSC-VF.ttf", size)
    cjk.set_variation_by_axes([cjk_weight])
    latin = load_font(SRC / "plugins/flight_radar/assets/fonts" / latin_name, size)
    expected = EsportsFont(latin, cjk)
    actual = readable_fonts.readable_font(size, weight)

    assert actual.covers(SAMPLE)
    assert actual.getbbox(SAMPLE) == expected.getbbox(SAMPLE)
    assert _raster(actual) == _raster(expected)


@pytest.mark.parametrize("payload", [None, b"invalid font"])
def test_missing_or_corrupt_chinese_font_uses_readable_ui_glyphs(tmp_path, monkeypatch, payload):
    path = tmp_path / "unavailable-cjk.ttf"
    if payload is not None:
        path.write_bytes(payload)
    monkeypatch.setattr(readable_fonts, "CJK_PATH", path)
    font = readable_fonts.readable_font(14, "bold")
    fallback = get_base_ui_font(14, bold=True)

    assert font.covers(SAMPLE)
    assert Path(font.fallback.path) == Path(fallback.path)
    assert font.getlength("不死鸟总监") == fallback.getlength("不死鸟总监")
    assert any(_raster(font))


def test_missing_latin_font_keeps_the_readable_cjk_face(tmp_path, monkeypatch):
    monkeypatch.setattr(readable_fonts, "LATIN_PATHS", {False: tmp_path / "absent.ttf", True: tmp_path / "absent.ttf"})
    font = readable_fonts.readable_font(13, "bold")

    assert Path(font.path) == readable_fonts.CJK_PATH
    assert font.covers(SAMPLE)
    assert any(_raster(font))


def test_font_pair_and_native_faces_are_released_when_the_render_stops_using_them():
    with render_font_scope():
        font = readable_fonts.readable_font(14, "bold")
        assert readable_fonts.readable_font(14, "bold") is font
        references = [weakref.ref(face) for face in (font, font.primary, font.fallback)]
        del font
        gc.collect()
        assert all(reference() is None for reference in references)
    assert all(reference() is None for reference in references)


def test_live_radar_routes_text_through_the_accepted_font_pair():
    font = LiveRadar._font(14, "bold")

    assert Path(font.primary.path).name == "Inter-Bold.ttf"
    assert Path(font.fallback.path).name == "NotoSansSC-VF.ttf"
    assert _raster(font) == _raster(readable_fonts.readable_font(14, "bold"))
