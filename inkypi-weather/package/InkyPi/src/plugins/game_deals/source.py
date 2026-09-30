"""Bounded CheapShark US/USD data, independent of the display renderer."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import json
from pathlib import Path
from urllib.parse import unquote, urlencode, urlsplit

from runtime.refresh_contracts import TaskCancelled, TaskDeadlineExceeded
from utils.atomic_file import atomic_write_json
from utils.plugin_cache import parse_datetime


API_URL = "https://www.cheapshark.com/api/1.0/deals"
USER_AGENT = "EpaperSystem-GameDeals/1.0 (personal e-paper dashboard; https://github.com/Feeengyuuu/EpaperSystem)"
REFRESH_SECONDS = 7200
MAX_STALE_SECONDS = 24 * 3600
MAX_JSON_BYTES = 128 * 1024
MAX_ITEMS = 6
SCHEMA_VERSION = 1
_ABORT = (TaskCancelled, TaskDeadlineExceeded)


def store_scope(settings):
    # This page deliberately stays on the user's US Steam scope.
    return "steam"


def redirect_url(deal_id):
    """CheapShark IDs arrive URL-encoded; encode exactly once for the redirect."""
    return "https://www.cheapshark.com/redirect?" + urlencode({"dealID": unquote(deal_id)})


def _money(value):
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as error:
        raise ValueError("invalid deal price") from error
    if not number.is_finite() or number < 0 or number > 100000:
        raise ValueError("invalid deal price")
    return number.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def safe_thumbnail_url(value):
    if not isinstance(value, str) or len(value) > 2048:
        return ""
    try:
        parts = urlsplit(value)
        host = (parts.hostname or "").lower()
        approved = any(host == item or host.endswith("." + item) for item in (
            "steamstatic.com", "steampowered.com", "cheapshark.com",
        ))
        if (parts.scheme != "https" or not approved or parts.username or parts.password
                or parts.port not in (None, 443)):
            return ""
    except ValueError:
        return ""
    return value


def normalize_deals(payload, *, scope="steam"):
    if not isinstance(payload, list) or len(payload) > 60:
        raise ValueError("CheapShark deals response must be a bounded list")
    deals, seen = [], set()
    for row in payload:
        if not isinstance(row, dict):
            raise ValueError("invalid CheapShark deal row")
        title = row.get("title")
        deal_id, game_id, store_id = (str(row.get(key) or "") for key in ("dealID", "gameID", "storeID"))
        if (not isinstance(title, str) or not title.strip() or len(title) > 300
                or not deal_id or len(deal_id) > 256
                or not game_id.isdecimal() or not store_id.isdecimal()):
            raise ValueError("invalid CheapShark deal identity")
        if scope == "steam" and store_id != "1":
            continue
        sale, normal = _money(row.get("salePrice")), _money(row.get("normalPrice"))
        if normal <= 0 or sale >= normal:
            continue
        if game_id in seen:
            continue
        seen.add(game_id)
        discount = int(((normal - sale) * 100 / normal).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
        deals.append({
            "title": " ".join(title.split()), "deal_id": deal_id,
            "game_id": game_id, "store_id": store_id,
            "store_name": "Steam" if store_id == "1" else f"商店 {store_id}",
            "sale_price": str(sale), "normal_price": str(normal),
            "discount_percent": min(discount, 100),
            "thumbnail_url": safe_thumbnail_url(row.get("thumb")),
            "redirect_url": redirect_url(deal_id),
        })
        if len(deals) == MAX_ITEMS:
            break
    return deals


@dataclass(frozen=True)
class DealSnapshot:
    deals: tuple[dict, ...]
    fetched_at: datetime | None
    state: str
    scope: str
    error: str | None = None


class DealRepository:
    def __init__(self, cache_dir, *, http):
        self.cache_dir = Path(cache_dir)
        self.http = http

    def _path(self, scope):
        return self.cache_dir / f"deals-{scope}.json"

    def _read(self, scope):
        path = self._path(scope)
        try:
            if path.is_symlink() or path.stat().st_size > MAX_JSON_BYTES:
                return {}
            envelope = json.loads(path.read_text(encoding="utf-8"))
            if (not isinstance(envelope, dict) or envelope.get("schema") != SCHEMA_VERSION
                    or envelope.get("scope") != scope):
                return {}
            deals = envelope.get("deals")
            if not isinstance(deals, list) or len(deals) > MAX_ITEMS:
                return {}
            # Validate cached data as strictly as remote data; never trust local URLs.
            rows = [{"title": item["title"], "dealID": item["deal_id"],
                     "gameID": item["game_id"], "storeID": item["store_id"],
                     "salePrice": item["sale_price"], "normalPrice": item["normal_price"],
                     "thumb": item.get("thumbnail_url", "")} for item in deals]
            envelope["deals"] = normalize_deals(rows, scope=scope)
            return envelope
        except (OSError, ValueError, TypeError, KeyError):
            return {}

    def _snapshot(self, envelope, *, now, scope, live=False):
        fetched = parse_datetime(envelope.get("fetched_at"))
        if fetched is not None:
            fetched = fetched.replace(tzinfo=timezone.utc) if fetched.tzinfo is None else fetched.astimezone(timezone.utc)
        age = (now - fetched).total_seconds() if fetched else None
        error = envelope.get("error")
        if age is None or age < -300 or age > MAX_STALE_SECONDS:
            return DealSnapshot((), fetched, "unavailable", scope, error)
        stale = age >= REFRESH_SECONDS or bool(error)
        state = "stale" if stale else ("live" if live else "fresh_cache")
        return DealSnapshot(tuple(envelope.get("deals") or ()), fetched, state, scope, error)

    def load(self, settings, *, now=None, context=None, cached_only=False, force=False):
        now = now or datetime.now(timezone.utc)
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        scope = store_scope(settings)
        envelope = self._read(scope)
        snapshot = self._snapshot(envelope, now=now, scope=scope)
        if cached_only:
            return snapshot
        retry_at = parse_datetime(envelope.get("retry_at"))
        if retry_at and retry_at > now:
            return snapshot
        if not force and snapshot.state == "fresh_cache":
            return snapshot
        if context:
            context.raise_if_cancelled()
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        params = {"pageSize": 20, "onSale": 1, "sortBy": "Deal Rating"}
        if scope == "steam":
            params["storeID"] = "1"
        try:
            result = self.http.request_json(
                "GET", API_URL, params=params, context=context, timeout=12,
                max_bytes=MAX_JSON_BYTES, headers={"User-Agent": USER_AGENT},
                allow_redirects=False,
            )
            deals = normalize_deals(result.data, scope=scope)
            if context:
                context.raise_if_cancelled()
            envelope = {"schema": SCHEMA_VERSION, "scope": scope, "currency": "USD",
                        "fetched_at": now.isoformat(), "deals": deals}
            atomic_write_json(self._path(scope), envelope)
            return self._snapshot(envelope, now=now, scope=scope, live=True)
        except _ABORT:
            raise
        except Exception as error:
            # Preserve source time and last-good offers. A failure is never an empty feed.
            status = getattr(error, "status", None)
            envelope.update({"schema": SCHEMA_VERSION, "scope": scope,
                             "deals": envelope.get("deals", []),
                             "error": "rate_limited" if status == 429 else "source_unavailable",
                             "retry_at": (now + timedelta(seconds=3600 if status == 429 else 900)).isoformat()})
            try:
                atomic_write_json(self._path(scope), envelope)
            except OSError:
                pass
            return self._snapshot(envelope, now=now, scope=scope)
