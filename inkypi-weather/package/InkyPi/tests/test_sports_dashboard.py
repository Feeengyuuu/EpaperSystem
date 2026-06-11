import sys
import types
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from PIL import Image, ImageDraw

try:
    import requests  # noqa: F401
except ModuleNotFoundError:
    requests_stub = types.ModuleType("requests")
    requests_stub.Session = object
    requests_stub.get = lambda *args, **kwargs: None
    requests_stub.adapters = types.SimpleNamespace(HTTPAdapter=object)
    sys.modules["requests"] = requests_stub

try:
    import psutil  # noqa: F401
except ModuleNotFoundError:
    psutil_stub = types.ModuleType("psutil")
    psutil_stub.virtual_memory = lambda: types.SimpleNamespace(total=8 * 1024 ** 3)
    sys.modules["psutil"] = psutil_stub

try:
    import jinja2  # noqa: F401
except ModuleNotFoundError:
    jinja2_stub = types.ModuleType("jinja2")
    jinja2_stub.Environment = object
    jinja2_stub.FileSystemLoader = object
    jinja2_stub.select_autoescape = lambda *args, **kwargs: None
    sys.modules["jinja2"] = jinja2_stub

from plugins.sports_dashboard.sports_dashboard import (
    COLORS,
    DAY_COLORS,
    DEEP_NIGHT_COLORS,
    LOCAL_LPL_LOGO_PATH,
    LOCAL_LPL_MARBLE_FILLER_PATH,
    LOCAL_NBA_COURT_STRIP_PATH,
    LOCAL_NBA_EMPTY_SLOT_FILLER_PATH,
    LOCAL_NBA_LOGO_PATH,
    LOCAL_WORLDCUP_HEADER_BANNER_PATH,
    LOCAL_WORLDCUP_PITCH_STRIP_PATH,
    LOCAL_WORLDCUP_LOGO_PATH,
    NBA_INLINE_LOGO_SIZE,
    NBA_INLINE_TEAM_FONT_SIZE,
    NBA_INLINE_TEAM_MIN_FONT_SIZE,
    NBA_MINI_LINEUP_LOGO_SIZE,
    NBA_MINI_LINEUP_ODDS_TEAM_FONT_SIZE,
    SportsDashboard,
    _ACTIVE_COLORS,
    _safe_exception_text,
)


class FakeDeviceConfig:
    def __init__(self, resolution=(800, 480), orientation="horizontal", timezone="America/Los_Angeles"):
        self.resolution = resolution
        self.orientation = orientation
        self.timezone = timezone
        self.env = {}

    def get_resolution(self):
        return self.resolution

    def get_config(self, key=None, default=None):
        values = {"orientation": self.orientation, "timezone": self.timezone}
        if key is None:
            return values
        return values.get(key, default)

    def load_env_key(self, key):
        return self.env.get(key)


def _plugin():
    return SportsDashboard({"id": "sports_dashboard"})


def test_league_accent_palettes_are_distinct():
    for palette in (DAY_COLORS, DEEP_NIGHT_COLORS):
        assert palette["worldcup_accent"] != palette["nba_accent"]
        assert palette["worldcup_accent"] != palette["lpl_accent"]
        assert palette["nba_accent"] != palette["lpl_accent"]
        assert palette["worldcup_tag"] != palette["nba_tag"]
        assert palette["nba_tag"] != palette["lpl_tag"]


def test_section_header_uses_supplied_league_accent():
    plugin = _plugin()
    image = Image.new("RGB", (120, 52), COLORS["paper"])
    draw = ImageDraw.Draw(image)

    plugin._draw_section_header(draw, 0, 120, 10, "UPCOMING", COLORS["lpl_accent"])

    assert image.getpixel((18, 20)) == COLORS["lpl_accent"]


def test_worldcup_scheduled_rows_use_worldcup_accent():
    assert SportsDashboard._worldcup_status_color({"state": "SCHEDULED"}) == COLORS["worldcup_accent"]
    assert SportsDashboard._worldcup_status_color({"state": "LIVE"}) == COLORS["worldcup_live"]


def test_safe_exception_text_redacts_query_secrets():
    text = _safe_exception_text(
        RuntimeError(
            "401 Client Error for url: "
            "https://api.example.test/odds?apiKey=secret-key-123&token=secret-token&regions=us"
        )
    )

    assert "secret-key-123" not in text
    assert "secret-token" not in text
    assert "apiKey=<redacted>" in text
    assert "token=<redacted>" in text
    assert "regions=us" in text


def _sample_payload():
    return {
        "data": {
            "schedule": {
                "events": [
                    {
                        "startTime": "2026-06-03T09:00:00Z",
                        "state": "unstarted",
                        "blockName": "Playoffs",
                        "match": {
                            "id": "match-blg-edg",
                            "strategy": {"type": "bestOf", "count": 5},
                            "teams": [
                                {"code": "BLG", "image": "https://example.com/blg.png", "result": {}},
                                {"code": "EDG", "image": "https://example.com/edg.png", "result": {}},
                            ]
                        },
                    },
                    {
                        "startTime": "2026-06-02T09:00:00Z",
                        "state": "completed",
                        "blockName": "Playoffs",
                        "match": {
                            "id": "match-tt-lgd",
                            "strategy": {"type": "bestOf", "count": 5},
                            "teams": [
                                {"code": "TT", "image": "https://example.com/tt.png", "result": {"gameWins": 2}},
                                {"code": "LGD", "image": "https://example.com/lgd.png", "result": {"gameWins": 3}},
                            ]
                        },
                    },
                ]
            }
        }
    }


def _sample_worldcup_fixture():
    return {
        "fixture": {
            "id": 10101,
            "date": "2026-06-12T00:00:00+00:00",
            "status": {"short": "NS", "long": "Not Started", "elapsed": None},
        },
        "league": {"round": "Group Stage - 1"},
        "teams": {
            "home": {"name": "United States", "code": "USA"},
            "away": {"name": "Mexico", "code": "MEX"},
        },
        "goals": {"home": None, "away": None},
        "score": {"fulltime": {"home": None, "away": None}},
    }


def _sample_nba_scoreboard_payload():
    return {
        "events": [
            {
                "id": "401000001",
                "date": "2026-06-05T00:30Z",
                "season": {"slug": "post-season"},
                "competitions": [
                    {
                        "id": "401000001",
                        "date": "2026-06-05T00:30Z",
                        "status": {
                            "period": 4,
                            "displayClock": "0.0",
                            "type": {
                                "state": "post",
                                "completed": True,
                                "description": "Final",
                                "shortDetail": "Final",
                            },
                        },
                        "series": {
                            "competitors": [
                                {"team": {"id": "18", "abbreviation": "NY"}, "wins": 2},
                                {"team": {"id": "24", "abbreviation": "SA"}, "wins": 0},
                            ]
                        },
                        "competitors": [
                            {
                                "homeAway": "home",
                                "score": "112",
                                "team": {
                                    "abbreviation": "NY",
                                    "shortDisplayName": "Knicks",
                                    "displayName": "New York Knicks",
                                    "logo": "https://example.com/ny.png",
                                },
                                "linescores": [{"value": 28}, {"value": 27}, {"value": 31}, {"value": 26}],
                            },
                            {
                                "homeAway": "away",
                                "score": "106",
                                "team": {
                                    "abbreviation": "SA",
                                    "shortDisplayName": "Spurs",
                                    "displayName": "San Antonio Spurs",
                                    "logo": "https://example.com/sa.png",
                                },
                                "linescores": [{"value": 25}, {"value": 29}, {"value": 24}, {"value": 28}],
                            },
                        ],
                    }
                ],
            },
            {
                "id": "401000002",
                "date": "2026-06-09T00:30Z",
                "season": {"slug": "post-season"},
                "competitions": [
                    {
                        "id": "401000002",
                        "date": "2026-06-09T00:30Z",
                        "status": {
                            "period": 0,
                            "displayClock": "",
                            "type": {
                                "state": "pre",
                                "completed": False,
                                "description": "Scheduled",
                                "shortDetail": "Tue, Jun 9",
                            },
                        },
                        "series": {
                            "competitors": [
                                {"team": {"id": "18", "abbreviation": "NY"}, "wins": 2},
                                {"team": {"id": "24", "abbreviation": "SA"}, "wins": 0},
                            ]
                        },
                        "competitors": [
                            {
                                "homeAway": "home",
                                "score": "0",
                                "team": {
                                    "abbreviation": "NY",
                                    "shortDisplayName": "Knicks",
                                    "displayName": "New York Knicks",
                                    "logo": "https://example.com/ny.png",
                                },
                                "linescores": [],
                            },
                            {
                                "homeAway": "away",
                                "score": "0",
                                "team": {
                                    "abbreviation": "SA",
                                    "shortDisplayName": "Spurs",
                                    "displayName": "San Antonio Spurs",
                                    "logo": "https://example.com/sa.png",
                                },
                                "linescores": [],
                            },
                        ],
                    }
                ],
            },
        ]
    }


def _sample_nba_odds_event():
    return {
        "id": "nba-ny-sa",
        "sport_key": "basketball_nba",
        "commence_time": "2026-06-09T00:30:00Z",
        "home_team": "New York Knicks",
        "away_team": "San Antonio Spurs",
        "bookmakers": [
            {
                "key": "draftkings",
                "title": "DraftKings",
                "markets": [
                    {
                        "key": "h2h",
                        "outcomes": [
                            {"name": "New York Knicks", "price": 1.75},
                            {"name": "San Antonio Spurs", "price": 2.05},
                        ],
                    }
                ],
            }
        ],
    }


def _sample_nba_odds_api_io_event():
    return {
        "id": 88112233,
        "home": "New York Knicks",
        "away": "San Antonio Spurs",
        "date": "2026-06-09T00:30:00Z",
        "status": "pending",
        "league": {"name": "USA - NBA, Playoffs", "slug": "usa-nba-playoffs"},
        "bookmakers": {
            "Bet365": [
                {
                    "name": "ML",
                    "odds": [
                        {"home": "1.650", "away": "2.100"},
                    ],
                }
            ]
        },
    }


def _sample_football_data_match():
    return {
        "utcDate": "2026-06-11T19:00:00Z",
        "status": "TIMED",
        "stage": "GROUP_STAGE",
        "group": "GROUP_A",
        "homeTeam": {"id": 758, "name": "Mexico", "shortName": "Mexico", "tla": "MEX"},
        "awayTeam": {"id": 1577, "name": "South Africa", "shortName": "South Africa", "tla": "RSA"},
        "score": {"fullTime": {"home": None, "away": None}},
    }


def _sample_worldcup_odds_event():
    return {
        "id": "mex-rsa",
        "sport_key": "soccer_fifa_world_cup",
        "commence_time": "2026-06-11T19:00:00Z",
        "home_team": "Mexico",
        "away_team": "South Africa",
        "bookmakers": [
            {
                "key": "draftkings",
                "title": "DraftKings",
                "markets": [
                    {
                        "key": "h2h",
                        "outcomes": [
                            {"name": "Mexico", "price": 1.8},
                            {"name": "Draw", "price": 3.25},
                            {"name": "South Africa", "price": 4.5},
                        ],
                    }
                ],
            }
        ],
    }


def _sample_worldcup_odds_api_io_event():
    return {
        "id": 66456904,
        "home": "Mexico",
        "away": "South Africa",
        "date": "2026-06-11T19:00:00Z",
        "status": "pending",
        "league": {"name": "International - FIFA World Cup", "slug": "international-fifa-world-cup"},
        "bookmakers": {
            "Bet365": [
                {
                    "name": "ML",
                    "odds": [
                        {"home": "1.400", "draw": "4.500", "away": "8.000"},
                    ],
                }
            ]
        },
    }


