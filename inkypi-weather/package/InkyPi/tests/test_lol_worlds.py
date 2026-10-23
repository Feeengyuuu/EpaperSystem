"""Worlds esports card: tournament window, sidebar pin priority and live state."""

import hashlib
import json
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw
import pytest

import plugins.sports_dashboard.sports_dashboard as sports_dashboard_module
from plugins.sports_dashboard.common import (
    _ACTIVE_COLORS,
    DAY_COLORS,
    DEEP_NIGHT_COLORS,
    LOCAL_WORLDS_WORDMARK_PATHS,
    TEAM_LOGO_LIGHT_INFO_KEY,
)
from plugins.sports_dashboard.sports_dashboard import (
    DEFAULT_WORLDS_LEAGUE_ID,
    LOCAL_WORLDS_LOGO_PATH,
    MSI_HEADER_LOGO_SIZE,
    WORLDS_LIVE_STATE_VERSION,
    SportsDashboard,
)
from tests.test_sports_dashboard import FakeDeviceConfig, _plugin


NOW = datetime(2026, 10, 16, 23, 30, tzinfo=timezone.utc)
TOURNAMENT = {
    "id": "115660540725177488",
    "slug": "worlds_2026",
    "start": datetime(2026, 10, 15, tzinfo=timezone.utc),
    "end": datetime(2026, 11, 15, 23, 59, 59, tzinfo=timezone.utc),
}


def _match(event_id, start, state, team_a, team_b, wins=(None, None), block="Play-Ins"):
    return {
        "event_id": event_id,
        "match_id": event_id,
        "source_match_id": event_id,
        "event_type": "match",
        "league_key": "WORLDS",
        "start": start,
        "state": state,
        "team_a": team_a,
        "team_b": team_b,
        "team_a_logo": "",
        "team_b_logo": "",
        "wins_a": wins[0],
        "wins_b": wins[1],
        "best_of": 5,
        "block": block,
        "stage_label": "Play-In",
    }


def _worlds_events():
    return [
        _match("w-1", datetime(2026, 10, 16, 18, 0, tzinfo=timezone.utc), "completed", "KC", "MVK", (3, 0)),
        _match("w-2", datetime(2026, 10, 16, 23, 0, tzinfo=timezone.utc), "inprogress", "FUR", "C9", (0, 0)),
        _match("w-3", datetime(2026, 10, 17, 18, 0, tzinfo=timezone.utc), "unstarted", "TBD", "MVK"),
    ]


def _pinned_worlds_card(selected):
    return {
        "league_key": "WORLDS",
        "selected": selected,
        "source_state": "WORLDS LIVE DATA",
        "priority": 3,
        "sidebar_pinned": True,
        "pinned_until": TOURNAMENT["end"],
    }


def _live_cs2_card(now):
    match = {"start": now - timedelta(minutes=20), "state": "inProgress", "team_a": "LGC", "team_b": "BB"}
    card = {
        "series": "CS",
        "event_name": "Thunderpick World Championship 2026",
        "status": "LIVE",
        "window_active": True,
        "main": match,
        "live": [match],
        "upcoming": [],
        "recent": [],
        "source_state": "PANDASCORE LIVE",
    }
    return {"primary": card, "cards": [card]}


def _schedule_payload():
    def event(event_id, start, state, block, teams, count=5):
        return {
            "id": event_id,
            "startTime": start,
            "state": state,
            "type": "match",
            "blockName": block,
            "league": {"id": DEFAULT_WORLDS_LEAGUE_ID, "name": "Worlds", "slug": "worlds"},
            "match": {
                "id": event_id,
                "strategy": {"type": "bestOf", "count": count},
                "teams": [
                    {"code": code, "image": "", "result": {"gameWins": wins}}
                    for code, wins in teams
                ],
            },
        }

    return {
        "data": {
            "schedule": {
                "events": [
                    event("2025-final", "2025-11-09T07:00:00Z", "completed", "Finals", [("T1", 3), ("KT", 2)]),
                    event("2026-a", "2026-10-16T18:00:00Z", "completed", "Play-Ins", [("KC", 3), ("MVK", 0)]),
                    event("2026-b", "2026-10-16T23:00:00Z", "inProgress", "Play-Ins", [("FUR", 0), ("C9", 0)]),
                    event("2026-c", "2026-10-23T17:00:00Z", "unstarted", "Swiss", [("TBD", None), ("TBD", None)], 1),
                ]
            }
        }
    }


