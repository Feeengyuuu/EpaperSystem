"""Lower-left panels: per-sport layouts, their data, and which league is shown."""

import ast
import hashlib
import json
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from PIL import Image, ImageDraw
import pytest

from plugins.sports_dashboard.common import _ACTIVE_COLORS, COLORS, DAY_COLORS, DEEP_NIGHT_COLORS
from plugins.sports_dashboard.event_marks import EVENT_MARK_DIR
from plugins.sports_dashboard.lower_panel import LOWER_LIVE_ROTATION_MINUTES
from plugins.sports_dashboard.sports_dashboard import SportsDashboard
from tests.test_sports_dashboard import (
    _plugin,
    _sample_f1_jolpica_bundle,
    _sample_mlb_scoreboard_payload,
    _sample_nba_scoreboard_payload,
    _sample_ncaa_scoreboard_payload,
    _sample_openf1_snapshot,
    _sample_pga_scoreboard_payload,
    _sample_wnba_scoreboard_payload,
)

LA = ZoneInfo("America/Los_Angeles")
NOW = datetime(2026, 10, 18, 12, 0, tzinfo=LA)
PLUGIN_DIR = Path(__file__).resolve().parents[1] / "src" / "plugins" / "sports_dashboard"


# Guard ----------------------------------------------------------------------


def test_no_method_is_defined_by_two_mixins():
    """A later mixin silently loses to an earlier one with the same name."""
    owners = defaultdict(list)
    for path in sorted(PLUGIN_DIR.glob("*.py")):
        for node in ast.parse(path.read_text(encoding="utf-8")).body:
            if isinstance(node, ast.ClassDef) and node.name.endswith("Mixin"):
                for item in node.body:
                    if isinstance(item, ast.FunctionDef):
                        owners[item.name].append(f"{path.name}:{node.name}")
    assert {name: where for name, where in owners.items() if len(where) > 1} == {}


# Which league owns the panel --------------------------------------------------


def _hub(*statuses, source="HUB LIVE"):
    cards = [{"sport": sport, "status": status, "main": {"start": NOW + timedelta(days=2)}} for sport, status in statuses]
    pool = [card["sport"] for card in cards if card["status"] == "LIVE"] or [card["sport"] for card in cards if card["status"] != "BREAK"]
    return {"cards": cards, "primary": next((card for card in cards if card["sport"] == (pool or [None])[0]), None), "rotation_pool": pool}, source


def _choose(nba=None, hub=(None, ""), f1=None, settings=None, now=NOW):
    return SportsDashboard._choose_lower_panel(settings or {}, nba or {"offseason": False, "live": []}, hub[0], hub[1], f1, now)


def test_f1_session_in_progress_beats_every_other_live_game():
    choice = _choose(nba={"offseason": False, "live": [{"event_id": "1"}]}, hub=_hub(("NFL", "LIVE")), f1={"status": "LIVE", "weekend": True})
    assert choice["kind"] == "f1"


def test_live_games_take_the_panel_and_rotate():
    hub = _hub(("NFL", "LIVE"), ("MLB", "LIVE"), ("NCAA", "NEXT"))
    nba = {"offseason": False, "live": [{"event_id": "nba"}]}
    seen = []
    for slot in range(3):
        moment = datetime.fromtimestamp(slot * LOWER_LIVE_ROTATION_MINUTES * 60 + 1_800_000_000, tz=timezone.utc)
        choice = _choose(nba=nba, hub=hub, now=moment)
        seen.append(choice["kind"] if choice["kind"] == "nba" else choice["card"]["sport"])
    assert sorted(seen) == ["MLB", "NFL", "nba"]


def test_nfl_live_takes_the_panel_during_the_nba_season():
    choice = _choose(hub=_hub(("NFL", "LIVE")))
    assert (choice["kind"], choice["card"]["sport"]) == ("hub", "NFL")


