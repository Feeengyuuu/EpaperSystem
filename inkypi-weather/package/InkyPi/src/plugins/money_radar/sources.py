"""Bounded money feeds: FX, crypto, yields and macro, plus holdings news, earnings and SEC filings.

Keyless by default (Frankfurter, CoinGecko, Yahoo chart/RSS, BLS). FRED and Finnhub keys and an
SEC contact address each switch on one more feed; without them that feed is simply disabled.
"""

from __future__ import annotations

import calendar
import math
import re
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import feedparser

from utils.http_client import HttpClientError, get_http_client


PACIFIC = ZoneInfo("America/Los_Angeles")
USER_AGENT = "InkyPi MoneyRadar/1.0 (personal e-paper display)"
REQUEST_TIMEOUT = (3, 9)
FX_URL = "https://api.frankfurter.dev/v1/{start}..{end}"
COINGECKO_URL = "https://api.coingecko.com/api/v3/coins/markets"
YAHOO_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
YAHOO_RSS_URL = "https://feeds.finance.yahoo.com/rss/2.0/headline"
BLS_URL = "https://api.bls.gov/publicAPI/v1/timeseries/data/{series}"
FRED_URL = "https://api.stlouisfed.org/fred/series/observations"
FINNHUB_EARNINGS_URL = "https://finnhub.io/api/v1/calendar/earnings"
SEC_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SEC_SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
FEAR_GREED_URL = "https://production.dataviz.cnn.io/index/fearandgreed/graphdata"
# CNN's data host answers a script's user agent with 418; it serves a browser's.
BROWSER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"
# CNN's five bands of its 0-100 score, in Chinese.
FEAR_GREED_BANDS = ((25, "极度恐惧"), (45, "恐惧"), (55, "中性"), (75, "贪婪"), (101, "极度贪婪"))
FEAR_GREED_HISTORY = 60
FX_CURRENCIES = ("CNY", "EUR", "JPY", "HKD")
DEFAULT_COINS = ("bitcoin", "ethereum")
DEFAULT_SYMBOLS = ("AAPL", "NVDA", "TSLA", "GOOGL", "MSFT")
# Yahoo symbols: 13-week bill, 5/10/30-year Treasury yields, dollar index, gold.
MARKET_SYMBOLS = ("^IRX", "^FVX", "^TNX", "^TYX", "DX-Y.NYB", "GC=F")
CURVE_POINTS = (("^IRX", "3月"), ("^FVX", "5年"), ("^TNX", "10年"), ("^TYX", "30年"))
MAX_SYMBOLS = 12
MAX_NEWS = 12
MAX_FILINGS = 20
SPARK_POINTS = 56
FORM_NAMES = {
    "10-Q": "季报", "10-K": "年报", "8-K": "重大事件", "6-K": "外国公司报告", "20-F": "外国公司年报",
    "S-1": "上市申请", "DEF 14A": "股东委托书", "10-Q/A": "季报修订", "10-K/A": "年报修订",
    "8-K/A": "重大事件修订", "SC 13G": "大股东持股", "SC 13D": "大股东持股",
}
ITEM_NAMES = {
    "1.01": "重大协议", "1.02": "终止协议", "1.05": "网络安全事件", "2.01": "收购或出售资产",
    "2.02": "业绩公告", "2.03": "新增债务", "2.05": "重组成本", "2.06": "资产减值", "3.01": "上市地位通知",
    "3.02": "未注册股票发行", "4.01": "更换审计师", "5.02": "高管或董事变动", "5.03": "章程修订",
    "5.07": "股东大会投票", "7.01": "公平披露", "8.01": "其他重大事项",
}
EARNINGS_HOURS = {"bmo": "盘前", "amc": "盘后", "dmh": "盘中"}


def _finite(value):
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def parse_time(value):
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def local_now(now=None):
    value = now or datetime.now(PACIFIC)
    return value.replace(tzinfo=PACIFIC) if value.tzinfo is None else value.astimezone(PACIFIC)


def symbols_setting(value, fallback=DEFAULT_SYMBOLS):
    text = str(value or "").strip()
    if not text or text.lower() == "auto":
        candidates = list(fallback or DEFAULT_SYMBOLS)
    else:
        candidates = re.split(r"[,，;\s]+", text)
    symbols = []
    for item in candidates:
        symbol = str(item).strip().upper()
        if re.fullmatch(r"[A-Z][A-Z0-9.\-]{0,9}", symbol) and symbol not in symbols and symbol != "CASH":
            symbols.append(symbol)
    if not symbols:
        raise ValueError("请填写至少一个股票代码，或留空/auto 跟随股票页持仓")
    return tuple(symbols[:MAX_SYMBOLS])


