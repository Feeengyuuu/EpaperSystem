"""Bounded, key-free ESPN pregame moneylines for the visible NBA matchups."""

from datetime import datetime, timedelta, timezone
import logging
import math
import time

from runtime.refresh_contracts import TaskCancelled, TaskDeadlineExceeded
from utils.http_client import create_single_attempt_http_client

logger = logging.getLogger(__name__)
SUMMARY_URL = "https://site.web.api.espn.com/apis/site/v2/sports/basketball/nba/summary"
VERSION = "nba-espn-free-odds-v1"


def _decimal(value):
    text = str(value or "").strip().upper()
    if text in {"EVEN", "EVENS"}:
        return "2.00"
    try:
        price = float(text)
    except (TypeError, ValueError):
        return ""
    if not math.isfinite(price) or abs(price) < 100:
        return ""
    return f"{1 + (price / 100 if price > 0 else 100 / abs(price)):.2f}"


def parse_summary_odds(payload, event_id):
    """Match the requested event, keeping both prices from the same bookmaker."""
    if not isinstance(payload, dict) or str((payload.get("header") or {}).get("id")) != event_id:
        return {}
    for market in payload.get("pickcenter") or []:
        if not isinstance(market, dict):
            continue
        prices = {}
        for side, target in (("away", "team_a"), ("home", "team_b")):
            block = (market.get("moneyline") or {}).get(side) or {}
            candidates = [(block.get(phase) or {}).get("odds") for phase in ("close", "open")]
            candidates.append((market.get(side + "TeamOdds") or {}).get("moneyLine"))
            prices[target] = next((price for value in candidates if (price := _decimal(value))), "")
        if all(prices.values()):
            return {**prices, "bookmaker": str((market.get("provider") or {}).get("name") or "ESPN")}
    return {}


def attach_free_odds(plugin, events, settings, now=None):
    """At most five requests and 15 seconds; never reuse expired prices on error."""
    now = now or datetime.now(timezone.utc)
    candidates = [event for event in events if str(event.get("event_id") or "").isdigit()
                  and str(event.get("state") or "").lower() in {"pre", "unstarted", "scheduled"}
                  and isinstance(event.get("start"), datetime)
                  and timedelta(0) <= event["start"] - now <= timedelta(days=7)][:5]
    if not candidates:
        return events
    path = plugin._sports_dashboard_cache_dir() / "nba_espn_odds.json"
    cache = plugin._read_json_file(path)
    offers = cache.get("offers", {}) if cache.get("version") == VERSION else {}
    offers = offers if isinstance(offers, dict) else {}
    retained, attached = {}, {}
    deadline = time.monotonic() + 15
    cache_only = plugin._bool_setting(settings, "_inkypi_ewc_cache_only", False)
    with create_single_attempt_http_client() as client:
        for event in candidates:
            event_id = str(event["event_id"])
            record = offers.get(event_id) or {}
            if not isinstance(record, dict):
                record = {}
            if not plugin._cache_is_fresh_seconds(record, 1800 if record.get("odds") else 300, now):
                remaining = deadline - time.monotonic()
                if cache_only or remaining <= 0:
                    continue
                try:
                    response = client.request_json("GET", SUMMARY_URL, params={"event":event_id},
                                                   timeout=min(5, remaining), max_bytes=2 * 1024 * 1024)
                    odds = parse_summary_odds(response.data, event_id)
                except (TaskCancelled, TaskDeadlineExceeded):
                    raise
                except Exception as exc:
                    logger.warning("NBA free odds unavailable for %s: %s", event_id, type(exc).__name__)
                    odds = {}
                record = {"fetched_at":now.isoformat(), "odds":odds}
            retained[event_id] = record
            if record.get("odds"):
                attached[event_id] = record["odds"]
    if not cache_only and retained != offers:
        try:
            plugin._write_json_file(path, {"version":VERSION, "offers":retained})
        except OSError:
            logger.warning("Failed to cache NBA free odds")
    return [{**event, "odds":attached[str(event["event_id"])]}
            if str(event.get("event_id")) in attached else event for event in events]
