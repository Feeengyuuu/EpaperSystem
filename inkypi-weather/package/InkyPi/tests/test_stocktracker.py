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
from PIL import Image, ImageDraw  # noqa: E402
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


def _moving(symbol, previous, today_percent, swing_percent, shares):
    closes = [previous]
    for index in range(21):
        closes.insert(0, closes[0] / (1 + (swing_percent if index % 2 else -swing_percent) / 100))
    return _stock(symbol, closes + [previous * (1 + today_percent / 100)], shares)


def test_smart_order_ranks_by_todays_impact_and_unusual_moves():
    plugin = StockTracker({"id": "stocktracker"})
    cash = _stock("CASH", [1.0], 5_000)
    cash["is_cash"] = True
    stock_data = [
        _moving("AAPL", 100, 1.0, 1.0, 800),    # the largest holding, an ordinary day: +$800
        _moving("NVDA", 100, -4.0, 2.0, 300),   # -$1,200 and twice its usual swing
        _moving("BOND", 100, 0.8, 0.1, 500),    # +$400, but eight times its usual swing
        _moving("FUND", 100, 0.6, 1.0, 800),    # +$480, an ordinary day
        _moving("SPY", 100, -3.0, 1.0, 600),    # -$1,800, set as lower priority
        cash,
    ]
    total = sum(row["total_value"] for row in stock_data)
    metrics = [plugin._holding_metrics(row, total) for row in stock_data]

    ordered = plugin._smart_order(metrics, total, "AAPL", "SPY")

    assert [item["data"]["symbol"] for item in ordered] == ["NVDA", "BOND", "AAPL", "FUND", "SPY", "CASH"]


def test_smart_order_keeps_pinned_symbols_on_an_overflowing_table():
    plugin = StockTracker({"id": "stocktracker"})
    stock_data = [_moving(f"S{index:02d}", 100, -(20 - index) / 10, 1.0, 10) for index in range(20)]
    stock_data.append(_moving("SPCX", 100, 0.0, 1.0, 1))

    image = plugin._create_dashboard(stock_data, (800, 480), holdings_pin_symbols="SPCX", ticker_theme="day")
    tables = []
    original = plugin._draw_holding_row

    def record(img, draw, box, item, *args, **kwargs):
        tables.append(item["data"]["symbol"])
        return original(img, draw, box, item, *args, **kwargs)

    plugin._draw_holding_row = record
    plugin._create_dashboard(stock_data, (800, 480), holdings_pin_symbols="SPCX", ticker_theme="day")

    assert image.size == (800, 480)
    # SPCX's kind (mobility) leads; the unlisted symbols follow as "other", still in smart order.
    assert tables[:2] == ["SPCX", "S00"] and len(tables) < len(stock_data)


def test_value_order_is_still_available():
    plugin = StockTracker({"id": "stocktracker"})
    stock_data = [_moving("AAPL", 100, 0.1, 1.0, 800), _moving("NVDA", 100, -5.0, 1.0, 300)]
    shown = []
    original = plugin._draw_holding_row

    def record(img, draw, box, item, *args, **kwargs):
        shown.append(item["data"]["symbol"])
        return original(img, draw, box, item, *args, **kwargs)

    plugin._draw_holding_row = record
    plugin._create_dashboard(stock_data, (800, 480), ticker_theme="day", holdings_order="value")
    assert shown == ["AAPL", "NVDA"]
    shown.clear()
    plugin._create_dashboard(stock_data, (800, 480), ticker_theme="day")
    assert shown == ["NVDA", "AAPL"]


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

    for symbol in ["AAPL", "NVDA", "TSLA", "GOOGL", "NTDOY", "VTI", "VXUS", "VGIT", "SPY", "TTWO", "SPCX", "FIG", "CASH"]:
        logo = plugin._holding_logo_image(symbol, 20)
        assert logo is not None, symbol
        assert logo.mode == "RGBA"
        assert logo.size == (24, 20)
        bbox = logo.getchannel("A").getbbox()
        assert bbox is not None
        assert abs(((bbox[1] + bbox[3]) / 2) - 10) <= 2.5
        assert abs(((bbox[0] + bbox[2]) / 2) - 12) <= 2.5