def _sample_lpl_odds_api_io_event(
    home="Bilibili Gaming",
    away="Edward Gaming",
    date="2026-06-03T09:00:00Z",
    home_odds="1.650",
    away_odds="2.100",
):
    return {
        "id": 71827048,
        "home": home,
        "away": away,
        "date": date,
        "status": "pending",
        "league": {"name": "League of Legends - Split 2", "slug": "league-of-legends-split-2"},
        "bookmakers": {
            "Bet365": [
                {
                    "name": "ML",
                    "odds": [
                        {"home": home_odds, "away": away_odds},
                    ],
                }
            ]
        },
    }


def _sports_dashboard_tmp(name):
    path = Path(__file__).resolve().parents[1] / "tmp" / "sports_dashboard_tests" / name
    path.mkdir(parents=True, exist_ok=True)
    return path


def _fresh_lpl_frame_time(minutes_ago=0):
    frame_time = datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)
    return frame_time.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def test_lpl_parser_converts_utc_starts_to_california_time():
    la = ZoneInfo("America/Los_Angeles")

    events = SportsDashboard._parse_lpl_events(_sample_payload(), la)

    assert events[0]["team_a"] == "TT"
    assert events[0]["start"].strftime("%Y-%m-%d %H:%M") == "2026-06-02 02:00"
    assert events[1]["team_a"] == "BLG"
    assert events[1]["start"].strftime("%Y-%m-%d %H:%M") == "2026-06-03 02:00"
    assert events[1]["team_a_logo"] == "https://example.com/blg.png"
    assert events[1]["team_b_logo"] == "https://example.com/edg.png"
    assert events[1]["best_of"] == 5
    assert events[1]["event_id"] == "match-blg-edg"
    assert events[1]["match_id"] == "match-blg-edg"


def test_select_lpl_events_returns_next_match_and_recent_result():
    la = ZoneInfo("America/Los_Angeles")
    events = SportsDashboard._parse_lpl_events(_sample_payload(), la)
    now = datetime(2026, 6, 2, 12, 0, tzinfo=la)

    selected = SportsDashboard._select_lpl_events(events, now)

    assert selected["main"]["team_a"] == "BLG"
    assert selected["upcoming"][0]["team_b"] == "EDG"
    assert selected["recent"][0]["team_a"] == "TT"
    assert SportsDashboard._result_label(selected["recent"][0]) == "TT 2-3 LGD"


def test_lpl_generic_playoff_stages_are_inferred_from_schedule_order():
    tz = timezone.utc
    events = [
        {
            "start": datetime(2026, 6, day, 9, 0, tzinfo=tz),
            "state": "unstarted",
            "team_a": team_a,
            "team_b": team_b,
            "block": "Playoffs",
        }
        for day, team_a, team_b in (
            (1, "QF1A", "QF1B"),
            (2, "QF2A", "QF2B"),
            (3, "SF1A", "SF1B"),
            (4, "SF2A", "SF2B"),
            (5, "FNL", "OPP"),
        )
    ]

    annotated = SportsDashboard._annotate_lpl_stage_labels(events)

    labels = {event["team_a"]: event["stage_label"] for event in annotated}
    assert labels["FNL"] == "Final"
    assert labels["SF2A"] == "Semi-Final"
    assert labels["SF1A"] == "Semi-Final"
    assert labels["QF2A"] == "Quarter-Final"


def test_lpl_generic_stage_respects_explicit_future_final():
    tz = timezone.utc
    events = [
        {
            "start": datetime(2026, 6, 12, 9, 0, tzinfo=tz),
            "state": "unstarted",
            "team_a": "EARLY",
            "team_b": "OPP",
            "block": "Playoffs",
        },
        {
            "start": datetime(2026, 6, 13, 9, 0, tzinfo=tz),
            "state": "unstarted",
            "team_a": "BLG",
            "team_b": "WE",
            "block": "Playoffs",
        },
        {
            "start": datetime(2026, 6, 14, 9, 0, tzinfo=tz),
            "state": "unstarted",
            "team_a": "TBD",
            "team_b": "TES",
            "block": "Finals",
        },
    ]

    annotated = SportsDashboard._annotate_lpl_stage_labels(events)

    labels = {event["team_a"]: event["stage_label"] for event in annotated}
    assert labels["TBD"] == "Final"
    assert labels["BLG"] == "Semi-Final"
    assert labels["EARLY"] == "Semi-Final"


def test_lpl_focus_stage_label_uses_stage_without_series_score():
    plugin = _plugin()
    image = Image.new("RGB", (320, 220), COLORS["paper"])
    draw = ImageDraw.Draw(image)
    seen_texts = []
    original_fit_text = plugin._fit_text

    def capture_fit_text(draw_obj, text, *args, **kwargs):
        seen_texts.append(str(text))
        return original_fit_text(draw_obj, text, *args, **kwargs)

    plugin._fit_text = capture_fit_text
    event = {
        "start": datetime(2026, 6, 13, 9, 0, tzinfo=timezone.utc),
        "state": "unstarted",
        "team_a": "BLG",
        "team_b": "WE",
        "team_a_logo": "",
        "team_b_logo": "",
        "wins_a": 0,
        "wins_b": 0,
        "block": "Playoffs",
        "stage_label": "Semi-Final",
    }

    plugin._draw_lpl_focus_card(image, draw, 0, 220, 0, event, event["start"], False)

    assert "Semi-Final" in seen_texts
    assert "0-0" not in seen_texts


def test_lpl_marble_filler_asset_is_exact_transparent_strip():
    with Image.open(LOCAL_LPL_MARBLE_FILLER_PATH) as source:
        filler = source.convert("RGBA")

    assert filler.size == (196, 46)
    assert filler.getchannel("A").getextrema()[0] == 0


def test_lpl_empty_upcoming_slot_draws_marble_filler(monkeypatch):
    plugin = _plugin()
    image = Image.new("RGB", (224, 420), COLORS["panel"])
    draw = ImageDraw.Draw(image)
    filler = Image.new("RGBA", (196, 46), (240, 10, 20, 255))
    event = {"start": datetime(2026, 6, 14, 2, 0, tzinfo=timezone.utc), "team_a": "TBD", "team_b": "TES"}

    monkeypatch.setattr(plugin, "_load_lpl_sidebar_filler", lambda size: filler.resize(size))
    monkeypatch.setattr(plugin, "_draw_lpl_next_row", lambda *_args, **_kwargs: None)

    plugin._draw_lpl_next_rows(image, draw, 0, 224, 244, [event], event["start"], False)

    assert image.getpixel((112, 345)) == (240, 10, 20)


def test_lpl_empty_upcoming_slot_stays_clear_with_two_rows(monkeypatch):
    plugin = _plugin()
    image = Image.new("RGB", (224, 420), COLORS["panel"])
    draw = ImageDraw.Draw(image)
    filler = Image.new("RGBA", (196, 46), (240, 10, 20, 255))
    event = {"start": datetime(2026, 6, 14, 2, 0, tzinfo=timezone.utc), "team_a": "TBD", "team_b": "TES"}

    monkeypatch.setattr(plugin, "_load_lpl_sidebar_filler", lambda size: filler.resize(size))
    monkeypatch.setattr(plugin, "_draw_lpl_next_row", lambda *_args, **_kwargs: None)

    plugin._draw_lpl_next_rows(image, draw, 0, 224, 244, [event, event], event["start"], False)

    assert image.getpixel((112, 345)) != (240, 10, 20)


def test_nba_espn_parser_uses_chinese_team_names_and_period_scores():
    la = ZoneInfo("America/Los_Angeles")
    events = SportsDashboard._parse_nba_espn_events(_sample_nba_scoreboard_payload(), la)

    assert events[0]["team_a"] == "\u9a6c\u523a"
    assert events[0]["team_b"] == "\u5c3c\u514b\u65af"
    assert events[0]["team_a_code"] == "SA"
    assert events[0]["wins_a"] == 106
    assert events[0]["wins_b"] == 112
    assert events[0]["series_wins_a"] == 0
    assert events[0]["series_wins_b"] == 2
    assert events[0]["period_scores_a"] == [25, 29, 24, 28]
    assert SportsDashboard._nba_period_label(events[0]) == "Q1 25-28  Q2 29-27  Q3 24-31  Q4 28-26"


def test_select_nba_events_returns_next_upcoming_and_recent_result():
    la = ZoneInfo("America/Los_Angeles")
    events = SportsDashboard._parse_nba_espn_events(_sample_nba_scoreboard_payload(), la)
    now = datetime(2026, 6, 6, 12, 0, tzinfo=la)

    selected = SportsDashboard._select_nba_events(events, now)

    assert selected["main"]["state"] == "unstarted"
    assert selected["upcoming"][0]["team_a"] == "\u9a6c\u523a"
    assert selected["recent"][0]["team_b"] == "\u5c3c\u514b\u65af"
    assert SportsDashboard._nba_score_label(selected["recent"][0]) == "106-112"


def test_nba_parser_propagates_latest_series_score_to_upcoming_game():
    la = ZoneInfo("America/Los_Angeles")
    payload = json.loads(json.dumps(_sample_nba_scoreboard_payload()))
    completed_series = payload["events"][0]["competitions"][0]["series"]["competitors"]
    upcoming_series = payload["events"][1]["competitions"][0]["series"]["competitors"]
    completed_series[0]["wins"] = 3
    completed_series[1]["wins"] = 1
    upcoming_series[0]["wins"] = 2
    upcoming_series[1]["wins"] = 1

    events = SportsDashboard._parse_nba_espn_events(payload, la)
    selected = SportsDashboard._select_nba_events(events, datetime(2026, 6, 6, 12, 0, tzinfo=la))

    assert selected["main"]["state"] == "unstarted"
    assert selected["main"]["team_a_code"] == "SA"
    assert selected["main"]["team_b_code"] == "NY"
    assert selected["main"]["series_wins_a"] == 1
    assert selected["main"]["series_wins_b"] == 3


def test_nba_scoreboard_live_cache_uses_short_refresh_window():
    plugin = _plugin()
    tmp_path = _sports_dashboard_tmp("nba_live_score_refresh")
    plugin._sports_dashboard_cache_dir = lambda: tmp_path
    la = ZoneInfo("America/Los_Angeles")
    settings = {"nbaCacheHours": "1", "nbaLiveRefreshSeconds": "180"}
    now_utc = datetime.now(timezone.utc)
    cache_key = plugin._nba_scoreboard_cache_key(settings, la, now_utc)

    cached_scoreboard = json.loads(json.dumps(_sample_nba_scoreboard_payload()))
    cached_status = cached_scoreboard["events"][0]["competitions"][0]["status"]["type"]
    cached_status["state"] = "in"
    cached_status["completed"] = False
    cached_status["description"] = "In Progress"
    cached_status["shortDetail"] = "4th Quarter"

    fresh_scoreboard = json.loads(json.dumps(cached_scoreboard))
    fresh_competitors = fresh_scoreboard["events"][0]["competitions"][0]["competitors"]
    fresh_competitors[0]["score"] = "118"
    fresh_competitors[1]["score"] = "111"
    fresh_payload = {
        "version": "sports-dashboard-nba-scoreboard-v1",
        "cache_key": cache_key,
        "fetched_at": now_utc.isoformat(),
        "range_start": "2026-06-01",
        "range_end": "2026-06-30",
        "scoreboard": fresh_scoreboard,
    }
    calls = []
    plugin._fetch_nba_scoreboard_payload = lambda *args, **kwargs: calls.append(args) or fresh_payload
    SportsDashboard._write_json_file(
        tmp_path / "nba_scoreboard.json",
        {
            "version": "sports-dashboard-nba-scoreboard-v1",
            "cache_key": cache_key,
            "fetched_at": (now_utc - timedelta(seconds=240)).isoformat(),
            "scoreboard": cached_scoreboard,
        },
    )

    payload, source_state, _fetched_at = plugin._load_nba_scoreboard(settings, la)

    assert calls
    assert source_state == "ESPN LIVE"
    assert payload["events"][0]["competitions"][0]["competitors"][0]["score"] == "118"


