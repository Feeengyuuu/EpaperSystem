import sys
from datetime import datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from plugins.money_radar import money_radar as money_module  # noqa: E402
from plugins.money_radar import sources  # noqa: E402
from plugins.money_radar.money_radar import MoneyRadar  # noqa: E402
from plugins.money_radar import render as money_render  # noqa: E402
from plugins.money_radar.render import chip_style, price_text, render_page  # noqa: E402
from utils.page_canvas import PageCanvas  # noqa: E402
from utils.http_client import HttpStatusError  # noqa: E402

NOW = datetime(2026, 10, 21, 13, 0, tzinfo=sources.PACIFIC)


class Device:
    def __init__(self, keys=None):
        self.keys = keys or {}

    def get_resolution(self):
        return (800, 480)

    def get_config(self, key=None, default=None):
        values = {"orientation": "horizontal", "timezone": "America/Los_Angeles"}
        return values if key is None else values.get(key, default)

    def load_env_key(self, key):
        return self.keys.get(key)


def test_fx_crypto_and_chart_payloads_normalize():
    fx = sources.normalize_fx({"rates": {
        "2026-09-22": {"CNY": 7.1, "EUR": 0.9, "JPY": 150.0, "HKD": 7.8},
        "2026-10-21": {"CNY": 6.6761, "EUR": 0.86, "JPY": 157.9, "HKD": 7.85},
    }})
    crypto = sources.normalize_crypto([{
        "id": "bitcoin", "symbol": "btc", "name": "Bitcoin", "current_price": 84476,
        "price_change_percentage_24h_in_currency": 2.38, "price_change_percentage_7d_in_currency": 3.5,
        "sparkline_in_7d": {"price": list(range(168))},
    }], ("bitcoin", "ethereum"))
    chart = sources.normalize_chart({"chart": {"result": [{
        "meta": {"regularMarketPrice": 5.146},
        "timestamp": [1758000000 + day * 86400 for day in range(40)],
        "indicators": {"quote": [{"close": [4.9 + day * 0.01 for day in range(40)]}]},
    }]}})

    assert fx["date"] == "2026-10-21" and fx["series"]["CNY"][-1] == ["2026-10-21", 6.6761]
    assert crypto["coins"][0]["symbol"] == "BTC" and len(crypto["coins"][0]["sparkline"]) == sources.SPARK_POINTS
    # 40 daily closes: the first one within 30 days of the last is index 9.
    assert chart["last"] == 5.146 and chart["month_ago"] == pytest.approx(4.99)


def test_bls_year_over_year_and_unemployment():
    def series(values, start_year=2025):
        rows = []
        for index, value in enumerate(values):
            year, month = start_year + index // 12, index % 12 + 1
            rows.append({"year": str(year), "period": f"M{month:02d}", "value": str(value)})
        return {"Results": {"series": [{"data": rows}]}}

    cpi = series([100 + index for index in range(21)])
    data = sources.normalize_bls(cpi, series([4.0] * 20 + [4.2]))

    assert data["cpi"]["period"] == "2026-09"
    assert data["cpi"]["value"] == pytest.approx(round((120 / 108 - 1) * 100, 1))
    assert data["unemployment"] == {"value": 4.2, "previous": 4.0, "period": "2026-09"}


def _monthly(values_by_period):
    rows = [{"year": period[:4], "period": f"M{period[5:]}", "value": str(value)}
            for period, value in values_by_period.items()]
    return {"Results": {"series": [{"data": rows}]}}


def _months(first, count):
    year, month = (int(part) for part in first.split("-"))
    periods = []
    for _ in range(count):
        periods.append(f"{year}-{month:02d}")
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return periods


def test_cpi_falls_back_past_a_month_without_a_year_ago_reading():
    # BLS never published October 2025 CPI (the 2025 lapse in appropriations); its value is "-".
    values = {period: 300 + index for index, period in enumerate(_months("2024-09", 26))}
    values["2025-10"] = "-"
    unemployment = _monthly({"2026-09": 4.3, "2026-10": 4.4})

    october = sources.normalize_bls(_monthly(values), unemployment)
    values["2026-11"] = 330
    november = sources.normalize_bls(_monthly(values), unemployment)

    # October 2026 has no year-ago base, so the newest comparable month is shown instead of nothing.
    assert october["cpi"]["period"] == "2026-09"
    assert october["cpi"]["value"] == pytest.approx(round((324 / 312 - 1) * 100, 1))
    assert october["cpi"]["previous"] == pytest.approx(round((323 / 311 - 1) * 100, 1))
    # November compares with November; the month before it has no reading to compare with.
    assert november["cpi"] == {"value": pytest.approx(round((330 / 314 - 1) * 100, 1)),
                               "previous": None, "period": "2026-11"}


