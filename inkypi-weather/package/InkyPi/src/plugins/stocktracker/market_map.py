"""The Nasdaq-100 as a heat map: its members by market value with the day's change, from Nasdaq's
keyless list (one reply of about 22 KB through the client fundamentals.py uses), kept
FRESH_SECONDS on disk and shown up to USABLE_SECONDS old. The map takes the holdings table's place
on one display turn in MAP_EVERY (the portfolio card and its trend stay): turns are SLOT_SECONDS
long, about the time the frame takes to come round to the page again.

This module holds the data, the turn and the map's geometry; the page draws it.
"""

from __future__ import annotations

import json
import logging
import math
import re
from datetime import datetime, timezone
from pathlib import Path

from runtime.refresh_contracts import TaskDeadlineExceeded
from utils.http_client import HttpClientError

from . import fundamentals


logger = logging.getLogger(__name__)
LIST_URL = "https://api.nasdaq.com/api/quote/list-type/nasdaq100"
FRESH_SECONDS = 30 * 60
USABLE_SECONDS = 4 * 86400
CACHE_FILE = "nasdaq100.json"
MIN_ROWS, MAX_ROWS = 50, 120
SLOT_SECONDS = 200 * 60
MAP_EVERY = 4
MAP_TURN = MAP_EVERY - 1
SYMBOL = re.compile(r"[A-Z][A-Z0-9.]{0,6}")


def _number(value):
    try:
        number = float(str(value).replace(",", "").replace("$", "").replace("%", "").strip())
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def members(payload) -> dict:
    """{as_of, rows: [[symbol, market value in $bn, day change %]]}, the largest first. Two share
    classes of one company (GOOGL and GOOG each carry Alphabet's whole value) split it between
    them, so the company takes its own room on the map."""
    data = payload.get("data") if isinstance(payload, dict) else None
    table = data.get("data") if isinstance(data, dict) else None
    listed = table.get("rows") if isinstance(table, dict) else None
    rows, companies = [], {}
    for row in listed if isinstance(listed, list) else []:
        if not isinstance(row, dict):
            continue
        symbol = str(row.get("symbol") or "").strip().upper()
        value = _number(row.get("marketCap"))
        if not SYMBOL.fullmatch(symbol) or not value or value <= 0:
            continue
        change = str(row.get("percentageChange") or "").strip()
        percent = 0.0 if change.upper() == "UNCH" else _number(change)
        company = str(row.get("companyName") or symbol).split(" Class ")[0].strip() or symbol
        companies[company] = companies.get(company, 0) + 1
        rows.append((symbol, value, percent, company))
    if len(rows) < MIN_ROWS:
        raise ValueError("Nasdaq returned too few Nasdaq-100 members")
    shared = [[symbol, round(value / companies[company] / 1e9, 2), None if percent is None else round(percent, 2)]
              for symbol, value, percent, company in rows]
    shared.sort(key=lambda row: -row[1])
    return {"as_of": " ".join(str(data.get("date") or "").split())[:40], "rows": shared[:MAX_ROWS]}


def valid(data) -> bool:
    rows = data.get("rows") if isinstance(data, dict) else None
    return isinstance(rows, list) and len(rows) >= MIN_ROWS and all(
        isinstance(row, list) and len(row) == 3 and isinstance(row[0], str) and isinstance(row[1], (int, float))
        and row[1] > 0 and (row[2] is None or isinstance(row[2], (int, float))) for row in rows)


def fetch(context):
    return members(fundamentals._get(LIST_URL, context))


def _read(path):
    try:
        if path.is_symlink() or path.stat().st_size > 64 * 1024:
            return None
        record = json.loads(path.read_text(encoding="utf-8"))
        fetched = datetime.fromisoformat(str(record["fetched_at"]))
    except (OSError, ValueError, TypeError, KeyError):
        return None
    return (fetched, record["data"]) if fetched.tzinfo is not None and valid(record.get("data")) else None


def load(directory, *, context, now=None, allow_network=True, fetcher=None):
    """The map's data: the cached list while it is fresh, else a new one (with the network), else
    the cached one up to USABLE_SECONDS old; None when there is none to show."""
    now = now or datetime.now(timezone.utc)
    path = Path(directory) / CACHE_FILE
    cached = _read(path)
    age = (now - cached[0]).total_seconds() if cached else None
    if cached and 0 <= age < FRESH_SECONDS:
        return cached[1]
    if allow_network:
        try:
            data = (fetcher or fetch)(context)
            if not valid(data):
                raise ValueError("the Nasdaq-100 list is not usable")
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_suffix(".tmp")
            temporary.write_text(json.dumps({"fetched_at": now.isoformat(), "data": data}, allow_nan=False),
                                 encoding="utf-8")
            temporary.replace(path)
            return data
        except TaskDeadlineExceeded:
            context.raise_if_cancelled()
        except (HttpClientError, ValueError, TypeError, KeyError, OSError) as error:
            logger.info("Nasdaq-100 list unavailable: %s", type(error).__name__)
    return cached[1] if cached and 0 <= age <= USABLE_SECONDS else None


def turn(now) -> int:
    """Which of the MAP_EVERY display turns ``now`` falls in; MAP_TURN is the map's."""
    return int(now.timestamp() // SLOT_SECONDS) % MAP_EVERY


def _worst(row, side):
    total = sum(row)
    return max(side * side * max(row) / (total * total), total * total / (side * side * min(row)))


def squarify(values, box):
    """Rectangles (left, top, right, bottom) that fill ``box``, one per value in the given
    (descending) order, each as large as its value's share and as near a square as the squarified
    treemap makes them."""
    left, top, right, bottom = (float(edge) for edge in box)
    values = [float(value) for value in values]
    total = sum(values)
    if total <= 0 or right <= left or bottom <= top:
        return []
    scale = (right - left) * (bottom - top) / total
    areas = [value * scale for value in values]
    rects, index = [], 0
    while index < len(areas):
        width, height = right - left, bottom - top
        side = min(width, height)
        row = [areas[index]]
        while index + len(row) < len(areas) and _worst(row + [areas[index + len(row)]], side) <= _worst(row, side):
            row.append(areas[index + len(row)])
        depth = sum(row) / side
        offset = 0.0
        for area in row:
            length = area / depth
            if width >= height:
                rects.append((left, top + offset, left + depth, top + offset + length))
            else:
                rects.append((left + offset, top, left + offset + length, top + depth))
            offset += length
        if width >= height:
            left += depth
        else:
            top += depth
        index += len(row)
    return rects
