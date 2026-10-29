"""NFL standalone panel: scorebug parsing, labels, geometry and rendering."""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from PIL import Image, ImageDraw
import pytest

from plugins.sports_dashboard.common import _ACTIVE_COLORS, COLORS, DAY_COLORS, DEEP_NIGHT_COLORS
from plugins.sports_dashboard.scorebug_kit import KIT_SCOREBOARD_ROW_HEIGHT
from plugins.sports_dashboard.sports_dashboard import SportsDashboard
from tests.test_sports_dashboard import _plugin

LA = ZoneInfo("America/Los_Angeles")
NOW = datetime(2026, 10, 18, 11, 25, tzinfo=LA)


def _competitor(side, team_id, code, name, score, color, linescores=(), record="3-2"):
    return {
        "homeAway": side,
        "id": team_id,
        "score": str(score),
        "linescores": [{"value": float(value), "period": index + 1} for index, value in enumerate(linescores)],
        "records": [{"type": "total", "summary": record}],
        "team": {
            "id": team_id,
            "abbreviation": code,
            "shortDisplayName": name,
            "displayName": name,
            "color": color,
            "logo": f"https://a.espncdn.com/i/teamlogos/nfl/500/scoreboard/{code.lower()}.png",
        },
    }


def _event(event_id, start, state, away, home, situation=None, detail="", period=0, clock="0:00", **extra):
    status_type = {
        "state": state,
        "completed": state == "post",
        "description": {"in": "In Progress", "post": "Final", "pre": "Scheduled"}[state],
        "shortDetail": detail,
    }
    competition = {
        "id": event_id,
        "date": start,
        "venue": {"fullName": "Mercedes-Benz Stadium", "address": {"city": "Atlanta", "state": "GA"}},
        "status": {"period": period, "displayClock": clock, "type": status_type},
        "broadcasts": [{"market": "national", "names": ["Fox"]}],
        "competitors": [home, away],
        **extra,
    }
    if situation is not None:
        competition["situation"] = situation
    return {"id": event_id, "date": start, "week": {"number": 6}, "competitions": [competition]}


def _leaders(team_id):
    return [
        {"shortDisplayName": "PASS", "leaders": [{"displayValue": "16/18, 172 YDS, 1 TD", "athlete": {"shortName": "C. Williams"}, "team": {"id": team_id}}]},
        {"shortDisplayName": "RUSH", "leaders": [{"displayValue": "3 CAR, 38 YDS", "athlete": {"shortName": "B. Robinson"}, "team": {"id": "1"}}]},
    ]


def _payload():
    live = _event(
        "live-away-ball",
        "2026-10-18T17:00Z",
        "in",
        _competitor("away", "3", "CHI", "Bears", 14, "0b1c3a", (14, 0)),
        _competitor("home", "1", "ATL", "Falcons", 7, "a71930", (0, 7), record="2-3"),
        situation={
            "possession": "3",
            "yardLine": 76,
            "distance": 17,
            "downDistanceText": "3rd & 17 at CHI 24",
            "shortDownDistanceText": "3rd & 17",
            "possessionText": "CHI 24",
            "isRedZone": False,
            "awayTimeouts": 3,
            "homeTimeouts": 1,
            "lastPlay": {
                "text": "Two-Minute Warning",
                "team": {"id": "3"},
                "probability": {"awayWinPercentage": 0.7856, "homeWinPercentage": 0.2144},
                "drive": {"description": "5 plays, 4 yards, 3:45", "start": {"yardLine": 80, "text": "CHI 20"}},
            },
        },
        detail="2:00 - 2nd",
        period=2,
        clock="2:00",
        leaders=_leaders("3"),
    )
    red_zone = _event(
        "live-red-zone",
        "2026-10-18T17:00Z",
        "in",
        _competitor("away", "29", "CAR", "Panthers", 3, "0085ca", (3, 0)),
        _competitor("home", "21", "PHI", "Eagles", 0, "06424d", (0, 0)),
        situation={
            "possession": "29",
            "yardLine": 12,
            "distance": 10,
            "shortDownDistanceText": "1st & 10",
            "possessionText": "PHI 12",
            "isRedZone": True,
            "awayTimeouts": 3,
            "homeTimeouts": 3,
            "lastPlay": {"text": "B.Young pass", "team": {"id": "21"}, "drive": {"description": "1 play, 0 yards, 0:00"}},
        },
        detail="8:32 - 2nd",
        period=2,
        clock="8:32",
    )
    halftime = _event(
        "live-half",
        "2026-10-18T17:00Z",
        "in",
        _competitor("away", "33", "BAL", "Ravens", 0, "29126f", (0, 0)),
        _competitor("home", "5", "CLE", "Browns", 10, "472a08", (3, 7)),
        situation={},
        detail="Halftime",
        period=2,
        clock="0:00",
    )
    scheduled = _event(
        "next",
        "2026-10-18T20:05Z",
        "pre",
        _competitor("away", "22", "ARI", "Cardinals", 0, "a40227", record="1-4"),
        _competitor("home", "14", "LAR", "Rams", 0, "003594"),
        detail="10/18 - 4:05 PM EDT",
        odds=[{"details": "LAR -12.5", "overUnder": 51.5}],
        leaders=_leaders("14"),
    )
    later = _event(
        "monday",
        "2026-10-20T00:15Z",
        "pre",
        _competitor("away", "28", "WSH", "Commanders", 0, "5a1414"),
        _competitor("home", "25", "SF", "49ers", 0, "aa0000"),
    )
    final = _event(
        "final-ot",
        "2026-10-18T13:30Z",
        "post",
        _competitor("away", "26", "SEA", "Seahawks", 14, "002a5c", (3, 0, 0, 11, 0)),
        _competitor("home", "7", "DEN", "Broncos", 17, "0a2343", (0, 14, 0, 0, 3)),
        detail="Final/OT",
        period=5,
        leaders=_leaders("26"),
    )
    return {"week": {"number": 6}, "events": [live, red_zone, halftime, scheduled, later, final]}


