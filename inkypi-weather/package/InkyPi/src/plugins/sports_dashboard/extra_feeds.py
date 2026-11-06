"""One cached, bounded JSON reading per extra league (UFC, tennis, MotoGP) for the lower panel.

Each feed keeps its last good reading in the dashboard's cache folder with when it was fetched. A
reading younger than its TTL is used as it is; an older one is refreshed with one bounded attempt
(the feed's own fetch, given a single-attempt client and a deadline), and kept when that fails. A
theme-only or cache-only redraw never reaches the network. The state names follow the dashboard's
(LIVE / CACHE / STALE / NO DATA) so the page's provenance reads them like any other league's.
"""

from __future__ import annotations

import logging
import time
from datetime import timezone

from runtime.refresh_contracts import TaskCancelled
from utils.http_client import create_single_attempt_http_client

logger = logging.getLogger(__name__)
DEADLINE_SECONDS = 12


def _path(plugin, name):
    return plugin._sports_dashboard_cache_dir() / f"extra_{name}.json"


def load(plugin, settings, name, version, fetch, ttl_seconds, now, *, label):
    """(data, state) for the feed ``name``: ``fetch(client, deadline)`` returns its normalized,
    JSON-ready data."""
    now = now.astimezone(timezone.utc)
    path = _path(plugin, name)
    cache = plugin._read_json_file(path)
    cache = cache if isinstance(cache, dict) and cache.get("version") == version and isinstance(cache.get("data"), dict) else {}
    kept = cache.get("data") or {}
    fresh = bool(cache) and plugin._cache_is_fresh_seconds(cache, ttl_seconds, now)
    if fresh and not plugin._force_refresh_requested(settings):
        return kept, f"{label} CACHE"
    if plugin._provider_cache_only(settings):
        return (kept, f"{label} STALE") if kept else ({}, f"{label} NO DATA")
    try:
        with create_single_attempt_http_client() as client:
            data = fetch(client, time.monotonic() + DEADLINE_SECONDS)
    except TaskCancelled:
        raise
    except Exception as exc:  # noqa: BLE001 - a failed league only keeps its last reading.
        logger.warning("%s feed unavailable: %s", label, type(exc).__name__)
        return (kept, f"{label} STALE") if kept else ({}, f"{label} NO DATA")
    if not isinstance(data, dict) or not data:
        return (kept, f"{label} STALE") if kept else ({}, f"{label} NO DATA")
    try:
        plugin._write_json_file(path, {"version": version, "fetched_at": now.isoformat(), "data": data})
    except OSError as exc:
        logger.warning("Failed to cache the %s feed: %s", label, exc)
    return data, f"{label} LIVE"


def remaining(deadline):
    """Seconds left before ``deadline`` (time.monotonic), at least 1 for one more request."""
    left = deadline - time.monotonic()
    if left <= 1:
        raise TimeoutError("extra league feed ran out of time")
    return min(10.0, left)