class _FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def json(self):
        return self.payload

    def raise_for_status(self):
        return None


def test_fetch_worlds_events_uses_worlds_league_and_drops_previous_edition(monkeypatch):
    plugin = _plugin()
    calls = []

    class FakeSession:
        def get(self, url, headers=None, timeout=None):
            calls.append(url)
            if "getLive" in url:
                return _FakeResponse({"data": {"schedule": {"events": []}}})
            return _FakeResponse(_schedule_payload())

    monkeypatch.setattr(sports_dashboard_module, "get_http_session", lambda: FakeSession())

    events = plugin._fetch_worlds_events({}, timezone.utc, NOW, tournament=TOURNAMENT)

    assert f"leagueId={DEFAULT_WORLDS_LEAGUE_ID}" in calls[0]
    assert any("getLive" in url for url in calls)
    assert [event["event_id"] for event in events] == ["2026-a", "2026-b", "2026-c"]
    assert {event["league_key"] for event in events} == {"WORLDS"}
    assert [event["stage_label"] for event in events] == ["Play-In", "Play-In", "Swiss Stage"]


def test_worlds_card_is_pinned_on_a_match_day():
    plugin = _plugin()
    plugin._load_worlds_events = lambda *_args, **_kwargs: (
        _worlds_events(),
        "WORLDS LIVE DATA",
        TOURNAMENT,
    )

    card = plugin._load_worlds_sidebar_card({}, timezone.utc, NOW)

    assert card["league_key"] == "WORLDS"
    assert card["in_window"] is True
    assert card["sidebar_pinned"] is True
    # Live w-2 and w-3 within 24 hours: the pin covers w-3's live window.
    assert card["pinned_until"] == datetime(2026, 10, 18, 0, 0, tzinfo=timezone.utc)
    assert [event["event_id"] for event in card["selected"]["live"]] == ["w-2"]


@pytest.mark.parametrize("hours_ahead, pinned", [(23, True), (30, False)])
def test_worlds_pins_only_when_the_next_match_is_within_a_day(hours_ahead, pinned):
    plugin = _plugin()
    rest_day = datetime(2026, 10, 19, 12, 0, tzinfo=timezone.utc)
    events = [
        _match("done", rest_day - timedelta(days=2), "completed", "KC", "MVK", (3, 0)),
        _match("next", rest_day + timedelta(hours=hours_ahead), "unstarted", "GEN", "IG", block="Swiss"),
    ]
    plugin._load_worlds_events = lambda *_args, **_kwargs: (events, "WORLDS LIVE DATA", TOURNAMENT)

    card = plugin._load_worlds_sidebar_card({}, timezone.utc, rest_day)

    assert card["in_window"] is True
    assert card["sidebar_pinned"] is pinned
    assert (card["pinned_until"] is None) is (not pinned)


@pytest.mark.parametrize(
    "now",
    [
        datetime(2026, 10, 14, 12, 0, tzinfo=timezone.utc),
        datetime(2026, 11, 16, 12, 0, tzinfo=timezone.utc),
    ],
)
def test_worlds_card_is_not_pinned_outside_the_tournament_window(now):
    plugin = _plugin()
    plugin._load_worlds_events = lambda *_args, **_kwargs: (
        _worlds_events(),
        "WORLDS LIVE DATA",
        TOURNAMENT,
    )

    card = plugin._load_worlds_sidebar_card({}, timezone.utc, now)

    assert card["sidebar_pinned"] is False
    assert card["pinned_until"] is None


