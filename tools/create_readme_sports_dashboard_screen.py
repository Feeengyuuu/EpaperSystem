from __future__ import annotations

import sys
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
INKYPI = ROOT / "inkypi-weather" / "package" / "InkyPi"
SRC = INKYPI / "src"
OUT = INKYPI / "docs" / "images" / "readme" / "screens" / "actual-sports-dashboard-800x480.png"

sys.path.insert(0, str(SRC))

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

import plugins.sports_dashboard.sports_dashboard as sports_dashboard_module
from plugins.sports_dashboard.sports_dashboard import SportsDashboard


class FakeDeviceConfig:
    def __init__(self, resolution=(800, 480), orientation="horizontal", timezone_name="America/Los_Angeles"):
        self.resolution = resolution
        self.orientation = orientation
        self.timezone = timezone_name
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


def worldcup_events(now):
    return [
        {
            "event_id": "sample-worldcup-main",
            "start": now + timedelta(hours=3),
            "state": "scheduled",
            "block": "Group Stage",
            "team_a": "Croatia",
            "team_b": "Ghana",
            "team_a_tla": "CRO",
            "team_b_tla": "GHA",
            "team_a_standing_points": 3,
            "team_b_standing_points": 1,
            "team_a_record": "1-0-0",
            "team_b_record": "0-1-0",
            "odds": {"team_a": "1.80", "draw": "3.20", "team_b": "4.10"},
        },
        {
            "event_id": "sample-worldcup-upcoming",
            "start": now + timedelta(hours=5),
            "state": "scheduled",
            "block": "Group Stage",
            "team_a": "England",
            "team_b": "Wales",
            "team_a_tla": "ENG",
            "team_b_tla": "WAL",
        },
        {
            "event_id": "sample-worldcup-recent",
            "start": now - timedelta(hours=2),
            "state": "completed",
            "block": "Group Stage",
            "team_a": "Morocco",
            "team_b": "Iran",
            "team_a_tla": "MAR",
            "team_b_tla": "IRN",
            "wins_a": 1,
            "wins_b": 1,
        },
    ]


def msi_events(now):
    return [
        {
            "event_id": "sample-msi-live",
            "match_id": "sample-msi-live",
            "start": now - timedelta(minutes=18),
            "state": "inprogress",
            "team_a": "T1",
            "team_b": "TLAW",
            "wins_a": 1,
            "wins_b": 1,
            "best_of": 5,
            "block": "Bracket Stage",
            "league_name": "Mid-Season Invitational",
            "league_slug": "msi",
        },
        {
            "event_id": "sample-msi-next",
            "match_id": "sample-msi-next",
            "start": now + timedelta(hours=2),
            "state": "unstarted",
            "team_a": "KC",
            "team_b": "DCG",
            "wins_a": None,
            "wins_b": None,
            "best_of": 3,
            "block": "Play-In Knockouts",
            "league_name": "Mid-Season Invitational",
            "league_slug": "msi",
        },
    ]


def pga_selection(now):
    event = {
        "sport": "PGA",
        "event_id": "sample-pga-live",
        "start": now - timedelta(days=1),
        "end": now + timedelta(days=2),
        "state": "inprogress",
        "status_text": "THRU 06/27",
        "name": "Travelers Championship",
        "venue": "TPC River Highlands",
        "leader": {
            "name": "S. Scheffler",
            "score": "-16",
            "country": "USA",
            "round": 2,
            "today": "-4",
            "strokes": "67",
        },
        "leaderboard": [
            {"position": 1, "position_label": "P1", "name": "S. Scheffler", "country": "USA", "score": "-16", "round": 2, "today": "-4", "strokes": "67"},
            {"position": 2, "position_label": "P2", "name": "R. McIlroy", "country": "NIR", "score": "-14", "round": 2, "today": "-2", "strokes": "69"},
            {"position": 3, "position_label": "P3", "name": "A. Bhati", "country": "USA", "score": "-12", "round": 2, "today": "-1", "strokes": "70"},
            {"position": 4, "position_label": "P4", "name": "E. Cole", "country": "USA", "score": "-11", "round": 2, "today": "E", "strokes": "71"},
            {"position": 5, "position_label": "P5", "name": "L. Aberg", "country": "SWE", "score": "-10", "round": 2, "today": "-3", "strokes": "68"},
        ],
    }
    card = {
        "sport": "PGA",
        "status": "LIVE",
        "main": event,
        "live": [event],
        "upcoming": [],
        "recent": [],
        "events": [event],
        "order": 2,
    }
    return {"primary": card, "cards": [card], "rotation_pool": ["PGA"]}


def main() -> None:
    plugin = SportsDashboard({"id": "sports_dashboard"})
    device_config = FakeDeviceConfig()
    timezone_info = ZoneInfo("America/Los_Angeles")
    now = datetime(2026, 6, 27, 12, 0, tzinfo=timezone_info)
    settings = {
        "worldCupTopHeight": "208",
        "overlayWorldCupLocalTimes": "false",
        "nbaOffseasonPanelMode": "always",
        "lolEsportsSidebarOverride": "MSI",
        "ewcSidebarEnabled": "false",
        "valveEsportsEnabled": "false",
    }

    plugin._try_worldcup_scoreboard_panel = lambda _settings, _device_config, dimensions, _timezone_info, visible_matches, _now: plugin._render_worldcup_api_panel(
        dimensions,
        worldcup_events(now),
        "PUBLIC SAMPLE",
        now.isoformat(),
        visible_matches,
        now,
    )
    plugin._try_worldcup_football_data_panel = lambda *args, **kwargs: None
    plugin._try_worldcup_api_panel = lambda *args, **kwargs: None
    plugin._take_worldcup_screenshot = lambda *args, **kwargs: None
    plugin._load_nba_events = lambda _settings, _timezone_info: ([], "NBA OFFSEASON")
    plugin._attach_nba_odds = lambda events, *_args, **_kwargs: events
    plugin._load_offseason_hub = lambda _settings, _timezone_info, _now: (pga_selection(now), "PUBLIC SAMPLE")
    plugin._write_offseason_hub_state = lambda *_args, **_kwargs: None
    plugin._load_lpl_events = lambda _settings, _timezone_info: ([], "LPL OFFSEASON")
    plugin._load_lck_events = lambda _settings, _timezone_info: ([], "LCK NO DATA")
    plugin._load_msi_events = lambda _settings, _timezone_info, _now: (msi_events(now), "PUBLIC SAMPLE", None)
    plugin._attach_lpl_odds = lambda events, *_args, **_kwargs: events
    plugin._attach_lpl_realtime_info = lambda selected, _settings, **_kwargs: selected
    plugin._write_lol_live_state = lambda *_args, **_kwargs: None
    plugin._write_nba_live_state = lambda *_args, **_kwargs: None
    plugin._load_team_logo = lambda *_args, **_kwargs: None
    plugin._load_flag_image = lambda *_args, **_kwargs: None

    theme_context = plugin._sports_dashboard_theme_context(settings, device_config, now)
    token = sports_dashboard_module._ACTIVE_COLORS.set(plugin._sports_dashboard_colors(theme_context))
    try:
        image = plugin._generate_image_with_active_colors(settings, device_config, (800, 480), timezone_info, now)
    finally:
        sports_dashboard_module._ACTIVE_COLORS.reset(token)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    image.convert("RGB").save(OUT)
    print(OUT)


if __name__ == "__main__":
    main()
