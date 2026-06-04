import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from plugins.stocktracker.stocktracker import ACCENT_ORANGE, CINNABAR, MALACHITE, PAPER, StockTracker  # noqa: E402
from utils.massive_market_data import MassiveBar  # noqa: E402


class FakeLoc:
    def __init__(self, values):
        self.values = values

    def __getitem__(self, key):
        date, column = key
        assert column == "Close"
        return self.values[date]

    def __setitem__(self, key, value):
        date, column = key
        assert column == "Close"
        self.values[date] = value


class FakeIloc:
    def __init__(self, values):
        self.values = values

    def __getitem__(self, index):
        return self.values[index]


class FakeCloseSeries:
    def __init__(self, values, index):
        self.values = list(values)
        self.index = list(index)
        self.iloc = FakeIloc(self.values)
        self.empty = len(self.values) == 0

    def dropna(self):
        pairs = [(index, value) for index, value in zip(self.index, self.values) if value is not None]
        return FakeCloseSeries([value for _, value in pairs], [index for index, _ in pairs])


class FakeHistory:
    def __init__(self, values):
        self.index = list(range(len(values)))
        self.loc = FakeLoc(dict(enumerate(values)))
        self.columns = ["Close"]
        self.empty = len(values) == 0

    def __getitem__(self, key):
        assert key == "Close"
        return FakeCloseSeries([self.loc.values[index] for index in self.index], self.index)

    def copy(self):
        return FakeHistory([self.loc.values[index] for index in self.index])


class FakeStock:
    def __init__(self):
        self.info = {"shortName": "Apple"}

    def history(self, **kwargs):
        if kwargs.get("prepost"):
            return FakeHistory([101.25])
        return FakeHistory([90.0, 100.0])


class FakeYFinance:
    def __init__(self, stock):
        self.stock = stock

    def Ticker(self, ticker):
        assert ticker == "AAPL"
        return self.stock


def _stock(symbol, prices, shares):
    current = prices[-1]
    first = prices[0]
    change = current - first
    return {
        "symbol": symbol,
        "name": symbol,
        "price": current,
        "change": change,
        "change_percent": (change / first) * 100 if first else 0,
        "shares": shares,
        "total_value": current * shares,
        "total_change": change * shares,
        "history": FakeHistory(prices),
    }


def _near_color_count(image, target, tolerance=8):
    return sum(
        1
        for y in range(image.height)
        for x in range(image.width)
        for pixel in (image.getpixel((x, y)),)
        if max(abs(pixel[index] - target[index]) for index in range(3)) <= tolerance
    )


def test_stock_dashboard_uses_color_theme_and_us_change_colors():
    plugin = StockTracker({"id": "stocktracker"})
    stock_data = [
        _stock("AAPL", [190, 192, 196], 10),
        _stock("TSLA", [260, 250, 240], 5),
        _stock("SPY", [520, 522, 525], 3),
    ]
    history_points = [
        {"date": "2026-05-30", "timestamp": "2026-05-30T05:30:00", "value": 4500.0},
        {"date": "2026-05-31", "timestamp": "2026-05-31T05:30:00", "value": 4900.0},
        {"date": "2026-06-01", "timestamp": "2026-06-01T05:30:00", "value": 4700.0},
    ]

    image = plugin._create_dashboard(stock_data, (800, 480), history_points)

    assert image.size == (800, 480)
    assert image.mode == "RGB"
    assert StockTracker._change_color(1.0) == MALACHITE
    assert StockTracker._change_color(-1.0) == CINNABAR
    assert _near_color_count(image, PAPER, tolerance=5) > 10_000
    assert _near_color_count(image, MALACHITE, tolerance=12) > 500
    assert _near_color_count(image, CINNABAR, tolerance=12) > 500
    assert image.getpixel((318, 188)) == ACCENT_ORANGE
    assert image.getpixel((540, 102)) == MALACHITE
    assert image.getpixel((762, 145)) == CINNABAR


def test_stock_tracker_records_one_snapshot_per_day(monkeypatch):
    plugin = StockTracker({"id": "stocktracker"})
    persisted_history = []
    writes = []

    monkeypatch.setattr(plugin, "_portfolio_history_path", lambda stock_data: "memory-history.json")
    monkeypatch.setattr(plugin, "_read_portfolio_history", lambda history_path: list(persisted_history))

    def write_history(history_path, history):
        writes.append((history_path, list(history)))
        persisted_history[:] = list(history)

    monkeypatch.setattr(plugin, "_write_portfolio_history", write_history)

    first = plugin._record_portfolio_snapshot(
        [_stock("AAPL", [100, 110], 2), _stock("TSLA", [50, 60], 1)],
        datetime(2026, 6, 1, 5, 30),
    )
    second = plugin._record_portfolio_snapshot(
        [_stock("AAPL", [100, 120], 2), _stock("TSLA", [50, 55], 1)],
        datetime(2026, 6, 1, 18, 45),
    )
    third = plugin._record_portfolio_snapshot(
        [_stock("AAPL", [100, 125], 2), _stock("TSLA", [50, 58], 1)],
        datetime(2026, 6, 2, 5, 30),
    )

    assert len(first) == 1
    assert len(second) == 1
    assert second[0]["date"] == "2026-06-01"
    assert second[0]["timestamp"] == "2026-06-01T18:45:00"
    assert second[0]["value"] == 295.0
    assert [point["date"] for point in third] == ["2026-06-01", "2026-06-02"]
    assert [point["value"] for point in third] == [295.0, 308.0]
    assert len(writes) == 3
    assert writes[-1][0] == "memory-history.json"