def test_nba_live_state_tracks_active_scoreboard_event():
    plugin = _plugin()
    tmp_path = _sports_dashboard_tmp("nba_live_state")
    plugin._sports_dashboard_cache_dir = lambda: tmp_path
    la = ZoneInfo("America/Los_Angeles")
    scoreboard = json.loads(json.dumps(_sample_nba_scoreboard_payload()))
    status = scoreboard["events"][0]["competitions"][0]["status"]["type"]
    status["state"] = "in"
    status["completed"] = False
    status["description"] = "In Progress"
    events = SportsDashboard._parse_nba_espn_events(scoreboard, la)
    selected = SportsDashboard._select_nba_events(events, events[0]["start"] + timedelta(hours=1))

    plugin._write_nba_live_state(selected, events[0]["start"] + timedelta(hours=1), "ESPN LIVE")
    state = json.loads((tmp_path / "nba_live_state.json").read_text(encoding="utf-8"))

    assert state["version"] == "sports-dashboard-nba-live-v1"
    assert state["has_live"] is True
    assert state["team_a"] == "\u9a6c\u523a"
    assert state["team_b"] == "\u5c3c\u514b\u65af"
    assert state["live_until"] == (events[0]["start"] + timedelta(hours=4)).astimezone(timezone.utc).isoformat()


def test_nba_odds_match_espn_event_with_chinese_team_names():
    plugin = _plugin()
    la = ZoneInfo("America/Los_Angeles")
    events = SportsDashboard._parse_nba_espn_events(_sample_nba_scoreboard_payload(), la)

    enriched = plugin._merge_nba_odds(events, [_sample_nba_odds_event()], la, {"nbaOddsBookmakers": "DraftKings"})
    upcoming = next(event for event in enriched if event["state"] == "unstarted")

    assert upcoming["team_a"] == "\u9a6c\u523a"
    assert upcoming["team_b"] == "\u5c3c\u514b\u65af"
    assert upcoming["odds"]["team_a"] == "2.05"
    assert upcoming["odds"]["team_b"] == "1.75"
    assert upcoming["odds"]["bookmaker"] == "DraftKings"


def test_nba_odds_api_io_match_espn_event():
    plugin = _plugin()
    la = ZoneInfo("America/Los_Angeles")
    events = SportsDashboard._parse_nba_espn_events(_sample_nba_scoreboard_payload(), la)

    enriched = plugin._merge_nba_odds(
        events,
        [_sample_nba_odds_api_io_event()],
        la,
        {"nbaOddsProvider": "oddsapiio", "nbaOddsBookmakers": "Bet365"},
    )
    upcoming = next(event for event in enriched if event["state"] == "unstarted")

    assert upcoming["odds"]["team_a"] == "2.10"
    assert upcoming["odds"]["team_b"] == "1.65"
    assert upcoming["odds"]["bookmaker"] == "Bet365"


def test_nba_odds_uses_fresh_cache_without_network():
    plugin = _plugin()
    tmp_path = _sports_dashboard_tmp("nba_odds_fresh_cache")
    plugin._sports_dashboard_cache_dir = lambda: tmp_path
    plugin._fetch_nba_odds_payload = lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("network called"))
    settings = {"nbaOddsCacheHours": "6"}
    cache_key = plugin._nba_odds_cache_key(settings, "secret")
    odds_event = _sample_nba_odds_event()
    SportsDashboard._write_json_file(
        tmp_path / "nba_odds.json",
        {
            "version": "sports-dashboard-nba-odds-v1",
            "cache_key": cache_key,
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "odds_events": [odds_event],
        },
    )

    odds_events, source_state, _fetched_at = plugin._load_nba_odds(settings, "secret")

    assert odds_events == [odds_event]
    assert source_state == "NBA ODDS CACHE"


def test_nba_odds_api_io_payload_fetches_event_ids_then_multi_odds():
    plugin = _plugin()
    tmp_path = _sports_dashboard_tmp("nba_odds_api_io_fetch")
    plugin._sports_dashboard_cache_dir = lambda: tmp_path
    calls = []

    def fake_get_json(path, params, settings, now_utc):
        calls.append((path, params))
        if path == "/events":
            return [{"id": 88112233}, {"id": 88112234}]
        return [_sample_nba_odds_api_io_event()]

    plugin._nba_odds_api_io_get_json = fake_get_json
    settings = {"nbaOddsProvider": "oddsapiio", "nbaOddsBookmakers": "Bet365"}

    payload = plugin._fetch_nba_odds_payload(settings, "secret", "cache", datetime.now(timezone.utc))

    assert payload["provider"] == "oddsapiio"
    assert payload["odds_events"] == [_sample_nba_odds_api_io_event()]
    assert calls[0][0] == "/events"
    assert calls[0][1]["sport"] == "basketball"
    assert calls[0][1]["league"] == "usa-nba-playoffs"
    assert calls[1][0] == "/odds/multi"
    assert calls[1][1]["eventIds"] == "88112233,88112234"


def test_nba_mini_match_row_renders_moneyline_odds():
    plugin = _plugin()
    image = Image.new("RGB", (240, 60), COLORS["paper"])
    draw = ImageDraw.Draw(image)
    la = ZoneInfo("America/Los_Angeles")
    events = SportsDashboard._parse_nba_espn_events(_sample_nba_scoreboard_payload(), la)
    event = plugin._merge_nba_odds(events, [_sample_nba_odds_event()], la, {"nbaOddsBookmakers": "DraftKings"})[1]
    odds_text = []
    odds_sizes = []
    logo_sizes = []
    team_sizes = []
    original_draw_odds_text = plugin._draw_nba_odds_text
    original_fit_text = plugin._fit_text

    def record_odds_text(draw, box, text, max_size=9, align="center"):
        if text:
            odds_text.append(text)
            odds_sizes.append(max_size)
        return original_draw_odds_text(draw, box, text, max_size=max_size, align=align)

    def record_fit_text(draw_arg, text, max_width, size, bold=False, min_size=11):
        if text in {event["team_a"], event["team_b"]}:
            team_sizes.append(size)
        return original_fit_text(draw_arg, text, max_width, size, bold=bold, min_size=min_size)

    def record_team_logo(_image, _draw, _logo_url, _x, _y, size, fallback_text):
        if fallback_text in {event["team_a"], event["team_b"]}:
            logo_sizes.append(size)

    plugin._draw_nba_odds_text = record_odds_text
    plugin._fit_text = record_fit_text
    plugin._draw_team_logo = record_team_logo

    plugin._draw_nba_mini_match_row(image, draw, 4, 236, 4, event, "VS", show_time=True)

    assert odds_text == ["2.05", "1.75"]
    assert odds_sizes == [8, 8]
    assert logo_sizes == [NBA_MINI_LINEUP_LOGO_SIZE, NBA_MINI_LINEUP_LOGO_SIZE]
    assert team_sizes == [NBA_MINI_LINEUP_ODDS_TEAM_FONT_SIZE, NBA_MINI_LINEUP_ODDS_TEAM_FONT_SIZE]


def test_nba_focus_card_renders_larger_moneyline_odds():
    plugin = _plugin()
    image = Image.new("RGB", (300, 190), COLORS["paper"])
    draw = ImageDraw.Draw(image)
    la = ZoneInfo("America/Los_Angeles")
    events = SportsDashboard._parse_nba_espn_events(_sample_nba_scoreboard_payload(), la)
    event = dict(plugin._merge_nba_odds(events, [_sample_nba_odds_event()], la, {"nbaOddsBookmakers": "DraftKings"})[1])
    event["team_a_logo"] = ""
    event["team_b_logo"] = ""
    odds_sizes = []
    original_draw_odds_text = plugin._draw_nba_odds_text

    def record_odds_text(draw, box, text, max_size=9, align="center"):
        if text:
            odds_sizes.append(max_size)
        return original_draw_odds_text(draw, box, text, max_size=max_size, align=align)

    plugin._draw_nba_odds_text = record_odds_text

    plugin._draw_nba_compact_main_card(image, draw, 4, 4, 276, 172, event, datetime.now(la), False)

    assert odds_sizes == [10, 10]


def test_nba_inline_list_team_names_use_larger_font():
    plugin = _plugin()
    image = Image.new("RGB", (240, 44), COLORS["paper"])
    draw = ImageDraw.Draw(image)
    event = {
        "team_a": "\u9a6c\u523a",
        "team_b": "\u5c3c\u514b\u65af",
        "team_a_logo": "",
        "team_b_logo": "",
    }
    fit_calls = []
    logo_sizes = []
    original_fit_text = plugin._fit_text
    original_draw_team_logo = plugin._draw_team_logo

    def record_fit_text(draw_arg, text, max_width, size, bold=False, min_size=11):
        fit_calls.append((text, size, min_size))
        return original_fit_text(draw_arg, text, max_width, size, bold=bold, min_size=min_size)

    def record_team_logo(image_arg, draw_arg, logo_url, x, y, size, fallback_text):
        logo_sizes.append(size)
        return original_draw_team_logo(image_arg, draw_arg, logo_url, x, y, size, fallback_text)

    plugin._fit_text = record_fit_text
    plugin._draw_team_logo = record_team_logo

    plugin._draw_nba_teams_inline(image, draw, 4, 236, 12, event, "VS")

    team_calls = [call for call in fit_calls if call[0] in {event["team_a"], event["team_b"]}]
    assert team_calls == [
        (event["team_a"], NBA_INLINE_TEAM_FONT_SIZE, NBA_INLINE_TEAM_MIN_FONT_SIZE),
        (event["team_b"], NBA_INLINE_TEAM_FONT_SIZE, NBA_INLINE_TEAM_MIN_FONT_SIZE),
    ]
    assert logo_sizes == [NBA_INLINE_LOGO_SIZE, NBA_INLINE_LOGO_SIZE]


def test_nba_header_court_strip_asset_renders_in_empty_header_space():
    plugin = _plugin()
    assert Path(LOCAL_NBA_COURT_STRIP_PATH).exists()
    with Image.open(LOCAL_NBA_COURT_STRIP_PATH) as strip:
        assert strip.size == (310, 38)
        assert "A" in strip.getbands()
        assert strip.getchannel("A").getextrema() == (0, 255)
        bottom_alpha = strip.getchannel("A").crop((0, strip.height - 1, strip.width, strip.height))
        assert sum(bottom_alpha.histogram()[1:]) > 250

    def render_header(colors):
        token = _ACTIVE_COLORS.set(colors)
        try:
            image = Image.new("RGB", (552, 268), COLORS["paper"])
            draw = ImageDraw.Draw(image)
            la = ZoneInfo("America/Los_Angeles")
            events = SportsDashboard._parse_nba_espn_events(_sample_nba_scoreboard_payload(), la)
            now = datetime(2026, 6, 6, 12, 0, tzinfo=la)
            selected = SportsDashboard._select_nba_events(events, now)
            plugin._load_team_logo = lambda _logo_url, _size: None

            plugin._draw_nba_compact_panel(image, draw, (0, 0, 551, 267), selected, "ESPN LIVE", now)
            return image
        finally:
            _ACTIVE_COLORS.reset(token)

    image = render_header(DAY_COLORS)
    pixels = image.load()
    dark_pixels = 0
    background_pixels = 0
    for y in range(10, 48):
        for x in range(150, 460):
            if pixels[x, y] == DAY_COLORS["text"]:
                dark_pixels += 1
            if pixels[x, y] not in (DAY_COLORS["text"], DAY_COLORS["panel"]):
                background_pixels += 1
    assert dark_pixels > 20
    assert background_pixels > 200

    image = render_header(DEEP_NIGHT_COLORS)
    pixels = image.load()
    light_pixels = 0
    background_pixels = 0
    for y in range(10, 48):
        for x in range(150, 460):
            if pixels[x, y] == DEEP_NIGHT_COLORS["text"]:
                light_pixels += 1
            if pixels[x, y] not in (DEEP_NIGHT_COLORS["text"], DEEP_NIGHT_COLORS["panel"]):
                background_pixels += 1
    assert light_pixels > 20
    assert background_pixels > 200


