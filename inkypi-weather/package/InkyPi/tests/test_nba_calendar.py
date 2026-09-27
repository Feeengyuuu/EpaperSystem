"""Provider-boundary regressions for ESPN date tokens and cache freshness."""

from datetime import datetime, timedelta, timezone
from copy import deepcopy
from contextlib import contextmanager
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from PIL import ImageDraw

from plugins.sports_dashboard.nba_calendar import load_calendar
import plugins.sports_dashboard.nba_calendar as calendar_module
from plugins.base_plugin.render_provenance import SourceProvenance
from plugins.sports_dashboard.sports_dashboard import SportsDashboard
import plugins.sports_dashboard.sports_dashboard as sports_module
from tests.test_sports_dashboard import FakeDeviceConfig, _sample_nba_scoreboard_payload


class ESPN:
    def __init__(self, feeds):
        self.feeds = feeds
        self.calls = []

    def get(self, url, *, params, **kwargs):
        token = params["dates"]
        self.calls.append(dict(params))
        assert token.isdigit() and len(token) in (6, 8), "ESPN NBA rejects date ranges"
        value = self.feeds.get(token, {"events": []})
        if isinstance(value, Exception):
            raise value

        class Response:
            def raise_for_status(self):
                pass

            def json(self):
                return value

        return Response()


def test_calendar_compaction_preserves_display_fields_without_analytics():
    import json
    event = deepcopy(_sample_nba_scoreboard_payload()["events"][0])
    for competitor in event["competitions"][0]["competitors"]:
        competitor["statistics"] = [{"name": "unused", "value": "x" * 10000} for _ in range(8)]
        competitor["team"]["links"] = [{"href": "https://example.org/" + "x" * 10000}]
    expected = SportsDashboard._parse_nba_espn_events({"events": [event]}, timezone.utc)
    compact = calendar_module._compact(event)
    assert SportsDashboard._parse_nba_espn_events({"events": [compact]}, timezone.utc) == expected
    assert len(json.dumps(compact)) < 10000


def test_month_queries_preserve_more_than_one_hundred_matches(monkeypatch, tmp_path):
    plugin = SportsDashboard({"id": "sports_dashboard"})
    plugin._sports_dashboard_cache_dir = lambda: tmp_path
    events = []
    template = _sample_nba_scoreboard_payload()["events"][0]
    for index in range(155):
        events.append({**template, "id": str(index), "date": "2026-10-20T23:00:00Z",
                       "competitions": [{**template["competitions"][0], "date": "2026-10-20T23:00:00Z"}]})
    session = ESPN({"202610": {"events": events}})
    monkeypatch.setattr(sports_module, "get_http_session", lambda: session)
    now = datetime(2026, 9, 23, 5, tzinfo=timezone.utc)
    payload = plugin._fetch_nba_scoreboard_payload(
        {"nbaLookbackDays": 0, "nbaLookaheadDays": 40}, timezone.utc, "test", now,
    )
    assert len(payload["scoreboard"]["events"]) == 155
    assert all(int(call["limit"]) > 155 for call in session.calls)


def match(key="game", start="2026-10-20T23:00:00Z", state="in", score="42"):
    event = deepcopy(_sample_nba_scoreboard_payload()["events"][0])
    event.update(id=key, date=start)
    competition = event["competitions"][0]
    competition["date"] = start
    competition["status"] = {"type": {"state": state, "completed": state == "post", "description": "Final" if state == "post" else "In Progress" if state == "in" else "Scheduled"}}
    event["status"] = competition["status"]
    competition["competitors"][0]["score"] = score
    return event


def calendar(tmp_path, session):
    plugin = SportsDashboard({"id": "sports_dashboard"})
    plugin._sports_dashboard_cache_dir = lambda: tmp_path

    def load(now, settings=None, tz=timezone.utc):
        return load_calendar(plugin, {"nbaLookbackDays": 0, "nbaLookaheadDays": 1, **(settings or {})}, tz, now, session)

    return plugin, load


