"""Discovery must not reserve the shared esports sidebar between match days."""

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest
import requests

from plugins.sports_dashboard.sports_dashboard import SportsDashboard
from tests.test_sports_cs2_follow import cs_match, loader

SETTINGS = {"pandaScoreCs2HltvLogos": "false"}
TZ = ZoneInfo("America/Los_Angeles")


def select(plugin, card, source, now, lol_cards=()):
    card["source_state"] = source
    valve = plugin._select_valve_esports([card], now)
    return plugin._select_right_esports_sidebar(lol_cards, valve, source, now)


def lol_card(now, hours=1, league="LPL", live=False):
    event = {"start": now + timedelta(hours=hours), "state": "inProgress" if live else "unstarted"}
    return {
        "league_key": league,
        "selected": {"main": event, "live": [event] if live else [], "upcoming": [] if live else [event]},
        "source_state": "LIVE DATA",
        "priority": 0 if league == "LPL" else 1,
    }


def test_future_discovery_is_cached_without_reserving_sidebar(monkeypatch, tmp_path):
    now = datetime(2026, 9, 6, 20, tzinfo=TZ)
    main = cs_match(now, offset=11)
    own_next = cs_match(now, match_id=502, offset=5, team_a="Small A", team_b="Small B")
    plugin, device, session = loader(monkeypatch, tmp_path, {"upcoming": [main], "series:1001": [own_next]})
    card, source = plugin._load_pandascore_cs2_card(SETTINGS, device, TZ, now)
    assert card["main"]["match_id"] == "501"
    assert card["upcoming"][0]["match_id"] == "502"
    assert (tmp_path / "pandascore_cs2_follow.json").exists()
    choice = select(plugin, card, source, now)
    assert choice["kind"] == "lol" and choice["choice"]["league_key"] == "LPL"
    count = len(session.calls)
    cached, source = plugin._load_pandascore_cs2_card(SETTINGS, device, TZ, now + timedelta(seconds=30))
    assert len(session.calls) == count
    assert not cached["window_active"]


@pytest.mark.parametrize("minutes,expected", [(0, "lol"), (2, "valve")])
def test_cached_discovery_enters_on_local_match_day(monkeypatch, tmp_path, minutes, expected):
    now = datetime(2026, 9, 6, 23, 59, tzinfo=TZ)
    plugin, device, session = loader(monkeypatch, tmp_path, {"upcoming": [cs_match(now)]})
    plugin._load_pandascore_cs2_card(SETTINGS, device, TZ, now)
    count = len(session.calls)
    current = now + timedelta(minutes=minutes)
    card, source = plugin._load_pandascore_cs2_card(
        {**SETTINGS, "_inkypi_ewc_cache_only": True}, device, TZ, current
    )
    assert len(session.calls) == count
    assert select(plugin, card, source, current)["kind"] == expected


def test_own_schedule_can_make_today_active_before_followed_match(monkeypatch, tmp_path):
    now = datetime(2026, 9, 6, 12, tzinfo=TZ)
    main = cs_match(now, offset=24)
    local = cs_match(now, match_id=502, offset=1, team_a="Small A", team_b="Small B")
    plugin, device, _ = loader(monkeypatch, tmp_path, {"upcoming": [main], "series:1001": [local]})
    card, source = plugin._load_pandascore_cs2_card(SETTINGS, device, TZ, now)
    assert select(plugin, card, source, now)["kind"] == "valve"
    # An expired event schedule cannot reopen today's slot from an old row.
    cached, source = plugin._load_pandascore_cs2_card(
        {**SETTINGS, "_inkypi_ewc_cache_only": True}, device, TZ, now + timedelta(minutes=16)
    )
    assert not cached["window_active"]


def test_live_match_keeps_slot_across_midnight_but_yields_to_live_lpl(monkeypatch, tmp_path):
    now = datetime(2026, 9, 7, 0, 30, tzinfo=TZ)
    plugin, device, _ = loader(monkeypatch, tmp_path, {"running": [cs_match(now, status="running", offset=-2)]})
    card, source = plugin._load_pandascore_cs2_card(SETTINGS, device, TZ, now)
    assert select(plugin, card, source, now)["kind"] == "valve"
    choice = select(plugin, card, source, now, [lol_card(now, live=True)])
    assert choice["kind"] == "lol" and choice["choice"]["league_key"] == "LPL"