def coins_setting(value):
    text = str(value or "").strip().lower()
    coins = [item for item in re.split(r"[,，;\s]+", text) if re.fullmatch(r"[a-z0-9\-]{2,40}", item)]
    return tuple(dict.fromkeys(coins))[:3] or DEFAULT_COINS


def downsample(values, count=SPARK_POINTS):
    values = [value for value in values if value is not None]
    if len(values) <= count:
        return values
    step = (len(values) - 1) / (count - 1)
    return [values[round(index * step)] for index in range(count)]


def _get(method, url, context, *, max_bytes, params=None, headers=None, as_text=False):
    context.raise_if_cancelled()
    client = get_http_client()
    merged = {"User-Agent": USER_AGENT, **(headers or {})}
    call = client.request_text if as_text else client.request_json
    response = call(method, url, context=context, timeout=REQUEST_TIMEOUT, max_bytes=max_bytes,
                    params=params, headers=merged)
    context.raise_if_cancelled()
    return response.data


# --- FX (Frankfurter, ECB reference rates) ---------------------------------------------------

def normalize_fx(payload):
    rates = payload.get("rates") if isinstance(payload, dict) else None
    if not isinstance(rates, dict) or len(rates) < 2:
        raise ValueError("Frankfurter returned no rate series")
    series = {currency: [] for currency in FX_CURRENCIES}
    for day in sorted(rates):
        row = rates[day]
        if not isinstance(row, dict) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(day)):
            continue
        for currency in FX_CURRENCIES:
            value = _finite(row.get(currency))
            if value is not None and value > 0:
                series[currency].append([day, round(value, 5)])
    if len(series["CNY"]) < 2:
        raise ValueError("Frankfurter returned too few CNY rates")
    return {"base": "USD", "series": series, "date": series["CNY"][-1][0]}


def fetch_fx(now, context):
    start = (now.date() - timedelta(days=40)).isoformat()
    url = FX_URL.format(start=start, end="")
    payload = _get("GET", url, context, max_bytes=256 * 1024,
                   params={"from": "USD", "to": ",".join(FX_CURRENCIES)})
    return normalize_fx(payload)


def valid_fx(data):
    series = (data or {}).get("series")
    return isinstance(series, dict) and isinstance(series.get("CNY"), list) and len(series["CNY"]) >= 2


# --- Crypto (CoinGecko) ----------------------------------------------------------------------

def normalize_crypto(payload, coins):
    if not isinstance(payload, list):
        raise ValueError("CoinGecko returned no market list")
    by_id = {row.get("id"): row for row in payload if isinstance(row, dict)}
    result = []
    for coin in coins:
        row = by_id.get(coin)
        if not row:
            continue
        price = _finite(row.get("current_price"))
        if price is None or price <= 0:
            continue
        spark = ((row.get("sparkline_in_7d") or {}).get("price") or [])
        result.append({
            "id": coin, "symbol": str(row.get("symbol") or coin).upper()[:8],
            "name": str(row.get("name") or coin)[:24], "price": price,
            "change_24h": _finite(row.get("price_change_percentage_24h_in_currency")
                                  or row.get("price_change_percentage_24h")),
            "change_7d": _finite(row.get("price_change_percentage_7d_in_currency")),
            "sparkline": [round(value, 6) for value in downsample([_finite(value) for value in spark])],
        })
    if not result:
        raise ValueError("CoinGecko returned none of the requested coins")
    return {"coins": result}


def fetch_crypto(coins, context):
    payload = _get("GET", COINGECKO_URL, context, max_bytes=512 * 1024, params={
        "vs_currency": "usd", "ids": ",".join(coins), "sparkline": "true",
        "price_change_percentage": "24h,7d",
    })
    return normalize_crypto(payload, coins)


def valid_crypto(data):
    coins = (data or {}).get("coins")
    return isinstance(coins, list) and bool(coins) and all(
        isinstance(coin, dict) and _finite(coin.get("price")) for coin in coins)


# --- Yields, dollar index and gold (Yahoo chart) ----------------------------------------------