def test_fred_cpi_compares_each_month_with_the_same_month_a_year_earlier(monkeypatch):
    cpi = {period: 300 + index for index, period in enumerate(_months("2024-09", 26))}
    del cpi["2025-10"]  # FRED leaves the unpublished month out of the series

    def fake_series(series_id, _key, _context, limit):
        rows = {"CPIAUCSL": cpi, "UNRATE": {"2026-09": 4.3, "2026-10": 4.4},
                "DFF": {"2026-10": 4.1}, "MORTGAGE30US": {"2026-10": 6.2}}[series_id]
        return [(f"{period}-01", value) for period, value in sorted(rows.items(), reverse=True)][:limit]

    monkeypatch.setattr(sources, "_fred_series", fake_series)
    data = sources.fetch_macro("key", None)

    assert data["cpi"] == {"value": pytest.approx(round((324 / 312 - 1) * 100, 1)),
                           "previous": pytest.approx(round((323 / 311 - 1) * 100, 1)), "period": "2026-09"}
    assert data["unemployment"] == {"value": 4.4, "previous": 4.3, "period": "2026-10"}


def test_one_failing_market_symbol_keeps_the_others(monkeypatch):
    chart = {"chart": {"result": [{
        "meta": {"regularMarketPrice": 4.2},
        "timestamp": [1758000000 + day * 86400 for day in range(5)],
        "indicators": {"quote": [{"close": [4.0, 4.1, 4.2, 4.1, 4.2]}]},
    }]}}

    def fake_get(_method, url, _context, **_kwargs):
        if "DX-Y.NYB" in url:
            raise HttpStatusError("GET", url, 404)
        return chart

    monkeypatch.setattr(sources, "_get", fake_get)
    quotes = sources.fetch_markets(None)["quotes"]

    assert "DX-Y.NYB" not in quotes
    assert set(quotes) == set(sources.MARKET_SYMBOLS) - {"DX-Y.NYB"}


def test_earnings_already_reported_drop_out_of_a_kept_cache():
    items = [{"symbol": "TSLA", "date": "2026-10-20"}, {"symbol": "AAPL", "date": "2026-10-21"},
             {"symbol": "NVDA", "date": "2026-11-19"}, {"symbol": "BAD", "date": "soon"}]

    assert [item["symbol"] for item in sources.upcoming_earnings(items, NOW)] == ["AAPL", "NVDA"]


def test_news_rss_and_sec_submissions_normalize():
    rss = """<?xml version="1.0"?><rss version="2.0"><channel><title>Yahoo</title>
      <item><title>Tesla to report earnings</title><pubDate>Wed, 21 Oct 2026 20:05:00 +0000</pubDate></item>
      <item><title>Tesla to report earnings</title><pubDate>Wed, 21 Oct 2026 20:06:00 +0000</pubDate></item>
      <item><title>Nvidia partner news</title><pubDate>Wed, 21 Oct 2026 19:00:00 +0000</pubDate></item>
    </channel></rss>"""
    news = sources.normalize_news(rss)
    filings, insider = sources.normalize_submissions("TSLA", {"filings": {"recent": {
        "form": ["4", "8-K", "10-Q", "4", "8-K"],
        "filingDate": ["2026-10-20", "2026-10-19", "2026-10-10", "2026-08-01", "2026-06-01"],
        "items": ["", "2.02,9.01", "", "", "5.02"],
        "primaryDocDescription": ["", "8-K", "10-Q", "", "8-K"],
    }}}, NOW)

    assert [item["title"] for item in news["items"]] == ["Tesla to report earnings", "Nvidia partner news"]
    assert [(item["form"], item["items"]) for item in filings] == [("8-K", ["2.02"]), ("10-Q", [])]
    assert insider == 1


