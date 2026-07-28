"""Ian: resource-aware staged work scheduling for constrained devices."""

from __future__ import annotations

from collections.abc import Mapping as MappingABC
from dataclasses import dataclass, field, replace
from enum import Enum, IntEnum
import math
import threading
import time
from typing import Any, Callable, Mapping

from .refresh_contracts import (
    TaskCancelled,
    TaskDeadlineExceeded,
    freeze_payload,
)


class IanPriority(IntEnum):
    BACKGROUND = 100
    SCHEDULED = 200
    LIVE = 300
    MANUAL = 400
    DISPLAY = 500


class IanStageKind(str, Enum):
    FETCH = "fetch"
    NORMALIZE = "normalize"
    RENDER = "render"
    ENCODE = "encode"
    CACHE = "cache"
    DISPLAY = "display"


@dataclass(frozen=True)
class IanStage:
    name: str
    kind: IanStageKind
    claim: "IanResourceClaim | None" = None


@dataclass(frozen=True)
class IanResourceClaim:
    memory_mb: float
    swap_growth_percent: float = 0.0
    pressure_safe: bool = False


@dataclass(frozen=True)
class IanRequest:
    request_id: str
    plugin_id: str
    instance_uuid: str | None
    structural_generation: int | None
    settings_revision: int | None
    intent: str
    plan_token: str
    priority: IanPriority
    deadline_monotonic: float
    stages: tuple[IanStage, ...]
    payload: Mapping[str, Any] = field(default_factory=dict, compare=False, repr=False)


@dataclass(frozen=True)
class IanResourceSample:
    available_mb: float | None
    swap_percent: float | None


@dataclass(frozen=True)
class IanExecutionResult:
    result: Any = None
    checkpoint: "IanCheckpoint | None" = None
    peak_memory_mb: float | None = None
    swap_growth_percent: float | None = None


@dataclass(frozen=True)
class IanCheckpoint:
    token: str
    size_bytes: int
    digest: str | None = None
    request_id: str | None = None
    plan_token: str | None = None
    stage_name: str | None = None
    predecessor_token: str | None = None


class IanOfferStatus(str, Enum):
    ACCEPTED = "accepted"
    COALESCED = "coalesced"
    SUPERSEDED = "superseded"
    REJECTED = "rejected"


@dataclass(frozen=True)
class IanOffer:
    status: IanOfferStatus
    request_id: str
    queue_depth: int
    reason: str | None = None
    superseded_request_id: str | None = None


class IanTurnStatus(str, Enum):
    IDLE = "idle"
    CHECKPOINTED = "checkpointed"
    SUCCEEDED = "succeeded"
    DEFERRED = "deferred"
    RESOURCE_UNKNOWN = "resource_unknown"
    DEADLINE_EXPIRED = "deadline_expired"
    FAILED = "failed"
    CANCELED = "canceled"
    DRAINING = "draining"
    COOLDOWN = "cooldown"


@dataclass(frozen=True)
class IanTurn:
    status: IanTurnStatus
    queue_depth: int
    request: IanRequest | None = None
    stage: IanStage | None = None
    result: Any = None
    remaining_stages: int = 0
    reason: str | None = None
    resource_sample: IanResourceSample | None = None
    reserved_memory_mb: float | None = None
    reserved_swap_percent: float | None = None
    checkpoint: IanCheckpoint | None = None
    error: BaseException | None = field(
        default=None,
        compare=False,
        repr=False,
    )
    drain_deadline_monotonic: float | None = None
    cooldown_until_monotonic: float | None = None


@dataclass
class _IanWork:
    request: IanRequest
    stage_index: int = 0
    previous_checkpoint: IanCheckpoint | None = None
    offered_sequence: int = 0
    last_run_sequence: int = 0
    last_progress_monotonic: float = 0.0
    cooldown_until_monotonic: float = 0.0


@dataclass
class _IanDrain:
    work: _IanWork
    deadline_monotonic: float


@dataclass
class _IanObservedCost:
    peak_memory_mb: float | None = None
    swap_growth_percent: float | None = None


