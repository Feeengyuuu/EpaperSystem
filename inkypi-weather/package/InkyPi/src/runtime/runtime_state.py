"""Bounded runtime refresh state, kept separate from user configuration."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from enum import Enum
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

SCHEMA_VERSION = 3
PERSISTENCE_INTERVAL_SECONDS = 5.0
MAX_TOMBSTONES = 64
_UNSET = object()
_LOWERCASE_HEX = frozenset("0123456789abcdef")


class RefreshLane(str, Enum):
    DATA = "data"
    LIVE = "live"
    THEME = "theme"
    PRESENTATION = "presentation"


@dataclass(frozen=True)
class RefreshLaneState:
    last_attempt_at: str | None = None
    last_success_at: str | None = None
    last_failure_at: str | None = None
    last_error: str | None = None
    next_retry_at: str | None = None


def _validated_request_id(value) -> str:
    if not isinstance(value, str):
        raise TypeError("request_id must be a string")
    if len(value) != 32 or any(character not in _LOWERCASE_HEX for character in value):
        raise ValueError("request_id must be 32 lowercase hexadecimal characters")
    return value


def _validated_positive_integer(value, field) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{field} must be an integer")
    if value <= 0:
        raise ValueError(f"{field} must be positive")
    return value


def _validated_theme_mode(value, field) -> str | None:
    if value not in {None, "day", "night"}:
        raise ValueError(f"{field} must be None, 'day', or 'night'")
    return value


def _validated_iso_timestamp(value, field) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field} must be a string")
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field} must not be empty")
    try:
        datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ValueError(f"{field} must be an ISO timestamp") from exc
    return normalized


def _validated_non_empty_text(value, field) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field} must be a string")
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field} must not be empty")
    return normalized


@dataclass(frozen=True)
class PresentationRequestState:
    request_id: str
    requested_at: str
    structural_generation: int
    settings_revision: int
    origin_theme_mode: str | None
    origin_display_commit_id: str
    prepared_at: str | None = None
    prepared_theme_mode: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "request_id", _validated_request_id(self.request_id))
        object.__setattr__(
            self,
            "requested_at",
            _validated_iso_timestamp(self.requested_at, "requested_at"),
        )
        for field_name in ("structural_generation", "settings_revision"):
            _validated_positive_integer(getattr(self, field_name), field_name)
        _validated_theme_mode(self.origin_theme_mode, "origin_theme_mode")
        object.__setattr__(
            self,
            "origin_display_commit_id",
            _validated_non_empty_text(
                self.origin_display_commit_id,
                "origin_display_commit_id",
            ),
        )
        if self.prepared_at is not None:
            object.__setattr__(
                self,
                "prepared_at",
                _validated_iso_timestamp(self.prepared_at, "prepared_at"),
            )
        _validated_theme_mode(self.prepared_theme_mode, "prepared_theme_mode")


@dataclass(frozen=True)
class PresentationCommitReceipt:
    request_id: str
    committed_at: str
    display_commit_id: str
    structural_generation: int
    settings_revision: int
    theme_mode: str | None

    def __post_init__(self) -> None:
        object.__setattr__(self, "request_id", _validated_request_id(self.request_id))
        object.__setattr__(
            self,
            "committed_at",
            _validated_iso_timestamp(self.committed_at, "committed_at"),
        )
        object.__setattr__(
            self,
            "display_commit_id",
            _validated_non_empty_text(self.display_commit_id, "display_commit_id"),
        )
        for field_name in ("structural_generation", "settings_revision"):
            _validated_positive_integer(getattr(self, field_name), field_name)
        _validated_theme_mode(self.theme_mode, "theme_mode")


@dataclass(frozen=True)
class LastGoodCacheState:
    theme_mode: str | None
    structural_generation: int
    settings_revision: int
    promoted_at: str

    def __post_init__(self) -> None:
        if self.theme_mode not in {None, "day", "night"}:
            raise ValueError("theme_mode must be None, 'day', or 'night'")
        for name in ("structural_generation", "settings_revision"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int):
                raise TypeError(f"{name} must be an integer")
            if value <= 0:
                raise ValueError(f"{name} must be positive")
        if not isinstance(self.promoted_at, str):
            raise TypeError("promoted_at must be a string")
        promoted_at = self.promoted_at.strip()
        if not promoted_at:
            raise ValueError("promoted_at must not be empty")
        object.__setattr__(self, "promoted_at", promoted_at)


@dataclass(frozen=True)
class InstanceRuntimeState:
    """Immutable refresh lanes and cache state for one stable instance UUID."""

    data: RefreshLaneState = field(default_factory=RefreshLaneState)
    live: RefreshLaneState = field(default_factory=RefreshLaneState)
    theme: RefreshLaneState = field(default_factory=RefreshLaneState)
    presentation: RefreshLaneState = field(default_factory=RefreshLaneState)
    presentation_request: PresentationRequestState | None = None
    presentation_receipt: PresentationCommitReceipt | None = None
    last_good_cache: LastGoodCacheState | None = None
    legacy_cache_success_at: str | None = None
    tombstoned_at: str | None = None

    # Transitional aliases keep the pre-v2 scheduler on the data lane until C.
    @property
    def last_attempt_at(self) -> str | None:
        return self.data.last_attempt_at

    @property
    def last_success_at(self) -> str | None:
        return self.data.last_success_at or self.legacy_cache_success_at

    @property
    def last_failure_at(self) -> str | None:
        return self.data.last_failure_at

    @property
    def last_error(self) -> str | None:
        return self.data.last_error

    @property
    def next_retry_at(self) -> str | None:
        return self.data.next_retry_at

    def latest_activity_at(self) -> str:
        values = [
            value
            for lane in (self.data, self.live, self.theme, self.presentation)
            for value in (
                lane.last_attempt_at,
                lane.last_success_at,
                lane.last_failure_at,
            )
            if value is not None
        ]
        values.extend(
            value
            for value in (
                self.last_good_cache.promoted_at
                if self.last_good_cache is not None
                else None,
                self.legacy_cache_success_at,
                self.presentation_request.requested_at
                if self.presentation_request is not None
                else None,
                self.presentation_request.prepared_at
                if self.presentation_request is not None
                else None,
                self.presentation_receipt.committed_at
                if self.presentation_receipt is not None
                else None,
            )
            if value is not None
        )
        return max(values, default="")


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

    def record_attempt(
        self,
        instance_uuid,
        attempted_at,
        *,
        lane=RefreshLane.DATA,
    ) -> None:
        instance_uuid = self._instance_uuid(instance_uuid)
        attempted_at = self._timestamp(attempted_at, "attempted_at")
        lane = self._refresh_lane(lane)
        self._update_instance(
            instance_uuid,
            attempted_at,
            lambda previous: self._replace_lane(
                previous,
                lane,
                last_attempt_at=attempted_at,
            ),
        )

    def record_success(
        self,
        instance_uuid,
        succeeded_at,
        *,
        lane=RefreshLane.DATA,
        last_good_cache: LastGoodCacheState | None = None,
    ) -> None:
        instance_uuid = self._instance_uuid(instance_uuid)
        succeeded_at = self._timestamp(succeeded_at, "succeeded_at")
        lane = self._refresh_lane(lane)
        if last_good_cache is not None and not isinstance(
            last_good_cache,
            LastGoodCacheState,
        ):
            raise TypeError("last_good_cache must be LastGoodCacheState or None")

        def update(previous):
            candidate = self._replace_lane(
                previous,
                lane,
                last_success_at=succeeded_at,
                next_retry_at=None,
            )
            if last_good_cache is not None:
                candidate = replace(candidate, last_good_cache=last_good_cache)
            return candidate

        self._update_instance(
            instance_uuid,
            succeeded_at,
            update,
        )

    def record_failure(
        self,
        instance_uuid,
        failed_at,
        error,
        next_retry_at=None,
        *,
        lane=RefreshLane.DATA,
    ) -> None:
        instance_uuid = self._instance_uuid(instance_uuid)
        failed_at = self._timestamp(failed_at, "failed_at")
        next_retry_at = self._optional_timestamp(next_retry_at, "next_retry_at")
        error_text = str(error)
        lane = self._refresh_lane(lane)
        self._update_instance(
            instance_uuid,
            failed_at,
            lambda previous: self._replace_lane(
                previous,
                lane,
                last_failure_at=failed_at,
                last_error=error_text,
                next_retry_at=next_retry_at,
            ),
        )

    def request_presentation(
        self,
        instance_uuid,
        request: PresentationRequestState,
    ) -> bool:
        instance_uuid = self._instance_uuid(instance_uuid)
        if not isinstance(request, PresentationRequestState):
            raise TypeError("request must be PresentationRequestState")

        def update(previous):
            current = previous.presentation_request
            if (
                current is not None
                and current.structural_generation == request.structural_generation
                and current.settings_revision == request.settings_revision
            ):
                return previous
            return replace(previous, presentation_request=request)

        return self._cas_update_instance(
            instance_uuid,
            request.requested_at,
            update,
        )

    def mark_presentation_prepared(
        self,
        instance_uuid,
        request_id,
        prepared_at,
        theme_mode,
    ) -> bool:
        instance_uuid = self._instance_uuid(instance_uuid)
        request_id = _validated_request_id(request_id)
        prepared_at = _validated_iso_timestamp(prepared_at, "prepared_at")
        theme_mode = _validated_theme_mode(theme_mode, "theme_mode")

        def update(previous):
            current = previous.presentation_request
            if current is None or current.request_id != request_id:
                return previous
            return replace(
                previous,
                presentation_request=replace(
                    current,
                    prepared_at=prepared_at,
                    prepared_theme_mode=theme_mode,
                ),
            )

        return self._cas_update_instance(instance_uuid, prepared_at, update)

    def commit_presentation(
        self,
        instance_uuid,
        receipt: PresentationCommitReceipt,
        *,
        last_good_cache,
    ) -> bool:
        instance_uuid = self._instance_uuid(instance_uuid)
        if not isinstance(receipt, PresentationCommitReceipt):
            raise TypeError("receipt must be PresentationCommitReceipt")
        if not isinstance(last_good_cache, LastGoodCacheState):
            raise TypeError("last_good_cache must be LastGoodCacheState")

        def update(previous):
            current = previous.presentation_request
            if (
                current is None
                or current.prepared_at is None
                or current.request_id != receipt.request_id
                or current.structural_generation != receipt.structural_generation
                or current.settings_revision != receipt.settings_revision
                or current.prepared_theme_mode != receipt.theme_mode
            ):
                return previous
            return replace(
                previous,
                presentation=replace(
                    previous.presentation,
                    last_success_at=receipt.committed_at,
                    next_retry_at=None,
                ),
                presentation_request=None,
                presentation_receipt=receipt,
                last_good_cache=last_good_cache,
            )

        return self._cas_update_instance(
            instance_uuid,
            receipt.committed_at,
            update,
        )

    def satisfy_presentation_no_change(
        self,
        instance_uuid,
        request_id,
        succeeded_at,
    ) -> bool:
        instance_uuid = self._instance_uuid(instance_uuid)
        request_id = _validated_request_id(request_id)
        succeeded_at = _validated_iso_timestamp(succeeded_at, "succeeded_at")

        def update(previous):
            current = previous.presentation_request
            if current is None or current.request_id != request_id:
                return previous
            return replace(
                previous,
                presentation=replace(
                    previous.presentation,
                    last_success_at=succeeded_at,
                    next_retry_at=None,
                ),
                presentation_request=None,
            )

        return self._cas_update_instance(instance_uuid, succeeded_at, update)

    def clear_stale_presentation(
        self,
        instance_uuid,
        *,
        structural_generation,
        settings_revision,
    ) -> bool:
        instance_uuid = self._instance_uuid(instance_uuid)
        structural_generation = _validated_positive_integer(
            structural_generation,
            "structural_generation",
        )
        settings_revision = _validated_positive_integer(
            settings_revision,
            "settings_revision",
        )
        cleared_at = self._now_iso()

        def update(previous):
            current = previous.presentation_request
            if current is None or (
                current.structural_generation == structural_generation
                and current.settings_revision == settings_revision
            ):
                return previous
            return replace(previous, presentation_request=None)

        return self._cas_update_instance(instance_uuid, cleared_at, update)

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

    @staticmethod
    def _refresh_lane(value) -> RefreshLane:
        try:
            return RefreshLane(value)
        except (TypeError, ValueError) as exc:
            raise ValueError("lane must be data, live, theme, or presentation") from exc

    @staticmethod
    def _replace_lane(state, lane, **changes) -> InstanceRuntimeState:
        lane_state = replace(getattr(state, lane.value), **changes)
        return replace(state, **{lane.value: lane_state})

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
        self._cas_update_instance(instance_uuid, updated_at, update)

    def _cas_update_instance(self, instance_uuid, updated_at, update) -> bool:
        with self._state_lock:
            previous = self._snapshot.instances.get(
                instance_uuid,
                InstanceRuntimeState(),
            )
            candidate_state = update(previous)
            if candidate_state == previous:
                return False
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
        return True

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
        if payload.get("schema_version") == SCHEMA_VERSION:
            self._last_persisted_monotonic = float(self._clock())
            return

        # Rewrite a valid v1 snapshot through the same atomic writer used for
        # normal persistence.  If that write fails, retain the safe in-memory
        # migration and leave it dirty for the bounded retry timer.
        self._version += 1
        self._dirty = True
        try:
            self._persist(force=True)
        except Exception:
            logger.warning(
                "Could not persist migrated runtime state yet: %s",
                self.path,
                exc_info=True,
            )
            self._schedule_dirty_flush(min_delay=PERSISTENCE_INTERVAL_SECONDS)

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
                    "lanes": {
                        lane.value: cls._serialize_lane(getattr(state, lane.value))
                        for lane in RefreshLane
                    },
                    "last_good_cache": cls._serialize_last_good_cache(
                        state.last_good_cache
                    ),
                    "presentation_request": cls._serialize_presentation_request(
                        state.presentation_request
                    ),
                    "presentation_receipt": cls._serialize_presentation_receipt(
                        state.presentation_receipt
                    ),
                    "legacy_cache_success_at": state.legacy_cache_success_at,
                    "tombstoned_at": state.tombstoned_at,
                }
                for instance_uuid, state in snapshot.instances.items()
            },
        }

    @staticmethod
    def _serialize_lane(state: RefreshLaneState) -> dict:
        return {
            "last_attempt_at": state.last_attempt_at,
            "last_success_at": state.last_success_at,
            "last_failure_at": state.last_failure_at,
            "last_error": state.last_error,
            "next_retry_at": state.next_retry_at,
        }

    @staticmethod
    def _serialize_last_good_cache(state: LastGoodCacheState | None):
        if state is None:
            return None
        return {
            "theme_mode": state.theme_mode,
            "structural_generation": state.structural_generation,
            "settings_revision": state.settings_revision,
            "promoted_at": state.promoted_at,
        }

    @staticmethod
    def _serialize_presentation_request(state: PresentationRequestState | None):
        if state is None:
            return None
        return {
            "request_id": state.request_id,
            "requested_at": state.requested_at,
            "structural_generation": state.structural_generation,
            "settings_revision": state.settings_revision,
            "origin_theme_mode": state.origin_theme_mode,
            "origin_display_commit_id": state.origin_display_commit_id,
            "prepared_at": state.prepared_at,
            "prepared_theme_mode": state.prepared_theme_mode,
        }

    @staticmethod
    def _serialize_presentation_receipt(state: PresentationCommitReceipt | None):
        if state is None:
            return None
        return {
            "request_id": state.request_id,
            "committed_at": state.committed_at,
            "display_commit_id": state.display_commit_id,
            "structural_generation": state.structural_generation,
            "settings_revision": state.settings_revision,
            "theme_mode": state.theme_mode,
        }

    @classmethod
    def _deserialize(cls, payload) -> RuntimeStateSnapshot:
        if not isinstance(payload, dict):
            raise ValueError("runtime state must be a JSON object")
        schema_version = payload.get("schema_version")
        if schema_version not in {1, 2, SCHEMA_VERSION}:
            raise ValueError("unsupported runtime state schema")
        raw_instances = payload.get("instances", {})
        if not isinstance(raw_instances, dict):
            raise ValueError("runtime state instances must be an object")
        instances = {}
        for raw_uuid, raw_state in raw_instances.items():
            instance_uuid = cls._instance_uuid(raw_uuid)
            if not isinstance(raw_state, dict):
                raise ValueError("runtime instance state must be an object")
            if schema_version == 1:
                instances[instance_uuid] = cls._deserialize_v1_instance(raw_state)
            elif schema_version == 2:
                instances[instance_uuid] = cls._deserialize_v2_instance(raw_state)
            else:
                instances[instance_uuid] = cls._deserialize_v3_instance(raw_state)
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

    @classmethod
    def _deserialize_v1_instance(cls, raw_state) -> InstanceRuntimeState:
        return InstanceRuntimeState(
            data=RefreshLaneState(
                last_attempt_at=cls._optional_timestamp(
                    raw_state.get("last_attempt_at"),
                    "last_attempt_at",
                ),
                # A v1 success has no lane or exact cache revision provenance.
                last_success_at=None,
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
            ),
            legacy_cache_success_at=cls._optional_timestamp(
                raw_state.get("last_success_at"),
                "last_success_at",
            ),
            tombstoned_at=cls._optional_timestamp(
                raw_state.get("tombstoned_at"),
                "tombstoned_at",
            ),
        )

    @classmethod
    def _deserialize_v2_instance(cls, raw_state) -> InstanceRuntimeState:
        raw_lanes = raw_state.get("lanes", {})
        if not isinstance(raw_lanes, dict):
            raise ValueError("runtime lanes must be an object")
        return InstanceRuntimeState(
            data=cls._deserialize_lane(raw_lanes.get(RefreshLane.DATA.value, {})),
            live=cls._deserialize_lane(raw_lanes.get(RefreshLane.LIVE.value, {})),
            theme=cls._deserialize_lane(raw_lanes.get(RefreshLane.THEME.value, {})),
            last_good_cache=cls._deserialize_last_good_cache(
                raw_state.get("last_good_cache")
            ),
            legacy_cache_success_at=cls._optional_timestamp(
                raw_state.get("legacy_cache_success_at"),
                "legacy_cache_success_at",
            ),
            tombstoned_at=cls._optional_timestamp(
                raw_state.get("tombstoned_at"),
                "tombstoned_at",
            ),
        )

    @classmethod
    def _deserialize_v3_instance(cls, raw_state) -> InstanceRuntimeState:
        raw_lanes = raw_state.get("lanes", {})
        if not isinstance(raw_lanes, dict):
            raise ValueError("runtime lanes must be an object")
        return replace(
            cls._deserialize_v2_instance(raw_state),
            presentation=cls._deserialize_lane(
                raw_lanes.get(RefreshLane.PRESENTATION.value, {})
            ),
            presentation_request=cls._deserialize_presentation_request(
                raw_state.get("presentation_request")
            ),
            presentation_receipt=cls._deserialize_presentation_receipt(
                raw_state.get("presentation_receipt")
            ),
        )

    @classmethod
    def _deserialize_lane(cls, raw_state) -> RefreshLaneState:
        if not isinstance(raw_state, dict):
            raise ValueError("runtime lane state must be an object")
        return RefreshLaneState(
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
        )

    @classmethod
    def _deserialize_last_good_cache(cls, raw_state):
        if raw_state is None:
            return None
        if not isinstance(raw_state, dict):
            raise ValueError("last_good_cache must be an object or null")
        return LastGoodCacheState(
            theme_mode=raw_state.get("theme_mode"),
            structural_generation=raw_state.get("structural_generation"),
            settings_revision=raw_state.get("settings_revision"),
            promoted_at=cls._timestamp(
                raw_state.get("promoted_at"),
                "last_good_cache promoted_at",
            ),
        )

    @staticmethod
    def _deserialize_presentation_request(raw_state):
        if raw_state is None:
            return None
        if not isinstance(raw_state, dict):
            raise ValueError("presentation_request must be an object or null")
        return PresentationRequestState(
            request_id=raw_state.get("request_id"),
            requested_at=raw_state.get("requested_at"),
            structural_generation=raw_state.get("structural_generation"),
            settings_revision=raw_state.get("settings_revision"),
            origin_theme_mode=raw_state.get("origin_theme_mode"),
            origin_display_commit_id=raw_state.get("origin_display_commit_id"),
            prepared_at=raw_state.get("prepared_at"),
            prepared_theme_mode=raw_state.get("prepared_theme_mode"),
        )

    @staticmethod
    def _deserialize_presentation_receipt(raw_state):
        if raw_state is None:
            return None
        if not isinstance(raw_state, dict):
            raise ValueError("presentation_receipt must be an object or null")
        return PresentationCommitReceipt(
            request_id=raw_state.get("request_id"),
            committed_at=raw_state.get("committed_at"),
            display_commit_id=raw_state.get("display_commit_id"),
            structural_generation=raw_state.get("structural_generation"),
            settings_revision=raw_state.get("settings_revision"),
            theme_mode=raw_state.get("theme_mode"),
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
