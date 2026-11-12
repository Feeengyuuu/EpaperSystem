"""Each company holding's insider trading and short interest, from Nasdaq's keyless company pages
(the client fundamentals.py already uses): how many open-market buys and sells its insiders made in
the past three months, and how its short interest moved since the report before (Nasdaq-listed
stocks only; reported twice a month) with the days it would take to cover.

Each is one small reply per symbol (about 3 KB), so they are read on a slow rotation: a refresh asks
for FETCH_PER_REFRESH replies at most, those never read first and then the oldest, and keeps each
per symbol on disk (insider counts INSIDER_SECONDS, short interest SHORT_SECONDS). A reply that
failed is asked again after RETRY_SECONDS, its last reading kept meanwhile. A missing reading just
leaves its note off the page.
"""

from __future__ import annotations

import json
import logging
import math
import re
from datetime import datetime, timezone
from pathlib import Path

from runtime.refresh_contracts import TaskContext, TaskDeadlineExceeded
from utils.http_client import HttpClientError

from . import fundamentals


logger = logging.getLogger(__name__)
INSIDER_URL = "https://api.nasdaq.com/api/company/{symbol}/insider-trades"
SHORT_URL = "https://api.nasdaq.com/api/quote/{symbol}/short-interest"
INSIDER_SECONDS = 3 * 86400
SHORT_SECONDS = 7 * 86400
RETRY_SECONDS = 12 * 3600
USABLE_SECONDS = 45 * 86400
FETCH_PER_REFRESH = 4
FETCH_SECONDS = 12
FIELDS = ("insider_buys_3m", "insider_sells_3m", "short_change_percent", "short_days_to_cover", "short_date")
SYMBOL = re.compile(r"[A-Z][A-Z0-9.-]{0,9}")
INSIDER_ROWS = {"Number of Open Market Buys": "insider_buys_3m", "Number of Sells": "insider_sells_3m"}


def _number(value):
    try:
        number = float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def insider_reading(payload) -> dict:
    """{insider_buys_3m, insider_sells_3m}: the insiders' open-market buys and their sells of the
    past three months (both 0 for a fund, which has none)."""
    data = payload.get("data") if isinstance(payload, dict) else None
    table = data.get("numberOfTrades") if isinstance(data, dict) else None
    rows = table.get("rows") if isinstance(table, dict) else None
    reading = {}
    for row in rows if isinstance(rows, list) else []:
        key = INSIDER_ROWS.get(str(row.get("insiderTrade") or "").strip()) if isinstance(row, dict) else None
        count = _number(row.get("months3")) if key else None
        if count is not None and count >= 0:
            reading[key] = int(count)
    if len(reading) != len(INSIDER_ROWS):
        raise ValueError("Nasdaq returned no insider trade counts")
    return reading


def short_reading(payload) -> dict:
    """{short_change_percent, short_days_to_cover, short_date}: the newest short interest report
    against the one before; {} for a symbol Nasdaq reports none for (funds, other exchanges)."""
    if not isinstance(payload, dict):
        raise ValueError("Nasdaq returned no short interest reply")
    data = payload.get("data")
    if data is None:
        return {}
    table = data.get("shortInterestTable") if isinstance(data, dict) else None
    rows = [row for row in (table.get("rows") or []) if isinstance(row, dict)] if isinstance(table, dict) else []
    if len(rows) < 2:
        return {}
    latest, before = (_number(row.get("interest")) for row in rows[:2])
    try:
        day = datetime.strptime(str(rows[0].get("settlementDate") or ""), "%m/%d/%Y").date()
    except ValueError:
        return {}
    if not latest or not before or latest <= 0 or before <= 0:
        return {}
    reading = {"short_change_percent": round((latest / before - 1) * 100, 1), "short_date": day.isoformat()}
    days = _number(rows[0].get("daysToCover"))
    if days is not None and days >= 0:
        reading["short_days_to_cover"] = round(days, 1)
    return reading


def fetch_insider(symbol, context):
    return insider_reading(fundamentals._get(INSIDER_URL.format(symbol=symbol), context, {
        "limit": 5, "type": "ALL", "sortColumn": "lastDate", "sortOrder": "DESC"}))


