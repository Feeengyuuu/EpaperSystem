"""Semantic framing for reviewed covers and bounded local fallback for new art."""

from PIL import Image, ImageDraw, ImageOps
import pytest

from plugins.steam_profile_dashboard.focal_crop import focused_cover_fit


@pytest.mark.parametrize("size", [(388, 162), (192, 60), (64, 64)])
def test_hero_top_and_square_framing_remain_unchanged(size):
    source = Image.linear_gradient("L").resize((460, 215)).convert("RGB")
    actual, detail = focused_cover_fit(source, size, "3751260")
    expected = ImageOps.fit(source, size, method=Image.Resampling.LANCZOS)
    assert actual.tobytes() == expected.tobytes()
    assert detail == {"mode": "center"}


@pytest.mark.parametrize("appid", ["999000001", "3751260"])
def test_new_game_and_changed_cover_find_offcenter_title_band(appid):
    # The known AppID has different pixels: its old eye anchor must not apply.
    source = Image.new("RGB", (460, 215), "black")
    draw = ImageDraw.Draw(source)
    for x in range(45, 400, 22):
        draw.rectangle((x, 159, x + 10, 174), fill="white")
    original = source.tobytes()
    fitted, detail = focused_cover_fit(source, (595, 28), appid)
    assert detail["mode"] == "detail-focus"
    assert 0.70 < detail["focus_y"] < 0.84
    assert detail["source_box"][1] < 167 < detail["source_box"][3]
    assert fitted.convert("L").getextrema()[1] > 220
    assert source.tobytes() == original
    assert fitted.size == (595, 28)


@pytest.mark.parametrize("size", [(460, 215), (3, 3), (40, 2)])
def test_uniform_or_tiny_sources_fall_back_safely(size):
    source = Image.new("RGB", size, (30, 40, 50))
    fitted, detail = focused_cover_fit(source, (595, 28), "999000002")
    assert fitted.size == (595, 28)
    assert fitted.getpixel((297, 14)) == (30, 40, 50)
    assert detail.get("focus_y", 0.5) == 0.5
