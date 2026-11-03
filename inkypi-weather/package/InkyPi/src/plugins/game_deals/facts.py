"""What each offer's row and two small CheapShark lists add: its reviews, its all-time low and
the store names.

- Reviews come with every ``/deals`` row (``metacriticScore`` and Steam's ``steamRating*``).
- ``/games?ids=`` (one request for the page's offers, about 1.2 KB each) gives each game's
  ``cheapestPriceEver`` across every store CheapShark tracks.
- ``/stores`` (about 6 KB) names the stores (``store_name``); it is kept for 30 days.

A failed lookup never fails the offer refresh: the offers simply lack those marks.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path

from runtime.refresh_contracts import TaskCancelled, TaskDeadlineExceeded
from utils.atomic_file import atomic_write_json
from utils.plugin_cache import parse_datetime


GAMES_URL = "https://www.cheapshark.com/api/1.0/games"
STORES_URL = "https://www.cheapshark.com/api/1.0/stores"
MAX_GAME_IDS = 25
MAX_GAMES_BYTES = 96 * 1024
MAX_STORES_BYTES = 64 * 1024
MAX_STORES = 100
MAX_STORE_NAME = 40
STORE_TTL = timedelta(days=30)
STORE_RETRY = timedelta(hours=6)
STORES_SCHEMA = 1
# A sale price at most this share above the all-time low counts as near it.
NEAR_LOW_SHARE = Decimal("1.10")
# Steam's review summaries, as its Simplified Chinese store words them.
STEAM_RATINGS = {
    "Overwhelmingly Positive": "好评如潮", "Very Positive": "特别好评", "Positive": "好评",
    "Mostly Positive": "多半好评", "Mixed": "褒贬不一", "Mostly Negative": "多半差评",
    "Negative": "差评", "Very Negative": "特别差评", "Overwhelmingly Negative": "差评如潮",
}


def _whole(value, high):
    try:
        number = int(str(value).strip())
    except (TypeError, ValueError):
        return 0
    return number if 0 < number <= high else 0


def _price(value):
    """A non-negative price with cents as text, or ""."""
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return ""
    if not number.is_finite() or number < 0 or number > 100000:
        return ""
    return str(number.quantize(Decimal("0.01")))


def ratings(row):
    """The review fields of a ``/deals`` row (or a cached offer), each 0/"" when absent."""
    text = str(row.get("steamRatingText") or "")
    percent = _whole(row.get("steamRatingPercent"), 100)
    count = _whole(row.get("steamRatingCount"), 10 ** 9)
    known = text in STEAM_RATINGS and percent and count
    return {
        "metacritic": _whole(row.get("metacriticScore"), 100),
        "steam_rating": text if known else "",
        "steam_rating_percent": percent if known else 0,
        "steam_rating_count": count if known else 0,
    }


def history(row):
    """The all-time-low fields of an offer (a cached one, or from ``game_lows``), validated."""
    lowest = _price(row.get("lowest_price"))
    at = row.get("lowest_at")
    return {"lowest_price": lowest, "lowest_at": at if lowest and isinstance(at, int) and at > 0 else 0}


def low_mark(deal):
    """How the sale stands to the all-time low: ("low", "") at or under it, ("near", "") within
    NEAR_LOW_SHARE of it, ("above", low price) higher, ("free", "") when it was once given away;
    ("", "") without a known low."""
    lowest, sale = _price(deal.get("lowest_price")), _price(deal.get("sale_price"))
    if not lowest or not sale:
        return "", ""
    if Decimal(lowest) <= 0:
        return "free", ""
    if Decimal(sale) <= Decimal(lowest):
        return "low", ""
    if Decimal(sale) <= Decimal(lowest) * NEAR_LOW_SHARE:
        return "near", ""
    return "above", lowest


def game_lows(http, game_ids, *, context=None, user_agent):
    """{game id: {"price", "date"}} (its all-time low) from one ``/games?ids=`` request."""
    wanted = [game_id for game_id in dict.fromkeys(map(str, game_ids)) if game_id.isdecimal()][:MAX_GAME_IDS]
    if not wanted:
        return {}
    result = http.request_json(
        "GET", GAMES_URL, params={"ids": ",".join(wanted)}, context=context, timeout=10,
        max_bytes=MAX_GAMES_BYTES, headers={"User-Agent": user_agent}, allow_redirects=False,
    )
    payload = result.data
    if not isinstance(payload, dict):
        raise ValueError("unexpected CheapShark games response")
    lows = {}
    for game_id in wanted:
        entry = payload.get(game_id)
        if not isinstance(entry, dict):
            continue
        ever = entry.get("cheapestPriceEver") if isinstance(entry.get("cheapestPriceEver"), dict) else {}
        price = _price(ever.get("price"))
        if price:
            lows[game_id] = {"price": price, "date": ever.get("date")}
    return lows


class StoreNames:
    """CheapShark's store names by id, asked for once a month (and again after a failure)."""

    def __init__(self, path, *, http, user_agent):
        self.path = Path(path)
        self.http = http
        self.user_agent = user_agent

    def _read(self):
        try:
            if self.path.is_symlink() or self.path.stat().st_size > MAX_STORES_BYTES:
                return {}
            envelope = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(envelope, dict) or envelope.get("schema") != STORES_SCHEMA:
                return {}
            return envelope
        except (OSError, ValueError):
            return {}

    @staticmethod
    def _names(stores):
        names = {}
        for key, value in (stores.items() if isinstance(stores, dict) else ()):
            name = " ".join(str(value or "").split())
            if str(key).isdecimal() and name and len(name) <= MAX_STORE_NAME:
                names[str(key)] = name
        return names

    def names(self, *, now, context=None):
        envelope = self._read()
        names = self._names(envelope.get("stores"))
        checked = _aware(parse_datetime(envelope.get("checked_at")))
        retry = _aware(parse_datetime(envelope.get("retry_at")))
        if (names and now - checked < STORE_TTL) or retry > now:
            return names
        try:
            result = self.http.request_json(
                "GET", STORES_URL, context=context, timeout=8, max_bytes=MAX_STORES_BYTES,
                headers={"User-Agent": self.user_agent}, allow_redirects=False,
            )
            rows = result.data if isinstance(result.data, list) else []
            fresh = self._names({str(row.get("storeID")): row.get("storeName")
                                 for row in rows[:MAX_STORES] if isinstance(row, dict)})
            if not fresh:
                raise ValueError("unexpected CheapShark stores response")
            envelope = {"schema": STORES_SCHEMA, "checked_at": now.isoformat(), "stores": fresh}
            names = fresh
        except (TaskCancelled, TaskDeadlineExceeded):
            raise
        except Exception:
            # Keep the names already known and do not ask again for a while.
            envelope = {"schema": STORES_SCHEMA, "checked_at": envelope.get("checked_at"),
                        "retry_at": (now + STORE_RETRY).isoformat(), "stores": names}
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_json(self.path, envelope)
        except OSError:
            pass
        return names


def _aware(value):
    if value is None:
        return datetime.min.replace(tzinfo=timezone.utc)
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value