def test_news_is_fetched_per_holding_and_interleaved(monkeypatch):
    feeds = {
        "TSLA": "<rss><channel><item><title>Tesla A</title><pubDate>Wed, 21 Oct 2026 20:00:00 +0000</pubDate></item>"
                "<item><title>Tesla B</title><pubDate>Wed, 21 Oct 2026 19:00:00 +0000</pubDate></item></channel></rss>",
        "AAPL": "<rss><channel><item><title>Apple A</title><pubDate>Wed, 21 Oct 2026 18:00:00 +0000</pubDate></item>"
                "<item><title>Tesla A</title><pubDate>Wed, 21 Oct 2026 20:00:00 +0000</pubDate></item></channel></rss>",
    }

    def fake_get(_method, _url, _context, **kwargs):
        symbol = kwargs["params"]["s"]
        if symbol == "FAIL":
            raise sources.HttpClientError("down")
        return feeds[symbol]

    monkeypatch.setattr(sources, "_get", fake_get)
    items = sources.fetch_news(("TSLA", "FAIL", "AAPL"), None)["items"]

    assert [(item["symbol"], item["title"]) for item in items] == [
        ("TSLA", "Tesla A"), ("AAPL", "Apple A"), ("TSLA", "Tesla B")]


def test_symbols_follow_the_stock_page_unless_set(monkeypatch):
    plugin = MoneyRadar({"id": "money_radar"})
    monkeypatch.setattr(money_module, "read_contexts",
                        lambda *_args, **_kwargs: [{"payload": {"symbols": ["SPCX", "AAPL", "CASH", "aapl"]}}])

    assert plugin._symbols({}) == (("SPCX", "AAPL"), True)
    assert plugin._symbols({"symbols": "msft, nvda"}) == (("MSFT", "NVDA"), False)
    monkeypatch.setattr(money_module, "read_contexts", lambda *_args, **_kwargs: [])
    assert plugin._symbols({"symbols": "auto"}) == (sources.DEFAULT_SYMBOLS, False)
    with pytest.raises(ValueError):
        sources.symbols_setting("$$$")


def _fake_sources(monkeypatch):
    monkeypatch.setattr(sources, "fetch_markets", lambda _ctx: {"quotes": {
        symbol: {"last": value, "month_ago": value - 0.1, "series": [value - 0.1, value]}
        for symbol, value in (("^IRX", 4.06), ("^FVX", 4.97), ("^TNX", 5.15), ("^TYX", 5.44),
                              ("DX-Y.NYB", 101.7), ("GC=F", 4153.0))}})
    monkeypatch.setattr(sources, "fetch_fx", lambda *_args: {"base": "USD", "date": "2026-10-21", "series": {
        "CNY": [["2026-09-22", 6.7], ["2026-10-21", 6.6761]], "EUR": [["2026-10-21", 0.86]]}})
    monkeypatch.setattr(sources, "fetch_crypto", lambda *_args: {"coins": [
        {"id": "bitcoin", "symbol": "BTC", "name": "Bitcoin", "price": 84476.0, "change_24h": 2.4,
         "change_7d": 3.5, "sparkline": [1, 2, 3]}]})
    monkeypatch.setattr(sources, "fetch_news", lambda *_args: {"items": [
        {"title": "Tesla to report earnings", "time": "2026-10-21T20:05:00+00:00"}]})
    monkeypatch.setattr(sources, "fetch_macro", lambda *_args: {
        "source": "BLS", "cpi": {"value": 3.7, "previous": 3.4, "period": "2026-09"},
        "unemployment": {"value": 4.2, "previous": 4.1, "period": "2026-09"}})


