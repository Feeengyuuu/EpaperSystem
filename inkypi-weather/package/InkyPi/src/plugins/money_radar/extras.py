"""Money Radar's rotating feeds: small keyless extras that take turns on the yield-curve card.

The card takes turns with CBOE's positioning (the VIX futures curve under the day's put/call
ratios) and China's markets (five indices and the yuan's central parity), moving on one place
every SLOT_SECONDS; a panel without its data gives its turn to the next. The central parity also
stands on the dollar/yuan card. The holdings card's right half takes turns, on the same clock,
between the holdings news and r/wallstreetbets' most mentioned tickers, each with its logo. (For a
day it also took turns with a market wire, the week's releases and IPO pricings; the owner did not
want those.)

Every feed is optional: it has its own cache and life, a refresh fetches at most one of them (the
ones on show first), and a failure only leaves the page as it was.
"""

from __future__ import annotations

import base64
import html
import io
import re
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from PIL import Image

from plugins.base_plugin.source_book import CLOCK_SKEW_SECONDS, LIVE, Source
from utils.http_client import HttpClientError, HttpStatusError, get_http_client
from utils.safe_image import ImageLimits, safe_open_image

from .sources import REQUEST_TIMEOUT, USER_AGENT, _finite, parse_time


EASTERN = ZoneInfo("America/New_York")
SHANGHAI = ZoneInfo("Asia/Shanghai")
HOUR = 3600
# What takes turns on the yield-curve card, one place on every SLOT_SECONDS.
SLOT_SECONDS = 3 * HOUR
SLOT_A = ("curve", "volatility", "china")
# And in the holdings card's right half ("news" is the holdings' own news).
SLOT_B = ("chatter", "news")
ALWAYS_READY = frozenset({"curve", "news"})
# The feeds each panel reads (the first is the one it cannot do without); the parity is also on
# the dollar/yuan card whatever the slots show.
PANEL_FEEDS = {"volatility": ("vix_curve", "put_call"), "china": ("cn_indices", "parity"), "chatter": ("chatter",)}
ALWAYS_SHOWN = ("parity",)
FEEDS = ("put_call", "vix_curve", "cn_indices", "parity", "chatter")
# How long each reading is current, and how long a kept one may still be shown.
FRESH_SECONDS = {"put_call": 6 * HOUR, "vix_curve": 6 * HOUR, "cn_indices": HOUR, "parity": 6 * HOUR,
                 "chatter": 2 * HOUR}
USABLE_SECONDS = {"put_call": 6 * 86400, "vix_curve": 6 * 86400, "cn_indices": 4 * 86400, "parity": 6 * 86400,
                  "chatter": 2 * 86400}
# A feed that is not on show is refreshed no more often than this; one that failed waits this long.
HIDDEN_FRESH_SECONDS = 3 * HOUR
RETRY_SECONDS = 2 * HOUR
STATE_NAME = "extras_state"

# CBOE's daily files carry their trading day in the address, and a day's are published after its
# close: the newest candidate is today from CBOE_PUBLISH_HOUR Eastern on, else the weekday before.
PUT_CALL_URL = "https://cdn.cboe.com/data/us/options/market_statistics/daily/{day}_daily_options"
VIX_URL = "https://www.cboe.com/us/futures/market_statistics/settlement/csv"
CBOE_PUBLISH_HOUR = 17
MISSED_DAY_SECONDS = 6 * HOUR
PUT_CALL_NAMES = {
    "TOTAL PUT/CALL RATIO": "total", "INDEX PUT/CALL RATIO": "index",
    "EXCHANGE TRADED PRODUCTS PUT/CALL RATIO": "etp", "EQUITY PUT/CALL RATIO": "equity",
    "CBOE VOLATILITY INDEX (VIX) PUT/CALL RATIO": "vix", "SPX + SPXW + SPXO PUT/CALL RATIO": "spx",
}
# The monthly VIX futures (VX/X6), not the weeklies (VX45/X6) or the minis (VXM/X6).
VIX_MONTHLY = re.compile(r"VX/[FGHJKMNQUVXZ]\d{1,2}")
VIX_MIN_POINTS, VIX_MAX_POINTS = 3, 10

# Tencent's quote line for the five indices, in the page's order and under its short names.
CN_INDICES = (("s_sh000001", "上证"), ("s_sz399001", "深成"), ("s_sz399006", "创业板"), ("s_hkHSI", "恒生"),
              ("s_hkHSTECH", "恒生科技"))