def test_f1_race_weekend_comes_before_the_nba_when_nothing_is_live():
    assert _choose(hub=_hub(("NFL", "NEXT")), f1={"status": "NEXT", "weekend": True})["kind"] == "f1"
    assert _choose(hub=_hub(("NFL", "NEXT")), f1={"status": "NEXT", "weekend": False})["kind"] == "nba"


def test_placeholder_hub_data_never_preempts_the_nba():
    assert _choose(hub=_hub(("MLB", "LIVE"), source="HUB FALLBACK"))["kind"] == "nba"


def test_nba_offseason_rotates_the_hub_with_the_next_grand_prix():
    hub = _hub(("NFL", "NEXT"), ("MLB", "NEXT"))
    kinds = set()
    for slot in range(6):
        moment = NOW + timedelta(minutes=30 * slot)
        choice = _choose(nba={"offseason": True, "live": []}, hub=hub, f1={"status": "NEXT", "weekend": False}, now=moment)
        kinds.add(choice["kind"] if choice["kind"] == "f1" else choice["card"]["sport"])
    assert kinds == {"NFL", "MLB", "f1"}


def test_panel_modes_pin_a_league():
    assert _choose(hub=_hub(("NFL", "LIVE")), settings={"nbaOffseasonPanelMode": "nba"})["kind"] == "nba"
    assert _choose(f1={"status": "NEXT", "weekend": False}, settings={"f1PanelMode": "always"})["kind"] == "f1"
    assert _choose(f1={"status": "LIVE", "weekend": True}, settings={"f1PanelMode": "off"})["kind"] == "nba"


def test_hub_polls_at_the_live_cadence_once_a_game_should_have_started():
    started = {"cards": [{"sport": "NFL", "events": [{"state": "pre", "start": NOW - timedelta(minutes=5)}]}]}
    later = {"cards": [{"sport": "NFL", "events": [{"state": "pre", "start": NOW + timedelta(hours=2)}]}]}
    golf = {"cards": [{"sport": "PGA", "events": [{"state": "pre", "start": NOW - timedelta(minutes=5)}]}]}
    assert SportsDashboard._offseason_hub_has_started_game(started, NOW) is True
    assert SportsDashboard._offseason_hub_has_started_game(later, NOW) is False
    assert SportsDashboard._offseason_hub_has_started_game(golf, NOW) is False


# Data ---------------------------------------------------------------------------


def test_espn_rank_99_means_unranked():
    assert SportsDashboard._football_rank({"curatedRank": {"current": 99}}) is None
    assert SportsDashboard._football_rank({"curatedRank": {"current": 12}}) == 12


def _mlb_game(**overrides):
    game = _sample_mlb_scoreboard_payload()["dates"][0]["games"][0]
    game = {**game, **overrides}
    return game


def test_mlb_parse_carries_innings_series_decisions_and_national_tv():
    game = _mlb_game(
        description="ALCS Game 5, Makeup of 10/16",
        seriesStatus={"result": "Series tied 2-2", "shortDescription": "ALCS Game 5"},
        decisions={"winner": {"fullName": "Nick Martinez"}, "loser": {"fullName": "Daniel Espino"}},
        broadcasts=[
            {"type": "TV", "isNational": True, "name": "TBS/HBO MAX"},
            {"type": "TV", "isNational": True, "name": "Univision/ TUDN"},
            {"type": "TV", "isNational": False, "name": "SNLA"},
        ],
    )
    game["linescore"] = {**game["linescore"], "innings": [{"away": {"runs": 0}, "home": {"runs": 1}}, {"away": {"runs": 2}, "home": {}}]}
    event = SportsDashboard._parse_mlb_game(game, LA)

    assert event["innings"] == [(0, 1), (2, None)]
    assert event["series_summary"] == "Series tied 2-2"
    assert event["series_game"] == "ALCS Game 5"
    assert event["decisions"] == [{"label": "W", "name": "N. Martinez"}, {"label": "L", "name": "D. Espino"}]
    assert event["broadcast"] == "TBS"
    assert event["team_a_color"] and event["team_b_color"]


