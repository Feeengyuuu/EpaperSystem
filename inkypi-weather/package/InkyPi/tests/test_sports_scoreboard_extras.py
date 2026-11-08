"""Scoreboard fields the sports page already downloads and now shows: NBA box score and recap,
club football form / scorers / cards / possession, and an NFL game's forecast and recap."""

from datetime import datetime
from zoneinfo import ZoneInfo

from PIL import Image, ImageDraw
import pytest

from plugins.sports_dashboard import nba_calendar
from plugins.sports_dashboard.common import _ACTIVE_COLORS, COLORS, DAY_COLORS, DEEP_NIGHT_COLORS
from plugins.sports_dashboard.sports_dashboard import SportsDashboard

LA = ZoneInfo("America/Los_Angeles")
STATS = [{"name": "fieldGoalPct", "displayValue": "44.0"}, {"name": "threePointFieldGoalsMade", "displayValue": "12"},
         {"name": "threePointFieldGoalsAttempted", "displayValue": "44"}, {"name": "rebounds", "displayValue": "41"},
         {"name": "assists", "displayValue": "29"}]
RECAP = {"type": "Recap", "shortLinkText": "Tatum's 32 lead Boston to win at Orlando", "description": "long"}


def nba_event(state="post", stats=STATS):
    def competitor(side, team_id, code, score):
        return {"id": team_id, "homeAway": side, "score": score, "statistics": stats,
                "team": {"id": team_id, "abbreviation": code, "displayName": code, "logo": ""}}
    return {"id": "1", "date": "2026-11-02T00:00Z", "status": {"type": {"state": state, "completed": state == "post"}},
            "competitions": [{"id": "1", "date": "2026-11-02T00:00Z", "headlines": [RECAP, {"type": "Preview", "shortLinkText": "x"}],
                              "status": {"type": {"state": state, "completed": state == "post"}},
                              "competitors": [competitor("home", "22", "ORL", "97"), competitor("away", "2", "BOS", "108")]}]}


def render(callback, colors=DAY_COLORS, size=(556, 268)):
    token = _ACTIVE_COLORS.set(colors)
    try:
        image = Image.new("RGB", size, COLORS["paper"])
        callback(image, ImageDraw.Draw(image))
        return image
    finally:
        _ACTIVE_COLORS.reset(token)


def recording(plugin, monkeypatch):
    drawn = []
    for name in ("_draw_text_in_box", "_draw_centered_in_box"):
        original = getattr(plugin, name)
        monkeypatch.setattr(plugin, name, lambda draw, box, text, *args, _original=original, **kwargs: (
            drawn.append(str(text)), _original(draw, box, text, *args, **kwargs)))
    return drawn


# NBA --------------------------------------------------------------------------------------

def test_calendar_keeps_box_score_and_recap_only_once_a_game_is_played():
    played = nba_calendar._compact(nba_event("post"))
    competition = played["competitions"][0]
    assert competition["headlines"] == [{"type": "Recap", "shortLinkText": RECAP["shortLinkText"]}]
    assert [stat["name"] for stat in competition["competitors"][0]["statistics"]] == list(nba_calendar.BOX_STATISTICS)
    assert nba_calendar._compact(played) == played
    scheduled = nba_calendar._compact(nba_event("pre"))["competitions"][0]
    assert "headlines" not in scheduled and "statistics" not in scheduled["competitors"][0]


def test_nba_events_read_the_box_score_and_recap():
    event = SportsDashboard._parse_nba_espn_events({"events": [nba_event("post")]}, LA)[0]
    assert event["box_a"] == {"fg": "44.0", "three": "12/44", "reb": "41"}
    assert event["recap"] == RECAP["shortLinkText"]
    scheduled = SportsDashboard._parse_nba_espn_events({"events": [nba_event("pre")]}, LA)[0]
    assert scheduled["box_a"] == {} and scheduled["recap"] == ""
    assert SportsDashboard._basketball_team_box({"statistics": [{"name": "fieldGoalPct", "displayValue": "--"}]}) == {}


def test_duel_shows_the_box_score_under_three_leader_rows_and_the_recap_on_top(monkeypatch):
    plugin = SportsDashboard({"id": "sports_dashboard"})
    drawn = recording(plugin, monkeypatch)
    leaders = [{"label": label, "name": f"P. {label}", "value": "9"} for label in ("PTS", "REB", "AST")]
    event = {"team_a_code": "TOR", "team_b_code": "LAL", "team_leaders_a": leaders, "team_leaders_b": leaders,
             "box_a": {"fg": "42.9", "three": "17/54", "reb": "47"}, "box_b": {"fg": "53.8", "three": "18/37", "reb": "45"},
             "recap": "Luka Doncic has 45 points"}
    spec = plugin._basketball_spec("NBA")
    for colors in (DAY_COLORS, DEEP_NIGHT_COLORS):
        render(lambda image, draw: plugin._draw_basketball_leader_duel(draw, (12, 178, 328, 260), event, spec, image=image), colors)
    assert drawn.count("P. AST") == 4
    assert {"FG%", "3PT", "REB", "42.9", "18/37", "Luka Doncic has 45 points", "战报"} <= set(drawn)
    assert "LEADERS" not in drawn
    assert SportsDashboard._basketball_box_rows(event)[0] == ("FG%", "42.9", "53.8", "b")


