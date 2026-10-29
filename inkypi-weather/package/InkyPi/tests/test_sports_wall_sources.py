"""The live wall credits its score feeds without obscuring scores or league counts."""

import hashlib
import json
from pathlib import Path

from PIL import Image, ImageDraw
import pytest

from plugins.sports_dashboard.common import _ACTIVE_COLORS, DAY_COLORS, DEEP_NIGHT_COLORS
from plugins.sports_dashboard.lower_wall_render import WALL_SOURCE_LOGO_DIR
from plugins.sports_dashboard.sports_dashboard import SportsDashboard
from tests.test_lower_panels import _render, _wall_games
from tests.test_sports_dashboard import _plugin


@pytest.mark.parametrize(
    "sports,settings,expected",
    [
        (["MLB", "MLB"], {}, ["MLB"]),
        (["NFL", "NBA", "WNBA", "NCAA", "PGA"], {}, ["ESPN"]),
        (["MLB", "NFL", "NBA"], {}, ["MLB", "ESPN"]),
        (["NHL", "MLB"], {}, ["ESPN", "MLB"]),
        (["NHL"], {"nhlScoreboardUrl": "https://scores.example.test/scoreboard"}, ["自定义"]),
        (["NBA"], {"nbaScoreboardUrl": "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard"}, ["ESPN"]),
        (["NBA", "MLB"], {"nbaScoreboardUrl": "https://scores.example.test/scoreboard"}, ["自定义", "MLB"]),
        (["NBA"], {"nbaScoreboardUrl": "https://site.web.api.espn.com.example.test/scoreboard"}, ["自定义"]),
        (["MLB"], {"mlbScoreboardUrl": "https://[invalid/scoreboard"}, ["自定义"]),
        (["NBA"], {"nbaScoreboardUrl": "https://[invalid/scoreboard"}, ["自定义"]),
        (["CS2", "LPL"], {}, []),
        ([], {}, []),
    ],
)
def test_live_wall_credits_only_its_actual_score_providers(sports, settings, expected):
    assert SportsDashboard._wall_score_sources([{"sport": sport} for sport in sports], settings) == expected


@pytest.mark.parametrize("colors", [DAY_COLORS, DEEP_NIGHT_COLORS], ids=["day", "night"])
def test_source_logos_are_transparent_and_leave_the_rest_of_the_header_untouched(colors):
    plugin = _plugin()
    image = Image.new("RGB", (100, 30), colors["panel"])
    draw = ImageDraw.Draw(image)
    games = [{"sport": "MLB"}, {"sport": "NBA"}]
    token = _ACTIVE_COLORS.set(colors)
    try:
        right = plugin._draw_wall_sources(image, draw, (4, 9, 98, 20), games)
    finally:
        _ACTIVE_COLORS.reset(token)
    assert 60 <= right - 4 <= 80
    assert image.crop((0, 0, 100, 9)).getextrema() == Image.new("RGB", (100, 9), colors["panel"]).getextrema()
    assert image.crop((0, 20, 100, 30)).getextrema() == Image.new("RGB", (100, 10), colors["panel"]).getextrema()
    assert image.getpixel((4, 9)) == colors["panel"]
    assert len(set(image.crop((4, 9, right, 20)).get_flattened_data())) > 5


def test_source_credit_keeps_league_counts_on_a_crowded_wall(monkeypatch):
    games, now = _wall_games(16)
    observed = []
    original = SportsDashboard._draw_wall_sources

    def capture(self, image, draw, box, games, settings=None):
        right = original(self, image, draw, box, games, settings)
        observed.append((box, right))
        return right

    monkeypatch.setattr(SportsDashboard, "_draw_wall_sources", capture)
    _render(lambda plugin, image, draw: plugin._draw_lower_live_wall(image, draw, (0, 0, 555, 267), games, now))
    box, right = observed[0]
    assert right > box[0]
    assert right <= 543
    assert box[1] >= 8 + 2 + 26  # Below the large league logos, even when the row is crowded.
    assert box[3] < 48  # Above the divider and score cards.


def test_missing_logo_falls_back_to_the_source_name_without_network(monkeypatch):
    plugin = _plugin()
    labels = []
    monkeypatch.setattr(plugin, "_load_local_logo", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(plugin, "_draw_text_in_box", lambda _draw, _box, text, *_args, **_kwargs: labels.append(text))
    image = Image.new("RGB", (100, 20))
    plugin._draw_wall_sources(image, ImageDraw.Draw(image), (0, 0, 100, 11), [{"sport": "MLB"}, {"sport": "NBA"}])
    assert labels == ["来源", "MLB", "ESPN"]


def test_source_assets_keep_official_provenance_and_alpha():
    folder = Path(WALL_SOURCE_LOGO_DIR)
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))["logos"]
    for name, host in (("mlb", "https://www.mlbstatic.com/"), ("espn", "https://a.espncdn.com/")):
        entry = manifest[name]
        path = folder / f"{name}.png"
        assert entry["source_url"].startswith(host)
        assert hashlib.sha256(path.read_bytes()).hexdigest() == entry["sha256"]
        with Image.open(path) as logo:
            assert logo.mode == "RGBA"
            assert logo.getchannel("A").getextrema() == (0, 255)


def test_a_live_wall_with_an_nhl_game_draws_instead_of_falling_back():
    games, now = _wall_games(3)
    games[1] = {**games[1], "sport": "NHL"}

    image, _draw = _render(lambda plugin, image, draw: plugin._draw_lower_live_wall(image, draw, (0, 0, 555, 267),
                                                                                    games, now))

    assert image.size[0] >= 555
