"""Bounded, persistent discovery of followed-team CS2 matches across events."""

from collections.abc import Mapping
from datetime import timedelta
from email.utils import parsedate_to_datetime
import hashlib
import logging
import time

from runtime.refresh_contracts import TaskContext
from utils.http_client import HttpClient, create_single_attempt_http_client
from .cs2_cards import apply_event_schedule, followed_names, has_followed_team, parse_cs2_card, utc_datetime
from .cs2_branding import enrich_event_branding


logger = logging.getLogger(__name__)
VERSION = "cs2-follow-v1"
FEEDS = ("running", "upcoming", "past")
MAX_PAGES = 3
MAX_BYTES = 2 * 1024 * 1024
DEADLINE_SECONDS = 45


class DailyBudgetExceeded(RuntimeError):
    pass


class BudgetedSession:
    def __init__(self, plugin, settings, now, session):
        self.plugin, self.settings, self.now, self.session = plugin, settings, now, session
        self.retry_after = 0

    def request(self, method, url, **kwargs):
        if self.plugin._pandascore_cs2_calls_left(self.settings, self.now) < 1:
            raise DailyBudgetExceeded("CS2 daily request budget exhausted")
        try:
            response = self.session.request(method, url, **kwargs)
            if getattr(response, "status_code", None) == 429:
                value = str((getattr(response, "headers", {}) or {}).get("Retry-After") or "")
                try:
                    self.retry_after = max(0, int(value))
                except ValueError:
                    try:
                        self.retry_after = max(0, int((parsedate_to_datetime(value) - self.now).total_seconds()))
                    except (TypeError, ValueError, OverflowError):
                        pass
            return response
        finally:
            self.plugin._record_pandascore_cs2_call(self.settings, self.now)


def _cache_key(settings, tz):
    names = "|".join(sorted(followed_names(settings)))
    return VERSION + "|" + str(tz) + "|" + hashlib.sha256(names.encode()).hexdigest()


def _fresh(snapshot, ttl, now):
    stamp = utc_datetime(snapshot.get("fetched_at"))
    return bool(stamp and timedelta(0) <= now - stamp < timedelta(seconds=ttl))


def _merge_snapshots(snapshots, ttls, now):
    by_id = {}
    state_rank = {"finished": 2, "running": 1, "not_started": 0}
    for feed in FEEDS:
        snapshot = snapshots.get(feed) or {}
        stamp = utc_datetime(snapshot.get("fetched_at"))
        if not stamp or now - stamp > timedelta(days=2):
            continue
        fresh = _fresh(snapshot, ttls[feed], now) and not snapshot.get("failure_kind")
        rows = snapshot.get("rows")
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, Mapping) or not row.get("id"):
                continue
            key = str(row["id"])
            rank = (stamp, state_rank.get(str(row.get("status")), -1))
            if key not in by_id or rank > by_id[key][0]:
                by_id[key] = (
                    rank,
                    {
                        **row,
                        "_cs2_feed": feed,
                        "_cs2_feed_fresh": fresh,
                        "_cs2_feed_failure": snapshot.get("failure_kind", ""),
                    },
                )
    return [entry[1] for entry in by_id.values()]


def _feed_params(feed, page, now):
    end = now + (timedelta(days=14) if feed == "upcoming" else timedelta(minutes=30))
    start = now - timedelta(hours=12 if feed == "upcoming" else 48)
    return {
        "page[size]": 100,
        "page[number]": page,
        "sort": "-begin_at" if feed == "past" else "begin_at",
        "range[begin_at]": start.isoformat() + "," + end.isoformat(),
    }


def _fetch_feed(client, feed, settings, api_key, now, context, *, series_id=None):
    rows = []
    path = "series/" + series_id + "/matches/" if series_id else "csgo/matches/"
    for page in range(1, MAX_PAGES + 1):
        result = client.request_json(
            "GET",
            "https://api.pandascore.co/" + path + feed,
            context=context,
            max_bytes=MAX_BYTES,
            timeout=15,
            headers={
                "Accept": "application/json",
                "Authorization": "Bearer " + api_key,
                "User-Agent": "EpaperSystem/SportsDashboard",
            },
            params=_feed_params(feed, page, now),
        )
        if not isinstance(result.data, list) or len(result.data) > 100:
            raise ValueError("CS2 match feed must be a bounded list")
        rows.extend(row for row in result.data if isinstance(row, Mapping))
        # Stop only after enough matching fixtures or the provider's last page.
        if len(result.data) < 100 or (not series_id and sum(has_followed_team(row, settings) for row in rows) >= 4):
            return rows
    if series_id:
        raise ValueError("CS2 event schedule exceeded the bounded page limit")
    return rows