def test_holding_logos_are_bare_marks_of_one_visual_size():
    from plugins.stocktracker import stocktracker as module
    from PIL import Image

    logos_dir = Path(module.__file__).resolve().parent / "assets" / "logos"
    credits = json.loads((logos_dir / "credits.json").read_text(encoding="utf-8"))["marks"]
    plugin = StockTracker({"id": "stocktracker"})
    areas = {}
    for symbol, asset in module.LOGO_ASSET_BY_SYMBOL.items():
        assert asset in credits, asset
        with Image.open(logos_dir / asset) as source:
            mark = source.convert("RGBA")
        # No tile behind the mark: its corners are clear.
        assert mark.getpixel((0, 0))[3] == 0 and mark.getpixel((mark.width - 1, mark.height - 1))[3] == 0, asset
        bbox = plugin._holding_logo_image(symbol, 16, width=22).getchannel("A").getbbox()
        areas[symbol] = (bbox[2] - bbox[0]) * (bbox[3] - bbox[1])
    # A wide mark runs wider and a tall one taller, but none dwarfs another.
    assert max(areas.values()) <= 2.2 * min(areas.values())


def test_dark_holding_marks_turn_to_ink_at_night():
    from plugins.stocktracker import stocktracker as module

    plugin = StockTracker({"id": "stocktracker"})
    night = {**module._active_stock_colors(), "mode": "night", "ink": (240, 240, 240)}
    token = module._ACTIVE_STOCK_COLORS.set(night)
    try:
        apple = plugin._holding_logo_image("AAPL", 20)
        tesla = plugin._holding_logo_image("TSLA", 20)
    finally:
        module._ACTIVE_STOCK_COLORS.reset(token)
    def pixels(image):
        return [image.getpixel((x, y)) for x in range(image.width) for y in range(image.height)]

    opaque = [pixel for pixel in pixels(apple) if pixel[3] > 200]
    assert opaque and all(pixel[:3] == (240, 240, 240) for pixel in opaque)
    # A brand colour keeps its own.
    assert any(pixel[3] > 200 and pixel[0] > 180 and pixel[1] < 80 for pixel in pixels(tesla))


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
        ("PORTFOLIO", 26, 56),
        ("PORTFOLIO TREND", 338, 56),
        ("HOLDINGS", 26, 206),
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

# The table shows every holding that fits; only the overflow rolls into the bottom ticker.
TICKER_REGION = (36, 434, 764, 463)


def _hidden_ticker_stock_data():
    symbols = [
        "SPCX", "AAPL", "NVDA", "TSLA", "VTI", "NTDOY", "VXUS", "VGIT", "TTWO",
        "GOOGL", "SPY", "MSFT", "AMZN", "GOOG", "FIG", "QQQ", "VOO", "CASH",
    ]
    stock_data = []
    for index, symbol in enumerate(symbols):
        start = 100 + index
        end = start + (6 if index % 2 == 0 else -5)
        stock = _stock(symbol, [start, end], 120 - index * 7)
        if symbol == "CASH":
            stock.update({"is_cash": True, "price_text": "USD", "change_text": "BP $42.00", "change_text_color": WHITE})
        stock_data.append(stock)
    return stock_data


def test_holdings_group_by_kind_largest_first_with_their_share_and_day():
    def item(symbol, weight, value, day_pl, is_cash=False):
        return {"data": {"symbol": symbol}, "weight": weight, "value": value, "day_pl": day_pl, "is_cash": is_cash}

    metrics = [
        item("VTI", 20.0, 2000.0, 20.0),
        item("NVDA", 30.0, 3000.0, -30.0),
        item("AAPL", 25.0, 2500.0, -20.0),
        item("XYZ", 3.0, 300.0, None),
        item("CASH", 2.0, 200.0, None, is_cash=True),
        item("VGIT", 20.0, 2000.0, 0.0),
    ]
    shown = metrics[:5]

    groups = StockTracker._holding_groups(shown, metrics)

    assert [kind for kind, *_rest in groups] == ["tech", "index", "other", "cash"]
    kind, rows, weight, day = groups[0]
    assert [row["data"]["symbol"] for row in rows] == ["NVDA", "AAPL"]
    assert weight == 55.0 and round(day, 3) == round(-50 / 5550 * 100, 3)
    # A kind without a day figure has none; VGIT counts in no shown group.
    assert groups[2][3] is None and all(kind != "bonds" for kind, *_rest in groups)


def test_holdings_rule_shows_the_portfolio_mix_in_kind_colours():
    plugin = StockTracker({"id": "stocktracker"})
    image = plugin._create_dashboard(_hidden_ticker_stock_data()[:11] + [_hidden_ticker_stock_data()[-1]], (800, 480),
                                     ticker_theme="day")
    rule = {image.getpixel((x, 246)) for x in range(30, 770)}

    for colour in (stocktracker_module.ACCENT_BLUE, stocktracker_module.MALACHITE, stocktracker_module.CINNABAR):
        assert colour in rule


