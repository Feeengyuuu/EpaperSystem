"""The stock page's Nasdaq extras: insider trading and short interest notes."""

import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import plugins.stocktracker.stocktracker as stocktracker_module  # noqa: E402
from plugins.stocktracker import market_signals  # noqa: E402
from plugins.stocktracker.stocktracker import StockTracker  # noqa: E402
from runtime.refresh_contracts import TaskCancelled, TaskContext, TaskDeadlineExceeded  # noqa: E402
from tests.test_stocktracker import FakeDeviceConfig, _canonical_theme, _stock  # noqa: E402
from utils.http_client import HttpStatusError  # noqa: E402

NOW = datetime(2026, 11, 11, 21, tzinfo=timezone.utc)
# api.nasdaq.com/api/company/AAPL/insider-trades?limit=5&type=ALL..., 2026-11-11 (its counts and
# the first transaction).
INSIDER = {"data": {"title": None, "numberOfTrades": {"asOf": None, "rows": [
    {"insiderTrade": "Number of Open Market Buys", "months3": "0", "months12": "0"},
    {"insiderTrade": "Number of Sells", "months3": "25", "months12": "45"},
    {"insiderTrade": "Total Insider Trades", "months3": "25", "months12": "45"}]},
    "transactionTable": {"totalRecords": "117", "table": {"rows": [
        {"insider": "NEWSTEAD JENNIFER", "relation": "Officer", "lastDate": "11/03/2026",
         "transactionType": "Automatic Sell", "ownType": "Direct", "sharesTraded": "2,399", "lastPrice": "$325.75",
         "sharesHeld": "29,997"}]}}}}
# api.nasdaq.com/api/quote/AAPL/short-interest?assetClass=stocks (its two newest reports).
SHORT = {"data": {"symbol": "aapl", "shortInterestTable": {"rows": [
    {"settlementDate": "10/30/2026", "interest": "144,791,472", "avgDailyShareVolume": "43,085,075",
     "daysToCover": 3.360595},
    {"settlementDate": "10/15/2026", "interest": "131,591,865", "avgDailyShareVolume": "35,498,663",
     "daysToCover": 3.706953}]}}}
# The same address for SPY: Nasdaq reports short interest for its own listed stocks only.
SHORT_NONE = {"data": None, "message": "Short interest is not available. Short interest is only supported for "
                                        "Nasdaq Listed stocks", "status": {"rCode": 200}}
def _context():
    return TaskContext.never_cancelled(deadline_monotonic=time.monotonic() + 30)


def test_optional_nasdaq_requests_propagate_cancellation(tmp_path):
    def cancel(*_args):
        raise TaskCancelled("refresh canceled")

    with pytest.raises(TaskCancelled, match="refresh canceled"):
        market_signals.attach([{"symbol": "AAPL"}], tmp_path, context=_context(), now=NOW,
                              fetchers={"insider": cancel}, budget=1)
    assert not list(tmp_path.glob("*.json"))


def test_an_optional_nasdaq_request_deadline_does_not_fail_the_page(tmp_path):
    def timeout(*_args):
        raise TaskDeadlineExceeded("optional source budget")

    assert market_signals.attach([{"symbol": "AAPL"}], tmp_path, context=_context(), now=NOW,
                                 fetchers={"insider": timeout}, budget=1) == [{"symbol": "AAPL"}]
    assert not list(tmp_path.glob("*.json"))


def test_insider_counts_and_the_move_of_short_interest_are_read_from_nasdaqs_replies():
    assert market_signals.insider_reading(INSIDER) == {"insider_buys_3m": 0, "insider_sells_3m": 25}
    # 144.8M shares short against 131.6M a fortnight before.
    assert market_signals.short_reading(SHORT) == {"short_change_percent": 10.0, "short_days_to_cover": 3.4,
                                                   "short_date": "2026-10-30"}
    assert market_signals.short_reading(SHORT_NONE) == {}
    with pytest.raises(ValueError):
        market_signals.insider_reading({"data": None})
    with pytest.raises(ValueError):
        market_signals.short_reading(None)