def test_nba_empty_slot_filler_asset_is_exact_slot_size():
    assert Path(LOCAL_NBA_EMPTY_SLOT_FILLER_PATH).exists()
    with Image.open(LOCAL_NBA_EMPTY_SLOT_FILLER_PATH) as source:
        filler = source.convert("RGB")

    assert filler.size == (257, 67)
    assert filler.getbbox() is not None
    assert len(filler.getcolors(maxcolors=257 * 67)) > 20


def test_nba_empty_slot_filler_preserves_aspect_ratio_when_short():
    with Image.open(LOCAL_NBA_EMPTY_SLOT_FILLER_PATH) as source:
        source = source.convert("RGBA")
        distorted = source.resize((257, 34), Image.LANCZOS)

    fitted = SportsDashboard._load_nba_empty_slot_filler((257, 34))

    assert fitted.size == (257, 34)
    assert fitted.tobytes() != distorted.tobytes()


def test_nba_recent_empty_slot_draws_filler(monkeypatch):
    plugin = _plugin()
    image = Image.new("RGB", (300, 150), COLORS["panel"])
    draw = ImageDraw.Draw(image)
    filler = Image.new("RGBA", (257, 67), (10, 220, 30, 255))
    event = {
        "start": datetime(2026, 6, 8, 17, 0, tzinfo=timezone.utc),
        "team_a": "NY",
        "team_b": "SA",
        "wins_a": 106,
        "wins_b": 107,
    }

    monkeypatch.setattr(plugin, "_load_nba_empty_slot_filler", lambda size: filler.resize(size))
    monkeypatch.setattr(plugin, "_draw_nba_mini_match_row", lambda *_args, **_kwargs: None)

    plugin._draw_nba_compact_recent_rows(image, draw, 10, 266, 10, 130, [event, event])

    assert image.getpixel((20, 100)) == (10, 220, 30)


def test_worldcup_header_banner_asset_renders_in_empty_header_space():
    plugin = _plugin()
    assert Path(LOCAL_WORLDCUP_HEADER_BANNER_PATH).exists()
    with Image.open(LOCAL_WORLDCUP_HEADER_BANNER_PATH) as banner:
        assert banner.size == (233, 40)
        assert "A" in banner.getbands()
        assert banner.getchannel("A").getextrema() == (0, 255)

    image = Image.new("RGB", (556, 208), COLORS["paper"])
    draw = ImageDraw.Draw(image)
    plugin._draw_worldcup_header_banner(image, 229, 8, 461, 47)

    pixels = image.load()
    changed_pixels = 0
    paper_pixels = 0
    for y in range(8, 48):
        for x in range(229, 462):
            if pixels[x, y] != COLORS["paper"]:
                changed_pixels += 1
            else:
                paper_pixels += 1
    assert changed_pixels > 900
    assert paper_pixels > 100


def test_live_lpl_event_becomes_now_playing_without_duplicate_rows():
    tz = timezone.utc
    live_event = {
        "start": datetime(2026, 6, 3, 9, 0, tzinfo=tz),
        "state": "inprogress",
        "team_a": "BLG",
        "team_b": "EDG",
        "team_a_logo": "",
        "team_b_logo": "",
        "wins_a": 1,
        "wins_b": 0,
        "block": "Playoffs",
    }
    next_event = {
        "start": datetime(2026, 6, 5, 9, 0, tzinfo=tz),
        "state": "unstarted",
        "team_a": "LGD",
        "team_b": "AL",
        "team_a_logo": "",
        "team_b_logo": "",
        "wins_a": None,
        "wins_b": None,
        "block": "Playoffs",
    }
    recent_event = {
        "start": datetime(2026, 6, 2, 9, 0, tzinfo=tz),
        "state": "completed",
        "team_a": "TT",
        "team_b": "LGD",
        "team_a_logo": "",
        "team_b_logo": "",
        "wins_a": 2,
        "wins_b": 3,
        "block": "Playoffs",
    }

    selected = SportsDashboard._select_lpl_events(
        [recent_event, live_event, next_event],
        datetime(2026, 6, 3, 10, 0, tzinfo=tz),
    )

    assert selected["main"] is live_event
    assert selected["live"] == [live_event]
    assert selected["upcoming"] == [next_event]
    assert selected["recent"] == [recent_event]
    assert SportsDashboard._lpl_focus_tag(True) == "NOW PLAYING"
    assert SportsDashboard._score_label(live_event) == "1-0"


def test_recent_zero_zero_lpl_match_is_inferred_live_during_match_window():
    tz = timezone.utc
    event = {
        "start": datetime(2026, 6, 3, 9, 0, tzinfo=tz),
        "state": "completed",
        "team_a": "BLG",
        "team_b": "EDG",
        "team_a_logo": "",
        "team_b_logo": "",
        "wins_a": 0,
        "wins_b": 0,
        "block": "Playoffs",
    }

    selected = SportsDashboard._select_lpl_events([event], datetime(2026, 6, 3, 9, 15, tzinfo=tz))

    assert selected["live"] == [event]
    assert selected["main"] is event
    assert selected["recent"] == []

    stale = SportsDashboard._select_lpl_events([event], datetime(2026, 6, 3, 16, 1, tzinfo=tz))
    assert stale["live"] == []
    assert stale["recent"] == [event]


def test_future_completed_zero_zero_lpl_match_stays_next_until_start():
    tz = timezone.utc
    event = {
        "start": datetime(2026, 6, 6, 9, 0, tzinfo=tz),
        "state": "completed",
        "team_a": "BLG",
        "team_b": "JDG",
        "team_a_logo": "",
        "team_b_logo": "",
        "wins_a": 0,
        "wins_b": 0,
        "best_of": 5,
        "block": "Playoffs",
    }
    next_event = {
        "start": datetime(2026, 6, 7, 9, 0, tzinfo=tz),
        "state": "unstarted",
        "team_a": "WE",
        "team_b": "TES",
        "team_a_logo": "",
        "team_b_logo": "",
        "wins_a": 0,
        "wins_b": 0,
        "best_of": 5,
        "block": "Playoffs",
    }

    selected = SportsDashboard._select_lpl_events(
        [event, next_event],
        datetime(2026, 6, 6, 8, 50, tzinfo=tz),
    )

    assert selected["live"] == []
    assert selected["main"] is event
    assert selected["upcoming"] == [event, next_event]
    assert selected["recent"] == []

    live = SportsDashboard._select_lpl_events([event, next_event], datetime(2026, 6, 6, 9, 5, tzinfo=tz))
    assert live["live"] == [event]
    assert live["main"] is event
    assert live["upcoming"] == [next_event]
    assert live["recent"] == []


def test_lpl_live_endpoint_polling_starts_in_pregame_window():
    tz = timezone.utc
    event = {
        "start": datetime(2026, 6, 6, 9, 0, tzinfo=tz),
        "state": "completed",
        "team_a": "BLG",
        "team_b": "JDG",
        "wins_a": 0,
        "wins_b": 0,
        "best_of": 5,
    }

    assert SportsDashboard._should_poll_lpl_live_endpoint([event], datetime(2026, 6, 6, 8, 45, tzinfo=tz))
    assert not SportsDashboard._should_poll_lpl_live_endpoint([event], datetime(2026, 6, 6, 8, 0, tzinfo=tz))


def test_partial_best_of_lpl_series_is_inferred_live_between_games():
    tz = timezone.utc
    event = {
        "start": datetime(2026, 6, 3, 9, 0, tzinfo=tz),
        "state": "completed",
        "team_a": "BLG",
        "team_b": "EDG",
        "team_a_logo": "",
        "team_b_logo": "",
        "wins_a": 1,
        "wins_b": 0,
        "best_of": 5,
        "block": "Playoffs",
    }

    selected = SportsDashboard._select_lpl_events([event], datetime(2026, 6, 3, 10, 45, tzinfo=tz))

    assert selected["live"] == [event]
    assert selected["main"] is event
    assert selected["recent"] == []
    assert SportsDashboard._score_label(event) == "1-0"


def test_completed_best_of_lpl_series_moves_to_recent_after_deciding_win():
    tz = timezone.utc
    event = {
        "start": datetime(2026, 6, 3, 9, 0, tzinfo=tz),
        "state": "completed",
        "team_a": "BLG",
        "team_b": "EDG",
        "team_a_logo": "",
        "team_b_logo": "",
        "wins_a": 2,
        "wins_b": 0,
        "best_of": 3,
        "block": "Playoffs",
    }

    selected = SportsDashboard._select_lpl_events([event], datetime(2026, 6, 3, 10, 45, tzinfo=tz))

    assert selected["live"] == []
    assert selected["main"] is event
    assert selected["recent"] == [event]


def test_lpl_realtime_info_reads_little_round_in_event_team_order():
    plugin = _plugin()
    event = {
        "event_id": "match-blg-edg",
        "team_a": "BLG",
        "team_b": "EDG",
        "wins_a": 1,
        "wins_b": 0,
        "best_of": 5,
    }
    plugin._fetch_lpl_event_details_payload = lambda event_id: {
        "data": {
            "event": {
                "match": {
                    "teams": [
                        {"id": "team-blg", "code": "BLG", "result": {"gameWins": 1}},
                        {"id": "team-edg", "code": "EDG", "result": {"gameWins": 0}},
                    ],
                    "games": [
                        {"number": 1, "id": "game-1", "state": "completed", "teams": []},
                        {
                            "number": 2,
                            "id": "game-2",
                            "state": "inProgress",
                            "teams": [
                                {"id": "team-edg", "side": "blue"},
                                {"id": "team-blg", "side": "red"},
                            ],
                        },
                    ],
                }
            }
        }
    }
    plugin._fetch_lpl_live_stats_window = lambda game_id: {
        "esportsGameId": game_id,
        "frames": [
            {
                "rfc460Timestamp": _fresh_lpl_frame_time(),
                "blueTeam": {"totalKills": 5},
                "redTeam": {"totalKills": 7},
            }
        ],
    }

    info = plugin._fetch_lpl_realtime_info(event)

    assert info["label"] == "Little Round"
    assert info["score"] == "7-5"
    assert info["game_id"] == "game-2"
    assert info["game_number"] == 2


def test_lpl_realtime_info_falls_back_to_stats_window_when_detail_games_lag():
    plugin = _plugin()
    event = {
        "event_id": "match-blg-jdg",
        "team_a": "BLG",
        "team_b": "JDG",
        "wins_a": 0,
        "wins_b": 0,
        "best_of": 5,
    }
    plugin._fetch_lpl_event_details_payload = lambda event_id: {
        "data": {
            "event": {
                "match": {
                    "teams": [
                        {"id": "team-blg", "code": "BLG", "result": {"gameWins": 0}},
                        {"id": "team-jdg", "code": "JDG", "result": {"gameWins": 0}},
                    ],
                    "games": [
                        {
                            "number": 1,
                            "id": "game-1",
                            "state": "unstarted",
                            "teams": [
                                {"id": "team-blg", "side": "blue"},
                                {"id": "team-jdg", "side": "red"},
                            ],
                        },
                        {"number": 2, "id": "game-2", "state": "unstarted", "teams": []},
                    ],
                }
            }
        }
    }
    plugin._fetch_lpl_live_stats_window = lambda game_id: {
        "esportsGameId": game_id,
        "frames": [
            {
                "rfc460Timestamp": _fresh_lpl_frame_time(),
                "blueTeam": {"totalKills": 2},
                "redTeam": {"totalKills": 1},
            }
        ],
    }

    info = plugin._fetch_lpl_realtime_info(event)

    assert info["label"] == "Little Round"
    assert info["score"] == "2-1"
    assert info["game_id"] == "game-1"
    assert info["game_number"] == 1


