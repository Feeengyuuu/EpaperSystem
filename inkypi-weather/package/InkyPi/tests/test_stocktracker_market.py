"""The stock page's Nasdaq extras: insider trading and short interest notes, and the Nasdaq-100
heat map that takes the holdings table's turn."""

import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import plugins.stocktracker.stocktracker as stocktracker_module  # noqa: E402
from plugins.stocktracker import market_map, market_signals  # noqa: E402
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
# api.nasdaq.com/api/quote/list-type/nasdaq100 (five of its 101 rows).
LISTED = [
    {"symbol": "AAPL", "companyName": "Apple Inc. Common Stock", "marketCap": "5,171,422,286,520",
     "lastSalePrice": "$355.82", "netChange": "$7.31", "percentageChange": "+2.10%", "deltaIndicator": "up"},
    {"symbol": "NVDA", "companyName": "NVIDIA Corporation Common Stock", "marketCap": "4,190,749,000,000",
     "lastSalePrice": "$173.79", "netChange": "$4.90", "percentageChange": "+2.90%", "deltaIndicator": "up"},
    {"symbol": "GOOGL", "companyName": "Alphabet Inc. Class A Common Stock", "marketCap": "3,608,227,530,000",
     "lastSalePrice": "$293.47", "netChange": "$6.09", "percentageChange": "+2.12%", "deltaIndicator": "up"},
    {"symbol": "GOOG", "companyName": "Alphabet Inc. Class C Capital Stock", "marketCap": "3,579,029,280,000",
     "lastSalePrice": "$290.62", "netChange": "$5.70", "percentageChange": "+2.00%", "deltaIndicator": "up"},
    {"symbol": "MSFT", "companyName": "Microsoft Corporation Common Stock", "marketCap": "3,869,000,000,000",
     "lastSalePrice": "$520.11", "netChange": "$12.04", "percentageChange": "-2.26%", "deltaIndicator": "down"},
    {"symbol": "WMT", "companyName": "Walmart Inc. Common Stock", "marketCap": "996,000,000,000",
     "lastSalePrice": "$124.90", "netChange": "$0.005", "percentageChange": "UNCH", "deltaIndicator": "up"},
]


def _listing(extra=60):
    """The literal rows plus plain members so the list is long enough to be the index."""
    filler = [{"symbol": f"Z{index:02d}", "companyName": f"Filler {index} Common Stock",
               "marketCap": f"{(extra - index) * 5_000_000_000:,}", "percentageChange": f"{(index % 9 - 4) * 0.8:+.2f}%"}
              for index in range(extra)]
    return {"data": {"totalrecords": 101, "date": "Nov 11, 2026 3:58 PM", "data": {"rows": LISTED + filler + ["junk"]}}}


def _context():
    return TaskContext.never_cancelled(deadline_monotonic=time.monotonic() + 30)


@pytest.mark.parametrize("kind", ["map", "signals"])
def test_optional_nasdaq_requests_propagate_cancellation(tmp_path, kind):
    def cancel(*_args):
        raise TaskCancelled("refresh canceled")

    with pytest.raises(TaskCancelled, match="refresh canceled"):
        if kind == "map":
            market_map.load(tmp_path, context=_context(), now=NOW, fetcher=cancel)
        else:
            market_signals.attach([{"symbol": "AAPL"}], tmp_path, context=_context(), now=NOW,
                                  fetchers={"insider": cancel}, budget=1)
    assert not list(tmp_path.glob("*.json"))


@pytest.mark.parametrize("kind", ["map", "signals"])
def test_an_optional_nasdaq_request_deadline_does_not_fail_the_page(tmp_path, kind):
    def timeout(*_args):
        raise TaskDeadlineExceeded("optional source budget")

    if kind == "map":
        assert market_map.load(tmp_path, context=_context(), now=NOW, fetcher=timeout) is None
    else:
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