def fetch_short_interest(symbol, context):
    return short_reading(fundamentals._get(SHORT_URL.format(symbol=symbol), context, {"assetClass": "stocks"}))


KINDS = (("insider", INSIDER_SECONDS, "fetch_insider"), ("short", SHORT_SECONDS, "fetch_short_interest"))


def _read(path):
    """(when it was last asked for, its reading or None, whether that try failed, when the reading
    was read), or None."""
    try:
        if path.is_symlink() or path.stat().st_size > 4096:
            return None
        record = json.loads(path.read_text(encoding="utf-8"))
        asked = datetime.fromisoformat(str(record["fetched_at"]))
        read = datetime.fromisoformat(str(record.get("read_at") or record["fetched_at"]))
    except (OSError, ValueError, TypeError, KeyError):
        return None
    data = record.get("data")
    if asked.tzinfo is None or read.tzinfo is None or not (data is None or isinstance(data, dict)):
        return None
    return asked, data, bool(record.get("failed")), read


def _write(path, now, data, *, read_at=None, failed=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps({"fetched_at": now.isoformat(), "read_at": (read_at or now).isoformat(),
                                     "data": data, "failed": failed}, allow_nan=False), encoding="utf-8")
    temporary.replace(path)


def attach(stock_data, directory, *, context: TaskContext, now=None, fetchers=None, budget=None,
           seconds=FETCH_SECONDS):
    """Give each company holding its insider counts and short interest (FIELDS) from the
    per-symbol cache in ``directory``, first asking Nasdaq for at most ``budget`` of the stale
    replies within ``seconds``: the ones never read, then the oldest."""
    now = now or datetime.now(timezone.utc)
    budget = FETCH_PER_REFRESH if budget is None else budget
    directory = Path(directory)
    window = TaskContext(context.cancel_event, min(context.deadline_monotonic, context.clock() + seconds),
                         context.clock)
    symbols = {}
    for row in stock_data:
        symbol = str(row.get("symbol") or "").strip().upper()
        if SYMBOL.fullmatch(symbol) and symbol != "CASH":
            symbols.setdefault(symbol, []).append(row)
    kept, stale = {}, []
    for symbol in symbols:
        for kind, life, fetcher in KINDS:
            path = directory / f"{re.sub(r'[^A-Z0-9.-]', '.', symbol)}.{kind}.json"
            record = _read(path)
            kept[symbol, kind] = record
            age = (now - record[0]).total_seconds() if record else None
            if age is None or not 0 <= age < (RETRY_SECONDS if record[2] else life):
                stale.append((age is not None, -(age or 0.0), len(stale), symbol, kind, path, fetcher))
    for _read_before, _age, _order, symbol, kind, path, fetcher in sorted(stale)[:max(0, budget)]:
        if window.remaining_seconds() <= 0:
            break
        fetch = (fetchers or {}).get(kind) or globals()[fetcher]
        record = kept[symbol, kind]
        try:
            data = fetch(symbol.replace(".", "-"), window)
            _write(path, now, data)
            kept[symbol, kind] = (now, data, False, now)
        except TaskDeadlineExceeded:
            # The window ran out: the refresh's own deadline or cancellation ends it here, else
            # the rest wait for the next refresh.
            context.raise_if_cancelled()
            break
        except (HttpClientError, ValueError, TypeError, KeyError, OSError) as error:
            logger.info("Nasdaq %s reading unavailable for %s: %s", kind, symbol, type(error).__name__)
            try:
                # Note the failed try (so the next refresh asks for another), keeping the last reading.
                _write(path, now, record[1] if record else None, read_at=record[3] if record else None, failed=True)
            except OSError:
                pass
    for symbol, rows in symbols.items():
        for kind, _life, _fetcher in KINDS:
            record = kept[symbol, kind]
            if not record or not record[1] or not 0 <= (now - record[3]).total_seconds() <= USABLE_SECONDS:
                continue
            for row in rows:
                row.update({key: value for key, value in record[1].items() if key in FIELDS})
    return stock_data
