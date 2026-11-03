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


def summary(event_id="123"):
    """A pregame summary: Phoenix (away, id 21) at Golden State (home, id 9)."""
    def injury(name, status, returns):
        return {"status": status, "athlete": {"shortName": name}, "details": {"returnDate": returns}}
    return {
        **payload(event_id),
        "header": {"id": event_id, "competitions": [{"competitors": [
            {"id": "9", "homeAway": "home"}, {"id": "21", "homeAway": "away"}]}]},
        "predictor": {"homeTeam": {"id": "9", "gameProjection": "55.3"}, "awayTeam": {"id": "21", "gameProjection": "44.7"}},
        "lastFiveGames": [
            {"team": {"id": "9"}, "events": [{"gameDate": f"2026-10-2{day}T02:00Z", "gameResult": result}
                                            for day, result in zip("97531", "WWWLW")]},
            {"team": {"id": "21"}, "events": [{"gameDate": "2026-10-31T02:00Z", "gameResult": "L"},
                                             {"gameDate": "2026-10-17T02:00Z", "gameResult": "W"}]},
        ],
        "leaders": [{"team": {"id": "9"}, "leaders": [{"name": "pointsPerGame", "leaders": [{"athlete": {"shortName": "S. Curry"}}]}]}],
        "injuries": [
            {"team": {"id": "9"}, "injuries": [
                injury("A. Horford", "Out", "2026-11-05"), injury("B. Williams", "Day-To-Day", "2026-11-02"),
                injury("S. Curry", "Questionable", ""), injury("K. Porzingis", "Out", "2026-12-01"),
                injury("M. Moody", "Out", "2026-12-01"), injury("X. Active", "Active", ""),
            ]},
            {"team": {"id": "21"}, "injuries": [injury("M. Williams", "Out", "2027-02-03T00:00Z")]},
        ],
    }


def test_summary_insights_orient_by_side_and_rank_the_injury_report():
    insights = odds.parse_summary_insights(summary(), "123")
    assert insights["predictor_a"] == pytest.approx(0.447) and insights["predictor_b"] == pytest.approx(0.553)
    assert insights["form_a"] == "WL" and insights["form_b"] == "WLWWW"
    # The season leader first, then those ruled out, then the game-time decisions; four at most.
    assert [(row["name"], row["status"], row["key"]) for row in insights["injuries_b"]] == [
        ("S. Curry", "GTD", True), ("A. Horford", "OUT", False), ("K. Porzingis", "OUT", False), ("M. Moody", "OUT", False)]
    assert insights["injuries_a"] == [{"name": "M. Williams", "status": "OUT", "return": "2027-02-03", "key": False}]
    assert odds.parse_summary_insights(summary(), "456") == {}


@pytest.mark.parametrize("predictor", [None, {}, {"homeTeam": {"id": "9", "gameProjection": "55"}},
                                       {"homeTeam": {"id": "9", "gameProjection": "80"}, "awayTeam": {"id": "21", "gameProjection": "80"}},
                                       {"homeTeam": {"id": "9", "gameProjection": "x"}, "awayTeam": {"id": "21", "gameProjection": "45"}}])
def test_incomplete_or_inconsistent_predictors_are_left_out(predictor):
    data = summary()
    data["predictor"] = predictor
    insights = odds.parse_summary_insights(data, "123")
    assert "predictor_a" not in insights and "predictor_b" not in insights
    assert insights["form_b"] == "WLWWW"


def test_summary_details_attach_with_or_without_free_prices(monkeypatch, tmp_path):
    plugin, calls, now, event = setup_loader(monkeypatch, tmp_path, {"123": summary()})
    priced = odds.attach_free_odds(plugin, [event], {}, now)[0]
    assert priced["odds"]["team_a"] == "2.60" and priced["predictor_b"] == pytest.approx(0.553)
    keyed = odds.attach_free_odds(plugin, [{**event, "odds": {"team_a": "1.10"}}], {}, now, include_odds=False)[0]
    assert keyed["odds"] == {"team_a": "1.10"} and keyed["form_a"] == "WL"
    assert len(calls) == 1


def test_pregame_faceoff_shows_form_predictor_and_injury_report(monkeypatch):
    from zoneinfo import ZoneInfo
    from PIL import Image, ImageDraw
    from plugins.sports_dashboard.common import _ACTIVE_COLORS, COLORS, DAY_COLORS, DEEP_NIGHT_COLORS

    la = ZoneInfo("America/Los_Angeles")
    now = datetime(2026, 11, 2, 16, 30, tzinfo=la)
    event = {"event_id": "123", "state": "pre", "start": now + timedelta(hours=1, minutes=30),
             "team_a": "Phoenix Suns", "team_b": "Golden State Warriors", "team_a_code": "PHX", "team_b_code": "GS",
             "team_a_color": "1d1160", "team_b_color": "fdb927", "record_a": "2-4", "record_b": "5-1",
             **odds.parse_summary_insights(summary(), "123")}
    plugin = SportsDashboard({"id": "sports_dashboard"})
    monkeypatch.setattr(plugin, "_load_team_logo_for_render", lambda *_args: None)
    drawn = []
    original = plugin._draw_text_in_box
    monkeypatch.setattr(plugin, "_draw_text_in_box", lambda draw, box, text, *args, **kwargs: (
        drawn.append(str(text)), original(draw, box, text, *args, **kwargs)))
    for colors in (DAY_COLORS, DEEP_NIGHT_COLORS):
        token = _ACTIVE_COLORS.set(colors)
        try:
            image = Image.new("RGB", (556, 268), COLORS["paper"])
            plugin._draw_basketball_faceoff(image, ImageDraw.Draw(image), (12, 57, 543, 261), event, [], now,
                                            plugin._basketball_spec("NBA"))
        finally:
            _ACTIVE_COLORS.reset(token)
    assert drawn.count("近5") == 4
    assert "WIN" in drawn and any(text.endswith("45%") for text in drawn)
    # Curry (a season leader) first, his game-time decision as 待定; the Suns' surname and return day.
    assert {"Curry", " 待定", "Williams", " 2/3"} <= set(drawn)
