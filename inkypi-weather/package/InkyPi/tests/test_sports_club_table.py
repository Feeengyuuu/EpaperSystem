"""Each club's place and points on the club football panel: ESPN's key-free league table, asked
for once an hour for the league on the page, shown round the last-five cells on the card and at
the ends of the line under the names in the rows."""

from datetime import datetime, timedelta, timezone

import pytest

from plugins.sports_dashboard import club_table
from plugins.sports_dashboard.common import DAY_COLORS, DEEP_NIGHT_COLORS
from plugins.sports_dashboard.sports_dashboard import SportsDashboard

from tests.test_sports_scoreboard_extras import LA, club_payload, render

NOW = datetime(2026, 11, 11, 16, tzinfo=timezone.utc)
PRESENTATION = {"competition": "club", "team_asset_kind": "logo", "main_team_logo_scale": 1.5, "main_team_logo_top": 42}


def standings(*groups):
    def entry(team_id, name, rank, points, played):
        return {"team": {"id": team_id, "displayName": name},
                "stats": [{"name": "rank", "value": float(rank), "displayValue": str(rank)},
                          {"name": "points", "value": float(points), "displayValue": str(points)},
                          {"name": "gamesPlayed", "value": float(played), "displayValue": str(played)}]}

    return {"children": [{"name": f"Table {index}", "standings": {"entries": [entry(*row) for row in group]}}
                         for index, group in enumerate(groups)]}


BUNDESLIGA = standings([("125", "Eintracht Frankfurt", 7, 14, 9), ("127", "Hamburg SV", 13, 9, 9)])


def plugin_with_table(monkeypatch, tmp_path, replies):
    """A plugin whose cache is ``tmp_path`` and whose ESPN table replies come from ``replies[0]``
    (None fails the request); returns it and the list of URLs asked."""
    plugin = SportsDashboard({"id": "sports_dashboard"})
    plugin._sports_dashboard_cache_dir = lambda: tmp_path
    asked = []

    def fetch(_session, url):
        asked.append(url)
        if replies[0] is None:
            raise OSError("ESPN is down")
        return replies[0]

    monkeypatch.setattr(club_table, "fetch_table", fetch)
    return plugin, asked


def test_a_league_table_gives_each_club_its_place_points_and_games():
    assert club_table.parse_table(BUNDESLIGA) == {
        "125": {"rank": 7, "points": 14, "played": 9, "name": "Eintracht Frankfurt"},
        "127": {"rank": 13, "points": 9, "played": 9, "name": "Hamburg SV"},
    }


def test_a_league_split_into_conferences_has_points_but_no_single_place():
    table = club_table.parse_table(standings([("1", "East One", 1, 60, 34)], [("2", "West One", 1, 55, 34)]))

    assert table["1"] == {"rank": None, "points": 60, "played": 34, "name": "East One"}
    assert table["2"]["rank"] is None and table["2"]["points"] == 55


@pytest.mark.parametrize("payload", [{}, {"children": []}, {"children": [{"standings": {"entries": []}}]},
                                     standings([("125", "Only One", 1, 3, 1)])])
def test_a_reply_that_is_not_a_table_is_refused(payload):
    with pytest.raises(ValueError):
        club_table.parse_table(payload)


def test_a_club_is_found_by_its_espn_id_or_else_by_its_name():
    table = club_table.parse_table(BUNDESLIGA)

    assert club_table.team_row(table, "127", "whatever")["points"] == 9
    # Another provider's id: the name decides.
    assert club_table.team_row(table, "9001", "hamburg sv")["rank"] == 13
    assert club_table.team_row(table, "9001", "Hamburger Nobody") is None


