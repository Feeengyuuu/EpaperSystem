import sys
import json
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import plugins.stocktracker.stocktracker as stocktracker_module  # noqa: E402
from plugins.stocktracker.stocktracker import (  # noqa: E402
    ACCENT_BLUE,
    ACCENT_GOLD,
    ACCENT_ORANGE,
    CINNABAR,
    INK,
    MALACHITE,
    PANEL,
    PANEL_GOLD,
    PAPER,
    WHITE,
    SECTION_WORDMARK_IMAGES,
    StockTracker,
)
from utils.massive_market_data import MassiveBar  # noqa: E402
from plugins.base_plugin.presentation import PresentationMode  # noqa: E402
from plugins.base_plugin.render_provenance import (  # noqa: E402
    SourceProvenance,
    attach_source_provenance,
)


class FakeDeviceConfig:
    def __init__(self, resolution=(800, 480), timezone="America/Los_Angeles"):
        self.resolution = resolution
        self.timezone = timezone

    def get_resolution(self):
        return self.resolution

    def get_config(self, key=None, default=None):
        values = {"orientation": "horizontal", "timezone": self.timezone}
        if key is None:
            return values
        return values.get(key, default)

    def load_env_key(self, _key):
        return None


def _canonical_theme(mode):
    if mode == "night":
        palette = {
            "background": (11, 21, 12),
            "panel": (19, 35, 21),
            "ink": (244, 252, 245),
            "muted": (174, 194, 177),
            "rule": (65, 100, 70),
            "accent": (127, 213, 138),
        }
    else:
        palette = {
            "background": (241, 244, 236),
            "panel": (252, 253, 248),
            "ink": (18, 31, 20),
            "muted": (75, 94, 78),
            "rule": (165, 181, 168),
            "accent": (61, 122, 69),
        }
    return {
        "requested_mode": "auto",
        "mode": mode,
        "source": "weather",
        "reason": "sunrise/sunset",
        "date": "2026-07-12",
        "palette": palette,
        "css": {},
    }


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


def test_stock_tracker_can_pin_and_sink_holdings_display_order():
    plugin = StockTracker({"id": "stocktracker"})
    stock_data = [
        _stock("AAPL", [100], 100),
        _stock("SPY", [100], 80),
        _stock("SPCX", [100], 1),
        _stock("NVDA", [100], 90),
    ]

    ordered = plugin._ordered_holdings(stock_data, "SPCX", "SPY")

    assert [row["symbol"] for row in ordered] == ["SPCX", "AAPL", "NVDA", "SPY"]


def test_stock_tracker_declares_and_prepares_fresh_before_display(monkeypatch):
    manifest_path = Path(stocktracker_module.__file__).with_name("plugin-info.json")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["capabilities"]["supports_presentation_refresh"] is True
    assert manifest["refresh_on_display"] is True

    plugin = StockTracker({"id": "stocktracker"})
    image = attach_source_provenance(
        stocktracker_module.Image.new("RGB", (800, 480), "white"),
        SourceProvenance.LIVE,
    )
    monkeypatch.setattr(plugin, "render_themed_image", lambda *_args, **_kwargs: image)
    request = SimpleNamespace(request_id="a" * 32)

    assert plugin.presentation_mode({"refreshOnDisplay": True}) is PresentationMode.PREPARED_BANK
    preparation = plugin.prepare_presentation(
        {"refreshOnDisplay": True},
        FakeDeviceConfig(),
        request=request,
        resolved_theme_context=_canonical_theme("day"),
    )
    assert preparation.changed is True
    assert preparation.request_id == "a" * 32
    assert preparation.image.size == (800, 480)


def test_stock_tracker_keeps_spcx_visible_by_default():
    plugin = StockTracker({"id": "stocktracker"})
    stock_data = [
        _stock("AAPL", [100], 100),
        _stock("NVDA", [100], 90),
        _stock("SPCX", [100], 1),
    ]

    ordered = plugin._ordered_holdings(stock_data)

    assert [row["symbol"] for row in ordered] == ["SPCX", "AAPL", "NVDA"]