def test_stock_tracker_selects_day_and_night_ticker_palettes():
    assert StockTracker._ticker_theme_key("day") == "day"
    assert StockTracker._ticker_theme_key("night") == "night"
    assert StockTracker._ticker_theme_key("auto", _canonical_theme("day")) == "day"
    assert StockTracker._ticker_theme_key("auto", _canonical_theme("night")) == "night"


def test_stock_day_theme_restores_original_dashboard_colors():
    from plugins.stocktracker.stocktracker import (
        _LEGACY_STOCK_COLORS,
        _stock_render_colors,
    )

    assert _stock_render_colors(_canonical_theme("day")) is _LEGACY_STOCK_COLORS
    assert _stock_render_colors(_canonical_theme("night")) is not _LEGACY_STOCK_COLORS


def test_stock_night_manifest_keeps_data_distinct_and_frame_quieter_than_text():
    from utils.theme_utils import contrast_ratio, resolve_palette_roles

    manifest = json.loads(Path(stocktracker_module.__file__).with_name("plugin-info.json").read_text())
    colors = stocktracker_module._stock_render_colors({
        "mode": "night", "palette": resolve_palette_roles(manifest["theme"], "night"),
    })
    # Small figures and semantic marks stay legible; the broad decorative frame is quieter.
    for key in ("ink", "muted", "accent_blue", "malachite", "cinnabar", "accent_orange", "accent_gold"):
        assert contrast_ratio(colors[key], colors["panel"]) >= 4.5, key
    assert contrast_ratio(colors["frame_light"], colors["panel"]) < contrast_ratio(colors["muted"], colors["panel"])
    assert max(abs(a - b) for a, b in zip(colors["accent_blue"], colors["malachite"])) >= 80
    assert max(abs(a - b) for a, b in zip(colors["accent_orange"], colors["accent_gold"])) >= 25


@pytest.mark.parametrize("ticker_mode", ["day", "night"])
def test_night_page_ticker_uses_its_own_theme_instead_of_cached_day_inks(ticker_mode, monkeypatch):
    plugin = StockTracker({"id": "stocktracker"})
    theme = _canonical_theme("night")
    colors = stocktracker_module._stock_render_colors(theme)
    expected = stocktracker_module._LEGACY_STOCK_COLORS if ticker_mode == "day" else colors
    rows = [_stock("AAPL", [100, 105], 1), _stock("DOWN", [100, 95], 1)]
    logo_modes = []
    original_logo = plugin._holding_logo_image

    def logo(symbol, size):
        logo_modes.append(stocktracker_module._active_stock_colors()["mode"])
        return original_logo(symbol, size)

    monkeypatch.setattr(plugin, "_holding_logo_image", logo)
    for row in rows:
        row["change_text_color"] = (3, 4, 5)  # Source/cache ink has no authority over the new theme.
    token = stocktracker_module._ACTIVE_STOCK_COLORS.set(colors)
    text_colors = {}
    try:
        image = Image.new("RGB", (800, 60), colors["paper"])
        draw = ImageDraw.Draw(image)
        original_text = draw.text

        def text(xy, value, **kwargs):
            text_colors[value] = kwargs.get("fill")
            return original_text(xy, value, **kwargs)

        monkeypatch.setattr(draw, "text", text)
        plugin._draw_hidden_holdings_ticker(image, draw, (10, 10, 790, 50), rows,
                                           ticker_theme=ticker_mode, theme_context=theme)
    finally:
        stocktracker_module._ACTIVE_STOCK_COLORS.reset(token)
    pixels = set(image.get_flattened_data())
    assert (3, 4, 5) not in pixels
    assert expected["ink"] in pixels
    assert text_colors["+5.00%"] == expected["malachite"]
    assert text_colors["-5.00%"] == expected["cinnabar"]
    assert logo_modes == [ticker_mode, ticker_mode]
    if ticker_mode == "day":
        assert stocktracker_module.PANEL_GOLD in pixels


def test_stock_dashboard_draws_hidden_holdings_as_horizontal_night_ticker():
    plugin = StockTracker({"id": "stocktracker"})
    stock_data = _hidden_ticker_stock_data()

    image = plugin._create_dashboard(
        stock_data,
        (800, 480),
        tracking_window_label="WINDOW: LAST MONTH",
        ticker_theme="night",
    )
    ticker_region = TICKER_REGION

    assert _region_near_color_count(image, INK, ticker_region, tolerance=5) > 10000
    assert _region_near_color_count(image, ACCENT_GOLD, ticker_region, tolerance=12) > 600
    assert _region_near_color_count(image, WHITE, ticker_region, tolerance=18) > 200
    assert _region_near_color_count(image, MALACHITE, ticker_region, tolerance=35) > 40
    assert _region_near_color_count(image, CINNABAR, ticker_region, tolerance=35) > 30
    assert _region_near_color_count(image, PANEL, ticker_region, tolerance=2) < 500


