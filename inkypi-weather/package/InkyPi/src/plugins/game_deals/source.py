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

from .facts import StoreNames, game_lows, history, ratings
from .names import ChineseNameCache, chinese_title


API_URL = "https://www.cheapshark.com/api/1.0/deals"
USER_AGENT = "EpaperSystem-GameDeals/1.0 (personal e-paper dashboard; https://github.com/Feeengyuuu/EpaperSystem)"
REFRESH_SECONDS = 7200
MAX_STALE_SECONDS = 24 * 3600
MAX_JSON_BYTES = 128 * 1024
MAX_ITEMS = 10
SCHEMA_VERSION = 1
NAME_LANGUAGE = "schinese"
# Offers cached before reviews and all-time lows were kept are fetched again once.
FACTS_VERSION = 1
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


def _deal_rating(value):
    """CheapShark's deal rating as one-decimal text from "0.0" to "10.0", or ""."""
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return ""
    if not number.is_finite() or not 0 < number <= 10:
        return ""
    return str(number.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP))


def _release_date(value):
    """The release day as Unix seconds (1980 through 2100), or 0."""
    try:
        number = int(str(value).strip())
    except (TypeError, ValueError):
        return 0
    return number if 315532800 <= number <= 4102444800 else 0


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


def normalize_deals(payload, *, scope="steam", store_names=None):
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
        steam_app_id = str(row.get("steamAppID") or "")
        deals.append({
            "title": " ".join(title.split()),
            # Steam's Simplified Chinese store name, when it has one; "" keeps the English title.
            "title_zh": chinese_title(row.get("title_zh")),
            "steam_app_id": steam_app_id if steam_app_id.isdecimal() and len(steam_app_id) <= 12 else "",
            "deal_id": deal_id, "game_id": game_id, "store_id": store_id,
            "store_name": (store_names or {}).get(store_id) or ("Steam" if store_id == "1" else f"商店 {store_id}"),
            "sale_price": str(sale), "normal_price": str(normal),
            "discount_percent": min(discount, 100),
            "thumbnail_url": safe_thumbnail_url(row.get("thumb")),
            "redirect_url": redirect_url(deal_id),
            # CheapShark's own verdict on the deal (0-10) and the game's release day, as the row gives them.
            "deal_rating": _deal_rating(row.get("dealRating")),
            "release_date": _release_date(row.get("releaseDate")),
            # Metacritic and Steam's review summary from the row; the all-time low from /games
            # (cached offers carry it already).
            **ratings(row),
            **history(row),
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
                     "thumb": item.get("thumbnail_url", ""), "title_zh": item.get("title_zh", ""),
                     "steamAppID": item.get("steam_app_id", ""),
                     "metacriticScore": item.get("metacritic"), "steamRatingText": item.get("steam_rating"),
                     "steamRatingPercent": item.get("steam_rating_percent"),
                     "steamRatingCount": item.get("steam_rating_count"),
                     "dealRating": item.get("deal_rating"), "releaseDate": item.get("release_date"),
                     "lowest_price": item.get("lowest_price"), "lowest_at": item.get("lowest_at")}
                    for item in deals]
            names = {item["store_id"]: item["store_name"] for item in deals
                     if isinstance(item, dict) and isinstance(item.get("store_name"), str)}
            envelope["deals"] = normalize_deals(rows, scope=scope, store_names=names)
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

    def _add_history(self, deals, *, now, context):
        """Each offer's all-time low (across CheapShark's stores) and its store's name.

        Either lookup failing only leaves the offers without them."""
        stores = StoreNames(self.cache_dir / "stores.json", http=self.http, user_agent=USER_AGENT).names(
            now=now, context=context)
        try:
            lows = game_lows(self.http, [deal["game_id"] for deal in deals], context=context, user_agent=USER_AGENT)
        except (TaskCancelled, TaskDeadlineExceeded):
            raise
        except Exception:
            lows = {}
        for deal in deals:
            if deal["store_id"] in stores:
                deal["store_name"] = stores[deal["store_id"]]
            low = lows.get(deal["game_id"]) or {}
            deal.update(history({"lowest_price": low.get("price"), "lowest_at": low.get("date")}))

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
        # A cache written before Chinese names existed is refreshed once, not kept for two hours.
        if (not force and snapshot.state == "fresh_cache" and envelope.get("names") == NAME_LANGUAGE
                and envelope.get("facts") == FACTS_VERSION):
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
            names = ChineseNameCache(self.cache_dir / "names-schinese.json", http=self.http,
                                     user_agent=USER_AGENT).names_for(
                [deal["steam_app_id"] for deal in deals], now=now, context=context)
            for deal in deals:
                deal["title_zh"] = names.get(deal["steam_app_id"], "")
            if context:
                context.raise_if_cancelled()
            self._add_history(deals, now=now, context=context)
            if context:
                context.raise_if_cancelled()
            envelope = {"schema": SCHEMA_VERSION, "scope": scope, "currency": "USD",
                        "names": NAME_LANGUAGE, "facts": FACTS_VERSION,
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
