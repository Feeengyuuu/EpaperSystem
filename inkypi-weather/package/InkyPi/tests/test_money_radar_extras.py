"""Money Radar's rotating feeds: CBOE positioning, China's indices, the central parity and the
retail chatter list."""

import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from plugins.base_plugin.render_provenance import SourceProvenance  # noqa: E402
from plugins.money_radar import extra_panels, extras, sources  # noqa: E402
from plugins.money_radar import money_radar as money_module  # noqa: E402
from plugins.money_radar import render as money_render  # noqa: E402
from plugins.money_radar.money_radar import MoneyRadar  # noqa: E402
from utils.http_client import HttpStatusError  # noqa: E402
from utils.page_canvas import PageCanvas  # noqa: E402
from tests.test_http_client import FakeResponse, FakeSession  # noqa: E402
from utils.http_client import HttpClient  # noqa: E402

NOW = datetime(2026, 11, 11, 13, 0, tzinfo=sources.PACIFIC)
EXTRA_HTTP_GET = extras._get

# nfs.faireconomy.media/ff_calendar_thisweek.json, 2026-11-11 (rows of the week's 106).
PUT_CALL = {"ratios": [
    {"name": "TOTAL PUT/CALL RATIO", "value": "0.95"}, {"name": "INDEX PUT/CALL RATIO", "value": "1.02"},
    {"name": "EXCHANGE TRADED PRODUCTS PUT/CALL RATIO", "value": "1.04"},
    {"name": "EQUITY PUT/CALL RATIO", "value": "0.77"},
    {"name": "CBOE VOLATILITY INDEX (VIX) PUT/CALL RATIO", "value": "0.59"},
    {"name": "SPX + SPXW + SPXO PUT/CALL RATIO", "value": "1.11"}, {"name": "OEX PUT/CALL RATIO", "value": "0.00"}],
    "SUM OF ALL PRODUCTS": [{"name": "VOLUME", "call": 6632948, "put": 6319117, "total": 12952065}]}
# www.cboe.com/us/futures/market_statistics/settlement/csv?dt=2026-11-10 (the VIX futures, then
# the first of the other products).
VIX_CSV = """Product,Symbol,Expiration Date,Price
VX,VX45/X6,2026-11-11,19.9067
VX,VX/X6,2026-11-18,19.9067
VX,VX47/X6,2026-11-24,19.9067
VX,VX/Z6,2026-12-16,19.5125
VX,VX51/Z6,2026-12-23,19.5125
VX,VX/F7,2027-01-20,20.0385
VX,VX/G7,2027-02-17,20.3507
VX,VX/H7,2027-03-17,20.5457
VX,VX/J7,2027-04-21,20.8593
VX,VX/K7,2027-05-18,20.9492
VX,VX/M7,2027-06-16,21.00
VX,VX/N7,2027-07-21,21.325
VXM,VXM/X6,2026-11-18,19.9067
VA,VA/X6,2026-11-20,203.75
"""
# The same address for the running day, before its settlement: only the expiring weekly.
VIX_CSV_EARLY = "Product,Symbol,Expiration Date,Price\nVX,VX45/X6,2026-11-11,18.56\n"
# qt.gtimg.cn/q=s_sh000001,s_sz399001,s_sz399006,s_hkHSI,s_hkHSTECH, 2026-11-11 (GBK names).
CN_INDICES = (
    'v_s_sh000001="1~上证指数~000001~3680.64~16.92~0.46~433120083~73615479~~646127.31~ZS~";\n'
    'v_s_sz399001="51~深证成指~399001~11389.74~148.20~1.32~569401113~94012379~~385956.73~ZS~";\n'
    'v_s_sz399006="51~创业板指~399006~2572.07~77.67~3.11~184074734~47841677~~156129.27~ZS~";\n'
    'v_s_hkHSI="100~恒生指数~HSI~24315.920~-241.120~-0.98~18074914.7485~18074914.748~~0";\n'
    'v_s_hkHSTECH="100~恒生科技指数~HSTECH~4027.060~-57.390~-1.41~4839883.8013~4839883.801~~0";\n')
# www.chinamoney.com.cn/ags/ms/cm-u-bk-ccpr/CcprHisNew?...currency=USD/CNY (newest first).
PARITY = {"head": {"rep_code": "200"}, "data": {"currency": "USD/CNY", "total": 23}, "records": [
    {"date": "2026-11-11", "values": ["6.7191"]}, {"date": "2026-11-10", "values": ["6.7167"]},
    {"date": "2026-11-09", "values": ["6.7188"]}, {"date": "2026-11-06", "values": ["6.7168"]},
    {"date": "2026-11-05", "values": ["6.7156"]}, {"date": "bad", "values": ["6.7"]},
    {"date": "2026-11-04", "values": ["---"]}]}