def test_live_scores_refresh_only_match_day_and_stop_fast_polling_after_final(tmp_path):
    now = datetime(2026, 10, 20, 23, 30, tzinfo=timezone.utc)
    session = ESPN({"202610": {"events": [match(state="pre")]}, "20261020": {"events": [match()]}})
    plugin, load = calendar(tmp_path, session)
    first = load(now)
    assert first["source_state"] == "ESPN LIVE"
    assert len(session.calls) == 2
    cache_mtime = (tmp_path / "nba_calendar.json").stat().st_mtime_ns
    cached = load(now + timedelta(seconds=30))
    assert cached["source_state"] == "ESPN CACHE" and len(session.calls) == 2
    assert (tmp_path / "nba_calendar.json").stat().st_mtime_ns == cache_mtime
    session.feeds["20261020"] = {"events": [match(state="post", score="101")]}
    final = load(now + timedelta(seconds=181))
    assert [call["dates"] for call in session.calls] == ["20261020", "202610", "20261020"]
    assert final["scoreboard"]["events"][0]["competitions"][0]["competitors"][0]["score"] == "101"
    load(now + timedelta(seconds=362))
    assert len(session.calls) == 3, "A final must stop fast polling even when the monthly fixture still says scheduled"
    assert plugin._read_json_file(plugin._nba_scoreboard_state_path())["count"] == 3


def test_cross_year_months_deduplicate_and_filter_in_device_timezone(tmp_path):
    now = datetime(2026, 12, 31, 20, tzinfo=timezone.utc)
    january = match("jan", "2027-01-01T16:00:00Z", "pre")
    session = ESPN({
        "202612": {"events": [january, match("outside", "2026-12-30T23:00:00Z", "post")]},
        "202701": {"events": [january, match("later", "2027-01-02T18:00:00Z", "pre")]},
    })
    _plugin, load = calendar(tmp_path, session)
    result = load(now, tz=ZoneInfo("Asia/Shanghai"))
    assert {call["dates"] for call in session.calls} == {"20261231", "202612", "202701"}
    assert [event["id"] for event in result["scoreboard"]["events"]] == ["jan"]


def test_failed_month_retains_prior_data_and_recovers_without_reusing_empty_as_old_data(tmp_path):
    now = datetime(2026, 10, 20, 12, tzinfo=timezone.utc)
    session = ESPN({"202610": {"events": [match(start="2026-10-21T23:00:00Z", state="pre")]}})
    plugin, load = calendar(tmp_path, session)
    load(now)
    session.feeds["202610"] = TimeoutError("offline")
    failed = load(now + timedelta(hours=13))
    assert failed["source_state"] == "ESPN STALE"
    assert len(failed["scoreboard"]["events"]) == 1
    assert failed["shards"]["202610"]["fetched_at"] == now.isoformat()
    count = len(session.calls)
    load(now + timedelta(hours=13, seconds=30))
    assert len(session.calls) == count
    session.feeds["202610"] = {"events": []}
    recovered = load(now + timedelta(hours=13, minutes=6))
    assert recovered["source_state"] == "ESPN LIVE"
    assert recovered["scoreboard"]["events"] == []
    assert plugin._read_json_file(tmp_path / "nba_calendar.json")["snapshots"]["202610"]["events"] == []


@pytest.mark.parametrize("bad", [{}, {"events": [match()] * 1000}, {"events": [{"id": "bad", "date": "nonsense"}]}])
def test_invalid_or_truncated_month_cannot_replace_last_good_snapshot(tmp_path, bad):
    now = datetime(2026, 10, 20, 12, tzinfo=timezone.utc)
    session = ESPN({"202610": {"events": [match(start="2026-10-21T23:00:00Z", state="pre")]}})
    _plugin, load = calendar(tmp_path, session)
    load(now)
    session.feeds["202610"] = bad
    result = load(now + timedelta(hours=13))
    assert result["source_state"] == "ESPN STALE"
    assert [event["id"] for event in result["scoreboard"]["events"]] == ["game"]


def test_daily_budget_counts_actual_attempts_and_reserves_first_request_for_scores(tmp_path):
    now = datetime(2026, 10, 20, 23, 30, tzinfo=timezone.utc)
    session = ESPN({"20261020": {"events": [match()]}})
    plugin, load = calendar(tmp_path, session)
    result = load(now, {"nbaDailyLimit": 1})
    assert [call["dates"] for call in session.calls] == ["20261020"]
    assert plugin._read_json_file(plugin._nba_scoreboard_state_path())["count"] == 1
    assert result["source_state"] == "ESPN STALE"
    assert len(result["scoreboard"]["events"]) == 1


def test_cache_only_render_never_fetches_even_when_forced_or_missing(tmp_path):
    now = datetime(2026, 10, 20, 23, 30, tzinfo=timezone.utc)
    session = ESPN({})
    _plugin, load = calendar(tmp_path, session)
    result = load(now, {"_inkypi_ewc_cache_only": True, "forceRefresh": True})
    assert session.calls == [] and result["source_state"] == "NBA NO DATA"
    assert not (tmp_path / "nba_calendar.json").exists()


