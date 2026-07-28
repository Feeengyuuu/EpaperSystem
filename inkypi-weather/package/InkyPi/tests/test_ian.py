from __future__ import annotations

import threading

import pytest

from runtime.refresh_contracts import TaskCancelled, TaskDeadlineExceeded
from runtime.ian import (
    Ian,
    IanCheckpoint,
    IanExecutionResult,
    IanOfferStatus,
    IanPriority,
    IanRequest,
    IanResourceClaim,
    IanResourceSample,
    IanStage,
    IanStageKind,
    IanTurnStatus,
)


class FakeClock:
    def __init__(self, value: float = 10.0):
        self.value = value

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


def request(
    *,
    request_id: str = "request-1",
    plugin_id: str = "weather",
    instance_uuid: str = "weather-instance",
    structural_generation: int = 3,
    settings_revision: int = 7,
    intent: str = "data_refresh",
    plan_token: str = "plan-v1",
    priority: IanPriority = IanPriority.BACKGROUND,
    deadline_monotonic: float = 100.0,
    stages: tuple[IanStage, ...] | None = None,
    payload=None,
) -> IanRequest:
    return IanRequest(
        request_id=request_id,
        plugin_id=plugin_id,
        instance_uuid=instance_uuid,
        structural_generation=structural_generation,
        settings_revision=settings_revision,
        intent=intent,
        plan_token=plan_token,
        priority=priority,
        deadline_monotonic=deadline_monotonic,
        stages=(
            (
                IanStage(
                    "fetch",
                    IanStageKind.FETCH,
                    IanResourceClaim(memory_mb=12.0, swap_growth_percent=1.0),
                ),
                IanStage(
                    "render",
                    IanStageKind.RENDER,
                    IanResourceClaim(memory_mb=45.0, swap_growth_percent=0.0),
                ),
            )
            if stages is None
            else stages
        ),
        payload={} if payload is None else payload,
    )


def checkpoint_for(
    offered,
    stage,
    previous_checkpoint,
    *,
    token: str,
    size_bytes: int,
) -> IanCheckpoint:
    return IanCheckpoint(
        token,
        size_bytes=size_bytes,
        request_id=offered.request_id,
        plan_token=offered.plan_token,
        stage_name=stage.name,
        predecessor_token=(
            None
            if previous_checkpoint is None
            else previous_checkpoint.token
        ),
    )


def test_ian_executes_one_stage_per_turn_and_exposes_checkpoint_result():
    clock = FakeClock()
    executed = []

    def execute(offered, stage, previous_checkpoint):
        executed.append((offered.request_id, stage.name, previous_checkpoint))
        return IanExecutionResult(
            result=f"{stage.name}-result",
            checkpoint=(
                checkpoint_for(
                    offered,
                    stage,
                    previous_checkpoint,
                    token="fetch-checkpoint",
                    size_bytes=16,
                )
                if stage.name == "fetch"
                else None
            ),
        )

    ian = Ian(
        clock=clock,
        resource_sampler=lambda: IanResourceSample(
            available_mb=300.0,
            swap_percent=10.0,
        ),
        executor=execute,
    )

    receipt = ian.offer(request())
    first = ian.run_turn()
    second = ian.run_turn()

    assert receipt.status is IanOfferStatus.ACCEPTED
    assert receipt.queue_depth == 1
    assert first.status is IanTurnStatus.CHECKPOINTED
    assert first.request.request_id == "request-1"
    assert first.stage.name == "fetch"
    assert first.result == "fetch-result"
    assert first.remaining_stages == 1
    assert first.queue_depth == 1
    assert second.status is IanTurnStatus.SUCCEEDED
    assert second.request.request_id == "request-1"
    assert second.stage.name == "render"
    assert second.result == "render-result"
    assert second.remaining_stages == 0
    assert second.queue_depth == 0
    assert executed == [
        ("request-1", "fetch", None),
        (
            "request-1",
            "render",
            IanCheckpoint(
                "fetch-checkpoint",
                size_bytes=16,
                request_id="request-1",
                plan_token="plan-v1",
                stage_name="fetch",
                predecessor_token=None,
            ),
        ),
    ]


def test_ian_coalesces_urgency_without_mixing_checkpoint_payloads():
    clock = FakeClock()
    executed = []

    def execute(offered, stage, previous_checkpoint):
        executed.append(
            (
                offered.request_id,
                stage.name,
                offered.payload["version"],
                previous_checkpoint,
            )
        )
        return IanExecutionResult(
            result=f"{stage.name}-result",
            checkpoint=(
                checkpoint_for(
                    offered,
                    stage,
                    previous_checkpoint,
                    token="fetch-checkpoint",
                    size_bytes=16,
                )
                if stage.name == "fetch"
                else None
            ),
        )

    ian = Ian(
        clock=clock,
        resource_sampler=lambda: IanResourceSample(300.0, 10.0),
        executor=execute,
    )
    ian.offer(request(request_id="original", payload={"version": 1}))
    assert ian.run_turn().status is IanTurnStatus.CHECKPOINTED

    receipt = ian.offer(
        request(
            request_id="duplicate",
            payload={"version": 2},
            priority=IanPriority.MANUAL,
            deadline_monotonic=80.0,
        )
    )
    completed = ian.run_turn()

    assert receipt.status is IanOfferStatus.COALESCED
    assert receipt.request_id == "original"
    assert receipt.queue_depth == 1
    assert completed.status is IanTurnStatus.SUCCEEDED
    assert completed.request.request_id == "original"
    assert completed.request.priority is IanPriority.MANUAL
    assert completed.request.deadline_monotonic == 80.0
    assert completed.request.payload["version"] == 1
    assert executed == [
        ("original", "fetch", 1, None),
        (
            "original",
            "render",
            1,
            IanCheckpoint(
                "fetch-checkpoint",
                size_bytes=16,
                request_id="original",
                plan_token="plan-v1",
                stage_name="fetch",
                predecessor_token=None,
            ),
        ),
    ]