def test_lpl_realtime_info_falls_back_to_bo3_when_riot_frame_is_stale():
    plugin = _plugin()
    event = {
        "event_id": "match-blg-jdg",
        "start": datetime(2026, 6, 6, 9, 0, tzinfo=timezone.utc),
        "team_a": "BLG",
        "team_b": "JDG",
        "wins_a": 0,
        "wins_b": 1,
        "best_of": 5,
    }
    plugin._fetch_lpl_event_details_payload = lambda event_id: {
        "data": {
            "event": {
                "match": {
                    "teams": [
                        {"id": "team-blg", "code": "BLG", "result": {"gameWins": 0}},
                        {"id": "team-jdg", "code": "JDG", "result": {"gameWins": 1}},
                    ],
                    "games": [
                        {
                            "number": 2,
                            "id": "game-2",
                            "state": "inProgress",
                            "teams": [
                                {"id": "team-blg", "side": "blue"},
                                {"id": "team-jdg", "side": "red"},
                            ],
                        },
                    ],
                }
            }
        }
    }
    plugin._fetch_lpl_live_stats_window = lambda game_id: {
        "esportsGameId": game_id,
        "frames": [
            {
                "rfc460Timestamp": _fresh_lpl_frame_time(minutes_ago=20),
                "blueTeam": {"totalKills": 0},
                "redTeam": {"totalKills": 0},
            }
        ],
    }
    plugin._fetch_lpl_bo3_match_payload = lambda event: {
        "id": 116291,
        "slug": "jd-gaming-lol-vs-bilibili-gaming-lol-06-06-2026",
        "status": "current",
        "start_date": "2026-06-06T09:00:00.000+00:00",
        "team1_score": 0,
        "team2_score": 1,
        "team1": {"name": "JD Gaming", "slug": "jd-gaming-lol"},
        "team2": {"name": "Bilibili Gaming", "slug": "bilibili-gaming-lol"},
        "live_updates": {
            "team_1": {"game_score": 10, "match_score": 1},
            "team_2": {"game_score": 3, "match_score": 0},
            "game_number": 2,
        },
    }

    info = plugin._fetch_lpl_realtime_info(event)

    assert info["label"] == "Little Round"
    assert info["score"] == "3-10"
    assert info["game_id"] == "bo3:116291"
    assert info["game_number"] == 2
    assert info["source"] == "bo3.gg"


def test_lpl_realtime_info_hides_stale_riot_frame_when_bo3_is_disabled():
    plugin = _plugin()
    event = {
        "event_id": "match-blg-jdg",
        "team_a": "BLG",
        "team_b": "JDG",
        "wins_a": 0,
        "wins_b": 0,
        "best_of": 5,
    }
    plugin._fetch_lpl_event_details_payload = lambda event_id: {
        "data": {
            "event": {
                "match": {
                    "teams": [
                        {"id": "team-blg", "code": "BLG", "result": {"gameWins": 0}},
                        {"id": "team-jdg", "code": "JDG", "result": {"gameWins": 0}},
                    ],
                    "games": [
                        {
                            "number": 1,
                            "id": "game-1",
                            "state": "inProgress",
                            "teams": [
                                {"id": "team-blg", "side": "blue"},
                                {"id": "team-jdg", "side": "red"},
                            ],
                        }
                    ],
                }
            }
        }
    }
    plugin._fetch_lpl_live_stats_window = lambda game_id: {
        "esportsGameId": game_id,
        "frames": [
            {
                "rfc460Timestamp": _fresh_lpl_frame_time(minutes_ago=20),
                "blueTeam": {"totalKills": 0},
                "redTeam": {"totalKills": 0},
            }
        ],
    }
    plugin._fetch_lpl_bo3_match_payload = lambda event: (_ for _ in ()).throw(AssertionError("bo3 called"))

    info = plugin._fetch_lpl_realtime_info(event, {"lplBo3LiveApiEnabled": False})

    assert info is None


def test_lpl_realtime_info_shows_intermission_between_series_games():
    plugin = _plugin()
    event = {
        "event_id": "match-blg-edg",
        "team_a": "BLG",
        "team_b": "EDG",
        "wins_a": 1,
        "wins_b": 0,
        "best_of": 5,
    }
    plugin._fetch_lpl_event_details_payload = lambda event_id: {
        "data": {
            "event": {
                "match": {
                    "teams": [
                        {"id": "team-blg", "code": "BLG", "result": {"gameWins": 1}},
                        {"id": "team-edg", "code": "EDG", "result": {"gameWins": 0}},
                    ],
                    "games": [
                        {"number": 1, "id": "game-1", "state": "completed", "teams": []},
                        {"number": 2, "id": "game-2", "state": "unstarted", "teams": []},
                    ],
                }
            }
        }
    }
    plugin._fetch_lpl_live_stats_window = lambda game_id: (_ for _ in ()).throw(AssertionError("window called"))

    info = plugin._fetch_lpl_realtime_info(event)

    assert info == {"state": "intermission", "label": "Little Round", "score": "0-0"}


def test_lpl_bo3_completed_game_resets_little_round_for_intermission():
    event = {
        "start": datetime(2026, 6, 6, 9, 0, tzinfo=timezone.utc),
        "team_a": "BLG",
        "team_b": "JDG",
        "wins_a": 1,
        "wins_b": 0,
        "best_of": 5,
    }
    payload = {
        "id": 116291,
        "slug": "jd-gaming-lol-vs-bilibili-gaming-lol-06-06-2026",
        "start_date": "2026-06-06T09:00:00.000+00:00",
        "team1_score": 0,
        "team2_score": 1,
        "team1": {"name": "JD Gaming", "slug": "jd-gaming-lol"},
        "team2": {"name": "Bilibili Gaming", "slug": "bilibili-gaming-lol"},
        "live_updates": {
            "team_1": {"game_score": 23, "match_score": 0},
            "team_2": {"game_score": 6, "match_score": 1},
            "game_number": 1,
            "game_ended": True,
        },
    }

    info = SportsDashboard._lpl_little_round_from_bo3_payload(payload, event)

    assert info["state"] == "intermission"
    assert info["label"] == "Little Round"
    assert info["score"] == "0-0"
    assert info["game_id"] == "bo3:116291"
    assert info["game_number"] == 2
    assert info["source"] == "bo3.gg"


def test_lpl_live_state_file_marks_inferred_live_window():
    plugin = _plugin()
    tmp_path = _sports_dashboard_tmp("lpl_live_state")
    plugin._sports_dashboard_cache_dir = lambda: tmp_path
    now = datetime(2026, 6, 3, 9, 15, tzinfo=timezone.utc)
    event = {
        "event_id": "1122",
        "start": datetime(2026, 6, 3, 9, 0, tzinfo=timezone.utc),
        "state": "completed",
        "team_a": "BLG",
        "team_b": "EDG",
        "team_a_logo": "",
        "team_b_logo": "",
        "wins_a": 0,
        "wins_b": 0,
        "best_of": 5,
        "block": "Playoffs",
    }

    selected = SportsDashboard._select_lpl_events([event], now)
    plugin._write_lpl_live_state(selected, now, "LIVE DATA")

    state = json.loads((tmp_path / "lpl_live_state.json").read_text(encoding="utf-8"))
    assert state["version"] == "sports-dashboard-lpl-live-v1"
    assert state["has_live"] is True
    assert state["event_id"] == "1122"
    assert state["team_a"] == "BLG"
    assert state["team_b"] == "EDG"
    assert state["score"] == "0-0"
    assert state["best_of"] == 5
    assert state["live_until"] == "2026-06-03T15:00:00+00:00"


def test_lpl_live_endpoint_merge_replaces_matching_schedule_event():
    start = datetime(2026, 6, 3, 9, 0, tzinfo=timezone.utc)
    schedule_event = {
        "event_id": "100",
        "league_id": "98767991314006698",
        "start": start,
        "state": "completed",
        "team_a": "BLG",
        "team_b": "EDG",
        "wins_a": 0,
        "wins_b": 0,
    }
    live_event = {
        "event_id": "100",
        "league_id": "98767991314006698",
        "start": start,
        "state": "inProgress",
        "team_a": "BLG",
        "team_b": "EDG",
        "wins_a": 1,
        "wins_b": 0,
    }

    merged = SportsDashboard._merge_lpl_live_events([schedule_event], [live_event], "98767991314006698")

    assert len(merged) == 1
    assert merged[0]["state"] == "inProgress"
    assert merged[0]["wins_a"] == 1


def test_lpl_odds_match_lolesports_team_codes():
    plugin = _plugin()
    la = ZoneInfo("America/Los_Angeles")
    events = SportsDashboard._parse_lpl_events(_sample_payload(), la)

    enriched = plugin._merge_lpl_odds(
        events,
        [_sample_lpl_odds_api_io_event()],
        la,
        {"lplOddsBookmakers": "Bet365"},
    )

    assert enriched[1]["odds"]["team_a"] == "1.65"
    assert enriched[1]["odds"]["team_b"] == "2.10"
    assert enriched[1]["odds"]["bookmaker"] == "Bet365"
    assert "odds" not in enriched[0]


def test_lpl_odds_handles_reversed_lolesports_order():
    plugin = _plugin()
    la = ZoneInfo("America/Los_Angeles")
    event = next(
        item
        for item in SportsDashboard._fallback_lpl_events(la)
        if item["team_a"] == "LGD" and item["team_b"] == "AL"
    )

    enriched = plugin._merge_lpl_odds(
        [event],
        [
            _sample_lpl_odds_api_io_event(
                home="Anyones Legend",
                away="LGD Gaming",
                date="2026-06-05T09:00:00Z",
                home_odds="1.350",
                away_odds="3.200",
            )
        ],
        la,
        {"lplOddsBookmakers": "Bet365"},
    )

    assert enriched[0]["odds"]["team_a"] == "3.20"
    assert enriched[0]["odds"]["team_b"] == "1.35"


def test_lpl_odds_uses_fresh_cache_without_network():
    plugin = _plugin()
    tmp_path = _sports_dashboard_tmp("lpl_odds_fresh_cache")
    plugin._sports_dashboard_cache_dir = lambda: tmp_path
    plugin._fetch_lpl_odds_payload = lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("network called"))
    settings = {"lplOddsCacheHours": "12"}
    cache_key = plugin._lpl_odds_cache_key(settings, "secret")
    odds_event = _sample_lpl_odds_api_io_event()
    SportsDashboard._write_json_file(
        tmp_path / "lpl_odds.json",
        {
            "version": "sports-dashboard-lpl-odds-v1",
            "cache_key": cache_key,
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "odds_events": [odds_event],
        },
    )

    odds_events, source_state, _fetched_at = plugin._load_lpl_odds(settings, "secret")

    assert odds_events == [odds_event]
    assert source_state == "LPL ODDS CACHE"


def test_left_width_keeps_lpl_sidebar_usable():
    width = SportsDashboard._left_width({"worldCupLeftWidth": "680"}, (800, 480))

    assert width == 556


def test_worldcup_defaults_to_four_visible_matches():
    assert SportsDashboard._visible_worldcup_matches({}) == 4
    assert SportsDashboard._visible_worldcup_matches({"worldCupVisibleMatches": "7"}) == 4
    assert SportsDashboard._worldcup_capture_width({}, 800, 4) == 800
    assert SportsDashboard._worldcup_local_time_labels()[0] == "12:00"