def test_mlb_parse_skips_postponed_games():
    game = _mlb_game(status={"abstractGameState": "Final", "detailedState": "Postponed", "codedGameState": "D"})
    assert SportsDashboard._parse_mlb_game(game, LA) is None


def test_mlb_labels():
    live = {"state": "live", "inning": 7, "inning_state": "Top", "outs": 1}
    assert SportsDashboard._mlb_inning_label(live) == "TOP 7"
    assert SportsDashboard._mlb_pill_label(live, NOW) == "TOP 7 · 1 OUT"
    assert SportsDashboard._mlb_batting_side(live) == "a"
    assert SportsDashboard._mlb_batting_side({**live, "inning_state": "Middle"}) == ""
    extra = {"state": "final", "innings": [(0, 0)] * 10, "scheduled_innings": 9}
    assert SportsDashboard._mlb_pill_label(extra, NOW) == "FINAL/10"
    assert SportsDashboard._mlb_series_label({"series_summary": "Series tied 2-2"}) == "TIED 2-2"


def test_basketball_leaders_per_team_and_for_the_game():
    def competitor(points, name):
        return {"leaders": [{"name": "points", "leaders": [{"value": points, "displayValue": str(points), "athlete": {"shortName": name}}]}]}

    away, home = competitor(21, "H. Gonzalez"), competitor(27, "J. Embiid")
    assert SportsDashboard._basketball_team_leaders(away) == [{"label": "PTS", "name": "H. Gonzalez", "value": "21"}]
    assert SportsDashboard._basketball_game_leaders(away, home) == [{"label": "PTS", "name": "J. Embiid", "value": "27", "side": "b"}]


def test_basketball_series_helpers():
    main = {"team_a_code": "ATL", "team_b_code": "GS", "headline": "WNBA Finals - Game 2", "series_summary": "GS leads series 1-0", "series_length": 7, "state": "pre"}
    game_one = {"team_a_code": "ATL", "team_b_code": "GS", "headline": "WNBA Finals - Game 1", "state": "post", "wins_a": 64, "wins_b": 68, "start": NOW - timedelta(days=1)}
    other = {"team_a_code": "LV", "team_b_code": "SEA", "headline": "Regular Season", "state": "post", "start": NOW}
    series = SportsDashboard._basketball_series_games([main, game_one, other], main)

    assert series == [game_one, main] or series == [main, game_one]
    assert SportsDashboard._basketball_game_number(main) == 2
    assert SportsDashboard._basketball_series_title(main) == "WNBA FINALS"
    assert SportsDashboard._basketball_series_wins(main, series) == {"a": 0, "b": 1}
    assert SportsDashboard._basketball_series_label(main) == "GS LEADS 1-0"


def test_basketball_clutch_flag_and_ticker_clock():
    assert SportsDashboard._basketball_is_clutch({"period": 4, "wins_a": 100, "wins_b": 104}) is True
    assert SportsDashboard._basketball_is_clutch({"period": 3, "wins_a": 100, "wins_b": 101}) is False
    spec = _plugin()._basketball_spec("NBA")
    later = {"state": "pre", "start": NOW + timedelta(days=1, hours=6, minutes=30)}
    assert SportsDashboard._kit_ticker_clock(later, NOW, spec)[0] == "MON 6:30PM"


def test_pga_parse_rounds_dates_and_calendar():
    parsed = SportsDashboard._parse_pga_scoreboard(_sample_pga_scoreboard_payload(), LA, NOW)
    row = parsed["events"][0]["leaderboard"][0]
    assert isinstance(row["rounds"], list)
    event = {"start": datetime(2026, 10, 8, 4, 0, tzinfo=timezone.utc).astimezone(LA), "end": datetime(2026, 10, 11, 4, 0, tzinfo=timezone.utc).astimezone(LA)}
    assert SportsDashboard._pga_dates_label(event) == "10/08–10/11"
    assert SportsDashboard._pga_pill_label(event, "scheduled", NOW) == "THU 10/08"
    assert SportsDashboard._pga_row_rounds_label({"rounds": ["-3", "-4"]}, "final") == "R1 -3  R2 -4"
    assert SportsDashboard._pga_row_rounds_label({"thru": "14", "today": "-2"}, "live") == "THRU 14  TODAY -2"