def test_worlds_card_without_matches_is_not_pinned():
    plugin = _plugin()
    plugin._load_worlds_events = lambda *_args, **_kwargs: ([], "WORLDS NO DATA", TOURNAMENT)

    card = plugin._load_worlds_sidebar_card({}, timezone.utc, NOW)

    assert card["sidebar_pinned"] is False


def test_worlds_schedule_is_not_requested_outside_the_tournament():
    plugin = _plugin()
    june = datetime(2026, 6, 21, 12, 0, tzinfo=timezone.utc)
    plugin._load_worlds_tournament = lambda *_args: TOURNAMENT
    plugin._fetch_worlds_events = lambda *_args, **_kwargs: pytest.fail(
        "Worlds schedule must not be fetched months before the tournament"
    )

    events, source_state, tournament = plugin._load_worlds_events({}, timezone.utc, june)

    assert events == []
    assert source_state == "WORLDS IDLE"
    assert tournament is TOURNAMENT

    fetched = []
    plugin._fetch_worlds_events = lambda *_args, **_kwargs: fetched.append(True) or _worlds_events()
    events, source_state, _ = plugin._load_worlds_events({}, timezone.utc, june, force=True)

    assert fetched == [True]
    assert source_state == "WORLDS LIVE DATA"


def test_worlds_tournament_metadata_is_cached_in_its_own_state_file(monkeypatch, tmp_path):
    plugin = _plugin()
    monkeypatch.setattr(plugin, "get_plugin_dir", lambda name: str(tmp_path / name))
    calls = []

    class FakeSession:
        def get(self, url, headers=None, timeout=None):
            calls.append(url)
            return _FakeResponse(
                {
                    "data": {
                        "leagues": [
                            {
                                "tournaments": [
                                    {"id": "115660540725177488", "slug": "worlds_2026", "startDate": "2026-10-15", "endDate": "2026-11-15"},
                                    {"id": "113475452383887518", "slug": "worlds_2025", "startDate": "2025-10-14", "endDate": "2025-11-09"},
                                ]
                            }
                        ]
                    }
                }
            )

    monkeypatch.setattr(sports_dashboard_module, "get_http_session", lambda: FakeSession())

    tournament = plugin._load_worlds_tournament({}, timezone.utc, NOW)
    cached = plugin._load_worlds_tournament({}, timezone.utc, NOW + timedelta(hours=1))

    assert tournament["slug"] == "worlds_2026"
    assert cached["slug"] == "worlds_2026"
    assert len(calls) == 1
    assert f"leagueId={DEFAULT_WORLDS_LEAGUE_ID}" in calls[0]
    state = json.loads((tmp_path / "cache" / "worlds_tournament_state.json").read_text(encoding="utf-8"))
    assert state["version"] == "sports-dashboard-worlds-tournament-v1"
    assert state["tournament"]["end_date"] == "2026-11-15"
    assert not (tmp_path / "cache" / "msi_tournament_state.json").exists()


def test_lol_sidebar_cards_include_worlds_only_during_the_tournament():
    plugin = _plugin()
    plugin._load_lpl_events = lambda *_args: ([], "CACHE DATA")
    plugin._attach_lpl_odds = lambda events, *_args, **_kwargs: events
    plugin._load_lck_events = lambda *_args: ([], "LCK NO DATA")
    plugin._load_msi_events = lambda *_args: ([], "MSI NO DATA", None)
    state = {"in_window": False, "pinned": False}
    plugin._load_worlds_sidebar_card = lambda *_args, **_kwargs: {
        **_pinned_worlds_card(SportsDashboard._select_worlds_events(_worlds_events(), NOW)),
        "in_window": state["in_window"],
        "sidebar_pinned": state["pinned"],
    }

    keys = [card["league_key"] for card in plugin._load_lol_esports_sidebar_cards({}, FakeDeviceConfig(), timezone.utc, NOW)]
    assert "WORLDS" not in keys

    state["in_window"] = True  # a rest day: a candidate, not pinned
    keys = [card["league_key"] for card in plugin._load_lol_esports_sidebar_cards({}, FakeDeviceConfig(), timezone.utc, NOW)]
    assert keys[-1] == "WORLDS"

    state["pinned"] = True
    keys = [card["league_key"] for card in plugin._load_lol_esports_sidebar_cards({}, FakeDeviceConfig(), timezone.utc, NOW)]
    assert keys[-1] == "WORLDS"

    keys = [
        card["league_key"]
        for card in plugin._load_lol_esports_sidebar_cards(
            {"worldsEnabled": "false"},
            FakeDeviceConfig(),
            timezone.utc,
            NOW,
        )
    ]
    assert "WORLDS" not in keys