def test_a_refresh_reads_a_few_replies_and_works_round_the_holdings(tmp_path):
    calls = []
    fetchers = {"insider": lambda symbol, _context: calls.append(("insider", symbol)) or {
                    "insider_buys_3m": 1, "insider_sells_3m": 2},
                "short": lambda symbol, _context: calls.append(("short", symbol)) or (
                    {} if symbol == "SPY" else {"short_change_percent": 8.0, "short_date": "2026-10-30"})}

    def rows():
        return [{"symbol": "AAPL"}, {"symbol": "NVDA"}, {"symbol": "SPY"}, {"symbol": "BRK.B"}, {"symbol": "CASH"},
                {"symbol": "bad symbol"}]

    first = market_signals.attach(rows(), tmp_path, context=_context(), now=NOW, fetchers=fetchers, budget=4)
    # Four replies a refresh, in the holdings' order; nothing is asked for cash.
    assert calls == [("insider", "AAPL"), ("short", "AAPL"), ("insider", "NVDA"), ("short", "NVDA")]
    assert first[0]["insider_sells_3m"] == 2 and first[1]["short_change_percent"] == 8.0
    assert "insider_buys_3m" not in first[2] and "insider_buys_3m" not in first[4]
    calls.clear()
    second = market_signals.attach(rows(), tmp_path, context=_context(), now=NOW + timedelta(hours=1),
                                   fetchers=fetchers, budget=4)
    # The next refresh goes on to the holdings not read yet (a class share spelt with a dash).
    assert calls == [("insider", "SPY"), ("short", "SPY"), ("insider", "BRK-B"), ("short", "BRK-B")]
    assert second[0]["insider_buys_3m"] == 1 and second[2]["insider_sells_3m"] == 2
    assert "short_change_percent" not in second[2]  # none reported for a fund
    calls.clear()
    # Everything is kept: nothing is asked for a day later, the insider counts again after three
    # days (the oldest first), short interest after a week.
    market_signals.attach(rows(), tmp_path, context=_context(), now=NOW + timedelta(days=1), fetchers=fetchers)
    assert calls == []
    market_signals.attach(rows(), tmp_path, context=_context(), now=NOW + timedelta(days=3, hours=2),
                          fetchers=fetchers, budget=3)
    assert calls == [("insider", "AAPL"), ("insider", "NVDA"), ("insider", "SPY")]
    assert market_signals.FETCH_PER_REFRESH == 0  # the suite's refreshes ask for none (conftest)


def test_a_failed_reply_keeps_the_last_reading_and_waits_before_it_is_asked_again(tmp_path):
    calls = []

    def insider(symbol, _context):
        calls.append(symbol)
        if symbol == "AAPL" and len(calls) > 1:
            raise HttpStatusError("GET", "nasdaq", 503)
        return {"insider_buys_3m": 0, "insider_sells_3m": 25}

    fetchers = {"insider": insider, "short": lambda *_args: {}}
    market_signals.attach([{"symbol": "AAPL"}], tmp_path, context=_context(), now=NOW, fetchers=fetchers, budget=2)
    later = NOW + timedelta(days=4)
    rows = market_signals.attach([{"symbol": "AAPL"}], tmp_path, context=_context(), now=later, fetchers=fetchers,
                                 budget=2)
    assert calls == ["AAPL", "AAPL"] and rows[0]["insider_sells_3m"] == 25
    # Not at the next refresh, but after the wait.
    market_signals.attach([{"symbol": "AAPL"}], tmp_path, context=_context(), now=later + timedelta(hours=1),
                          fetchers=fetchers, budget=2)
    assert len(calls) == 2
    market_signals.attach([{"symbol": "AAPL"}], tmp_path, context=_context(),
                          now=later + timedelta(seconds=market_signals.RETRY_SECONDS + 60), fetchers=fetchers, budget=2)
    assert len(calls) == 3
    # A reading too old to stand for the company is left off.
    old = market_signals.attach([{"symbol": "AAPL"}], tmp_path, context=_context(),
                                now=NOW + timedelta(seconds=market_signals.USABLE_SECONDS + 86400), fetchers=fetchers,
                                budget=0)
    assert "insider_sells_3m" not in old[0]