def test_worldcup_group_points_labels_reserve_future_slots():
    assert SportsDashboard._worldcup_group_points_label({}, "a") == "PTS -"
    assert SportsDashboard._worldcup_group_points_label({"team_a_group_points": 3}, "a") == "PTS 3"
    assert SportsDashboard._worldcup_group_points_label({"group_points_b": "0"}, "b") == "PTS 0"
    assert SportsDashboard._worldcup_team_points_meta({"team_b_standing_points": 4}, "b") == "PTS 4"


def test_worldcup_live_state_file_tracks_active_match():
    plugin = _plugin()
    tmp_path = _sports_dashboard_tmp("worldcup_live_state")
    plugin._sports_dashboard_cache_dir = lambda: tmp_path
    now = datetime(2026, 6, 11, 12, 15, tzinfo=timezone.utc)
    event = {
        "event_id": "wc-mex-rsa",
        "start": datetime(2026, 6, 11, 12, 0, tzinfo=timezone.utc),
        "state": "IN_PLAY",
        "status": "In Play",
        "elapsed": 15,
        "team_a": "\u58a8\u897f\u54e5",
        "team_b": "\u5357\u975e",
        "wins_a": 1,
        "wins_b": 0,
        "block": "Group A",
    }
    selected = SportsDashboard._select_worldcup_event_sections([event], now, 4)

    plugin._write_worldcup_live_state(selected, now, "FOOTBALL LIVE")
    state = json.loads((tmp_path / "worldcup_live_state.json").read_text(encoding="utf-8"))

    assert state["version"] == "sports-dashboard-worldcup-live-v1"
    assert state["has_live"] is True
    assert state["team_a"] == "\u58a8\u897f\u54e5"
    assert state["team_b"] == "\u5357\u975e"
    assert state["score"] == "1-0"
    assert state["live_until"] == "2026-06-11T15:00:00+00:00"


def test_worldcup_fallback_renders_compact_four_match_list():
    plugin = _plugin()

    image = plugin._render_worldcup_fallback((800, 208), 4)

    assert image.size == (800, 208)
    assert image.getpixel((18, 64)) != COLORS["paper"]
    assert image.getpixel((30, 190)) != COLORS["paper"]


def test_worldcup_api_parser_converts_fixture_to_local_match_row():
    la = ZoneInfo("America/Los_Angeles")

    events = SportsDashboard._parse_worldcup_api_events([_sample_worldcup_fixture()], la)

    assert events[0]["start"].strftime("%Y-%m-%d %H:%M") == "2026-06-11 17:00"
    assert events[0]["team_a"] == "USA"
    assert events[0]["team_b"] == "MEX"
    assert events[0]["state"] == "NS"
    assert events[0]["block"] == "Group Stage - 1"
    assert events[0]["fixture_id"] == "10101"


def test_worldcup_lineups_attach_formation_summary_from_api_cache():
    plugin = _plugin()
    tmp_path = _sports_dashboard_tmp("worldcup_lineups")
    plugin._sports_dashboard_cache_dir = lambda: tmp_path
    la = ZoneInfo("America/Los_Angeles")
    event = SportsDashboard._parse_worldcup_api_events([_sample_worldcup_fixture()], la)[0]
    selected = {"main": event}

    def fake_api(path, params, api_key, settings, now_utc):
        assert path == "/fixtures/lineups"
        assert params == {"fixture": "10101"}
        return {
            "response": [
                {"team": {"name": "United States"}, "formation": "4-3-3"},
                {"team": {"name": "Mexico"}, "formation": "4-2-3-1"},
            ]
        }

    plugin._api_football_get_json = fake_api
    plugin._attach_worldcup_lineup_summary(
        selected,
        {"worldCupLineupCacheSeconds": "600"},
        "secret",
        la,
        datetime(2026, 6, 11, 16, 30, tzinfo=la),
    )

    assert event["formation_a"] == "4-3-3"
    assert event["formation_b"] == "4-2-3-1"
    assert event["lineups_ready"] is True


def test_worldcup_formation_pair_includes_team_labels():
    pair = SportsDashboard._worldcup_formation_pair(
        {
            "team_a": "\u58a8\u897f\u54e5",
            "team_b": "\u5357\u975e",
            "formation_a": "4-3-3",
            "formation_b": "4-2-3-1",
        }
    )

    assert pair == ("\u58a8\u897f\u54e5 4-3-3", "4-2-3-1 \u5357\u975e")

    pair = SportsDashboard._worldcup_formation_pair(
        {
            "team_a": "United States",
            "team_a_tla": "USA",
            "team_b": "Netherlands",
            "team_b_tla": "NED",
            "formation_a": "4-3-3",
            "formation_b": "3-4-2-1",
        }
    )

    assert pair == ("USA 4-3-3", "3-4-2-1 NED")


def test_worldcup_tactics_strip_draws_pitch_when_lineups_missing():
    plugin = _plugin()
    assert Path(LOCAL_WORLDCUP_PITCH_STRIP_PATH).exists()
    with Image.open(LOCAL_WORLDCUP_PITCH_STRIP_PATH) as pitch_strip:
        assert pitch_strip.size == (248, 13)
        pitch_strip = pitch_strip.convert("RGB")
        left_goal_pixels = 0
        right_goal_pixels = 0
        for y in range(pitch_strip.height):
            for x in range(0, 32):
                if pitch_strip.getpixel((x, y)) == (255, 255, 255):
                    left_goal_pixels += 1
            for x in range(pitch_strip.width - 32, pitch_strip.width):
                if pitch_strip.getpixel((x, y)) == (255, 255, 255):
                    right_goal_pixels += 1
        assert left_goal_pixels > 0
        assert right_goal_pixels > 0

    image = Image.new("RGB", (340, 18), COLORS["paper"])
    draw = ImageDraw.Draw(image)

    plugin._draw_worldcup_tactics_strip(image, draw, 0, 339, 0, 17, {})

    pixels = image.load()
    white_pixels = 0
    dark_pixels = 0
    left_edge_pixels = 0
    right_edge_pixels = 0
    for y in range(2, 16):
        for x in range(8, 332):
            if pixels[x, y] == (255, 255, 255):
                white_pixels += 1
            if pixels[x, y] == (0, 0, 0):
                dark_pixels += 1
    for y in range(2, 16):
        for x in range(8, 42):
            if pixels[x, y] == (255, 255, 255):
                left_edge_pixels += 1
        for x in range(298, 332):
            if pixels[x, y] == (255, 255, 255):
                right_edge_pixels += 1
    assert white_pixels > 10
    assert dark_pixels > 10
    assert left_edge_pixels > 0
    assert right_edge_pixels > 0
    assert pixels[337, 8] in {(0, 0, 0), (255, 255, 255)}
    bottom_line_pixels = 0
    for x in range(8, 338):
        if pixels[x, 17] == (255, 255, 255):
            bottom_line_pixels += 1
    assert bottom_line_pixels > 120


def test_worldcup_api_key_can_come_from_device_env_alias():
    device_config = FakeDeviceConfig()
    device_config.env["World_CUP"] = "secret"

    assert SportsDashboard._api_sports_key({}, device_config) == "secret"


def test_football_data_key_can_come_from_device_env_alias():
    device_config = FakeDeviceConfig()
    device_config.env["FOOTBALL_DATA"] = "secret"

    assert SportsDashboard._football_data_key({}, device_config) == "secret"


def test_worldcup_odds_key_can_come_from_device_env_alias():
    device_config = FakeDeviceConfig()
    device_config.env["THE_ODDS_API_KEY"] = "secret"

    assert SportsDashboard._the_odds_api_key({}, device_config) == "secret"
    assert SportsDashboard._worldcup_odds_api_key({}, device_config, "theoddsapi") == "secret"


def test_worldcup_odds_provider_auto_detects_odds_api_io_env_key():
    device_config = FakeDeviceConfig()
    device_config.env["ODDS_API_IO_KEY"] = "secret"

    assert SportsDashboard._worldcup_odds_provider({}, device_config) == "oddsapiio"
    assert SportsDashboard._worldcup_odds_api_key({}, device_config, "oddsapiio") == "secret"


def test_worldcup_the_odds_api_provider_does_not_reuse_odds_api_io_key():
    device_config = FakeDeviceConfig()
    device_config.env["ODDS_API_IO_KEY"] = "secret"

    assert SportsDashboard._worldcup_odds_api_key({}, device_config, "theoddsapi") == ""


def test_odds_api_io_live_alias_takes_priority_over_old_uppercase_key():
    device_config = FakeDeviceConfig()
    device_config.env["ODDS_API_IO_KEY"] = "old-secret"
    device_config.env["Odds_API_IO_KEY"] = "new-secret"

    assert SportsDashboard._worldcup_odds_provider({}, device_config) == "oddsapiio"
    assert SportsDashboard._worldcup_odds_api_key({}, device_config, "oddsapiio") == "new-secret"
    assert SportsDashboard._nba_odds_api_key({}, device_config, "oddsapiio") == "new-secret"
    assert SportsDashboard._lpl_odds_api_key({}, device_config) == "new-secret"


def test_odds_api_io_legacy_league_slugs_map_to_current_feed_slugs():
    assert (
        SportsDashboard._worldcup_odds_api_io_league({"worldCupOddsApiIoLeague": "international-world-cup"})
        == "international-fifa-world-cup"
    )
    assert SportsDashboard._nba_odds_api_io_league({"nbaOddsApiIoLeague": "usa-nba"}) == "usa-nba-playoffs"
    assert (
        SportsDashboard._lpl_odds_api_io_league({"lplOddsApiIoLeague": "league-of-legends-lpl"})
        == "league-of-legends-split-2"
    )


def test_nba_odds_provider_auto_detects_odds_api_io_env_key():
    device_config = FakeDeviceConfig()
    device_config.env["ODDS_API_IO_KEY"] = "secret"

    assert SportsDashboard._nba_odds_provider({}, device_config) == "oddsapiio"
    assert SportsDashboard._nba_odds_api_key({}, device_config, "oddsapiio") == "secret"


def test_nba_the_odds_api_provider_does_not_reuse_odds_api_io_key():
    device_config = FakeDeviceConfig()
    device_config.env["ODDS_API_IO_KEY"] = "secret"

    assert SportsDashboard._nba_odds_api_key({}, device_config, "theoddsapi") == ""


def test_football_data_parser_uses_chinese_country_names_and_flat_flags():
    la = ZoneInfo("America/Los_Angeles")

    events = SportsDashboard._parse_football_data_events([_sample_football_data_match()], la)

    assert events[0]["start"].strftime("%Y-%m-%d %H:%M") == "2026-06-11 12:00"
    assert events[0]["team_a"] == "墨西哥"
    assert events[0]["team_b"] == "南非"
    assert events[0]["team_a_flag"] == "https://flagsapi.com/MX/flat/64.png"
    assert events[0]["team_b_flag"] == "https://flagsapi.com/ZA/flat/64.png"
    assert events[0]["block"] == "Group A"
    assert "Mexico" in events[0]["team_a_source_aliases"]


def test_football_data_uses_fresh_cache_without_network():
    plugin = _plugin()
    tmp_path = _sports_dashboard_tmp("football_data_fresh_cache")
    plugin._sports_dashboard_cache_dir = lambda: tmp_path
    plugin._fetch_football_data_payload = lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("network called"))
    settings = {"footballDataKey": "secret", "footballDataCacheHours": "6"}
    la = ZoneInfo("America/Los_Angeles")
    cache_key = plugin._football_data_cache_key(settings, "secret", la)
    match = _sample_football_data_match()
    SportsDashboard._write_json_file(
        tmp_path / "football_data_worldcup.json",
        {
            "version": "sports-dashboard-football-data-v1",
            "cache_key": cache_key,
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "matches": [match],
        },
    )

    matches, source_state, _fetched_at = plugin._load_football_data_matches(settings, "secret", la)

    assert matches == [match]
    assert source_state == "FOOTBALL CACHE"