def _retry_delay(kind, session):
    if kind == "AUTH":
        return 3600
    if kind == "LIMIT":
        return max(900, session.retry_after)
    return 60


def _state(card, snapshots, updated):
    failures = {str(s.get("failure_kind") or "") for s in snapshots.values()} - {""}
    blocked = "LIMIT" if "LIMIT" in failures else "AUTH" if "AUTH" in failures else ""
    if card:
        main = card["main"]
        if not main["feed_fresh"]:
            source = "PANDASCORE STALE" + (
                " " + (main["feed_failure"] or blocked)
                if (main["feed_failure"] or blocked) in {"AUTH", "LIMIT"}
                else ""
            )
        else:
            source = "PANDASCORE " + ("LIVE" if main["feed"] in updated else "CACHE")
            if failures:
                source += " PARTIAL"
        card.update(source_state=source, polling_blocked=bool(blocked), polling_failure_kind=blocked)
        return source
    return "PANDASCORE " + (blocked or ("STALE" if failures else "LIVE" if updated else "CACHE"))


def _refresh_event_schedule(plugin, card, old, settings, api_key, now, ttl, client, context, transport, force, retry):
    """Fetch the event's own schedule within the discovery request budget."""
    event_id = card["event_id"] if card else ""
    old = old if isinstance(old, Mapping) and old.get("event_id") == event_id else {}
    series_id = event_id.removeprefix("series:") if event_id.startswith("series:") else ""
    if not series_id.isdigit() or int(series_id) < 1:
        return {}, retry, False
    own_retry = utc_datetime(old.get("retry_until"))
    if (retry and now < retry) or (own_retry and now < own_retry):
        return old, retry, False
    if not force and _fresh(old, ttl, now) and not old.get("failure_kind"):
        return old, retry, False
    try:
        rows = _fetch_feed(client, "upcoming", settings, api_key, now, context, series_id=series_id)
        return {"event_id": event_id, "rows": rows, "fetched_at": now.isoformat()}, retry, True
    except Exception as exc:
        kind = "LIMIT" if isinstance(exc, DailyBudgetExceeded) else plugin._pandascore_failure_kind(exc)
        until = now + timedelta(seconds=_retry_delay(kind, transport))
        logger.warning(
            "CS2 event schedule unavailable: event=%s kind=%s retained=%s", event_id, kind, bool(old.get("rows"))
        )
        return (
            {
                **old,
                "event_id": event_id,
                "failure_kind": kind,
                "retry_until": until.isoformat(),
                "last_attempt_at": now.isoformat(),
            },
            until if kind in {"AUTH", "LIMIT"} else retry,
            True,
        )