def test_ian_new_revision_supersedes_checkpoint_and_stale_revision_is_rejected():
    executed = []

    def execute(offered, stage, previous_checkpoint):
        executed.append(
            (
                offered.request_id,
                offered.settings_revision,
                stage.name,
                previous_checkpoint,
            )
        )
        return IanExecutionResult(
            result=f"{stage.name}-{offered.settings_revision}",
            checkpoint=checkpoint_for(
                offered,
                stage,
                previous_checkpoint,
                token=f"fetch-{offered.settings_revision}",
                size_bytes=16,
            ),
        )

    ian = Ian(
        clock=FakeClock(),
        resource_sampler=lambda: IanResourceSample(300.0, 10.0),
        executor=execute,
    )
    ian.offer(request(request_id="revision-7", settings_revision=7))
    assert ian.run_turn().status is IanTurnStatus.CHECKPOINTED

    newer = ian.offer(request(request_id="revision-8", settings_revision=8))
    stale = ian.offer(request(request_id="stale-7", settings_revision=7))
    restarted = ian.run_turn()

    assert newer.status is IanOfferStatus.SUPERSEDED
    assert newer.request_id == "revision-8"
    assert newer.superseded_request_id == "revision-7"
    assert newer.queue_depth == 1
    assert stale.status is IanOfferStatus.REJECTED
    assert stale.reason == "ian_stale_revision"
    assert stale.queue_depth == 1
    assert restarted.status is IanTurnStatus.CHECKPOINTED
    assert restarted.request.request_id == "revision-8"
    assert restarted.stage.name == "fetch"
    assert executed == [
        ("revision-7", 7, "fetch", None),
        ("revision-8", 8, "fetch", None),
    ]


@pytest.mark.parametrize(
    ("available_mb", "swap_percent", "expected_status"),
    [
        (115.0, 69.999, IanTurnStatus.SUCCEEDED),
        (114.999, 0.0, IanTurnStatus.DEFERRED),
        (1000.0, 70.0, IanTurnStatus.DEFERRED),
        (1000.0, 99.0, IanTurnStatus.DEFERRED),
        (None, 10.0, IanTurnStatus.RESOURCE_UNKNOWN),
        (float("nan"), 10.0, IanTurnStatus.RESOURCE_UNKNOWN),
        (float("inf"), 10.0, IanTurnStatus.RESOURCE_UNKNOWN),
        (True, 10.0, IanTurnStatus.RESOURCE_UNKNOWN),
        (200.0, None, IanTurnStatus.RESOURCE_UNKNOWN),
    ],
)
def test_ian_fails_closed_and_honors_heavy_start_boundary(
    available_mb,
    swap_percent,
    expected_status,
):
    executed = []
    ian = Ian(
        clock=FakeClock(),
        resource_sampler=lambda: IanResourceSample(
            available_mb,
            swap_percent,
        ),
        executor=lambda offered, stage, checkpoint: (
            executed.append(stage.name) or IanExecutionResult(result="done")
        ),
    )
    ian.offer(
        request(
            stages=(
                IanStage(
                    "render",
                    IanStageKind.RENDER,
                    IanResourceClaim(
                        memory_mb=45.0,
                        swap_growth_percent=0.0,
                    ),
                ),
            )
        )
    )

    turn = ian.run_turn()

    assert turn.status is expected_status
    assert turn.request.request_id == "request-1"
    assert turn.stage.name == "render"
    assert turn.queue_depth == (
        0 if expected_status is IanTurnStatus.SUCCEEDED else 1
    )
    if expected_status is IanTurnStatus.SUCCEEDED:
        assert executed == ["render"]
        assert turn.reserved_memory_mb == 45.0
    else:
        assert executed == []
        assert turn.reason == (
            "ian_resource_metrics_unknown"
            if expected_status is IanTurnStatus.RESOURCE_UNKNOWN
            else "ian_resource_reservation_unavailable"
        )