def test_the_nasdaq_100_list_is_cut_down_to_symbol_value_and_change():
    data = market_map.members(_listing())

    assert data["as_of"] == "Nov 11, 2026 3:58 PM" and len(data["rows"]) == 66
    # The largest first; Alphabet's two classes share its value instead of doubling it.
    assert data["rows"][:3] == [["AAPL", 5171.42, 2.1], ["NVDA", 4190.75, 2.9], ["MSFT", 3869.0, -2.26]]
    by_symbol = {row[0]: row for row in data["rows"]}
    assert by_symbol["GOOGL"][1] == 1804.11 and by_symbol["GOOG"][1] == 1789.51
    assert by_symbol["WMT"][2] == 0.0 and market_map.valid(data)
    with pytest.raises(ValueError):
        market_map.members({"data": {"data": {"rows": LISTED}}})  # six rows are no index
    assert not market_map.valid({"rows": data["rows"][:10]}) and not market_map.valid(None)


def test_the_list_is_kept_half_an_hour_and_a_failure_keeps_the_last(tmp_path):
    calls = []

    def fetcher(_context):
        calls.append(1)
        if len(calls) == 2:
            raise HttpStatusError("GET", "nasdaq", 503)
        return market_map.members(_listing())

    first = market_map.load(tmp_path, context=_context(), now=NOW, fetcher=fetcher)
    assert market_map.load(tmp_path, context=_context(), now=NOW + timedelta(minutes=10), fetcher=fetcher) == first
    assert len(calls) == 1
    # Stale: asked again; the failure keeps the last list, and a redraw from cache asks nobody.
    assert market_map.load(tmp_path, context=_context(), now=NOW + timedelta(hours=1), fetcher=fetcher) == first
    assert len(calls) == 2
    assert market_map.load(tmp_path, context=_context(), now=NOW + timedelta(hours=2), allow_network=False,
                           fetcher=fetcher) == first and len(calls) == 2
    # Too old to show as today's market, and nothing without a cache.
    assert market_map.load(tmp_path, context=_context(), now=NOW + timedelta(days=5), allow_network=False) is None
    assert market_map.load(tmp_path / "empty", context=_context(), now=NOW, allow_network=False) is None


def test_the_map_fills_its_box_with_cells_as_large_as_their_values():
    values = [row[1] for row in market_map.members(_listing())["rows"]]
    box = (24, 234, 777, 463)
    rects = market_map.squarify(values, box)

    assert len(rects) == len(values)
    area = (box[2] - box[0]) * (box[3] - box[1])
    for value, (left, top, right, bottom) in zip(values, rects):
        assert box[0] - 1e-6 <= left < right <= box[2] + 1e-6 and box[1] - 1e-6 <= top < bottom <= box[3] + 1e-6
        assert (right - left) * (bottom - top) == pytest.approx(area * value / sum(values), rel=1e-6)
    # No two cells overlap, and the large ones are near squares, not slivers.
    for index, first in enumerate(rects):
        for second in rects[index + 1:]:
            assert (first[2] <= second[0] + 1e-6 or second[2] <= first[0] + 1e-6
                    or first[3] <= second[1] + 1e-6 or second[3] <= first[1] + 1e-6)
    for left, top, right, bottom in rects[:10]:
        assert max((right - left) / (bottom - top), (bottom - top) / (right - left)) < 3.2
    assert market_map.squarify([], box) == [] and market_map.squarify([1.0], (0, 0, 0, 5)) == []


def test_the_map_takes_one_display_turn_in_four():
    start = datetime(2026, 11, 11, 0, 0, tzinfo=timezone.utc)
    turns = [market_map.turn(start + timedelta(seconds=step * market_map.SLOT_SECONDS)) for step in range(8)]

    assert sorted(turns[:4]) == [0, 1, 2, 3] and turns[:4] == turns[4:]
    assert market_map.MAP_TURN == 3 and market_map.MAP_EVERY == 4
    # A turn lasts about as long as the frame takes to come round, and days do not repeat it.
    assert market_map.SLOT_SECONDS == 200 * 60 and (86400 / market_map.SLOT_SECONDS) % market_map.MAP_EVERY != 0


def _stock_data():
    return [_stock("AAPL", [100.0, 104.0], 5), _stock("NVDA", [50.0, 49.0], 10), _stock("MSFT", [300.0, 303.0], 2)]