@pytest.fixture
def parsed():
    return SportsDashboard._parse_football_scoreboard(_payload(), LA, "NFL")


def _by_id(parsed, event_id):
    return next(event for event in parsed["events"] if event["event_id"] == event_id)


def _card(parsed, main_id=None, events=None):
    card = SportsDashboard._offseason_hub_card("NFL", {"events": events or parsed["events"], "week_label": parsed["week_label"]}, NOW)
    if main_id:
        card["main"] = _by_id(parsed, main_id)
    return card


# Parsing -----------------------------------------------------------------------


def test_live_event_carries_scorebug_fields(parsed):
    event = _by_id(parsed, "live-away-ball")

    assert event["linescore_a"] == [14, 0]
    assert event["linescore_b"] == [0, 7]
    assert (event["timeouts_a"], event["timeouts_b"]) == (3, 1)
    assert event["down_distance_short"] == "3RD & 17"
    assert event["distance"] == 17
    assert event["ball_yard"] == 76
    assert event["drive_summary"] == "5 plays, 4 yards, 3:45"
    assert event["drive_start_yard"] == 80
    assert event["win_pct_a"] == pytest.approx(0.7856)
    assert event["win_pct_b"] == pytest.approx(0.2144)
    assert (event["team_a_color"], event["team_b_color"]) == ("0b1c3a", "a71930")
    assert event["red_zone"] is False
    assert event["status_detail"] == "2:00 - 2nd"
    assert event["leaders"][0] == {"label": "PASS", "name": "C. Williams", "value": "16/18, 172 YDS, 1 TD", "side": "a"}
    assert event["leaders"][1]["side"] == "b"


def test_drive_is_dropped_when_the_last_play_was_the_other_teams(parsed):
    event = _by_id(parsed, "live-red-zone")

    assert event["red_zone"] is True
    assert event["ball_yard"] == 12
    assert event["drive_summary"] == ""
    assert event["drive_start_yard"] is None


def test_scheduled_and_final_events_have_no_live_situation(parsed):
    scheduled = _by_id(parsed, "next")
    final = _by_id(parsed, "final-ot")

    for event in (scheduled, final):
        assert event["timeouts_a"] is None and event["ball_yard"] is None
        assert event["win_pct_a"] is None and event["drive_summary"] == ""
    assert scheduled["linescore_a"] == []
    # ESPN sends season leaders before kickoff and game leaders after it.
    assert scheduled["leaders"][0]["name"] == "C. Williams"
    assert final["linescore_b"] == [0, 14, 0, 0, 3]


