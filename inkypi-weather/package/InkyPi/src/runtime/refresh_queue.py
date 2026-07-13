from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
import math
import threading
import time
from uuid import uuid4

from .refresh_contracts import (
    CommandKind,
    CommandSource,
    JobRecord,
    JobStatus,
    QueueSnapshot,
    RefreshCommand,
    RefreshIntent,
    freeze_payload,
)


_FINISH_STATUSES = {
    JobStatus.SUCCEEDED,
    JobStatus.FAILED,
    JobStatus.CANCELED,
    JobStatus.ABANDONED,
}
_SOURCE_URGENCY = {
    CommandSource.MANUAL: 4,
    CommandSource.SCHEDULER: 3,
    CommandSource.LIVE: 2,
    CommandSource.BACKGROUND: 1,
}
_MAX_TERMINAL_LIMIT = 256
_MAX_TERMINAL_TTL_SECONDS = 1800.0


@dataclass(frozen=True)
class QueueEntry:
    command: RefreshCommand
    job: JobRecord
    cancel_event: "CancellationSignal" = field(compare=False, repr=False)


class CancellationSignal:
    """Read-only view of a queue-owned cancellation event."""

    __slots__ = ("__event",)

    def __init__(self, event: threading.Event):
        self.__event = event

    def is_set(self) -> bool:
        return self.__event.is_set()

    def wait(self, timeout: float | None = None) -> bool:
        return self.__event.wait(timeout)


class RefreshQueueError(RuntimeError):
    error_code = "refresh_queue_error"

    def __init__(self, message: str, job: JobRecord | None = None):
        super().__init__(message)
        detached = replace(job) if job is not None else None
        self.job = detached
        self.rejected_job = detached
        self.job_record = detached


class QueueFullError(RefreshQueueError):
    error_code = "refresh_queue_full"


class QueueStoppingError(RefreshQueueError):
    error_code = "refresh_service_stopping"


class IdempotencyConflictError(RefreshQueueError):
    error_code = "idempotency_conflict"


class DuplicateCommandConflictError(RefreshQueueError):
    error_code = "duplicate_command_conflict"


class InvalidRefreshCommandError(RefreshQueueError):
    error_code = "invalid_refresh_command"


class InvalidJobTransitionError(RefreshQueueError):
    error_code = "invalid_job_transition"


