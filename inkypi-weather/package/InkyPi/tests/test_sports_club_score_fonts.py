"""Private league faces follow provider identifiers through actual football rows."""

from datetime import datetime, timedelta, timezone
from pathlib import Path
import shutil

from PIL import Image, ImageDraw
import pytest

from plugins.sports_dashboard import score_fonts
from plugins.sports_dashboard.club_football import CLUB_FOOTBALL_LEAGUES
from plugins.sports_dashboard.render_fonts import render_font_scope
from plugins.sports_dashboard.sports_dashboard import SportsDashboard


LEAGUES = [("MLS", "mls"), ("PL", "epl"), ("PD", "laliga"),
           ("BL1", "bundesliga"), ("SA", "seriea"), ("FL1", "ligue1")]


@pytest.fixture
def private_fonts(monkeypatch, tmp_path):
    monkeypatch.setenv("INKYPI_DATA_DIR", str(tmp_path))
    fonts = tmp_path / "fonts"
    fonts.mkdir()
    for _code, key in LEAGUES:
        # Use the redistributable fixture bytes; real private font files stay outside Git.
        shutil.copyfile(score_fonts.FONT_DIR / "BarlowCondensed-SemiBold.ttf", fonts / f"sports-{key}-score.ttf")
    return fonts


def test_football_font_keys_cover_current_registry_and_only_use_known_ids():
    assert set(dict(LEAGUES)) == set(CLUB_FOOTBALL_LEAGUES)
    assert score_fonts.CLUB_PRIVATE_FONT_KEYS == dict(LEAGUES)
    assert score_fonts.football_score_league({"league_code": "MLS"}, {"competition": "club", "league_code": "PL"}) == "MLS"
    assert score_fonts.football_score_league({}, {"competition": "club", "league_code": "PL"}) == "PL"
    assert score_fonts.football_score_league({"league_code": "CSL"}, {"competition": "club", "league_code": "PL"}) is None
    assert score_fonts.football_score_league({"league_name": "MLS", "team_a": "Premier League"}) is None
    assert score_fonts.football_score_league({}, {"competition": "worldcup", "league_code": "PL"}) is None


@pytest.mark.parametrize("code,key", LEAGUES)
def test_each_league_override_fits_three_digits_and_small_results(private_fonts, code, key):
    draw = ImageDraw.Draw(Image.new("RGB", (100, 40)))
    with render_font_scope():
        for value, width, height, size in [("128-199", 92, 26, 24), ("100-88", 48, 11, 10), ("-", 31, 12, 10)]:
            text, font = score_fonts.fit_score(draw, value, width, size, True,
                                              sport="SOCCER", league=code, max_height=height)
            assert text == value
            assert Path(font.path) == private_fonts / f"sports-{key}-score.ttf"
            image = Image.new("RGB", (100, 40))
            SportsDashboard._draw_centered_in_box(ImageDraw.Draw(image), (4, 4, 4 + width, 4 + height),
                                                 text, font, "white")
            left, top, right, bottom = image.getbbox()
            assert 4 <= left < right <= 4 + width
            assert 4 <= top < bottom <= 4 + height


@pytest.mark.parametrize("code,key", LEAGUES)
def test_each_league_missing_invalid_and_conflicting_files_use_open_fallback(private_fonts, code, key):
    path = private_fonts / f"sports-{key}-score.ttf"
    path.unlink()
    assert Path(score_fonts.score_font("SOCCER", 20, True, league=code).path).name == "BarlowSemiCondensed-Bold.ttf"
    path.write_bytes(b"invalid font")
    assert Path(score_fonts.score_font("SOCCER", 20, True, league=code).path).name == "BarlowSemiCondensed-Bold.ttf"
    shutil.copyfile(score_fonts.FONT_DIR / "BarlowCondensed-SemiBold.ttf", path)
    shutil.copyfile(path, path.with_suffix(".woff"))
    assert Path(score_fonts.score_font("SOCCER", 20, True, league=code).path).name == "BarlowSemiCondensed-Bold.ttf"
    assert Path(score_fonts.score_font("SOCCER", 20, True).path).name == "BarlowSemiCondensed-Bold.ttf"
    assert Path(score_fonts.score_font("NBA", 20, True, league=code).path).name == "BarlowCondensed-Bold.ttf"


def test_league_rotation_keeps_each_private_font_distinct_within_one_render(private_fonts):
    with render_font_scope():
        fonts = [score_fonts.score_font("SOCCER", 14, True, league=code) for code, _key in LEAGUES]
        assert [Path(font.path) for font in fonts] == [private_fonts / f"sports-{key}-score.ttf" for _code, key in LEAGUES]
        assert score_fonts.score_font("SOCCER", 14, True, league="MLS") is fonts[0]


@pytest.mark.parametrize("code,key", LEAGUES)
def test_real_adapter_routes_main_recent_and_lineup_scores_to_event_league(monkeypatch, private_fonts, code, key):
    plugin = SportsDashboard({"id": "sports_dashboard"})
    now = datetime(2026, 10, 27, 5, tzinfo=timezone.utc)
    records = []
    for index, scores in enumerate(((128, 199), (2, 1))):
        records.append({
            "event_id": f"{code}-{index}", "league_code": code,
            "status": "FINAL", "start_utc": now - timedelta(hours=2 + index),
            "home_name": "Home", "away_name": "Away",
            "home_score": scores[0], "away_score": scores[1], "provider": "ESPN",
        })
    by_league = {code: records}
    selected = plugin._select_club_football_timeline_events(by_league, (code,), now, 0, source_state="CLUB LIVE")
    sections = plugin._build_club_football_event_sections(by_league, selected, (code,), now, timezone.utc,
                                                        source_state="CLUB LIVE")
    seen = []
    fit = plugin._fit_score

    def record_fit(draw, text, *args, **kwargs):
        fitted, font = fit(draw, text, *args, **kwargs)
        seen.append((fitted, kwargs.get("league"), Path(font.path)))
        return fitted, font

    monkeypatch.setattr(plugin, "_fit_score", record_fit)
    monkeypatch.setattr(plugin, "_load_team_logo", lambda *_args, **_kwargs: None)
    panel = plugin._render_club_football_panel((536, 240), sections, "CLUB LIVE", now.isoformat(), now)
    assert panel.size == (536, 240)
    expected = private_fonts / f"sports-{key}-score.ttf"
    assert ("128-199", code, expected) in seen
    assert ("2-1", code, expected) in seen
    image = Image.new("RGB", (270, 50))
    plugin._draw_worldcup_row_lineup(image, ImageDraw.Draw(image), 0, 269, 12,
                                    sections["main"], "128-199", presentation=sections["presentation"])
    assert seen[-1] == ("128-199", code, expected)


def test_f1_private_font_is_optional_and_preserves_timing_text(monkeypatch, tmp_path):
    monkeypatch.setenv("INKYPI_DATA_DIR", str(tmp_path))
    original = SportsDashboard._font(20, True)
    assert Path(score_fonts.score_font("F1", 20, True).path) == Path(original.path)
    fonts = tmp_path / "fonts"
    fonts.mkdir()
    path = fonts / "sports-f1-score.ttf"
    shutil.copyfile(score_fonts.FONT_DIR / "BarlowSemiCondensed-Bold.ttf", path)
    text, font = score_fonts.fit_score(ImageDraw.Draw(Image.new("RGB", (100, 30))), "+1:23.456", 80, 20,
                                      True, sport="F1", max_height=22)
    assert text == "+1:23.456"
    assert Path(font.path) == path
    assert font.covers(text)