@pytest.mark.parametrize("mode", ["day", "night"])
def test_the_heat_map_takes_the_holdings_place_and_leaves_the_cards_above(monkeypatch, mode):
    plugin = StockTracker({"id": "stocktracker"})
    market = market_map.members(_listing())
    theme = _canonical_theme(mode)
    texts = []
    original = ImageDraw.ImageDraw.text
    monkeypatch.setattr(ImageDraw.ImageDraw, "text", lambda self, xy, text, *args, **kwargs: texts.append(
        (xy[1], str(text), kwargs.get("fill"))) or original(self, xy, text, *args, **kwargs))

    usual = plugin._create_dashboard(_stock_data(), (800, 480), theme_context=theme, updated_at=datetime(2026, 11, 11, 13))
    texts.clear()
    mapped = plugin._create_dashboard(_stock_data(), (800, 480), theme_context=theme, updated_at=datetime(2026, 11, 11, 13),
                                      market=market)

    # The portfolio card and its trend are untouched; the holdings' box is the map.
    upper = (0, 0, 800, stocktracker_module.HOLDINGS_TOP - 4)
    assert mapped.crop(upper).tobytes() == usual.crop(upper).tobytes()
    lower = [(text, fill) for y, text, fill in texts if y > stocktracker_module.HOLDINGS_TOP]
    words = [text for text, _fill in lower]
    assert {"AAPL", "NVDA", "MSFT", "GOOGL", "GOOG", "+2.1%", "-2.3%", "HELD", "NOV 11 3:58 PM ET"} <= set(words)
    assert any(word.endswith("UP") for word in words) and "SYMBOL" not in words
    # Every label on the map is the page's ink; the colour is in the cells.
    colours = stocktracker_module._stock_render_colors(theme)
    assert {fill for _text, fill in lower} == {colours["ink"]}
    pixels = set(mapped.crop((20, 240, 780, 460)).get_flattened_data())
    assert plugin._blend(colours["malachite"], colours["panel"], 0.74 if mode == "day" else 0.4) in pixels
    assert plugin._blend(colours["cinnabar"], colours["panel"], 0.74 if mode == "day" else 0.4) in pixels
    # The cells of the holdings carry the corner mark: ink at the top right of Apple's.
    apple = market_map.squarify([row[1] for row in market["rows"]], (24, 234, 777, 463))[0]
    assert mapped.getpixel((round(apple[2]) - 3, round(apple[1]) + 1)) == colours["ink"]


def test_the_page_shows_the_map_only_on_its_turn_and_only_with_a_list(monkeypatch, tmp_path):
    monkeypatch.setenv("INKYPI_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("INKYPI_DATA_DIR", str(tmp_path / "data"))
    plugin = StockTracker({"id": "stocktracker"})
    monkeypatch.setattr(plugin, "_fetch_stock_data", lambda ticker, shares, period, **_kwargs: _stock(
        ticker, [100.0, 110.0], shares))
    monkeypatch.setattr(plugin, "_massive_client", lambda *_args: None)
    monkeypatch.setattr(plugin, "_record_portfolio_snapshot", lambda *_args, **_kwargs: [])
    settings = {"tickers": "AAPL,NVDA", "shares": "2,3", "period": "1mo", "data_provider": "yfinance",
                "_inkypi_theme": _canonical_theme("day")}
    shown, fetched = [], []
    original = plugin._create_dashboard
    monkeypatch.setattr(plugin, "_create_dashboard", lambda *args, **kwargs: shown.append(
        bool(kwargs.get("market"))) or original(*args, **kwargs))
    monkeypatch.setattr(market_map, "fetch", lambda _context: fetched.append(1) or market_map.members(_listing()))

    # A refresh on another turn still renews the list, and draws the holdings.
    monkeypatch.setattr(market_map, "turn", lambda _now: 1)
    plugin.generate_image(settings, FakeDeviceConfig())
    assert shown == [False] and fetched == [1]
    # On the map's turn a redraw from cache shows it without asking anybody.
    monkeypatch.setattr(market_map, "turn", lambda _now: market_map.MAP_TURN)
    monkeypatch.setattr(market_map, "fetch", lambda _context: pytest.fail("a redraw fetched the list"))
    plugin.generate_image({**settings, "_theme_render_only": True}, FakeDeviceConfig())
    assert shown[-1] is True
    # Without a list the holdings keep their place on that turn too.
    for path in (tmp_path / "cache").rglob(market_map.CACHE_FILE):
        path.unlink()
    plugin.generate_image({**settings, "_theme_render_only": True}, FakeDeviceConfig())
    assert shown[-1] is False