def test_football_data_force_refresh_bypasses_fresh_cache():
    plugin = _plugin()
    tmp_path = _sports_dashboard_tmp("football_data_force_refresh")
    plugin._sports_dashboard_cache_dir = lambda: tmp_path
    settings = {"footballDataKey": "secret", "footballDataCacheHours": "6", "forceRefresh": "true"}
    la = ZoneInfo("America/Los_Angeles")
    cache_key = plugin._football_data_cache_key(settings, "secret", la)
    cached_match = _sample_football_data_match()
    live_match = {
        **_sample_football_data_match(),
        "utcDate": "2026-06-12T22:00:00Z",
    }
    SportsDashboard._write_json_file(
        tmp_path / "football_data_worldcup.json",
        {
            "version": "sports-dashboard-football-data-v1",
            "cache_key": cache_key,
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "matches": [cached_match],
        },
    )
    plugin._fetch_football_data_payload = lambda *args, **kwargs: {
        "version": "sports-dashboard-football-data-v1",
        "cache_key": cache_key,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "matches": [live_match],
    }

    matches, source_state, _fetched_at = plugin._load_football_data_matches(settings, "secret", la)

    assert matches == [live_match]
    assert source_state == "FOOTBALL LIVE"


def test_football_data_live_cache_uses_short_refresh_window():
    plugin = _plugin()
    tmp_path = _sports_dashboard_tmp("football_data_live_cache")
    plugin._sports_dashboard_cache_dir = lambda: tmp_path
    settings = {"footballDataKey": "secret", "footballDataCacheHours": "6", "worldCupLiveRefreshSeconds": "180"}
    la = ZoneInfo("America/Los_Angeles")
    now_utc = datetime.now(timezone.utc)
    cache_key = plugin._football_data_cache_key(settings, "secret", la)
    cached_match = {**_sample_football_data_match(), "status": "IN_PLAY"}
    fresh_match = {
        **cached_match,
        "score": {"fullTime": {"home": 1, "away": 0}},
    }
    calls = []
    plugin._fetch_football_data_payload = lambda *args, **kwargs: calls.append(args) or {
        "version": "sports-dashboard-football-data-v1",
        "cache_key": cache_key,
        "fetched_at": now_utc.isoformat(),
        "matches": [fresh_match],
    }
    SportsDashboard._write_json_file(
        tmp_path / "football_data_worldcup.json",
        {
            "version": "sports-dashboard-football-data-v1",
            "cache_key": cache_key,
            "fetched_at": (now_utc - timedelta(seconds=240)).isoformat(),
            "matches": [cached_match],
        },
    )

    matches, source_state, _fetched_at = plugin._load_football_data_matches(settings, "secret", la)

    assert calls
    assert matches == [fresh_match]
    assert source_state == "FOOTBALL LIVE"


def test_worldcup_api_uses_fresh_cache_without_network():
    plugin = _plugin()
    tmp_path = _sports_dashboard_tmp("fresh_cache")
    plugin._sports_dashboard_cache_dir = lambda: tmp_path
    plugin._fetch_worldcup_api_payload = lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("network called"))
    settings = {"apiSportsKey": "secret", "worldCupApiCacheHours": "6"}
    la = ZoneInfo("America/Los_Angeles")
    cache_key = plugin._worldcup_api_cache_key(settings, "secret", la)
    fixture = _sample_worldcup_fixture()
    SportsDashboard._write_json_file(
        tmp_path / "worldcup_api.json",
        {
            "version": "sports-dashboard-api-v1",
            "cache_key": cache_key,
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "fixtures": [fixture],
        },
    )

    fixtures, source_state, _fetched_at = plugin._load_worldcup_api_fixtures(settings, "secret", la)

    assert fixtures == [fixture]
    assert source_state == "API CACHE"


def test_worldcup_api_live_cache_uses_short_refresh_window():
    plugin = _plugin()
    tmp_path = _sports_dashboard_tmp("worldcup_api_live_cache")
    plugin._sports_dashboard_cache_dir = lambda: tmp_path
    settings = {"apiSportsKey": "secret", "worldCupApiCacheHours": "6", "worldCupLiveRefreshSeconds": "180"}
    la = ZoneInfo("America/Los_Angeles")
    now_utc = datetime.now(timezone.utc)
    cache_key = plugin._worldcup_api_cache_key(settings, "secret", la)
    cached_fixture = {
        **_sample_worldcup_fixture(),
        "fixture": {
            **_sample_worldcup_fixture()["fixture"],
            "status": {"short": "1H", "long": "First Half", "elapsed": 22},
        },
        "goals": {"home": 0, "away": 0},
    }
    fresh_fixture = {
        **cached_fixture,
        "goals": {"home": 1, "away": 0},
    }
    calls = []
    plugin._fetch_worldcup_api_payload = lambda *args, **kwargs: calls.append(args) or {
        "version": "sports-dashboard-api-v1",
        "cache_key": cache_key,
        "fetched_at": now_utc.isoformat(),
        "fixtures": [fresh_fixture],
    }
    SportsDashboard._write_json_file(
        tmp_path / "worldcup_api.json",
        {
            "version": "sports-dashboard-api-v1",
            "cache_key": cache_key,
            "fetched_at": (now_utc - timedelta(seconds=240)).isoformat(),
            "fixtures": [cached_fixture],
        },
    )

    fixtures, source_state, _fetched_at = plugin._load_worldcup_api_fixtures(settings, "secret", la)

    assert calls
    assert fixtures == [fresh_fixture]
    assert source_state == "API LIVE"


def test_worldcup_api_daily_limit_uses_stale_cache_without_network():
    plugin = _plugin()
    tmp_path = _sports_dashboard_tmp("daily_limit")
    plugin._sports_dashboard_cache_dir = lambda: tmp_path
    plugin._fetch_worldcup_api_payload = lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("network called"))
    settings = {"apiSportsKey": "secret", "worldCupApiCacheHours": "1", "worldCupApiDailyLimit": "1"}
    la = ZoneInfo("America/Los_Angeles")
    cache_key = plugin._worldcup_api_cache_key(settings, "secret", la)
    fixture = _sample_worldcup_fixture()
    SportsDashboard._write_json_file(
        tmp_path / "worldcup_api.json",
        {
            "version": "sports-dashboard-api-v1",
            "cache_key": cache_key,
            "fetched_at": (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat(),
            "fixtures": [fixture],
        },
    )
    SportsDashboard._write_json_file(
        tmp_path / "api_state.json",
        {"date": datetime.now(timezone.utc).date().isoformat(), "count": 1},
    )

    fixtures, source_state, _fetched_at = plugin._load_worldcup_api_fixtures(settings, "secret", la)

    assert fixtures == [fixture]
    assert source_state == "API STALE"


def test_worldcup_api_free_plan_error_is_negative_cached():
    plugin = _plugin()
    tmp_path = _sports_dashboard_tmp("free_plan_block")
    plugin._sports_dashboard_cache_dir = lambda: tmp_path
    settings = {"apiSportsKey": "secret", "worldCupApiCacheHours": "6", "worldCupApiDailyLimit": "12"}
    la = ZoneInfo("America/Los_Angeles")

    plugin._fetch_worldcup_api_payload = lambda *args, **kwargs: (_ for _ in ()).throw(
        RuntimeError("API-Sports returned errors: {'plan': 'Free plans do not have access to this season'}")
    )
    fixtures, source_state, _fetched_at = plugin._load_worldcup_api_fixtures(settings, "secret", la)

    assert fixtures == []
    assert source_state == "API BLOCKED"

    plugin._fetch_worldcup_api_payload = lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("network called"))
    fixtures, source_state, _fetched_at = plugin._load_worldcup_api_fixtures(settings, "secret", la)

    assert fixtures == []
    assert source_state == "API BLOCKED"


def test_worldcup_api_force_refresh_bypasses_negative_cache():
    plugin = _plugin()
    tmp_path = _sports_dashboard_tmp("force_refresh_api_block")
    plugin._sports_dashboard_cache_dir = lambda: tmp_path
    settings = {"apiSportsKey": "secret", "worldCupApiCacheHours": "6", "worldCupApiDailyLimit": "12", "forceRefresh": "true"}
    la = ZoneInfo("America/Los_Angeles")
    cache_key = plugin._worldcup_api_cache_key(settings, "secret", la)
    fixture = _sample_worldcup_fixture()
    SportsDashboard._write_json_file(
        tmp_path / "worldcup_api.json",
        {
            "version": "sports-dashboard-api-v1",
            "cache_key": cache_key,
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "blocked_until": (datetime.now(timezone.utc) + timedelta(hours=6)).isoformat(),
            "source_state": "API BLOCKED",
            "error": "Free plans do not have access to this season",
            "fixtures": [],
        },
    )
    plugin._fetch_worldcup_api_payload = lambda *args, **kwargs: {
        "version": "sports-dashboard-api-v1",
        "cache_key": cache_key,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "fixtures": [fixture],
    }

    fixtures, source_state, _fetched_at = plugin._load_worldcup_api_fixtures(settings, "secret", la)

    assert fixtures == [fixture]
    assert source_state == "API LIVE"


def test_worldcup_odds_uses_fresh_cache_without_network():
    plugin = _plugin()
    tmp_path = _sports_dashboard_tmp("odds_fresh_cache")
    plugin._sports_dashboard_cache_dir = lambda: tmp_path
    plugin._fetch_worldcup_odds_payload = lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("network called"))
    settings = {"theOddsApiKey": "secret", "worldCupOddsCacheHours": "6"}
    cache_key = plugin._worldcup_odds_cache_key(settings, "secret")
    odds_event = _sample_worldcup_odds_event()
    SportsDashboard._write_json_file(
        tmp_path / "worldcup_odds.json",
        {
            "version": "sports-dashboard-worldcup-odds-v1",
            "cache_key": cache_key,
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "odds_events": [odds_event],
        },
    )

    odds_events, source_state, _fetched_at = plugin._load_worldcup_odds(settings, "secret")

    assert odds_events == [odds_event]
    assert source_state == "ODDS CACHE"


def test_worldcup_odds_match_football_data_event_with_localized_country_names():
    plugin = _plugin()
    la = ZoneInfo("America/Los_Angeles")
    events = SportsDashboard._parse_football_data_events([_sample_football_data_match()], la)

    enriched = plugin._merge_worldcup_odds(events, [_sample_worldcup_odds_event()], la, {})

    assert enriched[0]["odds"]["team_a"] == "1.80"
    assert enriched[0]["odds"]["draw"] == "3.25"
    assert enriched[0]["odds"]["team_b"] == "4.50"


def test_worldcup_odds_api_io_match_football_data_event():
    plugin = _plugin()
    la = ZoneInfo("America/Los_Angeles")
    events = SportsDashboard._parse_football_data_events([_sample_football_data_match()], la)

    enriched = plugin._merge_worldcup_odds(
        events,
        [_sample_worldcup_odds_api_io_event()],
        la,
        {"worldCupOddsProvider": "oddsapiio", "worldCupOddsBookmakers": "Bet365"},
    )

    assert enriched[0]["odds"]["team_a"] == "1.40"
    assert enriched[0]["odds"]["draw"] == "4.50"
    assert enriched[0]["odds"]["team_b"] == "8.00"


def test_worldcup_odds_normalizes_ampersand_country_aliases():
    assert (
        SportsDashboard._normalize_odds_team_name("Bosnia & Herzegovina")
        == SportsDashboard._normalize_odds_team_name("Bosnia and Herzegovina")
    )
    assert (
        SportsDashboard._normalize_odds_team_name("Bosnia-Herzegovina")
        == SportsDashboard._normalize_odds_team_name("Bosnia and Herzegovina")
    )