def test_ian_rearbitrates_checkpoints_and_bounds_priority_streaks():
    executed = []

    def execute(offered, stage, previous_checkpoint):
        executed.append((offered.request_id, stage.name))
        return IanExecutionResult(
            result=stage.name,
            checkpoint=checkpoint_for(
                offered,
                stage,
                previous_checkpoint,
                token=stage.name,
                size_bytes=8,
            ),
        )

    ian = Ian(
        clock=FakeClock(),
        resource_sampler=lambda: IanResourceSample(300.0, 10.0),
        executor=execute,
        max_priority_streak=2,
    )
    background_stages = (
        IanStage(
            "background-1",
            IanStageKind.FETCH,
            IanResourceClaim(10.0),
        ),
        IanStage(
            "background-2",
            IanStageKind.NORMALIZE,
            IanResourceClaim(10.0),
        ),
    )
    display_stages = tuple(
        IanStage(
            f"display-{index}",
            IanStageKind.DISPLAY,
            IanResourceClaim(8.0, pressure_safe=True),
        )
        for index in range(1, 4)
    )
    ian.offer(
        request(
            request_id="background",
            instance_uuid="background-instance",
            priority=IanPriority.BACKGROUND,
            stages=background_stages,
        )
    )
    assert ian.run_turn().status is IanTurnStatus.CHECKPOINTED
    ian.offer(
        request(
            request_id="display",
            instance_uuid="display-instance",
            priority=IanPriority.DISPLAY,
            stages=display_stages,
        )
    )

    turns = [ian.run_turn() for _ in range(4)]

    assert [turn.request.request_id for turn in turns] == [
        "display",
        "display",
        "background",
        "display",
    ]
    assert executed == [
        ("background", "background-1"),
        ("display", "display-1"),
        ("display", "display-2"),
        ("background", "background-2"),
        ("display", "display-3"),
    ]


def test_ian_promotes_near_deadline_work_and_expires_before_execution():
    clock = FakeClock()
    executed = []
    stage = IanStage(
        "work",
        IanStageKind.NORMALIZE,
        IanResourceClaim(10.0),
    )
    ian = Ian(
        clock=clock,
        resource_sampler=lambda: IanResourceSample(300.0, 10.0),
        executor=lambda offered, current_stage, checkpoint: (
            executed.append(offered.request_id)
            or IanExecutionResult(result="done")
        ),
        deadline_urgency_seconds=30.0,
    )
    ian.offer(
        request(
            request_id="display-later",
            instance_uuid="display-later",
            priority=IanPriority.DISPLAY,
            deadline_monotonic=100.0,
            stages=(stage,),
        )
    )
    ian.offer(
        request(
            request_id="background-urgent",
            instance_uuid="background-urgent",
            priority=IanPriority.BACKGROUND,
            deadline_monotonic=20.0,
            stages=(stage,),
        )
    )

    urgent = ian.run_turn()
    clock.advance(91.0)
    expired = ian.run_turn()

    assert urgent.status is IanTurnStatus.SUCCEEDED
    assert urgent.request.request_id == "background-urgent"
    assert expired.status is IanTurnStatus.DEADLINE_EXPIRED
    assert expired.request.request_id == "display-later"
    assert expired.stage.name == "work"
    assert expired.reason == "ian_deadline_expired"
    assert expired.queue_depth == 0
    assert executed == ["background-urgent"]


def test_ian_bounds_distinct_demands_and_reserves_capacity_for_urgent_work():
    ian = Ian(
        clock=FakeClock(),
        resource_sampler=lambda: IanResourceSample(300.0, 10.0),
        executor=lambda offered, stage, checkpoint: IanExecutionResult(),
        capacity=2,
        urgent_reserved=1,
    )

    first = ian.offer(
        request(
            request_id="background-1",
            instance_uuid="background-1",
        )
    )
    duplicate = ian.offer(
        request(
            request_id="background-1-duplicate",
            instance_uuid="background-1",
        )
    )
    background_overflow = ian.offer(
        request(
            request_id="background-2",
            instance_uuid="background-2",
        )
    )
    reserved = ian.offer(
        request(
            request_id="display-1",
            instance_uuid="display-1",
            priority=IanPriority.DISPLAY,
        )
    )
    hard_overflow = ian.offer(
        request(
            request_id="manual-2",
            instance_uuid="manual-2",
            priority=IanPriority.MANUAL,
        )
    )

    assert first.status is IanOfferStatus.ACCEPTED
    assert duplicate.status is IanOfferStatus.COALESCED
    assert duplicate.request_id == "background-1"
    assert background_overflow.status is IanOfferStatus.REJECTED
    assert background_overflow.reason == "ian_queue_full"
    assert background_overflow.queue_depth == 1
    assert reserved.status is IanOfferStatus.ACCEPTED
    assert reserved.queue_depth == 2
    assert hard_overflow.status is IanOfferStatus.REJECTED
    assert hard_overflow.reason == "ian_queue_full"
    assert hard_overflow.queue_depth == 2


