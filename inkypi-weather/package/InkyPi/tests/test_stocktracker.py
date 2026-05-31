import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from plugins.stocktracker.stocktracker import CHART_MARKER_GREEN, CINNABAR, MALACHITE, PAPER, StockTracker  # noqa: E402


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

    image = plugin._create_dashboard(stock_data, (800, 480))

    assert image.size == (800, 480)
    assert image.mode == "RGB"
    assert StockTracker._change_color(1.0) == MALACHITE
    assert StockTracker._change_color(-1.0) == CINNABAR
    assert _near_color_count(image, PAPER, tolerance=5) > 10_000
    assert _near_color_count(image, MALACHITE, tolerance=12) > 500
    assert _near_color_count(image, CINNABAR, tolerance=12) > 500
    assert _near_color_count(image, CHART_MARKER_GREEN, tolerance=8) > 40


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