def test_the_title_row_notes_insider_trading_and_short_interest():
    plugin = StockTracker({"id": "stocktracker"})
    metrics = [
        {"data": {"symbol": "AAPL", "insider_buys_3m": 0, "insider_sells_3m": 25, "short_change_percent": 10.0},
         "is_cash": False},
        {"data": {"symbol": "NVDA", "insider_buys_3m": 2, "insider_sells_3m": 40, "short_change_percent": -3.0},
         "is_cash": False},
        {"data": {"symbol": "MSFT", "insider_buys_3m": 0, "insider_sells_3m": 0, "short_change_percent": -12.4,
                  "ex_dividend_date": "2026-11-19"}, "is_cash": False},
        {"data": {"symbol": "CASH", "insider_sells_3m": 9}, "is_cash": True}]

    assert plugin._holding_signals(metrics) == [
        ("内部人3月", "accent_orange", [("NVDA", "买2卖40"), ("AAPL", "卖25")]),
        ("空头", "cinnabar", [("MSFT", "-12%"), ("AAPL", "+10%")])]

    for item in metrics:
        item.update({"day_percent": 1.0, "value": 100.0})

    def title_row(rows):
        """(texts with their x, where the status on the right begins) of the HOLDINGS title row."""
        image = Image.new("RGB", (800, 300), (255, 253, 240))
        draw = ImageDraw.Draw(image)
        texts = []
        original = draw.text
        draw.text = lambda xy, text, **kwargs: texts.append((round(xy[0]), text)) or original(xy, text, **kwargs)
        status_left = plugin._draw_holdings_status(draw, (770, 30), rows, "1M RETURN", brief=bool(plugin._holding_signals(rows)))
        plugin._draw_holding_events(image, draw, (158, status_left - 18), 30, rows, date(2026, 11, 11))
        return texts, status_left

    texts, status_left = title_row(metrics)
    words = [text for _x, text in texts]
    # The dates keep their place and the two new pills follow, every time: with notes to show, the
    # status on the right is cut to its figures (the window is named on the trend card and column).
    assert words.index("除息") < words.index("内部人3月") < words.index("空头")
    # Each pill shows its first holding before either shows a second, so both are always seen.
    assert {"买2卖40", "-12%", "11/19", "3 POS", "3", "0"} <= set(words)
    assert ("卖25" in words) >= ("+10%" in words)
    assert not {"1M RETURN", "3 POSITIONS", "3 UP"} & set(words)
    # With the row to themselves the pills list two holdings each.
    for item in metrics:
        item["data"].pop("ex_dividend_date", None)
    assert {"买2卖40", "卖25", "-12%", "+10%"} <= {text for _x, text in title_row(metrics)[0]}
    assert all(x < status_left for x, text in texts if text in ("买2卖40", "卖25", "-12%", "+10%", "空头"))
    # Without a note the title row is the one it was.
    plain = [{"data": {"symbol": "MSFT", "ex_dividend_date": "2026-11-19"}, "is_cash": False, "day_percent": 1.0}]
    plain_texts, plain_left = title_row(plain)
    assert {"1 POSITIONS", "1 UP", "0 DOWN", "1M RETURN", "除息"} <= {text for _x, text in plain_texts}
    assert plain_left < status_left


def _stock_data():
    return [_stock("AAPL", [100.0, 104.0], 5), _stock("NVDA", [50.0, 49.0], 10), _stock("MSFT", [300.0, 303.0], 2)]


def test_the_holdings_table_has_its_place_on_every_display_turn(monkeypatch):
    # The Nasdaq-100 heat map that took one display turn in four is gone (owner, 2026-11-12).
    assert not hasattr(stocktracker_module, "market_map") and not hasattr(stocktracker_module, "MAP_STEPS")
    assert not (Path(stocktracker_module.__file__).parent / "market_map.py").exists()
    plugin = stocktracker_module.StockTracker({"id": "stocktracker"})
    assert not hasattr(plugin, "_draw_market_map") and not hasattr(plugin, "_market_map")
    drawn = []
    draw_holdings = plugin._draw_holdings
    monkeypatch.setattr(plugin, "_draw_holdings", lambda *args, **kwargs: drawn.append(1) or draw_holdings(*args, **kwargs))
    image = plugin._create_dashboard(_stock_data(), (800, 480), theme_context={"mode": "day"},
                                     updated_at=datetime(2026, 11, 11, 13))
    assert image.size == (800, 480) and drawn == [1]
