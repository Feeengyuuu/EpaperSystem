"""Exercise the actual football loaders against ESPN's numeric-date contract."""

from copy import deepcopy
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from types import SimpleNamespace

import pytest

from plugins.sports_dashboard.sports_dashboard import SportsDashboard
import plugins.sports_dashboard.sports_dashboard as sports_module
from tests.test_sports_dashboard import _sample_club_espn_payload
from tests.test_sports_dashboard_csl_data import _sample_csl_scoreboard, _CSLHarness


NOW = datetime(2026, 9, 23, 12, tzinfo=timezone.utc)


class ESPN:
    def __init__(self, feeds):
        self.feeds = feeds
        self.calls = []
        self.closed = 0

    def get(self, url, *, params, **kwargs):
        token = params["dates"]
        self.calls.append((url, dict(params)))
        assert token.isdigit() and len(token) in (6, 8), "ESPN rejects hyphenated football dates"
        value = self.feeds.get(token, {"events": []})
        if isinstance(value, Exception):
            raise value
        owner = self

        class Response:
            def raise_for_status(self):
                pass

            def json(self):
                return deepcopy(value)

            def close(self):
                owner.closed += 1

        return Response()


def match(key="game", start="2026-10-03T19:00:00Z", state="pre", score="0"):
    event = deepcopy(_sample_club_espn_payload()["events"][0])
    event.update(id=key, date=start)
    competition = event["competitions"][0]
    competition["date"] = start
    competition["status"] = {"type": {"state": state, "completed": state == "post"}}
    competition["competitors"][0]["score"] = score
    return event


def plugin_for(monkeypatch, tmp_path, session):
    plugin = SportsDashboard({"id": "sports_dashboard"})
    plugin._sports_dashboard_cache_dir = lambda: tmp_path
    monkeypatch.setattr(sports_module, "get_http_session", lambda: session)
    return plugin


@pytest.mark.parametrize("league", ["PL", "PD", "BL1", "SA", "FL1", "MLS"])
def test_club_loader_recovers_each_live_failing_league(monkeypatch, tmp_path, league):
    session = ESPN({"202610": {"events": [match()]}})
    plugin = plugin_for(monkeypatch, tmp_path, session)
    payload, source, fetched = plugin._load_club_espn_league_payload(league, {}, timezone.utc, NOW)
    assert [event["id"] for event in payload["events"]] == ["game"]
    assert source == "ESPN LIVE" and fetched == NOW.isoformat()
    count = len(session.calls)
    warm, source, fetched = plugin._load_club_espn_league_payload(league, {}, timezone.utc, NOW + timedelta(minutes=1))
    assert warm == payload and source == "ESPN CACHE"
    assert len(session.calls) == count == session.closed


def test_csl_loader_recovers_numeric_date_queries(tmp_path):
    board = _sample_csl_scoreboard()
    board["events"][0]["date"] = "2026-09-24T11:35:00Z"
    session = ESPN({"202609": board})
    plugin = _CSLHarness(tmp_path, session)
    payload, source, fetched = plugin._load_csl_scoreboard({}, ZoneInfo("America/Los_Angeles"), NOW)
    assert payload["events"] == board["events"]
    assert source == "CSL ESPN LIVE" and fetched == NOW.isoformat()


def test_window_filters_cross_year_timezone_and_deduplicates_final(monkeypatch, tmp_path):
    final = match("edge", "2027-01-01T01:00:00Z", "post", "3")
    scheduled = match("edge", "2027-01-01T01:00:00Z")
    outside = match("outside", "2026-12-31T01:00:00Z")
    session = ESPN({"202612": {"events": [outside, final]}, "202701": {"events": [scheduled]}})
    from plugins.sports_dashboard.football_espn import fetch_scoreboard

    tz = ZoneInfo("America/Los_Angeles")
    attempts = []
    payload = fetch_scoreboard(
        session,
        "https://example.test/scoreboard",
        datetime(2026, 12, 31, tzinfo=tz),
        datetime(2027, 1, 1, tzinfo=tz),
        can_request=lambda: True,
        record_request=lambda: attempts.append(1),
    )
    assert payload["events"] == [final]
    assert len(attempts) == session.closed == 2


def test_month_limit_keeps_over_one_hundred_matches(monkeypatch, tmp_path):
    games = [match(str(index)) for index in range(155)]
    session = ESPN({"202610": {"events": games + [games[0]]}})
    plugin = plugin_for(monkeypatch, tmp_path, session)
    payload = plugin._fetch_club_espn_payload("PL", {}, NOW)
    assert len(payload["events"]) == 155
    assert all(int(params["limit"]) > 155 for _, params in session.calls)