def test_empty_success_retracts_removed_game_from_monthly_schedule(tmp_path):
    now = datetime(2026, 10, 20, 23, 30, tzinfo=timezone.utc)
    session = ESPN({"202610": {"events": [match()]}})
    _plugin, load = calendar(tmp_path, session)
    result = load(now)
    assert result["source_state"] == "ESPN LIVE"
    assert result["scoreboard"]["events"] == []


def test_live_game_keeps_polling_its_provider_day_after_midnight(tmp_path):
    now = datetime(2026, 10, 21, 3, 59, tzinfo=timezone.utc)
    game = match(start="2026-10-21T03:00:00Z")
    session = ESPN({"202610": {"events": [game]}, "20261020": {"events": [game]}})
    _plugin, load = calendar(tmp_path, session)
    load(now)
    after_midnight = load(now + timedelta(minutes=4))
    assert {call["dates"] for call in session.calls[2:]} == {"20261020", "20261021"}
    assert after_midnight["scoreboard"]["events"][0]["id"] == "game"


def test_fresh_empty_scoreboard_renders_without_fixed_historical_results(monkeypatch, tmp_path):
    session = ESPN({})
    monkeypatch.setattr(sports_module, "get_http_session", lambda: session)
    monkeypatch.setenv("INKYPI_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("INKYPI_DATA_DIR", str(tmp_path / "data"))
    drawn = []
    original = ImageDraw.ImageDraw.text

    def record(draw, xy, text, *args, **kwargs):
        drawn.append(str(text))
        return original(draw, xy, text, *args, **kwargs)

    monkeypatch.setattr(ImageDraw.ImageDraw, "text", record)
    plugin = SportsDashboard({"id": "sports_dashboard"})
    image, source = plugin.render_isolated_region(
        {"nbaOffseasonPanelMode": "off", "nbaOddsEnabled": False,
         "nbaLookbackDays": 0, "nbaLookaheadDays": 1},
        FakeDeviceConfig(), region="lower",
    )
    assert image.size == (800, 480)
    assert source is SourceProvenance.LIVE
    assert not any("FALLBACK" in text or "94-90" in text or "94 : 90" in text for text in drawn)
    events, state = plugin._load_nba_events({"nbaLookbackDays": 0, "nbaLookaheadDays": 1}, ZoneInfo("America/Los_Angeles"))
    assert events == [] and state == "ESPN CACHE"


def test_corrupt_calendar_does_not_overwrite_last_good_aggregate(monkeypatch, tmp_path):
    now = datetime.now(timezone.utc)
    plugin = SportsDashboard({"id": "sports_dashboard"})
    plugin._sports_dashboard_cache_dir = lambda: tmp_path
    settings = {"nbaLookbackDays": 0, "nbaLookaheadDays": 1, "forceRefresh": True}
    good = {"events": [match(start=now.isoformat())]}
    plugin._write_json_file(plugin._nba_scoreboard_cache_path(), {
        "cache_key": plugin._nba_scoreboard_cache_key(settings, timezone.utc, now),
        "scoreboard": good, "fetched_at": (now - timedelta(hours=2)).isoformat(),
    })

    class Offline:
        def get(self, *args, **kwargs):
            raise TimeoutError("offline")

    monkeypatch.setattr(sports_module, "get_http_session", lambda: Offline())
    payload, state, _stamp = plugin._load_nba_scoreboard(settings, timezone.utc)
    assert payload == good and state == "ESPN STALE"
    assert plugin._read_json_file(plugin._nba_scoreboard_cache_path())["scoreboard"] == good


def test_request_accounting_uses_transport_without_hidden_adapter_retries(monkeypatch, tmp_path):
    now = datetime(2026, 10, 20, 23, 30, tzinfo=timezone.utc)
    shared = ESPN({})
    shared._inkypi_adapter_retries = True
    owned = ESPN({})

    @contextmanager
    def client():
        yield SimpleNamespace(session=owned)

    monkeypatch.setattr(calendar_module, "create_single_attempt_http_client", client)
    plugin, load = calendar(tmp_path, shared)
    result = load(now)
    assert result["source_state"] == "ESPN LIVE"
    assert shared.calls == [] and shared._inkypi_adapter_retries is True
    assert len(owned.calls) == plugin._read_json_file(plugin._nba_scoreboard_state_path())["count"] == 2


def test_offseason_empty_days_do_not_spend_hourly_request_budget(tmp_path):
    now = datetime(2026, 9, 23, 12, tzinfo=timezone.utc)
    session = ESPN({})
    _plugin, load = calendar(tmp_path, session)
    load(now)
    assert len(session.calls) == 2
    result = load(now + timedelta(hours=2))
    assert result["source_state"] == "ESPN CACHE"
    assert len(session.calls) == 2
