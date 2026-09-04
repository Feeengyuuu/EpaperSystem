"""Provider-shaped inputs to F1 event decisions without a dashboard or runtime."""

from datetime import datetime, timedelta, timezone
from copy import deepcopy

from plugins.sports_dashboard.f1_domain import parse_bundle, select_events, should_poll


def schedule():
    return {"MRData": {"RaceTable": {"Races": [{
        "season": "2026", "round": "16", "raceName": "Example Grand Prix",
        "date": "2026-09-06", "time": "13:00:00Z",
        "Qualifying": {"date": "2026-09-05", "time": "13:00:00Z"},
        "Circuit": {"circuitName": "Example", "Location": {"country": "Italy"}},
    }]}}}


def test_provider_payload_becomes_sorted_local_sessions_without_mutating_input():
    payload = schedule()
    original = deepcopy(payload)
    data = parse_bundle(payload, timezone(timedelta(hours=8)))
    race = data["races"][0]
    assert race["race_name"] == "Example Grand Prix"
    assert [session["key"] for session in race["sessions"]] == ["Qualifying", "Race"]
    assert race["race_start"].hour == 21
    assert payload == original


def test_live_selection_and_polling_share_the_pregame_and_end_boundaries():
    data = parse_bundle(schedule(), timezone.utc)
    start = datetime(2026, 9, 6, 13, tzinfo=timezone.utc)
    assert select_events(data, start)["status"] == "LIVE"
    assert should_poll(data, start)
    assert not should_poll(data, start + timedelta(hours=4))
    assert select_events(data, start + timedelta(hours=4))["live_session"] is None


def test_empty_and_invalid_dates_do_not_create_live_sessions():
    payload = schedule()
    payload["MRData"]["RaceTable"]["Races"][0]["date"] = "not-a-date"
    data = parse_bundle(payload, timezone.utc)
    assert data["races"][0]["race_start"] is None
    assert select_events({}, datetime(2026, 9, 6, tzinfo=timezone.utc))["status"] == "BREAK"
