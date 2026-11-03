"""Each holding's 52-week range, ex-dividend date and analyst view (coming earnings are Money
Radar's to show).

On the Yahoo (yfinance) path these come from the ``info`` the tracker already fetches. Rows from
Robinhood's MCP or Massive carry no such fields, so they are filled from Nasdaq's keyless quote
pages (two small JSON replies per symbol, about 3 KB in all), kept a day per symbol on disk. The
page refreshes once a day after the close, so one refresh fetches every stale symbol it can within
FETCH_SECONDS (FETCH_PER_REFRESH at most); those it has no time for keep their cached fields until
the next. A missing field just leaves its mark off the table.
"""

from __future__ import annotations

import json
import logging
import math
import re
from datetime import datetime, timezone
from pathlib import Path

from runtime.refresh_contracts import TaskContext, TaskDeadlineExceeded
from utils.http_client import HttpClientError, get_http_client


logger = logging.getLogger(__name__)
FIELDS = ("high_52w", "low_52w", "ex_dividend_date", "target_price", "rating", "analysts")
RATINGS = ("strong_buy", "buy", "hold", "underperform", "sell")
NASDAQ_RATINGS = {"strong buy": "strong_buy", "buy": "buy", "hold": "hold", "sell": "underperform",
                  "strong sell": "sell"}
NASDAQ_SUMMARY_URL = "https://api.nasdaq.com/api/quote/{symbol}/summary"
NASDAQ_TARGET_URL = "https://api.nasdaq.com/api/analyst/{symbol}/targetprice"
USER_AGENT = "InkyPi StockTracker/1.0 (personal e-paper display)"
REQUEST_TIMEOUT = (3, 8)
MAX_BYTES = 64 * 1024
FRESH_SECONDS = 86400
USABLE_SECONDS = 7 * 86400
# A refresh fetches up to this many stale symbols, within this many seconds of its own deadline.
FETCH_PER_REFRESH = 20
FETCH_SECONDS = 20
SYMBOL = re.compile(r"[A-Z][A-Z0-9./\-]{0,9}")


def _finite(value):
    if isinstance(value, bool):
        return None
    try:
        number = float(str(value).replace("$", "").replace(",", "").strip())
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _day_from_epoch(value, zone=timezone.utc):
    seconds = _finite(value)
    if seconds is None or seconds <= 0:
        return None
    try:
        return datetime.fromtimestamp(seconds, zone).date().isoformat()
    except (OverflowError, OSError, ValueError):
        return None


def _range(low, high):
    low, high = _finite(low), _finite(high)
    return {"low_52w": round(low, 4), "high_52w": round(high, 4)} if low and high and 0 < low < high else {}


def from_yfinance_info(info):
    """The fields from a yfinance ``Ticker.info`` dict; absent ones are left out."""
    if not isinstance(info, dict):
        return {}
    fields = _range(info.get("fiftyTwoWeekLow"), info.get("fiftyTwoWeekHigh"))
    # The ex-dividend date is a day, given as its UTC midnight.
    ex_dividend = _day_from_epoch(info.get("exDividendDate"))
    if ex_dividend:
        fields["ex_dividend_date"] = ex_dividend
    target = _finite(info.get("targetMeanPrice"))
    if target and target > 0:
        fields["target_price"] = round(target, 2)
    rating = str(info.get("recommendationKey") or "").strip().lower()
    if rating in RATINGS:
        fields["rating"] = rating
    analysts = _finite(info.get("numberOfAnalystOpinions"))
    if analysts and analysts >= 1:
        fields["analysts"] = int(analysts)
    return fields


def _summary_value(summary, key):
    entry = ((((summary or {}).get("data") or {}).get("summaryData")) or {}).get(key)
    return entry.get("value") if isinstance(entry, dict) else None


def _nasdaq_day(text):
    """"Aug 10, 2026" -> "2026-08-10"; None for "N/A" and the like."""
    try:
        return datetime.strptime(str(text or "").strip(), "%b %d, %Y").date().isoformat()
    except ValueError:
        return None