def test_f1_labels_and_team_inks():
    assert SportsDashboard._f1_grand_prix_zh("United States Grand Prix") == "美国大奖赛"
    assert SportsDashboard._f1_grand_prix_zh("Made Up Grand Prix") == "Made Up Grand Prix"
    assert SportsDashboard._f1_team_ink_key("McLaren") == "amber"
    assert SportsDashboard._f1_team_ink_key("Scuderia Ferrari") == "red"
    assert SportsDashboard._f1_team_ink_key("") == "text"
    assert SportsDashboard._f1_gap_label({"gap": "7.335"}, 2, False) == "+7.335"
    assert SportsDashboard._f1_gap_label({"gap": "1:42:00.785"}, 1, False) == "1:42:00.785"
    assert SportsDashboard._f1_gap_label({"gap": "0.1"}, 1, True) == "LEADER"


def test_f1_selection_marks_the_race_weekend():
    data = SportsDashboard._parse_f1_jolpica_bundle(_sample_f1_jolpica_bundle(), LA)
    race_start = data["races"][0]["race_start"]
    assert SportsDashboard._select_f1_events(data, race_start - timedelta(days=1))["weekend"] is True
    assert SportsDashboard._select_f1_events(data, race_start - timedelta(days=30))["weekend"] is False


# Rendering ----------------------------------------------------------------------


@pytest.fixture
def boxes(monkeypatch):
    recorded = []
    original = SportsDashboard._draw_text_in_box

    def capture(draw, box, text, font, color, align="left"):
        recorded.append((tuple(box), str(text), font))
        return original(draw, box, text, font, color, align=align)

    monkeypatch.setattr(SportsDashboard, "_draw_text_in_box", staticmethod(capture))
    return recorded


def _render(draw_panel, colors=DAY_COLORS):
    plugin = _plugin()
    plugin._draw_team_logo = lambda *_args, **_kwargs: None
    token = _ACTIVE_COLORS.set(colors)
    try:
        image = Image.new("RGB", (556, 268), COLORS["paper"])
        draw = ImageDraw.Draw(image)
        draw_panel(plugin, image, draw)
    finally:
        _ACTIVE_COLORS.reset(token)
    return image, draw


def _assert_fits(draw, recorded):
    assert recorded
    for box, text, font in recorded:
        assert SportsDashboard._text_width(draw, text, font) <= box[2] - box[0] + 1, (text, box)


def _hub_card(sport, parsed, now):
    return SportsDashboard._offseason_hub_card(sport, parsed, now)


