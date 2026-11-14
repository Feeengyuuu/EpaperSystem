"""Sport text remains local, readable in both scripts, and owned by its render."""

import gc
from pathlib import Path
import shutil
import weakref

from PIL import Image, ImageDraw
import pytest

from plugins.sports_dashboard.esports_fonts import type_scope
from plugins.sports_dashboard.render_fonts import render_font_scope
from plugins.sports_dashboard.sport_type import active_sport_font, sport_panel, typography_scope
from plugins.sports_dashboard.sports_dashboard import SportsDashboard


FONT_DIR = Path(__file__).resolve().parents[1] / "src/plugins/sports_dashboard/assets/fonts"


def test_body_faces_keep_sports_distinct_and_cover_chinese():
    plugin = SportsDashboard({"id": "sports_dashboard"})
    outside = plugin._font(11, True).path
    with typography_scope("NBA"):
        basketball = plugin._font(11, True)
        assert Path(basketball.path).name == "Inter-Bold.ttf"
        assert basketball.covers("Luka Dončić 湖人 112–108")
        with typography_scope("NFL"):
            football = plugin._font(11, True)
            assert Path(football.path).name == "BarlowSemiCondensed-Bold.ttf"
            assert football.covers("海鹰 1st & 10 25 YDS")
        assert plugin._font(11, True).path == basketball.path
    assert active_sport_font(11, True) is None and plugin._font(11, True).path == outside


def test_brand_face_is_for_large_labels_and_never_replaces_cjk(tmp_path, monkeypatch):
    fonts = tmp_path / "fonts"
    fonts.mkdir()
    installed = fonts / "sports-nba-score.ttf"
    shutil.copyfile(FONT_DIR / "BarlowCondensed-Bold.ttf", installed)
    monkeypatch.setenv("INKYPI_DATA_DIR", str(tmp_path))
    with typography_scope("NBA"):
        small, large = active_sport_font(9, True), active_sport_font(20, True)
        assert Path(small.path).name == "Inter-Bold.ttf"
        assert Path(large.path) == installed
        assert large.covers("NBA 湖人 108")
        image = Image.new("L", (160, 40))
        ImageDraw.Draw(image).text((0, 0), "NBA 湖人 108", font=large, fill=255)
        assert image.getbbox() is not None


def test_mixed_wall_resolves_each_game_and_restores_after_an_exception():
    @sport_panel(game="game")
    def draw(game, fail=False):
        font = active_sport_font(11, True)
        if fail:
            raise RuntimeError("draw failed")
        return Path(font.path).name

    with typography_scope("PGA"):
        before = active_sport_font(11, True).path
        assert draw({"sport": "NFL"}) == "BarlowSemiCondensed-Bold.ttf"
        assert draw({"sport": "NBA"}) == "Inter-Bold.ttf"
        with pytest.raises(RuntimeError):
            draw({"sport": "NHL"}, fail=True)
        assert active_sport_font(11, True).path == before
    assert active_sport_font(11, True) is None


def test_real_panel_exit_and_esports_take_precedence(monkeypatch):
    plugin = SportsDashboard({"id": "sports_dashboard"})
    outside = plugin._font(11, True).path

    def broken_header(*args):
        assert Path(plugin._font(11, True).path).name == "Inter-Bold.ttf"
        raise RuntimeError("header failed")

    monkeypatch.setattr(plugin, "_draw_nba_compact_header", broken_header)
    image = Image.new("RGB", (556, 268))
    with pytest.raises(RuntimeError):
        plugin._draw_nba_compact_panel(image, ImageDraw.Draw(image), (0, 0, 555, 267), {}, "", None)
    assert plugin._font(11, True).path == outside
    monkeypatch.delenv("INKYPI_DATA_DIR", raising=False)
    with typography_scope("NFL"), type_scope("riot"):
        # No private Riot face: the established esports UI fallback wins over
        # the NFL body face instead of inheriting the outer sport's identity.
        assert plugin._font(11, True).path == outside


def test_league_comes_from_competition_identity(tmp_path, monkeypatch):
    fonts = tmp_path / "fonts"
    fonts.mkdir()
    target = fonts / "sports-epl-score.ttf"
    shutil.copyfile(FONT_DIR / "BarlowCondensed-Bold.ttf", target)
    monkeypatch.setenv("INKYPI_DATA_DIR", str(tmp_path))

    @sport_panel("SOCCER", selection="selected")
    def draw(selected):
        return active_sport_font(20, True).path

    assert Path(draw({"presentation": {"competition": "club", "league_code": "PL"}})) == target
    assert Path(draw({"main": {"team_a": "Premier League"}})) != target


def test_text_scope_does_not_keep_font_objects_alive():
    with render_font_scope(), typography_scope("NFL"):
        font = active_sport_font(11, True)
        refs = [weakref.ref(font), weakref.ref(font.primary), weakref.ref(font.fallback)]
        del font
        gc.collect()
        assert all(ref() is None for ref in refs)
