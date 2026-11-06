"""The UFC, tennis and MotoGP lower panels: drawn by day and night, chosen by the ranking, left out
when their feeds fail; and the golf board's places and countries as medal and flag icons."""

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from plugins.sports_dashboard import motogp, tennis, ufc  # noqa: E402
from plugins.sports_dashboard.common import _ACTIVE_COLORS, DAY_COLORS, DEEP_NIGHT_COLORS  # noqa: E402
from plugins.sports_dashboard.sports_dashboard import SportsDashboard  # noqa: E402
from tests import test_sports_extra_feeds as feeds  # noqa: E402

LA = timezone(timedelta(hours=-8))
BOX = (0, 0, 555, 267)


def _cards():
    fight_night = datetime(2026, 11, 14, 22, 30, tzinfo=timezone.utc)
    ufc_card = ufc.build_card(ufc.parse_scoreboard(feeds.UFC)["events"], fight_night)
    tennis_card = tennis.build_card({"ATP": tennis.parse_scoreboard(feeds.ATP, "ATP")}, feeds.NOW)
    moto_data = {"current": motogp.pick_events(feeds.EVENTS, feeds.NOW)["current"],
                 "sessions": motogp.normalize_sessions(feeds.SESSIONS),
                 "result": {"session": "RAC", "event": "MAL", "event_zh": "马来西亚大奖赛",
                            "rows": motogp.normalize_classification(feeds.CLASSIFICATION)},
                 "standings": motogp.normalize_standings(feeds.STANDINGS)}
    return {"UFC": (ufc_card, fight_night), "TENNIS": (tennis_card, feeds.NOW),
            "MOTOGP": (motogp.build_card(moto_data, feeds.NOW), feeds.NOW)}


def _render(plugin, drawer, card, now, colours, monkeypatch, drawn):
    original = plugin._draw_text_in_box
    monkeypatch.setattr(plugin, "_draw_text_in_box", lambda draw, box, text, *args, **kwargs: drawn.append(text) or
                        original(draw, box, text, *args, **kwargs))
    monkeypatch.setattr(plugin, "_load_icon_flag", lambda url, size: Image.new("RGBA", size, (0, 80, 200, 255)))
    monkeypatch.setattr(plugin, "_load_team_logo_for_render", lambda url, size: Image.new("RGBA", (size, size // 2), (90, 60, 40, 255)))
    token = _ACTIVE_COLORS.set(colours)
    try:
        image = Image.new("RGB", (556, 268), colours["paper"])
        getattr(plugin, drawer)(image, ImageDraw.Draw(image), BOX, card, "ESPN LIVE", now.astimezone(LA))
    finally:
        _ACTIVE_COLORS.reset(token)
    return image


@pytest.mark.parametrize("colours", [DAY_COLORS, DEEP_NIGHT_COLORS], ids=["day", "night"])
@pytest.mark.parametrize("key, drawer, expected", [
    ("UFC", "_draw_ufc_panel", {"UFC 334", "Gane vs. Hokit", "Ciryl Gane", "13-3-0"}),
    ("TENNIS", "_draw_tennis_panel", {"巴黎大师赛", "1/4决赛", "Carlos Alcaraz", "进行中"}),
    ("MOTOGP", "_draw_motogp_panel", {"卡塔尔大奖赛", "冲刺赛", "正赛成绩 · 马来西亚大奖赛", "车手积分榜", "399"}),
])
def test_each_extra_panel_draws_its_card_by_day_and_night(monkeypatch, key, drawer, expected, colours):
    plugin = SportsDashboard({"id": "sports_dashboard"})
    card, now = _cards()[key]
    drawn = []
    image = _render(plugin, drawer, card, now, colours, monkeypatch, drawn)

    assert expected <= set(drawn), set(drawn)
    assert image.getbbox() is not None
    # Flags are drawn as icons (the stand-in blue), never as their country codes.
    assert (0, 80, 200) in set(image.convert("RGB").get_flattened_data())


def test_the_ranking_hands_the_panel_to_a_numbered_ufc_card_on_fight_night():
    card, now = _cards()["UFC"]
    extra = ufc.candidate(card, now)
    nba = {"live": [], "upcoming": [{"start": now + timedelta(hours=3), "state": "scheduled"}], "recent": []}

    choice = SportsDashboard._choose_lower_panel({}, nba, None, "", None, now, extras=[extra])
    assert (choice["kind"], choice["key"]) == ("extra", "UFC")
    # Switched off, the extra leagues stay out of the ranking.
    plugin = SportsDashboard({"id": "sports_dashboard"})
    assert plugin._load_lower_extras({"extraSportsPanels": "off"}, now) == ([], {})


def test_a_league_whose_feed_fails_is_left_out(tmp_path, monkeypatch):
    plugin = SportsDashboard({"id": "sports_dashboard"})
    monkeypatch.setattr(plugin, "_sports_dashboard_cache_dir", lambda: tmp_path)

    candidates, states = plugin._load_lower_extras({}, feeds.NOW)

    assert candidates == []
    assert states == {"UFC": "ESPN UFC NO DATA", "TENNIS": "ESPN TENNIS NO DATA", "MOTOGP": "MOTOGP NO DATA"}


def test_the_golf_board_shows_places_as_medals_and_countries_as_flags(monkeypatch):
    plugin = SportsDashboard({"id": "sports_dashboard"})
    medals, flags = [], []
    original_medal, original_flag = plugin._draw_rank_medal, plugin._draw_flag_icon
    monkeypatch.setattr(plugin, "_draw_rank_medal", lambda draw, box, number, tied=False: medals.append((number, tied)) or
                        original_medal(draw, box, number, tied))
    monkeypatch.setattr(plugin, "_draw_flag_icon", lambda image, draw, url, box, fallback="": flags.append((url, fallback)) or
                        original_flag(image, draw, url, box, fallback))
    monkeypatch.setattr(plugin, "_load_icon_flag", lambda url, size: Image.new("RGBA", size, (0, 80, 200, 255)))
    rows = [{"position": 1, "position_label": "P1", "name": "A. Smotherman", "country": "USA", "score": "-16",
             "flag": "https://a.espncdn.com/i/teamlogos/countries/500/usa.png", "rounds": ["-6", "-5"]},
            {"position": 3, "position_label": "T3", "name": "T. Moore", "country": "CAN", "score": "-14", "rounds": ["-6"]}]
    from plugins.sports_dashboard.scorebug_kit import ScorebugSpec

    image = Image.new("RGB", (556, 200), DAY_COLORS["paper"])
    plugin._draw_pga_board(image, ImageDraw.Draw(image), (10, 10, 540, 190), rows, "final",
                           ScorebugSpec("PGA", "pga_accent", "pga_live", "pga_tag", "pga_course_tint"))

    assert medals == [(1, False), (3, True)]
    assert flags == [("https://a.espncdn.com/i/teamlogos/countries/500/usa.png", "USA"),
                     ("https://a.espncdn.com/i/teamlogos/countries/500/can.png", "CAN")]
