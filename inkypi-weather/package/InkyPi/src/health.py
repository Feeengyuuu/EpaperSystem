"""Lock-free health publication and readiness evaluation."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
import logging
import math
import shutil
import threading
import time
from types import MappingProxyType
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4


logger = logging.getLogger(__name__)


_SENSITIVE_HEALTH_KEYS = {
    "api_key",
    "apikey",
    "authorization",
    "cookie",
    "password",
    "secret",
    "session",
    "token",
}


def _is_sensitive_health_key(key):
    normalized = key.lower().replace("-", "_")
    return normalized in _SENSITIVE_HEALTH_KEYS or normalized.endswith(
        ("_api_key", "_password", "_secret", "_token")
    )


def _freeze(value, *, key_hint=None):
    normalized_key = "" if key_hint is None else key_hint.lower().replace("-", "_")
    if normalized_key and _is_sensitive_health_key(normalized_key):
        return "<redacted>"
    if normalized_key.endswith("settings") and isinstance(
        value,
        Mapping,
    ):
        return tuple(sorted(str(key) for key in value))
    if (
        normalized_key in {"url", "uri"}
        or normalized_key.endswith(("_url", "_uri"))
    ) and isinstance(
        value,
        str,
    ):
        parsed = urlsplit(value)
        return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))
    if isinstance(value, Mapping):
        return MappingProxyType(
            {
                str(key): _freeze(item, key_hint=str(key))
                for key, item in value.items()
            }
        )
    if isinstance(value, (list, tuple, set, frozenset)):
        return tuple(_freeze(item) for item in value)
    return value


def health_jsonable(value):
    """Return plain JSON containers from a published immutable value."""

    if isinstance(value, Mapping):
        return {str(key): health_jsonable(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [health_jsonable(item) for item in value]
    return value


@dataclass(frozen=True)
class HealthSnapshot:
    release_id: str
    boot_id: str
    started_monotonic: float
    published_at_monotonic: float
    components: Mapping[str, object]

    def uptime_seconds(self, now_monotonic: float) -> float:
        return max(0.0, float(now_monotonic) - self.started_monotonic)


@dataclass(frozen=True)
class ReadinessResult:
    status: str
    error_codes: tuple[str, ...]


class HealthPublisher:
    """Publish detached component values and expose an unlocked snapshot read."""

    def __init__(
        self,
        *,
        release_id,
        boot_id=None,
        clock=time.monotonic,
        started_monotonic=None,
    ):
        self._clock = clock
        self._lock = threading.Lock()
        started = float(clock()) if started_monotonic is None else float(started_monotonic)
        self._snapshot = HealthSnapshot(
            release_id=str(release_id or "unknown"),
            boot_id=str(boot_id or uuid4()),
            started_monotonic=started,
            published_at_monotonic=started,
            components=MappingProxyType({}),
        )

    def snapshot(self) -> HealthSnapshot:
        """Return the current immutable reference without acquiring any lock."""

        return self._snapshot

    def now_monotonic(self) -> float:
        return float(self._clock())

    def publish_component(self, name, value) -> HealthSnapshot:
        return self.publish_components({name: value})

    def publish_components(self, components) -> HealthSnapshot:
        if not isinstance(components, Mapping):
            raise TypeError("components must be a mapping")
        detached = {}
        for name, value in components.items():
            if not isinstance(name, str) or not name.strip():
                raise ValueError("health component names must be non-empty strings")
            canonical_name = name.strip()
            detached[canonical_name] = _freeze(
                value,
                key_hint=canonical_name,
            )
        published_at = float(self._clock())
        with self._lock:
            merged = dict(self._snapshot.components)
            merged.update(detached)
            self._snapshot = replace(
                self._snapshot,
                published_at_monotonic=published_at,
                components=MappingProxyType(merged),
            )
            return self._snapshot


class ReadinessEvaluator:
    """Evaluate only immutable health data; never touch live components."""

    def __init__(
        self,
        *,
        startup_grace_seconds=120.0,
        queue_full_grace_seconds=60.0,
        active_deadline_grace_seconds=10.0,
    ):
        self.startup_grace_seconds = max(0.0, float(startup_grace_seconds))
        self.queue_full_grace_seconds = max(
            0.0,
            float(queue_full_grace_seconds),
        )
        self.active_deadline_grace_seconds = max(
            0.0,
            float(active_deadline_grace_seconds),
        )

    def evaluate(self, snapshot: HealthSnapshot, *, now_monotonic=None):
        now = (
            float(time.monotonic())
            if now_monotonic is None
            else float(now_monotonic)
        )
        components = snapshot.components
        runtime = components.get("runtime", {})
        lifecycle = components.get("lifecycle", {})
        config = components.get("config", {})
        display = components.get("display", {})
        queue = components.get("queue", {})
        scheduler = components.get("scheduler", {})
        startup = components.get("startup", {})
        disk = components.get("disk", {})
        fatal = []
        degraded = []
        uptime = snapshot.uptime_seconds(now)

        lifecycle_state = lifecycle.get("state", "starting")
        within_startup_grace = uptime < self.startup_grace_seconds
        starting = lifecycle_state == "starting" and within_startup_grace
        if lifecycle_state != "running":
            fatal.append("lifecycle_not_running")

        if not config.get("valid", False):
            fatal.append("config_invalid")
        elif not config.get("writable", False) or config.get("source") != "primary":
            degraded.append("config_degraded")

        display_state = display.get("state", "unknown")
        if display_state != "committed":
            if bool(runtime.get("dev_mode")):
                degraded.append("development_display_unavailable")
            else:
                fatal.append(
                    "display_unknown"
                    if display_state in {"unknown", "display_unknown"}
                    else "display_not_ready"
                )

        heartbeat = self._optional_float(scheduler.get("heartbeat_monotonic"))
        tick_seconds = self._positive_float(
            scheduler.get("tick_seconds"),
            default=30.0,
        )
        active_deadline = self._optional_float(
            scheduler.get("active_deadline_monotonic")
        )
        scheduler_stalled = False
        if heartbeat is None:
            if within_startup_grace:
                starting = True
                fatal.append("scheduler_starting")
            else:
                fatal.append("scheduler_not_started")
                scheduler_stalled = True
        else:
            allowed_until = heartbeat + 2.0 * tick_seconds
            if active_deadline is not None:
                allowed_until = max(
                    allowed_until,
                    active_deadline + self.active_deadline_grace_seconds,
                )
            scheduler_stalled = now > allowed_until
            if scheduler_stalled:
                fatal.append("scheduler_stalled")

        if lifecycle_state == "running" and not queue.get("accepting", False):
            fatal.append("queue_not_accepting")
        try:
            queue_full = int(queue.get("depth", 0)) >= int(queue.get("capacity", 1))
        except (TypeError, ValueError, OverflowError):
            queue_full = True
        if queue_full:
            degraded.append("queue_full")
            full_since = self._optional_float(queue.get("full_since_monotonic"))
            sustained = (
                full_since is not None
                and now - full_since >= self.queue_full_grace_seconds
            )
            if sustained and scheduler_stalled:
                fatal.append("queue_full_stalled")

        if startup.get("degraded", False):
            degraded.append("startup_degraded")

        free_bytes = self._optional_float(disk.get("free_bytes"))
        hard_min = self._optional_float(disk.get("hard_min_bytes"))
        soft_min = self._optional_float(disk.get("soft_min_bytes"))
        if free_bytes is None:
            degraded.append("disk_status_unavailable")
        elif hard_min is not None and free_bytes < hard_min:
            fatal.append("disk_hard_limit")
        elif soft_min is not None and free_bytes < soft_min:
            degraded.append("disk_low")

        error_codes = tuple(dict.fromkeys((*fatal, *degraded)))
        if fatal:
            status = "starting" if starting else "not_ready"
        elif degraded:
            status = "degraded"
        else:
            status = "ready"
        return ReadinessResult(status=status, error_codes=error_codes)

    @staticmethod
    def _positive_float(value, *, default):
        try:
            number = float(value)
        except (TypeError, ValueError, OverflowError):
            return default
        return number if math.isfinite(number) and number > 0 else default

    @staticmethod
    def _optional_float(value):
        if value is None:
            return None
        try:
            number = float(value)
        except (TypeError, ValueError, OverflowError):
            return None
        return number if math.isfinite(number) else None


class HealthCollector:
    """Sample mutable runtime components away from HTTP request threads."""

    def __init__(
        self,
        publisher,
        *,
        refresh_task,
        device_config,
        runtime_paths,
        dev_mode,
        startup_state,
        cache_manager=None,
        clock=time.monotonic,
        interval_seconds=1.0,
    ):
        self.publisher = publisher
        self.refresh_task = refresh_task
        self.device_config = device_config
        self.runtime_paths = runtime_paths
        self.dev_mode = bool(dev_mode)
        self.startup_state = startup_state
        self.cache_manager = cache_manager
        self._clock = clock
        self.interval_seconds = max(0.1, float(interval_seconds))
        self._queue_full_since = None
        self._stop_event = threading.Event()
        self._thread = None
        self._thread_lock = threading.Lock()

    def collect_once(self):
        now = float(self._clock())
        if self.cache_manager is not None:
            try:
                self.cache_manager.maintenance_if_due()
            except Exception:
                logger.exception("Managed cache maintenance failed")
        components = {
            "runtime": {"dev_mode": self.dev_mode},
            "lifecycle": self._lifecycle_component(),
            "config": self._config_component(),
            "display": self._display_component(),
            "queue": self._queue_component(now),
            "scheduler": self._scheduler_component(),
            "startup": self._startup_component(),
            "disk": self._disk_component(),
        }
        return self.publisher.publish_components(components)

    def start(self):
        with self._thread_lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop_event.clear()
            self._thread = threading.Thread(
                target=self._run,
                name="inkypi-health-collector",
                daemon=True,
            )
            self._thread.start()

    def stop(self, join_timeout=2.0):
        self._stop_event.set()
        with self._thread_lock:
            thread = self._thread
        if thread is not None:
            thread.join(timeout=max(0.0, float(join_timeout)))
        return thread is None or not thread.is_alive()

    def _run(self):
        while not self._stop_event.is_set():
            try:
                self.collect_once()
            except Exception:
                logger.exception("Health snapshot collection failed")
            if self._stop_event.wait(self.interval_seconds):
                break

    def _lifecycle_component(self):
        try:
            snapshot = self.refresh_task.lifecycle.snapshot()
            state = getattr(snapshot.state, "value", snapshot.state)
            return {
                "state": str(state),
                "changed_at_monotonic": snapshot.changed_at_monotonic,
            }
        except Exception as error:
            return {"state": "starting", "error_code": type(error).__name__}

    def _config_component(self):
        try:
            state = self.device_config._config_store.current()
            status = state.status
            return {
                "valid": bool(status.valid),
                "writable": bool(status.writable),
                "source": status.source,
                "version": status.version,
                "degraded_reason": status.degraded_reason,
            }
        except Exception as error:
            return {
                "valid": False,
                "writable": False,
                "source": "unavailable",
                "version": 0,
                "error_code": type(error).__name__,
            }

    def _display_component(self):
        try:
            snapshot = self.refresh_task.runtime_state.snapshot()
            return {
                "state": snapshot.display_state,
                "commit_id": snapshot.display_commit_id,
                "updated_at": snapshot.updated_at,
            }
        except Exception as error:
            return {
                "state": "unknown",
                "commit_id": None,
                "error_code": type(error).__name__,
            }

    def _queue_component(self, now):
        try:
            snapshot = self.refresh_task.refresh_queue.snapshot()
            full = snapshot.depth >= snapshot.capacity
            if full and self._queue_full_since is None:
                self._queue_full_since = now
            elif not full:
                self._queue_full_since = None
            return {
                "depth": snapshot.depth,
                "capacity": snapshot.capacity,
                "rejected_total": snapshot.rejected_total,
                "superseded_total": snapshot.superseded_total,
                "accepting": snapshot.accepting,
                "full_since_monotonic": self._queue_full_since,
            }
        except Exception as error:
            return {
                "depth": 1,
                "capacity": 1,
                "accepting": False,
                "full_since_monotonic": self._queue_full_since,
                "error_code": type(error).__name__,
            }

    def _scheduler_component(self):
        try:
            snapshot = self.refresh_task.scheduler_snapshot()
            active = self.refresh_task.active_operation_snapshot()
            aggregate = self.refresh_task.refresh_health_snapshot()
            due_counts = aggregate.get("due_counts", {})
            active_intent = None if active is None else active.intent
            active_intent = getattr(active_intent, "value", active_intent)
            return {
                "heartbeat_monotonic": snapshot.last_attempt_monotonic,
                "tick_seconds": self.refresh_task._scheduler_poll_seconds(),
                "active_deadline_monotonic": (
                    None if active is None else active.deadline_monotonic
                ),
                "resource_tier": str(aggregate.get("resource_tier", "unknown")),
                "due_counts": {
                    lane: max(0, int(due_counts.get(lane, 0)))
                    for lane in ("data", "live", "theme")
                },
                "oldest_data_overdue_seconds": aggregate.get(
                    "oldest_data_overdue_seconds"
                ),
                "active_intent": (
                    None if active_intent is None else str(active_intent)
                ),
            }
        except Exception as error:
            return {
                "heartbeat_monotonic": None,
                "tick_seconds": 30.0,
                "active_deadline_monotonic": None,
                "error_code": type(error).__name__,
            }

    def _startup_component(self):
        try:
            state = self.startup_state()
            degraded = bool(state.get("degraded", False))
            reasons = state.get("reasons", {})
            reason_codes = tuple(sorted(str(key) for key in reasons))
            return {"degraded": degraded, "reason_codes": reason_codes}
        except Exception as error:
            return {
                "degraded": True,
                "reason_codes": ("startup_state_unavailable",),
                "error_code": type(error).__name__,
            }

    def _disk_component(self):
        hard_mb = self._config_number("health_disk_hard_free_mb", 64.0)
        soft_mb = max(
            hard_mb,
            self._config_number("health_disk_soft_free_mb", 256.0),
        )
        try:
            usage = shutil.disk_usage(self.runtime_paths.data_dir)
            free_bytes = usage.free
            error_code = None
        except OSError as error:
            free_bytes = None
            error_code = type(error).__name__
        return {
            "free_bytes": free_bytes,
            "soft_min_bytes": int(soft_mb * 1024 * 1024),
            "hard_min_bytes": int(hard_mb * 1024 * 1024),
            "error_code": error_code,
        }

    def _config_number(self, key, default):
        try:
            value = float(self.device_config.get_config(key, default=default))
        except (TypeError, ValueError, OverflowError):
            return default
        return max(0.0, value)
