"""F1 readouts retain precision and fit the existing small race-panel cells."""

from pathlib import Path
import shutil

from PIL import Image, ImageDraw
import pytest

from plugins.sports_dashboard.render_fonts import render_font_scope
from tests.test_sports_dashboard import _plugin


@pytest.mark.parametrize("text,width,height", [
    ("1:23.456", 65, 13), ("+123.456", 65, 13), ("P23", 20, 12),
    ("999", 24, 13), ("SAT 12:30 PM", 60, 13), ("QUALIFYING", 52, 12),
])
def test_private_f1_readouts_keep_all_characters_inside_their_cells(tmp_path, monkeypatch, text, width, height):
    fonts = tmp_path / "fonts"
    fonts.mkdir()
    source = Path(__file__).resolve().parents[1] / "src/plugins/flight_radar/assets/fonts/Inter-Bold.ttf"
    target = fonts / "sports-f1-score.ttf"
    shutil.copyfile(source, target)
    monkeypatch.setenv("INKYPI_DATA_DIR", str(tmp_path))
    draw = ImageDraw.Draw(Image.new("RGB", (200, 40)))
    with render_font_scope():
        actual, font = _plugin()._fit_f1_metric(draw, text, width, 10, True, max_height=height)
        assert actual == text
        assert Path(font.path) == target
        left, top, right, bottom = draw.textbbox((0, 0), actual, font=font)
        assert right - left <= width and bottom - top <= height


def test_f1_chinese_readout_keeps_the_existing_text_renderer():
    plugin = _plugin()
    called = []
    sentinel = object()
    plugin._fit_text = lambda draw, text, *args, **kwargs: (called.append(text) or text, sentinel)
    plugin._fit_score = lambda *args, **kwargs: pytest.fail("Chinese passed to the private Latin face")
    text, font = plugin._fit_f1_metric(None, "2圈", 40, 10)
    assert text == "2圈" and font is sentinel and called == ["2圈"]
