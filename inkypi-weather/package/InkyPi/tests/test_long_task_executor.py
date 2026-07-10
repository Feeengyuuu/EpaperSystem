import threading
import time
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from runtime.long_task_executor import (
    InstanceIdentity,
    LongTaskExecutor,
    LongTaskQueueFull,
    bind_long_task_runtime,
    current_instance_identity,
    current_task_context,
)
from runtime.refresh_contracts import TaskContext


def _echo_task(payload, _cancel_event):
    return {"value": payload["value"]}


def _blocking_task(_payload, cancel_event):
    while not cancel_event.wait(0.01):
        pass
    return {"unexpected": True}


def _ignores_cancel_task(_payload, _cancel_event):
    while True:
        time.sleep(0.02)


def _context(seconds):
    return TaskContext.never_cancelled(
        deadline_monotonic=time.monotonic() + seconds,
    )


def _wait_for_active(executor, timeout=2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if executor.active_processes:
            return
        time.sleep(0.01)
    raise AssertionError("isolated process did not start")


def test_capacity_is_one_running_plus_one_queued_and_deadline_reclaims_process():
    executor = LongTaskExecutor(
        {"block": _blocking_task, "echo": _echo_task},
        max_workers=1,
        max_queue=1,
        poll_interval_seconds=0.01,
        terminate_grace_seconds=0.05,
    )
    try:
        running = executor.submit(
            "block",
            {},
            context=_context(0.25),
            instance_identity=InstanceIdentity("one", 1, 1),
        )
        queued = executor.submit(
            "echo",
            {"value": 42},
            context=_context(3),
            instance_identity=InstanceIdentity("two", 1, 1),
        )

        with pytest.raises(LongTaskQueueFull):
            executor.submit(
                "echo",
                {"value": 99},
                context=_context(3),
                instance_identity=InstanceIdentity("three", 1, 1),
            )

        assert running.result(timeout=3).status == "abandoned"
        completed = queued.result(timeout=3)
        assert completed.status == "succeeded"
        assert completed.value == {"value": 42}
        assert executor.active_processes == ()
    finally:
        executor.shutdown(deadline_monotonic=time.monotonic() + 2)


def test_cancel_terminates_running_process_and_never_accepts_its_late_result():
    executor = LongTaskExecutor(
        {"block": _ignores_cancel_task},
        poll_interval_seconds=0.01,
        terminate_grace_seconds=0.05,
    )
    try:
        handle = executor.submit(
            "block",
            {},
            context=_context(3),
            instance_identity=InstanceIdentity("one", 1, 1),
        )
        _wait_for_active(executor)

        assert handle.cancel()
        assert handle.result(timeout=2).status == "canceled"
        assert executor.active_processes == ()
    finally:
        executor.shutdown(deadline_monotonic=time.monotonic() + 2)


def test_identity_is_revalidated_in_parent_before_success_is_published():
    executor = LongTaskExecutor({"echo": _echo_task})
    try:
        handle = executor.submit(
            "echo",
            {"value": "late"},
            context=_context(3),
            instance_identity=InstanceIdentity("changed", 4, 7),
            identity_validator=lambda identity: identity.settings_revision == 8,
        )

        result = handle.result(timeout=3)

        assert result.status == "stale"
        assert result.value is None
    finally:
        executor.shutdown(deadline_monotonic=time.monotonic() + 2)


def test_shutdown_cancels_queued_work_and_reaps_the_active_child():
    executor = LongTaskExecutor(
        {"block": _blocking_task, "echo": _echo_task},
        poll_interval_seconds=0.01,
        terminate_grace_seconds=0.05,
    )
    running = executor.submit(
        "block",
        {},
        context=_context(10),
        instance_identity=InstanceIdentity("one", 1, 1),
    )
    queued = executor.submit(
        "echo",
        {"value": 1},
        context=_context(10),
        instance_identity=InstanceIdentity("two", 1, 1),
    )
    _wait_for_active(executor)

    executor.shutdown(deadline_monotonic=time.monotonic() + 2)

    assert running.result(timeout=1).status == "canceled"
    assert queued.result(timeout=1).status == "canceled"
    assert executor.active_processes == ()


def test_runtime_binding_is_scoped_and_keeps_identity_immutable():
    context = _context(3)
    identity = InstanceIdentity("instance", 2, 5)

    assert current_task_context() is None
    assert current_instance_identity() is None
    with bind_long_task_runtime(context, identity):
        assert current_task_context() is context
        assert current_instance_identity() == identity
        with pytest.raises(Exception):
            identity.settings_revision = 6

    assert current_task_context() is None
    assert current_instance_identity() is None


def test_payload_rejects_objects_that_cannot_cross_the_process_boundary():
    executor = LongTaskExecutor({"echo": _echo_task})
    try:
        with pytest.raises(TypeError, match="primitive"):
            executor.submit(
                "echo",
                {"event": threading.Event()},
                context=_context(3),
                instance_identity=InstanceIdentity("one", 1, 1),
            )
    finally:
        executor.shutdown(deadline_monotonic=time.monotonic() + 2)