def test_running_worlds_outranks_a_live_cs2_match_even_between_worlds_games():
    upcoming_only = SportsDashboard._select_worlds_events(
        [_match("w-3", NOW + timedelta(hours=18), "unstarted", "TBD", "MVK")],
        NOW,
    )
    lpl_live = SportsDashboard._select_lpl_events(
        [{"start": NOW - timedelta(minutes=10), "state": "inProgress", "team_a": "BLG", "team_b": "TES"}],
        NOW,
    )

    choice = SportsDashboard._select_right_esports_sidebar(
        [
            {"league_key": "LPL", "selected": lpl_live, "source_state": "LIVE DATA", "priority": 0},
            _pinned_worlds_card(upcoming_only),
        ],
        _live_cs2_card(NOW),
        "PANDASCORE LIVE",
        NOW,
    )

    assert choice["kind"] == "lol"
    assert choice["pinned"] is True
    assert choice["choice"]["league_key"] == "WORLDS"


def test_worlds_stops_pinning_after_the_final_and_a_live_cs2_match_takes_over():
    plugin = _plugin()
    final_day = datetime(2026, 11, 15, 20, 0, tzinfo=timezone.utc)
    final = _match("final", final_day - timedelta(hours=8), "completed", "GEN", "BLG", (3, 1), block="Finals")
    plugin._load_worlds_events = lambda *_args, **_kwargs: ([final], "WORLDS LIVE DATA", TOURNAMENT)

    card = plugin._load_worlds_sidebar_card({}, timezone.utc, final_day)

    assert card["in_window"] is True
    assert card["sidebar_pinned"] is False
    assert SportsDashboard._lol_sidebar_candidate_phase(card) is None
    assert SportsDashboard._lol_sidebar_candidate_phase({**card, "sidebar_pinned": True}) == 2
    choice = SportsDashboard._select_right_esports_sidebar([card], _live_cs2_card(final_day), "PANDASCORE LIVE", final_day)
    assert choice["kind"] == "valve"


def _upcoming_cs2_card(start):
    match = {"start": start, "state": "unstarted", "team_a": "LGC", "team_b": "BB"}
    card = {
        "series": "CS",
        "event_name": "Thunderpick World Championship 2026",
        "status": "NEXT",
        "window_active": True,
        "main": match,
        "live": [],
        "upcoming": [match],
        "recent": [],
        "source_state": "PANDASCORE LIVE",
    }
    return {"primary": card, "cards": [card]}


@pytest.mark.parametrize("cs2_hours, winner", [(2, "valve"), (40, "lol")])
def test_on_a_worlds_rest_day_the_earlier_match_wins(cs2_hours, winner):
    rest_day = datetime(2026, 10, 19, 12, 0, tzinfo=timezone.utc)
    worlds_next = _match("next", rest_day + timedelta(hours=30), "unstarted", "GEN", "IG", block="Swiss")
    worlds = {
        **_pinned_worlds_card(SportsDashboard._select_worlds_events([worlds_next], rest_day)),
        "in_window": True,
        "sidebar_pinned": False,
        "pinned_until": None,
    }

    choice = SportsDashboard._select_right_esports_sidebar(
        [worlds],
        _upcoming_cs2_card(rest_day + timedelta(hours=cs2_hours)),
        "PANDASCORE LIVE",
        rest_day,
    )

    assert choice["kind"] == winner
    if winner == "lol":
        assert choice["choice"]["league_key"] == "WORLDS"


