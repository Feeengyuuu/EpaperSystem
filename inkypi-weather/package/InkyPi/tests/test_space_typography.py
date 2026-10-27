"""Mixed scripts and technical readings keep their actual measured glyphs."""
from PIL import Image, ImageDraw
import pytest

from utils.space_typography import MixedDraw, scoped_space_font, space_font, space_type_scope
from plugins.money_radar.news_rows import ticker_colors


@pytest.mark.parametrize("value", ["太阳风 | WIND", "磁场 Bz -0.04 nT", "48H PEAK | Kp 6.3 / G2"])
def test_mixed_type_keeps_complete_content_and_shared_baseline(value):
    draw = MixedDraw(ImageDraw.Draw(Image.new("RGB", (500, 80))), numbers=True)
    selected = space_font("public_semibold", 13)
    runs = draw.runs(value, selected)
    assert "".join(text for text, _face, _shift in runs) == value
    assert len({face.getmetrics()[0] + shift for _text, face, shift in runs}) == 1
    assert draw.textlength(value, font=selected) == sum(
        draw.draw.textlength(text, font=face) for text, face, _shift in runs
    )
    for text, face, _shift in runs:
        # A selected Latin face never has to invent Chinese glyphs.
        if any("\u4e00" <= char <= "\u9fff" for char in text):
            assert "PublicSans" not in str(face.path)


def test_space_font_scope_unwinds_after_nested_exception():
    assert scoped_space_font(16) is None
    with space_type_scope():
        expected = scoped_space_font(16)
        with pytest.raises(ValueError), space_type_scope():
            assert scoped_space_font(16).path == expected.path
            raise ValueError("render failed")
        assert scoped_space_font(16).path == expected.path
    assert scoped_space_font(16) is None


@pytest.mark.parametrize("symbol", ["AAPL", "NVDA", "TSLA", "GOOGL", "SPCX", "NTDOY", "VGIT", "VXUS", "SPY", "UNKNOWN"])
def test_brand_ticker_foreground_is_readable(symbol):
    _primary, _fill, foreground, ratio = ticker_colors(symbol)
    assert foreground in {(0, 0, 0), (255, 255, 255)}
    assert ratio >= 4.5