def normalize_chart(payload):
    result = (((payload or {}).get("chart") or {}).get("result") or [None])[0]
    if not isinstance(result, dict):
        raise ValueError("Yahoo chart returned no result")
    closes = (((result.get("indicators") or {}).get("quote") or [{}])[0].get("close") or [])
    stamps = result.get("timestamp") or []
    points = [(int(stamp), _finite(close)) for stamp, close in zip(stamps, closes) if _finite(close) is not None]
    if len(points) < 2:
        raise ValueError("Yahoo chart returned too few closes")
    last = _finite((result.get("meta") or {}).get("regularMarketPrice")) or points[-1][1]
    cutoff = points[-1][0] - 30 * 86400
    month_ago = next((value for stamp, value in points if stamp >= cutoff), points[0][1])
    return {"last": round(last, 4), "month_ago": round(month_ago, 4),
            "series": [round(value, 4) for value in downsample([value for _stamp, value in points])]}


def fetch_markets(context):
    quotes = {}
    for symbol in MARKET_SYMBOLS:
        try:
            payload = _get("GET", YAHOO_CHART_URL.format(symbol=symbol), context, max_bytes=256 * 1024,
                           params={"range": "3mo", "interval": "1d"})
            quotes[symbol] = normalize_chart(payload)
        except (HttpClientError, ValueError, KeyError, TypeError):
            continue  # one unavailable symbol leaves the others standing
    if "^TNX" not in quotes:
        raise ValueError("Yahoo returned no 10-year yield")
    return {"quotes": quotes}


def valid_markets(data):
    quotes = (data or {}).get("quotes")
    return isinstance(quotes, dict) and isinstance(quotes.get("^TNX"), dict) and _finite(quotes["^TNX"].get("last"))


# --- CNN Fear & Greed Index -------------------------------------------------------------------

def fear_greed_band(score):
    """The band of CNN's 0-100 score: its index (0 extreme fear .. 4 extreme greed) and Chinese name."""
    for index, (upper, name) in enumerate(FEAR_GREED_BANDS):
        if score < upper:
            return index, name
    return len(FEAR_GREED_BANDS) - 1, FEAR_GREED_BANDS[-1][1]


def normalize_fear_greed(payload):
    reading = (payload or {}).get("fear_and_greed") or {}
    score = _finite(reading.get("score"))
    if score is None or not 0 <= score <= 100:
        raise ValueError("CNN returned no Fear & Greed score")
    history = [_finite(point.get("y")) for point in ((payload.get("fear_and_greed_historical") or {}).get("data") or [])
               if isinstance(point, dict)]
    return {"score": round(score, 1), "timestamp": str(reading.get("timestamp") or ""),
            "previous_close": _finite(reading.get("previous_close")), "week": _finite(reading.get("previous_1_week")),
            "month": _finite(reading.get("previous_1_month")), "year": _finite(reading.get("previous_1_year")),
            "history": [round(value, 1) for value in history if value is not None][-FEAR_GREED_HISTORY:]}


def fetch_fear_greed(context):
    return normalize_fear_greed(_get("GET", FEAR_GREED_URL, context, max_bytes=512 * 1024,
                                     headers={"User-Agent": BROWSER_AGENT, "Accept": "application/json"}))


def valid_fear_greed(data):
    return _finite((data or {}).get("score")) is not None


# --- Macro (FRED with a key, BLS otherwise) ----------------------------------------------------

def _monthly_observations(rows):
    """BLS rows -> [(YYYY-MM, value)] oldest first."""
    clean = []
    for row in rows or []:
        period = str(row.get("period") or "")
        value = _finite(row.get("value"))
        if not re.fullmatch(r"M(0[1-9]|1[0-2])", period) or value is None:
            continue
        clean.append((f"{row.get('year')}-{period[1:]}", value))
    return sorted(clean)


def _yoy(points, period):
    lookup = dict(points)
    year, month = (int(part) for part in period.split("-"))
    previous = lookup.get(f"{year - 1}-{month:02d}")
    return None if not previous or period not in lookup else round((lookup[period] / previous - 1) * 100, 1)


def _month_before(period):
    year, month = (int(part) for part in period.split("-"))
    return f"{year - 1}-12" if month == 1 else f"{year}-{month - 1:02d}"


def cpi_reading(points):
    """Year-over-year CPI for the newest month that has a year-ago reading.

    Months are matched by date, never by position: BLS published no October 2025 CPI,
    so October 2026 has nothing to compare with and the month before it is shown.
    """
    for period, _value in reversed(points):
        value = _yoy(points, period)
        if value is not None:
            return {"value": value, "previous": _yoy(points, _month_before(period)), "period": period}
    raise ValueError("CPI series has no month with a year-ago reading")