def test_prepare_right_panel_skips_cs2_discovery_while_worlds_is_pinned():
    plugin = _plugin()
    card = _pinned_worlds_card(SportsDashboard._select_worlds_events(_worlds_events(), NOW))
    plugin._load_lol_esports_sidebar_cards = lambda *_args: [card]
    plugin._lol_esports_sidebar_override = lambda settings=None: ""
    for name in ("_load_pandascore_cs2_card", "_load_valve_ti_official_card", "_load_valve_esports"):
        setattr(plugin, name, lambda *_args, _name=name, **_kwargs: pytest.fail(f"{_name} must not run"))
    plugin._write_valve_esports_live_state = lambda *_args, **_kwargs: pytest.fail("valve state must stay untouched")

    choice = plugin._prepare_right_esports_panel({}, FakeDeviceConfig(), timezone.utc, NOW)

    assert choice == {"kind": "lol", "choice": card}


def test_worlds_live_state_pin_marker_pauses_cs2_live_refresh(monkeypatch, tmp_path):
    plugin = _plugin()
    monkeypatch.setattr(plugin, "get_plugin_dir", lambda name: str(tmp_path / name))
    selected = SportsDashboard._select_worlds_events(_worlds_events(), NOW)

    assert plugin._live_image_refresh_enabled({}, "valve_esports", NOW) is True

    plugin._write_lol_live_state(
        selected,
        NOW,
        "WORLDS LIVE DATA",
        league_key="WORLDS",
        sidebar_pinned_until=TOURNAMENT["end"],
    )

    state = json.loads((tmp_path / "cache" / "worlds_live_state.json").read_text(encoding="utf-8"))
    assert state["version"] == WORLDS_LIVE_STATE_VERSION
    assert state["league_key"] == "WORLDS"
    assert state["team_a"] == "FUR"
    assert state["sidebar_pinned_until"] == TOURNAMENT["end"].isoformat()
    assert "worlds" in plugin._active_live_refresh_sources({}, NOW)
    assert plugin._live_image_refresh_interval({}, "worlds") == 60
    assert plugin._live_image_refresh_enabled({}, "valve_esports", NOW) is False
    assert plugin._worlds_sidebar_pin_marker_active(TOURNAMENT["end"] + timedelta(seconds=1)) is False

    plugin._release_worlds_sidebar_pin()

    state = json.loads((tmp_path / "cache" / "worlds_live_state.json").read_text(encoding="utf-8"))
    assert state["sidebar_pinned_until"] is None
    assert state["team_a"] == "FUR"
    assert plugin._live_image_refresh_enabled({}, "valve_esports", NOW) is True


def test_drawing_another_sidebar_releases_the_worlds_pin(monkeypatch, tmp_path):
    plugin = _plugin()
    monkeypatch.setattr(plugin, "get_plugin_dir", lambda name: str(tmp_path / name))
    plugin._write_lol_live_state(
        {"live": []},
        NOW,
        "WORLDS LIVE DATA",
        league_key="WORLDS",
        sidebar_pinned_until=TOURNAMENT["end"],
    )
    lpl_card = {
        "league_key": "LPL",
        "selected": SportsDashboard._select_lpl_events([], NOW),
        "source_state": "CACHE DATA",
        "priority": 0,
    }
    plugin._prepare_right_esports_panel = lambda *_args: {"kind": "lol", "choice": lpl_card}
    plugin._attach_lpl_realtime_info = lambda *_args, **_kwargs: None
    plugin._draw_lpl_sidebar = lambda *_args, **_kwargs: None

    plugin._draw_right_esports_region(Image.new("RGB", (800, 480)), {}, FakeDeviceConfig(), timezone.utc, NOW, 552)

    assert plugin._worlds_sidebar_pin_marker_active(NOW) is False


def test_lol_sidebar_override_can_force_worlds():
    assert SportsDashboard._lol_esports_sidebar_override({"lolEsportsSidebarOverride": "worlds"}) == "WORLDS"