def test_stock_tracker_snapshot_uses_only_finite_numbers():
    plugin = StockTracker({"id": "stocktracker"})
    stock_data = [_stock("AAPL", [100, 110], 2)]
    stock_data[0]["total_change"] = float("nan")

    totals = plugin._portfolio_totals(stock_data)
    snapshot = plugin._portfolio_snapshot(stock_data, datetime(2026, 6, 1, 5, 30))
    normalized = plugin._normalize_portfolio_history_entry({
        "date": "2026-06-01",
        "timestamp": "2026-06-01T05:30:00",
        "value": 220.0,
        "change": float("nan"),
        "change_percent": float("nan"),
    })

    assert totals == (220.0, 0.0, 0.0)
    assert snapshot["change"] == 0.0
    assert snapshot["change_percent"] == 0.0
    assert normalized == {
        "date": "2026-06-01",
        "timestamp": "2026-06-01T05:30:00",
        "value": 220.0,
    }
    assert plugin._normalize_portfolio_history_entry({"date": "2026-06-01", "value": float("nan")}) is None


def test_stock_tracker_loads_direct_holdings_csv():
    csv_path = Path(__file__).resolve().parent / "fixtures" / "stock_holdings.csv"
    plugin = StockTracker({"id": "stocktracker"})
    period, holdings = plugin._portfolio_holdings_from_settings({
        "portfolio_csv_path": str(csv_path),
        "period": "1mo",
    })

    assert period == "1mo"
    assert holdings == [("AAPL", 246.30), ("NVDA", 245.29)]


def test_stock_tracker_loads_robinhood_activity_csv():
    csv_path = Path(__file__).resolve().parent / "fixtures" / "robinhood_activity.csv"
    plugin = StockTracker({"id": "stocktracker"})
    holdings = plugin._load_portfolio_csv(str(csv_path))

    assert holdings == [("AAPL", 8.0), ("NVDA", 9.0)]


def test_stock_tracker_prefers_extended_hours_quote(monkeypatch):
    fake_stock = FakeStock()
    fake_yf = FakeYFinance(fake_stock)
    monkeypatch.setattr("plugins.stocktracker.stocktracker._load_yfinance", lambda: fake_yf)
    plugin = StockTracker({"id": "stocktracker"})

    data = plugin._fetch_stock_data("AAPL", 2, "1mo")

    assert data["price"] == 101.25
    assert data["regular_price"] == 100.0
    assert data["quote_source"] == "extended_1m"
    assert data["extended_hours"] is True
    assert data["total_value"] == 202.5
    assert data["history"].loc[data["history"].index[-1], "Close"] == 101.25


def test_stock_tracker_can_fetch_stock_data_from_massive_without_yfinance(monkeypatch):
    class FakeMassiveClient:
        def fetch_daily_bars(self, ticker, period="1mo"):
            assert ticker == "AAPL"
            assert period == "1mo"
            return [
                MassiveBar("AAPL", "2026-06-01", 1780272000000, 100.0, 101.0, 99.0, 100.0, 1000.0),
                MassiveBar("AAPL", "2026-06-02", 1780358400000, 109.0, 111.0, 108.0, 110.0, 1500.0),
            ]

        def fetch_ticker_details(self, ticker):
            assert ticker == "AAPL"
            return {"name": "Apple Inc."}

    def fail_yfinance():
        raise AssertionError("yfinance should not be used when Massive returns data")

    monkeypatch.setattr("plugins.stocktracker.stocktracker._load_yfinance", fail_yfinance)
    plugin = StockTracker({"id": "stocktracker"})

    data = plugin._fetch_stock_data(
        "AAPL",
        2,
        "1mo",
        data_provider="auto",
        massive_client=FakeMassiveClient(),
    )

    assert data["data_provider"] == "massive"
    assert data["quote_source"] == "massive_daily"
    assert data["massive_symbol"] == "AAPL"
    assert data["name"] == "Apple Inc."
    assert data["price"] == 110.0
    assert data["change"] == 10.0
    assert data["change_percent"] == 10.0
    assert data["total_value"] == 220.0
    assert data["history"].loc["2026-06-02", "Close"] == 110.0
    assert StockTracker._source_label([data]) == "Massive market data"