def normalize_bls(cpi_payload, unemployment_payload):
    def series(payload):
        results = ((payload or {}).get("Results") or {}).get("series") or [{}]
        return _monthly_observations(results[0].get("data"))

    cpi, unemployment = series(cpi_payload), series(unemployment_payload)
    if len(cpi) < 13 or len(unemployment) < 2:
        raise ValueError("BLS returned too few observations")
    return {
        "source": "BLS",
        "cpi": cpi_reading(cpi),
        "unemployment": {"value": unemployment[-1][1], "previous": unemployment[-2][1],
                         "period": unemployment[-1][0]},
    }


def _fred_series(series_id, api_key, context, limit):
    payload = _get("GET", FRED_URL, context, max_bytes=256 * 1024, params={
        "series_id": series_id, "api_key": api_key, "file_type": "json", "sort_order": "desc", "limit": limit,
    })
    rows = payload.get("observations") if isinstance(payload, dict) else None
    points = [(row.get("date"), _finite(row.get("value"))) for row in rows or [] if isinstance(row, dict)]
    return [(day, value) for day, value in points if day and value is not None]


def fetch_macro(fred_key, context):
    if fred_key:
        # Two years of months, so a skipped month still leaves a year-ago match.
        cpi = _fred_series("CPIAUCSL", fred_key, context, 26)
        unrate = _fred_series("UNRATE", fred_key, context, 4)
        fed = _fred_series("DFF", fred_key, context, 30)
        mortgage = _fred_series("MORTGAGE30US", fred_key, context, 5)
        if len(cpi) < 13 or len(unrate) < 2:
            raise ValueError("FRED returned too few observations")
        return {
            "source": "FRED",
            "cpi": cpi_reading(sorted((day[:7], value) for day, value in cpi)),
            "unemployment": {"value": unrate[0][1], "previous": unrate[1][1], "period": unrate[0][0][:7]},
            "fed_funds": {"value": fed[0][1], "previous": fed[-1][1], "period": fed[0][0]} if fed else None,
            "mortgage30": {"value": mortgage[0][1], "previous": mortgage[-1][1], "period": mortgage[0][0]}
            if mortgage else None,
        }
    cpi = _get("GET", BLS_URL.format(series="CUUR0000SA0"), context, max_bytes=256 * 1024)
    unemployment = _get("GET", BLS_URL.format(series="LNS14000000"), context, max_bytes=256 * 1024)
    return normalize_bls(cpi, unemployment)


def valid_macro(data):
    return isinstance(data, dict) and isinstance(data.get("cpi"), dict) and isinstance(data.get("unemployment"), dict)


# --- Holdings news (Yahoo Finance RSS) ---------------------------------------------------------

def normalize_news(text, symbol=None):
    feed = feedparser.parse(text)
    items, seen = [], set()
    for entry in feed.entries:
        title = " ".join(str(entry.get("title") or "").split())[:160]
        stamp = entry.get("published_parsed")
        if not title or title.lower() in seen or not stamp:
            continue
        seen.add(title.lower())
        published = datetime.fromtimestamp(calendar.timegm(stamp), timezone.utc)
        items.append({"title": title, "time": published.isoformat(), "symbol": symbol})
    items.sort(key=lambda item: item["time"], reverse=True)
    if not items:
        raise ValueError("Yahoo RSS returned no headlines")
    return {"items": items[:MAX_NEWS]}


def interleave_news(feeds):
    """Newest headline of each holding first, then the next round.

    A story carried by several holdings stays with the first one, and is removed from the
    others before interleaving so every holding keeps its own first-round slot.
    """
    seen, unique_feeds = set(), []
    for items in feeds:
        unique = [item for item in items if item["title"].lower() not in seen]
        seen.update(item["title"].lower() for item in unique)
        unique_feeds.append(unique)
    merged = []
    for depth in range(max((len(items) for items in unique_feeds), default=0)):
        round_items = [items[depth] for items in unique_feeds if depth < len(items)]
        merged.extend(sorted(round_items, key=lambda entry: entry["time"], reverse=True))
    return merged[:MAX_NEWS]


def fetch_news(symbols, context):
    # One feed per holding so every headline carries its ticker.
    feeds = []
    for symbol in symbols[:8]:
        try:
            text = _get("GET", YAHOO_RSS_URL, context, max_bytes=512 * 1024, as_text=True,
                        params={"s": symbol, "region": "US", "lang": "en-US"})
            feeds.append(normalize_news(text, symbol)["items"][:4])
        except (HttpClientError, ValueError):
            continue
    items = interleave_news(feeds)
    if not items:
        raise ValueError("Yahoo RSS returned no headlines for any holding")
    return {"items": items}


