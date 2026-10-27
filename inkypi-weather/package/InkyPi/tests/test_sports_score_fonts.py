"""Scores retain all digits, small-slot bounds and per-render font ownership."""

import gc
from pathlib import Path
import shutil
import weakref

from PIL import Image, ImageDraw
import pytest

from plugins.sports_dashboard import score_fonts
from plugins.sports_dashboard.render_fonts import render_font_scope
from plugins.sports_dashboard.sports_dashboard import SportsDashboard


@pytest.fixture(autouse=True)
def isolated_owner_fonts(monkeypatch, tmp_path):
    monkeypatch.setenv("INKYPI_DATA_DIR", str(tmp_path))


@pytest.mark.parametrize("sport", ["NBA", "WNBA", "MLB", "NFL", "SOCCER", "PGA"])
@pytest.mark.parametrize("value,width,height,size", [
    ("0", 63, 32, 30), ("88", 63, 32, 30), ("100", 63, 32, 30),
    ("128", 63, 32, 30), ("199", 63, 32, 30), ("-", 63, 32, 30),
    ("199-128", 40, 11, 9), ("100", 20, 12, 11), ("-12", 30, 14, 15),
    ("+3", 30, 14, 15), ("E", 30, 14, 15), ("—", 63, 32, 30),
])
def test_score_uses_complete_digits_that_fit_ink_box(sport, value, width, height, size):
    image = Image.new("RGB", (100, 50))
    draw = ImageDraw.Draw(image)
    text, font = score_fonts.fit_score(draw, value, width, size, True, sport=sport, max_height=height)
    assert text == value
    family = "BarlowCondensed" if sport in {"NBA", "WNBA"} else "BarlowSemiCondensed"
    assert Path(font.path).name == f"{family}-Bold.ttf"
    assert font.covers(font.safe_text(value))
    SportsDashboard._draw_text_in_box(draw, (4, 4, 4 + width, 4 + height), text, font, (255, 255, 255))
    left, top, right, bottom = image.getbbox()
    assert 4 <= left < right <= 4 + width
    assert 4 <= top < bottom <= 4 + height


def test_nba_wnba_overrides_do_not_leak_to_mlb_and_bad_files_fall_back(tmp_path):
    fonts = tmp_path / "fonts"
    fonts.mkdir(parents=True)
    source = score_fonts.FONT_DIR / "BarlowCondensed-SemiBold.ttf"
    shutil.copyfile(source, fonts / "sports-nba-score.ttf")
    shutil.copyfile(source, fonts / "sports-mlb-score.ttf")
    (fonts / "sports-wnba-score.ttf").write_bytes(b"invalid optional font")
    assert Path(score_fonts.score_font("NBA", 20, True).path) == fonts / "sports-nba-score.ttf"
    assert Path(score_fonts.score_font("WNBA", 20, True).path).name == "BarlowCondensed-Bold.ttf"
    assert Path(score_fonts.score_font("MLB", 20, True).path).name == "BarlowSemiCondensed-Bold.ttf"
    shutil.copyfile(source, fonts / "sports-nba-score.otf")
    assert Path(score_fonts.score_font("NBA", 20, True).path).name == "BarlowCondensed-Bold.ttf"


def test_missing_bundled_fonts_and_unsupported_sports_keep_base_font(monkeypatch, tmp_path):
    original = SportsDashboard._font(12, True)
    assert Path(score_fonts.score_font("CS2", 12, True).path) == Path(original.path)
    monkeypatch.setattr(score_fonts, "FONT_DIR", tmp_path / "missing")
    assert Path(score_fonts.score_font("NBA", 12, True).path) == Path(original.path)
    assert Path(SportsDashboard._font(12, True).path) == Path(original.path)


def test_score_fonts_share_only_while_owned_by_current_render():
    with render_font_scope():
        font = score_fonts.score_font("NBA", 12, True)
        assert score_fonts.score_font("NBA", 12, True) is font
        reference = weakref.ref(font)
        with render_font_scope():
            assert score_fonts.score_font("NBA", 12, True) is not font
        del font
        gc.collect()
        assert reference() is None


def test_small_score_ink_height_matches_existing_digit_scale():
    for size in (9, 11, 30):
        old = SportsDashboard._font(size, True).getbbox("88")
        new = score_fonts.score_font("NBA", size, True).getbbox("88")
        assert abs((new[3] - new[1]) - (old[3] - old[1])) <= 1


def test_wide_score_can_shrink_below_preferred_minimum_without_losing_digits():
    draw = ImageDraw.Draw(Image.new("RGB", (80, 30)))
    text, font = score_fonts.fit_score(draw, "1999", 20, 30, True, min_size=16, sport="NBA", max_height=20)
    assert text == "1999"
    bounds = draw.textbbox((0, 0), text, font=font)
    assert bounds[2] - bounds[0] <= 20


def test_missing_original_symbol_uses_ui_font_without_normalizing_text(monkeypatch):
    class DigitsOnlyFont:
        def covers(self, text):
            return text.isascii()

        def safe_text(self, text):
            pytest.fail("Coverage must check the original symbol before any substitution")

    monkeypatch.setattr(score_fonts, "score_font", lambda *_args, **_kwargs: DigitsOnlyFont())
    draw = ImageDraw.Draw(Image.new("RGB", (120, 30)))
    text, font = score_fonts.fit_score(draw, "−12", 100, 20, True, sport="F1", max_height=24)
    assert text == "−12"
    assert Path(font.path) == Path(SportsDashboard._font(20, True).path)
    assert font.covers(text)