CN_INDEX_URL = "https://qt.gtimg.cn/q=" + ",".join(code for code, _name in CN_INDICES)
PARITY_URL = "https://www.chinamoney.com.cn/ags/ms/cm-u-bk-ccpr/CcprHisNew"
PARITY_DAYS = 30
# r/wallstreetbets' most mentioned tickers (ApeWisdom), the list's first TICKER_LOGOS each with its
# company's logo from Financial Modeling Prep's image host, kept small with the reading.
CHATTER_URL = "https://apewisdom.io/api/v1.0/filter/wallstreetbets/page/1"
MAX_TICKERS = 12
CHATTER_MIN = 4
TICKER_LOGO_URL = "https://images.financialmodelingprep.com/symbol/{ticker}.png"
TICKER_LOGOS = 8
TICKER_LOGO_PIXELS = 32
TICKER_LOGO_LIMITS = ImageLimits(max_bytes=256 * 1024, max_width=1024, max_height=1024, max_pixels=1024 * 1024)


def _get(url, context, *, max_bytes, params=None, headers=None, encoding=None):
    """One bounded GET: the reply's JSON, or its text when ``encoding`` is given."""
    context.raise_if_cancelled()
    client = get_http_client()
    options = {"context": context, "timeout": REQUEST_TIMEOUT, "max_bytes": max_bytes, "params": params,
               "headers": {"User-Agent": USER_AGENT, **(headers or {})}}
    if encoding:
        response = client.request_text("GET", url, encoding=encoding, errors="replace", **options)
    else:
        response = client.request_json("GET", url, **options)
    context.raise_if_cancelled()
    return response.data


def _whole(value):
    number = _finite(value)
    return int(number) if number is not None and number >= 0 and number == int(number) else None


# --- CBOE: put/call ratios and the VIX futures curve (dated daily files) ----------------------

def cboe_days(now, count=4):
    """The trading days whose CBOE files may exist, newest first (holidays are found out by asking)."""
    eastern = now.astimezone(EASTERN)
    day = eastern.date() if eastern.hour >= CBOE_PUBLISH_HOUR else eastern.date() - timedelta(days=1)
    days = []
    while len(days) < count:
        if day.weekday() < 5:
            days.append(day.isoformat())
        day -= timedelta(days=1)
    return days


def _dated(now, known, missed, read):
    """The newest CBOE day's reading with one request: the kept one (``known``) when nothing newer
    can exist yet; a day whose file was missing within MISSED_DAY_SECONDS is passed over for the
    day before, and a new miss is written into ``missed``."""
    have = str((known or {}).get("date") or "")
    for day in cboe_days(now):
        if have and day <= have:
            return known
        stamp = parse_time(missed.get(day))
        if stamp is not None and 0 <= (now - stamp).total_seconds() < MISSED_DAY_SECONDS:
            continue
        try:
            return read(day)
        except HttpStatusError as error:
            if error.status in (403, 404):
                missed[day] = now.isoformat()
            raise
        except ValueError:
            missed[day] = now.isoformat()  # the day's file is not complete yet
            raise
    raise ValueError("CBOE has published none of the recent days")


def normalize_put_call(payload, day):
    """{date, ratios: {total, index, etp, equity, vix, spx}} from a day's options statistics."""
    rows = payload.get("ratios") if isinstance(payload, dict) else None
    ratios = {}
    for row in rows if isinstance(rows, list) else []:
        key = PUT_CALL_NAMES.get(str(row.get("name") or "").strip().upper()) if isinstance(row, dict) else None
        value = _finite(row.get("value")) if key else None
        if value is not None and 0 < value < 20:
            ratios[key] = round(value, 2)
    if "total" not in ratios:
        raise ValueError("CBOE returned no total put/call ratio")
    return {"date": day, "ratios": ratios}


def fetch_put_call(now, context, known=None, missed=None):
    return _dated(now, known, {} if missed is None else missed, lambda day: normalize_put_call(
        _get(PUT_CALL_URL.format(day=day), context, max_bytes=128 * 1024, headers={"Accept": "application/json"}),
        day))


def valid_put_call(data):
    ratios = (data or {}).get("ratios")
    return isinstance(ratios, dict) and _finite(ratios.get("total")) is not None and bool((data or {}).get("date"))


def normalize_vix_curve(text, day):
    """{date, points: [{expiry, month, price}]}: the monthly VIX futures' settlements, nearest first."""
    points = []
    for line in str(text or "").splitlines()[1:400]:
        parts = [part.strip() for part in line.split(",")]
        if len(parts) != 4 or parts[0] != "VX" or not VIX_MONTHLY.fullmatch(parts[1]):
            continue
        price = _finite(parts[3])
        try:
            expiry = date.fromisoformat(parts[2])
        except ValueError:
            continue
        if price is not None and price > 0:
            points.append({"expiry": expiry.isoformat(), "month": expiry.month, "price": round(price, 4)})
    points.sort(key=lambda point: point["expiry"])
    if len(points) < VIX_MIN_POINTS:
        raise ValueError("CBOE returned too few VIX futures settlements")
    return {"date": day, "points": points[:VIX_MAX_POINTS]}


