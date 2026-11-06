"""Each holding's latest SEC filing of the past week, from Nasdaq's keyless company filings list
(the SEC's EDGAR filings as QuoteMedia serves them; SEC's own endpoints refuse a client without a
contact e-mail): insider trades (Form 4, with the insider's name), current reports (8-K),
quarterly and annual reports (10-Q, 10-K) and proxy statements. A company's list is kept a day;
funds (VTI, SPY) have none. A failure only leaves the mark off the title row.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from runtime.refresh_contracts import TaskContext, TaskDeadlineExceeded
from utils.http_client import HttpClientError, get_http_client


logger = logging.getLogger(__name__)
FILINGS_URL = "https://api.nasdaq.com/api/company/{symbol}/sec-filings"
USER_AGENT = "Mozilla/5.0 (compatible; InkyPi StockTracker/1.0)"
FILINGS_MAX_BYTES = 256 * 1024
FILINGS_SECONDS = 86400
FETCH_SECONDS = 15
FETCH_PER_REFRESH = 15
WINDOW_DAYS = 7
# The forms the page names (an amendment as its form), and how.
FORMS = {"4": "内部人", "8-K": "8-K", "10-Q": "季报", "10-K": "年报", "DEF 14A": "股东会"}
SYMBOL = re.compile(r"[A-Z][A-Z0-9.-]{0,9}")


def recent_filings(payload, now) -> list[dict]:
    """The company's filings of the named forms in the past WINDOW_DAYS, newest first:
    [{form, label, date}] (an insider trade with ``owner``, the insider's surname)."""
    data = payload.get("data") if isinstance(payload, dict) else None
    rows = data.get("rows") if isinstance(data, dict) else None
    since = (now.astimezone(timezone.utc) - timedelta(days=WINDOW_DAYS)).date()
    found = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        form = str(row.get("formType") or "").strip().upper().removesuffix("/A")
        try:
            day = datetime.strptime(str(row.get("filed") or ""), "%m/%d/%Y").date()
        except ValueError:
            continue
        if form not in FORMS or day < since:
            continue
        filing = {"form": form, "label": FORMS[form], "date": day.isoformat()}
        owner = " ".join(str(row.get("reportingOwner") or "").split())
        if form == "4" and owner:
            filing["owner"] = owner.split(" ")[0].title()[:20]
        found.append(filing)
    return sorted(found, key=lambda filing: filing["date"], reverse=True)


def fetch_filings(symbol, context):
    response = get_http_client().request_json(
        "GET", FILINGS_URL.format(symbol=symbol), context=context, timeout=10, max_bytes=FILINGS_MAX_BYTES,
        params={"limit": 20, "sortColumn": "filed", "sortOrder": "desc"},
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    return response.data


def _read(path):
    try:
        record = json.loads(Path(path).read_text(encoding="utf-8"))
        return datetime.fromisoformat(record["fetched_at"]), record["data"]
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _write(path, now, data):
    path = Path(path)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps({"fetched_at": now.isoformat(), "data": data}), encoding="utf-8")
    temporary.replace(path)


def _kept(path, now, seconds, fetch, budget, window):
    """The cached reading at ``path`` while it is younger than ``seconds``, else a fresh one (when
    the budget and window allow), else the old one; (data, budget left)."""
    cached = _read(path)
    if cached and 0 <= (now - cached[0]).total_seconds() < seconds:
        return cached[1], budget
    if budget <= 0 or window.remaining_seconds() <= 0:
        return (cached[1] if cached else None), budget
    data = fetch()
    _write(path, now, data)
    return data, budget - 1


def attach(stock_data, directory, *, context: TaskContext, now=None, fetch=None, budget=FETCH_PER_REFRESH,
           seconds=FETCH_SECONDS):
    """Give each company holding its latest filing of the past week as ``sec_filing`` ({form,
    label, date[, owner]}); a fund, cash or a holding without one gets none."""
    now = now or datetime.now(timezone.utc)
    fetch = fetch or fetch_filings
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    window = TaskContext(context.cancel_event, min(context.deadline_monotonic, context.clock() + seconds),
                         context.clock)
    since = (now.astimezone(timezone.utc) - timedelta(days=WINDOW_DAYS)).date().isoformat()
    for row in stock_data:
        symbol = str(row.get("symbol") or "").strip().upper()
        if not SYMBOL.fullmatch(symbol) or symbol == "CASH":
            continue
        path = directory / (re.sub(r"[^A-Z0-9.-]", ".", symbol) + ".json")
        try:
            filings, budget = _kept(path, now, FILINGS_SECONDS,
                                    lambda symbol=symbol: recent_filings(fetch(symbol.replace(".", "-"), window), now),
                                    budget, window)
        except TaskDeadlineExceeded:
            context.raise_if_cancelled()
            budget = 0
            filings = (_read(path) or (None, None))[1]
        except (HttpClientError, ValueError, TypeError, KeyError, OSError) as error:
            logger.info("SEC filings unavailable for %s: %s", symbol, type(error).__name__)
            filings = (_read(path) or (None, None))[1]
        latest = [filing for filing in filings or [] if isinstance(filing, dict) and filing.get("date", "") >= since]
        if latest:
            row["sec_filing"] = latest[0]
    return stock_data