def test_stock_dashboard_draws_hidden_holdings_as_horizontal_day_ticker():
    plugin = StockTracker({"id": "stocktracker"})
    # By value, so the fixture's same holdings overflow into the ticker.
    image = plugin._create_dashboard(
        _hidden_ticker_stock_data(),
        (800, 480),
        tracking_window_label="WINDOW: LAST MONTH",
        ticker_theme="day",
        holdings_order="value",
    )
    ticker_region = TICKER_REGION

    assert _region_near_color_count(image, PANEL_GOLD, ticker_region, tolerance=8) > 10000
    assert _region_near_color_count(image, ACCENT_GOLD, ticker_region, tolerance=12) > 600
    assert _region_near_color_count(image, ACCENT_BLUE, ticker_region, tolerance=35) > 80
    assert _region_near_color_count(image, INK, ticker_region, tolerance=10) > 200
    assert _region_near_color_count(image, WHITE, ticker_region, tolerance=18) < 150


def test_stock_dashboard_lists_every_holding_that_fits_without_a_ticker():
    plugin = StockTracker({"id": "stocktracker"})
    stock_data = _hidden_ticker_stock_data()[:11] + [_hidden_ticker_stock_data()[-1]]
    rows_drawn = []
    original = plugin._draw_holding_row

    def record_row(img, draw, row_box, item, columns, max_weight):
        rows_drawn.append(item["data"]["symbol"])
        return original(img, draw, row_box, item, columns, max_weight)

    plugin._draw_holding_row = record_row
    image = plugin._create_dashboard(stock_data, (800, 480), ticker_theme="day")

    assert len(rows_drawn) == 12
    assert rows_drawn[-1] == "CASH"
    # Rows of one kind of investment sit together.
    kinds = [stocktracker_module.HOLDING_CATEGORY_BY_SYMBOL.get(symbol, "cash") for symbol in rows_drawn]
    assert [kind for index, kind in enumerate(kinds) if index == 0 or kind != kinds[index - 1]] == list(dict.fromkeys(kinds))
    assert _region_near_color_count(image, PANEL_GOLD, TICKER_REGION, tolerance=8) < 500


def test_holding_metrics_use_official_day_change_and_history_window():
    plugin = StockTracker({"id": "stocktracker"})
    live = _stock("SPCX", [100.0, 104.0, 110.0], 2)
    live.update({"data_provider": "robinhood_mcp", "change": 2.0, "change_percent": 1.85})

    metrics = plugin._holding_metrics(live, 1100.0)

    assert metrics["day_percent"] == pytest.approx(1.85)
    assert metrics["day_pl"] == pytest.approx(4.0)
    assert metrics["window_percent"] == pytest.approx(10.0)
    assert metrics["weight"] == pytest.approx(20.0)
    assert metrics["closes"] == [100.0, 104.0, 110.0]


def test_holding_metrics_derive_day_change_for_window_based_providers():
    plugin = StockTracker({"id": "stocktracker"})
    yahoo = _stock("AAPL", [100.0, 104.0, 110.0], 2)
    yahoo["data_provider"] = "yfinance"
    cash = _stock("CASH", [1.0], 50.0)
    cash["is_cash"] = True

    metrics = plugin._holding_metrics(yahoo, 270.0)
    cash_metrics = plugin._holding_metrics(cash, 270.0)

    # Yahoo's change spans the window; the day move is against the previous close.
    assert metrics["day_percent"] == pytest.approx(6 / 104 * 100)
    assert metrics["day_pl"] == pytest.approx(12.0)
    assert metrics["window_percent"] == pytest.approx(10.0)
    assert cash_metrics["day_percent"] is None
    assert cash_metrics["window_percent"] is None
    assert cash_metrics["weight"] == pytest.approx(50 / 270 * 100)