def load_cs2_follow_card(plugin, settings, device_config, tz, now, *, session, game_logo_path):
    """Refresh separate feed snapshots and retain each feed's own provenance."""
    if getattr(session, "_inkypi_adapter_retries", False):
        # Adapter retries would bypass BudgetedSession and its per-request counter.
        # Own a no-retry session instead of mutating the shared transport.
        with create_single_attempt_http_client() as owned:
            return load_cs2_follow_card(
                plugin, settings, device_config, tz, now, session=owned.session, game_logo_path=game_logo_path
            )
    now = utc_datetime(now)
    key = _cache_key(settings, tz)
    path = plugin._sports_dashboard_cache_dir() / "pandascore_cs2_follow.json"
    cache = plugin._read_json_file(path)
    snapshots = cache.get("feeds") if cache.get("cache_key") == key else {}
    snapshots = snapshots if isinstance(snapshots, Mapping) else {}
    snapshots = {feed: dict(value) for feed, value in snapshots.items() if feed in FEEDS and isinstance(value, Mapping)}
    schedule = cache.get("event_schedule", {}) if cache.get("cache_key") == key else {}
    schedule = schedule if isinstance(schedule, Mapping) else {}
    schedule_ttl = plugin._int_setting(settings, "pandaScoreCs2ScheduleCacheSeconds", 900, 300, 3600)
    live_ttl = plugin._int_setting(settings, "pandaScoreCs2CacheSeconds", 180, 60, 900)
    ttls = dict.fromkeys(FEEDS, schedule_ttl)
    previous = parse_cs2_card(_merge_snapshots(snapshots, ttls, now), tz, now, settings, game_logo_path=game_logo_path)
    near_match = previous and (
        previous["status"] == "LIVE"
        or (previous["status"] == "NEXT" and previous["main"]["start"] <= now + timedelta(minutes=30))
    )
    if near_match:
        ttls["running"] = live_ttl
    if plugin._bool_setting(settings, "_inkypi_ewc_cache_only", False):
        card = parse_cs2_card(_merge_snapshots(snapshots, ttls, now), tz, now, settings, game_logo_path=game_logo_path)
        card = apply_event_schedule(card, schedule, tz, now, schedule_ttl)
        source = _state(card, snapshots, set())
        return enrich_event_branding(plugin, card, settings, now, session, allow_network=False), source
    api_key = plugin._pandascore_api_key(device_config)
    if not api_key:
        card = parse_cs2_card(
            _merge_snapshots(snapshots, dict.fromkeys(FEEDS, 0), now), tz, now, settings, game_logo_path=game_logo_path
        )
        if card:
            apply_event_schedule(card, schedule, tz, now, 0)
            card.update(source_state="PANDASCORE STALE NO KEY", polling_blocked=True, polling_failure_kind="AUTH")
        return card, "PANDASCORE STALE NO KEY" if card else "PANDASCORE NO KEY"
    transport = BudgetedSession(plugin, settings, now, session)
    client = HttpClient(session=transport, max_attempts=1)
    context = TaskContext.never_cancelled(deadline_monotonic=time.monotonic() + DEADLINE_SECONDS)
    updated = set()
    changed = False
    force_schedule = False
    force = plugin._force_refresh_requested(settings)
    global_retry = utc_datetime(cache.get("retry_until")) if cache.get("cache_key") == key else None
    for feed in FEEDS:
        old = snapshots.get(feed, {})
        retry = utc_datetime(old.get("retry_until"))
        if (global_retry and now < global_retry) or (retry and now < retry):
            continue
        if (
            not force
            and not (force_schedule and feed != "running")
            and _fresh(old, ttls[feed], now)
            and not old.get("failure_kind")
        ):
            continue
        changed = True
        try:
            rows = _fetch_feed(client, feed, settings, api_key, now, context)
            snapshots[feed] = {"rows": rows, "fetched_at": now.isoformat(), "last_attempt_at": now.isoformat()}
            updated.add(feed)
            if feed == "running":
                former = {str(row.get("id")) for row in old.get("rows", []) if isinstance(row, Mapping)}
                force_schedule = bool(former - {str(row.get("id")) for row in rows})
        except Exception as exc:
            kind = "LIMIT" if isinstance(exc, DailyBudgetExceeded) else plugin._pandascore_failure_kind(exc)
            delay = _retry_delay(kind, transport)
            retry = now + timedelta(seconds=delay)
            snapshots[feed] = {
                **old,
                "last_attempt_at": now.isoformat(),
                "failure_kind": kind,
                "retry_until": retry.isoformat(),
            }
            logger.warning(
                "CS2 discovery feed unavailable: feed=%s kind=%s retained=%s", feed, kind, bool(old.get("rows"))
            )
            if kind in {"AUTH", "LIMIT"}:
                global_retry = retry
                break
    card = parse_cs2_card(_merge_snapshots(snapshots, ttls, now), tz, now, settings, game_logo_path=game_logo_path)
    schedule, global_retry, schedule_changed = _refresh_event_schedule(
        plugin,
        card,
        schedule,
        settings,
        api_key,
        now,
        schedule_ttl,
        client,
        context,
        transport,
        force or force_schedule,
        global_retry,
    )
    changed = changed or schedule_changed
    card = apply_event_schedule(card, schedule, tz, now, schedule_ttl)
    source = _state(card, {**snapshots, "event_schedule": schedule}, updated)
    card = enrich_event_branding(plugin, card, settings, now, session)
    signature = [card["event_id"], card["main"]["match_id"], card["status"], card["main"]["feed_fresh"]] if card else []
    if signature != cache.get("selected_signature", []):
        changed = True
        if card:
            logger.info(
                "CS2 auto-follow selected. | event: %s | match: %s | teams: %s vs %s | status: %s | source: %s | logo: %s",
                " ".join(card["event_name"].split()),
                card["main"]["match_id"],
                " ".join(card["main"]["team_a"].split()),
                " ".join(card["main"]["team_b"].split()),
                card["status"],
                source,
                card.get("event_logo_source", "provider"),
            )
        else:
            logger.info("CS2 auto-follow cleared: no eligible match in the discovery window")
    if changed:
        try:
            plugin._write_json_file(
                path,
                {
                    "version": VERSION,
                    "cache_key": key,
                    "feeds": snapshots,
                    "event_schedule": schedule,
                    "selected_signature": signature,
                    "retry_until": global_retry.isoformat() if global_retry and now < global_retry else None,
                },
            )
        except OSError as exc:
            logger.warning("CS2 discovery cache write failed: %s", type(exc).__name__)
    return card, source