@pytest.mark.parametrize("main_hours,next_hours,expected", [(1, 3, "valve"), (3, 1, "valve"), (3, 4, "lol")])
def test_cross_module_next_time_includes_main_and_event_schedule(monkeypatch, tmp_path, main_hours, next_hours, expected):
    now = datetime(2026, 9, 6, 12, tzinfo=timezone.utc)
    main = cs_match(now, offset=main_hours)
    own_next = cs_match(now, match_id=502, offset=next_hours)
    plugin, device, _ = loader(monkeypatch, tmp_path, {"upcoming": [main], "series:1001": [own_next]})
    card, source = plugin._load_pandascore_cs2_card(SETTINGS, device, timezone.utc, now)
    assert select(plugin, card, source, now, [lol_card(now, hours=2)])["kind"] == expected


def test_finished_event_releases_slot_with_next_event_kept_for_tomorrow(monkeypatch, tmp_path):
    now = datetime(2026, 9, 6, 20, tzinfo=TZ)
    future = cs_match(now, match_id=502, offset=11)
    future["serie"]["id"] = 1002
    plugin, device, _ = loader(monkeypatch, tmp_path, {
        "past": [cs_match(now, status="finished", offset=-2)], "upcoming": [future]
    })
    card, source = plugin._load_pandascore_cs2_card(SETTINGS, device, TZ, now)
    assert card["event_id"] == "series:1002"
    assert select(plugin, card, source, now)["kind"] == "lol"


def test_stale_own_schedule_cannot_beat_earlier_trusted_lpl(monkeypatch, tmp_path):
    now = datetime(2026, 9, 6, 12, tzinfo=TZ)
    plugin, device, session = loader(monkeypatch, tmp_path, {
        "upcoming": [cs_match(now, offset=4)],
        "series:1001": [cs_match(now, match_id=502, offset=1)],
    })
    plugin._load_pandascore_cs2_card(SETTINGS, device, TZ, now)
    session.feeds["series:1001"] = requests.Timeout("offline")
    current = now + timedelta(minutes=16)
    card, source = plugin._load_pandascore_cs2_card(SETTINGS, device, TZ, current)
    assert card["window_active"] and card["schedule_state"] == "stale"
    assert card["main"]["feed_fresh"] and "PARTIAL" in source
    assert select(plugin, card, source, current, [lol_card(now, hours=2)])["kind"] == "lol"


def test_successful_empty_schedule_releases_today_without_erasing_future_main(monkeypatch, tmp_path):
    now = datetime(2026, 9, 6, 12, tzinfo=TZ)
    plugin, device, session = loader(monkeypatch, tmp_path, {
        "upcoming": [cs_match(now, offset=24)],
        "series:1001": [cs_match(now, match_id=502, offset=1)],
    })
    card, _ = plugin._load_pandascore_cs2_card(SETTINGS, device, TZ, now)
    assert card["window_active"]
    session.feeds["series:1001"] = []
    current = now + timedelta(minutes=16)
    card, source = plugin._load_pandascore_cs2_card(SETTINGS, device, TZ, current)
    assert card["main"]["match_id"] == "501" and card["upcoming"] == []
    assert select(plugin, card, source, current)["kind"] == "lol"


@pytest.mark.parametrize("status,offset", [("not_started", -2), ("finished", -0.25)])
def test_yesterdays_pending_or_finished_match_does_not_hold_slot(monkeypatch, tmp_path, status, offset):
    now = datetime(2026, 9, 7, 0, 30, tzinfo=TZ)
    feed = "upcoming" if status == "not_started" else "past"
    plugin, device, _ = loader(monkeypatch, tmp_path, {feed: [cs_match(now, status=status, offset=offset)]})
    card, source = plugin._load_pandascore_cs2_card(SETTINGS, device, TZ, now)
    assert select(plugin, card, source, now)["kind"] == "lol"