def test_the_portfolio_total_sits_over_its_chart_in_todays_colour():
    plugin = StockTracker({"id": "stocktracker"})
    values = [250000, 248000, 252000, 258000, 255000, 262000, 266124.56]

    def card(day_percent):
        image = Image.new("RGB", (340, 200), stocktracker_module.PAPER)
        plugin._draw_summary(image, ImageDraw.Draw(image), (16, 20, 316, 158), 266124.56, day_percent * 2600,
                             day_percent, stats=[("INVESTED", "$260,836.97"), ("CASH", "$5,285.57")],
                             curve_values=values)
        return image, set(image.get_flattened_data())

    up, up_colours = card(0.84)
    down, down_colours = card(-0.84)
    _flat, flat_colours = card(0.0)

    # The figure and today's change take today's colour: deep green up, deep red down, ink when flat.
    assert stocktracker_module.GAIN_INK in up_colours and stocktracker_module.LOSS_INK not in up_colours
    assert stocktracker_module.LOSS_INK in down_colours and stocktracker_module.GAIN_INK not in down_colours
    assert not {stocktracker_module.GAIN_INK, stocktracker_module.LOSS_INK} & flat_colours

    # The trend behind is softened (no hard line colour) and tinted with the day: greenish up, reddish down.
    def tints(image):
        chart = image.crop((30, 54, 300, 90)).get_flattened_data()
        return (sum(1 for r, g, b in chart if g > r + 30), sum(1 for r, g, b in chart if r > g + 60 and r > b + 60))

    assert stocktracker_module.MALACHITE not in set(up.crop((30, 54, 300, 90)).get_flattened_data())
    assert tints(up)[0] > 200 and tints(down)[1] > 200 and tints(up)[1] < tints(down)[1]
    # The slim inner gold rule and chamfered corners preserve the card footprint.
    assert up.getpixel((166, 23)) == stocktracker_module.ACCENT_GOLD
    assert up.getpixel((16, 20)) == stocktracker_module.PAPER


@pytest.mark.parametrize("value", [22361.83, 266124.56, 9999999.99, -123456.78])
def test_engraved_bodoni_total_fits_the_portfolio_chart(value):
    plugin = StockTracker({"id": "stocktracker"})
    draw = ImageDraw.Draw(Image.new("RGB", (340, 200)))
    plot = (23, 53, 310, 112)
    figure = plugin._total_figure_layout(draw, plot, value)
    assert figure["text"] == f"{value:,.2f}"
    assert figure["font"].getname()[0] == "Bodoni Moda"
    boxes = [draw.textbbox((figure["origin"][0] + offset, figure["origin"][1]), character,
                          font=figure["font"], stroke_width=2) for character, offset in figure["glyphs"]]
    bounds = (min(box[0] for box in boxes), min(box[1] for box in boxes),
              max(box[2] for box in boxes), max(box[3] for box in boxes))
    assert bounds[0] >= figure["coin"][0] + stocktracker_module.SUMMARY_COIN
    assert bounds[2] <= plot[2] and bounds[1] >= plot[1] and bounds[3] < plot[3]
    first, second = figure["glyphs"][:2]
    assert second[1] - figure["font"].getlength(first[0]) == pytest.approx(3 * figure["font"].size / 38)
    assert figure["origin"][0] - figure["coin"][0] - stocktracker_module.SUMMARY_COIN == pytest.approx(9, abs=0.5)


def test_banknote_engraving_stays_inside_digits_and_keeps_punctuation_solid():
    plugin = StockTracker({"id": "stocktracker"})
    ink, ground = (80, 180, 120), (5, 15, 25)
    image = Image.new("RGB", (340, 200), ground)
    draw = ImageDraw.Draw(image)
    figure = plugin._total_figure_layout(draw, (23, 53, 310, 112), 888888.88)
    plugin._draw_total_figure(image, draw, figure, ink, ground)
    glyphs = Image.new("L", image.size)
    glyph_draw = ImageDraw.Draw(glyphs)
    solid = Image.new("RGB", image.size, ground)
    solid_draw = ImageDraw.Draw(solid)
    for character, offset in figure["glyphs"]:
        position = (figure["origin"][0] + offset, figure["origin"][1])
        if character.isdecimal():
            glyph_draw.text(position, character, font=figure["font"], fill=255)
        solid_draw.text(position, character, font=figure["font"], fill=ink, stroke_width=2, stroke_fill=ground)
        solid_draw.text(position, character, font=figure["font"], fill=ink, stroke_width=1,
                        stroke_fill=plugin._blend(ink, ground, 0.72))
    cut_colour = plugin._blend(ink, ground, 0.55)
    cuts = [(x, y) for y in range(53, 112) for x in range(70, 310)
            if image.getpixel((x, y)) == cut_colour and solid.getpixel((x, y)) != cut_colour]
    assert len(cuts) > 20
    assert all(glyphs.getpixel(point) > 200 for point in cuts)
    for character, offset in figure["glyphs"]:
        if character in ",.":
            left = figure["origin"][0] + offset
            right = left + figure["font"].getlength(character)
            assert not any(left <= x < right for x, _y in cuts)