def _capture_worlds_header(plugin):
    logos = []
    wordmarks = []
    texts = []
    original_wordmark = plugin._draw_worlds_header_wordmark
    original_text = plugin._draw_text_in_box

    def capture_logo(image, draw, x, y, width, height, logo_path=None, fallback_text=None):
        logos.append((logo_path, (width, height), fallback_text))

    def capture_wordmark(image, path, box):
        wordmarks.append((path, box))
        return original_wordmark(image, path, box)

    def capture_text(draw, box, text, font, color, align="left"):
        texts.append(str(text))
        return original_text(draw, box, text, font, color, align=align)

    plugin._draw_lpl_logo = capture_logo
    plugin._draw_worlds_header_wordmark = capture_wordmark
    plugin._draw_text_in_box = capture_text
    return logos, wordmarks, texts


def test_worlds_sidebar_header_uses_the_official_edition_wordmark():
    plugin = _plugin()
    logos, wordmarks, texts = _capture_worlds_header(plugin)
    image = Image.new("RGB", (800, 480), (255, 255, 255))
    selected = SportsDashboard._select_worlds_events(_worlds_events(), NOW)

    plugin._draw_lpl_sidebar(image, 552, selected, "WORLDS LIVE DATA", NOW, league_key="WORLDS")

    right_x = 552 + 4
    assert wordmarks == [(LOCAL_WORLDS_WORDMARK_PATHS[2026], (right_x + 13, 13, 800 - 96, 53))]
    assert logos == []
    assert "WORLDS DATA" not in texts
    # The mark fills the old emblem + label slot, left of the LIVE pill.
    header = image.crop((right_x + 13, 13, 800 - 96, 53))
    ink_columns = [x for x in range(header.width) if any(header.getpixel((x, y)) == DAY_COLORS["text"] for y in range(header.height))]
    assert ink_columns[0] <= 1 and ink_columns[-1] >= header.width - 3


def test_worlds_sidebar_keeps_the_emblem_for_editions_without_a_wordmark():
    plugin = _plugin()
    logos, wordmarks, texts = _capture_worlds_header(plugin)
    image = Image.new("RGB", (800, 480), (255, 255, 255))
    later = NOW.replace(year=2027)
    selected = SportsDashboard._select_worlds_events(_worlds_events(), later)

    plugin._draw_lpl_sidebar(image, 552, selected, "WORLDS LIVE DATA", later, league_key="WORLDS")

    assert 2027 not in LOCAL_WORLDS_WORDMARK_PATHS
    assert wordmarks == []
    assert logos[0] == (LOCAL_WORLDS_LOGO_PATH, MSI_HEADER_LOGO_SIZE, "WORLDS")
    assert "WORLDS DATA" in texts


def test_lpl_and_msi_headers_never_use_the_worlds_wordmark():
    for league in ("LPL", "LCK", "MSI"):
        config = SportsDashboard._lol_sidebar_config(league)
        assert SportsDashboard._worlds_header_wordmark_path(config, config["logo_path"], NOW) is None


def test_worlds_wordmark_keeps_its_proportions_and_uses_the_theme_ink():
    plugin = _plugin()
    for colors in (DAY_COLORS, DEEP_NIGHT_COLORS):
        token = _ACTIVE_COLORS.set(colors)
        try:
            image = Image.new("RGB", (160, 60), colors["panel"])
            assert plugin._draw_worlds_header_wordmark(image, LOCAL_WORLDS_WORDMARK_PATHS[2026], (0, 0, 131, 40))
        finally:
            _ACTIVE_COLORS.reset(token)
        bbox = ImageChops.difference(image, Image.new("RGB", image.size, colors["panel"])).getbbox()
        width, height = bbox[2] - bbox[0], bbox[3] - bbox[1]
        with Image.open(LOCAL_WORLDS_WORDMARK_PATHS[2026]) as source:
            assert abs(width / height - source.width / source.height) < 0.15
        # Hard-edged one-colour mark: only the panel and the theme ink.
        assert set(image.get_flattened_data()) <= {tuple(colors["panel"]), tuple(colors["text"])}