def test_ian_rejects_empty_or_invalid_work_before_it_reaches_a_turn():
    clock = FakeClock()
    sampled = []
    executed = []
    ian = Ian(
        clock=clock,
        resource_sampler=lambda: (
            sampled.append(True) or IanResourceSample(300.0, 10.0)
        ),
        executor=lambda offered, stage, checkpoint: (
            executed.append(True) or IanExecutionResult()
        ),
    )

    empty = ian.offer(request(request_id="empty", stages=()))
    expired = ian.offer(
        request(
            request_id="expired",
            deadline_monotonic=clock.value,
        )
    )
    invalid_deadline = ian.offer(
        request(
            request_id="invalid-deadline",
            deadline_monotonic=float("nan"),
        )
    )

    assert empty.status is IanOfferStatus.REJECTED
    assert empty.reason == "ian_invalid_request"
    assert expired.status is IanOfferStatus.REJECTED
    assert expired.reason == "ian_deadline_expired"
    assert invalid_deadline.status is IanOfferStatus.REJECTED
    assert invalid_deadline.reason == "ian_invalid_request"
    assert ian.run_turn().status is IanTurnStatus.IDLE
    assert sampled == []
    assert executed == []


def test_ian_plan_token_change_discards_checkpoint_even_with_same_revision():
    executed = []

    def execute(offered, stage, previous_checkpoint):
        executed.append(
            (offered.request_id, offered.plan_token, stage.name, previous_checkpoint)
        )
        return IanExecutionResult(
            result=stage.name,
            checkpoint=checkpoint_for(
                offered,
                stage,
                previous_checkpoint,
                token=stage.name,
                size_bytes=8,
            ),
        )

    ian = Ian(
        clock=FakeClock(),
        resource_sampler=lambda: IanResourceSample(300.0, 10.0),
        executor=execute,
    )
    ian.offer(request(request_id="plan-a", plan_token="plan-a"))
    assert ian.run_turn().status is IanTurnStatus.CHECKPOINTED

    replacement = ian.offer(
        request(
            request_id="plan-b",
            plan_token="plan-b",
            payload={"semantic": "changed"},
            stages=(
                IanStage(
                    "new-fetch",
                    IanStageKind.FETCH,
                    IanResourceClaim(10.0),
                ),
                IanStage(
                    "new-render",
                    IanStageKind.RENDER,
                    IanResourceClaim(45.0),
                ),
            ),
        )
    )
    restarted = ian.run_turn()

    assert replacement.status is IanOfferStatus.SUPERSEDED
    assert replacement.reason == "ian_plan_changed"
    assert replacement.superseded_request_id == "plan-a"
    assert restarted.status is IanTurnStatus.CHECKPOINTED
    assert restarted.request.request_id == "plan-b"
    assert restarted.stage.name == "new-fetch"
    assert executed == [
        ("plan-a", "plan-a", "fetch", None),
        ("plan-b", "plan-b", "new-fetch", None),
    ]


def test_ian_fails_closed_instead_of_retaining_oversized_checkpoint():
    executed = []
    ian = Ian(
        clock=FakeClock(),
        resource_sampler=lambda: IanResourceSample(300.0, 10.0),
        executor=lambda offered, stage, checkpoint: (
            executed.append(stage.name)
            or IanExecutionResult(
                result="large-intermediate",
                checkpoint=checkpoint_for(
                    offered,
                    stage,
                    checkpoint,
                    token="file:/tmp/large-intermediate",
                    size_bytes=1_500_001,
                ),
            )
        ),
        max_checkpoint_bytes=1_500_000,
    )
    ian.offer(request())

    failed = ian.run_turn()
    idle = ian.run_turn()

    assert failed.status is IanTurnStatus.FAILED
    assert failed.request.request_id == "request-1"
    assert failed.stage.name == "fetch"
    assert failed.result == "large-intermediate"
    assert failed.reason == "ian_checkpoint_rejected"
    assert failed.queue_depth == 0
    assert idle.status is IanTurnStatus.IDLE
    assert executed == ["fetch"]


def test_ian_terminalizes_probe_and_executor_cancellation_without_sampling_again():
    sampled = []
    executed = []
    canceled_ids = {"probe-canceled"}

    def execute(offered, stage, checkpoint):
        executed.append(offered.request_id)
        raise TaskCancelled("caller canceled")

    ian = Ian(
        clock=FakeClock(),
        resource_sampler=lambda: (
            sampled.append(True) or IanResourceSample(300.0, 10.0)
        ),
        executor=execute,
        cancellation_probe=lambda offered: (
            offered.request_id in canceled_ids
        ),
    )
    ian.offer(
        request(
            request_id="probe-canceled",
            instance_uuid="probe-canceled",
        )
    )
    ian.offer(
        request(
            request_id="executor-canceled",
            instance_uuid="executor-canceled",
        )
    )

    probed = ian.run_turn()
    raised = ian.run_turn()

    assert probed.status is IanTurnStatus.CANCELED
    assert probed.request.request_id == "probe-canceled"
    assert probed.reason == "ian_canceled"
    assert probed.queue_depth == 1
    assert raised.status is IanTurnStatus.CANCELED
    assert raised.request.request_id == "executor-canceled"
    assert raised.reason == "ian_execution_canceled"
    assert raised.queue_depth == 0
    assert sampled == [True]
    assert executed == ["executor-canceled"]