def test_summary_stats_show_account_balances_then_best_mover():
    plugin = StockTracker({"id": "stocktracker"})
    rows = [_stock("SPCX", [100.0, 110.0], 2), _stock("AAPL", [100.0, 95.0], 1)]
    for row, percent in zip(rows, (2.5, -1.25)):
        row.update({"data_provider": "robinhood_mcp", "change_percent": percent})
    metrics = [plugin._holding_metrics(row, 400.0) for row in rows]

    stats = plugin._summary_stats(
        metrics,
        {"cash_balance": 85.0, "buying_power": 80.5, "pending_deposits": 0.0},
    )

    assert stats == [
        ("INVESTED", "$315.00"),
        ("CASH", "$85.00"),
        ("BUYING POWER", "$80.50"),
        ("BEST TODAY", "SPCX +2.50%"),
    ]
    assert plugin._summary_stats(metrics, {})[1:] == [
        ("BEST TODAY", "SPCX +2.50%"),
        ("WORST TODAY", "AAPL -1.25%"),
        ("POSITIONS", "2"),
    ]


TREND_BOX = (304, 60, 776, 204)
DIP_THEN_RISE = [100.0, 96.0, 92.0, 95.0, 101.0, 106.0, 104.0, 109.0, 112.0]


def _trend_panel(values, theme_context=None):
    plugin = StockTracker({"id": "stocktracker"})
    colors = stocktracker_module._stock_render_colors(theme_context)
    token = stocktracker_module._ACTIVE_STOCK_COLORS.set(colors)
    try:
        image = Image.new("RGB", (800, 480), colors["paper"])
        plugin._draw_sparkline(image, ImageDraw.Draw(image), TREND_BOX, values)
    finally:
        stocktracker_module._ACTIVE_STOCK_COLORS.reset(token)
    return image, colors


def test_trend_panel_splits_gain_and_loss_around_the_start_baseline():
    image, colors = _trend_panel(DIP_THEN_RISE)
    # Plot inner box (328, 106, 750, 186); the start value 100 sits at y=152.
    dip_below = (332, 155, 520, 188)
    rise_above = (560, 104, 748, 150)

    assert _region_near_color_count(image, colors["cinnabar"], dip_below, tolerance=12) > 80
    assert _region_near_color_count(image, colors["malachite"], dip_below, tolerance=12) == 0
    assert _region_near_color_count(image, colors["malachite"], rise_above, tolerance=12) > 300
    assert _region_near_color_count(image, colors["muted"], (330, 152, 740, 153), tolerance=6) > 120
    assert image.getpixel((750, 115)) == colors["malachite"]


def test_trend_panel_dip_leaves_no_gain_tint_above_the_baseline():
    # Screenshot-shaped series: the start baseline lands on a fractional row (157.65).
    values = [262350, 260900, 258851, 260400, 263900, 266200, 265100, 264300, 265800, 267400, 270006.65]
    image, _colors = _trend_panel(values)
    chart_bg = image.getpixel((345, 120))

    for y in range(150, 158):
        assert [image.getpixel((x, y)) for x in range(345, 385)] == [chart_bg] * 40, y


def test_trend_panel_ending_below_start_is_mostly_loss_colored():
    image, colors = _trend_panel([100.0, 103.0, 99.0, 95.0, 92.0, 90.0, 88.0])
    plot = (320, 100, 760, 192)

    assert _region_near_color_count(image, colors["cinnabar"], plot, tolerance=12) > _region_near_color_count(
        image, colors["malachite"], plot, tolerance=12
    )
    assert image.getpixel((750, 177)) == colors["cinnabar"]


def test_trend_panel_handles_two_points_flat_series_and_night_theme():
    _trend_panel([5000.0, 5100.0])
    flat, flat_colors = _trend_panel([5000.0, 5000.0, 5000.0])
    night, night_colors = _trend_panel(DIP_THEN_RISE, _canonical_theme("night"))

    assert flat.getpixel((750, 146)) == flat_colors["malachite"]
    assert night.getpixel((750, 115)) == night_colors["malachite"]
    assert night_colors["malachite"] != stocktracker_module.MALACHITE


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
    assert holdings == [("AAPL", 12.50), ("NVDA", 30.25)]


def test_stock_tracker_prefers_current_csv_path_over_stale_legacy_file_setting(tmp_path):
    csv_path = Path(__file__).resolve().parent / "fixtures" / "stock_holdings.csv"
    plugin = StockTracker({"id": "stocktracker"})

    period, holdings = plugin._portfolio_holdings_from_settings({
        "portfolio_csv_path": str(csv_path),
        "portfolio_csv_file": str(tmp_path / "removed-legacy.csv"),
        "period": "1mo",
    })

    assert period == "1mo"
    assert holdings == [("AAPL", 12.50), ("NVDA", 30.25)]