def test_keyless_refresh_disables_key_gated_feeds(monkeypatch, tmp_path):
    monkeypatch.setenv("INKYPI_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setattr(money_module, "read_contexts", lambda *_args, **_kwargs: [])
    _fake_sources(monkeypatch)
    for name in ("fetch_earnings", "fetch_ciks", "fetch_filings"):
        monkeypatch.setattr(sources, name, lambda *_args: pytest.fail("key-gated feed fetched without its key"))

    image = MoneyRadar({"id": "money_radar"}).generate_image({"_inkypi_theme": {"mode": "day"}}, Device())

    assert image.size == (800, 480)


def test_keys_and_contact_switch_on_earnings_and_filings(monkeypatch, tmp_path):
    monkeypatch.setenv("INKYPI_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setattr(money_module, "read_contexts", lambda *_args, **_kwargs: [])
    _fake_sources(monkeypatch)
    seen = {}
    monkeypatch.setattr(sources, "fetch_earnings", lambda symbols, key, *_args: seen.setdefault("key", key) and {
        "items": [{"symbol": "TSLA", "date": "2026-10-28", "hour": "amc", "eps_estimate": 0.5}]})
    monkeypatch.setattr(sources, "fetch_ciks", lambda symbols, contact, _ctx: seen.setdefault("contact", contact)
                        and {"map": {"TSLA": 1318605}})
    monkeypatch.setattr(sources, "fetch_filings", lambda *_args: {
        "items": [{"symbol": "TSLA", "form": "8-K", "date": "2026-10-19", "items": ["2.02"], "description": ""}],
        "insider": {"TSLA": 2}})
    plugin = MoneyRadar({"id": "money_radar"})
    payload = plugin._payload({"secContact": "me@example.com", "symbols": "TSLA"},
                              Device({"FINNHUB_API_KEY": "k"}), NOW, allow_network=True,
                              context=money_module.TaskContext.never_cancelled(deadline_monotonic=1e12))

    assert seen == {"key": "k", "contact": "me@example.com"}
    assert payload["earnings"]["state"] == payload["filings"]["state"] == "live"
    image = render_page((800, 480), payload, {"_inkypi_theme": {"mode": "night"}}, NOW)
    assert image.size == (800, 480)


def _canvas(mode):
    return PageCanvas(money_render._palette({"_inkypi_theme": {"mode": mode}}))


def _luminance(colour):
    def channel(value):
        value /= 255
        return value / 12.92 if value <= 0.03928 else ((value + 0.055) / 1.055) ** 2.4
    red, green, blue = (channel(value) for value in colour[:3])
    return 0.2126 * red + 0.7152 * green + 0.0722 * blue


def _hex(colour):
    return "#" + "".join(f"{channel:02X}" for channel in colour[:3])


def test_holding_chips_wear_their_brands_published_colours_unchanged():
    canvas = _canvas("day")
    ink, paper = canvas.color("ink"), canvas.color("paper")

    assert _hex(chip_style(canvas, "NVDA")[0]) == "#76B900"
    assert _hex(chip_style(canvas, "TSLA")[0]) == "#E82127"
    assert _hex(chip_style(canvas, "SPY")[0]) == "#001AFF"
    assert chip_style(canvas, "NTDOY") == (paper, (0xE6, 0x00, 0x12), (0xE6, 0x00, 0x12))
    assert chip_style(canvas, "AAPL") == (ink, paper, None)
    assert chip_style(canvas, "SPCX") == (paper, ink, ink)
    # Vanguard's funds share its maroon; an unknown ticker keeps the plain ink chip.
    assert chip_style(canvas, "VTI") == chip_style(canvas, "vxus")
    assert _hex(chip_style(canvas, "VTI")[0]) == "#96151D"
    assert chip_style(canvas, "ZZZZ") == (ink, paper, None)


@pytest.mark.parametrize("mode", ["day", "night"])
def test_every_brand_chip_keeps_its_text_legible(mode):
    canvas = _canvas(mode)
    for symbol in [*money_render.BRAND_CHIPS, "ZZZZ"]:
        fill, text, _outline = chip_style(canvas, symbol)
        lighter, darker = sorted((_luminance(fill), _luminance(text)), reverse=True)
        assert (lighter + 0.05) / (darker + 0.05) >= 3, symbol
    # At night the monochrome chips invert with the page instead of vanishing into it.
    assert chip_style(canvas, "AAPL")[0] == canvas.color("ink")


def test_price_text_scales_decimals():
    assert price_text(84476.2) == "$84,476"
    assert price_text(3512.4) == "$3,512"
    assert price_text(23.456) == "$23.46"
    assert price_text(0.12345) == "$0.1235"


@pytest.mark.parametrize("key", ["cpi", "unemployment", "dxy", "gold", "fed_funds", "mortgage30", "curve"])
def test_every_macro_indicator_has_its_own_icon(key):
    icon = money_render._asset(f"macro-{key}", money_render.MACRO_ICON_SIZE)
    assert icon is not None and icon.height <= money_render.MACRO_ICON_SIZE
    assert icon.getchannel("A").getextrema()[1] == 255
    # The stored artwork keeps a transparent background (a card icon only at its rounded corners).
    with money_render.Image.open(money_render.ASSETS / f"macro-{key}.png") as source:
        assert source.convert("RGBA").getchannel("A").getextrema()[0] == 0


def test_macro_tiles_put_the_icon_before_the_title():
    canvas = PageCanvas(money_render._palette({}), family="Microsoft YaHei")
    payload = {"macro": {"data": {"cpi": {"value": 3.7, "previous": 3.4, "period": "2026-09"}, "source": "BLS"}},
               "markets": {"data": {"quotes": {"GC=F": {"last": 4030.0, "month_ago": 4318.0}}}}}
    money_render._macro_tiles(canvas, payload)
    # The cart's warm red-orange sits left of where the title text starts.
    corner = canvas.image.crop((408, 206, 430, 226)).convert("RGB")
    assert any(r > 180 and g < 140 for r, g, _b in corner.get_flattened_data())