class Ian:
    """Run at most one checkpointed stage on every turn."""

    _HARD_MIN_AVAILABLE_MB = 70.0
    _START_MAX_SWAP_PERCENT = 70.0
    _HARD_MAX_SWAP_PERCENT = 75.0
    _CONSERVATIVE_CLAIM = IanResourceClaim(
        memory_mb=128.0,
        swap_growth_percent=5.0,
    )

    def __init__(
        self,
        *,
        clock: Callable[[], float] = time.monotonic,
        resource_sampler: Callable[[], IanResourceSample],
        executor: Callable[[IanRequest, IanStage, Any], IanExecutionResult],
        max_priority_streak: int = 3,
        deadline_urgency_seconds: float = 30.0,
        capacity: int = 16,
        urgent_reserved: int = 2,
        max_checkpoint_bytes: int = 1_500_000,
        cancellation_probe: Callable[[IanRequest], bool] | None = None,
        starvation_seconds: float = 30.0 * 60.0,
        drain_seconds: float = 90.0,
        cooldown_seconds: float = 5.0 * 60.0,
        adaptive_safety_factor: float = 1.25,
        adaptive_alpha: float = 0.5,
    ):
        self._serial_lock = threading.Lock()
        self._clock = clock
        self._resource_sampler = resource_sampler
        self._executor = executor
        self._pending: list[_IanWork] = []
        self._max_priority_streak = max(1, min(100, int(max_priority_streak)))
        self._deadline_urgency_seconds = max(
            0.0,
            min(3600.0, float(deadline_urgency_seconds)),
        )
        self._capacity = max(1, min(128, int(capacity)))
        self._urgent_reserved = max(
            0,
            min(self._capacity, int(urgent_reserved)),
        )
        self._max_checkpoint_bytes = max(
            1,
            min(16 * 1024 * 1024, int(max_checkpoint_bytes)),
        )
        self._cancellation_probe = (
            (lambda request: False)
            if cancellation_probe is None
            else cancellation_probe
        )
        self._starvation_seconds = max(
            0.0,
            min(24.0 * 60.0 * 60.0, float(starvation_seconds)),
        )
        self._drain_seconds = max(
            0.0,
            min(5.0 * 60.0, float(drain_seconds)),
        )
        self._cooldown_seconds = max(
            0.0,
            min(60.0 * 60.0, float(cooldown_seconds)),
        )
        self._adaptive_safety_factor = max(
            1.0,
            min(3.0, float(adaptive_safety_factor)),
        )
        self._adaptive_alpha = max(
            0.05,
            min(1.0, float(adaptive_alpha)),
        )
        self._observed_costs: dict[
            tuple[str, str, IanStageKind],
            _IanObservedCost,
        ] = {}
        self._offer_sequence = 0
        self._turn_sequence = 0
        self._last_priority: IanPriority | None = None
        self._priority_streak = 0
        self._active_drain: _IanDrain | None = None

    def offer(self, request: IanRequest) -> IanOffer:
        with self._serial_lock:
            return self._offer_locked(request)

    def _offer_locked(self, request: IanRequest) -> IanOffer:
        now = self._clock()
        invalid_reason = self._invalid_request_reason(request)
        if invalid_reason is not None:
            return IanOffer(
                status=IanOfferStatus.REJECTED,
                request_id=getattr(request, "request_id", "invalid"),
                queue_depth=len(self._pending),
                reason="ian_invalid_request",
            )
        try:
            request = replace(
                request,
                payload=freeze_payload(request.payload),
            )
        except (TypeError, ValueError, RecursionError):
            return IanOffer(
                status=IanOfferStatus.REJECTED,
                request_id=request.request_id,
                queue_depth=len(self._pending),
                reason="ian_invalid_request",
            )
        if request.deadline_monotonic <= now:
            return IanOffer(
                status=IanOfferStatus.REJECTED,
                request_id=request.request_id,
                queue_depth=len(self._pending),
                reason="ian_deadline_expired",
            )
        identity = self._request_identity(request)
        scope = self._request_scope(request)
        for index, work in enumerate(self._pending):
            if self._request_scope(work.request) != scope:
                continue
            if self._request_identity(work.request) != identity:
                plan_changed = (
                    self._revision(request) == self._revision(work.request)
                    and request.plan_token != work.request.plan_token
                )
                if (
                    self._revision(request) > self._revision(work.request)
                    or plan_changed
                ):
                    superseded_request_id = work.request.request_id
                    self._offer_sequence += 1
                    self._clear_drain_for(work)
                    self._pending[index] = _IanWork(
                        request=request,
                        offered_sequence=self._offer_sequence,
                        last_progress_monotonic=now,
                    )
                    return IanOffer(
                        status=IanOfferStatus.SUPERSEDED,
                        request_id=request.request_id,
                        queue_depth=len(self._pending),
                        reason=(
                            "ian_plan_changed" if plan_changed else None
                        ),
                        superseded_request_id=superseded_request_id,
                    )
                return IanOffer(
                    status=IanOfferStatus.REJECTED,
                    request_id=request.request_id,
                    queue_depth=len(self._pending),
                    reason="ian_stale_revision",
                )
            work.request = replace(
                work.request,
                priority=max(work.request.priority, request.priority),
                deadline_monotonic=min(
                    work.request.deadline_monotonic,
                    request.deadline_monotonic,
                ),
            )
            return IanOffer(
                status=IanOfferStatus.COALESCED,
                request_id=work.request.request_id,
                queue_depth=len(self._pending),
            )
        if self._queue_is_full_for(request):
            return IanOffer(
                status=IanOfferStatus.REJECTED,
                request_id=request.request_id,
                queue_depth=len(self._pending),
                reason="ian_queue_full",
            )
        self._offer_sequence += 1
        self._pending.append(
            _IanWork(
                request=request,
                offered_sequence=self._offer_sequence,
                last_progress_monotonic=now,
            )
        )
        return IanOffer(
            status=IanOfferStatus.ACCEPTED,
            request_id=request.request_id,
            queue_depth=len(self._pending),
        )

    def run_turn(self) -> IanTurn:
        with self._serial_lock:
            return self._run_turn_locked()

    def _run_turn_locked(self) -> IanTurn:
        if not self._pending:
            return IanTurn(IanTurnStatus.IDLE, queue_depth=0)

        now = self._clock()
        expired = [
            work
            for work in self._pending
            if work.request.deadline_monotonic <= now
        ]
        if expired:
            work = min(
                expired,
                key=lambda candidate: (
                    candidate.request.deadline_monotonic,
                    candidate.offered_sequence,
                ),
            )
            self._remove_work(work)
            stage = work.request.stages[work.stage_index]
            return IanTurn(
                IanTurnStatus.DEADLINE_EXPIRED,
                queue_depth=len(self._pending),
                request=work.request,
                stage=stage,
                remaining_stages=len(work.request.stages) - work.stage_index,
                reason="ian_deadline_expired",
            )

        canceled = self._find_canceled_work()
        if canceled is not None:
            work, reason, error = canceled
            self._remove_work(work)
            return IanTurn(
                IanTurnStatus.CANCELED,
                queue_depth=len(self._pending),
                request=work.request,
                stage=work.request.stages[work.stage_index],
                remaining_stages=len(work.request.stages) - work.stage_index,
                reason=reason,
                error=error,
            )

        ordered = self._ordered_work(now)
        sample = self._sample_resources()
        work = None
        if (
            self._active_drain is not None
            and not any(
                candidate is self._active_drain.work
                for candidate in self._pending
            )
        ):
            self._active_drain = None
        if self._active_drain is not None:
            drain = self._active_drain
            target = drain.work
            target_stage = target.request.stages[target.stage_index]
            target_claim = self._claim_for(target)
            if now >= drain.deadline_monotonic:
                target.cooldown_until_monotonic = now + self._cooldown_seconds
                self._active_drain = None
                return IanTurn(
                    IanTurnStatus.COOLDOWN,
                    queue_depth=len(self._pending),
                    request=target.request,
                    stage=target_stage,
                    remaining_stages=(
                        len(target.request.stages) - target.stage_index
                    ),
                    reason="ian_starvation_drain_expired",
                    resource_sample=sample,
                    reserved_memory_mb=target_claim.memory_mb,
                    reserved_swap_percent=target_claim.swap_growth_percent,
                    cooldown_until_monotonic=(
                        target.cooldown_until_monotonic
                    ),
                )
            work = next(
                (
                    candidate
                    for candidate in ordered
                    if candidate is not target
                    and candidate.request.priority >= IanPriority.MANUAL
                    and (
                        (
                            sample is None
                            and self._stage_is_pressure_safe(
                                candidate.request.stages[
                                    candidate.stage_index
                                ]
                            )
                        )
                        or (
                            sample is not None
                            and self._can_reserve(
                                candidate.request.stages[
                                    candidate.stage_index
                                ],
                                self._claim_for(candidate),
                                sample,
                            )
                        )
                    )
                ),
                None,
            )
            if (
                work is None
                and sample is not None
                and self._can_reserve(
                    target_stage,
                    target_claim,
                    sample,
                )
            ):
                work = target
            if work is None:
                work = next(
                    (
                        candidate
                        for candidate in ordered
                        if candidate is not target
                        and self._stage_is_pressure_safe(
                            candidate.request.stages[candidate.stage_index]
                        )
                        and (
                            sample is None
                            or self._can_reserve(
                                candidate.request.stages[
                                    candidate.stage_index
                                ],
                                self._claim_for(candidate),
                                sample,
                            )
                        )
                    ),
                    None,
                )
                if work is None:
                    return IanTurn(
                        IanTurnStatus.DRAINING,
                        queue_depth=len(self._pending),
                        request=target.request,
                        stage=target_stage,
                        remaining_stages=(
                            len(target.request.stages) - target.stage_index
                        ),
                        reason="ian_starvation_draining",
                        resource_sample=sample,
                        reserved_memory_mb=target_claim.memory_mb,
                        reserved_swap_percent=(
                            target_claim.swap_growth_percent
                        ),
                        drain_deadline_monotonic=drain.deadline_monotonic,
                    )
        elif sample is not None:
            starved = [
                candidate
                for candidate in ordered
                if now >= candidate.cooldown_until_monotonic
                and (
                    now - candidate.last_progress_monotonic
                    >= self._starvation_seconds
                )
                and not self._stage_is_pressure_safe(
                    candidate.request.stages[candidate.stage_index]
                )
                and not self._can_reserve(
                    candidate.request.stages[candidate.stage_index],
                    self._claim_for(candidate),
                    sample,
                )
            ]
            if starved:
                work = next(
                    (
                        candidate
                        for candidate in ordered
                        if candidate.request.priority >= IanPriority.MANUAL
                        and self._can_reserve(
                            candidate.request.stages[candidate.stage_index],
                            self._claim_for(candidate),
                            sample,
                        )
                    ),
                    None,
                )
                if work is None:
                    target = min(
                        starved,
                        key=lambda candidate: (
                            candidate.last_progress_monotonic,
                            candidate.request.deadline_monotonic,
                            candidate.offered_sequence,
                        ),
                    )
                    self._active_drain = _IanDrain(
                        work=target,
                        deadline_monotonic=now + self._drain_seconds,
                    )
                    target_stage = target.request.stages[target.stage_index]
                    target_claim = self._claim_for(target)
                    return IanTurn(
                        IanTurnStatus.DRAINING,
                        queue_depth=len(self._pending),
                        request=target.request,
                        stage=target_stage,
                        remaining_stages=(
                            len(target.request.stages) - target.stage_index
                        ),
                        reason="ian_starvation_drain_started",
                        resource_sample=sample,
                        reserved_memory_mb=target_claim.memory_mb,
                        reserved_swap_percent=(
                            target_claim.swap_growth_percent
                        ),
                        drain_deadline_monotonic=(
                            self._active_drain.deadline_monotonic
                        ),
                    )
        if work is None and sample is None:
            work = next(
                (
                    candidate
                    for candidate in ordered
                    if self._stage_is_pressure_safe(
                        candidate.request.stages[candidate.stage_index]
                    )
                ),
                None,
            )
            if work is None:
                work = ordered[0]
                stage = work.request.stages[work.stage_index]
                return IanTurn(
                    IanTurnStatus.RESOURCE_UNKNOWN,
                    queue_depth=len(self._pending),
                    request=work.request,
                    stage=stage,
                    remaining_stages=(
                        len(work.request.stages) - work.stage_index
                    ),
                    reason="ian_resource_metrics_unknown",
                )
        elif work is None:
            work = next(
                (
                    candidate
                    for candidate in ordered
                    if self._can_reserve(
                        candidate.request.stages[candidate.stage_index],
                        self._claim_for(candidate),
                        sample,
                    )
                ),
                None,
            )
            if work is None:
                work = ordered[0]
                stage = work.request.stages[work.stage_index]
                claim = self._claim_for(work)
                return IanTurn(
                    IanTurnStatus.DEFERRED,
                    queue_depth=len(self._pending),
                    request=work.request,
                    stage=stage,
                    remaining_stages=(
                        len(work.request.stages) - work.stage_index
                    ),
                    reason="ian_resource_reservation_unavailable",
                    resource_sample=sample,
                    reserved_memory_mb=claim.memory_mb,
                    reserved_swap_percent=claim.swap_growth_percent,
                )
        stage = work.request.stages[work.stage_index]
        claim = self._claim_for(work)
        try:
            execution = self._executor(
                work.request,
                stage,
                work.previous_checkpoint,
            )
            if not isinstance(execution, IanExecutionResult):
                raise TypeError(
                    "Ian executor must return IanExecutionResult"
                )
        except TaskDeadlineExceeded as exc:
            self._record_run(work)
            self._remove_work(work)
            return IanTurn(
                IanTurnStatus.DEADLINE_EXPIRED,
                queue_depth=len(self._pending),
                request=work.request,
                stage=stage,
                remaining_stages=len(work.request.stages) - work.stage_index,
                reason="ian_execution_deadline_exceeded",
                resource_sample=sample,
                reserved_memory_mb=claim.memory_mb,
                reserved_swap_percent=claim.swap_growth_percent,
                error=exc,
            )
        except TaskCancelled as exc:
            self._record_run(work)
            self._remove_work(work)
            return IanTurn(
                IanTurnStatus.CANCELED,
                queue_depth=len(self._pending),
                request=work.request,
                stage=stage,
                remaining_stages=len(work.request.stages) - work.stage_index,
                reason="ian_execution_canceled",
                resource_sample=sample,
                reserved_memory_mb=claim.memory_mb,
                reserved_swap_percent=claim.swap_growth_percent,
                error=exc,
            )
        except Exception as exc:
            self._record_run(work)
            self._remove_work(work)
            return IanTurn(
                IanTurnStatus.FAILED,
                queue_depth=len(self._pending),
                request=work.request,
                stage=stage,
                remaining_stages=len(work.request.stages) - work.stage_index,
                reason="ian_execution_failed",
                resource_sample=sample,
                reserved_memory_mb=claim.memory_mb,
                reserved_swap_percent=claim.swap_growth_percent,
                error=exc,
            )
        if work.request.deadline_monotonic <= self._clock():
            self._record_run(work)
            self._remove_work(work)
            return IanTurn(
                IanTurnStatus.DEADLINE_EXPIRED,
                queue_depth=len(self._pending),
                request=work.request,
                stage=stage,
                remaining_stages=len(work.request.stages) - work.stage_index,
                reason="ian_deadline_expired_after_execution",
                resource_sample=sample,
                reserved_memory_mb=claim.memory_mb,
                reserved_swap_percent=claim.swap_growth_percent,
            )
        try:
            canceled_after_execution = bool(
                self._cancellation_probe(work.request)
            )
            cancellation_error = None
        except Exception as exc:
            canceled_after_execution = True
            cancellation_error = exc
        if canceled_after_execution:
            self._record_run(work)
            self._remove_work(work)
            return IanTurn(
                IanTurnStatus.CANCELED,
                queue_depth=len(self._pending),
                request=work.request,
                stage=stage,
                remaining_stages=len(work.request.stages) - work.stage_index,
                reason=(
                    "ian_cancellation_probe_failed_after_execution"
                    if cancellation_error is not None
                    else "ian_canceled_after_execution"
                ),
                resource_sample=sample,
                reserved_memory_mb=claim.memory_mb,
                reserved_swap_percent=claim.swap_growth_percent,
                error=cancellation_error,
            )
        self._observe_cost(work, execution)
        next_stage_index = work.stage_index + 1
        remaining = len(work.request.stages) - next_stage_index
        if remaining and not self._checkpoint_is_valid(
            execution.checkpoint,
            work,
            stage,
        ):
            self._record_run(work)
            self._remove_work(work)
            return IanTurn(
                IanTurnStatus.FAILED,
                queue_depth=len(self._pending),
                request=work.request,
                stage=stage,
                result=execution.result,
                remaining_stages=remaining,
                reason="ian_checkpoint_rejected",
                resource_sample=sample,
                reserved_memory_mb=claim.memory_mb,
                reserved_swap_percent=claim.swap_growth_percent,
            )
        work.previous_checkpoint = execution.checkpoint
        work.stage_index = next_stage_index
        self._record_run(work)
        self._clear_drain_for(work)
        if remaining:
            return IanTurn(
                IanTurnStatus.CHECKPOINTED,
                queue_depth=len(self._pending),
                request=work.request,
                stage=stage,
                result=execution.result,
                remaining_stages=remaining,
                resource_sample=sample,
                reserved_memory_mb=claim.memory_mb,
                reserved_swap_percent=claim.swap_growth_percent,
                checkpoint=execution.checkpoint,
            )

        self._remove_work(work)
        return IanTurn(
            IanTurnStatus.SUCCEEDED,
            queue_depth=len(self._pending),
            request=work.request,
            stage=stage,
            result=execution.result,
            resource_sample=sample,
            reserved_memory_mb=claim.memory_mb,
            reserved_swap_percent=claim.swap_growth_percent,
            checkpoint=execution.checkpoint,
        )

    @staticmethod
    def _request_identity(request: IanRequest) -> tuple[object, ...]:
        return (
            request.plugin_id,
            request.instance_uuid,
            request.structural_generation,
            request.settings_revision,
            request.intent,
            request.plan_token,
        )

    @staticmethod
    def _request_scope(request: IanRequest) -> tuple[object, ...]:
        return (
            request.plugin_id,
            request.instance_uuid,
            request.intent,
            request.request_id if request.instance_uuid is None else None,
        )

    @staticmethod
    def _revision(request: IanRequest) -> tuple[int, int]:
        return (
            -1
            if request.structural_generation is None
            else request.structural_generation,
            -1 if request.settings_revision is None else request.settings_revision,
        )

    def _sample_resources(self) -> IanResourceSample | None:
        try:
            sample = self._resource_sampler()
            available_mb = self._finite_metric(sample.available_mb)
            swap_percent = self._finite_metric(sample.swap_percent)
        except Exception:
            return None
        if (
            available_mb is None
            or available_mb < 0.0
            or swap_percent is None
            or not 0.0 <= swap_percent <= 100.0
        ):
            return None
        return IanResourceSample(available_mb, swap_percent)

    @classmethod
    def _resource_claim(cls, stage: IanStage) -> IanResourceClaim:
        claim = stage.claim
        if claim is None:
            return cls._CONSERVATIVE_CLAIM
        memory_mb = cls._finite_metric(claim.memory_mb)
        swap_growth = cls._finite_metric(claim.swap_growth_percent)
        if (
            memory_mb is None
            or memory_mb < 0.0
            or swap_growth is None
            or swap_growth < 0.0
        ):
            return cls._CONSERVATIVE_CLAIM
        return IanResourceClaim(
            memory_mb=memory_mb,
            swap_growth_percent=swap_growth,
            pressure_safe=(
                claim.pressure_safe
                if type(claim.pressure_safe) is bool
                else False
            ),
        )

    def _claim_for(self, work: _IanWork) -> IanResourceClaim:
        stage = work.request.stages[work.stage_index]
        base = self._resource_claim(stage)
        observed = self._observed_costs.get(self._cost_key(work))
        if observed is None:
            return base
        observed_memory = (
            0.0
            if observed.peak_memory_mb is None
            else observed.peak_memory_mb * self._adaptive_safety_factor
        )
        observed_swap = (
            0.0
            if observed.swap_growth_percent is None
            else observed.swap_growth_percent * self._adaptive_safety_factor
        )
        return IanResourceClaim(
            memory_mb=max(base.memory_mb, observed_memory),
            swap_growth_percent=max(
                base.swap_growth_percent,
                observed_swap,
            ),
            pressure_safe=base.pressure_safe,
        )

    def _observe_cost(
        self,
        work: _IanWork,
        execution: IanExecutionResult,
    ) -> None:
        memory = self._finite_metric(execution.peak_memory_mb)
        swap = self._finite_metric(execution.swap_growth_percent)
        if memory is not None and memory < 0.0:
            memory = None
        if swap is not None and swap < 0.0:
            swap = None
        if memory is None and swap is None:
            return
        key = self._cost_key(work)
        observed = self._observed_costs.setdefault(key, _IanObservedCost())
        if memory is not None:
            observed.peak_memory_mb = self._adaptive_average(
                observed.peak_memory_mb,
                min(4096.0, memory),
            )
        if swap is not None:
            observed.swap_growth_percent = self._adaptive_average(
                observed.swap_growth_percent,
                min(100.0, swap),
            )

    def _adaptive_average(
        self,
        previous: float | None,
        observed: float,
    ) -> float:
        if previous is None:
            return observed
        # A single unusually cheap run must not erase a measured peak on a
        # memory-constrained device.  Ian keeps the session high-water mark;
        # process restart remains the deliberate reset boundary.
        return max(previous, observed)

    @staticmethod
    def _cost_key(work: _IanWork) -> tuple[str, str, IanStageKind]:
        stage = work.request.stages[work.stage_index]
        return (
            work.request.plugin_id,
            stage.name,
            stage.kind,
        )

    @classmethod
    def _can_reserve(
        cls,
        stage: IanStage,
        claim: IanResourceClaim,
        sample: IanResourceSample,
    ) -> bool:
        pressure_safe = (
            claim.pressure_safe
            and stage.kind in {IanStageKind.CACHE, IanStageKind.DISPLAY}
        )
        start_swap_limit = (
            cls._HARD_MAX_SWAP_PERCENT
            if pressure_safe
            else cls._START_MAX_SWAP_PERCENT
        )
        return (
            sample.available_mb - claim.memory_mb
            >= cls._HARD_MIN_AVAILABLE_MB
            and sample.swap_percent < start_swap_limit
            and sample.swap_percent + claim.swap_growth_percent
            < cls._HARD_MAX_SWAP_PERCENT
        )

    @staticmethod
    def _finite_metric(value: object) -> float | None:
        if value is None or isinstance(value, bool):
            return None
        try:
            metric = float(value)
        except (TypeError, ValueError, OverflowError):
            return None
        return metric if math.isfinite(metric) else None

    def _ordered_work(self, now: float) -> list[_IanWork]:
        ordered = sorted(
            self._pending,
            key=lambda work: (
                not self._deadline_is_urgent(work, now),
                (
                    work.request.deadline_monotonic
                    if self._deadline_is_urgent(work, now)
                    else math.inf
                ),
                -int(work.request.priority),
                work.last_run_sequence,
                work.offered_sequence,
            ),
        )
        selected = ordered[0]
        if (
            not self._deadline_is_urgent(selected, now)
            and
            self._last_priority is selected.request.priority
            and self._priority_streak >= self._max_priority_streak
        ):
            lower_priority = [
                work
                for work in ordered
                if work.request.priority < selected.request.priority
            ]
            if lower_priority:
                selected = lower_priority[0]
        if selected is ordered[0]:
            return ordered
        return [selected, *(work for work in ordered if work is not selected)]

    def _deadline_is_urgent(self, work: _IanWork, now: float) -> bool:
        return (
            work.request.deadline_monotonic - now
            <= self._deadline_urgency_seconds
        )

    def _record_run(self, work: _IanWork) -> None:
        self._turn_sequence += 1
        work.last_run_sequence = self._turn_sequence
        work.last_progress_monotonic = self._clock()
        priority = work.request.priority
        if self._last_priority is priority:
            self._priority_streak += 1
        else:
            self._last_priority = priority
            self._priority_streak = 1

    def _remove_work(self, work: _IanWork) -> None:
        self._clear_drain_for(work)
        self._pending.remove(work)

    def _clear_drain_for(self, work: _IanWork) -> None:
        if (
            self._active_drain is not None
            and self._active_drain.work is work
        ):
            self._active_drain = None

    def _queue_is_full_for(self, request: IanRequest) -> bool:
        if len(self._pending) >= self._capacity:
            return True
        if request.priority >= IanPriority.MANUAL:
            return False
        non_urgent_depth = sum(
            work.request.priority < IanPriority.MANUAL
            for work in self._pending
        )
        return non_urgent_depth >= self._capacity - self._urgent_reserved

    @classmethod
    def _invalid_request_reason(cls, request: object) -> str | None:
        if not isinstance(request, IanRequest):
            return "request_type"
        if not cls._nonempty_text(request.request_id):
            return "request_id"
        if not cls._nonempty_text(request.plugin_id):
            return "plugin_id"
        if request.instance_uuid is not None and not cls._nonempty_text(
            request.instance_uuid
        ):
            return "instance_uuid"
        for revision in (
            request.structural_generation,
            request.settings_revision,
        ):
            if revision is not None and (
                isinstance(revision, bool)
                or not isinstance(revision, int)
                or revision < 0
            ):
                return "revision"
        if not cls._nonempty_text(request.intent):
            return "intent"
        if not cls._nonempty_text(request.plan_token):
            return "plan_token"
        if not isinstance(request.priority, IanPriority):
            return "priority"
        if cls._finite_metric(request.deadline_monotonic) is None:
            return "deadline"
        if not isinstance(request.stages, tuple) or not request.stages:
            return "stages"
        stage_names = set()
        for stage in request.stages:
            if (
                not isinstance(stage, IanStage)
                or not cls._nonempty_text(stage.name)
                or not isinstance(stage.kind, IanStageKind)
                or stage.name in stage_names
            ):
                return "stages"
            stage_names.add(stage.name)
            if stage.claim is not None and not isinstance(
                stage.claim,
                IanResourceClaim,
            ):
                return "claim"
            if (
                stage.claim is not None
                and not cls._claim_is_valid(stage.claim)
            ):
                return "claim"
        if not isinstance(request.payload, MappingABC):
            return "payload"
        return None

    @staticmethod
    def _nonempty_text(value: object) -> bool:
        return isinstance(value, str) and bool(value.strip())

    @staticmethod
    def _claim_is_valid(claim: IanResourceClaim) -> bool:
        if type(claim.pressure_safe) is not bool:
            return False
        for value, maximum in (
            (claim.memory_mb, 4096.0),
            (claim.swap_growth_percent, 100.0),
        ):
            if type(value) not in {int, float}:
                return False
            metric = float(value)
            if not math.isfinite(metric) or not 0.0 <= metric <= maximum:
                return False
        return True

    def _checkpoint_is_valid(
        self,
        checkpoint: object,
        work: _IanWork,
        stage: IanStage,
    ) -> bool:
        if checkpoint is None:
            return True
        if not isinstance(checkpoint, IanCheckpoint):
            return False
        if (
            not self._nonempty_text(checkpoint.token)
            or len(checkpoint.token.encode("utf-8")) > 4096
            or isinstance(checkpoint.size_bytes, bool)
            or not isinstance(checkpoint.size_bytes, int)
            or not 0 <= checkpoint.size_bytes <= self._max_checkpoint_bytes
        ):
            return False
        if checkpoint.digest is None:
            digest_is_valid = True
        else:
            digest_is_valid = (
                isinstance(checkpoint.digest, str)
                and len(checkpoint.digest) == 64
                and all(
                    character in "0123456789abcdef"
                    for character in checkpoint.digest
                )
            )
        expected_predecessor = (
            None
            if work.previous_checkpoint is None
            else work.previous_checkpoint.token
        )
        return (
            digest_is_valid
            and checkpoint.request_id == work.request.request_id
            and checkpoint.plan_token == work.request.plan_token
            and checkpoint.stage_name == stage.name
            and checkpoint.predecessor_token == expected_predecessor
        )

    def _find_canceled_work(
        self,
    ) -> tuple[_IanWork, str, BaseException | None] | None:
        for work in sorted(
            self._pending,
            key=lambda candidate: candidate.offered_sequence,
        ):
            try:
                canceled = bool(self._cancellation_probe(work.request))
            except Exception as exc:
                return work, "ian_cancellation_probe_failed", exc
            if canceled:
                return work, "ian_canceled", None
        return None

    @classmethod
    def _stage_is_pressure_safe(cls, stage: IanStage) -> bool:
        claim = cls._resource_claim(stage)
        return (
            claim.pressure_safe
            and stage.kind in {IanStageKind.CACHE, IanStageKind.DISPLAY}
        )