def _panels():
    sample_now = datetime(2026, 6, 14, 13, 30, tzinfo=LA)
    mlb = _hub_card("MLB", SportsDashboard._parse_mlb_scoreboard(_sample_mlb_scoreboard_payload(), LA), sample_now)
    wnba = _hub_card("WNBA", SportsDashboard._parse_wnba_scoreboard(_sample_wnba_scoreboard_payload(), LA), sample_now)
    pga = _hub_card("PGA", SportsDashboard._parse_pga_scoreboard(_sample_pga_scoreboard_payload(), LA, sample_now), sample_now)
    ncaa = _hub_card("NCAA", SportsDashboard._parse_football_scoreboard(_sample_ncaa_scoreboard_payload(), LA, "NCAA"), sample_now)
    nba_events = SportsDashboard._parse_nba_espn_events(_sample_nba_scoreboard_payload(), LA)
    nba = SportsDashboard._select_nba_events(nba_events, nba_events[0]["start"] + timedelta(minutes=30))
    nba["offseason"] = False
    f1_data = SportsDashboard._parse_f1_jolpica_bundle(_sample_f1_jolpica_bundle(), LA)
    f1_now = datetime(2026, 6, 14, 6, 45, tzinfo=LA)
    f1 = SportsDashboard._select_f1_events(f1_data, f1_now)
    f1["leaderboard"] = SportsDashboard._parse_f1_openf1_snapshot(_sample_openf1_snapshot())["leaderboard"]
    return {
        "mlb": lambda plugin, image, draw: plugin._draw_mlb_standalone_panel(image, draw, (0, 0, 555, 267), mlb, "HUB LIVE", sample_now),
        "wnba": lambda plugin, image, draw: plugin._draw_wnba_standalone_panel(image, draw, (0, 0, 555, 267), wnba, "HUB LIVE", sample_now),
        "pga": lambda plugin, image, draw: plugin._draw_pga_standalone_panel(image, draw, (0, 0, 555, 267), pga, "HUB LIVE", sample_now),
        "ncaa": lambda plugin, image, draw: plugin._draw_ncaa_standalone_panel(image, draw, (0, 0, 555, 267), ncaa, "HUB LIVE", sample_now),
        "nba": lambda plugin, image, draw: plugin._draw_nba_compact_panel(image, draw, (0, 0, 555, 267), nba, "ESPN LIVE", nba_events[0]["start"] + timedelta(minutes=30)),
        "f1": lambda plugin, image, draw: plugin._draw_f1_compact_panel(image, draw, (0, 0, 555, 267), f1, "JOLPICA LIVE", f1_now),
    }


@pytest.mark.parametrize("panel", ["mlb", "wnba", "pga", "ncaa", "nba", "f1"])
@pytest.mark.parametrize("colors", [DAY_COLORS, DEEP_NIGHT_COLORS], ids=["day", "night"])
def test_each_panel_renders_with_every_text_inside_its_box(panel, colors, boxes):
    image, draw = _render(_panels()[panel], colors)

    _assert_fits(draw, boxes)
    content = set(image.crop((12, 57, 543, 260)).get_flattened_data())
    assert tuple(colors["text"]) in content


def test_mlb_live_panel_lights_the_situation(boxes):
    _image, _draw = _render(_panels()["mlb"])
    texts = [text for _box, text, _font in boxes]
    assert "ON FIELD" in texts
    assert {"B", "S", "O"} <= set(texts)
    assert any(text.startswith("P ") or "AB " in text for text in texts)


def test_f1_live_panel_shows_the_session_order(boxes):
    _image, _draw = _render(_panels()["f1"])
    texts = [text for _box, text, _font in boxes]
    assert "LIVE NOW" in texts
    assert "DRIVERS" in texts
    assert any(text.startswith("LIVE · ") for text in texts)


# Competition marks ------------------------------------------------------------


def test_event_marks_match_their_manifest():
    manifest = json.loads((Path(EVENT_MARK_DIR) / "events_manifest.json").read_text(encoding="utf-8"))
    on_disk = {path.relative_to(EVENT_MARK_DIR).as_posix() for path in Path(EVENT_MARK_DIR).rglob("*.png")}
    assert on_disk == set(manifest["assets"])
    for relative, entry in manifest["assets"].items():
        path = Path(EVENT_MARK_DIR) / relative
        assert hashlib.sha256(path.read_bytes()).hexdigest() == entry["runtime_sha256"], relative
        with Image.open(path) as mark:
            assert mark.mode == "RGBA" and list(mark.size) == entry["runtime_dimensions"], relative
            assert max(mark.size) <= 300, relative
        assert entry["source_url"].startswith("https://"), relative


def _at(year, month=10, day=18):
    return datetime(year, month, day, 18, 0, tzinfo=timezone.utc)