def test_stock_tracker_uses_existing_legacy_csv_when_current_path_is_stale(tmp_path):
    csv_path = Path(__file__).resolve().parent / "fixtures" / "stock_holdings.csv"
    plugin = StockTracker({"id": "stocktracker"})

    period, holdings = plugin._portfolio_holdings_from_settings({
        "portfolio_csv_path": str(tmp_path / "removed-current.csv"),
        "portfolio_csv_file": str(csv_path),
        "period": "1mo",
    })

    assert period == "1mo"
    assert holdings == [("AAPL", 12.50), ("NVDA", 30.25)]


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


def test_stock_tracker_curve_uses_official_history_before_older_local_snapshots():
    plugin = StockTracker({"id": "stocktracker"})
    stock_data = [
        {
            **_stock("SPCX", [150.0, 170.0], 2.0),
            "history": stocktracker_module._SimpleHistory([
                ("2026-06-03T00:00:00Z", 150.0),
                ("2026-06-04T00:00:00Z", 170.0),
            ]),
        },
        {
            **_stock("AAPL", [200.0, 220.0], 1.0),
            "history": stocktracker_module._SimpleHistory([
                ("2026-06-03T00:00:00Z", 200.0),
                ("2026-06-04T00:00:00Z", 220.0),
            ]),
        },
    ]
    local_history = [
        {"date": "2026-04-01", "timestamp": "2026-04-01T18:00:00", "value": 1.0},
        {"date": "2026-06-01", "timestamp": "2026-06-01T18:00:00", "value": 450.0},
        {"date": "2026-06-02", "timestamp": "2026-06-02T18:00:00", "value": 480.0},
        {"date": "2026-06-03", "timestamp": "2026-06-03T18:00:00", "value": 505.0},
    ]

    values = plugin._portfolio_curve_values(
        stock_data,
        local_history,
        period="1mo",
        now=datetime(2026, 6, 4, 20, 0),
    )

    assert values == [450.0, 480.0, 500.0, 560.0]


def test_stock_tracker_curve_does_not_splice_snapshots_on_a_different_basis():
    # 2026-10-07 device data: Robinhood back-projects today's holdings and
    # cash, while saved snapshots recorded the account as it was. Across the
    # Labor Day weekend the stale snapshots drew a flat start and a $10k jump.
    plugin = StockTracker({"id": "stocktracker"})
    stock_data = [
        {
            **_stock("PORTFOLIO", [261569.0, 259478.27], 1.0),
            "history": stocktracker_module._SimpleHistory([
                ("2026-09-08T00:00:00Z", 261569.0),
                ("2026-09-09T00:00:00Z", 259478.27),
            ]),
        },
    ]
    local_history = [
        {"date": day, "timestamp": f"{day}T13:10:00", "value": 251807.82}
        for day in ("2026-09-05", "2026-09-06", "2026-09-07")
    ] + [{"date": "2026-09-08", "timestamp": "2026-09-08T13:22:00", "value": 251280.93}]

    values = plugin._portfolio_curve_values(
        stock_data,
        local_history,
        period="1mo",
        now=datetime(2026, 10, 7, 13, 19),
    )

    assert values == [261569.0, 259478.27]


def test_stock_tracker_snapshot_reads_imported_history_without_copying_it_into_current_file(tmp_path, monkeypatch):
    plugin = StockTracker({"id": "stocktracker"})
    history_dir = tmp_path / "history"
    history_dir.mkdir()
    current_path = history_dir / "current-holdings.json"
    imported_path = history_dir / "imported-history.json"
    imported_path.write_text(
        json.dumps([{"date": "2026-06-01", "timestamp": "2026-06-01T18:00:00", "value": 100.0}]),
        encoding="utf-8",
    )
    current_path.write_text(
        json.dumps([{"date": "2026-06-02", "timestamp": "2026-06-02T18:00:00", "value": 200.0}]),
        encoding="utf-8",
    )
    monkeypatch.setattr(plugin, "_portfolio_history_path", lambda _stock_data: str(current_path))

    combined = plugin._record_portfolio_snapshot(
        [_stock("SPCX", [150.0, 170.0], 2.0)],
        now=datetime(2026, 6, 3, 18, 0),
    )

    assert [point["date"] for point in combined] == ["2026-06-01", "2026-06-02", "2026-06-03"]
    persisted_current = json.loads(current_path.read_text(encoding="utf-8"))
    assert [point["date"] for point in persisted_current] == ["2026-06-02", "2026-06-03"]


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