class RefreshQueue:
    def __init__(
        self,
        capacity: int = 32,
        manual_reserved: int = 4,
        terminal_limit: int = 256,
        terminal_ttl_seconds: float = 1800,
        clock=time.monotonic,
        wall_clock=time.time,
        alias_limit: int = 512,
    ):
        self.capacity = max(1, min(128, int(capacity)))
        self.manual_reserved = max(
            0,
            min(self.capacity, int(manual_reserved)),
        )
        self.terminal_limit = self._bounded_terminal_limit(terminal_limit)
        self.terminal_ttl_seconds = self._bounded_terminal_ttl(
            terminal_ttl_seconds
        )
        self.alias_limit = max(1, min(4096, int(alias_limit)))
        self._clock = clock
        self._wall_clock = wall_clock
        self._condition = threading.Condition()
        self._pending: dict[str, int] = {}
        self._running: set[str] = set()
        self._jobs: dict[str, JobRecord] = {}
        self._commands: dict[str, RefreshCommand] = {}
        self._cancel_events: dict[str, threading.Event] = {}
        self._idempotency_targets: dict[str, str] = {}
        self._idempotency_requests: dict[str, RefreshCommand] = {}
        self._terminal_order: dict[str, tuple[float, int]] = {}
        self._accepting = True
        self._sequence = 0
        self._terminal_sequence = 0
        self._rejected_total = 0
        self._superseded_total = 0
        self._fairness_priority: int | None = None
        self._high_priority_streak = 0
        self._change_sequence = 0

    def submit(self, command: RefreshCommand) -> JobRecord:
        normalized = self._normalize_command(command)
        with self._condition:
            now = self._clock()
            self._prune_terminal_locked(now)
            self._expire_pending_locked(now)

            command_replay = self._resolve_command_id_locked(normalized, now)
            if command_replay is not None:
                return command_replay

            idempotent = self._resolve_idempotency_locked(normalized, now)
            if idempotent is not None:
                self._record_absorbed_identity_locked(
                    normalized,
                    idempotent.id,
                    now,
                )
                return idempotent

            if not self._accepting:
                rejected = self._reject_locked(
                    normalized,
                    QueueStoppingError.error_code,
                    "refresh service is stopping",
                    now,
                )
                raise QueueStoppingError("refresh service is stopping", rejected)

            invalid_reason = self._invalid_command_reason(normalized)
            if invalid_reason is not None:
                rejected = self._reject_locked(
                    normalized,
                    InvalidRefreshCommandError.error_code,
                    invalid_reason,
                    now,
                )
                raise InvalidRefreshCommandError(invalid_reason, rejected)

            if normalized.deadline_monotonic <= now:
                return self._cancel_expired_submission_locked(normalized, now)

            if normalized.idempotency_key is None:
                coalesced = self._coalesce_locked(normalized, now)
                if coalesced is not None:
                    self._record_absorbed_identity_locked(
                        normalized,
                        coalesced.id,
                        now,
                    )
                    self._publish_change_locked()
                    return replace(coalesced)

            if not self._has_active_idempotency_capacity_locked(normalized):
                rejected = self._reject_locked(
                    normalized,
                    QueueFullError.error_code,
                    "refresh queue metadata is full",
                    now,
                )
                raise QueueFullError("refresh queue metadata is full", rejected)

            if normalized.idempotency_key is not None:
                coalesced = self._coalesce_locked(normalized, now)
                if coalesced is not None:
                    self._register_idempotency_locked(normalized, coalesced.id)
                    self._record_absorbed_identity_locked(
                        normalized,
                        coalesced.id,
                        now,
                    )
                    self._publish_change_locked()
                    return replace(coalesced)

            if self._at_capacity_locked(normalized):
                rejected = self._reject_locked(
                    normalized,
                    QueueFullError.error_code,
                    "refresh queue is full",
                    now,
                )
                raise QueueFullError("refresh queue is full", rejected)

            job = JobRecord.from_command(normalized, self._wall_clock())
            self._jobs[job.id] = job
            self._commands[job.id] = normalized
            self._cancel_events[job.id] = threading.Event()
            self._sequence += 1
            self._pending[job.id] = self._sequence
            self._register_idempotency_locked(normalized, job.id)
            self._publish_change_locked()
            return replace(job)

    def take(self, timeout: float | None = None) -> QueueEntry | None:
        wait_deadline = None
        timed_out = False
        if timeout is not None:
            timeout = max(0.0, float(timeout))
            wait_deadline = self._clock() + timeout

        with self._condition:
            while True:
                now = self._clock()
                self._prune_terminal_locked(now)
                self._expire_pending_locked(now)
                if self._pending and len(self._running) < self.capacity:
                    job_id = self._select_pending_locked()
                    del self._pending[job_id]
                    job = self._jobs[job_id]
                    job.mark_running(self._wall_clock())
                    self._running.add(job_id)
                    return QueueEntry(
                        self._commands[job_id],
                        replace(job),
                        CancellationSignal(self._cancel_events[job_id]),
                    )

                if not self._accepting:
                    return None
                if timed_out:
                    return None
                if wait_deadline is not None:
                    remaining = wait_deadline - now
                    if remaining <= 0:
                        return None
                    timed_out = not self._condition.wait(remaining)
                else:
                    self._condition.wait()

    def finish(
        self,
        job_id: str,
        status: JobStatus,
        *,
        error_code: str | None = None,
        error: str | None = None,
    ) -> JobRecord:
        with self._condition:
            now = self._clock()
            self._prune_terminal_locked(now)
            self._expire_pending_locked(now)
            job = self._jobs.get(job_id)
            if job is None:
                raise InvalidJobTransitionError(
                    f"unknown refresh job: {job_id}",
                )
            if status not in _FINISH_STATUSES:
                raise InvalidJobTransitionError(
                    f"invalid terminal status for refresh job: {status}",
                    job,
                )

            effective_status = status
            if status is JobStatus.SUCCEEDED and job.cancel_requested_at is not None:
                effective_status = JobStatus.CANCELED

            if job.status in _FINISH_STATUSES:
                if (
                    job.status is effective_status
                    and job.error_code == error_code
                    and job.error == error
                ):
                    return replace(job)
                raise InvalidJobTransitionError(
                    f"refresh job already finished as {job.status.value}",
                    job,
                )

            if job.status is not JobStatus.RUNNING:
                raise InvalidJobTransitionError(
                    f"refresh job cannot finish from {job.status.value}",
                    job,
                )

            job.status = effective_status
            job.completed_at = self._wall_clock()
            job.error_code = error_code
            job.error = error
            if effective_status in {JobStatus.CANCELED, JobStatus.ABANDONED}:
                self._cancel_events[job.id].set()
            self._running.discard(job.id)
            result = replace(job)
            self._record_terminal_locked(job.id, now)
            self._prune_terminal_locked(now)
            self._publish_change_locked()
            return result

    def get_job(self, job_id: str) -> JobRecord | None:
        with self._condition:
            now = self._clock()
            self._prune_terminal_locked(now)
            self._expire_pending_locked(now)
            job = self._jobs.get(job_id)
            return replace(job) if job is not None else None

    def get_entry(self, job_id: str) -> QueueEntry | None:
        """Return immutable command metadata with a detached job snapshot."""
        with self._condition:
            now = self._clock()
            self._prune_terminal_locked(now)
            self._expire_pending_locked(now)
            actual_job_id = self._resolve_actual_job_id_locked(job_id)
            job = self._jobs.get(actual_job_id)
            command = self._commands.get(actual_job_id)
            if job is None or command is None:
                return None
            return QueueEntry(
                command,
                replace(job),
                CancellationSignal(self._cancel_events[actual_job_id]),
            )

    def cancel_instance(self, instance_uuid: str) -> int:
        with self._condition:
            now = self._clock()
            self._prune_terminal_locked(now)
            self._expire_pending_locked(now)
            affected = self._cancel_matching_locked(
                lambda command: command.instance_uuid == instance_uuid,
                now,
            )
            self._prune_terminal_locked(now)
            self._publish_change_locked()
            return affected

    def begin_quiesce(self) -> int:
        with self._condition:
            now = self._clock()
            self._prune_terminal_locked(now)
            self._expire_pending_locked(now)
            self._accepting = False
            affected = self._cancel_matching_locked(lambda _command: True, now)
            self._prune_terminal_locked(now)
            self._publish_change_locked()
            return affected

    def change_token(self) -> int:
        """Return a cursor for the queue's non-lossy change notification."""
        with self._condition:
            return self._change_sequence

    def wake(self) -> int:
        """Publish a state-change wake without adding synthetic queue work."""
        with self._condition:
            self._publish_change_locked()
            return self._change_sequence

    def wait_for_change(self, token: int, timeout: float | None = None) -> int:
        """Wait until the queue changes after ``token`` and return a new cursor."""
        timed_out = False
        wait_deadline = None
        if timeout is not None:
            timeout = max(0.0, float(timeout))
            wait_deadline = self._clock() + timeout

        with self._condition:
            while self._change_sequence == token and not timed_out:
                if wait_deadline is None:
                    self._condition.wait()
                    continue
                remaining = wait_deadline - self._clock()
                if remaining <= 0:
                    break
                timed_out = not self._condition.wait(remaining)
            return self._change_sequence

    def snapshot(self) -> QueueSnapshot:
        with self._condition:
            now = self._clock()
            self._prune_terminal_locked(now)
            self._expire_pending_locked(now)
            return QueueSnapshot(
                depth=len(self._pending),
                capacity=self.capacity,
                rejected_total=self._rejected_total,
                superseded_total=self._superseded_total,
                accepting=self._accepting,
            )

    def _publish_change_locked(self) -> None:
        self._change_sequence += 1
        self._condition.notify_all()

    @staticmethod
    def _normalize_command(command: RefreshCommand) -> RefreshCommand:
        return replace(command, payload=freeze_payload(command.payload))

    @staticmethod
    def _bounded_terminal_limit(value: object) -> int:
        try:
            converted = int(value)
        except (TypeError, ValueError, OverflowError):
            return _MAX_TERMINAL_LIMIT
        return max(0, min(_MAX_TERMINAL_LIMIT, converted))

    @staticmethod
    def _bounded_terminal_ttl(value: object) -> float:
        try:
            converted = float(value)
        except (TypeError, ValueError, OverflowError):
            return _MAX_TERMINAL_TTL_SECONDS
        if not math.isfinite(converted):
            return _MAX_TERMINAL_TTL_SECONDS
        return max(0.0, min(_MAX_TERMINAL_TTL_SECONDS, converted))

    def _resolve_idempotency_locked(
        self,
        command: RefreshCommand,
        now: float,
    ) -> JobRecord | None:
        key = command.idempotency_key
        if key is None or key not in self._idempotency_targets:
            return None

        requested = self._idempotency_requests[key]
        if not self._equivalent_request(requested, command):
            rejected = self._reject_locked(
                command,
                IdempotencyConflictError.error_code,
                "idempotency key conflicts with an existing refresh request",
                now,
            )
            raise IdempotencyConflictError(
                "idempotency key conflicts with an existing refresh request",
                rejected,
            )

        target_id = self._resolve_actual_job_id_locked(
            self._idempotency_targets[key]
        )
        job = self._jobs.get(target_id)
        if job is None:
            del self._idempotency_targets[key]
            del self._idempotency_requests[key]
            return None
        self._idempotency_targets[key] = target_id
        return replace(job)

    def _resolve_command_id_locked(
        self,
        command: RefreshCommand,
        now: float,
    ) -> JobRecord | None:
        if command.id not in self._jobs:
            return None
        requested = self._commands[command.id]
        target_id = self._resolve_actual_job_id_locked(command.id)

        if not self._equivalent_command_identity(requested, command):
            rejected = self._reject_locked(
                command,
                DuplicateCommandConflictError.error_code,
                "refresh command ID conflicts with an existing request",
                now,
            )
            raise DuplicateCommandConflictError(
                "refresh command ID conflicts with an existing request",
                rejected,
            )

        job = self._jobs.get(target_id)
        if job is None:
            return None
        return replace(job)

    @staticmethod
    def _equivalent_request(
        left: RefreshCommand,
        right: RefreshCommand,
    ) -> bool:
        return (
            left.kind == right.kind
            and left.source == right.source
            and left.plugin_id == right.plugin_id
            and left.instance_uuid == right.instance_uuid
            and left.structural_generation == right.structural_generation
            and left.settings_revision == right.settings_revision
            and left.force == right.force
            and left.priority == right.priority
            and left.payload == right.payload
            and left.intent == right.intent
            and left.coalescing_scope == right.coalescing_scope
            and left.allow_prepared_presentation
            == right.allow_prepared_presentation
        )

    @classmethod
    def _equivalent_command_identity(
        cls,
        left: RefreshCommand,
        right: RefreshCommand,
    ) -> bool:
        return (
            cls._equivalent_request(left, right)
            and left.idempotency_key == right.idempotency_key
        )

    def _resolve_actual_job_id_locked(self, job_id: str) -> str:
        visited: set[str] = set()
        while job_id not in visited:
            visited.add(job_id)
            job = self._jobs.get(job_id)
            if (
                job is None
                or job.status is not JobStatus.SUPERSEDED
                or job.superseded_by is None
            ):
                break
            job_id = job.superseded_by
        return job_id

    def _register_idempotency_locked(
        self,
        command: RefreshCommand,
        actual_job_id: str,
    ) -> None:
        key = command.idempotency_key
        if key is None:
            return
        self._idempotency_targets[key] = actual_job_id
        self._idempotency_requests[key] = command

    def _record_absorbed_identity_locked(
        self,
        command: RefreshCommand,
        actual_job_id: str,
        now: float,
    ) -> None:
        if command.id == actual_job_id or command.id in self._jobs:
            return
        target_id = self._resolve_actual_job_id_locked(actual_job_id)
        if target_id not in self._jobs:
            return
        identity = JobRecord.from_command(command, self._wall_clock())
        identity.status = JobStatus.SUPERSEDED
        identity.completed_at = self._wall_clock()
        identity.superseded_by = target_id
        self._jobs[identity.id] = identity
        self._commands[identity.id] = command
        self._record_terminal_locked(identity.id, now)
        self._prune_terminal_locked(now)

    def _has_active_idempotency_capacity_locked(
        self,
        command: RefreshCommand,
    ) -> bool:
        key = command.idempotency_key
        if key is None or key in self._idempotency_targets:
            return True
        return self._active_idempotency_count_locked() < self.alias_limit

    def _active_idempotency_count_locked(self) -> int:
        return sum(
            self._target_is_active_locked(target_id)
            for target_id in self._idempotency_targets.values()
        )

    def _target_is_active_locked(self, target_id: str) -> bool:
        actual_id = self._resolve_actual_job_id_locked(target_id)
        job = self._jobs.get(actual_id)
        return job is not None and job.status in {
            JobStatus.QUEUED,
            JobStatus.RUNNING,
        }

    @staticmethod
    def _invalid_command_reason(command: RefreshCommand) -> str | None:
        if not isinstance(command.kind, CommandKind):
            return "refresh command kind must be a CommandKind"
        if not isinstance(command.source, CommandSource):
            return "refresh command source must be a CommandSource"
        if not isinstance(command.payload, Mapping):
            return "refresh command payload must be a mapping"
        finite_fields = (
            ("enqueued_monotonic", command.enqueued_monotonic),
            ("deadline_monotonic", command.deadline_monotonic),
            ("priority", command.priority),
        )
        for field_name, value in finite_fields:
            try:
                finite = math.isfinite(float(value))
            except (TypeError, ValueError, OverflowError):
                finite = False
            if not finite:
                return f"refresh command {field_name} must be finite"
        return None

    def _at_capacity_locked(self, command: RefreshCommand) -> bool:
        if len(self._pending) >= self.capacity:
            return True
        if self._reserved_eligible(command):
            return False
        background_limit = self.capacity - self.manual_reserved
        return self._non_reserved_depth_locked() >= background_limit

    @staticmethod
    def _reserved_eligible(command: RefreshCommand) -> bool:
        return (
            command.source is CommandSource.MANUAL
            and command.kind is CommandKind.DISPLAY
        )

    def _non_reserved_depth_locked(self) -> int:
        return sum(
            not self._reserved_eligible(self._commands[job_id])
            for job_id in self._pending
        )

    def _coalesce_locked(
        self,
        incoming: RefreshCommand,
        now: float,
    ) -> JobRecord | None:
        if not incoming.instance_uuid:
            return None

        matches = [
            job_id
            for job_id in self._pending
            if self._same_identity(self._commands[job_id], incoming)
        ]
        if not matches:
            return None
        existing_id = min(matches, key=self._pending.__getitem__)
        existing = self._commands[existing_id]

        if incoming.kind is existing.kind:
            if self._compare_revision(
                incoming.settings_revision,
                existing.settings_revision,
            ) > 0:
                return self._supersede_locked(
                    existing_id,
                    self._merged_command(
                        existing,
                        incoming,
                        reuse_existing=False,
                        merge_priority=True,
                        now=now,
                    ),
                    now,
                )
            merged = self._merged_command(
                existing,
                incoming,
                reuse_existing=True,
                merge_priority=True,
                now=now,
            )
            self._commands[existing_id] = merged
            return self._jobs[existing_id]

        if incoming.kind is CommandKind.DISPLAY:
            return self._supersede_locked(
                existing_id,
                self._merged_command(
                    existing,
                    incoming,
                    reuse_existing=False,
                    merge_priority=True,
                    now=now,
                ),
                now,
            )

        merged = self._merged_command(
            existing,
            incoming,
            reuse_existing=True,
            merge_priority=False,
            now=now,
        )
        self._commands[existing_id] = merged
        return self._jobs[existing_id]

    @staticmethod
    def _same_identity(
        existing: RefreshCommand,
        incoming: RefreshCommand,
    ) -> bool:
        return (
            bool(existing.instance_uuid)
            and existing.instance_uuid == incoming.instance_uuid
            and existing.plugin_id == incoming.plugin_id
            and existing.structural_generation == incoming.structural_generation
            and existing.intent == incoming.intent
            and existing.coalescing_scope == incoming.coalescing_scope
            and existing.allow_prepared_presentation
            == incoming.allow_prepared_presentation
        )

    def _merged_command(
        self,
        existing: RefreshCommand,
        incoming: RefreshCommand,
        *,
        reuse_existing: bool,
        merge_priority: bool,
        now: float,
    ) -> RefreshCommand:
        revision_comparison = self._compare_revision(
            incoming.settings_revision,
            existing.settings_revision,
        )
        existing_theme_mode = self._resolved_theme_mode(existing)
        incoming_theme_mode = self._resolved_theme_mode(incoming)
        theme_mode_changed = (
            revision_comparison == 0
            and existing_theme_mode is not None
            and incoming_theme_mode is not None
            and existing_theme_mode != incoming_theme_mode
        )
        if revision_comparison > 0:
            selected_revision = incoming.settings_revision
            selected_payload = incoming.payload
        elif revision_comparison < 0:
            selected_revision = existing.settings_revision
            selected_payload = existing.payload
        elif reuse_existing:
            selected_revision = existing.settings_revision
            selected_payload = existing.payload
        else:
            selected_revision = incoming.settings_revision
            selected_payload = incoming.payload

        base = existing if reuse_existing else incoming
        priority = base.priority
        source = base.source
        if merge_priority:
            if incoming.priority > existing.priority:
                priority = incoming.priority
                source = incoming.source
            elif incoming.priority < existing.priority:
                priority = existing.priority
                source = existing.source
            else:
                priority = existing.priority
                source = self._more_urgent_source(existing.source, incoming.source)

        manual_display_owner = next(
            (
                candidate
                for candidate in (incoming, existing)
                if candidate.source is CommandSource.MANUAL
                and candidate.kind is CommandKind.DISPLAY
            ),
            None,
        )
        if revision_comparison == 0:
            manual_exact_owner = next(
                (
                    candidate
                    for candidate in (incoming, existing)
                    if candidate.source is CommandSource.MANUAL
                    and (
                        candidate.kind is CommandKind.DISPLAY
                        or candidate.intent is RefreshIntent.DATA_REFRESH
                    )
                    and candidate.payload.get("require_active") is False
                ),
                None,
            )
            if theme_mode_changed:
                selected_payload = self._merged_theme_transition_payload(
                    existing,
                    incoming,
                    manual_display_owner,
                    manual_exact_owner,
                )
            elif manual_exact_owner is not None:
                # Manual display or DATA_REFRESH of an exact inactive-playlist
                # revision is a stronger admission requirement than an older
                # scheduled probe. Keep its immutable payload when jobs coalesce.
                selected_payload = manual_exact_owner.payload
            elif manual_display_owner is not None:
                # A manual display never acknowledges the scheduler's persisted
                # shuffle bag, even when it targets the same immutable revision.
                selected_payload = manual_display_owner.payload

        if (
            manual_display_owner is not None
            and selected_payload.get("automatic_rotation") is True
        ):
            without_rotation_ack = dict(selected_payload)
            without_rotation_ack.pop("automatic_rotation", None)
            selected_payload = freeze_payload(without_rotation_ack)

        deadline = existing.deadline_monotonic
        if (
            incoming.deadline_monotonic > now
            and incoming.deadline_monotonic > deadline
        ):
            deadline = incoming.deadline_monotonic
        if deadline <= now and incoming.deadline_monotonic > now:
            deadline = incoming.deadline_monotonic

        kind = existing.kind if reuse_existing else incoming.kind
        return replace(
            base,
            kind=kind,
            settings_revision=selected_revision,
            force=existing.force or incoming.force,
            priority=priority,
            source=source,
            deadline_monotonic=deadline,
            payload=selected_payload,
        )

    @staticmethod
    def _merged_theme_transition_payload(
        existing: RefreshCommand,
        incoming: RefreshCommand,
        manual_display_owner: RefreshCommand | None,
        manual_exact_owner: RefreshCommand | None,
    ):
        """Merge theme metadata while retaining ordinary manual display intent."""
        payload_owner = manual_exact_owner or manual_display_owner or incoming
        merged = dict(payload_owner.payload)

        existing_theme_context = existing.payload.get("theme_context")
        incoming_theme_context = incoming.payload.get("theme_context")
        combined_theme_context = None
        if existing.kind is CommandKind.DISPLAY and isinstance(
            existing_theme_context,
            Mapping,
        ):
            combined_theme_context = dict(existing_theme_context)
        if isinstance(incoming_theme_context, Mapping):
            if combined_theme_context is None:
                combined_theme_context = {}
            combined_theme_context.update(incoming_theme_context)
        incoming_resolved_context = incoming.payload.get("resolved_theme_context")
        if (
            combined_theme_context is not None
            and isinstance(incoming_resolved_context, Mapping)
        ):
            incoming_resolved_mode = incoming_resolved_context.get("mode")
            if incoming_resolved_mode in {"day", "night"}:
                combined_theme_context["mode"] = incoming_resolved_mode
        if combined_theme_context is not None:
            merged["theme_context"] = combined_theme_context

        if (
            manual_display_owner is not None
            and manual_display_owner.payload.get("theme_render_only") is not True
        ):
            merged.pop("theme_render_only", None)
            merged.pop("expected_displayed_instance_uuid", None)
        else:
            theme_display = next(
                (
                    candidate
                    for candidate in (incoming, existing)
                    if candidate.kind is CommandKind.DISPLAY
                    and candidate.payload.get("theme_render_only") is True
                ),
                None,
            )
            if theme_display is not None:
                merged["theme_render_only"] = True
                if "expected_displayed_instance_uuid" in theme_display.payload:
                    merged["expected_displayed_instance_uuid"] = (
                        theme_display.payload["expected_displayed_instance_uuid"]
                    )

        if incoming_resolved_context is not None:
            merged["resolved_theme_context"] = incoming_resolved_context
        return freeze_payload(merged)

    @staticmethod
    def _resolved_theme_mode(command: RefreshCommand) -> str | None:
        for key in ("resolved_theme_context", "theme_context"):
            context = command.payload.get(key)
            if not isinstance(context, Mapping):
                continue
            mode = context.get("mode")
            if mode in {"day", "night"}:
                return mode
        return None

    @staticmethod
    def _compare_revision(
        left: int | None,
        right: int | None,
    ) -> int:
        if left is None:
            return 0 if right is None else -1
        if right is None:
            return 1
        return (left > right) - (left < right)

    @staticmethod
    def _more_urgent_source(
        left: CommandSource,
        right: CommandSource,
    ) -> CommandSource:
        if _SOURCE_URGENCY.get(right, 0) > _SOURCE_URGENCY.get(left, 0):
            return right
        return left

    def _supersede_locked(
        self,
        old_job_id: str,
        new_command: RefreshCommand,
        now: float,
    ) -> JobRecord:
        old_job = self._jobs[old_job_id]
        old_job.status = JobStatus.SUPERSEDED
        old_job.completed_at = self._wall_clock()
        old_job.superseded_by = new_command.id
        self._cancel_events[old_job_id].set()
        del self._pending[old_job_id]
        self._record_terminal_locked(old_job_id, now)
        self._superseded_total += 1

        new_job = JobRecord.from_command(new_command, self._wall_clock())
        self._jobs[new_job.id] = new_job
        self._commands[new_job.id] = new_command
        self._cancel_events[new_job.id] = threading.Event()
        self._sequence += 1
        self._pending[new_job.id] = self._sequence
        for key, target_id in tuple(self._idempotency_targets.items()):
            if target_id == old_job_id:
                self._idempotency_targets[key] = new_job.id
        self._prune_terminal_locked(now)
        return new_job

    def _reject_locked(
        self,
        command: RefreshCommand,
        error_code: str,
        error: str,
        now: float,
    ) -> JobRecord:
        if (
            command.id in self._jobs
            or command.id in self._commands
        ):
            command = replace(command, id=uuid4().hex)
        job = JobRecord.from_command(command, self._wall_clock())
        job.status = JobStatus.REJECTED
        job.completed_at = self._wall_clock()
        job.error_code = error_code
        job.error = error
        self._jobs[job.id] = job
        self._commands[job.id] = command
        self._cancel_events[job.id] = threading.Event()
        self._cancel_events[job.id].set()
        self._rejected_total += 1
        if (
            command.idempotency_key is not None
            and command.idempotency_key not in self._idempotency_targets
        ):
            self._register_idempotency_locked(command, job.id)
        result = replace(job)
        self._record_terminal_locked(job.id, now)
        self._prune_terminal_locked(now)
        return result

    def _cancel_expired_submission_locked(
        self,
        command: RefreshCommand,
        now: float,
    ) -> JobRecord:
        job = JobRecord.from_command(command, self._wall_clock())
        job.status = JobStatus.CANCELED
        job.completed_at = self._wall_clock()
        job.error_code = "deadline_expired"
        job.error = "refresh command deadline expired"
        self._jobs[job.id] = job
        self._commands[job.id] = command
        self._cancel_events[job.id] = threading.Event()
        self._cancel_events[job.id].set()
        if (
            command.idempotency_key is not None
            and command.idempotency_key not in self._idempotency_targets
        ):
            self._register_idempotency_locked(command, job.id)
        result = replace(job)
        self._record_terminal_locked(job.id, now)
        self._prune_terminal_locked(now)
        self._publish_change_locked()
        return result

    def _select_pending_locked(self) -> str:
        highest_priority = max(
            self._commands[job_id].priority for job_id in self._pending
        )
        if self._fairness_priority != highest_priority:
            self._fairness_priority = highest_priority
            self._high_priority_streak = 0

        lower_priorities = [
            self._commands[job_id].priority
            for job_id in self._pending
            if self._commands[job_id].priority < highest_priority
        ]
        if self._high_priority_streak >= 3 and lower_priorities:
            selected_priority = max(lower_priorities)
            self._high_priority_streak = 0
        else:
            selected_priority = highest_priority
            self._high_priority_streak += 1

        candidates = [
            job_id
            for job_id in self._pending
            if self._commands[job_id].priority == selected_priority
        ]
        return min(candidates, key=self._pending.__getitem__)

    def _expire_pending_locked(self, now: float) -> None:
        expired = sorted(
            (
                job_id
                for job_id in self._pending
                if self._commands[job_id].deadline_monotonic <= now
            ),
            key=self._pending.__getitem__,
        )
        if not expired:
            return
        completed_at = self._wall_clock()
        for job_id in expired:
            del self._pending[job_id]
            job = self._jobs[job_id]
            job.status = JobStatus.CANCELED
            job.completed_at = completed_at
            job.error_code = "deadline_expired"
            job.error = "refresh command deadline expired"
            self._cancel_events[job_id].set()
            self._record_terminal_locked(job_id, now)
        self._prune_terminal_locked(now)

    def _cancel_matching_locked(self, predicate, now: float) -> int:
        completed_at = self._wall_clock()
        affected = 0
        queued_matches = sorted(
            (
                job_id
                for job_id in self._pending
                if predicate(self._commands[job_id])
            ),
            key=self._pending.__getitem__,
        )
        for job_id in queued_matches:
            del self._pending[job_id]
            job = self._jobs[job_id]
            job.status = JobStatus.CANCELED
            job.completed_at = completed_at
            self._cancel_events[job_id].set()
            self._record_terminal_locked(job_id, now)
            affected += 1

        for job_id, job in self._jobs.items():
            if (
                job.status is JobStatus.RUNNING
                and job.cancel_requested_at is None
                and predicate(self._commands[job_id])
            ):
                job.request_cancel(completed_at)
                self._cancel_events[job_id].set()
                affected += 1
        return affected

    def _record_terminal_locked(self, job_id: str, now: float) -> None:
        if job_id in self._terminal_order:
            return
        self._terminal_sequence += 1
        self._terminal_order[job_id] = (now, self._terminal_sequence)

    def _prune_terminal_locked(self, now: float) -> None:
        while self._terminal_order:
            oldest_id = min(
                self._terminal_order,
                key=self._terminal_order.__getitem__,
            )
            completed_monotonic, _sequence = self._terminal_order[oldest_id]
            over_limit = len(self._terminal_order) > self.terminal_limit
            ttl_expired = (
                now - completed_monotonic > self.terminal_ttl_seconds
            )
            if not over_limit and not ttl_expired:
                break
            self._prune_terminal_job_locked(oldest_id)

    def _prune_terminal_job_locked(self, job_id: str) -> None:
        dependents = [
            dependent_id
            for dependent_id, job in self._jobs.items()
            if (
                job.status is JobStatus.SUPERSEDED
                and job.superseded_by == job_id
                and dependent_id in self._terminal_order
            )
        ]
        for dependent_id in dependents:
            self._prune_terminal_job_locked(dependent_id)

        self._terminal_order.pop(job_id, None)
        self._running.discard(job_id)
        self._jobs.pop(job_id, None)
        self._commands.pop(job_id, None)
        self._cancel_events.pop(job_id, None)
        for key, target_id in tuple(self._idempotency_targets.items()):
            if target_id == job_id:
                del self._idempotency_targets[key]
                del self._idempotency_requests[key]