def test_event_marks_are_looked_up_by_edition():
    assert SportsDashboard._mlb_series_mark_path({"series_game": "ALCS Game 5", "start": _at(2026)}).endswith("2026_alcs.png")
    assert SportsDashboard._mlb_series_mark_path({"series_game": "World Series Game 1", "start": _at(2026)}).endswith("2026_world_series.png")
    assert SportsDashboard._mlb_series_mark_path({"series_game": "ALCS Game 5", "start": _at(2027)}) is None
    assert SportsDashboard._mlb_series_mark_path({"series_game": "", "start": _at(2026)}) is None
    assert SportsDashboard._wnba_series_mark_path({"headline": "WNBA Finals - Game 2", "start": _at(2026)}).endswith("2026_finals.png")
    assert SportsDashboard._wnba_series_mark_path({"headline": "Semifinals - Game 2", "start": _at(2026)}) is None
    assert SportsDashboard._nba_event_mark_path({"headline": "Emirates NBA Cup - Group Play"}).endswith("nba_cup.png")
    assert SportsDashboard._nba_event_mark_path({"headline": ""}) is None
    assert SportsDashboard._pga_event_mark_path({"name_en": "Butterfield Bermuda Championship", "start": _at(2026)}).endswith("2026_bermuda.png")
    assert SportsDashboard._pga_event_mark_path({"name_en": "Mexico Open at VidantaWorld", "start": _at(2026)}).endswith("2026_mexico_open.png")
    assert SportsDashboard._pga_event_mark_path({"name_en": "Butterfield Bermuda Championship", "start": _at(2027)}) is None
    assert SportsDashboard._f1_circuit_mark_path({"circuit_id": "americas"}).endswith("americas.png")
    assert SportsDashboard._f1_circuit_mark_path({"circuit_id": "../americas"}).endswith("americas.png")
    assert SportsDashboard._f1_circuit_mark_path({"circuit_id": "nowhere"}) is None
    assert SportsDashboard._f1_country_flag_path({"country": "USA"}).endswith("us.png")
    assert SportsDashboard._f1_country_flag_path({"country": "UAE"}).endswith("ae.png")
    assert SportsDashboard._f1_country_flag_path({"country": "Atlantis"}) is None


@pytest.mark.parametrize("colors", [DAY_COLORS, DEEP_NIGHT_COLORS], ids=["day", "night"])
def test_circuit_outline_is_reinked_in_the_theme_colours(colors):
    plugin = _plugin()
    token = _ACTIVE_COLORS.set(colors)
    try:
        image = Image.new("RGB", (120, 60), colors["paper"])
        drawn = plugin._draw_event_mark(image, SportsDashboard._f1_circuit_mark_path({"circuit_id": "americas"}), (0, 0, 120, 60), line_art=True)
    finally:
        _ACTIVE_COLORS.reset(token)
    assert drawn is not None
    pixels = set(image.get_flattened_data())
    assert pixels <= {tuple(colors["paper"]), tuple(colors["text"]), tuple(colors["panel"])}
    assert tuple(colors["text"]) in pixels


def test_colour_marks_get_a_light_plate_at_night():
    plugin = _plugin()
    path = SportsDashboard._mlb_series_mark_path({"series_game": "ALCS Game 5", "start": _at(2026)})
    token = _ACTIVE_COLORS.set(DEEP_NIGHT_COLORS)
    try:
        image = Image.new("RGB", (140, 60), DEEP_NIGHT_COLORS["panel"])
        drawn = plugin._draw_event_mark(image, path, (10, 10, 130, 50))
    finally:
        _ACTIVE_COLORS.reset(token)
    assert image.getpixel((drawn[0] - 2, drawn[1] - 2)) == tuple(DEEP_NIGHT_COLORS["text"])


