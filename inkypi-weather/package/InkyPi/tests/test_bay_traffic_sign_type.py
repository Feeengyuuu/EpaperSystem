"""Bundled road-sign fonts must retain readable caps, Chinese and exact bounds."""

from pathlib import Path

from PIL import ImageChops
import pytest

from plugins.bay_traffic_cams import render, sign_type
from utils.page_canvas import PageCanvas


@pytest.fixture
def canvas(monkeypatch, tmp_path):
    monkeypatch.setenv("INKYPI_DATA_DIR", str(tmp_path))
    return PageCanvas({"paper": (0, 0, 0), "ink": (255, 255, 255)})


@pytest.mark.parametrize("size", [10, 12])
def test_direction_font_matches_existing_visible_caps_not_nominal_em(canvas, size):
    legend = sign_type.fit_sign(canvas, "WEST", size, 60, size + 2, series="C")
    assert legend.text == "WEST"
    font = legend.runs[0][1]
    assert Path(font.path).name == "RG2014C.ttf"
    assert sign_type._cap_height(font) >= sign_type._cap_height(canvas.font(size))
    assert legend.bounds[3] - legend.bounds[1] <= size + 2


@pytest.mark.parametrize("text", ["Mowry Loop 入口", "Millbrae Ave 以北", "84 号交汇", "Newbridge St · Menlo Park"])
@pytest.mark.parametrize("series", ["EM", "D"])
def test_place_legend_uses_roadgeek_and_retains_every_unsupported_character(canvas, text, series):
    legend = sign_type.fit_sign(canvas, text, 14, 420, 18, series=series)
    assert legend.text == text
    assert "".join(part for part, _font, _x in legend.runs) == text
    assert any(Path(font.path).name == f"RG2014{series}.ttf" for _part, font, _x in legend.runs)
    for part, font, _x in legend.runs:
        assert font.covers(font.safe_text(part))
        if any(ord(char) > 127 for char in part):
            assert Path(font.path).name != f"RG2014{series}.ttf"


@pytest.mark.parametrize("width,height,size", [(157, 19, 12), (225, 21, 14), (44, 19, 12)])
def test_long_mixed_names_fit_the_same_measured_and_drawn_box(canvas, width, height, size):
    text = "San Pablo Dam Road 出口 · Very Long Town Name"
    legend = sign_type.fit_sign(canvas, text, size, width - 10, height - 5, min_size=max(11, size - 2))
    assert legend.text.endswith("…")
    assert legend.width <= width - 10
    assert legend.bounds[3] - legend.bounds[1] <= height - 5
    before = canvas.image.copy()
    legend.draw(canvas, 25, 40, (255, 255, 255))
    bbox = ImageChops.difference(before, canvas.image).getbbox()
    assert bbox is not None
    assert 25 <= bbox[0] < bbox[2] <= 25 + width - 10
    assert 40 - (height - 5) / 2 - 1 <= bbox[1] < bbox[3] <= 40 + (height - 5) / 2 + 1


def test_missing_roadgeek_file_keeps_existing_chinese_font_and_place_text(canvas, monkeypatch):
    monkeypatch.setattr(sign_type, "_sign_font", lambda *_args: None)
    text = "Mowry Loop 入口"
    legend = sign_type.fit_sign(canvas, text, 12, 220, 16)
    assert legend.text == text
    assert all(font is canvas.font(12) for _part, font, _x in legend.runs)


def test_too_small_or_empty_guide_sign_does_not_draw_blank_furniture(canvas):
    before = canvas.image.copy()
    assert render._guide_sign(canvas, "", (20, 20), 19, 157) == 0
    assert render._guide_sign(canvas, "Newbridge St", (20, 20), 19, 20) == 0
    assert ImageChops.difference(before, canvas.image).getbbox() is None
