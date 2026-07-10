"""Bounded runtime refresh state, kept separate from user configuration."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
import json
import logging
from pathlib import Path
import threading
import time
from types import MappingProxyType
from typing import Iterable, Mapping

try:
    from ..utils.atomic_file import atomic_write_json
except ImportError:  # pragma: no cover - top-level runtime import in production
    from utils.atomic_file import atomic_write_json


logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1
PERSISTENCE_INTERVAL_SECONDS = 5.0
MAX_TOMBSTONES = 64
_UNSET = object()


@dataclass(frozen=True)
class InstanceRuntimeState:
    """Immutable attempt/success/failure state for one stable instance UUID."""

    last_attempt_at: str | None = None
    last_success_at: str | None = None
    last_failure_at: str | None = None
    last_error: str | None = None
    next_retry_at: str | None = None
    tombstoned_at: str | None = None

    def latest_activity_at(self) -> str:
        return max(
            (
                value
                for value in (
                    self.last_attempt_at,
                    self.last_success_at,
                    self.last_failure_at,
                )
                if value is not None
            ),
            default="",
        )


@dataclass(frozen=True)
class RuntimeStateSnapshot:
    """A detached, immutable view suitable for health and scheduler reads."""

    schema_version: int
    instances: Mapping[str, InstanceRuntimeState]
    display_state: str
    display_commit_id: str | None
    displayed_instance_uuid: str | None
    updated_at: str | None


def _frozen_instances(
    instances: Mapping[str, InstanceRuntimeState],
) -> Mapping[str, InstanceRuntimeState]:
    return MappingProxyType(dict(instances))


def _empty_snapshot() -> RuntimeStateSnapshot:
    return RuntimeStateSnapshot(
        schema_version=SCHEMA_VERSION,
        instances=_frozen_instances({}),
        display_state="unknown",
        display_commit_id=None,
        displayed_instance_uuid=None,
        updated_at=None,
    )


class RuntimeStateStore:
    """Publish in-memory state immediately and persist it at a bounded cadence."""

    def __init__(
        self,
        path,
        *,
        clock=time.monotonic,
        wall_clock=time.time,
        timer_factory=threading.Timer,
    ):
        self.path = Path(path)
        self._clock = clock
        self._wall_clock = wall_clock
        self._timer_factory = timer_factory
        self._state_lock = threading.Lock()
        self._persistence_lock = threading.Lock()
        self._snapshot = _empty_snapshot()
        self._version = 0
        self._dirty = False
        self._last_persisted_monotonic: float | None = None
        self._pending_timer = None
        self._load()

    def snapshot(self) -> RuntimeStateSnapshot:
        """Return the current immutable snapshot without performing disk I/O."""

        with self._state_lock:
            return self._snapshot

    def record_attempt(self, instance_uuid, attempted_at) -> None:
        instance_uuid = self._instance_uuid(instance_uuid)
        attempted_at = self._timestamp(attempted_at, "attempted_at")
        self._update_instance(
            instance_uuid,
            attempted_at,
            lambda previous: replace(previous, last_attempt_at=attempted_at),
        )

    def record_success(self, instance_uuid, succeeded_at) -> None:
        instance_uuid = self._instance_uuid(instance_uuid)
        succeeded_at = self._timestamp(succeeded_at, "succeeded_at")
        self._update_instance(
            instance_uuid,
            succeeded_at,
            lambda previous: replace(
                previous,
                last_success_at=succeeded_at,
                next_retry_at=None,
            ),
        )

    def record_failure(
        self,
        instance_uuid,
        failed_at,
        error,
        next_retry_at=None,
    ) -> None:
        instance_uuid = self._instance_uuid(instance_uuid)
        failed_at = self._timestamp(failed_at, "failed_at")
        next_retry_at = self._optional_timestamp(next_retry_at, "next_retry_at")
        error_text = str(error)
        self._update_instance(
            instance_uuid,
            failed_at,
            lambda previous: replace(
                previous,
                last_failure_at=failed_at,
                last_error=error_text,
                next_retry_at=next_retry_at,
            ),
        )

    def set_display_state(
        self,
        state,
        commit_id=None,
        *,
        instance_uuid=_UNSET,
        changed_at=None,
    ) -> None:
        state = self._non_empty_text(state, "state")
        commit_id = self._optional_text(commit_id, "commit_id")
        if instance_uuid is not _UNSET:
            instance_uuid = self._optional_instance_uuid(instance_uuid)
        changed_at = self._now_iso() if changed_at is None else self._timestamp(
            changed_at,
            "changed_at",
        )

        with self._state_lock:
            displayed_instance_uuid = self._snapshot.displayed_instance_uuid
            if instance_uuid is not _UNSET:
                displayed_instance_uuid = instance_uuid
            candidate = replace(
                self._snapshot,
                display_state=state,
                display_commit_id=commit_id,
                displayed_instance_uuid=displayed_instance_uuid,
                updated_at=changed_at,
            )
            if candidate == self._snapshot:
                return
            self._publish_locked(candidate)
        self._persist_if_due()

    def prune(
        self,
        current_instance_uuids: Iterable[str],
        *,
        tombstoned_at=None,
    ) -> None:
        """Retain all current records and no more than 64 recent tombstones."""

        if isinstance(current_instance_uuids, (str, bytes)):
            raise TypeError("current_instance_uuids must be an iterable of UUID strings")
        current = {self._instance_uuid(value) for value in current_instance_uuids}
        tombstoned_at = (
            self._now_iso()
            if tombstoned_at is None
            else self._timestamp(tombstoned_at, "tombstoned_at")
        )

        with self._state_lock:
            instances = {}
            for instance_uuid, state in self._snapshot.instances.items():
                if instance_uuid in current:
                    instances[instance_uuid] = replace(state, tombstoned_at=None)
                else:
                    instances[instance_uuid] = replace(
                        state,
                        tombstoned_at=state.tombstoned_at or tombstoned_at,
                    )

            tombstones = sorted(
                (
                    (instance_uuid, state)
                    for instance_uuid, state in instances.items()
                    if state.tombstoned_at is not None
                ),
                key=lambda item: (
                    item[1].tombstoned_at or "",
                    item[1].latest_activity_at(),
                    item[0],
                ),
                reverse=True,
            )
            keep_tombstones = {
                instance_uuid
                for instance_uuid, _state in tombstones[:MAX_TOMBSTONES]
            }
            instances = {
                instance_uuid: state
                for instance_uuid, state in instances.items()
                if state.tombstoned_at is None or instance_uuid in keep_tombstones
            }
            if instances == dict(self._snapshot.instances):
                return
            candidate = replace(
                self._snapshot,
                instances=_frozen_instances(instances),
                updated_at=tombstoned_at,
            )
            self._publish_locked(candidate)
        self._persist_if_due()

    def flush(self) -> bool:
        """Synchronously persist the latest dirty state, bypassing debounce."""

        self._cancel_pending_timer()
        try:
            wrote = self._persist(force=True)
        except Exception:
            self._schedule_dirty_flush(min_delay=PERSISTENCE_INTERVAL_SECONDS)
            raise
        self._cancel_timer_if_clean()
        return wrote

    def _update_instance(self, instance_uuid, updated_at, update) -> None:
        with self._state_lock:
            previous = self._snapshot.instances.get(
                instance_uuid,
                InstanceRuntimeState(),
            )
            candidate_state = update(previous)
            if candidate_state == previous:
                return
            instances = dict(self._snapshot.instances)
            instances[instance_uuid] = candidate_state
            self._publish_locked(
                replace(
                    self._snapshot,
                    instances=_frozen_instances(instances),
                    updated_at=updated_at,
                )
            )
        self._persist_if_due()

    def _publish_locked(self, snapshot) -> None:
        self._snapshot = snapshot
        self._version += 1
        self._dirty = True

    def _persist_if_due(self) -> bool:
        try:
            wrote = self._persist(force=False)
        except Exception:
            self._schedule_dirty_flush(min_delay=PERSISTENCE_INTERVAL_SECONDS)
            raise
        self._cancel_timer_if_clean()
        self._schedule_dirty_flush()
        return wrote

    def _schedule_dirty_flush(self, *, min_delay=0.0) -> None:
        token = object()
        with self._state_lock:
            if not self._dirty or self._pending_timer is not None:
                return
            now = float(self._clock())
            if self._last_persisted_monotonic is None:
                delay = PERSISTENCE_INTERVAL_SECONDS
            else:
                delay = max(
                    0.0,
                    PERSISTENCE_INTERVAL_SECONDS
                    - (now - self._last_persisted_monotonic),
                )
            delay = max(float(min_delay), delay)
            timer = self._timer_factory(
                delay,
                lambda: self._on_flush_timer(token),
            )
            timer.daemon = True
            self._pending_timer = (token, timer)
        try:
            timer.start()
        except BaseException:
            with self._state_lock:
                pending = self._pending_timer
                if pending is not None and pending[0] is token:
                    self._pending_timer = None
            raise

    def _on_flush_timer(self, token) -> None:
        with self._state_lock:
            pending = self._pending_timer
            if pending is None or pending[0] is not token:
                return
            self._pending_timer = None
        retry_delay = 0.0
        try:
            self._persist(force=False)
        except Exception:
            retry_delay = PERSISTENCE_INTERVAL_SECONDS
            logger.exception("Deferred runtime state flush failed: %s", self.path)
        finally:
            self._schedule_dirty_flush(min_delay=retry_delay)

    def _cancel_timer_if_clean(self) -> None:
        with self._state_lock:
            if self._dirty:
                return
        self._cancel_pending_timer()

    def _cancel_pending_timer(self) -> None:
        with self._state_lock:
            pending = self._pending_timer
            self._pending_timer = None
        if pending is not None:
            pending[1].cancel()

    def _persist(self, *, force) -> bool:
        wrote = False
        with self._persistence_lock:
            while True:
                now = float(self._clock())
                with self._state_lock:
                    if not self._dirty:
                        return wrote
                    if (
                        not force
                        and self._last_persisted_monotonic is not None
                        and now - self._last_persisted_monotonic
                        < PERSISTENCE_INTERVAL_SECONDS
                    ):
                        return wrote
                    version = self._version
                    payload = self._serialize(self._snapshot)

                atomic_write_json(self.path, payload, mode=0o600)
                wrote = True

                with self._state_lock:
                    self._last_persisted_monotonic = now
                    if self._version == version:
                        self._dirty = False
                        return wrote
                if not force:
                    return wrote

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            snapshot = self._deserialize(payload)
        except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError):
            logger.warning(
                "Ignoring unreadable runtime state; a later flush will replace it: %s",
                self.path,
                exc_info=True,
            )
            return
        self._snapshot = snapshot
        self._last_persisted_monotonic = float(self._clock())

    @classmethod
    def _serialize(cls, snapshot) -> dict:
        return {
            "schema_version": SCHEMA_VERSION,
            "updated_at": snapshot.updated_at,
            "display": {
                "state": snapshot.display_state,
                "commit_id": snapshot.display_commit_id,
                "instance_uuid": snapshot.displayed_instance_uuid,
            },
            "instances": {
                instance_uuid: {
                    "last_attempt_at": state.last_attempt_at,
                    "last_success_at": state.last_success_at,
                    "last_failure_at": state.last_failure_at,
                    "last_error": state.last_error,
                    "next_retry_at": state.next_retry_at,
                    "tombstoned_at": state.tombstoned_at,
                }
                for instance_uuid, state in snapshot.instances.items()
            },
        }

    @classmethod
    def _deserialize(cls, payload) -> RuntimeStateSnapshot:
        if not isinstance(payload, dict):
            raise ValueError("runtime state must be a JSON object")
        if payload.get("schema_version") != SCHEMA_VERSION:
            raise ValueError("unsupported runtime state schema")
        raw_instances = payload.get("instances", {})
        if not isinstance(raw_instances, dict):
            raise ValueError("runtime state instances must be an object")
        instances = {}
        for raw_uuid, raw_state in raw_instances.items():
            instance_uuid = cls._instance_uuid(raw_uuid)
            if not isinstance(raw_state, dict):
                raise ValueError("runtime instance state must be an object")
            instances[instance_uuid] = InstanceRuntimeState(
                last_attempt_at=cls._optional_timestamp(
                    raw_state.get("last_attempt_at"),
                    "last_attempt_at",
                ),
                last_success_at=cls._optional_timestamp(
                    raw_state.get("last_success_at"),
                    "last_success_at",
                ),
                last_failure_at=cls._optional_timestamp(
                    raw_state.get("last_failure_at"),
                    "last_failure_at",
                ),
                last_error=cls._optional_text(
                    raw_state.get("last_error"),
                    "last_error",
                ),
                next_retry_at=cls._optional_timestamp(
                    raw_state.get("next_retry_at"),
                    "next_retry_at",
                ),
                tombstoned_at=cls._optional_timestamp(
                    raw_state.get("tombstoned_at"),
                    "tombstoned_at",
                ),
            )
        instances = cls._cap_loaded_tombstones(instances)

        raw_display = payload.get("display", {})
        if not isinstance(raw_display, dict):
            raise ValueError("runtime display state must be an object")
        return RuntimeStateSnapshot(
            schema_version=SCHEMA_VERSION,
            instances=_frozen_instances(instances),
            display_state=cls._non_empty_text(
                raw_display.get("state", "unknown"),
                "display state",
            ),
            display_commit_id=cls._optional_text(
                raw_display.get("commit_id"),
                "display commit_id",
            ),
            displayed_instance_uuid=cls._optional_instance_uuid(
                raw_display.get("instance_uuid"),
            ),
            updated_at=cls._optional_timestamp(
                payload.get("updated_at"),
                "updated_at",
            ),
        )

    @staticmethod
    def _cap_loaded_tombstones(instances):
        tombstones = sorted(
            (
                (instance_uuid, state)
                for instance_uuid, state in instances.items()
                if state.tombstoned_at is not None
            ),
            key=lambda item: (
                item[1].tombstoned_at or "",
                item[1].latest_activity_at(),
                item[0],
            ),
            reverse=True,
        )
        keep = {
            instance_uuid
            for instance_uuid, _state in tombstones[:MAX_TOMBSTONES]
        }
        return {
            instance_uuid: state
            for instance_uuid, state in instances.items()
            if state.tombstoned_at is None or instance_uuid in keep
        }

    def _now_iso(self) -> str:
        return datetime.fromtimestamp(
            float(self._wall_clock()),
            tz=timezone.utc,
        ).isoformat()

    @staticmethod
    def _instance_uuid(value) -> str:
        if not isinstance(value, str):
            raise TypeError("instance_uuid must be a string")
        normalized = value.strip()
        if not normalized:
            raise ValueError("instance_uuid must not be empty")
        return normalized

    @classmethod
    def _optional_instance_uuid(cls, value) -> str | None:
        if value is None:
            return None
        return cls._instance_uuid(value)

    @classmethod
    def _timestamp(cls, value, field) -> str:
        return cls._non_empty_text(value, field)

    @classmethod
    def _optional_timestamp(cls, value, field) -> str | None:
        if value is None:
            return None
        return cls._timestamp(value, field)

    @staticmethod
    def _non_empty_text(value, field) -> str:
        if not isinstance(value, str):
            raise TypeError(f"{field} must be a string")
        normalized = value.strip()
        if not normalized:
            raise ValueError(f"{field} must not be empty")
        return normalized

    @classmethod
    def _optional_text(cls, value, field) -> str | None:
        if value is None:
            return None
        return cls._non_empty_text(value, field)