# Club football ---------------------------------------------------------------------------

def club_payload(state="post"):
    def athlete(short):
        return [{"shortName": short, "displayName": short}]
    details = [
        {"type": {"text": "Own Goal"}, "clock": {"displayValue": "10'"}, "team": {"id": "125"}, "scoringPlay": True,
         "ownGoal": True, "athletesInvolved": athlete("D. Omari")},
        {"type": {"text": "Goal"}, "clock": {"displayValue": "14'"}, "team": {"id": "127"}, "scoringPlay": True,
         "athletesInvolved": athlete("T. Moffi")},
        {"type": {"text": "Penalty - Scored"}, "clock": {"displayValue": "47'"}, "team": {"id": "125"}, "scoringPlay": True,
         "penaltyKick": True, "athletesInvolved": athlete("V. van Dijk")},
        {"type": {"text": "Yellow Card"}, "clock": {"displayValue": "33'"}, "team": {"id": "127"}, "yellowCard": True,
         "athletesInvolved": athlete("A. Nandja")},
        {"type": {"text": "Red Card"}, "clock": {"displayValue": "80'"}, "team": {"id": "127"}, "redCard": True,
         "athletesInvolved": athlete("J. Stange")},
        {"type": {"text": "Goal"}, "clock": {"displayValue": "120'"}, "team": {"id": "127"}, "scoringPlay": True, "shootout": True},
    ]

    def competitor(side, team_id, name, score, form, possession):
        return {"homeAway": side, "score": score, "form": form,
                "statistics": [{"name": "possessionPct", "displayValue": possession}],
                "team": {"id": team_id, "displayName": name, "abbreviation": name[:3].upper()}}
    return {"leagues": [], "events": [{
        "id": "9", "date": "2026-11-01T16:30Z",
        "status": {"type": {"state": state, "completed": state == "post", "name": "STATUS_FULL_TIME"}},
        "competitions": [{"status": {"type": {"state": state, "completed": state == "post"}}, "details": details,
                          "competitors": [competitor("home", "125", "Eintracht Frankfurt", "2", "WLWDD", "51.9"),
                                          competitor("away", "127", "Hamburg SV", "1", "LLWWW", "48.1")]}],
    }]}


def test_club_events_read_scorers_cards_possession_and_form():
    final = SportsDashboard._parse_club_espn_events("BL1", club_payload("post"), LA)[0]["match_details"]
    assert final["possession"] == {"home": 51.9, "away": 48.1}
    assert final["goals"] == [{"side": "home", "name": "Omari", "minute": "10'", "kind": "og"},
                              {"side": "away", "name": "Moffi", "minute": "14'", "kind": ""},
                              {"side": "home", "name": "van Dijk", "minute": "47'", "kind": "pen"}]
    assert final["cards"][1] == {"side": "away", "name": "Stange", "minute": "80'", "red": True}
    assert "form" not in final
    upcoming = SportsDashboard._parse_club_espn_events("BL1", club_payload("pre"), LA)[0]["match_details"]
    # ESPN lists the latest result first; the card reads oldest first.
    assert upcoming == {"form": {"home": "DDWLW", "away": "WWWLL"}}
    swapped = SportsDashboard._club_swap_match_details(final)
    assert swapped["possession"] == {"away": 51.9, "home": 48.1} and swapped["goals"][0]["side"] == "away"


def test_club_card_shows_scorers_cards_and_possession_and_the_form_before_kickoff(monkeypatch):
    plugin = SportsDashboard({"id": "sports_dashboard"})
    drawn = recording(plugin, monkeypatch)
    final = SportsDashboard._parse_club_espn_events("BL1", club_payload("post"), LA)[0]
    adapted = SportsDashboard._club_football_worldcup_event(final, LA)
    assert adapted["possession_a"] == 51.9 and adapted["goals"][0]["side"] == "a"
    assert SportsDashboard._club_side_event_runs(adapted, "b") == [("goal", "Moffi 14'"), ("yellow", "1"), ("red", "Stange")]
    assert SportsDashboard._club_side_event_runs(adapted, "a", compact=True) == [("goal", "10'乌龙 47'点")]
    presentation = {"competition": "club", "team_asset_kind": "logo", "main_team_logo_scale": 1.5, "main_team_logo_top": 42}
    now = datetime(2026, 11, 2, 12, tzinfo=LA)
    for colors in (DAY_COLORS, DEEP_NIGHT_COLORS):
        render(lambda image, draw: plugin._draw_worldcup_main_card(image, draw, 12, 8, 264, 155, adapted, now, "recent",
                                                                     presentation), colors, (276, 164))
    # The narrow test card names no scorer: each side's goals read as their minutes.
    assert {"控球", "10'乌龙 47'点", "14'", "Stange"} <= set(drawn)
    upcoming = SportsDashboard._club_football_worldcup_event(
        SportsDashboard._parse_club_espn_events("BL1", club_payload("pre"), LA)[0], LA)
    assert upcoming["form_a"] == "DDWLW" and "goals" not in upcoming
    image = render(lambda image, draw: plugin._draw_worldcup_main_card(image, draw, 12, 8, 264, 155, upcoming, now, "next",
                                                                         presentation), DAY_COLORS, (276, 164))
    colours = {colour for _count, colour in image.getcolors(1 << 16)}
    assert {(22, 150, 96), (214, 62, 72)} <= colours