def test_stock_tracker_sinks_cash_below_equity_holdings_by_default():
    plugin = StockTracker({"id": "stocktracker"})
    cash = _stock("CASH", [1], 10_000)
    cash["is_cash"] = True
    stock_data = [
        _stock("AAPL", [100], 10),
        cash,
        _stock("NVDA", [100], 9),
    ]

    ordered = plugin._ordered_holdings(stock_data)

    assert [row["symbol"] for row in ordered] == ["AAPL", "NVDA", "CASH"]
    assert [row["symbol"] for row in plugin._ordered_holdings(stock_data, "CASH")] == ["AAPL", "NVDA", "CASH"]


def test_stock_tracker_uses_robinhood_header_brand_for_money_settings():
    plugin = StockTracker({"id": "stocktracker"})

    assert plugin._header_brand_from_settings({"portfolio_csv_path": "/config/money_robinhood_holdings.csv"}) == "robinhood"
    assert plugin._header_brand_from_settings({"header_brand": "off"}) is None

    portfolio_meta = plugin._portfolio_meta_from_settings({"buying_power": "20.25"})
    assert plugin._header_brand_from_settings({}, portfolio_meta) == "robinhood"

    logo = plugin._header_logo_image("robinhood", 214, 34)
    assert logo is not None
    assert logo.mode == "RGBA"
    assert logo.width <= 214
    assert logo.height <= 34
    assert logo.getchannel("A").getbbox() is not None


def test_stock_tracker_loads_img2_holding_logo_assets():
    plugin = StockTracker({"id": "stocktracker"})

    for symbol in ["AAPL", "NVDA", "TSLA", "GOOGL", "NTDOY", "VTI", "VXUS", "VGIT", "SPY", "TTWO", "SPCX", "CASH"]:
        logo = plugin._holding_logo_image(symbol, 20)
        assert logo is not None, symbol
        assert logo.mode == "RGBA"
        assert logo.size == (20, 20)
        bbox = logo.getchannel("A").getbbox()
        assert bbox is not None
        assert abs(((bbox[1] + bbox[3]) / 2) - 10) <= 2.5




def test_stock_tracker_loads_img2_section_wordmark_assets():
    plugin = StockTracker({"id": "stocktracker"})

    for title, (_, size) in SECTION_WORDMARK_IMAGES.items():
        wordmark = plugin._load_section_wordmark(title)
        assert wordmark is not None, title
        assert wordmark.mode == "RGBA"
        assert wordmark.size == size
        alpha = wordmark.getchannel("A")
        assert alpha.getbbox() is not None
        assert alpha.getextrema() == (0, 255)
        assert wordmark.getpixel((0, 0))[3] == 0
        assert wordmark.getpixel((wordmark.width - 1, wordmark.height - 1))[3] == 0


def test_stock_dashboard_uses_img2_section_wordmarks(monkeypatch):
    plugin = StockTracker({"id": "stocktracker"})
    calls = []

    def fake_draw_wordmark(canvas, title, x, y):
        calls.append((title, x, y))
        return True

    monkeypatch.setattr(plugin, "_draw_section_wordmark", fake_draw_wordmark)
    plugin._create_dashboard(
        [_stock("AAPL", [100, 110], 2), _stock("NVDA", [120, 115], 3), _stock("TSLA", [90, 95], 1)],
        (800, 480),
    )

    assert calls == [
        ("PORTFOLIO", 34, 68),
        ("PORTFOLIO TREND", 314, 68),
        ("HOLDINGS", 34, 232),
    ]


def test_stock_tracker_adds_robinhood_cash_to_portfolio_rows_and_total():
    plugin = StockTracker({"id": "stocktracker"})
    stock_data = [_stock("AAPL", [100, 110], 2)]
    portfolio_meta = plugin._portfolio_meta_from_settings({
        "cash_balance": "25.50",
        "buying_power": "20.25",
        "account_value": "250.75",
    })

    stock_data.extend(plugin._portfolio_meta_rows(portfolio_meta, stock_data))

    assert [row["symbol"] for row in stock_data] == ["AAPL", "CASH"]
    assert stock_data[1]["total_value"] == 25.5
    assert stock_data[1]["change_text"] == "BP $20.25"
    totals = plugin._portfolio_totals(stock_data, account_value_override=portfolio_meta["account_value"])

    assert totals[0] == 250.75
    assert totals[1] == 20.0
    assert round(totals[2], 4) == 8.6674
    assert plugin._portfolio_values(stock_data, account_value_override=portfolio_meta["account_value"])[-1] == 250.75