def test_stocktracker_restores_original_jost_font_files():
    plugin = StockTracker({"id": "stocktracker"})

    regular = plugin._font(48, bold=False)
    semibold = plugin._font(48, bold=True)

    assert Path(regular.path).name == "Jost.ttf"
    assert Path(semibold.path).name == "Jost-SemiBold.ttf"


def test_stock_auto_ticker_uses_pinned_mode_with_original_day_colors(monkeypatch, tmp_path):
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
    assert day.getpixel((0, 100)) == stocktracker_module.PAPER
    assert night.getpixel((0, 100)) == night_theme["palette"]["background"]
    assert day.getpixel((22, 300)) == stocktracker_module.PANEL
    assert night.getpixel((22, 300)) == night_theme["palette"]["panel"]
    ticker_region = TICKER_REGION
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


def test_stock_tracker_publishes_only_holding_symbols_for_money_radar(monkeypatch):
    plugin = StockTracker({"id": "stocktracker"})
    published = {}
    monkeypatch.setattr(
        stocktracker_module,
        "write_context",
        lambda plugin_id, payload, **kwargs: published.update(plugin_id=plugin_id, payload=payload, **kwargs),
    )
    cash = _stock("CASH", [1.0], 500)
    cash["is_cash"] = True

    plugin._publish_holdings_context([_stock("AAPL", [100.0], 2), _stock("NVDA", [300.0], 2), cash])

    assert published["plugin_id"] == "stocktracker"
    assert published["payload"]["symbols"] == ["NVDA", "AAPL"]
    assert "shares" not in str(published["payload"]) and "value" not in published["payload"]


def test_weight_bars_take_their_kinds_colour_and_mark_the_windows_start():
    plugin = StockTracker({"id": "stocktracker"})
    # NVDA doubled over the window, VTI held still, cash stayed cash: NVDA's share grew, the others shrank.
    metrics = [
        {"data": {"symbol": "NVDA"}, "is_cash": False, "value": 200.0, "weight": 50.0, "window_percent": 100.0},
        {"data": {"symbol": "VTI"}, "is_cash": False, "value": 100.0, "weight": 25.0, "window_percent": 0.0},
        {"data": {"symbol": "CASH"}, "is_cash": True, "value": 100.0, "weight": 25.0, "window_percent": None},
    ]
    plugin._set_window_weights(metrics)
    assert [round(item["weight_then"], 1) for item in metrics] == [33.3, 33.3, 33.3]

    image = Image.new("RGB", (200, 40), stocktracker_module.PAPER)
    draw = ImageDraw.Draw(image)
    plugin._draw_weight_bar(draw, (20, 120), 10, metrics[0], 50.0)
    plugin._draw_weight_bar(draw, (20, 120), 30, metrics[1], 50.0)

    # NVDA (tech) fills blue to its end with the ink tick a third of the way in.
    assert image.getpixel((40, 10)) == stocktracker_module.ACCENT_BLUE
    assert image.getpixel((87, 7)) == stocktracker_module.INK
    # VTI (index) fills green to its half; the share it lost is outlined out to its tick.
    assert image.getpixel((50, 30)) == stocktracker_module.MALACHITE
    assert image.getpixel((80, 28)) == stocktracker_module.MALACHITE and image.getpixel((80, 30)) != stocktracker_module.MALACHITE
    assert image.getpixel((87, 27)) == stocktracker_module.INK


def test_a_rows_trend_ends_on_a_star_at_the_windows_high_and_a_ring_at_its_low():
    plugin = StockTracker({"id": "stocktracker"})

    def trend(closes, colour):
        image = Image.new("RGB", (60, 30), stocktracker_module.PAPER)
        plugin._draw_row_sparkline(ImageDraw.Draw(image), (5, 5, 50, 25), closes, colour)
        return image

    high = trend([10, 11, 10.5, 12], stocktracker_module.MALACHITE)
    low = trend([12, 11, 11.5, 10], stocktracker_module.CINNABAR)
    middle = trend([10, 12, 11], stocktracker_module.CINNABAR)

    assert stocktracker_module.COIN_GOLD in set(high.get_flattened_data())
    # The low ends on a ring: its rim in the line's colour around paper.
    assert low.getpixel((50, 25)) == stocktracker_module.PAPER and low.getpixel((46, 25)) == stocktracker_module.CINNABAR
    assert stocktracker_module.COIN_GOLD not in set(middle.get_flattened_data())
