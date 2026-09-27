from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from plugins.sports_dashboard import nba_free_odds as odds
from plugins.sports_dashboard.sports_dashboard import SportsDashboard
from runtime.refresh_contracts import TaskCancelled


def payload(event_id="123", away="+160", home="-192"):
    return {"header":{"id":event_id}, "pickcenter":[{
        "provider":{"name":"Draft Kings"},
        "moneyline":{"away":{"close":{"odds":away}}, "home":{"close":{"odds":home}}},
    }]}


def test_prices_use_away_home_orientation_and_require_exact_event():
    assert odds.parse_summary_odds(payload(), "123") == {
        "team_a":"2.60", "team_b":"1.52", "bookmaker":"Draft Kings"}
    assert odds.parse_summary_odds(payload(), "456") == {}


@pytest.mark.parametrize("value", [None, "", 0, "NaN", "inf", "suspended", "-20"])
def test_invalid_or_missing_markets_never_produce_display_odds(value):
    assert odds.parse_summary_odds(payload(away=value), "123") == {}


def test_legacy_prices_and_even_money():
    data = payload()
    market = data["pickcenter"][0]
    market.pop("moneyline")
    market.update(awayTeamOdds={"moneyLine":"EVEN"}, homeTeamOdds={"moneyLine":-110})
    assert odds.parse_summary_odds(data, "123")["team_a"] == "2.00"
    assert odds.parse_summary_odds(data, "123")["team_b"] == "1.91"


def setup_loader(monkeypatch, tmp_path, responses):
    plugin = SportsDashboard({"id":"sports_dashboard"})
    monkeypatch.setattr(plugin, "_sports_dashboard_cache_dir", lambda:tmp_path)
    calls = []
    def request(_method, _url, **kwargs):
        calls.append(kwargs)
        response = responses[str(kwargs["params"]["event"])]
        if isinstance(response, Exception):
            raise response
        return SimpleNamespace(data=response)
    @contextmanager
    def client():
        yield SimpleNamespace(request_json=request)
    monkeypatch.setattr(odds, "create_single_attempt_http_client", client)
    now = datetime(2026,10,3,12,tzinfo=timezone.utc)
    event = {"event_id":"123", "start":now+timedelta(hours=8), "state":"pre"}
    return plugin, calls, now, event


def test_free_quotes_are_cached_and_expired_quotes_disappear_on_failure(monkeypatch, tmp_path):
    responses = {"123":payload()}
    plugin, calls, now, event = setup_loader(monkeypatch, tmp_path, responses)
    first = odds.attach_free_odds(plugin, [event], {}, now)
    assert first[0]["odds"]["team_a"] == "2.60"
    assert "odds" not in event
    assert odds.attach_free_odds(plugin, [event], {}, now+timedelta(minutes=29)) == first
    assert len(calls) == 1
    responses["123"] = TimeoutError()
    expired = odds.attach_free_odds(plugin, [event], {}, now+timedelta(minutes=31))
    assert "odds" not in expired[0]
    assert len(calls) == 2
    odds.attach_free_odds(plugin, [event], {}, now+timedelta(minutes=32))
    assert len(calls) == 2


def test_no_market_is_cached_without_inventing_odds(monkeypatch, tmp_path):
    plugin, calls, now, event = setup_loader(monkeypatch, tmp_path, {"123":{"header":{"id":"123"},"pickcenter":[]}})
    assert odds.attach_free_odds(plugin, [event], {}, now) == [event]
    assert odds.attach_free_odds(plugin, [event], {}, now+timedelta(minutes=1)) == [event]
    assert len(calls) == 1


def test_fetch_budget_skips_results_distant_games_and_cache_only_work(monkeypatch, tmp_path):
    responses = {str(i):payload(str(i)) for i in range(10)}
    plugin, calls, now, event = setup_loader(monkeypatch, tmp_path, responses)
    events = [dict(event, event_id=str(i)) for i in range(10)]
    excluded = [dict(event, state="post"), dict(event, start=now+timedelta(days=8))]
    assert odds.attach_free_odds(plugin, excluded, {}, now) == excluded
    assert not calls
    assert odds.attach_free_odds(plugin, events, {"_inkypi_ewc_cache_only":True}, now) == events
    assert not calls
    result = odds.attach_free_odds(plugin, events, {}, now)
    assert sum(bool(e.get("odds")) for e in result) == 5
    assert len(calls) == 5
    assert all(c["timeout"] <= 5 and c["max_bytes"] == 2*1024*1024 for c in calls)


def test_cancellation_propagates(monkeypatch, tmp_path):
    plugin, _, now, event = setup_loader(monkeypatch, tmp_path, {"123":TaskCancelled("cancel")})
    with pytest.raises(TaskCancelled):
        odds.attach_free_odds(plugin, [event], {}, now)