def _near_color_count(image, target, tolerance=8):
    return sum(
        1
        for y in range(image.height)
        for x in range(image.width)
        for pixel in (image.getpixel((x, y)),)
        if max(abs(pixel[index] - target[index]) for index in range(3)) <= tolerance
    )


def _region_near_color_count(image, target, region, tolerance=8):
    left, top, right, bottom = region
    return sum(
        1
        for y in range(top, bottom)
        for x in range(left, right)
        for pixel in (image.getpixel((x, y)),)
        if max(abs(pixel[index] - target[index]) for index in range(3)) <= tolerance
    )

def _hidden_ticker_stock_data():
    symbols = ["SPCX", "AAPL", "NVDA", "TSLA", "VTI", "NTDOY", "VXUS", "VGIT", "TTWO", "GOOGL", "SPY", "CASH"]
    stock_data = []
    for index, symbol in enumerate(symbols):
        start = 100 + index
        end = start + (6 if index % 2 == 0 else -5)
        stock = _stock(symbol, [start, end], 120 - index * 7)
        if symbol == "CASH":
            stock.update({"is_cash": True, "price_text": "USD", "change_text": "BP $42.00", "change_text_color": WHITE})
        stock_data.append(stock)
    return stock_data


def test_stock_tracker_selects_day_and_night_ticker_palettes():
    assert StockTracker._ticker_theme_key("day") == "day"
    assert StockTracker._ticker_theme_key("night") == "night"
    assert StockTracker._ticker_theme_key("auto", _canonical_theme("day")) == "day"
    assert StockTracker._ticker_theme_key("auto", _canonical_theme("night")) == "night"


def test_stock_dashboard_draws_hidden_holdings_as_horizontal_night_ticker():
    plugin = StockTracker({"id": "stocktracker"})
    stock_data = _hidden_ticker_stock_data()

    image = plugin._create_dashboard(
        stock_data,
        (800, 480),
        tracking_window_label="WINDOW: LAST MONTH",
        ticker_theme="night",
    )
    ticker_region = (36, 420, 764, 449)

    assert _region_near_color_count(image, INK, ticker_region, tolerance=5) > 10000
    assert _region_near_color_count(image, ACCENT_GOLD, ticker_region, tolerance=12) > 600
    assert _region_near_color_count(image, WHITE, ticker_region, tolerance=18) > 250
    assert _region_near_color_count(image, MALACHITE, ticker_region, tolerance=35) > 40
    assert _region_near_color_count(image, CINNABAR, ticker_region, tolerance=35) > 30
    assert _region_near_color_count(image, PANEL, ticker_region, tolerance=2) < 500


def test_stock_dashboard_draws_hidden_holdings_as_horizontal_day_ticker():
    plugin = StockTracker({"id": "stocktracker"})
    image = plugin._create_dashboard(
        _hidden_ticker_stock_data(),
        (800, 480),
        tracking_window_label="WINDOW: LAST MONTH",
        ticker_theme="day",
    )
    ticker_region = (36, 420, 764, 449)

    assert _region_near_color_count(image, PANEL_GOLD, ticker_region, tolerance=8) > 10000
    assert _region_near_color_count(image, ACCENT_GOLD, ticker_region, tolerance=12) > 600
    assert _region_near_color_count(image, ACCENT_BLUE, ticker_region, tolerance=35) > 80
    assert _region_near_color_count(image, INK, ticker_region, tolerance=10) > 200
    assert _region_near_color_count(image, WHITE, ticker_region, tolerance=18) < 150