def test_worldcup_odds_api_io_payload_fetches_event_ids_then_multi_odds():
    plugin = _plugin()
    tmp_path = _sports_dashboard_tmp("odds_api_io_fetch")
    plugin._sports_dashboard_cache_dir = lambda: tmp_path
    calls = []

    def fake_get_json(path, params, settings, now_utc):
        calls.append((path, params))
        if path == "/events":
            return [{"id": 66456904}, {"id": 66456906}]
        return [_sample_worldcup_odds_api_io_event()]

    plugin._odds_api_io_get_json = fake_get_json
    settings = {"worldCupOddsProvider": "oddsapiio", "worldCupOddsBookmakers": "Bet365"}

    payload = plugin._fetch_worldcup_odds_payload(settings, "secret", "cache", datetime.now(timezone.utc))

    assert payload["provider"] == "oddsapiio"
    assert payload["odds_events"] == [_sample_worldcup_odds_api_io_event()]
    assert calls[0][0] == "/events"
    assert calls[0][1]["league"] == "international-fifa-world-cup"
    assert calls[1][0] == "/odds/multi"
    assert calls[1][1]["eventIds"] == "66456904,66456906"


def test_generate_image_builds_top_worldcup_panel_with_lpl_and_nba_below():
    plugin = _plugin()
    la = ZoneInfo("America/Los_Angeles")
    plugin._try_worldcup_football_data_panel = lambda *args, **kwargs: None
    plugin._try_worldcup_api_panel = lambda *args, **kwargs: None
    plugin._take_worldcup_screenshot = lambda settings, dimensions, timezone_name, visible_matches: Image.new("RGB", dimensions, (1, 2, 3))
    plugin._load_lpl_events = lambda settings, timezone_info: (
        SportsDashboard._parse_lpl_events(_sample_payload(), la),
        "LIVE DATA",
    )
    plugin._attach_lpl_odds = lambda events, *_args: events
    plugin._load_nba_events = lambda settings, timezone_info: (
        SportsDashboard._parse_nba_espn_events(_sample_nba_scoreboard_payload(), la),
        "ESPN LIVE",
    )
    plugin._attach_lpl_realtime_info = lambda selected, settings: selected
    plugin._write_nba_live_state = lambda selected, now, source_state: None
    plugin._write_lpl_live_state = lambda selected, now, source_state: None
    plugin._load_team_logo = lambda logo_url, size: None

    image = plugin.generate_image(
        {"worldCupTopHeight": "208", "overlayWorldCupLocalTimes": "false"},
        FakeDeviceConfig(),
    )

    assert image.size == (800, 480)
    assert image.mode == "RGB"
    assert image.getpixel((10, 10)) != (1, 2, 3)
    assert image.getpixel((10, 190)) != (1, 2, 3)
    assert image.getpixel((10, 230)) != (1, 2, 3)
    assert image.getpixel((360, 230)) != (1, 2, 3)
    assert image.getpixel((560, 230)) != (1, 2, 3)


def test_worldcup_panel_preserves_screenshot_aspect_ratio():
    plugin = _plugin()

    panel, content_box = plugin._prepare_worldcup_panel(
        Image.new("RGB", (400, 200), (1, 2, 3)),
        (540, 480),
        3,
    )

    assert panel.size == (540, 480)
    assert content_box == (0, 0, 540, 270)
    assert panel.getpixel((20, 20)) == (1, 2, 3)
    assert panel.getpixel((20, 300)) != (1, 2, 3)


def test_worldcup_api_panel_renders_flat_flag_matchup():
    plugin = _plugin()
    plugin._load_flag_image = lambda _url, size: Image.new("RGBA", size, (0, 92, 185, 255))
    la = ZoneInfo("America/Los_Angeles")
    events = plugin._merge_worldcup_odds(
        SportsDashboard._parse_football_data_events([_sample_football_data_match()], la),
        [_sample_worldcup_odds_event()],
        la,
        {},
    )

    image = plugin._render_worldcup_api_panel(
        (552, 480),
        events,
        "FOOTBALL LIVE",
        datetime.now(timezone.utc).isoformat(),
        1,
        datetime(2026, 6, 10, 12, 0, tzinfo=la),
    )

    assert image.size == (552, 480)
    assert SportsDashboard._worldcup_event_status_label(events[0], datetime(2026, 6, 10, 12, 0, tzinfo=la)) == "12:00"
    assert SportsDashboard._worldcup_event_time_label(events[0]) == "12:00"
    regions = SportsDashboard._worldcup_row_regions(552)
    date_range, time_range = SportsDashboard._worldcup_right_info_x_ranges(552)
    assert regions["group"][1] < regions["match"][0] < regions["match"][1] < time_range[0]
    assert time_range[0] < date_range[0]
    assert SportsDashboard._worldcup_matchup_row_offset(54) == 16
    assert image.getpixel((28, 92)) != COLORS["paper"]
    assert image.getpixel((65, 123)) == (0, 92, 185)
    assert image.getpixel((214, 124)) != COLORS["paper"]


def test_worldcup_compact_api_odds_stay_inside_each_match_row():
    plugin = _plugin()
    plugin._load_flag_image = lambda _url, size: Image.new("RGBA", size, (0, 92, 185, 255))
    la = ZoneInfo("America/Los_Angeles")
    base_event = plugin._merge_worldcup_odds(
        SportsDashboard._parse_football_data_events([_sample_football_data_match()], la),
        [_sample_worldcup_odds_event()],
        la,
        {},
    )[0]
    events = []
    for index, (team_a, team_b) in enumerate(
        [
            ("\u58a8\u897f\u54e5", "\u5357\u975e"),
            ("\u97e9\u56fd", "\u6377\u514b"),
            ("\u52a0\u62ff\u5927", "\u6ce2\u9ed1"),
            ("\u7f8e\u56fd", "\u5df4\u62c9\u572d"),
        ]
    ):
        event = dict(base_event)
        event["team_a"] = team_a
        event["team_b"] = team_b
        event["team_a_tla"] = team_a[:2]
        event["team_b_tla"] = team_b[:2]
        event["start"] = base_event["start"] + timedelta(days=index)
        event["odds"] = dict(base_event["odds"])
        events.append(event)

    odds_boxes = []
    original_draw_odds_text = plugin._draw_worldcup_odds_text

    def record_odds_text(draw, box, text, max_size=11):
        if text:
            odds_boxes.append(tuple(int(value) for value in box))
        return original_draw_odds_text(draw, box, text, max_size=max_size)

    plugin._draw_worldcup_odds_text = record_odds_text
    image = plugin._render_worldcup_api_panel(
        (552, 208),
        events,
        "FOOTBALL LIVE",
        datetime.now(timezone.utc).isoformat(),
        4,
        datetime(2026, 6, 10, 12, 0, tzinfo=la),
    )

    assert image.size == (552, 208)
    assert len(odds_boxes) == 12
    for box in odds_boxes:
        assert 0 <= box[0] < box[2] <= 552
        assert 57 <= box[1] < box[3] <= 208


def test_worldcup_compact_row_uses_larger_flags():
    plugin = _plugin()
    la = ZoneInfo("America/Los_Angeles")
    event = plugin._merge_worldcup_odds(
        SportsDashboard._parse_football_data_events([_sample_football_data_match()], la),
        [_sample_worldcup_odds_event()],
        la,
        {},
    )[0]
    image = Image.new("RGB", (260, 40), COLORS["paper"])
    draw = ImageDraw.Draw(image)
    flag_sizes = []

    def record_flag(_image, _draw, _flag_url, _x, _y, width, height, _fallback):
        flag_sizes.append((width, height))

    plugin._draw_worldcup_flag = record_flag

    plugin._draw_worldcup_row_lineup(image, draw, 4, 256, 14, event, "VS")

    assert flag_sizes == [(18, 12), (18, 12)]


def test_forced_night_theme_uses_deep_night_palette_without_leaking():
    plugin = _plugin()
    plugin._try_worldcup_football_data_panel = lambda _settings, _device_config, dimensions, *_args: Image.new(
        "RGB", dimensions, COLORS["paper"]
    )
    plugin._try_worldcup_api_panel = lambda *args, **kwargs: None
    plugin._take_worldcup_screenshot = lambda *args, **kwargs: None
    plugin._load_lpl_events = lambda _settings, timezone_info: (
        SportsDashboard._fallback_lpl_events(timezone_info),
        "LIVE DATA",
    )
    plugin._attach_lpl_odds = lambda events, *_args: events
    plugin._attach_lpl_realtime_info = lambda selected, settings: selected
    plugin._write_nba_live_state = lambda selected, now, source_state: None
    plugin._write_lpl_live_state = lambda selected, now, source_state: None
    plugin._load_nba_events = lambda _settings, timezone_info: (
        SportsDashboard._fallback_nba_events(timezone_info),
        "ESPN CACHE",
    )
    plugin._load_team_logo = lambda _logo_url, _size: None

    image = plugin.generate_image(
        {"sportsDashboardTheme": "night", "localTimezone": "UTC", "worldCupTopHeight": "208"},
        FakeDeviceConfig(timezone="UTC"),
    )

    assert max(image.getpixel((620, 120))) < 90
    assert image.getpixel((620, 120)) != DAY_COLORS["paper"]
    assert DEEP_NIGHT_COLORS["paper"] != DAY_COLORS["paper"]
    assert COLORS["paper"] == DAY_COLORS["paper"]


def test_uploaded_brand_logos_are_loaded_from_local_assets():
    lpl_logo = SportsDashboard._load_local_logo(LOCAL_LPL_LOGO_PATH, (74, 38), alpha_threshold=8)
    nba_logo = SportsDashboard._load_local_logo(LOCAL_NBA_LOGO_PATH, (34, 38), alpha_threshold=8)
    worldcup_logo = SportsDashboard._load_local_logo(LOCAL_WORLDCUP_LOGO_PATH, (36, 36), alpha_threshold=16)

    assert lpl_logo is not None
    assert lpl_logo.size[0] <= 74
    assert lpl_logo.size[1] <= 38
    assert lpl_logo.getchannel("A").getextrema()[0] == 0
    assert nba_logo is not None
    assert nba_logo.size[0] <= 34
    assert nba_logo.size[1] <= 38
    assert nba_logo.getchannel("A").getextrema()[0] == 0
    assert worldcup_logo is not None
    assert worldcup_logo.size[0] <= 36
    assert worldcup_logo.size[1] <= 36
    assert worldcup_logo.getchannel("A").getextrema()[0] == 0


def test_al_logo_draw_size_is_the_only_lpl_size_override():
    assert SportsDashboard._team_logo_draw_size("AL", 19) == 25
    assert SportsDashboard._team_logo_draw_size("al", 16) == 21
    assert SportsDashboard._team_logo_draw_size("BLG", 19) == 19


def test_worldcup_flag_draws_loaded_flag_without_background():
    plugin = _plugin()
    flag = Image.new("RGBA", (30, 22), (0, 0, 0, 0))
    for x in range(8, 22):
        for y in range(6, 16):
            flag.putpixel((x, y), (0, 92, 185, 255))
    plugin._load_flag_image = lambda _url, _size: flag
    image = Image.new("RGB", (80, 40), COLORS["paper"])
    draw = ImageDraw.Draw(image)

    plugin._draw_worldcup_flag(image, draw, "https://flagsapi.com/MX/flat/64.png", 10, 10, 30, 22, "MEX")

    assert image.getpixel((10, 10)) == COLORS["paper"]
    assert image.getpixel((20, 16)) == (0, 92, 185)


def test_logo_with_flat_background_becomes_transparent():
    source = Image.new("RGB", (8, 8), (255, 255, 255))
    for x in range(2, 6):
        for y in range(2, 6):
            source.putpixel((x, y), (0, 92, 185))

    logo = SportsDashboard._logo_with_transparent_background(source)

    assert logo.mode == "RGBA"
    assert logo.getpixel((0, 0))[3] == 0
    assert logo.getpixel((3, 3)) == (0, 92, 185, 255)