def test_nfl_logos_come_from_the_espn_thumbnail_combiner(parsed):
    event = _by_id(parsed, "live-away-ball")

    assert event["team_a_logo"] == (
        "https://a.espncdn.com/combiner/i?img=%2Fi%2Fteamlogos%2Fnfl%2F500%2Fscoreboard%2Fchi.png&w=200&h=200"
    )
    assert SportsDashboard._espn_logo_thumbnail_url("https://example.com/chi.png") == "https://example.com/chi.png"
    assert SportsDashboard._espn_logo_thumbnail_url("https://a.espncdn.com/i/headshots/nfl/1.png") == "https://a.espncdn.com/i/headshots/nfl/1.png"


# Labels and geometry -----------------------------------------------------------


def test_scoreboard_lists_live_then_upcoming_then_finals_without_the_featured_game(parsed):
    card = _card(parsed)
    games = SportsDashboard._football_scoreboard_events(card, "NFL")

    assert card["main"]["event_id"] not in [game["event_id"] for game in games]
    states = [SportsDashboard._hub_event_state(game) for game in games]
    assert states == sorted(states, key=["live", "scheduled", "final"].index)
    assert [game["event_id"] for game in games if SportsDashboard._hub_event_state(game) == "scheduled"] == ["next", "monday"]


@pytest.mark.parametrize(
    "event_id, expected",
    [
        ("live-away-ball", ("Q2", "2:00", "nfl_live")),
        ("live-half", ("HALF", "", "nfl_live")),
        ("final-ot", ("FINAL", "OT", "text")),
        ("next", ("1:05 PM", "Fox", "text")),
        ("monday", ("MON", "5:15 PM", "text")),
    ],
)
def test_scoreboard_state_column_labels(parsed, event_id, expected):
    assert SportsDashboard._nfl_scoreboard_state_labels(_by_id(parsed, event_id), NOW) == expected


def test_live_clock_labels_cover_breaks_and_overtime():
    assert SportsDashboard._nfl_live_clock_label({"status_detail": "Halftime", "period": 2}) == "HALFTIME"
    assert SportsDashboard._nfl_live_clock_label({"status_detail": "End of 1st", "period": 1}) == "END Q1"
    assert SportsDashboard._nfl_live_clock_label({"period": 5, "clock": "3:12"}) == "OT 3:12"
    assert SportsDashboard._nfl_live_clock_label({"period": 6, "clock": "1:00"}) == "2OT 1:00"


def test_status_pill_labels(parsed):
    assert SportsDashboard._nfl_status_pill_label(_by_id(parsed, "next"), NOW) == "TODAY 1:05 PM"
    assert SportsDashboard._nfl_status_pill_label(_by_id(parsed, "monday"), NOW) == "MON 5:15 PM"
    assert SportsDashboard._nfl_status_pill_label(_by_id(parsed, "final-ot"), NOW) == "FINAL/OT"
    far = dict(_by_id(parsed, "monday"), start=NOW + timedelta(days=9))
    assert SportsDashboard._nfl_status_pill_label(far, NOW) == "10/27 11:25 AM"


def test_line_to_gain_follows_the_offense_direction():
    away_ball = {"distance": 17, "down_distance_short": "3RD & 17"}
    assert SportsDashboard._nfl_line_to_gain(away_ball, 24, "a") == 41
    assert SportsDashboard._nfl_line_to_gain(away_ball, 24, "b") == 7
    assert SportsDashboard._nfl_line_to_gain({"distance": 5, "down_distance_short": "2ND & GOAL"}, 95, "a") is None
    assert SportsDashboard._nfl_line_to_gain({"distance": 10, "down_distance": "1ST & 10 AT PHI 5"}, 95, "a") is None


def test_short_down_label_falls_back_to_the_long_text():
    assert SportsDashboard._nfl_short_down_label({"down_distance": "3RD & 4 AT SEA 42"}) == "3RD & 4"


def test_spread_is_shown_from_each_teams_side(parsed):
    event = _by_id(parsed, "next")

    assert SportsDashboard._kit_spread_for_side(event, "a") == "+12.5"
    assert SportsDashboard._kit_spread_for_side(event, "b") == "-12.5"
    assert SportsDashboard._kit_spread_for_side({"spread": "LAR -3", "team_a_code": "ARI", "team_b_code": "LAR"}, "a") == "+3"
    assert SportsDashboard._kit_spread_for_side({"spread": "EVEN"}, "a") == ""