def test_stock_tracker_chart_bounds_and_smoothing_keep_endpoints():
    low, high = StockTracker._chart_value_bounds([100.0, 110.0])
    flat_low, flat_high = StockTracker._chart_value_bounds([100.0, 100.0])
    curve = StockTracker._smooth_curve_points([(0, 10), (10, 0), (20, 10)])

    assert low < 100.0
    assert high > 110.0
    assert flat_low < 100.0 < flat_high
    assert curve[0] == (0, 10)
    assert curve[-1] == (20, 10)
    assert len(curve) > 3

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
    values = plugin._portfolio_values(stock_data)
    vmin, vmax = plugin._chart_value_bounds(values)
    curve_points = plugin._plot_series_points((318, 102, 762, 188), values, vmin, vmax)
    marker_points = plugin._history_marker_points(curve_points, history_points)
    assert [image.getpixel(marker["point"]) for marker in marker_points] == [ACCENT_ORANGE, MALACHITE, CINNABAR]


def test_stock_tracker_history_markers_decorate_portfolio_curve_coordinates():
    plugin = StockTracker({"id": "stocktracker"})
    curve_points = [(318, 102), (540, 185), (762, 188)]
    history_points = [
        {"date": "2026-05-30", "timestamp": "2026-05-30T05:30:00", "value": 4500.0},
        {"date": "2026-05-31", "timestamp": "2026-05-31T05:30:00", "value": 4900.0},
        {"date": "2026-06-01", "timestamp": "2026-06-01T05:30:00", "value": 4700.0},
    ]

    marker_points = plugin._history_marker_points(curve_points, history_points)

    assert [marker["point"] for marker in marker_points] == curve_points
    assert [marker["fill"] for marker in marker_points] == [ACCENT_ORANGE, MALACHITE, CINNABAR]


def test_stock_tracker_labels_last_week_tracking_window():
    assert StockTracker._tracking_window_label("5d") == "WINDOW: LAST WEEK"
    assert StockTracker._tracking_window_label("1mo") == "WINDOW: LAST MONTH"
    assert StockTracker._tracking_window_label("") is None


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


def test_stock_tracker_prefers_current_csv_path_over_stale_legacy_file_setting(tmp_path):
    csv_path = Path(__file__).resolve().parent / "fixtures" / "stock_holdings.csv"
    plugin = StockTracker({"id": "stocktracker"})

    period, holdings = plugin._portfolio_holdings_from_settings({
        "portfolio_csv_path": str(csv_path),
        "portfolio_csv_file": str(tmp_path / "removed-legacy.csv"),
        "period": "1mo",
    })

    assert period == "1mo"
    assert holdings == [("AAPL", 246.30), ("NVDA", 245.29)]


def test_stock_tracker_uses_existing_legacy_csv_when_current_path_is_stale(tmp_path):
    csv_path = Path(__file__).resolve().parent / "fixtures" / "stock_holdings.csv"
    plugin = StockTracker({"id": "stocktracker"})

    period, holdings = plugin._portfolio_holdings_from_settings({
        "portfolio_csv_path": str(tmp_path / "removed-current.csv"),
        "portfolio_csv_file": str(csv_path),
        "period": "1mo",
    })

    assert period == "1mo"
    assert holdings == [("AAPL", 246.30), ("NVDA", 245.29)]


def test_stock_tracker_falls_back_to_inline_holdings_when_saved_csv_paths_are_stale(tmp_path):
    plugin = StockTracker({"id": "stocktracker"})

    period, holdings = plugin._portfolio_holdings_from_settings({
        "portfolio_csv_path": str(tmp_path / "removed-current.csv"),
        "portfolio_csv_file": str(tmp_path / "removed-legacy.csv"),
        "tickers": "AAPL,NVDA",
        "shares": "2,3.5",
        "period": "5d",
    })

    assert period == "5d"
    assert holdings == [("AAPL", 2.0), ("NVDA", 3.5)]


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