@pytest.mark.parametrize(
    "bad",
    [
        TimeoutError("offline"),
        {},
        {"events": None},
        {"events": [match()] * 1000},
        {"events": [{"id": "bad", "date": "invalid"}]},
    ],
)
def test_failed_month_keeps_previous_payload_and_timestamp(monkeypatch, tmp_path, bad):
    session = ESPN({"202610": {"events": [match()]}})
    plugin = plugin_for(monkeypatch, tmp_path, session)
    original = plugin._load_club_espn_league_payload("PL", {}, timezone.utc, NOW)
    current = plugin._club_football_cache_path("espn", "PL")
    before = current.read_bytes()
    session.feeds["202609"] = bad
    payload, source, fetched = plugin._load_club_espn_league_payload("PL", {}, timezone.utc, NOW + timedelta(hours=7))
    assert payload == original[0] and source == "ESPN STALE" and fetched == original[2]
    assert current.read_bytes() == before


def test_request_budget_checked_before_each_month(monkeypatch, tmp_path):
    session = ESPN({})
    plugin = plugin_for(monkeypatch, tmp_path, session)
    with pytest.raises(RuntimeError, match="daily request limit"):
        plugin._fetch_club_espn_payload("PL", {"clubFootballEspnDailyLimit": 2}, NOW)
    assert len(session.calls) == session.closed == 2
    assert plugin._read_json_file(plugin._club_espn_state_path())["count"] == 2


@pytest.mark.parametrize("start,state", [("2026-09-23T12:05:00Z", "pre"), ("2026-09-23T11:30:00Z", "in")])
def test_live_overlay_warm_read_does_not_repeat_same_timestamp_fetch(monkeypatch, tmp_path, start, state):
    session = ESPN({"202609": {"events": [match("live", start, state)]}})
    plugin = plugin_for(monkeypatch, tmp_path, session)
    initial = plugin._load_club_espn_league_payload("PL", {}, timezone.utc, NOW)
    count = len(session.calls)
    warm = plugin._load_club_espn_league_payload("PL", {}, timezone.utc, NOW + timedelta(seconds=30))
    assert warm[0] == initial[0] and warm[1] == "ESPN CACHE"
    assert len(session.calls) == count
    session.feeds["202609"]["events"][0] = match("live", start, "post", "2")
    updated = plugin._load_club_espn_league_payload("PL", {}, timezone.utc, NOW + timedelta(seconds=61))
    assert len(session.calls) == count + 1
    assert updated[0]["events"][0]["competitions"][0]["competitors"][0]["score"] == "2"
    finished_count = len(session.calls)
    plugin._load_club_espn_league_payload("PL", {}, timezone.utc, NOW + timedelta(minutes=3))
    assert len(session.calls) == finished_count


@pytest.mark.parametrize("league", ["PL", "CSL"])
def test_cache_only_ignores_force_without_network_or_disk_writes(monkeypatch, tmp_path, league):
    session = ESPN({})
    plugin = plugin_for(monkeypatch, tmp_path, session)
    settings = {"_inkypi_ewc_cache_only": True, "forceRefresh": True}
    if league == "CSL":
        plugin._load_csl_scoreboard(settings, timezone.utc, NOW)
    else:
        plugin._load_club_espn_league_payload(league, settings, timezone.utc, NOW)
    assert session.calls == [] and list(tmp_path.iterdir()) == []


def test_production_retry_session_uses_counted_single_attempt_client(monkeypatch, tmp_path):
    import plugins.sports_dashboard.football_espn as football_module

    shared = ESPN({})
    shared._inkypi_adapter_retries = True
    single = ESPN({"202608": ConnectionError("one failed attempt")})
    released = []

    @contextmanager
    def owned():
        try:
            yield SimpleNamespace(session=single)
        finally:
            released.append(True)

    monkeypatch.setattr(football_module, "create_single_attempt_http_client", owned)
    plugin = plugin_for(monkeypatch, tmp_path, shared)
    with pytest.raises(ConnectionError, match="one failed attempt"):
        plugin._fetch_club_espn_payload("PL", {}, NOW)
    assert not shared.calls and shared._inkypi_adapter_retries is True
    assert len(single.calls) == 1 and released == [True]
    assert plugin._read_json_file(plugin._club_espn_state_path())["count"] == 1


def test_cache_only_retains_expired_cache_without_new_provenance(monkeypatch, tmp_path):
    session = ESPN({"202610": {"events": [match()]}})
    plugin = plugin_for(monkeypatch, tmp_path, session)
    original = plugin._load_club_espn_league_payload("PL", {}, timezone.utc, NOW)
    before = {path.name: path.read_bytes() for path in tmp_path.iterdir()}
    count = len(session.calls)
    result = plugin._load_club_espn_league_payload(
        "PL", {"_inkypi_ewc_cache_only": True, "forceRefresh": True}, timezone.utc, NOW + timedelta(days=1)
    )
    assert result == (original[0], "ESPN STALE", original[2])
    assert len(session.calls) == count
    assert {path.name: path.read_bytes() for path in tmp_path.iterdir()} == before
