from __future__ import annotations

from collections import UserDict, UserList
from collections.abc import Mapping as MappingABC
from collections.abc import Sequence, Set
from copy import deepcopy
from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType
from typing import Any, Mapping
from uuid import uuid4
import threading
import time


class LifecycleState(str, Enum):
    STARTING = "starting"
    RUNNING = "running"
    QUIESCING = "quiescing"
    DRAINING = "draining"
    STOPPED = "stopped"
    FORCED_EXIT = "forced_exit"


class CommandKind(str, Enum):
    DISPLAY = "display"
    CACHE_REFRESH = "cache_refresh"


class CommandSource(str, Enum):
    MANUAL = "manual"
    SCHEDULER = "scheduler"
    LIVE = "live"
    BACKGROUND = "background"


class JobStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELED = "canceled"
    ABANDONED = "abandoned"
    SUPERSEDED = "superseded"
    REJECTED = "rejected"


_IMMUTABLE_PAYLOAD_SCALAR_TYPES = {
    type(None),
    bool,
    int,
    float,
    complex,
    str,
    bytes,
}
_SAFE_PAYLOAD_MAPPING_TYPES = {dict, MappingProxyType, UserDict}
_SAFE_PAYLOAD_SEQUENCE_TYPES = {list, tuple, UserList}
_SAFE_PAYLOAD_SET_TYPES = {set, frozenset}


def _is_immutable_payload_scalar(value: Any) -> bool:
    return type(value) in _IMMUTABLE_PAYLOAD_SCALAR_TYPES


def _freeze_hashable_payload_member(value: Any) -> Any:
    value_type = type(value)
    if _is_immutable_payload_scalar(value):
        frozen = value
    elif value_type is tuple:
        frozen = tuple(_freeze_hashable_payload_member(item) for item in value)
    elif value_type is frozenset:
        frozen = frozenset(
            _freeze_hashable_payload_member(item) for item in value
        )
    else:
        raise TypeError(
            f"unsupported mutable payload key/member: {type(value).__name__}"
        )
    hash(frozen)
    return frozen


def freeze_payload(value: Any) -> Any:
    value_type = type(value)
    if _is_immutable_payload_scalar(value):
        return value
    if value_type in _SAFE_PAYLOAD_MAPPING_TYPES:
        return MappingProxyType(
            {
                _freeze_hashable_payload_member(key): freeze_payload(item)
                for key, item in value.items()
            }
        )
    if value_type in _SAFE_PAYLOAD_SEQUENCE_TYPES:
        return tuple(freeze_payload(item) for item in value)
    if value_type in _SAFE_PAYLOAD_SET_TYPES:
        return frozenset(freeze_payload(item) for item in value)
    raise TypeError(
        f"unsupported semantic payload type: {value_type.__name__}"
    )


def _copy_hashable_payload_member(value: Any) -> Any:
    detached = deepcopy(value)
    hash(detached)
    return detached


def thaw_payload(value: Any) -> Any:
    """Return a fully detached mutable representation for plugin code."""
    if isinstance(value, MappingABC):
        return {
            _copy_hashable_payload_member(key): thaw_payload(item)
            for key, item in value.items()
        }
    if isinstance(value, (str, bytes)):
        return deepcopy(value)
    if isinstance(value, Sequence):
        return [thaw_payload(item) for item in value]
    if isinstance(value, Set):
        return {_copy_hashable_payload_member(item) for item in value}
    return deepcopy(value)


@dataclass(frozen=True)
class RefreshCommand:
    id: str
    kind: CommandKind
    source: CommandSource
    plugin_id: str
    instance_uuid: str | None
    structural_generation: int | None
    settings_revision: int | None
    force: bool
    priority: int
    enqueued_monotonic: float
    deadline_monotonic: float
    idempotency_key: str | None
    payload: Mapping[str, Any] = field(compare=False, repr=False)

    @classmethod
    def create(
        cls,
        *,
        kind,
        source,
        plugin_id,
        payload,
        now_monotonic,
        deadline_monotonic,
        instance_uuid=None,
        structural_generation=None,
        settings_revision=None,
        force=False,
        priority=0,
        idempotency_key=None,
    ):
        return cls(
            id=uuid4().hex,
            kind=kind,
            source=source,
            plugin_id=str(plugin_id),
            instance_uuid=instance_uuid,
            structural_generation=structural_generation,
            settings_revision=settings_revision,
            force=bool(force),
            priority=int(priority),
            enqueued_monotonic=float(now_monotonic),
            deadline_monotonic=float(deadline_monotonic),
            idempotency_key=idempotency_key,
            payload=freeze_payload({} if payload is None else payload),
        )


@dataclass
class JobRecord:
    id: str
    command_id: str
    status: JobStatus
    submitted_at: float
    started_at: float | None = None
    completed_at: float | None = None
    cancel_requested_at: float | None = None
    superseded_by: str | None = None
    error_code: str | None = None
    error: str | None = None

    @classmethod
    def from_command(cls, command: RefreshCommand, submitted_at: float):
        return cls(command.id, command.id, JobStatus.QUEUED, submitted_at)

    def mark_running(self, when: float) -> None:
        if self.status is not JobStatus.QUEUED:
            raise ValueError("Only queued jobs can start")
        self.status = JobStatus.RUNNING
        self.started_at = when

    def request_cancel(self, when: float) -> None:
        if self.status is JobStatus.RUNNING:
            self.cancel_requested_at = when

    def mark_succeeded(self, when: float) -> None:
        if self.status is not JobStatus.RUNNING or self.cancel_requested_at is not None:
            raise ValueError("Canceled or non-running jobs cannot succeed")
        self.status = JobStatus.SUCCEEDED
        self.completed_at = when


@dataclass(frozen=True)
class QueueSnapshot:
    depth: int
    capacity: int
    rejected_total: int
    superseded_total: int
    accepting: bool


class TaskCancelled(RuntimeError):
    pass


class TaskDeadlineExceeded(TaskCancelled):
    pass


@dataclass(frozen=True)
class TaskContext:
    cancel_event: threading.Event
    deadline_monotonic: float
    clock: Any = field(default=time.monotonic, compare=False, repr=False)

    @classmethod
    def never_cancelled(cls, *, deadline_monotonic, clock=time.monotonic):
        return cls(threading.Event(), float(deadline_monotonic), clock)

    def remaining_seconds(self):
        return max(0.0, self.deadline_monotonic - self.clock())

    def raise_if_cancelled(self):
        now = self.clock()
        if now >= self.deadline_monotonic:
            raise TaskDeadlineExceeded("task deadline expired")
        if self.cancel_event.is_set():
            raise TaskCancelled("task was canceled")