def test_worlds_wordmark_asset_matches_its_manifest():
    path = Path(LOCAL_WORLDS_WORDMARK_PATHS[2026])
    manifest = json.loads((path.parent / "worlds_manifest.json").read_text(encoding="utf-8"))
    entry = manifest["assets"][path.name]

    assert hashlib.sha256(path.read_bytes()).hexdigest() == entry["runtime_sha256"]
    with Image.open(path) as mark:
        assert mark.mode == "RGBA"
        assert list(mark.size) == entry["runtime_dimensions"]
        assert mark.getchannel("A").getbbox() == (0, 0, mark.width, mark.height)
        opaque = mark.getchannel("A").point(lambda alpha: 255 if alpha >= 128 else 0)
        brightness = mark.convert("RGB").convert("L")
    assert ImageChops.multiply(brightness, opaque).getextrema()[1] <= 16


def test_worlds_sidebar_uses_worlds_branding(monkeypatch):
    plugin = _plugin()
    logos, _wordmarks, _texts = _capture_worlds_header(plugin)
    plugin._draw_worlds_header_wordmark = lambda *_args, **_kwargs: False
    image = Image.new("RGB", (800, 480), (255, 255, 255))
    selected = SportsDashboard._select_worlds_events(_worlds_events(), NOW)

    plugin._draw_lpl_sidebar(image, 552, selected, "WORLDS LIVE DATA", NOW, league_key="WORLDS")

    # Without the wordmark (missing asset) the emblem comes back.
    assert logos[0] == (LOCAL_WORLDS_LOGO_PATH, MSI_HEADER_LOGO_SIZE, "WORLDS")
    config = SportsDashboard._lol_sidebar_config("WORLDS")
    assert config["name"] == "Worlds"
    assert config["accent"] == "worlds_accent"
    assert SportsDashboard._source_label("WORLDS LIVE DATA") == "WORLDS DATA"


def test_worlds_focus_stage_label_draws_below_team_names():
    plugin = _plugin()
    image = Image.new("RGB", (220, 180), (255, 255, 255))
    draw = ImageDraw.Draw(image)
    centered = []
    original = plugin._draw_centered_in_box

    def capture(draw_arg, box, text, font, fill):
        centered.append((box, text))
        return original(draw_arg, box, text, font, fill)

    plugin._draw_centered_in_box = capture
    event = _match("w-3", NOW + timedelta(hours=18), "unstarted", "T1", "MVK", block="Swiss")
    event["stage_label"] = "Swiss Stage"

    plugin._draw_lpl_focus_card(image, draw, 0, 220, 0, event, NOW, False, league_key="WORLDS")

    stage_box = next(box for box, text in centered if text == "Swiss Stage")
    assert stage_box[1] >= 138


def test_worlds_logo_asset_is_a_dark_emblem_matching_its_manifest():
    logo_path = Path(LOCAL_WORLDS_LOGO_PATH)
    manifest = json.loads((logo_path.parent / "worlds_manifest.json").read_text(encoding="utf-8"))

    assert hashlib.sha256(logo_path.read_bytes()).hexdigest() == manifest["assets"]["worlds.png"]["runtime_sha256"]
    with Image.open(logo_path) as logo:
        assert logo.mode == "RGBA"
        assert list(logo.size) == manifest["assets"]["worlds.png"]["runtime_dimensions"]
        opaque_mask = logo.getchannel("A").point(lambda alpha: 255 if alpha >= 128 else 0)
        brightness = logo.convert("RGB").convert("L")
    assert opaque_mask.getextrema()[1] == 255
    assert ImageChops.multiply(brightness, opaque_mask).getextrema()[1] <= 16


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("Play-Ins", "Play-In"),
        ("Swiss", "Swiss Stage"),
        ("Quarterfinals", "Quarter-Final"),
        ("Finals", "Final"),
        ("Play In Knockouts", ""),
    ],
)
def test_worlds_block_names_map_to_stage_labels(value, expected):
    assert SportsDashboard._canonical_lpl_stage_label(value) == expected


def test_worlds_stage_fallback_names_the_league():
    assert SportsDashboard._lol_event_stage_fallback({"league_id": DEFAULT_WORLDS_LEAGUE_ID}) == "Worlds"
    assert SportsDashboard._lol_event_stage_fallback({"league_slug": "worlds"}) == "Worlds"


