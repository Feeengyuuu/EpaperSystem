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
    LOCAL_WORLDCUP_LOGO_PATH,
    SportsDashboard,
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
        "league": {"name": "International - World Cup", "slug": "international-world-cup"},
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
        "league": {"name": "League of Legends - LPL", "slug": "league-of-legends-lpl"},
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


def test_worldcup_defaults_to_full_visible_match_list():
    assert SportsDashboard._visible_worldcup_matches({}) == 7
    assert SportsDashboard._worldcup_capture_width({}, 552, 7) == 552
    assert SportsDashboard._worldcup_local_time_labels()[0] == "12:00"


def test_worldcup_fallback_renders_full_schedule_list():
    plugin = _plugin()

    image = plugin._render_worldcup_fallback((552, 480), 7)

    assert image.size == (552, 480)
    assert image.getpixel((18, 84)) != COLORS["paper"]
    assert image.getpixel((30, 450)) != COLORS["paper"]


def test_worldcup_api_parser_converts_fixture_to_local_match_row():
    la = ZoneInfo("America/Los_Angeles")

    events = SportsDashboard._parse_worldcup_api_events([_sample_worldcup_fixture()], la)

    assert events[0]["start"].strftime("%Y-%m-%d %H:%M") == "2026-06-11 17:00"
    assert events[0]["team_a"] == "USA"
    assert events[0]["team_b"] == "MEX"
    assert events[0]["state"] == "NS"
    assert events[0]["block"] == "Group Stage - 1"


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
    assert calls[0][1]["league"] == "international-world-cup"
    assert calls[1][0] == "/odds/multi"
    assert calls[1][1]["eventIds"] == "66456904,66456906"


def test_generate_image_builds_narrow_worldcup_panel_and_lpl_sidebar():
    plugin = _plugin()
    la = ZoneInfo("America/Los_Angeles")
    plugin._try_worldcup_football_data_panel = lambda *args, **kwargs: None
    plugin._try_worldcup_api_panel = lambda *args, **kwargs: None
    plugin._take_worldcup_screenshot = lambda settings, dimensions, timezone_name, visible_matches: Image.new("RGB", dimensions, (1, 2, 3))
    plugin._load_lpl_events = lambda settings, timezone_info: (
        SportsDashboard._parse_lpl_events(_sample_payload(), la),
        "LIVE DATA",
    )
    plugin._load_team_logo = lambda logo_url, size: None

    image = plugin.generate_image(
        {"worldCupLeftWidth": "540", "overlayWorldCupLocalTimes": "false"},
        FakeDeviceConfig(),
    )

    assert image.size == (800, 480)
    assert image.mode == "RGB"
    assert image.getpixel((10, 10)) == (1, 2, 3)
    assert image.getpixel((10, 360)) == (1, 2, 3)
    assert image.getpixel((10, 479)) != (1, 2, 3)
    assert image.getpixel((560, 10)) != (1, 2, 3)


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
    assert image.getpixel((146, 110)) == (0, 92, 185)
    assert image.getpixel((214, 100)) != COLORS["panel_gold"]
    assert image.getpixel((290, 100)) != COLORS["panel_gold"]
    assert image.getpixel((214, 124)) != COLORS["paper"]
    assert image.getpixel((date_range[0] + 2, 92)) != COLORS["paper"]
    assert image.getpixel((time_range[0] + 2, 92)) != COLORS["paper"]


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
    plugin._load_team_logo = lambda _logo_url, _size: None

    image = plugin.generate_image(
        {"sportsDashboardTheme": "night", "localTimezone": "UTC", "worldCupLeftWidth": "540"},
        FakeDeviceConfig(timezone="UTC"),
    )

    assert max(image.getpixel((560, 8))) < 90
    assert image.getpixel((560, 8)) != DAY_COLORS["paper"]
    assert DEEP_NIGHT_COLORS["paper"] != DAY_COLORS["paper"]
    assert COLORS["paper"] == DAY_COLORS["paper"]


def test_uploaded_brand_logos_are_loaded_from_local_assets():
    lpl_logo = SportsDashboard._load_local_logo(LOCAL_LPL_LOGO_PATH, (74, 38), alpha_threshold=8)
    worldcup_logo = SportsDashboard._load_local_logo(LOCAL_WORLDCUP_LOGO_PATH, (36, 36), alpha_threshold=16)

    assert lpl_logo is not None
    assert lpl_logo.size[0] <= 74
    assert lpl_logo.size[1] <= 38
    assert lpl_logo.getchannel("A").getextrema()[0] == 0
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