def test_the_table_is_asked_for_once_an_hour_and_kept_while_espn_is_down(monkeypatch, tmp_path):
    replies = [BUNDESLIGA]
    plugin, asked = plugin_with_table(monkeypatch, tmp_path, replies)

    first = plugin._load_club_table("BL1", {}, NOW)
    assert first["125"]["points"] == 14
    assert asked == ["https://site.web.api.espn.com/apis/v2/sports/soccer/ger.1/standings"]
    assert plugin._load_club_table("BL1", {}, NOW + timedelta(minutes=59)) == first and len(asked) == 1

    # Past the hour with ESPN down: the kept table, and no second try for a quarter of an hour.
    replies[0] = None
    assert plugin._load_club_table("BL1", {}, NOW + timedelta(minutes=61)) == first and len(asked) == 2
    assert plugin._load_club_table("BL1", {}, NOW + timedelta(minutes=70)) == first and len(asked) == 2

    replies[0] = standings([("125", "Eintracht Frankfurt", 5, 17, 10), ("127", "Hamburg SV", 14, 9, 10)])
    later = plugin._load_club_table("BL1", {}, NOW + timedelta(minutes=77))
    assert later["125"] == {"rank": 5, "points": 17, "played": 10, "name": "Eintracht Frankfurt"} and len(asked) == 3

    # A theme-only redraw never asks; a table three days old is no longer shown.
    cache_only = {"_inkypi_ewc_cache_only": "true"}
    assert plugin._load_club_table("BL1", cache_only, NOW + timedelta(days=1))["125"]["points"] == 17
    replies[0] = None
    assert plugin._load_club_table("BL1", {}, NOW + timedelta(days=4)) == {}
    assert len(asked) == 4


def test_the_league_on_the_page_gets_its_table_and_its_matches_carry_both_clubs(monkeypatch, tmp_path):
    plugin, asked = plugin_with_table(monkeypatch, tmp_path, [BUNDESLIGA])
    event = SportsDashboard._parse_club_espn_events("BL1", club_payload("pre"), LA)[0]
    event["league_code"] = "BL1"
    other = {"league_code": "PL", "home_team_id": "125", "away_team_id": "127"}

    plugin._attach_club_table({"BL1": [event], "PL": [other]}, {"focus": event}, {}, NOW)

    adapted = SportsDashboard._club_football_worldcup_event(event, LA)
    assert adapted["table_a"] == {"rank": 7, "points": 14, "played": 9, "name": "Eintracht Frankfurt"}
    assert adapted["table_b"]["rank"] == 13 and adapted["table_b"]["points"] == 9
    # Only the league on the page is asked for and filled in.
    assert len(asked) == 1 and "home_table" not in other


def recording_boxes(plugin, monkeypatch):
    """Every text drawn through the box helpers, with its box: [(text, (left, top, right, bottom))]."""
    drawn = []
    original = plugin._draw_text_in_box
    monkeypatch.setattr(plugin, "_draw_text_in_box", lambda draw, box, text, *args, **kwargs: (
        drawn.append((str(text), tuple(box))), original(draw, box, text, *args, **kwargs)))
    return drawn


def upcoming_with_table(home=None, away=None):
    event = SportsDashboard._parse_club_espn_events("BL1", club_payload("pre"), LA)[0]
    if home:
        event["home_table"] = home
    if away:
        event["away_table"] = away
    return SportsDashboard._club_football_worldcup_event(event, LA)


def test_the_card_puts_a_clubs_place_before_its_last_five_and_its_points_after(monkeypatch):
    plugin = SportsDashboard({"id": "sports_dashboard"})
    drawn = recording_boxes(plugin, monkeypatch)
    event = upcoming_with_table({"rank": 7, "points": 14, "played": 9}, {"rank": None, "points": 9, "played": 9})
    now = datetime(2026, 11, 2, 12, tzinfo=LA)

    pages = {}
    for name, colors in (("day", DAY_COLORS), ("night", DEEP_NIGHT_COLORS)):
        drawn.clear()
        pages[name] = render(lambda image, draw: plugin._draw_worldcup_main_card(
            image, draw, 12, 8, 264, 155, event, now, "next", PRESENTATION), colors, (276, 164)).convert("RGB")
        boxes = dict(drawn)
        # A league in several tables has no single place: the away club shows its points alone.
        assert {"#7", "14分", "9分"} <= set(boxes) and not any(text.startswith("#") and text != "#7" for text in boxes)
    # (Measured on the day page, whose cell colours are these; the boxes are the night render's,
    # laid out alike.)
    image, cells = pages["day"], {(22, 150, 96), (214, 62, 72)}
    rows = [y for y in range(image.height) if sum(image.getpixel((x, y)) in cells for x in range(20, 130)) >= 3]
    columns = [x for x in range(20, 130) if any(image.getpixel((x, y)) in cells for y in rows)]
    rank, points = boxes["#7"], boxes["14分"]
    # Both sit on the cells' row: the place ends before the first cell, the points start after the last.
    assert rank[1] <= min(rows) and rank[3] >= max(rows) and (rank[1], rank[3]) == (points[1], points[3])
    assert rank[2] <= min(columns) - 3 and points[0] >= max(columns) + 3
    assert rank[0] >= 14 and boxes["9分"][2] <= 262