def test_ian_allows_only_explicit_cache_display_safe_work_through_pressure():
    sample = IanResourceSample(None, None)
    executed = []

    def resources():
        return sample

    ian = Ian(
        clock=FakeClock(),
        resource_sampler=resources,
        executor=lambda offered, stage, checkpoint: (
            executed.append(offered.request_id)
            or IanExecutionResult(result="done")
        ),
    )
    heavy = IanStage(
        "heavy-render",
        IanStageKind.RENDER,
        IanResourceClaim(45.0),
    )
    safe_cache = IanStage(
        "safe-cache",
        IanStageKind.CACHE,
        IanResourceClaim(8.0, pressure_safe=True),
    )
    safe_display = IanStage(
        "safe-display",
        IanStageKind.DISPLAY,
        IanResourceClaim(8.0, pressure_safe=True),
    )
    ian.offer(
        request(
            request_id="heavy",
            instance_uuid="heavy",
            priority=IanPriority.DISPLAY,
            stages=(heavy,),
        )
    )
    ian.offer(
        request(
            request_id="cache",
            instance_uuid="cache",
            stages=(safe_cache,),
        )
    )

    unknown_safe = ian.run_turn()
    sample = IanResourceSample(80.0, 72.0)
    ian.offer(
        request(
            request_id="display",
            instance_uuid="display",
            priority=IanPriority.MANUAL,
            stages=(safe_display,),
        )
    )
    pressured_safe = ian.run_turn()
    blocked_heavy = ian.run_turn()

    assert unknown_safe.status is IanTurnStatus.SUCCEEDED
    assert unknown_safe.request.request_id == "cache"
    assert unknown_safe.resource_sample is None
    assert pressured_safe.status is IanTurnStatus.SUCCEEDED
    assert pressured_safe.request.request_id == "display"
    assert pressured_safe.resource_sample == IanResourceSample(80.0, 72.0)
    assert blocked_heavy.status is IanTurnStatus.DEFERRED
    assert blocked_heavy.request.request_id == "heavy"
    assert executed == ["cache", "display"]


def test_ian_bounds_starvation_drain_and_cooldown_before_retrying():
    clock = FakeClock()
    sample = IanResourceSample(114.0, 10.0)
    executed = []
    ian = Ian(
        clock=clock,
        resource_sampler=lambda: sample,
        executor=lambda offered, stage, checkpoint: (
            executed.append(offered.request_id)
            or IanExecutionResult(result="done")
        ),
        starvation_seconds=5.0,
        drain_seconds=3.0,
        cooldown_seconds=10.0,
    )
    ian.offer(
        request(
            request_id="heavy",
            instance_uuid="heavy",
            stages=(
                IanStage(
                    "render",
                    IanStageKind.RENDER,
                    IanResourceClaim(45.0),
                ),
            ),
        )
    )

    initial = ian.run_turn()
    clock.advance(5.0)
    started = ian.run_turn()
    clock.advance(2.0)
    draining = ian.run_turn()
    clock.advance(1.0)
    cooled = ian.run_turn()
    ian.offer(
        request(
            request_id="ordinary",
            instance_uuid="ordinary",
            stages=(
                IanStage(
                    "normalize",
                    IanStageKind.NORMALIZE,
                    IanResourceClaim(10.0),
                ),
            ),
        )
    )
    ordinary = ian.run_turn()
    still_cooling = ian.run_turn()
    clock.advance(10.0)
    retried = ian.run_turn()
    sample = IanResourceSample(115.0, 10.0)
    recovered = ian.run_turn()

    assert initial.status is IanTurnStatus.DEFERRED
    assert started.status is IanTurnStatus.DRAINING
    assert started.reason == "ian_starvation_drain_started"
    assert started.drain_deadline_monotonic == 18.0
    assert draining.status is IanTurnStatus.DRAINING
    assert draining.reason == "ian_starvation_draining"
    assert cooled.status is IanTurnStatus.COOLDOWN
    assert cooled.reason == "ian_starvation_drain_expired"
    assert cooled.cooldown_until_monotonic == 28.0
    assert ordinary.status is IanTurnStatus.SUCCEEDED
    assert ordinary.request.request_id == "ordinary"
    assert still_cooling.status is IanTurnStatus.DEFERRED
    assert retried.status is IanTurnStatus.DRAINING
    assert recovered.status is IanTurnStatus.SUCCEEDED
    assert recovered.request.request_id == "heavy"
    assert executed == ["ordinary", "heavy"]


def test_ian_adapts_future_reservations_from_observed_stage_cost():
    sample = IanResourceSample(200.0, 10.0)
    executed = []

    def execute(offered, stage, checkpoint):
        executed.append(offered.request_id)
        return IanExecutionResult(
            result="done",
            peak_memory_mb=100.0,
            swap_growth_percent=4.0,
        )

    ian = Ian(
        clock=FakeClock(),
        resource_sampler=lambda: sample,
        executor=execute,
        adaptive_safety_factor=1.25,
    )
    render = IanStage(
        "render",
        IanStageKind.RENDER,
        IanResourceClaim(45.0),
    )
    ian.offer(
        request(
            request_id="first",
            instance_uuid="first",
            stages=(render,),
        )
    )
    first = ian.run_turn()
    ian.offer(
        request(
            request_id="second",
            instance_uuid="second",
            stages=(render,),
        )
    )
    sample = IanResourceSample(194.999, 10.0)
    deferred = ian.run_turn()
    sample = IanResourceSample(195.0, 69.9)
    admitted = ian.run_turn()

    assert first.status is IanTurnStatus.SUCCEEDED
    assert first.reserved_memory_mb == 45.0
    assert deferred.status is IanTurnStatus.DEFERRED
    assert deferred.reserved_memory_mb == 125.0
    assert deferred.reserved_swap_percent == 5.0
    assert admitted.status is IanTurnStatus.SUCCEEDED
    assert admitted.reserved_memory_mb == 125.0
    assert executed == ["first", "second"]