def test_drive_summary_label():
    assert SportsDashboard._nfl_drive_summary_label("5 plays, 4 yards, 3:45") == "5 PLAYS · 4 YDS · 3:45"
    assert SportsDashboard._nfl_drive_summary_label("1 play, 1 yard, 0:20") == "1 PLAY · 1 YD · 0:20"
    assert SportsDashboard._nfl_drive_summary_label("") == ""


def test_team_colours_map_to_pure_panel_inks():
    assert SportsDashboard._kit_team_ink_key("a71930", "nfl_accent") == "red"
    assert SportsDashboard._kit_team_ink_key("0b1c3a", "nfl_accent") == "text"
    assert SportsDashboard._kit_team_ink_key("0085ca", "nfl_accent") == "blue"
    assert SportsDashboard._kit_team_ink_key("ffb612", "nfl_accent") == "amber"
    assert SportsDashboard._kit_team_ink_key("", "nfl_accent") == "nfl_accent"


def test_kickoff_countdown_label():
    countdown = SportsDashboard._kit_countdown_label
    assert countdown(NOW + timedelta(hours=1, minutes=40), NOW) == "IN 1H 40M"
    assert countdown(NOW + timedelta(minutes=9), NOW) == "IN 9M"
    assert countdown(NOW + timedelta(days=2, hours=3), NOW) == "IN 2D 3H"
    assert countdown(NOW - timedelta(minutes=1), NOW) == "SOON"
    assert countdown(None, NOW) == "TBD"


# Rendering ---------------------------------------------------------------------


@pytest.fixture
def render(monkeypatch):
    """Render the panel offline, recording every boxed text draw."""
    boxes = []
    original = SportsDashboard._draw_text_in_box

    def capture(draw, box, text, font, color, align="left"):
        boxes.append((tuple(box), str(text), font))
        return original(draw, box, text, font, color, align=align)

    monkeypatch.setattr(SportsDashboard, "_draw_text_in_box", staticmethod(capture))

    def draw_panel(card, colors=DAY_COLORS, now=NOW, size=(556, 268)):
        plugin = _plugin()
        plugin._draw_team_logo = lambda *_args, **_kwargs: None
        token = _ACTIVE_COLORS.set(colors)
        try:
            image = Image.new("RGB", size, COLORS["paper"])
            draw = ImageDraw.Draw(image)
            plugin._draw_nfl_standalone_panel(image, draw, (0, 0, size[0] - 1, size[1] - 1), card, "HUB LIVE", now)
        finally:
            _ACTIVE_COLORS.reset(token)
        return image, draw, [text for _box, text, _font in boxes]

    draw_panel.boxes = boxes
    return draw_panel


def _assert_texts_fit(draw, boxes):
    assert boxes
    for box, text, font in boxes:
        width = SportsDashboard._text_width(draw, text, font)
        assert width <= box[2] - box[0] + 1, (text, box, width)


def test_live_panel_draws_scorebug_tracker_and_scoreboard(parsed, render):
    image, draw, texts = render(_card(parsed, "live-away-ball"))

    for expected in ("Q2 2:00", "Fox", "WEEK 6", "3RD & 17", "CHI 24", "5 PLAYS · 4 YDS · 3:45", "Two-Minute Warning", "CHI 79%", "21% ATL", "2 LIVE", "HALF"):
        assert expected in texts
    assert "14" in texts and "7" in texts
    _assert_texts_fit(draw, render.boxes)


def test_drive_tracker_places_ball_drive_and_line_to_gain(parsed):
    plugin = _plugin()
    event = _by_id(parsed, "live-away-ball")
    image = Image.new("RGB", (300, 30), DAY_COLORS["panel"])
    plugin._draw_nfl_drive_tracker(ImageDraw.Draw(image), (0, 0, 299, 22), event)

    zone_w = max(14, int(299 * 0.08))
    field_x1, field_x2 = zone_w, 299 - zone_w

    def x_at(yards):
        return int(round(field_x1 + (field_x2 - field_x1) * yards / 100))

    amber = DAY_COLORS["amber"]
    assert image.getpixel((x_at(41), 11)) == amber  # line to gain: CHI 24 + 17
    assert image.getpixel((x_at(21), 11)) == amber  # drive bar from CHI 20
    assert image.getpixel((x_at(24), 9)) == amber  # football at CHI 24
    assert image.getpixel((2, 3)) == DAY_COLORS["text"]  # CHI end zone: navy -> black ink
    assert image.getpixel((294, 3)) == DAY_COLORS["red"]  # ATL end zone: red ink


