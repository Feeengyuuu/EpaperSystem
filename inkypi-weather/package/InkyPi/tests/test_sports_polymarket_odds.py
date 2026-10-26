"""Polymarket's crowd odds on the LoL sidebar's matches: parsing, team matching, attaching and caching."""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

import plugins.sports_dashboard.lol_worlds as lol_worlds_module
from plugins.sports_dashboard.sports_dashboard import SportsDashboard
from tests.test_sports_dashboard import _plugin

LA = ZoneInfo("America/Los_Angeles")
START = datetime(2026, 10, 26, 10, 0, tzinfo=LA)
MARKET = {"question": "LoL: T1 vs CTBC Flying Oyster (BO1) - Worlds Group Stage", "outcomes": ["T1", "CTBC Flying Oyster"],
          "prices": ["0.885", "0.115"], "start": "2026-10-26 17:00:00+00", "volume": 277688.2}


def test_a_moneyline_becomes_decimal_odds_shares_best_of_and_volume():
    offer = SportsDashboard._polymarket_offer(MARKET, LA)

    assert (offer["home_team"], offer["away_team"]) == ("T1", "CTBC Flying Oyster")
    assert (offer["home_odds"], offer["away_odds"]) == ("1.13", "8.70")
    assert offer["best_of"] == 1 and offer["start"] == START and offer["volume"] == pytest.approx(277688.2)
    assert SportsDashboard._polymarket_offer({**MARKET, "prices": ["1", "0"]}, LA) is None
    assert SportsDashboard._polymarket_offer({**MARKET, "outcomes": ["Yes"]}, LA) is None


@pytest.mark.parametrize("code, name, same", [
    ("CFO", "CTBC Flying Oyster", True), ("BLG", "Bilibili Gaming", True), ("T1", "T1", True),
    ("KC", "Karmine Corp", True), ("TSW", "Team Secret Whales", True), ("AL", "Anyone's Legend", True),
    ("KC", "Bilibili Gaming", False), ("TBD", "TBD", False),
])
def test_lolesports_codes_match_the_names_polymarket_uses(code, name, same):
    assert SportsDashboard._polymarket_team_is(code, name) is same


def test_odds_attach_in_the_events_order_and_bookmaker_odds_stay():
    plugin = _plugin()
    offer = SportsDashboard._polymarket_offer(MARKET, LA)
    events = [{"team_a": "CFO", "team_b": "T1", "start": START},
              {"team_a": "BLG", "team_b": "KC", "start": START + timedelta(hours=1)},
              {"team_a": "T1", "team_b": "CFO", "start": START, "odds": {"team_a": "1.20", "team_b": "4.50"}}]

    priced = plugin._attach_polymarket_odds(events, [offer])

    assert priced[0]["odds"]["team_a"] == "8.70" and priced[0]["odds"]["share_b"] == pytest.approx(0.885)
    assert priced[0]["odds"]["bookmaker"] == "Polymarket" and priced[0]["odds"]["best_of"] == 1
    assert "odds" not in priced[1]
    assert priced[2]["odds"] == {"team_a": "1.20", "team_b": "4.50"}
    # A day apart is another meeting of the same teams.
    assert "odds" not in plugin._attach_polymarket_odds([{**events[0], "start": START + timedelta(days=2)}], [offer])[0]


def test_offers_come_from_one_cached_request_and_never_on_a_theme_redraw(monkeypatch, tmp_path):
    plugin = _plugin()
    monkeypatch.setattr(plugin, "_sports_dashboard_cache_dir", lambda: tmp_path)
    calls = []

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return [{"question": MARKET["question"], "outcomes": '["T1", "CTBC Flying Oyster"]',
                     "outcomePrices": '["0.885", "0.115"]', "gameStartTime": MARKET["start"], "volume": "277688.2"}]

    class Session:
        def get(self, url, **kwargs):
            calls.append((url, kwargs["params"]))
            return Response()

    monkeypatch.setattr(lol_worlds_module, "get_http_session", lambda: Session())
    assert plugin._polymarket_lol_offers({"_theme_render_only": True}, LA) == []
    first = plugin._polymarket_lol_offers({}, LA)
    again = plugin._polymarket_lol_offers({}, LA)

    assert len(calls) == 1 and calls[0][1]["tag_id"] == lol_worlds_module.POLYMARKET_LOL_TAG_ID
    assert [offer["home_odds"] for offer in first] == ["1.13"] and again == first
    assert (tmp_path / "polymarket_lol.json").is_file()
