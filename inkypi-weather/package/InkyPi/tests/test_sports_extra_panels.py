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
    centred = plugin._draw_centered_in_box
    monkeypatch.setattr(plugin, "_draw_centered_in_box", lambda draw, box, text, *args, **kwargs: drawn.append(text) or
                        centred(draw, box, text, *args, **kwargs))
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
    ("MOTOGP", "_draw_motogp_panel", {"卡塔尔大奖赛", "冲刺赛", "正赛成绩 · 马来西亚大奖赛", "车手积分榜", "399",
                                       "MARQUEZ", "ACOSTA", "+0.865"}),
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


def test_tournaments_are_named_by_their_official_marks():
    paris = {"tour": "ATP", "name": "Rolex Paris Masters"}
    assert (tennis.tournament_category(paris), tennis.tournament_mark(paris)) == ("1000", "tennis_atp_1000")
    assert tennis.tournament_mark({"tour": "ATP", "name": "Swiss Indoors Basel"}) == "tennis_atp_500"
    assert tennis.tournament_mark({"tour": "ATP", "name": "Moselle Open"}) == "tennis_atp_250"
    assert tennis.tournament_mark({"tour": "WTA", "name": "Australian Open"}) == "tennis_australian_open"
    # A WTA event carries the WTA's own tier tag; Wimbledon its crest, the ATP Finals their logo.
    hong_kong = {"tour": "WTA", "name": "Prudential Hong Kong Tennis Open"}
    assert (tennis.tournament_category(hong_kong), tennis.tournament_mark(hong_kong)) == ("250", "tennis_wta_250")
    assert tennis.tournament_mark({"tour": "WTA", "name": "Wuhan Open"}) == "tennis_wta_1000"
    assert tennis.tournament_mark({"tour": "WTA", "name": "Wimbledon"}) == "tennis_wimbledon"
    assert tennis.tournament_mark({"tour": "ATP", "name": "Nitto ATP Finals"}) == "tennis_atp_finals"
    assert tennis.tournament_mark({"tour": "WTA", "name": "WTA Finals Riyadh"}) is None
    from plugins.sports_dashboard.common import LOCAL_TEAM_LOGO_DIR

    for key in ("tennis_atp_1000", "tennis_atp_500", "tennis_atp_250", "tennis_australian_open", "tennis_roland_garros",
                "tennis_us_open", "tennis_wimbledon", "tennis_atp_finals", "tennis_wta_1000", "tennis_wta_500",
                "tennis_wta_250", "motogp_ducati", "motogp_ktm", "motogp_aprilia", "motogp_honda", "motogp_yamaha"):
        assert (Path(LOCAL_TEAM_LOGO_DIR) / f"{key}.png").is_file(), key


def test_each_rider_carries_his_manufacturers_mark():
    from plugins.sports_dashboard.motogp_render import maker_mark

    assert [maker_mark(name) for name in ("Ducati", "KTM", "Aprilia", "Honda", "Yamaha", "Unknown")] == [
        "motogp_ducati", "motogp_ktm", "motogp_aprilia", "motogp_honda", "motogp_yamaha", None]


def test_at_night_only_dark_neutral_strokes_take_the_light_ink():
    from plugins.sports_dashboard.icon_marks import night_inked

    mark = Image.new("RGBA", (3, 1))
    mark.putpixel((0, 0), (10, 10, 10, 255))     # black lettering
    mark.putpixel((1, 0), (230, 0, 18, 255))     # a brand red (Aprilia)
    mark.putpixel((2, 0), (20, 30, 90, 255))     # ATP navy
    token = _ACTIVE_COLORS.set(DEEP_NIGHT_COLORS)
    try:
        inked = night_inked(mark)
    finally:
        _ACTIVE_COLORS.reset(token)
    light = (*DEEP_NIGHT_COLORS["text"], 255)
    assert inked.getpixel((0, 0)) == light and inked.getpixel((2, 0)) == light
    assert inked.getpixel((1, 0)) == (230, 0, 18, 255)
    assert night_inked(mark) is mark  # by day the mark is drawn as printed


def test_an_upcoming_tennis_match_shows_both_players_headshots(monkeypatch):
    plugin = SportsDashboard({"id": "sports_dashboard"})
    asked = []
    monkeypatch.setattr(plugin, "_load_team_logo_for_render", lambda url, size: asked.append(url) or
                        Image.new("RGBA", (size, size * 3 // 4), (90, 60, 40, 255)))
    monkeypatch.setattr(plugin, "_load_icon_flag", lambda url, size: Image.new("RGBA", size, (0, 80, 200, 255)))
    event = tennis.parse_scoreboard(feeds.ATP, "ATP")["tournaments"][0]
    upcoming = next(match for match in event["matches"] if match["state"] == "pre")
    played = next(match for match in event["matches"] if match["state"] == "post")
    image = Image.new("RGB", (270, 70), DAY_COLORS["paper"])

    plugin._draw_tennis_match(image, ImageDraw.Draw(image), (0, 0, 260, 58), upcoming, feeds.NOW, DAY_COLORS["blue"])
    assert asked == [player["headshot"] for player in upcoming["players"]]
    assert asked[0] == "https://a.espncdn.com/i/headshots/tennis/players/full/7.png"
    # The pictures stand at the block's right edge, where a played match keeps its set scores.
    assert (90, 60, 40) in set(image.crop((170, 16, 258, 58)).get_flattened_data())
    asked.clear()
    plugin._draw_tennis_match(image, ImageDraw.Draw(image), (0, 0, 260, 58), played, feeds.NOW, DAY_COLORS["blue"])
    assert asked == []


def test_a_player_espn_has_no_photo_of_shows_the_tours_own(monkeypatch):
    plugin = SportsDashboard({"id": "sports_dashboard"})
    asked = []

    def load(url, size):
        asked.append(url)
        return None if "espncdn" in url else Image.new("RGBA", (size, size), (200, 40, 160, 255))

    monkeypatch.setattr(plugin, "_load_team_logo_for_render", load)
    monkeypatch.setattr(plugin, "_load_icon_flag", lambda url, size: Image.new("RGBA", size, (0, 80, 200, 255)))
    event = tennis.parse_scoreboard(feeds.ATP, "ATP")["tournaments"][0]
    upcoming = next(match for match in event["matches"] if match["state"] == "pre")
    upcoming["players"][0]["photo"] = "https://photoresources.wtatennis.com/photo-resources/x/Eala-Torso_330332.png?width=480"
    image = Image.new("RGB", (270, 70), DAY_COLORS["paper"])

    plugin._draw_tennis_match(image, ImageDraw.Draw(image), (0, 0, 260, 58), upcoming, feeds.NOW, DAY_COLORS["blue"])

    assert asked[:2] == [upcoming["players"][0]["headshot"], upcoming["players"][0]["photo"]]
    assert (200, 40, 160) in set(image.crop((170, 16, 258, 58)).get_flattened_data())