# api.nasdaq.com/api/ipo/calendar?date=2026-11 (its upcoming table, one priced row).
# apewisdom.io/api/v1.0/filter/wallstreetbets/page/1, cut down.
CHATTER = {"count": 600, "pages": 6, "current_page": 1, "results": [
    {"rank": 1, "ticker": "NVDA", "name": "NVIDIA", "mentions": "2430", "upvotes": "9000", "rank_24h_ago": "1",
     "mentions_24h_ago": "1800"},
    {"rank": 2, "ticker": "AMD", "name": "AMD", "mentions": 535, "upvotes": 800, "rank_24h_ago": 4, "mentions_24h_ago": 300},
    {"rank": 3, "ticker": "NBIS", "name": "Nebius", "mentions": 476, "upvotes": 500, "rank_24h_ago": None,
     "mentions_24h_ago": None},
    {"rank": 4, "ticker": "SPY", "name": "SPDR S&P 500", "mentions": 281, "upvotes": 300, "rank_24h_ago": 3,
     "mentions_24h_ago": 400},
    {"rank": 5, "ticker": "not a ticker", "mentions": 100},
    {"rank": 6, "ticker": "MU", "name": "Micron", "mentions": 198, "upvotes": 100, "rank_24h_ago": 7, "mentions_24h_ago": 90}]}


def _context():
    return money_module.TaskContext.never_cancelled(deadline_monotonic=1e12)


def _result(data, state="fresh_cache"):
    return {"data": data, "state": state, "error": "", "fetched_at": NOW.isoformat()}


def _all_extras():
    """Every rotating feed as its normalised reading of the fixtures above."""
    return {
        "put_call": _result(extras.normalize_put_call(PUT_CALL, "2026-11-10")),
        "vix_curve": _result(extras.normalize_vix_curve(VIX_CSV, "2026-11-10")),
        "cn_indices": _result(extras.normalize_cn_indices(CN_INDICES)),
        "parity": _result(extras.normalize_parity(PARITY)),
        "chatter": _result(extras.normalize_chatter(CHATTER)),
    }