def _logo_png(fill, size=400):
    logo = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    ImageDraw.Draw(logo).ellipse((20, 100, size - 20, size - 100), fill=fill)
    buffer = BytesIO()
    logo.save(buffer, format="PNG")
    return buffer.getvalue()


def _draw_logo_from_png(plugin, data, background):
    plugin._load_local_team_logo = lambda *_args: None
    plugin._load_team_logo_for_render = lambda _url, size: SportsDashboard._team_logo_from_bytes(data, size)
    image = Image.new("RGB", (80, 80), background)
    plugin._draw_team_logo(image, ImageDraw.Draw(image), "https://example.test/logo.png", 19, 19, 42, "MVK")
    return image


def _count(image, color):
    return sum(1 for pixel in image.get_flattened_data() if pixel == tuple(color))


def test_white_team_logo_takes_the_page_ink_without_a_backdrop_on_the_light_theme():
    plugin = _plugin()

    white = _draw_logo_from_png(plugin, _logo_png((255, 255, 255, 255)), DAY_COLORS["panel"])
    blue = _draw_logo_from_png(plugin, _logo_png((0, 92, 185, 255)), DAY_COLORS["panel"])

    # The white mark itself turns to ink, and nothing is drawn beyond the logo's own shape.
    footprint = 80 * 80 - _count(blue, DAY_COLORS["panel"])
    assert 40 < _count(white, DAY_COLORS["text"]) <= footprint
    assert _count(white, (255, 255, 255)) == 0
    assert _count(blue, DAY_COLORS["text"]) == 0


def test_white_team_logo_has_no_backdrop_on_the_deep_night_theme():
    plugin = _plugin()
    token = _ACTIVE_COLORS.set(DEEP_NIGHT_COLORS)
    try:
        image = _draw_logo_from_png(plugin, _logo_png((255, 255, 255, 255)), DEEP_NIGHT_COLORS["panel"])
    finally:
        _ACTIVE_COLORS.reset(token)

    assert _count(image, DEEP_NIGHT_COLORS["text"]) == 0


def test_light_logo_flag_is_measured_on_the_decoded_source():
    small = SportsDashboard._team_logo_from_bytes(_logo_png((255, 255, 255, 255)), 16)
    colored = SportsDashboard._team_logo_from_bytes(_logo_png((0, 92, 185, 255)), 16)

    assert small.info[TEAM_LOGO_LIGHT_INFO_KEY] is True
    assert colored.info[TEAM_LOGO_LIGHT_INFO_KEY] is False

    dark_thumbnail = Image.new("RGBA", (16, 9), (40, 40, 40, 255))
    dark_thumbnail.putpixel((0, 0), (0, 0, 0, 0))
    dark_thumbnail.info[TEAM_LOGO_LIGHT_INFO_KEY] = True
    assert SportsDashboard._team_logo_needs_ink(dark_thumbnail) is True


def test_worlds_header_emblem_uses_the_theme_ink_at_night():
    plugin = _plugin()
    token = _ACTIVE_COLORS.set(DEEP_NIGHT_COLORS)
    try:
        image = Image.new("RGB", (120, 70), DEEP_NIGHT_COLORS["panel"])
        width, height = MSI_HEADER_LOGO_SIZE
        plugin._draw_lpl_logo(image, ImageDraw.Draw(image), 4, 4, width, height, logo_path=LOCAL_WORLDS_LOGO_PATH, fallback_text="WORLDS")
    finally:
        _ACTIVE_COLORS.reset(token)

    assert _count(image, DEEP_NIGHT_COLORS["text"]) > 200


def test_settings_expose_the_worlds_toggle():
    html = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "plugins"
        / "sports_dashboard"
        / "settings.html"
    ).read_text(encoding="utf-8")

    assert 'id="worldsEnabled"' in html
    assert '<input type="hidden" name="worldsEnabled" value="false">' in html
    assert "pluginSettings.worldsEnabled" in html