# NFL ----------------------------------------------------------------------------------------

def nfl_event(state="pre", indoor=False, weather=None):
    def competitor(side, team_id, code):
        return {"id": team_id, "homeAway": side, "score": "0", "team": {"id": team_id, "abbreviation": code, "displayName": code}}
    return {"id": "401873036", "date": "2026-11-03T01:15Z",
            "weather": weather if weather is not None else {"temperature": 47, "conditionId": "36", "displayValue": "Intermittent clouds"},
            "status": {"type": {"state": state, "completed": state == "post"}},
            "competitions": [{"date": "2026-11-03T01:15Z", "venue": {"fullName": "Lumen Field", "indoor": indoor},
                              "headlines": [{"type": "Recap", "shortLinkText": "Seahawks edge Bears"}],
                              "status": {"type": {"state": state, "completed": state == "post"}},
                              "competitors": [competitor("home", "26", "SEA"), competitor("away", "3", "CHI")]}]}


@pytest.mark.parametrize("payload, expected", [
    (nfl_event(), {"temp": 47, "condition": "晴间多云"}),
    (nfl_event(indoor=True), {}),
    (nfl_event(weather={"temperature": "warm"}), {}),
    (nfl_event(weather={"temperature": 61, "conditionId": "99"}), {"temp": 61, "condition": ""}),
])
def test_nfl_forecast_only_for_open_air_games(payload, expected):
    event = SportsDashboard._parse_football_scoreboard({"events": [payload]}, LA, "NFL")["events"][0]
    assert event["weather"] == expected and event["recap"] == ""


@pytest.mark.parametrize("fahrenheit, label", [(66, "19°C 多云"), (32, "0°C 多云"), (14, "-10°C 多云")])
def test_nfl_forecast_shows_celsius(fahrenheit, label):
    assert SportsDashboard._football_weather_label({"weather": {"temp": fahrenheit, "condition": "多云"}}) == label


def test_nfl_card_footer_names_the_forecast_and_a_final_its_recap(monkeypatch):
    plugin = SportsDashboard({"id": "sports_dashboard"})
    drawn = recording(plugin, monkeypatch)
    footers = []
    monkeypatch.setattr(plugin, "_draw_kit_venue_line", lambda *args, **kwargs: footers.append(kwargs.get("after")) or True)
    pregame = SportsDashboard._parse_football_scoreboard({"events": [nfl_event()]}, LA, "NFL")["events"][0]
    final = SportsDashboard._parse_football_scoreboard({"events": [nfl_event("post")]}, LA, "NFL")["events"][0]
    assert final["recap"] == "Seahawks edge Bears" and final["weather"] == {}
    now = datetime(2026, 11, 2, 16, tzinfo=LA)
    render(lambda image, draw: plugin._draw_nfl_pregame_details(image, draw, (12, 150, 330, 252), pregame, now))
    render(lambda image, draw: plugin._draw_nfl_final_details(image, draw, (12, 150, 330, 252), final))
    assert footers[0].endswith("8°C 晴间多云")
    assert "Seahawks edge Bears" in drawn and "战报" in drawn


def test_club_card_keeps_a_clear_gap_between_each_name_and_its_form(monkeypatch):
    plugin = SportsDashboard({"id": "sports_dashboard"})
    recording(plugin, monkeypatch)
    upcoming = SportsDashboard._club_football_worldcup_event(
        SportsDashboard._parse_club_espn_events("BL1", club_payload("pre"), LA)[0], LA)
    presentation = {"competition": "club", "team_asset_kind": "logo", "main_team_logo_scale": 1.5, "main_team_logo_top": 42}
    now = datetime(2026, 11, 2, 12, tzinfo=LA)
    image = render(lambda image, draw: plugin._draw_worldcup_main_card(image, draw, 12, 8, 264, 155, upcoming, now, "next",
                                                                         presentation), DAY_COLORS, (276, 164)).convert("RGB")
    cells = {(22, 150, 96), (214, 62, 72)}
    form_rows = [y for y in range(image.height)
                 if sum(image.getpixel((x, y)) in cells for x in range(20, 130)) >= 3]
    form_top = min(form_rows)
    ink = DAY_COLORS["text"]
    name_rows = [y for y in range(form_top - 30, form_top) if any(image.getpixel((x, y)) == ink for x in range(20, 130))]
    # The name's letters end a clear gap above its last-five cells.
    # (At least three clear rows; the cards used to leave two, which read as touching on the panel.)
    assert name_rows and max(name_rows) <= form_top - 4
