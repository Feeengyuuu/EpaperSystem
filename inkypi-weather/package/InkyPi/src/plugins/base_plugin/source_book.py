"""Per-source JSON caches for pages that combine several public providers.

Each source keeps its own freshness. A refresh re-fetches only stale sources, gives
each one a child deadline so a slow provider fails alone, and falls back to a
usable cache instead of failing the page. Cache-only redraws never touch the network.
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from plugins.base_plugin.render_provenance import SourceProvenance
from runtime.refresh_contracts import TaskCancelled, TaskContext, TaskDeadlineExceeded
from utils.cache_manager import CacheBudget, cache_namespace_for_directory


logger = logging.getLogger(__name__)

LIVE = "live"
FRESH_CACHE = "fresh_cache"
STALE_CACHE = "stale_cache"
UNAVAILABLE = "unavailable"
DISABLED = "disabled"
CLOCK_SKEW_SECONDS = 300


def _is_dict(data):
    return isinstance(data, dict)


@dataclass(frozen=True)
class Source:
    """One provider feed. ``fetch`` is None when the source is not configured (no key)."""

    name: str
    identity: Mapping[str, Any]
    fetch: Callable[[TaskContext], dict] | None
    fresh_seconds: float
    usable_seconds: float
    validate: Callable[[dict], bool] = _is_dict
    timeout_seconds: float = 12.0
    min_remaining_seconds: float = 4.0


def parse_utc(value):
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


class SourceBook:
    def __init__(self, directory, budget: CacheBudget, *, schema, max_bytes=512 * 1024,
                 checkpoint: Callable[[TaskContext], None] | None = None):
        self.directory = Path(directory)
        self.budget = budget
        self.schema = schema
        self.max_bytes = int(max_bytes)
        self.checkpoint = checkpoint or (lambda context: context.raise_if_cancelled())

    @staticmethod
    def _identity(source):
        return json.loads(json.dumps(dict(source.identity), sort_keys=True))

    def _path(self, source):
        digest = hashlib.sha256(
            json.dumps(self._identity(source), sort_keys=True).encode("utf-8")
        ).hexdigest()[:16]
        return self.directory / f"{source.name}-{digest}.json"

    def read(self, source):
        path = self._path(source)
        try:
            if path.is_symlink() or path.stat().st_size > self.max_bytes:
                return None
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return None
        if (not isinstance(payload, dict) or payload.get("schema") != self.schema
                or payload.get("identity") != self._identity(source)
                or parse_utc(payload.get("fetched_at")) is None):
            return None
        try:
            if not source.validate(payload.get("data")):
                return None
        except (TypeError, ValueError, KeyError, AttributeError):
            return None
        return payload

    def write(self, source, payload):
        encoded = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")
        if len(encoded) > self.max_bytes:
            raise ValueError(f"{source.name} cache exceeds {self.max_bytes} bytes")
        self.directory.mkdir(parents=True, exist_ok=True)
        namespace = cache_namespace_for_directory(self.directory, self.budget)
        namespace.put_bytes(self._path(source).name, encoded)

    @staticmethod
    def _age(payload, now):
        fetched = parse_utc(payload.get("fetched_at"))
        return None if fetched is None else (now - fetched).total_seconds()

    def load(self, source: Source, now: datetime, *, allow_network: bool, context: TaskContext,
             force: bool = False) -> dict:
        if source.fetch is None:
            return {"data": {}, "fetched_at": None, "state": DISABLED, "error": "not_configured"}
        cached = self.read(source)
        age = self._age(cached, now) if cached else None
        if (cached and not force and age is not None
                and -CLOCK_SKEW_SECONDS <= age < source.fresh_seconds):
            return {**cached, "state": FRESH_CACHE, "error": ""}
        error = ""
        if allow_network:
            if context.remaining_seconds() < source.min_remaining_seconds:
                error = "deadline_budget"
            else:
                child = TaskContext(
                    context.cancel_event,
                    min(context.deadline_monotonic, context.clock() + source.timeout_seconds),
                    context.clock,
                )
                try:
                    self.checkpoint(context)
                    data = source.fetch(child)
                    if not source.validate(data):
                        raise ValueError("provider data is invalid")
                    self.checkpoint(context)
                    payload = {"schema": self.schema, "identity": self._identity(source),
                               "fetched_at": now.astimezone(timezone.utc).isoformat(), "data": data}
                    self.write(source, payload)
                    return {**payload, "state": LIVE, "error": ""}
                except TaskDeadlineExceeded:
                    # Only this source ran out of its slice; the page keeps going.
                    context.raise_if_cancelled()
                    error = "source_deadline"
                except TaskCancelled:
                    raise
                except Exception as exc:  # provider faults stay local to this source
                    error = type(exc).__name__
                if error:
                    logger.warning("%s refresh failed (%s)", source.name, error)
        if cached and age is not None and -CLOCK_SKEW_SECONDS <= age <= source.usable_seconds:
            return {**cached, "state": STALE_CACHE, "error": error}
        return {"data": {}, "fetched_at": None, "state": UNAVAILABLE, "error": error or "no_cache"}


def combined_provenance(results: Iterable[Mapping[str, Any]]) -> SourceProvenance:
    """Unconfigured sources do not count; any degraded configured source marks the page."""
    states = [result.get("state") for result in results if result.get("state") != DISABLED]
    if not states or all(state == UNAVAILABLE for state in states):
        return SourceProvenance.LOCAL_FALLBACK
    if any(state in (UNAVAILABLE, STALE_CACHE) for state in states):
        return SourceProvenance.STALE_CACHE
    if any(state == LIVE for state in states):
        return SourceProvenance.LIVE
    return SourceProvenance.FRESH_CACHE
