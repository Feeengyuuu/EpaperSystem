from contextlib import contextmanager
from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from plugins.sports_dashboard import espn_date_range as api
from plugins.sports_dashboard.sports_dashboard import SportsDashboard


def test_rejected_range_fetches_each_day_and_deduplicates_matches(monkeypatch):
    closed, calls = [], []
    session = SimpleNamespace(get=lambda *a, **kw:SimpleNamespace(status_code=400, close=lambda:closed.append(True)))
    def request(*args, **kwargs):
        day = kwargs["params"]["dates"]
        calls.append(day)
        return SimpleNamespace(data={"events":[{"id":"same"}, {"id":day}]})
    @contextmanager
    def client():
        yield SimpleNamespace(request_json=request)
    monkeypatch.setattr(api, "create_single_attempt_http_client", client)
    result = api.fetch_scoreboard(session, "https://site.web.api.espn.com/scoreboard", {"dates":"20260926-20260928", "limit":100})
    assert closed == [True]
    assert calls == ["20260926", "20260927", "20260928"]
    assert [event["id"] for event in result["events"]] == ["same", *calls]


def test_other_errors_do_not_trigger_date_fallback(monkeypatch):
    def fail():
        raise RuntimeError("provider error")
    session = SimpleNamespace(get=lambda *a, **kw:SimpleNamespace(status_code=503, raise_for_status=fail))
    monkeypatch.setattr(api, "create_single_attempt_http_client", lambda:pytest.fail("unexpected fallback"))
    with pytest.raises(RuntimeError, match="provider error"):
        api.fetch_scoreboard(session, "https://example.org/scoreboard", {"dates":"20260926-20260928"})


def test_team_golf_is_not_rendered_as_an_individual_player_leaderboard():
    tz = ZoneInfo("America/Los_Angeles")
    payload = {"events":[{"id":"cup", "date":"2026-09-24T12:00Z", "competitions":[{
        "competitors":[{"type":"team", "score":"7.5", "team":{"displayName":"USA"}},
                       {"type":"team", "score":"10.5", "team":{"displayName":"INTL"}}]}]}]}
    assert SportsDashboard._parse_pga_scoreboard(payload, tz, datetime(2026,9,27,tzinfo=tz))["events"] == []