def fetch_vix_curve(now, context, known=None, missed=None):
    return _dated(now, known, {} if missed is None else missed, lambda day: normalize_vix_curve(
        _get(VIX_URL, context, max_bytes=64 * 1024, params={"dt": day}, encoding="utf-8"), day))


def valid_vix_curve(data):
    points = (data or {}).get("points")
    return isinstance(points, list) and len(points) >= VIX_MIN_POINTS and all(
        isinstance(point, dict) and _finite(point.get("price")) is not None and point.get("month") for point in points)


# --- China: five indices (Tencent's quote line) and the yuan's central parity (CFETS) ----------

def normalize_cn_indices(text):
    """{indices: [{code, name, last, change, pct}]} in the page's order; the reply's own (GBK)
    names are not read."""
    rows = dict(re.findall(r'v_(s_[A-Za-z0-9]+)="([^"]*)"', str(text or "")))
    indices = []
    for code, name in CN_INDICES:
        parts = rows.get(code, "").split("~")
        if len(parts) < 6:
            continue
        last, change, pct = (_finite(parts[index]) for index in (3, 4, 5))
        if last is None or last <= 0 or pct is None:
            continue
        indices.append({"code": code[2:], "name": name, "last": round(last, 2),
                        "change": round(change, 2) if change is not None else None, "pct": round(pct, 2)})
    if len(indices) < 3:
        raise ValueError("Tencent returned too few index quotes")
    return {"indices": indices}


def fetch_cn_indices(context):
    return normalize_cn_indices(_get(CN_INDEX_URL, context, max_bytes=16 * 1024, encoding="gbk"))


def valid_cn_indices(data):
    indices = (data or {}).get("indices")
    return isinstance(indices, list) and len(indices) >= 3 and all(
        isinstance(row, dict) and row.get("name") and _finite(row.get("last")) is not None
        and _finite(row.get("pct")) is not None for row in indices)


def normalize_parity(payload):
    """{date, series: [[day, rate]]}: the dollar/yuan central parity of the past weeks, oldest first."""
    records = payload.get("records") if isinstance(payload, dict) else None
    series = []
    for row in records if isinstance(records, list) else []:
        values = row.get("values") if isinstance(row, dict) else None
        value = _finite(values[0]) if isinstance(values, list) and values else None
        day = str(row.get("date") or "") if isinstance(row, dict) else ""
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", day) and value is not None and 1 < value < 20:
            series.append([day, round(value, 4)])
    series.sort()
    if len(series) < 2:
        raise ValueError("CFETS returned too few central parity rates")
    return {"date": series[-1][0], "series": series[-PARITY_DAYS:]}


def fetch_parity(now, context):
    # The parity is fixed at 9:15 in Beijing, which is the Pacific evening of the day before.
    end = now.astimezone(SHANGHAI).date()
    return normalize_parity(_get(PARITY_URL, context, max_bytes=64 * 1024, headers={"Accept": "application/json"}, params={
        "startDate": (end - timedelta(days=45)).isoformat(), "endDate": end.isoformat(), "currency": "USD/CNY",
        "pageNum": 1, "pageSize": PARITY_DAYS}))


def valid_parity(data):
    series = (data or {}).get("series")
    return isinstance(series, list) and len(series) >= 2 and all(
        isinstance(row, list) and len(row) == 2 and _finite(row[1]) is not None for row in series)


def parity_reading(data):
    """(today's parity, its change on the day before in pips, the series' rates), or None."""
    if not valid_parity(data):
        return None
    rates = [float(rate) for _day, rate in data["series"]]
    return rates[-1], (rates[-1] - rates[-2]) * 10000, rates


# --- r/wallstreetbets' most mentioned tickers (ApeWisdom) --------------------------------------

def normalize_chatter(payload):
    """{tickers: [{ticker, mentions, rank, rank_before, mentions_before}]} in rank order."""
    rows = payload.get("results") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        raise ValueError("ApeWisdom returned no ranking")
    tickers = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        ticker = str(row.get("ticker") or "").strip().upper()
        mentions, rank = _whole(row.get("mentions")), _whole(row.get("rank"))
        if not re.fullmatch(r"[A-Z][A-Z0-9.]{0,5}", ticker) or mentions is None or not rank:
            continue
        tickers.append({"ticker": ticker, "mentions": mentions, "rank": rank,
                        "rank_before": _whole(row.get("rank_24h_ago")) or None,
                        "mentions_before": _whole(row.get("mentions_24h_ago"))})
        if len(tickers) >= MAX_TICKERS:
            break
    if not tickers:
        raise ValueError("ApeWisdom returned no tickers")
    return {"tickers": tickers}


