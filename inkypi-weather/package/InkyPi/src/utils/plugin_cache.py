import json
import os
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path


def read_json(path, default=None, require_dict=False):
    target = Path(path)
    try:
        with target.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (FileNotFoundError, OSError, ValueError):
        return default
    if require_dict and not isinstance(payload, dict):
        return default
    return payload


def write_json(path, payload, *, ensure_ascii=False, indent=2, sort_keys=False, separators=None):
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(
        payload,
        ensure_ascii=ensure_ascii,
        indent=indent,
        sort_keys=sort_keys,
        separators=separators,
    )
    tmp_path = None
    try:
        fd, tmp_name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=str(target.parent))
        tmp_path = Path(tmp_name)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.replace(tmp_path, target)
        tmp_path = None
    except OSError:
        target.write_text(text, encoding="utf-8")
    finally:
        if tmp_path and tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass


def parse_datetime(value):
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def age_seconds(entry, now=None, key="fetched_at"):
    if not isinstance(entry, dict):
        return None
    parsed = parse_datetime(entry.get(key))
    if not parsed:
        return None
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return (current.astimezone(timezone.utc) - parsed).total_seconds()


def is_fresh(entry, ttl_seconds, now=None, key="fetched_at"):
    age = age_seconds(entry, now=now, key=key)
    return age is not None and age < ttl_seconds


class CachedState:
    def __init__(self, path, *, version=None, ttl_seconds=None):
        self.path = Path(path)
        self.version = version
        self.ttl_seconds = ttl_seconds

    def read(self, default=None):
        state = read_json(self.path, default=default, require_dict=isinstance(default, dict))
        if not isinstance(state, dict):
            return default
        if self.version is not None and state.get("version") != self.version:
            return default
        return state

    def write(self, payload, **kwargs):
        state = dict(payload or {})
        if self.version is not None:
            state.setdefault("version", self.version)
        write_json(self.path, state, **kwargs)

    def is_fresh(self, now=None, key="fetched_at"):
        if self.ttl_seconds is None:
            return False
        return is_fresh(self.read(default={}), self.ttl_seconds, now=now, key=key)

    def daily_calls_left(self, limit, *, date_key=None, counter_key="calls"):
        if limit is None:
            return None
        today = date_key or datetime.now(timezone.utc).date().isoformat()
        state = self.read(default={}) or {}
        if state.get("date") != today:
            return int(limit)
        try:
            used = int(state.get(counter_key) or 0)
        except (TypeError, ValueError):
            used = 0
        return max(0, int(limit) - used)

    def record_daily_call(self, *, date_key=None, counter_key="calls"):
        today = date_key or datetime.now(timezone.utc).date().isoformat()
        state = self.read(default={}) or {}
        if state.get("date") != today:
            state = {"date": today, counter_key: 0}
        try:
            state[counter_key] = int(state.get(counter_key) or 0) + 1
        except (TypeError, ValueError):
            state[counter_key] = 1
        self.write(state)
        return state[counter_key]


class MemoryTTLCache:
    def __init__(self, time_func=None):
        self._items = {}
        self._time_func = time_func or time.time

    def get_entry(self, key, *, success_ttl, failure_ttl, now=None):
        entry = self._items.get(key)
        if not entry:
            return None
        current = self._time_func() if now is None else now
        ttl = failure_ttl if entry.get("failed") else success_ttl
        if current - float(entry.get("ts") or 0) >= ttl:
            self._items.pop(key, None)
            return None
        return dict(entry)

    def set_entry(self, key, entry, *, now=None):
        payload = dict(entry or {})
        payload["ts"] = self._time_func() if now is None else now
        self._items[key] = payload
        return payload

    def clear(self):
        self._items.clear()

    def __len__(self):
        return len(self._items)