def _slot_time(panel):
    """The start of a three-hour slot from NOW on that schedules ``panel`` on the yield-curve card."""
    first = int(NOW.timestamp() // extras.SLOT_SECONDS)
    for index in range(first, first + len(extras.SLOT_A)):
        if extras.SLOT_A[index % len(extras.SLOT_A)] == panel:
            return datetime.fromtimestamp(index * extras.SLOT_SECONDS, sources.PACIFIC)
    raise AssertionError(panel)


def _schedule(monkeypatch, panel):
    """Make the slot of every moment the one that schedules ``panel``."""
    index = int(_slot_time(panel).timestamp() // extras.SLOT_SECONDS)
    monkeypatch.setattr(extras, "slot_index", lambda _now: index)


def _recording_canvas(monkeypatch, mode="day"):
    """A page canvas whose texts are recorded as (x, y, text), the page's own and the news rows';
    ``canvas.tones`` collects the colour each was drawn in."""
    canvas = PageCanvas(money_render._palette({"_inkypi_theme": {"mode": mode}}), family="Microsoft YaHei")
    drawn, canvas.tones = [], []
    original = canvas.text

    def page_text(xy, text, size=14, color="ink", **kwargs):
        drawn.append((round(xy[0]), round(xy[1]), str(text)))
        canvas.tones.append(color)
        return original(xy, text, size, color, **kwargs)

    row_text = extra_panels.news_rows.text

    def news_text(target, xy, value, role="title", size=13, color="ink", align="left", width=None):
        drawn.append((round(xy[0]), round(xy[1]), str(value)))
        canvas.tones.append(color)
        return row_text(target, xy, value, role, size, color, align, width)

    monkeypatch.setattr(canvas, "text", page_text)
    monkeypatch.setattr(extra_panels.news_rows, "text", news_text)
    return canvas, drawn


# --- parsers --------------------------------------------------------------------------------

def test_cboe_put_call_ratios_and_the_vix_futures_curve():
    ratios = extras.normalize_put_call(PUT_CALL, "2026-11-10")
    curve = extras.normalize_vix_curve(VIX_CSV, "2026-11-10")

    assert ratios == {"date": "2026-11-10", "ratios": {"total": 0.95, "index": 1.02, "etp": 1.04, "equity": 0.77,
                                                        "vix": 0.59, "spx": 1.11}}
    # Only the monthly VIX futures make the curve: no weeklies, minis or other products.
    assert [point["month"] for point in curve["points"]] == [11, 12, 1, 2, 3, 4, 5, 6, 7]
    assert curve["points"][0] == {"expiry": "2026-11-18", "month": 11, "price": 19.9067}
    assert curve["points"][-1]["price"] == 21.325 and curve["date"] == "2026-11-10"
    assert extras.valid_put_call(ratios) and extras.valid_vix_curve(curve)
    # The running day's file holds only the expiring weekly until the settlement is published.
    with pytest.raises(ValueError):
        extras.normalize_vix_curve(VIX_CSV_EARLY, "2026-11-11")
    with pytest.raises(ValueError):
        extras.normalize_put_call({"ratios": [{"name": "OEX PUT/CALL RATIO", "value": "0.00"}]}, "2026-11-10")


def test_cboe_files_are_dated_and_the_newest_published_day_is_asked_once(monkeypatch):
    # 13:00 Pacific on Wednesday is before that day's files; the candidates start at Tuesday.
    assert extras.cboe_days(NOW)[:3] == ["2026-11-10", "2026-11-09", "2026-11-06"]
    evening = NOW.replace(hour=18)
    assert extras.cboe_days(evening)[:2] == ["2026-11-11", "2026-11-10"]
    saturday = datetime(2026, 11, 14, 9, 0, tzinfo=sources.PACIFIC)
    assert extras.cboe_days(saturday)[0] == "2026-11-13"

    asked = []

    def fake_get(url, _context, **_kwargs):
        asked.append(url)
        if "2026-11-11" in url:
            raise HttpStatusError("GET", url, 403)
        return PUT_CALL

    monkeypatch.setattr(extras, "_get", fake_get)
    missed = {}
    with pytest.raises(HttpStatusError):
        extras.fetch_put_call(evening, None, known={}, missed=missed)
    # The missing day is remembered, so the next refresh asks for the day before: one request each.
    assert list(missed) == ["2026-11-11"] and len(asked) == 1
    data = extras.fetch_put_call(evening, None, known={}, missed=missed)
    assert data["date"] == "2026-11-10" and len(asked) == 2
    # Nothing newer than the kept reading can exist yet: it is kept without a request.
    assert extras.fetch_put_call(evening, None, known=data, missed=missed) == data and len(asked) == 2
    # A miss is forgotten after a few hours, when the day's file may have been published.
    later = {"2026-11-11": (evening - timedelta(seconds=extras.MISSED_DAY_SECONDS + 60)).isoformat()}
    with pytest.raises(HttpStatusError):
        extras.fetch_put_call(evening, None, known=data, missed=later)
    assert len(asked) == 3 and "2026-11-11" in asked[-1]


def test_chinas_indices_and_the_central_parity():
    indices = extras.normalize_cn_indices(CN_INDICES)
    parity = extras.normalize_parity(PARITY)

    assert [(row["name"], row["last"], row["pct"]) for row in indices["indices"]] == [
        ("上证", 3680.64, 0.46), ("深成", 11389.74, 1.32), ("创业板", 2572.07, 3.11), ("恒生", 24315.92, -0.98),
        ("恒生科技", 4027.06, -1.41)]
    assert indices["indices"][3]["change"] == -241.12 and extras.valid_cn_indices(indices)
    with pytest.raises(ValueError):
        extras.normalize_cn_indices('v_pv_none_match="1";')
    # Oldest first, the unreadable rows left out.
    assert parity == {"date": "2026-11-11", "series": [["2026-11-05", 6.7156], ["2026-11-06", 6.7168],
                                                       ["2026-11-09", 6.7188], ["2026-11-10", 6.7167],
                                                       ["2026-11-11", 6.7191]]}
    assert extras.parity_reading(parity) == (6.7191, pytest.approx(24.0), [6.7156, 6.7168, 6.7188, 6.7167, 6.7191])
    assert extras.parity_reading({}) is None and extras.valid_parity(parity)
    with pytest.raises(ValueError):
        extras.normalize_parity({"records": [{"date": "2026-11-11", "values": ["6.7191"]}]})


# --- rotation -------------------------------------------------------------------------------

def test_the_yield_curve_card_takes_turns_every_three_hours():
    moments = [NOW + timedelta(seconds=step * extras.SLOT_SECONDS) for step in range(6)]
    scheduled = [extras.slot_panel(moment) for moment in moments]

    assert extras.SLOT_A == ("curve", "volatility", "china")
    first = extras.SLOT_A.index(scheduled[0])
    assert scheduled == [extras.SLOT_A[(first + step) % 3] for step in range(6)]
    assert extras.slot_panel(moments[0] + timedelta(minutes=30)) in (scheduled[0], scheduled[1])
    # Of the lists that shared the holdings card for a day, only the retail chatter is kept: it
    # takes turns with the holdings news.
    assert extras.FEEDS == ("put_call", "vix_curve", "cn_indices", "parity", "chatter")
    assert extras.SLOT_B == ("chatter", "news") and not hasattr(extras, "fetch_wire")
    assert [extras.list_panel(moment) for moment in moments[:4]] in (["chatter", "news"] * 2, ["news", "chatter"] * 2)


def test_the_chatter_list_gives_its_turn_to_the_news_without_its_ranking(monkeypatch):
    full = _all_extras()
    monkeypatch.setattr(extras, "slot_index", lambda _now: 0)
    assert extras.shown_list(full, NOW) == "chatter"
    assert extras.shown_list({**full, "chatter": _result({}, "unavailable")}, NOW) == "news"
    assert extras.shown_list(None, NOW) == "news"
    short = {"tickers": full["chatter"]["data"]["tickers"][:extras.CHATTER_MIN - 1]}
    assert extras.shown_list({**full, "chatter": _result(short)}, NOW) == "news"
    monkeypatch.setattr(extras, "slot_index", lambda _now: 1)
    assert extras.shown_list(full, NOW) == "news"


def test_the_chatter_ranking_normalizes_and_keeps_the_logos_it_already_has(monkeypatch):
    chatter = extras.normalize_chatter(CHATTER)
    assert [row["ticker"] for row in chatter["tickers"]] == ["NVDA", "AMD", "NBIS", "SPY", "MU"]
    assert chatter["tickers"][0] == {"ticker": "NVDA", "mentions": 2430, "rank": 1, "rank_before": 1,
                                     "mentions_before": 1800}
    assert [extras.rank_move(row) for row in chatter["tickers"]] == [0, 2, None, -1, 1]
    assert extras.valid_chatter(chatter) and not extras.valid_chatter({"tickers": []})
    with pytest.raises(ValueError):
        extras.normalize_chatter({"results": [{"ticker": "??", "mentions": 1, "rank": 1}]})

    asked = []
    monkeypatch.setattr(extras, "_get", lambda *_args, **_kwargs: CHATTER)
    monkeypatch.setattr(extras, "_ticker_logo", lambda ticker, _context: asked.append(ticker) or f"logo-{ticker}")
    monkeypatch.setattr(extras, "TICKER_LOGOS", 4)
    first = extras.fetch_chatter(None)
    # The list's first four have their logos; the fifth is not asked for.
    assert asked == ["NVDA", "AMD", "NBIS", "SPY"] and "logo" not in first["tickers"][4]
    assert first["tickers"][1]["logo"] == "logo-AMD" and extras.valid_chatter(first)
    # The next reading asks only for the tickers it has no logo for ("" counts: there is none).
    first["tickers"][2]["logo"] = ""
    asked.clear()
    again = extras.fetch_chatter(None, {"tickers": first["tickers"][1:]})
    assert asked == ["NVDA"] and again["tickers"][2]["logo"] == ""


def test_a_ticker_logo_is_read_from_the_image_host_and_kept_small(monkeypatch):
    import base64
    import io

    from PIL import Image

    art = Image.new("RGBA", (250, 250), (0, 0, 0, 0))
    art.paste((118, 185, 0, 255), (25, 75, 225, 175))
    out = io.BytesIO()
    art.save(out, format="PNG")
    asked = []

    class Client:
        def request_bytes(self, method, url, **_options):
            asked.append(url)
            if "MISSING" in url:
                raise HttpStatusError("GET", url, 404)
            return type("Reply", (), {"data": out.getvalue()})()

    monkeypatch.setattr(extras, "get_http_client", lambda: Client())
    logo = Image.open(io.BytesIO(base64.b64decode(extras._ticker_logo("NVDA", None))))
    # Its transparent margin is cut and the mark kept at most TICKER_LOGO_PIXELS a side.
    assert logo.size == (extras.TICKER_LOGO_PIXELS, extras.TICKER_LOGO_PIXELS // 2)
    assert asked == ["https://images.financialmodelingprep.com/symbol/NVDA.png"]
    assert extras._ticker_logo("MISSING", None) == ""


def test_a_panel_without_its_data_gives_its_turn_to_the_next(monkeypatch):
    full = _all_extras()
    _schedule(monkeypatch, "china")

    assert extras.shown_panel(full, NOW) == "china"
    # Without the indices the yield curve keeps the card.
    assert extras.shown_panel({**full, "cn_indices": _result({}, "unavailable")}, NOW) == "curve"
    # Nothing at all: the page is the one it was.
    assert extras.shown_panel({}, NOW) == "curve"
    _schedule(monkeypatch, "volatility")
    assert extras.shown_panel(None, NOW) == "curve"
    # The volatility card needs the curve; the ratios are an extra on it.
    assert extras.shown_panel({"vix_curve": full["vix_curve"]}, NOW) == "volatility"
    assert extras.shown_panel({"put_call": full["put_call"]}, NOW) == "curve"
    assert extras.shown_panel({"put_call": full["put_call"], "cn_indices": full["cn_indices"]}, NOW) == "china"


# --- loading --------------------------------------------------------------------------------

def _fetchers(monkeypatch, calls, failing=()):
    """Stand-ins for every feed's fetch that record their name; ``failing`` ones raise."""
    readings = _all_extras()

    def fetcher(name):
        def fetch(*_args, **_kwargs):
            calls.append(name)
            if name in failing:
                raise HttpStatusError("GET", name, 503)
            return readings[name]["data"]
        return fetch

    for name in extras.FEEDS:
        monkeypatch.setattr(extras, f"fetch_{name}", fetcher(name))


def _book(tmp_path):
    return money_module.SourceBook(tmp_path / "sources", money_module.CACHE_BUDGET, schema=money_module.CACHE_SCHEMA)


@pytest.mark.parametrize("feed", extras.FEEDS)
def test_retry_after_stays_within_the_rotating_sources_budget(monkeypatch, tmp_path, feed):
    response = FakeResponse(503, headers={"Retry-After": "30"})
    session = FakeSession([response])
    monkeypatch.setattr(extras, "_get", EXTRA_HTTP_GET)
    monkeypatch.setattr(extras, "get_http_client", lambda: HttpClient(session=session))
    monkeypatch.setattr(extras, "_due", lambda *_args: feed)
    parent = money_module.TaskContext.never_cancelled(deadline_monotonic=120, clock=lambda: 0)

    loaded = extras.load(_book(tmp_path), NOW, allow_network=True, context=parent)

    assert loaded[feed]["error"] == "source_deadline"
    assert loaded[feed]["state"] == "unavailable"
    assert len(session.calls) == 1 and response.closed
    assert parent.remaining_seconds() == 120


def test_a_refresh_fetches_at_most_one_rotating_feed_and_those_on_show_first(monkeypatch, tmp_path):
    calls = []
    _fetchers(monkeypatch, calls)
    book = _book(tmp_path)
    moment = _slot_time("volatility")
    order = []
    for step in range(len(extras.FEEDS)):
        calls.clear()
        loaded = extras.load(book, moment + timedelta(minutes=step), allow_network=True, context=_context())
        assert len(calls) == 1 and set(loaded) == set(extras.FEEDS)
        order.extend(calls)

    # Each refresh brought one feed: first the ones this slot shows (the parity always shows).
    assert sorted(order) == sorted(extras.FEEDS)
    shown = extras.shown_feeds(moment)
    assert {"vix_curve", "put_call", "parity"} <= shown and "cn_indices" not in shown
    assert set(order[:len(shown)]) == shown
    assert all(result["state"] in ("live", "fresh_cache") for result in loaded.values())
    # Everything is fresh now: the next refresh asks nobody, and a hidden feed (the indices, an
    # hour's life) waits three hours however short its own life.
    calls.clear()
    extras.load(book, moment + timedelta(minutes=20), allow_network=True, context=_context())
    extras.load(book, moment + timedelta(minutes=100), allow_network=True, context=_context())
    assert calls == []


def test_a_redraw_from_cache_fetches_nothing(monkeypatch, tmp_path):
    calls = []
    _fetchers(monkeypatch, calls)

    loaded = extras.load(_book(tmp_path), NOW, allow_network=False, context=_context())

    assert calls == [] and all(result["state"] == "unavailable" for result in loaded.values())


def test_a_failing_feed_keeps_its_last_reading_and_waits_before_its_next_try(monkeypatch, tmp_path):
    calls = []
    _fetchers(monkeypatch, calls)
    # The chatter list stays out of this: it is fresh for the whole test.
    monkeypatch.setitem(extras.FRESH_SECONDS, "chatter", 86400)
    book = _book(tmp_path)
    moment = _slot_time("china")
    for step in range(len(extras.FEEDS)):
        extras.load(book, moment + timedelta(minutes=step), allow_network=True, context=_context())
    _fetchers(monkeypatch, calls, failing=("cn_indices",))

    calls.clear()
    later = moment + timedelta(minutes=70)
    loaded = extras.load(book, later, allow_network=True, context=_context())
    # The indices failed: the page keeps the last ones, marked stale for nobody but themselves.
    assert calls == ["cn_indices"] and loaded["cn_indices"]["state"] == "stale_cache"
    assert loaded["cn_indices"]["data"]["indices"]
    # They are not asked again at once, so they cannot take every refresh's one request.
    calls.clear()
    extras.load(book, later + timedelta(minutes=30), allow_network=True, context=_context())
    assert "cn_indices" not in calls
    calls.clear()
    retry = later + timedelta(seconds=extras.RETRY_SECONDS + 60)
    # A refresh with no time left for it is no failure of the feed's: it is asked at the next one.
    spent = money_module.TaskContext.never_cancelled(deadline_monotonic=1.0, clock=lambda: 0.0)
    assert extras.load(book, retry, allow_network=True, context=spent)["cn_indices"]["error"] == "deadline_budget"
    extras.load(book, retry + timedelta(minutes=30), allow_network=True, context=_context())
    assert calls == ["cn_indices"]


def _fake_core(monkeypatch):
    monkeypatch.setattr(sources, "fetch_markets", lambda _ctx: {"quotes": {
        symbol: {"last": value, "month_ago": value - 0.1, "series": [value - 0.1, value]}
        for symbol, value in (("^IRX", 4.06), ("^FVX", 4.97), ("^TNX", 5.15), ("^TYX", 5.44),
                              ("DX-Y.NYB", 101.7), ("GC=F", 4153.0))}})
    monkeypatch.setattr(sources, "fetch_fx", lambda *_args: {"base": "USD", "date": "2026-11-11", "series": {
        "CNY": [["2026-10-12", 6.7], ["2026-11-11", 6.6761]], "EUR": [["2026-11-11", 0.86]]}})
    monkeypatch.setattr(sources, "fetch_crypto", lambda *_args: {"coins": [
        {"id": "bitcoin", "symbol": "BTC", "name": "Bitcoin", "price": 84476.0, "change_24h": 2.4,
         "change_7d": 3.5, "sparkline": [1, 2, 3]}]})
    monkeypatch.setattr(sources, "fetch_news", lambda *_args: {"items": [
        {"title": "Tesla to report earnings", "time": "2026-11-11T20:05:00+00:00", "symbol": "TSLA"}]})
    monkeypatch.setattr(sources, "fetch_macro", lambda *_args: {
        "source": "BLS", "cpi": {"value": 3.7, "previous": 3.4, "period": "2026-09"},
        "unemployment": {"value": 4.2, "previous": 4.1, "period": "2026-09"}})
    monkeypatch.setattr(sources, "fetch_fear_greed", lambda _ctx: {"score": 34.6, "week": 16.7, "month": 30.3})
    monkeypatch.setattr(sources, "fetch_trending", lambda *_args: {"coins": [{"id": "x", "symbol": "X", "logo": ""}]})
    monkeypatch.setattr(sources, "fetch_nasdaq_earnings", lambda day, *_args: {"items": [], "date": day})


def test_the_rotating_feeds_never_mark_the_page_stale_or_failed(monkeypatch, tmp_path):
    monkeypatch.setenv("INKYPI_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setattr(money_module, "read_contexts", lambda *_args, **_kwargs: [])
    _fake_core(monkeypatch)
    calls = []
    _fetchers(monkeypatch, calls, failing=tuple(extras.FEEDS))
    plugin = MoneyRadar({"id": "money_radar"})

    payload = plugin._payload({}, None, NOW, allow_network=True, context=_context())

    # One feed was tried and failed; every core feed is live, and so is the page.
    assert len(calls) == 1 and payload["provenance"] is SourceProvenance.LIVE
    assert all(result["state"] == "unavailable" for result in payload["extras"].values())
    image = money_render.render_page((800, 480), payload, {"_inkypi_theme": {"mode": "day"}}, NOW)
    plain = money_render.render_page((800, 480), {key: value for key, value in payload.items() if key != "extras"},
                                     {"_inkypi_theme": {"mode": "day"}}, NOW)
    # With no rotating feed at hand the page is drawn exactly as it was.
    assert image.tobytes() == plain.tobytes()


# --- drawing --------------------------------------------------------------------------------

def _core_payload():
    quotes = {symbol: {"last": value, "month_ago": value - 0.1, "series": [value - 0.1, value]}
              for symbol, value in (("^IRX", 4.06), ("^FVX", 4.97), ("^TNX", 5.15), ("^TYX", 5.44),
                                    ("DX-Y.NYB", 101.7), ("GC=F", 4153.0))}
    news = [{"symbol": "AAPL", "title": f"Story {index}", "time": "2026-11-11T20:05:00+00:00"} for index in range(8)]
    return {
        "markets": {"data": {"quotes": quotes}},
        "fx": {"data": {"series": {"CNY": [[f"2026-10-{day:02d}", 6.66 + day / 1000] for day in range(12, 31)]}}},
        "crypto": {"data": {"coins": []}}, "trending": {"data": {}},
        "macro": {"data": {"source": "BLS", "cpi": {"value": 3.7, "previous": 3.4, "period": "2026-09"},
                           "unemployment": {"value": 4.2, "previous": 4.1, "period": "2026-09"}}},
        "fear_greed": {"data": {"score": 34.6, "week": 16.7, "month": 30.3}},
        "news": {"data": {"items": news}},
        "earnings": {"state": "live", "data": {"items": [], "source": "nasdaq", "days": 5}},
        "filings": {"state": "disabled", "data": {}},
        "symbols": ["AAPL", "NVDA", "MSFT"], "symbols_from_portfolio": False,
    }


@pytest.mark.parametrize("mode", ["day", "night"])
def test_the_dollar_card_carries_the_central_parity_beside_its_line(monkeypatch, mode):
    canvas, drawn = _recording_canvas(monkeypatch, mode)
    payload = {**_core_payload(), "extras": {"parity": _all_extras()["parity"]}}

    money_render._fx_tile(canvas, payload, money_render.TILES[0])

    texts = {text: (x, y) for x, y, text in drawn}
    left, right = money_render.TILES[0]
    # The parity stands at the end of the rate's line, as the coins' 7-day change does on theirs.
    assert texts["中间价"][0] == texts["6.7191"][0] == right - 10
    assert 150 <= texts["中间价"][1] < texts["6.7191"][1] <= 176
    drawn.clear()
    money_render._fx_tile(canvas, _core_payload(), money_render.TILES[0])
    assert "中间价" not in {text for *_xy, text in drawn}


@pytest.mark.parametrize("mode", ["day", "night"])
def test_the_volatility_card_takes_the_yield_curves_turn(monkeypatch, mode):
    canvas, drawn = _recording_canvas(monkeypatch, mode)
    loaded = _all_extras()

    extra_panels.draw_card(canvas, "volatility", loaded, NOW)

    left, top, right, bottom = money_render.CURVE_CARD
    assert all(left <= x <= right and top <= y <= bottom for x, y, _text in drawn)
    texts = [text for *_xy, text in drawn]
    assert "VIX 期货曲线" in texts
    # The curve's ends are labelled with their settlements, its foot with the contract months.
    assert {"19.91", "21.33"} <= set(texts) and {"11月", "7月"} <= set(texts)
    # Far above near: the structure is named, with the day of the settlement.
    assert any("升水" in text and "1.42" in text for text in texts) and any("11/10" in text for text in texts)
    # The put/call ratios read in one line over the plot.
    assert any("Put/Call" in text for text in texts) and {"0.95", "0.77", "1.02"} <= set(texts)
    plot = canvas.image.crop((left + 30, top + 50, right - 10, bottom - 20)).convert("RGB")
    assert extra_panels.VOLATILITY_COLOUR in set(plot.get_flattened_data())
    # Without the ratios the curve still stands.
    drawn.clear()
    extra_panels.draw_card(canvas, "volatility", {"vix_curve": loaded["vix_curve"]}, NOW)
    assert "VIX 期货曲线" in [text for *_xy, text in drawn] and not any("Put/Call" in text for *_xy, text in drawn)


@pytest.mark.parametrize("mode", ["day", "night"])
def test_the_china_card_lists_five_indices_and_the_parity(monkeypatch, mode):
    canvas, drawn = _recording_canvas(monkeypatch, mode)

    extra_panels.draw_card(canvas, "china", _all_extras(), NOW)

    left, top, right, bottom = money_render.CURVE_CARD
    assert all(left <= x <= right and top <= y <= bottom for x, y, _text in drawn)
    texts = [text for *_xy, text in drawn]
    assert "中国市场" in texts
    rows = [y for _x, y, text in drawn if text in ("上证", "深成", "创业板", "恒生", "恒生科技")]
    assert len(rows) == 5 and rows == sorted(rows) and len(set(rows)) == 5
    assert {"3,680.64", "+0.46%", "24,315.92", "-0.98%"} <= set(texts)
    assert any("中间价" in text for text in texts) and "6.7191" in texts
    # Each change is a solid bar in the page's rise and fall colours; the figures stay ink.
    card = set(canvas.image.crop((left, top + 28, right, bottom - 22)).convert("RGB").get_flattened_data())
    assert canvas.color("up") in card and canvas.color("down") in card
    assert set(canvas.tones) <= {"ink", "muted"}


def test_the_holdings_right_half_takes_turns_between_the_news_and_the_chatter_list(monkeypatch):
    payload = {**_core_payload(), "extras": _all_extras()}
    payload["extras"]["chatter"]["data"]["tickers"][1]["logo"] = _logo_base64((0, 150, 110, 255))
    pasted = []
    canvas, drawn = _recording_canvas(monkeypatch)
    paste = canvas.image.paste
    monkeypatch.setattr(canvas.image, "paste", lambda image, *args: pasted.append(args[0]) or paste(image, *args))
    monkeypatch.setattr(extras, "slot_index", lambda _now: 1)
    money_render._holdings(canvas, payload, NOW)
    # The news' turn: the holdings news reads on down the right half.
    assert any(text.startswith("Story") for x, _y, text in drawn if x >= 400)
    assert not any(text in ("财经快讯", "本周美国数据", "散户热议", "新股定价") for *_xy, text in drawn)

    drawn.clear()
    pasted.clear()
    monkeypatch.setattr(extras, "slot_index", lambda _now: 0)
    money_render._holdings(canvas, payload, NOW)
    right = [(x, y, text) for x, y, text in drawn if x >= 400]
    texts = [text for *_xy, text in right]
    assert "散户热议" in texts and not any(text.startswith("Story") for text in texts)
    assert ["NVDA", "AMD", "NBIS", "SPY", "MU"] == [text for text in texts if text in ("NVDA", "AMD", "NBIS", "SPY", "MU")]
    assert {"2,430", "535", "新"} <= set(texts) and all(y <= 468 for _x, y, _text in right)
    # NVDA and SPY are holdings with their own marks, AMD has the logo kept with the ranking: three
    # logos in the list's columns, each before its ticker.
    logos = [xy for xy in pasted if isinstance(xy, tuple) and xy[0] >= 400 and xy[1] >= 380]
    assert len(logos) == 3
    # The other lists stay gone.
    assert not any(text in ("财经快讯", "本周美国数据", "新股定价") for *_xy, text in drawn)


def _logo_base64(colour):
    import base64
    import io

    from PIL import Image

    out = io.BytesIO()
    art = Image.new("RGBA", (32, 32), (0, 0, 0, 0))
    art.paste(colour, (4, 4, 28, 28))
    art.save(out, format="PNG")
    return base64.b64encode(out.getvalue()).decode("ascii")


@pytest.mark.parametrize("mode", ["day", "night"])
def test_a_logo_that_would_vanish_on_the_paper_takes_the_ink_and_a_tile_keeps_its_ground(mode):
    from PIL import Image

    palette = money_render._palette({"_inkypi_theme": {"mode": mode}})
    paper, ink = tuple(palette["paper"]), tuple(palette["ink"])
    mark = Image.new("RGBA", (16, 16), (0, 0, 0, 0))
    mark.paste((*paper, 255), (4, 4, 12, 12))
    assert extra_panels._visible(mark, paper, ink).getpixel((8, 8)) == (*ink, 255)
    tile = Image.new("RGBA", (16, 16), (*paper, 255))
    assert extra_panels._visible(tile, paper, ink).getpixel((8, 8)) == (*paper, 255)


def test_each_index_on_the_china_card_is_led_by_its_exchanges_mark(monkeypatch):
    canvas, _drawn = _recording_canvas(monkeypatch)
    asked = []
    asset = money_render._asset
    monkeypatch.setattr(money_render, "_asset", lambda name, height: asked.append(name) or asset(name, height))

    extra_panels.draw_card(canvas, "china", _all_extras(), NOW)

    marks = [name for name in asked if name.startswith("logos/index-")]
    assert marks == ["logos/index-sse", "logos/index-szse", "logos/index-szse", "logos/index-hsi", "logos/index-hsi"]
    for name in set(marks):
        assert money_render._asset(name, extra_panels.INDEX_MARK_SIZE) is not None
    # Shanghai's red emblem stands before the first index's name.
    left, top, _right, _bottom = money_render.CURVE_CARD
    lead = canvas.image.crop((left + 8, top + 30, left + 9 + extra_panels.INDEX_MARK_SLOT, top + 46)).convert("RGB")
    assert any(red > 150 and green < 80 and blue < 80 for red, green, blue in lead.get_flattened_data())


@pytest.mark.parametrize("mode", ["day", "night"])
def test_the_page_draws_each_turn_of_the_yield_curve_card(mode):
    payload = {**_core_payload(), "extras": _all_extras()}
    seen = set()
    for step in range(6):
        moment = NOW + timedelta(seconds=step * extras.SLOT_SECONDS)
        image = money_render.render_page((800, 480), payload, {"_inkypi_theme": {"mode": mode}}, moment)
        assert image.size == (800, 480)
        seen.add(image.crop(money_render.CURVE_CARD).tobytes())
    # Three cards take turns.
    assert len(seen) == 3