def test_stock_tracker_robinhood_mcp_uses_official_positions_and_quotes_without_csv_fallback(
    monkeypatch,
    tmp_path,
):
    plugin = StockTracker({"id": "stocktracker"})
    device = FakeDeviceConfig()
    monkeypatch.setenv("INKYPI_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("INKYPI_DATA_DIR", str(tmp_path / "data"))

    class FakeClient:
        def __init__(self, token_path=None):
            assert token_path == "/secure/robinhood.json"

        def fetch_snapshot(self, account_hash, period="1mo"):
            assert account_hash == "b76f3558212e"
            assert period == "1mo"
            return {
                "positions": [{"symbol": "SPCX", "quantity": 2.5}],
                "quotes": {
                    "SPCX": {
                        "price": 136.12,
                        "previous_close": 130.0,
                        "timestamp": "2026-07-14T21:00:00Z",
                        "extended_hours": True,
                    }
                },
                "histories": {
                    "SPCX": [
                        ("2026-06-16T00:00:00Z", 120.0),
                        ("2026-06-23T00:00:00Z", 125.0),
                        ("2026-06-30T00:00:00Z", 123.0),
                        ("2026-07-07T00:00:00Z", 132.0),
                        ("2026-07-14T00:00:00Z", 135.0),
                    ]
                },
                "portfolio_meta": {
                    "account_value": 365.8,
                    "cash_balance": 25.5,
                    "buying_power": 50.25,
                    "pending_deposits": 0.0,
                    "currency": "USD",
                },
            }

    monkeypatch.setattr(stocktracker_module, "RobinhoodMCPClient", FakeClient)
    monkeypatch.setattr(
        plugin,
        "_portfolio_holdings_from_settings",
        lambda *_args: pytest.fail("Robinhood MCP must not inspect CSV or inline holdings"),
    )
    monkeypatch.setattr(
        plugin,
        "_fetch_stock_data",
        lambda *_args, **_kwargs: pytest.fail("Robinhood MCP must not call fallback market providers"),
    )
    monkeypatch.setattr(plugin, "_record_portfolio_snapshot", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(plugin, "_create_dashboard", lambda stock_data, *_args, **kwargs: (stock_data, kwargs))

    rows, render = plugin.generate_image(
        {
            "data_provider": "robinhood_mcp",
            "robinhood_account_hash": "b76f3558212e",
            "robinhood_token_path": "/secure/robinhood.json",
            "portfolio_csv_path": "/old/portfolio.csv",
            "tickers": "OLD",
            "shares": "99",
        },
        device,
    )

    spcx = next(row for row in rows if row["symbol"] == "SPCX")
    assert spcx["shares"] == 2.5
    assert spcx["price"] == 136.12
    assert spcx["total_value"] == pytest.approx(340.3)
    assert spcx["quote_time"] == "2026-07-14T21:00:00Z"
    assert spcx["data_provider"] == "robinhood_mcp"
    assert len(spcx["history"].index) == 6
    assert [spcx["history"].loc[key, "Close"] for key in spcx["history"].index] == [
        120.0,
        125.0,
        123.0,
        132.0,
        135.0,
        136.12,
    ]
    assert next(row for row in rows if row["symbol"] == "CASH")["shares"] == 25.5
    assert render["account_value_override"] == 365.8
    assert render["header_brand"] == "robinhood"
    assert StockTracker._source_label(rows) == "Robinhood official real-time"


def test_stock_tracker_robinhood_mcp_aligns_all_holdings_on_one_live_portfolio_point(monkeypatch):
    plugin = StockTracker({"id": "stocktracker"})

    class FakeClient:
        def __init__(self, token_path=None):
            pass

        def fetch_snapshot(self, account_hash, period="1mo"):
            return {
                "positions": [
                    {"symbol": "SPCX", "quantity": 2.0},
                    {"symbol": "AAPL", "quantity": 1.0},
                ],
                "quotes": {
                    "SPCX": {
                        "price": 170.0,
                        "previous_close": 155.0,
                        "timestamp": "2026-07-14T21:00:01Z",
                        "extended_hours": True,
                    },
                    "AAPL": {
                        "price": 220.0,
                        "previous_close": 210.0,
                        "timestamp": "2026-07-14T21:00:03Z",
                        "extended_hours": True,
                    },
                },
                "histories": {
                    "SPCX": [
                        ("2026-07-11T00:00:00Z", 150.0),
                        ("2026-07-14T00:00:00Z", 155.0),
                    ],
                    "AAPL": [
                        ("2026-07-11T00:00:00Z", 200.0),
                        ("2026-07-14T00:00:00Z", 210.0),
                    ],
                },
                "portfolio_meta": {
                    "account_value": 585.0,
                    "cash_balance": 25.0,
                    "buying_power": 25.0,
                    "currency": "USD",
                },
            }

    monkeypatch.setattr(stocktracker_module, "RobinhoodMCPClient", FakeClient)

    stock_data, portfolio_meta = plugin._fetch_robinhood_mcp_data({
        "robinhood_account_hash": "b76f3558212e",
        "period": "1mo",
    })

    assert stock_data[0]["quote_time"] != stock_data[1]["quote_time"]
    assert stock_data[0]["history"].index[-1] == stock_data[1]["history"].index[-1]
    assert plugin._portfolio_values(stock_data) == [500.0, 520.0, 560.0]
    assert plugin._portfolio_values(
        stock_data,
        account_value_override=portfolio_meta["account_value"],
    ) == [500.0, 520.0, 585.0]


def test_stock_tracker_portfolio_curve_excludes_dates_missing_from_any_holding():
    plugin = StockTracker({"id": "stocktracker"})
    stock_data = [
        {
            **_stock("SPCX", [150.0, 155.0, 170.0], 2.0),
            "history": stocktracker_module._SimpleHistory([
                ("2026-07-11", 150.0),
                ("2026-07-14", 155.0),
                ("current", 170.0),
            ]),
        },
        {
            **_stock("AAPL", [200.0, 220.0], 1.0),
            "history": stocktracker_module._SimpleHistory([
                ("2026-07-11", 200.0),
                ("current", 220.0),
            ]),
        },
    ]

    assert plugin._portfolio_values(stock_data) == [500.0, 560.0]


def test_stock_tracker_robinhood_mcp_failure_never_falls_back(monkeypatch, tmp_path):
    plugin = StockTracker({"id": "stocktracker"})
    monkeypatch.setenv("INKYPI_CACHE_DIR", str(tmp_path / "cache"))

    class FailingClient:
        def __init__(self, token_path=None):
            pass

        def fetch_snapshot(self, account_hash, period="1mo"):
            raise RuntimeError("official quote unavailable")

    monkeypatch.setattr(stocktracker_module, "RobinhoodMCPClient", FailingClient)
    monkeypatch.setattr(
        plugin,
        "_fetch_stock_data",
        lambda *_args, **_kwargs: pytest.fail("fallback provider must remain disabled"),
    )

    with pytest.raises(RuntimeError, match="Robinhood official MCP refresh failed"):
        plugin.generate_image(
            {
                "data_provider": "robinhood_mcp",
                "robinhood_account_hash": "b76f3558212e",
            },
            FakeDeviceConfig(),
        )


def test_stocktracker_preserves_shared_fallback_weight_rasters(monkeypatch):
    plugin = StockTracker({"id": "stocktracker"})
    sample = "Readable UI"

    for bold in (False, True):
        shared = stocktracker_module.get_base_ui_font(48, bold=bold)
        expected = bytes(shared.getmask(sample))
        monkeypatch.setattr(
            stocktracker_module,
            "get_base_ui_font",
            lambda size, bold=False, shared=shared: shared,
        )

        font = plugin._font(48, bold=bold)

        assert font is shared
        assert bytes(font.getmask(sample)) == expected


def test_stock_auto_ticker_and_main_pixels_use_pinned_weather_theme(monkeypatch, tmp_path):
    plugin = StockTracker({"id": "stocktracker"})
    device = FakeDeviceConfig()
    stock_data = _hidden_ticker_stock_data()
    monkeypatch.setenv("INKYPI_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setattr(
        plugin,
        "resolve_theme",
        lambda *_args, **_kwargs: pytest.fail("pinned theme was re-resolved"),
    )

    class NoonDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            value = cls(2026, 7, 12, 12, 0)
            return value.replace(tzinfo=tz) if tz is not None else value

    monkeypatch.setattr(stocktracker_module, "datetime", NoonDateTime)

    day_theme = _canonical_theme("day")
    night_theme = _canonical_theme("night")
    day = plugin._create_dashboard(
        stock_data,
        device.get_resolution(),
        ticker_theme="auto",
        theme_context=day_theme,
    )
    night = plugin._create_dashboard(
        stock_data,
        device.get_resolution(),
        ticker_theme="auto",
        theme_context=night_theme,
    )

    assert plugin._ticker_theme_key("auto", day_theme) == "day"
    assert plugin._ticker_theme_key("auto", night_theme) == "night"
    assert day.getpixel((0, 100)) == day_theme["palette"]["background"]
    assert night.getpixel((0, 100)) == night_theme["palette"]["background"]
    assert day.getpixel((40, 250)) == day_theme["palette"]["panel"]
    assert night.getpixel((40, 250)) == night_theme["palette"]["panel"]
    ticker_region = (36, 420, 764, 449)
    assert _region_near_color_count(
        night,
        night_theme["palette"]["background"],
        ticker_region,
        tolerance=0,
    ) > 8_000
    assert day.tobytes() != night.tobytes()


def test_stock_theme_only_uses_matching_source_cache_without_provider_or_state_writes(
    monkeypatch,
    tmp_path,
):
    plugin = StockTracker({"id": "stocktracker"})
    device = FakeDeviceConfig()
    monkeypatch.setenv("INKYPI_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("INKYPI_DATA_DIR", str(tmp_path / "data"))
    settings = {
        "tickers": "AAPL,NVDA",
        "shares": "2,3",
        "period": "1mo",
        "data_provider": "yfinance",
        "ticker_theme": "auto",
        "_inkypi_theme": _canonical_theme("day"),
    }
    provider_calls = []

    def fetch_stock(ticker, shares, period, **_kwargs):
        provider_calls.append(ticker)
        return _stock(ticker, [100.0, 110.0], shares)

    monkeypatch.setattr(plugin, "_fetch_stock_data", fetch_stock)
    monkeypatch.setattr(plugin, "_massive_client", lambda *_args: None)
    monkeypatch.setattr(plugin, "_record_portfolio_snapshot", lambda *_args, **_kwargs: [])

    plugin.generate_image(settings, device)
    assert provider_calls == ["AAPL", "NVDA"]
    source_files = list((tmp_path / "cache" / "plugins" / "stocktracker").rglob("*.json"))
    assert len(source_files) == 1
    source_bytes = source_files[0].read_bytes()

    provider_calls.clear()
    monkeypatch.setattr(
        plugin,
        "_record_portfolio_snapshot",
        lambda *_args, **_kwargs: pytest.fail("theme-only advanced portfolio history"),
    )
    monkeypatch.setattr(
        plugin,
        "_write_source_cache",
        lambda *_args, **_kwargs: pytest.fail("theme-only rewrote source cache"),
        raising=False,
    )
    image = plugin.generate_image(
        {
            **settings,
            "_theme_render_only": True,
            "_inkypi_theme": _canonical_theme("night"),
        },
        device,
    )

    assert image.size == (800, 480)
    assert image.getpixel((0, 100)) == _canonical_theme("night")["palette"]["background"]
    assert provider_calls == []
    assert source_files[0].read_bytes() == source_bytes


def test_stock_theme_only_cold_or_incompatible_cache_fails_closed_without_provider(
    monkeypatch,
    tmp_path,
):
    plugin = StockTracker({"id": "stocktracker"})
    device = FakeDeviceConfig()
    monkeypatch.setenv("INKYPI_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("INKYPI_DATA_DIR", str(tmp_path / "data"))
    provider_calls = []
    monkeypatch.setattr(
        plugin,
        "_fetch_stock_data",
        lambda *_args, **_kwargs: provider_calls.append("provider") or _stock("AAPL", [1, 2], 1),
    )

    with pytest.raises(RuntimeError, match="matching StockTracker source cache"):
        plugin.generate_image(
            {
                "tickers": "AAPL",
                "shares": "1",
                "_theme_render_only": True,
                "_inkypi_theme": _canonical_theme("night"),
            },
            device,
        )

    assert provider_calls == []
    assert not (Path(stocktracker_module.__file__).resolve().parent / ".stocktracker_history").exists()


def test_stock_theme_only_rejects_cache_warmed_for_different_currency_without_io(
    monkeypatch,
    tmp_path,
):
    plugin = StockTracker({"id": "stocktracker"})
    device = FakeDeviceConfig()
    monkeypatch.setenv("INKYPI_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("INKYPI_DATA_DIR", str(tmp_path / "data"))
    settings = {
        "tickers": "AAPL",
        "shares": "1",
        "period": "1mo",
        "data_provider": "yfinance",
        "cash_balance": "25",
        "currency": "USD",
        "_inkypi_theme": _canonical_theme("day"),
    }
    provider_calls = []

    def fetch_stock(ticker, shares, period, **_kwargs):
        provider_calls.append(ticker)
        return _stock(ticker, [100.0, 110.0], shares)

    monkeypatch.setattr(plugin, "_fetch_stock_data", fetch_stock)
    monkeypatch.setattr(plugin, "_massive_client", lambda *_args: None)
    monkeypatch.setattr(plugin, "_record_portfolio_snapshot", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(plugin, "_create_dashboard", lambda stock_data, *_args, **_kwargs: stock_data)

    plugin.generate_image(settings, device)
    assert provider_calls == ["AAPL"]

    provider_calls.clear()
    source_write_calls = []
    monkeypatch.setattr(
        plugin,
        "_write_source_cache",
        lambda *_args, **_kwargs: source_write_calls.append("write"),
    )

    with pytest.raises(RuntimeError, match="matching StockTracker source cache"):
        plugin.generate_image(
            {
                **settings,
                "currency": "EUR",
                "_theme_render_only": True,
                "_inkypi_theme": _canonical_theme("night"),
            },
            device,
        )

    assert provider_calls == []
    assert source_write_calls == []


def test_stock_theme_only_reuses_matching_currency_snapshot(monkeypatch, tmp_path):
    plugin = StockTracker({"id": "stocktracker"})
    device = FakeDeviceConfig()
    monkeypatch.setenv("INKYPI_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("INKYPI_DATA_DIR", str(tmp_path / "data"))
    settings = {
        "tickers": "AAPL",
        "shares": "1",
        "period": "1mo",
        "data_provider": "yfinance",
        "cash_balance": "25",
        "currency": "EUR",
        "_inkypi_theme": _canonical_theme("day"),
    }
    provider_calls = []

    def fetch_stock(ticker, shares, period, **_kwargs):
        provider_calls.append(ticker)
        return _stock(ticker, [100.0, 110.0], shares)

    monkeypatch.setattr(plugin, "_fetch_stock_data", fetch_stock)
    monkeypatch.setattr(plugin, "_massive_client", lambda *_args: None)
    monkeypatch.setattr(plugin, "_record_portfolio_snapshot", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(plugin, "_create_dashboard", lambda stock_data, *_args, **_kwargs: stock_data)

    plugin.generate_image(settings, device)
    period, holdings = plugin._portfolio_holdings_from_settings(settings)
    portfolio_meta = plugin._portfolio_meta_from_settings(settings)
    source_key = plugin._source_cache_key(
        period,
        holdings,
        portfolio_meta,
        plugin._data_provider(settings),
    )
    cached = plugin._read_source_cache(source_key)

    provider_calls.clear()
    rendered_rows = plugin.generate_image(
        {
            **settings,
            "_theme_render_only": True,
            "_inkypi_theme": _canonical_theme("night"),
        },
        device,
    )

    assert next(row for row in cached["stock_data"] if row["symbol"] == "CASH")["price_text"] == "EUR"
    assert next(row for row in rendered_rows if row["symbol"] == "CASH")["price_text"] == "EUR"
    assert provider_calls == []