def test_win_probability_bar_splits_at_the_away_share(parsed):
    plugin = _plugin()
    event = _by_id(parsed, "live-away-ball")
    image = Image.new("RGB", (300, 14), DAY_COLORS["panel"])
    draw = ImageDraw.Draw(image)

    assert plugin._draw_nfl_win_probability(draw, (0, 0, 299, 12), event)

    def longest_run(color):
        best = run = 0
        for x in range(300):
            run = run + 1 if image.getpixel((x, 6)) == color else 0
            best = max(best, run)
        return best

    away, home = longest_run(DAY_COLORS["text"]), longest_run(DAY_COLORS["red"])
    assert away / (away + home) == pytest.approx(0.7856, abs=0.06)
    assert plugin._draw_nfl_win_probability(draw, (0, 0, 299, 12), {"win_pct_a": None}) is False


def test_final_panel_marks_the_winner_and_lists_leaders(parsed, render):
    card = _card(parsed, "final-ot", events=[_by_id(parsed, "final-ot")])
    image, draw, texts = render(card)

    assert "FINAL/OT" in texts
    assert "OT" in texts  # fifth linescore column
    # The leader categories are icons (the pass, the run), not PASS / RUSH tags.
    assert {"C. Williams", "16/18, 172 YDS, 1 TD"} <= set(texts) and not {"PASS", "RUSH"} & set(texts)
    _assert_texts_fit(draw, render.boxes)
    # The winner triangle sits at the right edge of the home (DEN) row.
    card_right = 12 + 318 - 10
    home_row_middle = 57 + 31 + 10 + 33 + 15
    assert image.getpixel((card_right - 2, home_row_middle)) == DAY_COLORS["text"]


def test_pregame_panel_shows_kickoff_tiles_spread_and_season_leaders(parsed, render):
    events = [event for event in parsed["events"] if SportsDashboard._hub_event_state(event) != "live"]
    image, draw, texts = render(_card(parsed, "next", events=events))

    # The countdown is drawn as flip cards (1 H, 40 M), the total with its unit.
    for expected in ("TODAY 1:05 PM", "KICKOFF", "1", "H", "40", "M", "TV", "O/U", "51.5", "PTS", "+12.5", "-12.5", "C. Williams"):
        assert expected in texts
    # Fox's broadcast reads as the network's logo, not its name.
    assert "Fox" not in texts
    _assert_texts_fit(draw, render.boxes)


def test_scoreboard_never_draws_rows_past_its_bottom(parsed, render, monkeypatch):
    rows = []
    original = SportsDashboard._draw_kit_scoreboard_row

    def capture_row(self, image, draw, box, event, now, spec, last=False, slot=None):
        rows.append(box)
        return original(self, image, draw, box, event, now, spec, last=last, slot=slot)

    monkeypatch.setattr(SportsDashboard, "_draw_kit_scoreboard_row", capture_row)
    many = [dict(_by_id(parsed, "monday"), event_id=f"game-{index}") for index in range(20)]
    card = _card(parsed, "live-away-ball", events=[_by_id(parsed, "live-away-ball"), *many])
    render(card)

    bottom = 268 - 1 - 7
    assert rows and all(box[3] <= bottom for box in rows)
    assert len(rows) == (bottom - (57 - 2 + 23) + 1) // KIT_SCOREBOARD_ROW_HEIGHT


def test_empty_card_and_empty_scoreboard_render_placeholders(render):
    _image, _draw, texts = render({"sport": "NFL", "status": "BREAK", "main": None, "events": []})

    assert "No NFL schedule" in texts


def test_night_panel_uses_the_night_ink(parsed, render):
    image, _draw, _texts = render(_card(parsed, "live-away-ball"), colors=DEEP_NIGHT_COLORS)

    pixels = set(image.crop((12, 57, 330, 260)).get_flattened_data())
    assert tuple(DEEP_NIGHT_COLORS["text"]) in pixels
    assert tuple(DAY_COLORS["panel"]) not in pixels