def valid_news(data):
    items = (data or {}).get("items")
    return isinstance(items, list) and all(isinstance(item, dict) and item.get("title") for item in items)


# --- Earnings (Finnhub, optional key) ------------------------------------------------------------

def fetch_earnings(symbols, api_key, now, context):
    start, end = now.date(), now.date() + timedelta(days=45)
    items = []
    for symbol in symbols:
        payload = _get("GET", FINNHUB_EARNINGS_URL, context, max_bytes=256 * 1024, params={
            "from": start.isoformat(), "to": end.isoformat(), "symbol": symbol, "token": api_key,
        })
        for row in (payload or {}).get("earningsCalendar") or []:
            day = str(row.get("date") or "")
            if row.get("symbol") != symbol or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day):
                continue
            items.append({"symbol": symbol, "date": day, "hour": str(row.get("hour") or "")[:3],
                          "eps_estimate": _finite(row.get("epsEstimate"))})
    items.sort(key=lambda item: item["date"])
    return {"items": items[:MAX_SYMBOLS]}


def valid_earnings(data):
    items = (data or {}).get("items")
    return isinstance(items, list) and all(isinstance(item, dict) and item.get("symbol") for item in items)


# --- SEC filings (needs a contact address in the User-Agent) ---------------------------------------

def sec_headers(contact):
    return {"User-Agent": f"InkyPi MoneyRadar {contact}", "Accept-Encoding": "gzip, deflate"}


def fetch_ciks(symbols, contact, context):
    payload = _get("GET", SEC_TICKERS_URL, context, max_bytes=2 * 1024 * 1024, headers=sec_headers(contact))
    wanted = set(symbols)
    mapping = {}
    for row in (payload or {}).values() if isinstance(payload, dict) else []:
        ticker = str(row.get("ticker") or "").upper()
        if ticker in wanted and isinstance(row.get("cik_str"), int):
            mapping[ticker] = row["cik_str"]
    return {"map": mapping}


def valid_ciks(data):
    mapping = (data or {}).get("map")
    return isinstance(mapping, dict) and all(isinstance(value, int) for value in mapping.values())


def normalize_submissions(symbol, payload, now):
    recent = (((payload or {}).get("filings") or {}).get("recent")) or {}
    forms = recent.get("form") or []
    dates = recent.get("filingDate") or []
    item_codes = recent.get("items") or [""] * len(forms)
    descriptions = recent.get("primaryDocDescription") or [""] * len(forms)
    cutoff_filings = (now.date() - timedelta(days=60)).isoformat()
    cutoff_insider = (now.date() - timedelta(days=30)).isoformat()
    filings, insider = [], 0
    for form, day, items, description in zip(forms, dates, item_codes, descriptions):
        if form == "4" and day >= cutoff_insider:
            insider += 1
        if form not in FORM_NAMES or day < cutoff_filings:
            continue
        codes = [code.strip() for code in str(items or "").split(",") if code.strip() in ITEM_NAMES]
        filings.append({"symbol": symbol, "form": form, "date": day, "items": codes[:3],
                        "description": " ".join(str(description or "").split())[:60]})
    return filings[:6], insider


def fetch_filings(symbols, ciks, contact, now, context):
    items, insider = [], {}
    for symbol in symbols:
        cik = ciks.get(symbol)
        if not cik:
            continue  # ETFs and ADRs without an SEC registrant are skipped
        payload = _get("GET", SEC_SUBMISSIONS_URL.format(cik=cik), context, max_bytes=3 * 1024 * 1024,
                       headers=sec_headers(contact))
        filings, count = normalize_submissions(symbol, payload, now)
        items.extend(filings)
        if count:
            insider[symbol] = count
    items.sort(key=lambda item: (item["date"], item["symbol"]), reverse=True)
    return {"items": items[:MAX_FILINGS], "insider": insider}


def valid_filings(data):
    items = (data or {}).get("items")
    return isinstance(items, list) and isinstance((data or {}).get("insider"), dict) and all(
        isinstance(item, dict) and item.get("form") and item.get("symbol") for item in items)


def days_until(day, now):
    try:
        target = date.fromisoformat(day)
    except (TypeError, ValueError):
        return None
    return (target - now.date()).days


def upcoming_earnings(items, now):
    """Reports from today on; a cache kept past a report date no longer lists it."""
    return [item for item in items or []
            if (remaining := days_until(item.get("date"), now)) is not None and remaining >= 0]