def test_ian_terminalizes_executor_failure_with_original_exception():
    failure = ValueError("renderer failed")

    def execute(offered, stage, checkpoint):
        raise failure

    ian = Ian(
        clock=FakeClock(),
        resource_sampler=lambda: IanResourceSample(300.0, 10.0),
        executor=execute,
    )
    ian.offer(request())

    failed = ian.run_turn()

    assert failed.status is IanTurnStatus.FAILED
    assert failed.request.request_id == "request-1"
    assert failed.stage.name == "fetch"
    assert failed.reason == "ian_execution_failed"
    assert failed.error is failure
    assert failed.queue_depth == 0
    assert ian.run_turn().status is IanTurnStatus.IDLE


def test_ian_uses_conservative_reservation_when_stage_claim_is_unknown():
    sample = IanResourceSample(197.999, 0.0)
    executed = []
    ian = Ian(
        clock=FakeClock(),
        resource_sampler=lambda: sample,
        executor=lambda offered, stage, checkpoint: (
            executed.append(stage.name) or IanExecutionResult(result="done")
        ),
    )
    ian.offer(
        request(
            stages=(
                IanStage(
                    "unknown-fetch",
                    IanStageKind.FETCH,
                ),
            )
        )
    )

    blocked = ian.run_turn()
    sample = IanResourceSample(198.0, 0.0)
    admitted = ian.run_turn()

    assert blocked.status is IanTurnStatus.DEFERRED
    assert blocked.reserved_memory_mb == 128.0
    assert blocked.reserved_swap_percent == 5.0
    assert admitted.status is IanTurnStatus.SUCCEEDED
    assert executed == ["unknown-fetch"]


def test_ian_detaches_payload_at_offer_so_checkpoint_plan_cannot_mutate():
    payload = {"theme": {"mode": "day"}}
    observed = []
    ian = Ian(
        clock=FakeClock(),
        resource_sampler=lambda: IanResourceSample(300.0, 10.0),
        executor=lambda offered, stage, checkpoint: (
            observed.append(offered.payload["theme"]["mode"])
            or IanExecutionResult(result="done")
        ),
    )

    ian.offer(request(payload=payload))
    payload["theme"]["mode"] = "night"
    ian.run_turn()

    assert observed == ["day"]


def test_ian_keeps_instance_less_manual_requests_as_distinct_demands():
    ian = Ian(
        clock=FakeClock(),
        resource_sampler=lambda: IanResourceSample(300.0, 10.0),
        executor=lambda offered, stage, checkpoint: IanExecutionResult(),
    )

    first = ian.offer(
        request(
            request_id="manual-1",
            instance_uuid=None,
            structural_generation=None,
            settings_revision=None,
            intent="manual_render",
            plan_token="manual-plan-1",
            priority=IanPriority.MANUAL,
        )
    )
    second = ian.offer(
        request(
            request_id="manual-2",
            instance_uuid=None,
            structural_generation=None,
            settings_revision=None,
            intent="manual_render",
            plan_token="manual-plan-2",
            priority=IanPriority.MANUAL,
        )
    )

    assert first.status is IanOfferStatus.ACCEPTED
    assert second.status is IanOfferStatus.ACCEPTED
    assert second.queue_depth == 2


def test_ian_single_low_observation_cannot_drop_learned_high_water():
    sample = IanResourceSample(200.0, 10.0)
    observations = iter((100.0, 0.0, 0.0))
    render = IanStage(
        "render",
        IanStageKind.RENDER,
        IanResourceClaim(45.0),
    )
    ian = Ian(
        clock=FakeClock(),
        resource_sampler=lambda: sample,
        executor=lambda offered, stage, checkpoint: IanExecutionResult(
            result="done",
            peak_memory_mb=next(observations),
        ),
        adaptive_safety_factor=1.25,
    )

    for request_id in ("high", "low"):
        ian.offer(
            request(
                request_id=request_id,
                instance_uuid=request_id,
                stages=(render,),
            )
        )
        assert ian.run_turn().status is IanTurnStatus.SUCCEEDED

    sample = IanResourceSample(140.0, 10.0)
    ian.offer(
        request(
            request_id="after-low",
            instance_uuid="after-low",
            stages=(render,),
        )
    )
    blocked = ian.run_turn()

    assert blocked.status is IanTurnStatus.DEFERRED
    assert blocked.reserved_memory_mb == 125.0