def _ticker_logo(ticker, context):
    """A ticker's logo from the image host, shrunk and kept as base64 PNG; "" when there is none
    or it cannot be read."""
    try:
        data = get_http_client().request_bytes(
            "GET", TICKER_LOGO_URL.format(ticker=ticker), context=context, timeout=REQUEST_TIMEOUT,
            max_bytes=TICKER_LOGO_LIMITS.max_bytes, headers={"User-Agent": USER_AGENT}).data
        logo = safe_open_image(io.BytesIO(data), limits=TICKER_LOGO_LIMITS).convert("RGBA")
    except (HttpClientError, OSError, ValueError, Image.DecompressionBombError):
        return ""
    box = logo.getchannel("A").getbbox()
    if box is None:
        return ""
    logo = logo.crop(box)
    logo.thumbnail((TICKER_LOGO_PIXELS, TICKER_LOGO_PIXELS), Image.Resampling.LANCZOS)
    out = io.BytesIO()
    logo.save(out, format="PNG", optimize=True)
    return base64.b64encode(out.getvalue()).decode("ascii")


def fetch_chatter(context, known=None):
    """The ranking, its first TICKER_LOGOS tickers with their logos: one already kept in ``known``
    (the last reading) is not asked for again, found or not."""
    chatter = normalize_chatter(_get(CHATTER_URL, context, max_bytes=128 * 1024, headers={"Accept": "application/json"}))
    kept = {row.get("ticker"): row["logo"] for row in (known or {}).get("tickers") or []
            if isinstance(row, dict) and isinstance(row.get("logo"), str)}
    for row in chatter["tickers"][:TICKER_LOGOS]:
        row["logo"] = kept[row["ticker"]] if row["ticker"] in kept else _ticker_logo(row["ticker"], context)
    return chatter


def valid_chatter(data):
    tickers = (data or {}).get("tickers")
    return isinstance(tickers, list) and bool(tickers) and all(
        isinstance(row, dict) and row.get("ticker") and _whole(row.get("rank")) for row in tickers)


def rank_move(row):
    """Places climbed since a day ago (negative: fallen); None for a ticker that was not ranked."""
    before = _whole(row.get("rank_before"))
    return None if not before else before - row["rank"]


# --- Whose turn it is --------------------------------------------------------------------------