def from_nasdaq(summary, target=None):
    """The fields from Nasdaq's quote summary and analyst target replies."""
    fields = {}
    high_low = str(_summary_value(summary, "FiftTwoWeekHighLow") or "")
    if "/" in high_low:
        high, low = high_low.split("/", 1)
        fields.update(_range(low, high))
    one_year = _finite(_summary_value(summary, "OneYrTarget"))
    if one_year and one_year > 0:
        fields["target_price"] = round(one_year, 2)
    ex_dividend = _nasdaq_day(_summary_value(summary, "ExDividendDate"))
    if ex_dividend:
        fields["ex_dividend_date"] = ex_dividend
    overview = (((target or {}).get("data") or {}).get("consensusOverview")) if isinstance(target, dict) else None
    if isinstance(overview, dict):
        price = _finite(overview.get("priceTarget"))
        if price and price > 0:
            fields["target_price"] = round(price, 2)
        counts = [_finite(overview.get(key)) or 0 for key in ("buy", "hold", "sell")]
        if sum(counts) >= 1:
            fields["analysts"] = int(sum(counts))
        history = ((target.get("data") or {}).get("historicalConsensus")) or []
        latest = history[-1].get("z") if history and isinstance(history[-1], dict) else None
        consensus = str((latest or {}).get("consensus") or "").strip().lower() if isinstance(latest, dict) else ""
        if consensus in NASDAQ_RATINGS:
            fields["rating"] = NASDAQ_RATINGS[consensus]
    return fields


def _get(url, context, params=None):
    context.raise_if_cancelled()
    response = get_http_client().request_json(
        "GET", url, context=context, timeout=REQUEST_TIMEOUT, max_bytes=MAX_BYTES, params=params,
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    context.raise_if_cancelled()
    return response.data if isinstance(response.data, dict) else {}


def fetch_nasdaq(symbol, context):
    """One symbol's fields from Nasdaq: the summary as a stock, else as an ETF (which has no
    analyst target). A share class is spelt with a dot there (BRK.B, not BRK-B)."""
    symbol = re.sub(r"[-/ ]", ".", symbol)
    summary = _get(NASDAQ_SUMMARY_URL.format(symbol=symbol), context, {"assetclass": "stocks"})
    if summary.get("data"):
        return from_nasdaq(summary, _get(NASDAQ_TARGET_URL.format(symbol=symbol), context))
    return from_nasdaq(_get(NASDAQ_SUMMARY_URL.format(symbol=symbol), context, {"assetclass": "etf"}))


def _read(path):
    try:
        if path.is_symlink() or path.stat().st_size > 16 * 1024:
            return None
        payload = json.loads(path.read_text(encoding="utf-8"))
        fetched = datetime.fromisoformat(str(payload["fetched_at"]))
    except (OSError, ValueError, TypeError, KeyError):
        return None
    data = payload.get("data")
    if not isinstance(data, dict) or fetched.tzinfo is None:
        return None
    return fetched, {key: data[key] for key in FIELDS if data.get(key) is not None}


def _write(path, now, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps({"fetched_at": now.isoformat(), "data": data}, allow_nan=False), encoding="utf-8")
    temp.replace(path)


def attach(stock_data, directory, *, context: TaskContext, now=None, fetch=None, budget=FETCH_PER_REFRESH,
           seconds=FETCH_SECONDS):
    """Fill the fields of each row that has none of its own from the per-symbol cache in
    ``directory``, fetching at most ``budget`` stale or missing symbols within ``seconds``: when
    that window runs out the remaining rows keep their cached fields, while the refresh's own
    deadline or cancellation still stops it."""
    now = now or datetime.now(timezone.utc)
    fetch = fetch or fetch_nasdaq
    directory = Path(directory)
    window = TaskContext(context.cancel_event, min(context.deadline_monotonic, context.clock() + seconds),
                         context.clock)
    for row in stock_data:
        symbol = str(row.get("symbol") or "").strip().upper()
        if not SYMBOL.fullmatch(symbol) or symbol == "CASH" or any(row.get(key) is not None for key in FIELDS):
            continue
        path = directory / f"{re.sub(r'[^A-Z0-9.-]', '.', symbol)}.json"
        cached = _read(path)
        age = (now - cached[0]).total_seconds() if cached else None
        data = cached[1] if cached else None
        if (age is None or not 0 <= age < FRESH_SECONDS) and budget > 0 and window.remaining_seconds() > 0:
            budget -= 1
            try:
                data = fetch(symbol, window)
                _write(path, now, data)
            except TaskDeadlineExceeded:
                # The window ran out: the refresh's own deadline or cancellation ends it here,
                # else the rest keep what they have cached.
                context.raise_if_cancelled()
                logger.info("Nasdaq fundamentals window spent at %s; the rest wait for the next refresh", symbol)
                budget = 0
                data = cached[1] if cached and age is not None and age <= USABLE_SECONDS else None
            except (HttpClientError, ValueError, TypeError, KeyError, OSError) as error:
                logger.info("Nasdaq fundamentals unavailable for %s: %s", symbol, type(error).__name__)
                data = cached[1] if cached and age is not None and age <= USABLE_SECONDS else None
        elif data is not None and (age is None or age > USABLE_SECONDS):
            data = None
        for key, value in (data or {}).items():
            if key in FIELDS:
                row[key] = value
    return stock_data
