"""Bounded, sanitized status snapshots produced outside HTTP request handling."""
from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import logging
import math
from itertools import islice
from pathlib import Path

from plugins.plugin_registry import plugin_refreshes_data_before_display
from runtime.runtime_state import InstanceRuntimeState
from utils.atomic_file import atomic_write_json

logger = logging.getLogger(__name__)
HISTORY_NAME = "supervised-recoveries.json"
RECOVERY_REASONS = frozenset({"isolated_worker_cleanup_failed", "memory_pressure", "refresh_worker_overrun"})
SOURCE_LIMIT = 128 * 1024
ISSUE_MARKERS = (("stale_cache", "source_stale"), ("deadline", "deadline"),
                 ("resource", "resource_pressure"), ("cleanup", "worker_cleanup"))


def _stamp(value):
    try:
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return datetime.fromtimestamp(value, timezone.utc)
        result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return result.replace(tzinfo=timezone.utc) if result.tzinfo is None else result
    except (ValueError, TypeError, OverflowError, OSError):
        return None


def _safe_stamp(value):
    parsed = _stamp(value)
    return parsed.isoformat() if parsed else None


def _number(value):
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
        return number if math.isfinite(number) and number > 0 else None
    except (TypeError, ValueError, OverflowError):
        return None


def _read_small_json(path):
    try:
        with path.open("rb") as stream:
            raw = stream.read(SOURCE_LIMIT + 1)
        if len(raw) > SOURCE_LIMIT:
            return {}
        value = json.loads(raw)
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def recovery_events(data_dir):
    if data_dir is None:
        return []
    rows = _read_small_json(Path(data_dir) / HISTORY_NAME).get("events", [])
    allowed = RECOVERY_REASONS | {"unknown"}
    return [{"at": _safe_stamp(item.get("at")),
             "reason": item.get("reason") if item.get("reason") in allowed else "unknown",
             "release_id": str(item.get("release_id") or "unknown")[:128]}
            for item in rows[-32:] if isinstance(item, dict)] if isinstance(rows, list) else []


def record_supervised_recovery(device_config, reason):
    paths = getattr(device_config, "runtime_paths", None)
    data_dir = getattr(paths, "data_dir", None)
    if data_dir is None:
        return
    try:
        events = recovery_events(data_dir)
        events.append({"at": datetime.now(timezone.utc).isoformat(),
                       "reason": reason if reason in RECOVERY_REASONS else "unknown",
                       "release_id": str(getattr(paths, "release_id", "unknown"))[:128]})
        atomic_write_json(Path(data_dir) / HISTORY_NAME, {"version": 1, "events": events[-32:]})
    except Exception as error:
        logger.warning("Could not persist supervised recovery history. | error_code: %s", type(error).__name__)


class RuntimeStatusObserver:
    """Read only small known provenance records; never fetch or render content."""

    def __init__(self, *, cache_dir=None, data_dir=None):
        self.cache_dir = Path(cache_dir) if cache_dir is not None else None
        self.data_dir = data_dir
        self._files = {}
        self._snapshot = {"observed_at": None, "instances": [], "recoveries": []}

    def snapshot(self):
        return deepcopy(self._snapshot)

    def _cached_read(self, path):
        try:
            stat = path.stat()
            identity = (stat.st_mtime_ns, stat.st_size)
        except OSError:
            return {}
        previous = self._files.get(str(path))
        if previous and previous[0] == identity:
            return previous[1]
        value = _read_small_json(path)
        if len(self._files) >= 8:
            self._files.clear()
        self._files[str(path)] = (identity, value)
        return value

    def _source(self, plugin_id, now):
        if self.cache_dir is None:
            return None
        if plugin_id == "weather":
            candidates = list(islice((p for p in (self.cache_dir / "weather").glob("onecall_*.json")
                                      if p.name != "onecall_usage.json"), 2))
            if len(candidates) != 1:
                return None  # Do not attribute another location's cache.
            record = self._cached_read(candidates[0])
            observed = ((record.get("data") or {}).get("current") or {}).get("dt")
            source = {"observed_at": _safe_stamp(observed), "fetched_at": _safe_stamp(record.get("fetched_at")),
                      "state": "stale_cache" if record.get("stale") else "cached"}
        elif plugin_id == "vehicle_status":
            record = self._cached_read(self.cache_dir / "plugins/vehicle_status/cache/summary-v3.json")
            snapshot = (record.get("summary") or {}).get("snapshot") or {}
            connectivity = snapshot.get("vehicle_connectivity")
            freshness = snapshot.get("freshness")
            source = {"observed_at": _safe_stamp(snapshot.get("captured_at")),
                      "state": freshness if freshness in {"live", "fresh_cache", "stale_cache"} else "unknown",
                      "connectivity": connectivity if connectivity in {"online", "offline", "asleep"} else "unknown"}
        elif plugin_id == "steam_charts":
            record = self._cached_read(self.cache_dir / "context/steam_charts.json")
            diagnostics = (record.get("payload") or {}).get("diagnostics") or {}
            source = {"observed_at": None, "fetched_at": _safe_stamp(record.get("generated_at")),
                      "state": "cached", "media": {
                          key: value for key in ("items", "metadata_missing", "metadata_checked",
                                                 "covers_requested", "covers_available")
                          if isinstance((value := diagnostics.get(key)), int)
                          and not isinstance(value, bool) and 0 <= value <= 100
                      }}
        else:
            return None
        observed = _stamp(source.get("observed_at"))
        source["age_seconds"] = max(0, (now - observed).total_seconds()) if observed and observed <= now else None
        return source

    def observe(self, instances, states, plugin_configs, *, now, known_instances=None):
        rows = []
        counts = Counter(item.plugin_id for item in (instances if known_instances is None else known_instances))
        for item in instances[:256]:
            state = states.get(item.instance_uuid, InstanceRuntimeState())
            lane = state.data
            success, failure = _stamp(lane.last_success_at), _stamp(lane.last_failure_at)
            issue = None
            if failure is not None and (success is None or failure > success):
                error = str(lane.last_error or "").lower()
                issue = next((code for marker, code in ISSUE_MARKERS if marker in error), "provider_failure")
            retry = _stamp(lane.next_retry_at)
            if issue is None and retry is not None and retry > now:
                issue = "retry_wait"
            before_display = plugin_refreshes_data_before_display(plugin_configs.get(item.plugin_id) or {})
            interval = _number(item.refresh.get("interval"))
            try:
                source = self._source(item.plugin_id, now) if counts[item.plugin_id] == 1 else None
            except (OSError, TypeError, ValueError, AttributeError):
                source = None
            rows.append({
                "id": hashlib.sha256(item.instance_uuid.encode()).hexdigest()[:16],
                "name": str(item.name)[:100], "plugin_id": str(item.plugin_id)[:80],
                "policy": "before_display" if before_display else "interval" if interval else "scheduled",
                "interval_seconds": None if before_display else interval,
                "last_success_at": _safe_stamp(lane.last_success_at),
                "last_attempt_at": _safe_stamp(lane.last_attempt_at),
                "last_failure_at": _safe_stamp(lane.last_failure_at),
                "next_retry_at": _safe_stamp(lane.next_retry_at), "issue": issue,
                "cache_present": state.last_good_cache is not None,
                "cache_committed_at": _safe_stamp(state.last_good_cache.promoted_at) if state.last_good_cache else None,
                "source": source,
            })
        self._snapshot = {"observed_at": now.isoformat(), "instances": rows,
                          "recoveries": recovery_events(self.data_dir)}
