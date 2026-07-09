from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
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


@dataclass(frozen=True)
class QueueEntry:
    command: RefreshCommand
    job: JobRecord


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
        self.terminal_limit = max(0, int(terminal_limit))
        self.terminal_ttl_seconds = max(0.0, float(terminal_ttl_seconds))
        self.alias_limit = max(1, min(4096, int(alias_limit)))
        self._clock = clock
        self._wall_clock = wall_clock
        self._condition = threading.Condition()
        self._pending: dict[str, int] = {}
        self._jobs: dict[str, JobRecord] = {}
        self._commands: dict[str, RefreshCommand] = {}
        self._command_targets: dict[str, str] = {}
        self._command_requests: dict[str, RefreshCommand] = {}
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
                if self._has_alias_capacity_locked(normalized, include_key=False):
                    self._register_command_identity_locked(normalized, idempotent.id)
                return idempotent

            invalid_reason = self._invalid_command_reason(normalized)
            if invalid_reason is not None:
                rejected = self._reject_locked(
                    normalized,
                    InvalidRefreshCommandError.error_code,
                    invalid_reason,
                    now,
                )
                raise InvalidRefreshCommandError(invalid_reason, rejected)

            if not self._accepting:
                rejected = self._reject_locked(
                    normalized,
                    QueueStoppingError.error_code,
                    "refresh service is stopping",
                    now,
                )
                raise QueueStoppingError("refresh service is stopping", rejected)

            if not self._has_alias_capacity_locked(normalized):
                rejected = self._reject_locked(
                    normalized,
                    QueueFullError.error_code,
                    "refresh queue metadata is full",
                    now,
                    register_identity=False,
                )
                raise QueueFullError("refresh queue metadata is full", rejected)

            coalesced = self._coalesce_locked(normalized, now)
            if coalesced is not None:
                self._register_command_identity_locked(normalized, coalesced.id)
                self._register_idempotency_locked(normalized, coalesced.id)
                self._condition.notify_all()
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
            self._sequence += 1
            self._pending[job.id] = self._sequence
            self._register_command_identity_locked(normalized, job.id)
            self._register_idempotency_locked(normalized, job.id)
            self._condition.notify_all()
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
                if self._pending:
                    job_id = self._select_pending_locked()
                    del self._pending[job_id]
                    job = self._jobs[job_id]
                    job.mark_running(self._wall_clock())
                    return QueueEntry(self._commands[job_id], replace(job))

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
            result = replace(job)
            self._record_terminal_locked(job.id, now)
            self._prune_terminal_locked(now)
            self._condition.notify_all()
            return result

    def get_job(self, job_id: str) -> JobRecord | None:
        with self._condition:
            now = self._clock()
            self._prune_terminal_locked(now)
            self._expire_pending_locked(now)
            job = self._jobs.get(job_id)
            return replace(job) if job is not None else None

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
            self._condition.notify_all()
            return affected

    def begin_quiesce(self) -> int:
        with self._condition:
            now = self._clock()
            self._prune_terminal_locked(now)
            self._expire_pending_locked(now)
            self._accepting = False
            affected = self._cancel_matching_locked(lambda _command: True, now)
            self._prune_terminal_locked(now)
            self._condition.notify_all()
            return affected

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

    @staticmethod
    def _normalize_command(command: RefreshCommand) -> RefreshCommand:
        return replace(command, payload=freeze_payload(command.payload))

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
        if command.id not in self._command_targets:
            return None

        requested = self._command_requests[command.id]
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

        target_id = self._resolve_actual_job_id_locked(
            self._command_targets[command.id]
        )
        job = self._jobs.get(target_id)
        if job is None:
            del self._command_targets[command.id]
            del self._command_requests[command.id]
            return None
        self._command_targets[command.id] = target_id
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

    def _register_command_identity_locked(
        self,
        command: RefreshCommand,
        actual_job_id: str,
    ) -> None:
        self._command_targets[command.id] = actual_job_id
        self._command_requests[command.id] = command

    def _has_alias_capacity_locked(
        self,
        command: RefreshCommand,
        *,
        include_key: bool = True,
    ) -> bool:
        needed = int(command.id not in self._command_targets)
        if (
            include_key
            and command.idempotency_key is not None
            and command.idempotency_key not in self._idempotency_targets
        ):
            needed += 1
        return self._alias_count_locked() + needed <= self.alias_limit

    def _alias_count_locked(self) -> int:
        return len(self._command_targets) + len(self._idempotency_targets)

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
        del self._pending[old_job_id]
        self._record_terminal_locked(old_job_id, now)
        self._superseded_total += 1

        new_job = JobRecord.from_command(new_command, self._wall_clock())
        self._jobs[new_job.id] = new_job
        self._commands[new_job.id] = new_command
        self._sequence += 1
        self._pending[new_job.id] = self._sequence
        for key, target_id in tuple(self._idempotency_targets.items()):
            if target_id == old_job_id:
                self._idempotency_targets[key] = new_job.id
        for command_id, target_id in tuple(self._command_targets.items()):
            if target_id == old_job_id:
                self._command_targets[command_id] = new_job.id
        self._prune_terminal_locked(now)
        return new_job

    def _reject_locked(
        self,
        command: RefreshCommand,
        error_code: str,
        error: str,
        now: float,
        *,
        register_identity: bool = True,
    ) -> JobRecord:
        if (
            command.id in self._jobs
            or command.id in self._commands
            or command.id in self._command_targets
        ):
            command = replace(command, id=uuid4().hex)
        job = JobRecord.from_command(command, self._wall_clock())
        job.status = JobStatus.REJECTED
        job.completed_at = self._wall_clock()
        job.error_code = error_code
        job.error = error
        self._jobs[job.id] = job
        self._commands[job.id] = command
        self._rejected_total += 1
        if (
            register_identity
            and self._has_alias_capacity_locked(command)
        ):
            self._register_command_identity_locked(command, job.id)
            if (
                command.idempotency_key is not None
                and command.idempotency_key not in self._idempotency_targets
            ):
                self._register_idempotency_locked(command, job.id)
        result = replace(job)
        self._record_terminal_locked(job.id, now)
        self._prune_terminal_locked(now)
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
            self._record_terminal_locked(job_id, now)
            affected += 1

        for job_id, job in self._jobs.items():
            if (
                job.status is JobStatus.RUNNING
                and job.cancel_requested_at is None
                and predicate(self._commands[job_id])
            ):
                job.request_cancel(completed_at)
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
        self._jobs.pop(job_id, None)
        self._commands.pop(job_id, None)
        for command_id, target_id in tuple(self._command_targets.items()):
            if target_id == job_id:
                del self._command_targets[command_id]
                del self._command_requests[command_id]
        for key, target_id in tuple(self._idempotency_targets.items()):
            if target_id == job_id:
                del self._idempotency_targets[key]
                del self._idempotency_requests[key]