def test_layouts_draw_their_competition_marks(monkeypatch):
    drawn = []
    original = SportsDashboard._draw_event_mark

    def capture(self, image, path, box, align="center", line_art=False):
        drawn.append(Path(path).name if path else None)
        return original(self, image, path, box, align=align, line_art=line_art)

    monkeypatch.setattr(SportsDashboard, "_draw_event_mark", capture)
    plugin = _plugin()
    plugin._draw_team_logo = lambda *_args, **_kwargs: None
    image = Image.new("RGB", (556, 268), COLORS["paper"])
    draw = ImageDraw.Draw(image)

    pga_event = {
        "sport": "PGA", "state": "pre", "name": "Bermuda", "name_en": "Butterfield Bermuda Championship",
        "start": _at(2026, 10, 22), "end": _at(2026, 10, 25), "leaderboard": [],
    }
    plugin._draw_pga_standalone_panel(image, draw, (0, 0, 555, 267), {"sport": "PGA", "status": "NEXT", "main": pga_event, "events": [pga_event]}, "HUB LIVE", NOW)
    race = {"round": "18", "season": "2026", "race_name": "United States Grand Prix", "circuit_id": "americas", "country": "USA", "sessions": []}
    plugin._draw_f1_compact_panel(image, draw, (0, 0, 555, 267), {"status": "NEXT", "main_race": race, "weekend_sessions": []}, "JOLPICA LIVE", NOW)

    assert "2026_bermuda.png" in drawn
    assert "americas.png" in drawn
    assert "us.png" in drawn  # the host flag replaces the round badge


# Theme-only redraws ---------------------------------------------------------


@pytest.mark.parametrize("flag", ["_theme_render_only", "_inkypi_ewc_cache_only"])
def test_cache_only_lower_panel_sources_never_open_a_provider_session(monkeypatch, tmp_path, flag):
    import plugins.sports_dashboard.sports_dashboard as sports_dashboard_module

    def no_provider():
        raise AssertionError("a theme-only redraw must not open a provider session")

    monkeypatch.setattr(sports_dashboard_module, "get_http_session", no_provider)
    plugin = _plugin()
    monkeypatch.setattr(plugin, "_offseason_hub_cache_path", lambda: tmp_path / "hub.json")
    monkeypatch.setattr(plugin, "_f1_jolpica_cache_path", lambda: tmp_path / "jolpica.json")
    monkeypatch.setattr(plugin, "_f1_openf1_cache_path", lambda: tmp_path / "openf1.json")
    settings = {flag: True, "forceRefresh": True}
    now_utc = datetime.now(timezone.utc)

    assert plugin._load_offseason_hub_payload(settings, LA, NOW)[1] == "HUB NO DATA"
    assert plugin._load_f1_jolpica_bundle(settings, LA)[1] == "JOLPICA NO DATA"
    assert plugin._load_f1_openf1_snapshot(settings)[1] == "OPENF1 NO DATA"

    # An expired cache is shown as stale instead of being refreshed.
    expired = (now_utc - timedelta(days=3)).isoformat()
    hub = {"cache_key": plugin._offseason_hub_cache_key(settings, LA, now_utc), "payloads": {}, "fetched_at": expired}
    jolpica = {**_sample_f1_jolpica_bundle(), "cache_key": plugin._f1_jolpica_cache_key(settings, LA), "fetched_at": expired}
    openf1 = {"cache_key": plugin._f1_openf1_cache_key(settings), "snapshot": _sample_openf1_snapshot(), "fetched_at": expired}
    (tmp_path / "hub.json").write_text(json.dumps(hub), encoding="utf-8")
    (tmp_path / "jolpica.json").write_text(json.dumps(jolpica), encoding="utf-8")
    (tmp_path / "openf1.json").write_text(json.dumps(openf1), encoding="utf-8")

    assert plugin._load_offseason_hub_payload(settings, LA, NOW)[1] == "HUB STALE"
    assert plugin._load_f1_jolpica_bundle(settings, LA)[1] == "JOLPICA STALE"
    assert plugin._load_f1_openf1_snapshot(settings)[1] == "OPENF1 STALE"