def test_ian_rechecks_deadline_after_executor_before_publishing_checkpoint():
    clock = FakeClock()

    def execute(offered, stage, checkpoint):
        clock.advance(2.0)
        return IanExecutionResult(
            result="stale-result",
            checkpoint=checkpoint_for(
                offered,
                stage,
                checkpoint,
                token="stale-checkpoint",
                size_bytes=16,
            ),
        )

    ian = Ian(
        clock=clock,
        resource_sampler=lambda: IanResourceSample(300.0, 10.0),
        executor=execute,
    )
    ian.offer(request(deadline_monotonic=11.0))

    expired = ian.run_turn()

    assert expired.status is IanTurnStatus.DEADLINE_EXPIRED
    assert expired.reason == "ian_deadline_expired_after_execution"
    assert expired.result is None
    assert expired.checkpoint is None
    assert expired.queue_depth == 0
    assert ian.run_turn().status is IanTurnStatus.IDLE


def test_ian_rechecks_cancellation_after_executor_before_publishing_result():
    canceled = {"value": False}

    def execute(offered, stage, checkpoint):
        canceled["value"] = True
        return IanExecutionResult(
            result="canceled-result",
            checkpoint=checkpoint_for(
                offered,
                stage,
                checkpoint,
                token="canceled-checkpoint",
                size_bytes=16,
            ),
        )

    ian = Ian(
        clock=FakeClock(),
        resource_sampler=lambda: IanResourceSample(300.0, 10.0),
        executor=execute,
        cancellation_probe=lambda offered: canceled["value"],
    )
    ian.offer(request())

    stopped = ian.run_turn()

    assert stopped.status is IanTurnStatus.CANCELED
    assert stopped.reason == "ian_canceled_after_execution"
    assert stopped.result is None
    assert stopped.checkpoint is None
    assert stopped.queue_depth == 0
    assert ian.run_turn().status is IanTurnStatus.IDLE


@pytest.mark.parametrize(
    "urgent_priority",
    [IanPriority.MANUAL, IanPriority.DISPLAY],
)
def test_ian_active_drain_never_blocks_admissible_urgent_work(
    urgent_priority,
):
    sample = IanResourceSample(114.0, 10.0)
    executed = []
    ian = Ian(
        clock=FakeClock(),
        resource_sampler=lambda: sample,
        executor=lambda offered, stage, checkpoint: (
            executed.append(offered.request_id)
            or IanExecutionResult(result="done")
        ),
        starvation_seconds=0.0,
        drain_seconds=10.0,
    )
    ian.offer(
        request(
            request_id="starved-heavy",
            instance_uuid="starved-heavy",
            stages=(
                IanStage(
                    "render",
                    IanStageKind.RENDER,
                    IanResourceClaim(45.0),
                ),
            ),
        )
    )
    assert ian.run_turn().status is IanTurnStatus.DRAINING
    ian.offer(
        request(
            request_id="urgent",
            instance_uuid="urgent",
            priority=urgent_priority,
            stages=(
                IanStage(
                    "urgent-work",
                    IanStageKind.NORMALIZE,
                    IanResourceClaim(10.0),
                ),
            ),
        )
    )
    sample = IanResourceSample(115.0, 10.0)

    urgent = ian.run_turn()
    heavy = ian.run_turn()

    assert urgent.status is IanTurnStatus.SUCCEEDED
    assert urgent.request.request_id == "urgent"
    assert heavy.status is IanTurnStatus.SUCCEEDED
    assert heavy.request.request_id == "starved-heavy"
    assert executed == ["urgent", "starved-heavy"]


def test_ian_concurrent_run_turn_cannot_execute_the_same_stage_twice():
    start = threading.Barrier(3)
    release_executor = threading.Event()
    first_executor_started = threading.Event()
    duplicate_executor_started = threading.Event()
    calls_lock = threading.Lock()
    call_count = 0
    results = []
    failures = []

    def execute(offered, stage, checkpoint):
        nonlocal call_count
        with calls_lock:
            call_count += 1
            current_call = call_count
        if current_call == 1:
            first_executor_started.set()
            if not release_executor.wait(timeout=1.0):
                raise TimeoutError("test executor was not released")
        else:
            duplicate_executor_started.set()
        return IanExecutionResult(result="done")

    ian = Ian(
        clock=FakeClock(),
        resource_sampler=lambda: IanResourceSample(300.0, 10.0),
        executor=execute,
    )
    ian.offer(
        request(
            stages=(
                IanStage(
                    "only-stage",
                    IanStageKind.NORMALIZE,
                    IanResourceClaim(10.0),
                ),
            )
        )
    )

    def run() -> None:
        try:
            start.wait()
            results.append(ian.run_turn())
        except BaseException as exc:  # pragma: no cover - asserted below
            failures.append(exc)

    threads = [threading.Thread(target=run) for _ in range(2)]
    for thread in threads:
        thread.start()
    start.wait()
    assert first_executor_started.wait(timeout=1.0)
    duplicated = duplicate_executor_started.wait(timeout=0.1)
    release_executor.set()
    for thread in threads:
        thread.join(timeout=1.0)

    assert not duplicated
    assert not failures
    assert all(not thread.is_alive() for thread in threads)
    assert call_count == 1
    assert {result.status for result in results} == {
        IanTurnStatus.SUCCEEDED,
        IanTurnStatus.IDLE,
    }


