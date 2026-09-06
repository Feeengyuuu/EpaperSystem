from datetime import datetime, timedelta, timezone

import pytest
import requests

from tests.test_sports_cs2_follow import FeedResponse, cs_match, loader

SETTINGS = {"pandaScoreCs2HltvLogos": "false"}


def test_loader_reads_selected_series_schedule_instead_of_filtered_global_feed(monkeypatch, tmp_path):
    now = datetime(2026, 9, 6, 12, tzinfo=timezone.utc)
    live = cs_match(now, status="running", offset=-1)
    other = cs_match(now, match_id=502, offset=1)
    other["serie"]["id"] = 1002
    local = cs_match(now, match_id=503, offset=2, team_a="Small A", team_b="Small B", tournament_id=23002)
    plugin, device, session = loader(
        monkeypatch,
        tmp_path,
        {
            "running": [live],
            "upcoming": [other],
            "past": [],
            "series:1001": [other, local],
        },
    )
    settings = {"pandaScoreCs2HltvLogos": "false"}
    card, _ = plugin._load_pandascore_cs2_card(settings, device, timezone.utc, now)
    assert card["event_id"] == "series:1001"
    assert [row["match_id"] for row in card["upcoming"]] == ["503"]
    assert card["schedule_state"] == "fresh"
    assert session.calls[-1][0] == "series:1001"
    assert session.calls[-1][1]["sort"] == "begin_at"
    cached, _ = plugin._load_pandascore_cs2_card(settings, device, timezone.utc, now + timedelta(seconds=30))
    assert cached["upcoming"][0]["team_a"] == "Small A"
    assert len(session.calls) == 4


def test_event_schedule_orders_and_deduplicates_across_pages_without_team_filter(monkeypatch, tmp_path):
    now = datetime(2026, 9, 6, 12, tzinfo=timezone.utc)
    main = cs_match(now)
    later = cs_match(now, match_id=504, offset=5, team_a="Small A", team_b="Small B")
    next_match = cs_match(now, match_id=503, offset=3, team_a="Small C", team_b="Small D")
    bad = cs_match(now, match_id=502, offset=1)
    bad["serie_id"] = 999
    plugin, device, session = loader(
        monkeypatch,
        tmp_path,
        {
            "running": [],
            "upcoming": [main],
            "past": [],
            "series:1001": lambda params: (
                [main] * 100 if params["page[number]"] == 1 else [later, bad, next_match, later]
            ),
        },
    )
    card, _ = plugin._load_pandascore_cs2_card(SETTINGS, device, timezone.utc, now)
    assert [row["match_id"] for row in card["upcoming"]] == ["503", "504"]
    assert [p["page[number]"] for f, p in session.calls if f == "series:1001"] == [1, 2]


def test_switching_event_discards_old_schedule_and_uses_new_event_cache(monkeypatch, tmp_path):
    now = datetime(2026, 9, 6, 12, tzinfo=timezone.utc)
    old = cs_match(now, status="running", offset=-1)
    old_next = cs_match(now, match_id=503)
    new = cs_match(now, match_id=502, offset=1, event="New Cup")
    new["serie"]["id"] = 1002
    plugin, device, session = loader(
        monkeypatch,
        tmp_path,
        {
            "running": [old],
            "upcoming": [new],
            "past": [],
            "series:1001": [old_next],
            "series:1002": [],
        },
    )
    card, _ = plugin._load_pandascore_cs2_card(SETTINGS, device, timezone.utc, now)
    assert card["upcoming"][0]["match_id"] == "503"
    session.feeds.update(running=[], past=[{**old, "status": "finished"}])
    card, _ = plugin._load_pandascore_cs2_card(SETTINGS, device, timezone.utc, now + timedelta(minutes=4))
    assert card["event_id"] == "series:1002" and card["schedule_state"] == "fresh"
    assert card["upcoming"] == []
    assert session.calls[-1][0] == "series:1002"


def test_failed_schedule_retains_only_stale_own_rows_then_successful_empty_clears_them(monkeypatch, tmp_path):
    now = datetime(2026, 9, 6, 12, tzinfo=timezone.utc)
    plugin, device, session = loader(
        monkeypatch,
        tmp_path,
        {
            "running": [cs_match(now, status="running", offset=-1)],
            "upcoming": [],
            "past": [],
            "series:1001": [cs_match(now, match_id=503)],
        },
    )
    plugin._load_pandascore_cs2_card(SETTINGS, device, timezone.utc, now)
    session.feeds["series:1001"] = requests.Timeout("offline")
    card, source = plugin._load_pandascore_cs2_card(SETTINGS, device, timezone.utc, now + timedelta(minutes=16))
    assert card["main"]["feed_fresh"] and "PARTIAL" in source
    assert card["schedule_state"] == "stale" and not card["upcoming"][0]["feed_fresh"]
    count = len(session.calls)
    cached, _ = plugin._load_pandascore_cs2_card(
        {**SETTINGS, "_inkypi_ewc_cache_only": True},
        device,
        timezone.utc,
        now + timedelta(hours=1),
    )
    assert cached["schedule_state"] == "stale" and len(session.calls) == count
    session.feeds["series:1001"] = []
    cleared, _ = plugin._load_pandascore_cs2_card(SETTINGS, device, timezone.utc, now + timedelta(minutes=18))
    assert cleared["upcoming"] == [] and cleared["schedule_state"] == "fresh"


@pytest.mark.parametrize("failure", ["page2", "page_limit", "auth", "limit"])
def test_schedule_failures_are_unavailable_and_bounded(monkeypatch, tmp_path, failure):
    now = datetime(2026, 9, 6, 12, tzinfo=timezone.utc)
    row = cs_match(now, match_id=503)
    error = FeedResponse({}, "https://api.pandascore.co/series/1001/matches/upcoming")
    error.status_code = 401 if failure == "auth" else 429
    error.headers = {"Retry-After": "1800"}

    def pages(params):
        if failure in {"auth", "limit"}:
            return error
        if failure == "page2" and params["page[number]"] == 2:
            raise requests.Timeout("page two failed")
        return [row] * 100

    plugin, device, session = loader(
        monkeypatch,
        tmp_path,
        {
            "running": [cs_match(now, status="running", offset=-1)],
            "upcoming": [],
            "past": [],
            "series:1001": pages,
        },
    )
    card, source = plugin._load_pandascore_cs2_card(SETTINGS, device, timezone.utc, now)
    assert card["schedule_state"] == "unavailable" and card["upcoming"] == []
    assert "PARTIAL" in source
    count = len(session.calls)
    assert count == {"page2": 5, "page_limit": 6, "auth": 4, "limit": 4}[failure]
    plugin._load_pandascore_cs2_card(SETTINGS, device, timezone.utc, now + timedelta(seconds=30))
    assert len(session.calls) == count
    assert plugin._pandascore_cs2_calls_left(SETTINGS, now) == 720 - count