def test_a_card_without_a_table_draws_no_place_or_points(monkeypatch):
    plugin = SportsDashboard({"id": "sports_dashboard"})
    drawn = recording_boxes(plugin, monkeypatch)
    now = datetime(2026, 11, 2, 12, tzinfo=LA)

    render(lambda image, draw: plugin._draw_worldcup_main_card(image, draw, 12, 8, 264, 155, upcoming_with_table(), now, "next",
                                                                 PRESENTATION), DAY_COLORS, (276, 164))

    assert not any(text.endswith("分") or text.startswith("#") for text, _box in drawn)


def test_a_fixture_row_puts_each_clubs_points_at_the_ends_of_the_line_under_the_names(monkeypatch):
    plugin = SportsDashboard({"id": "sports_dashboard"})
    drawn = recording_boxes(plugin, monkeypatch)
    odds_boxes = []
    original = plugin._draw_worldcup_odds_text
    monkeypatch.setattr(plugin, "_draw_worldcup_odds_text", lambda draw, box, text, **kwargs: (
        odds_boxes.append(tuple(box)) if str(text or "").strip() else None, original(draw, box, text, **kwargs)))
    event = upcoming_with_table({"rank": 7, "points": 14, "played": 9}, {"rank": 13, "points": 9, "played": 9})
    event["odds"] = {"team_a": "1.22", "draw": "6.00", "team_b": "7.50"}

    render(lambda image, draw: plugin._draw_worldcup_mini_match_row(image, draw, 280, 548, 20, 34, event, "VS", show_time=True,
                                                                      presentation=PRESENTATION), DAY_COLORS)

    boxes = dict(drawn)
    home, away = boxes["14分"], boxes["9分"]
    names = {text: box for text, box in drawn if text in (event["team_a"], event["team_b"])}
    # Under the names, level with the odds, outside them on both sides.
    assert home[1] >= names[event["team_a"]][1] + 8 and home[1] == away[1]
    assert len(odds_boxes) == 3
    assert home[2] <= min(box[0] for box in odds_boxes) and away[0] >= max(box[2] for box in odds_boxes)
    assert 280 < home[0] < 414 < away[2] < 548
    # The row names no place: the card does.
    assert not any(text.startswith("#") for text in boxes)


def test_a_result_row_puts_each_clubs_points_under_its_name(monkeypatch):
    plugin = SportsDashboard({"id": "sports_dashboard"})
    drawn = recording_boxes(plugin, monkeypatch)
    final = SportsDashboard._parse_club_espn_events("BL1", club_payload("post"), LA)[0]
    final["home_table"], final["away_table"] = {"rank": 7, "points": 14, "played": 9}, {"rank": 13, "points": 9, "played": 9}
    event = SportsDashboard._club_football_worldcup_event(final, LA)

    render(lambda image, draw: plugin._draw_worldcup_recent_match_row(image, draw, 280, 548, 20, 32, event,
                                                                        presentation=PRESENTATION), DAY_COLORS)

    boxes = dict(drawn)
    home, away = boxes["14分"], boxes["9分"]
    name_bottom = boxes[event["team_a"]][3]
    assert home[1] >= name_bottom - 1 and home[1] == away[1] and home[3] <= 52
    assert 280 < home[0] < 400 and 428 < away[2] < 548