def test_ian_rejects_checkpoint_bound_to_a_foreign_request():
    executed = []

    def execute(offered, stage, previous_checkpoint):
        executed.append(stage.name)
        return IanExecutionResult(
            result="foreign",
            checkpoint=IanCheckpoint(
                "foreign-checkpoint",
                size_bytes=16,
                request_id="different-request",
                plan_token=offered.plan_token,
                stage_name=stage.name,
                predecessor_token=None,
            ),
        )

    ian = Ian(
        clock=FakeClock(),
        resource_sampler=lambda: IanResourceSample(300.0, 10.0),
        executor=execute,
    )
    ian.offer(request())

    rejected = ian.run_turn()

    assert rejected.status is IanTurnStatus.FAILED
    assert rejected.reason == "ian_checkpoint_rejected"
    assert rejected.queue_depth == 0
    assert executed == ["fetch"]


def test_pressure_safe_requires_literal_boolean():
    executed = []
    ian = Ian(
        clock=FakeClock(),
        resource_sampler=lambda: IanResourceSample(None, None),
        executor=lambda offered, stage, checkpoint: (
            executed.append(stage.name) or IanExecutionResult()
        ),
    )

    rejected = ian.offer(
        request(
            stages=(
                IanStage(
                    "unsafe-string",
                    IanStageKind.DISPLAY,
                    IanResourceClaim(
                        memory_mb=0.0,
                        swap_growth_percent=0.0,
                        pressure_safe="false",
                    ),
                ),
            )
        )
    )

    assert rejected.status is IanOfferStatus.REJECTED
    assert rejected.reason == "ian_invalid_request"
    assert ian.run_turn().status is IanTurnStatus.IDLE
    assert executed == []


def test_ian_deadline_wins_over_cancellation_before_and_after_execution():
    before_clock = FakeClock()
    before_canceled = {"value": False}
    before = Ian(
        clock=before_clock,
        resource_sampler=lambda: IanResourceSample(300.0, 10.0),
        executor=lambda offered, stage, checkpoint: IanExecutionResult(),
        cancellation_probe=lambda offered: before_canceled["value"],
    )
    before.offer(request(deadline_monotonic=11.0))
    before_clock.advance(2.0)
    before_canceled["value"] = True

    before_turn = before.run_turn()

    after_clock = FakeClock()
    after_canceled = {"value": False}

    def execute_after(offered, stage, checkpoint):
        after_clock.advance(2.0)
        after_canceled["value"] = True
        return IanExecutionResult(result="stale")

    after = Ian(
        clock=after_clock,
        resource_sampler=lambda: IanResourceSample(300.0, 10.0),
        executor=execute_after,
        cancellation_probe=lambda offered: after_canceled["value"],
    )
    after.offer(request(deadline_monotonic=11.0))

    after_turn = after.run_turn()

    assert before_turn.status is IanTurnStatus.DEADLINE_EXPIRED
    assert before_turn.reason == "ian_deadline_expired"
    assert after_turn.status is IanTurnStatus.DEADLINE_EXPIRED
    assert after_turn.reason == "ian_deadline_expired_after_execution"


def test_ian_classifies_executor_deadline_exception_as_deadline_expired():
    deadline_error = TaskDeadlineExceeded("executor deadline")

    def execute(offered, stage, checkpoint):
        raise deadline_error

    ian = Ian(
        clock=FakeClock(),
        resource_sampler=lambda: IanResourceSample(300.0, 10.0),
        executor=execute,
    )
    ian.offer(request())

    expired = ian.run_turn()

    assert expired.status is IanTurnStatus.DEADLINE_EXPIRED
    assert expired.reason == "ian_execution_deadline_exceeded"
    assert expired.error is deadline_error
    assert expired.queue_depth == 0


def test_ian_drain_start_turn_immediately_runs_admissible_urgent_work():
    executed = []
    ian = Ian(
        clock=FakeClock(),
        resource_sampler=lambda: IanResourceSample(114.0, 10.0),
        executor=lambda offered, stage, checkpoint: (
            executed.append(offered.request_id)
            or IanExecutionResult(result="done")
        ),
        starvation_seconds=0.0,
        drain_seconds=10.0,
    )
    ian.offer(
        request(
            request_id="heavy",
            instance_uuid="heavy",
            stages=(
                IanStage(
                    "render",
                    IanStageKind.RENDER,
                    IanResourceClaim(45.0),
                ),
            ),
        )
    )
    ian.offer(
        request(
            request_id="manual",
            instance_uuid="manual",
            priority=IanPriority.MANUAL,
            stages=(
                IanStage(
                    "manual-work",
                    IanStageKind.NORMALIZE,
                    IanResourceClaim(10.0),
                ),
            ),
        )
    )

    first = ian.run_turn()
    second = ian.run_turn()

    assert first.status is IanTurnStatus.SUCCEEDED
    assert first.request.request_id == "manual"
    assert second.status is IanTurnStatus.DRAINING
    assert second.request.request_id == "heavy"
    assert executed == ["manual"]
