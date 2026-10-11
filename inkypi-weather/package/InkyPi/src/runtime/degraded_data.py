"""Retry cadence and log transitions for display-safe but unhealthy DATA.

A stale-cache or local-fallback result keeps the screen usable, so retrying it
on the generic failure ladder only occupies the single refresh worker. Every
plugin waits for a configurable floor, never longer than its saved interval.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
import hashlib
import math
import threading


DEFAULT_DEGRADED_DATA_RETRY_SECONDS = 30 * 60
MAX_DEGRADED_DATA_RETRY_SECONDS = 24 * 60 * 60
DEGRADED_DATA_RETRY_CONFIG_KEY = "degraded_data_retry_seconds"
DEGRADED_DATA_ERROR_PREFIX = "DATA source is display-safe but unhealthy"


def _seconds(value, *, positive=False):
    if isinstance(value, bool):
        return None
    try:
        seconds = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(seconds) or seconds < 0 or (positive and seconds == 0):
        return None
    return seconds


def saved_refresh_interval_seconds(payload):
    """Return the positive numeric saved interval; scheduled cadences have none."""
    refresh = payload.get("refresh") if isinstance(payload, Mapping) else None
    return _seconds(
        refresh.get("interval") if isinstance(refresh, Mapping) else None,
        positive=True,
    )


def degraded_data_retry_config_keys(plugin_id):
    """Per-plugin override first (keeps the legacy Vehicle Status key), then the shared key."""
    return (f"{plugin_id}_degraded_retry_seconds", DEGRADED_DATA_RETRY_CONFIG_KEY)


def degraded_data_retry_floor_seconds(plugin_id, payload, get_config):
    """Return min(configured floor, saved interval) for one degraded DATA retry."""
    configured = next(
        (
            seconds
            for seconds in (
                _seconds(get_config(key, default=None))
                for key in degraded_data_retry_config_keys(plugin_id)
            )
            if seconds is not None
        ),
        float(DEFAULT_DEGRADED_DATA_RETRY_SECONDS),
    )
    floor = min(configured, float(MAX_DEGRADED_DATA_RETRY_SECONDS))
    interval = saved_refresh_interval_seconds(payload)
    return floor if interval is None else min(floor, interval)


def _instance_uuid_hash(instance_uuid):
    return hashlib.sha256(str(instance_uuid).encode("utf-8")).hexdigest()[:16]


def _lane_is_degraded(lane_state):
    """Whether persisted DATA state ends in a degraded result newer than any success."""
    error = getattr(lane_state, "last_error", None)
    if not isinstance(error, str) or not error.startswith(DEGRADED_DATA_ERROR_PREFIX):
        return False
    try:
        failed_at = datetime.fromisoformat(lane_state.last_failure_at)
        if lane_state.last_success_at is None:
            return True
        return failed_at > datetime.fromisoformat(lane_state.last_success_at)
    except (TypeError, ValueError):
        return False


class DegradedDataEpisodes:
    """Warn once when an instance enters degraded DATA and note its recovery.

    The announcement set lives in memory, so each process warns once for an
    episode that is still running after a restart; recovery also consults the
    persisted lane state for episodes that began in an earlier process.
    """

    def __init__(self, logger):
        self._logger = logger
        self._lock = threading.Lock()
        self._announced = set()

    def record_degraded(self, command, provenance, minimum_seconds, retry_seconds):
        if retry_seconds is None:
            return
        with self._lock:
            first = command.instance_uuid not in self._announced
            self._announced.add(command.instance_uuid)
        if first:
            self._logger.warning(
                "Degraded DATA started; the source is display-safe but "
                "unhealthy. | plugin_id: %s | "
                "instance_uuid_hash: %s | provenance: %s | "
                "minimum_seconds: %.1f | retry_seconds: %.1f",
                command.plugin_id,
                _instance_uuid_hash(command.instance_uuid),
                provenance,
                minimum_seconds,
                retry_seconds,
                stacklevel=2,
            )
        elif minimum_seconds > 0:
            self._logger.info(
                "Degraded DATA retry floor applied. | plugin_id: %s | "
                "minimum_seconds: %.1f | retry_seconds: %.1f",
                command.plugin_id,
                minimum_seconds,
                retry_seconds,
                stacklevel=2,
            )

    def record_success(self, command, lane_state):
        with self._lock:
            announced = command.instance_uuid in self._announced
            self._announced.discard(command.instance_uuid)
        if announced or _lane_is_degraded(lane_state):
            self._logger.info(
                "Degraded DATA recovered. | plugin_id: %s | instance_uuid_hash: %s",
                command.plugin_id,
                _instance_uuid_hash(command.instance_uuid),
                stacklevel=2,
            )