def slot_index(now):
    return int(now.timestamp() // SLOT_SECONDS)


def slot_panel(now):
    """The panel this slot schedules on the yield-curve card."""
    return SLOT_A[slot_index(now) % len(SLOT_A)]


def feed_data(loaded, name):
    result = loaded.get(name) if isinstance(loaded, dict) else None
    data = result.get("data") if isinstance(result, dict) else None
    return data if isinstance(data, dict) else {}


def panel_ready(panel, loaded, now):
    """Whether ``panel`` has what it needs to be drawn now."""
    if panel in ALWAYS_READY:
        return True
    data = feed_data(loaded, PANEL_FEEDS[panel][0])
    if panel == "volatility":
        return valid_vix_curve(data)
    if panel == "chatter":
        return valid_chatter(data) and len(data["tickers"]) >= CHATTER_MIN
    return valid_cn_indices(data)


def shown_panel(loaded, now):
    """The panel to draw on the yield-curve card: the scheduled one, else the next in turn that is
    ready (the yield curve always is)."""
    index = slot_index(now)
    for step in range(len(SLOT_A)):
        panel = SLOT_A[(index + step) % len(SLOT_A)]
        if panel_ready(panel, loaded, now):
            return panel
    return SLOT_A[0]


def list_panel(now):
    """What this slot schedules in the holdings card's right half."""
    return SLOT_B[slot_index(now) % len(SLOT_B)]


def shown_list(loaded, now):
    """What to draw in the holdings card's right half: the scheduled list when it has its data,
    else the holdings news."""
    panel = list_panel(now)
    return panel if panel_ready(panel, loaded, now) else "news"


# --- Loading: one request a refresh ---------------------------------------------------------

def _state_source():
    return Source(STATE_NAME, {"kind": "attempts"}, lambda _context: {}, fresh_seconds=0,
                  usable_seconds=30 * 86400, validate=lambda data: isinstance(data, dict))


def _read_state(book):
    """{failed: {feed: time}, missed: {feed: {day: time}}}: when a feed last failed and which dated
    files were missing, kept beside the caches so a failing feed waits its turn."""
    kept = book.read(_state_source())
    data = kept.get("data") if isinstance(kept, dict) else None
    failed = data.get("failed") if isinstance(data, dict) else None
    missed = data.get("missed") if isinstance(data, dict) else None
    return {"failed": dict(failed) if isinstance(failed, dict) else {},
            "missed": {name: dict(days) for name, days in (missed if isinstance(missed, dict) else {}).items()
                       if isinstance(days, dict)}}


def _write_state(book, state, now):
    cutoff = now - timedelta(days=2)
    for days in state["missed"].values():
        for day in [day for day, stamp in days.items() if (parse_time(stamp) or cutoff) <= cutoff]:
            del days[day]
    source = _state_source()
    try:
        book.write(source, {"schema": book.schema, "identity": dict(source.identity),
                            "fetched_at": now.astimezone(timezone.utc).isoformat(), "data": state})
    except (OSError, ValueError):
        pass  # without the note a failing feed is only asked again sooner


def _sources(book, now, state):
    """Each feed's Source; the dated CBOE feeds get their kept reading and their missing days."""
    def source(name, fetch, validate):
        return Source(name, {"feed": name}, fetch, fresh_seconds=FRESH_SECONDS[name],
                      usable_seconds=USABLE_SECONDS[name], validate=validate, timeout_seconds=10)

    def dated(name, validate):
        spec = source(name, None, validate)
        kept = book.read(spec)
        known = kept.get("data") if isinstance(kept, dict) else None
        missed = state["missed"].setdefault(name, {})
        fetch = globals()[f"fetch_{name}"]
        return source(name, lambda context: fetch(now, context, known, missed), validate)

    return {
        "put_call": dated("put_call", valid_put_call),
        "vix_curve": dated("vix_curve", valid_vix_curve),
        "cn_indices": source("cn_indices", lambda context: fetch_cn_indices(context), valid_cn_indices),
        "parity": source("parity", lambda context: fetch_parity(now, context), valid_parity),
        "chatter": source("chatter", lambda context: fetch_chatter(context, _kept(book, source("chatter", None,
                                                                                               valid_chatter))),
                          valid_chatter),
    }


def _kept(book, spec):
    kept = book.read(spec)
    return kept.get("data") if isinstance(kept, dict) and isinstance(kept.get("data"), dict) else None


def shown_feeds(now):
    """The feeds whose panels this slot schedules, and the ones always on the page."""
    return {feed for panel in (slot_panel(now), list_panel(now)) for feed in PANEL_FEEDS.get(panel, ())} \
        | set(ALWAYS_SHOWN)


def _due(book, sources, state, now, force):
    """The one feed this refresh may fetch: a stale one that is on show before any hidden one, the
    most overdue first; None when all are current or waiting after a failure."""
    shown = shown_feeds(now)
    best = None
    for name, source in sources.items():
        failed = parse_time(state["failed"].get(name))
        if failed is not None and 0 <= (now - failed).total_seconds() < RETRY_SECONDS:
            continue
        kept = book.read(source)
        fetched = parse_time(kept.get("fetched_at")) if isinstance(kept, dict) else None
        age = (now - fetched).total_seconds() if fetched is not None else None
        life = source.fresh_seconds if name in shown else max(source.fresh_seconds, HIDDEN_FRESH_SECONDS)
        if not force and age is not None and -CLOCK_SKEW_SECONDS <= age < life:
            continue
        rank = (name in shown, float("inf") if age is None or age < 0 else age / life)
        if best is None or rank > best[0]:
            best = (rank, name)
    return best[1] if best else None


def load(book, now, *, allow_network, context, force=False):
    """Every feed's result ({data, state, error, fetched_at}) from its cache; with the network, at
    most one of them is fetched first."""
    state = _read_state(book)
    sources = _sources(book, now, state)
    due = _due(book, sources, state, now, force) if allow_network else None
    missed_before = {name: dict(days) for name, days in state["missed"].items()}
    loaded = {name: book.load(source, now, allow_network=name == due, context=context, force=name == due)
              for name, source in sources.items()}
    if due is not None:
        failed_before = dict(state["failed"])
        if loaded[due]["state"] == LIVE:
            state["failed"].pop(due, None)
        elif loaded[due]["error"] != "deadline_budget":
            # A refresh that ran out of time before the feed's turn says nothing about the feed.
            state["failed"][due] = now.isoformat()
        if state["failed"] != failed_before or state["missed"] != missed_before:
            _write_state(book, state, now)
    return loaded
