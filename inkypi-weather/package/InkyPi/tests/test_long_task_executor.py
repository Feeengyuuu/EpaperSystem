import threading
import time
import sys
import os
import signal
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from runtime.long_task_executor import (
    InstanceIdentity,
    LongTaskExecutor,
    LongTaskQueueFull,
    bind_long_task_runtime,
    current_instance_identity,
    current_instance_identity_validator,
    current_parallel_image_runner,
    current_task_context,
)
from runtime import long_task_executor as long_task_module
from runtime.refresh_contracts import TaskContext
from runtime.resource_governor import (
    AI_GENERATION,
    CHROMIUM,
    HEAVY_CHILD,
    PROVIDER_IO,
    PROVIDER_IO_EXCLUSIVE,
    RuntimeResourceGovernor,
)


def _echo_task(payload, _cancel_event):
    return {"value": payload["value"]}


def _blocking_task(_payload, cancel_event):
    while not cancel_event.wait(0.01):
        pass
    return {"unexpected": True}


def _ignores_cancel_task(_payload, _cancel_event):
    while True:
        time.sleep(0.02)


def _overlap_probe_task(payload, _cancel_event):
    root = Path(payload["root"])
    ready = root / payload["ready"]
    peer_ready = root / payload["peer_ready"]
    ready.write_text("ready", encoding="ascii")
    peer_deadline = time.monotonic() + 1.0
    while not peer_ready.exists() and time.monotonic() < peer_deadline:
        time.sleep(0.005)

    active = root / "active"
    try:
        descriptor = os.open(active, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        (root / "overlap").write_text("overlap", encoding="ascii")
        return {"overlap": True}
    try:
        os.close(descriptor)
        time.sleep(0.15)
        return {"overlap": False}
    finally:
        try:
            active.unlink()
        except FileNotFoundError:
            pass


class _TrackingLease:
    def __init__(self):
        self.release_calls = 0

    def release(self):
        self.release_calls += 1

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.release()


class _TrackingGovernor:
    def __init__(self):
        self.leases = []
        self.kinds = []

    def acquire(self, kind, _claim, context):
        context.raise_if_cancelled()
        lease = _TrackingLease()
        self.kinds.append(kind)
        self.leases.append(lease)
        return lease


class _FakePipeEnd:
    def close(self):
        return None


class _FakeReceiver(_FakePipeEnd):
    def __init__(self, message=None):
        self.message = message

    def poll(self, timeout=0):
        if self.message is not None:
            return True
        time.sleep(min(max(0.0, float(timeout)), 0.01))
        return False

    def recv(self):
        message = self.message
        self.message = None
        return message


class _NeverExitProcess:
    pid = 9876

    def __init__(self):
        self.started = False
        self.terminated = False
        self.killed = False
        self.closed = False

    def start(self):
        self.started = True

    def is_alive(self):
        return self.started

    def join(self, timeout=None):
        return None

    def terminate(self):
        self.terminated = True

    def kill(self):
        self.killed = True

    def close(self):
        self.closed = True


class _DirectSignalStopsProcess(_NeverExitProcess):
    pid = 9877

    def terminate(self):
        super().terminate()
        self.started = False

    def kill(self):
        super().kill()
        self.started = False


class _NeverExitProcessContext:
    def __init__(self, *, message=None, process=None):
        self.receiver = _FakeReceiver(message)
        self.process = process if process is not None else _NeverExitProcess()

    def Pipe(self, duplex=False):
        assert duplex is False
        return self.receiver, _FakePipeEnd()

    def Event(self):
        return threading.Event()

    def Process(self, **_kwargs):
        return self.process


class _ExitedProcess(_NeverExitProcess):
    def start(self):
        self.started = False


class _ProcessGroupReadyContext(_NeverExitProcessContext):
    def __init__(self, *, message, process):
        super().__init__(message=message, process=process)
        self.events = []

    def Event(self):
        event = threading.Event()
        self.events.append(event)
        if len(self.events) == 2:
            event.set()
        return event


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


def test_heavy_child_capacity_is_shared_across_executor_instances(tmp_path):
    first = LongTaskExecutor({"probe": _overlap_probe_task}, max_queue=0)
    second = LongTaskExecutor({"probe": _overlap_probe_task}, max_queue=0)
    try:
        first_handle = first.submit(
            "probe",
            {
                "root": str(tmp_path),
                "ready": "first-ready",
                "peer_ready": "second-ready",
            },
            context=_context(5),
            instance_identity=InstanceIdentity("first", 1, 1),
        )
        second_handle = second.submit(
            "probe",
            {
                "root": str(tmp_path),
                "ready": "second-ready",
                "peer_ready": "first-ready",
            },
            context=_context(5),
            instance_identity=InstanceIdentity("second", 1, 1),
        )

        assert first_handle.result(timeout=5).status == "succeeded"
        assert second_handle.result(timeout=5).status == "succeeded"
        assert not (tmp_path / "overlap").exists()
    finally:
        first.shutdown(deadline_monotonic=time.monotonic() + 2)
        second.shutdown(deadline_monotonic=time.monotonic() + 2)


def test_long_task_holds_provider_exclusive_after_heavy_child_until_reap():
    governor = _TrackingGovernor()
    executor = LongTaskExecutor(
        {"echo": _echo_task},
        max_queue=0,
        resource_governor=governor,
    )
    try:
        result = executor.submit(
            "echo",
            {"value": "bounded-provider-lane"},
            context=_context(3),
            instance_identity=InstanceIdentity("provider-child", 1, 1),
        ).result(timeout=3)

        assert result.status == "succeeded"
        assert governor.kinds == [HEAVY_CHILD, PROVIDER_IO_EXCLUSIVE]
        assert [lease.release_calls for lease in governor.leases] == [1, 1]
    finally:
        executor.shutdown(deadline_monotonic=time.monotonic() + 2)


def test_parent_provider_io_blocks_child_start_and_deadline_returns_heavy_slot():
    governor = RuntimeResourceGovernor()
    provider = governor.acquire(
        PROVIDER_IO,
        {"host": "parent.example"},
        _context(2),
    )
    executor = LongTaskExecutor(
        {"echo": _echo_task},
        max_queue=0,
        resource_governor=governor,
    )
    try:
        result = executor.submit(
            "echo",
            {"value": "must-not-start"},
            context=_context(0.15),
            instance_identity=InstanceIdentity("provider-wait", 1, 1),
        ).result(timeout=2)

        assert result.status == "abandoned"
        assert result.error_code == "deadline_expired"
        assert executor.active_processes == ()
        with governor.acquire(HEAVY_CHILD, {}, _context(1)):
            pass
    finally:
        provider.release()
        executor.shutdown(deadline_monotonic=time.monotonic() + 2)


def test_child_provider_exclusive_blocks_parent_io_until_child_is_reaped():
    governor = RuntimeResourceGovernor()
    executor = LongTaskExecutor(
        {"block": _blocking_task},
        max_queue=0,
        poll_interval_seconds=0.01,
        resource_governor=governor,
    )
    handle = executor.submit(
        "block",
        {},
        context=_context(3),
        instance_identity=InstanceIdentity("exclusive-child", 1, 1),
    )
    _wait_for_active(executor)
    provider_outcome = []
    provider_thread = threading.Thread(
        target=lambda: provider_outcome.append(
            governor.acquire(
                PROVIDER_IO,
                {"host": "parent.example"},
                _context(2),
            )
        )
    )
    provider_thread.start()
    time.sleep(0.05)
    assert provider_thread.is_alive()

    try:
        assert handle.cancel()
        assert handle.result(timeout=2).status == "canceled"
        provider_thread.join(timeout=1)
        assert not provider_thread.is_alive()
        provider_outcome[0].release()
    finally:
        executor.shutdown(deadline_monotonic=time.monotonic() + 2)


def test_cancel_while_waiting_for_provider_exclusive_returns_all_parent_permits():
    governor = RuntimeResourceGovernor()
    provider = governor.acquire(
        PROVIDER_IO,
        {"host": "parent.example"},
        _context(2),
    )
    executor = LongTaskExecutor(
        {"echo": _echo_task},
        max_queue=0,
        resource_governor=governor,
    )
    try:
        handle = executor.submit(
            "echo",
            {"value": "cancel-before-start"},
            context=_context(2),
            instance_identity=InstanceIdentity("provider-cancel", 1, 1),
        )
        time.sleep(0.05)
        assert executor.active_processes == ()
        assert handle.cancel()
        assert handle.result(timeout=1).status == "canceled"
        with governor.acquire(HEAVY_CHILD, {}, _context(1)):
            pass
    finally:
        provider.release()
        executor.shutdown(deadline_monotonic=time.monotonic() + 2)


def test_heavy_child_wait_honors_deadline_and_releases_capacity():
    holder = LongTaskExecutor({"block": _blocking_task}, max_queue=0)
    waiter = LongTaskExecutor({"echo": _echo_task}, max_queue=0)
    follower = LongTaskExecutor({"echo": _echo_task}, max_queue=0)
    try:
        held = holder.submit(
            "block",
            {},
            context=_context(5),
            instance_identity=InstanceIdentity("holder", 1, 1),
        )
        _wait_for_active(holder)

        expired = waiter.submit(
            "echo",
            {"value": "too-late"},
            context=_context(0.15),
            instance_identity=InstanceIdentity("waiter", 1, 1),
        )
        assert expired.result(timeout=2).status == "abandoned"
        assert waiter.active_processes == ()

        assert held.cancel()
        assert held.result(timeout=2).status == "canceled"
        completed = follower.submit(
            "echo",
            {"value": "after-release"},
            context=_context(3),
            instance_identity=InstanceIdentity("follower", 1, 1),
        ).result(timeout=3)
        assert completed.status == "succeeded"
        assert completed.value == {"value": "after-release"}
    finally:
        holder.shutdown(deadline_monotonic=time.monotonic() + 2)
        waiter.shutdown(deadline_monotonic=time.monotonic() + 2)
        follower.shutdown(deadline_monotonic=time.monotonic() + 2)


def test_heavy_child_wait_honors_handle_cancellation():
    holder = LongTaskExecutor({"block": _blocking_task}, max_queue=0)
    waiter = LongTaskExecutor({"echo": _echo_task}, max_queue=0)
    try:
        held = holder.submit(
            "block",
            {},
            context=_context(5),
            instance_identity=InstanceIdentity("holder", 1, 1),
        )
        _wait_for_active(holder)
        waiting = waiter.submit(
            "echo",
            {"value": "canceled"},
            context=_context(5),
            instance_identity=InstanceIdentity("waiter", 1, 1),
        )

        assert waiting.cancel()
        assert waiting.result(timeout=2).status == "canceled"
        assert waiter.active_processes == ()
        assert held.cancel()
        assert held.result(timeout=2).status == "canceled"
    finally:
        holder.shutdown(deadline_monotonic=time.monotonic() + 2)
        waiter.shutdown(deadline_monotonic=time.monotonic() + 2)


def test_additional_chromium_claim_is_parent_owned_and_deadline_bounded():
    governor = RuntimeResourceGovernor()
    held = governor.acquire(CHROMIUM, {}, _context(2))
    executor = LongTaskExecutor({"echo": _echo_task}, max_queue=0)
    follower = LongTaskExecutor({"echo": _echo_task}, max_queue=0)
    try:
        blocked = executor.submit(
            "echo",
            {"value": "blocked"},
            context=_context(0.15),
            instance_identity=InstanceIdentity("browser-child", 1, 1),
            resource_kinds=(CHROMIUM,),
        )
        assert blocked.result(timeout=2).status == "abandoned"
        assert executor.active_processes == ()
    finally:
        held.release()

    try:
        completed = follower.submit(
            "echo",
            {"value": "released"},
            context=_context(3),
            instance_identity=InstanceIdentity("follower", 1, 1),
        ).result(timeout=3)
        assert completed.status == "succeeded"
        assert completed.value == {"value": "released"}
    finally:
        executor.shutdown(deadline_monotonic=time.monotonic() + 2)
        follower.shutdown(deadline_monotonic=time.monotonic() + 2)


def test_child_resource_claims_reject_lock_order_inverting_ai_generation():
    executor = LongTaskExecutor({"echo": _echo_task}, max_queue=0)
    try:
        with pytest.raises(ValueError, match="unsupported child resource"):
            executor.submit(
                "echo",
                {"value": "unsafe"},
                context=_context(3),
                instance_identity=InstanceIdentity("unsafe", 1, 1),
                resource_kinds=(AI_GENERATION,),
            )
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


def test_success_message_from_unreapable_child_is_failed_and_quarantined():
    process_context = _NeverExitProcessContext(
        message=("succeeded", {"value": 42}, None, None),
    )
    governor = _TrackingGovernor()
    executor = LongTaskExecutor(
        {"echo": _echo_task},
        max_queue=0,
        poll_interval_seconds=0.01,
        terminate_grace_seconds=0.01,
        multiprocessing_context=process_context,
        resource_governor=governor,
    )
    try:
        result = executor.submit(
            "echo",
            {"value": 42},
            context=_context(2),
            instance_identity=InstanceIdentity("leaked-success", 1, 1),
        ).result(timeout=1)

        assert result.status == "failed"
        assert result.error_code == "child_process_leaked"
        assert result.value is None
        assert executor.active_processes == (9876,)
        assert process_context.process.terminated
        assert process_context.process.killed
        assert not process_context.process.closed
        assert governor.kinds == [HEAVY_CHILD, PROVIDER_IO_EXCLUSIVE]
        assert [lease.release_calls for lease in governor.leases] == [0, 0]
    finally:
        executor.shutdown(deadline_monotonic=time.monotonic() + 1)


def test_cancel_of_unreapable_child_returns_leak_failure_without_reusing_capacity():
    process_context = _NeverExitProcessContext()
    governor = _TrackingGovernor()
    executor = LongTaskExecutor(
        {"block": _blocking_task},
        max_queue=0,
        poll_interval_seconds=0.01,
        terminate_grace_seconds=0.01,
        multiprocessing_context=process_context,
        resource_governor=governor,
    )
    try:
        handle = executor.submit(
            "block",
            {},
            context=_context(2),
            instance_identity=InstanceIdentity("leaked-cancel", 1, 1),
        )
        _wait_for_active(executor)
        assert handle.cancel()

        result = handle.result(timeout=1)
        assert result.status == "failed"
        assert result.error_code == "child_process_leaked"
        assert executor.active_processes == (9876,)
        assert process_context.process.terminated
        assert process_context.process.killed
        assert not process_context.process.closed
        assert governor.kinds == [HEAVY_CHILD, PROVIDER_IO_EXCLUSIVE]
        assert [lease.release_calls for lease in governor.leases] == [0, 0]
    finally:
        executor.shutdown(deadline_monotonic=time.monotonic() + 1)


def test_posix_long_task_terminates_the_verified_child_process_group(monkeypatch):
    process = _NeverExitProcess()
    process.pid = 6543
    process_context = _NeverExitProcessContext(
        message=("succeeded", {"value": "group"}, None, None),
        process=process,
    )
    signals = []
    sigterm = 15
    sigkill = 9

    monkeypatch.setattr(long_task_module, "_is_posix_platform", lambda: True)
    monkeypatch.setattr(long_task_module.signal, "SIGTERM", sigterm, raising=False)
    monkeypatch.setattr(long_task_module.signal, "SIGKILL", sigkill, raising=False)
    monkeypatch.setattr(
        long_task_module.os, "getpgid", lambda pid: pid, raising=False
    )
    monkeypatch.setattr(
        long_task_module.os, "getpgrp", lambda: 7000, raising=False
    )

    def killpg(pgid, signal_number):
        if signal_number == 0:
            if process.started:
                return
            raise ProcessLookupError(pgid)
        signals.append((pgid, signal_number))
        if signal_number == sigkill:
            process.started = False

    monkeypatch.setattr(long_task_module.os, "killpg", killpg, raising=False)
    executor = LongTaskExecutor(
        {"echo": _echo_task},
        max_queue=0,
        terminate_grace_seconds=0.01,
        multiprocessing_context=process_context,
        resource_governor=_TrackingGovernor(),
    )
    try:
        result = executor.submit(
            "echo",
            {"value": "group"},
            context=_context(2),
            instance_identity=InstanceIdentity("group", 1, 1),
        ).result(timeout=1)

        assert result.status == "succeeded"
        assert signals == [(6543, sigterm), (6543, sigkill)]
        assert not process.terminated
        assert not process.killed
        assert executor.active_processes == ()
    finally:
        executor.shutdown(deadline_monotonic=time.monotonic() + 1)


@pytest.mark.parametrize(
    "message",
    [
        ("succeeded", {"value": "must-not-publish"}, None, None),
        ("failed", None, "provider_failed", "provider failed"),
    ],
)
def test_terminal_result_is_rejected_when_a_process_group_descendant_survives(
    monkeypatch,
    message,
):
    process = _ExitedProcess()
    process.pid = 6550
    process_context = _ProcessGroupReadyContext(
        message=message,
        process=process,
    )
    governor = _TrackingGovernor()
    group_signals = []
    sigterm = 15
    sigkill = 9

    monkeypatch.setattr(long_task_module, "_is_posix_platform", lambda: True)
    monkeypatch.setattr(long_task_module.signal, "SIGTERM", sigterm, raising=False)
    monkeypatch.setattr(long_task_module.signal, "SIGKILL", sigkill, raising=False)
    monkeypatch.setattr(long_task_module.os, "getpgrp", lambda: 7000, raising=False)

    def killpg(pgid, signal_number):
        assert pgid == process.pid
        group_signals.append(signal_number)
        # Signal 0 keeps reporting a stubborn Chromium descendant even after
        # the direct LongTask child has already exited.

    monkeypatch.setattr(long_task_module.os, "killpg", killpg, raising=False)
    executor = LongTaskExecutor(
        {"echo": _echo_task},
        max_queue=0,
        terminate_grace_seconds=0.01,
        multiprocessing_context=process_context,
        resource_governor=governor,
    )
    try:
        result = executor.submit(
            "echo",
            {"value": "terminal"},
            context=_context(2),
            instance_identity=InstanceIdentity("descendant-leak", 1, 1),
        ).result(timeout=1)

        assert result.status == "failed"
        assert result.error_code == "child_process_leaked"
        assert result.value is None
        assert sigterm in group_signals
        assert sigkill in group_signals
        assert 0 in group_signals
        assert executor.active_processes == (process.pid,)
        assert not process.closed
        assert governor.kinds == [HEAVY_CHILD, PROVIDER_IO_EXCLUSIVE]
        assert [lease.release_calls for lease in governor.leases] == [0, 0]
    finally:
        executor.shutdown(deadline_monotonic=time.monotonic() + 1)


def test_posix_long_task_never_signals_the_parent_process_group(monkeypatch):
    process = _DirectSignalStopsProcess()
    process.pid = 6544
    process_context = _NeverExitProcessContext(
        message=("succeeded", {"value": "fallback"}, None, None),
        process=process,
    )
    monkeypatch.setattr(long_task_module, "_is_posix_platform", lambda: True)
    monkeypatch.setattr(
        long_task_module.os, "getpgid", lambda _pid: 7000, raising=False
    )
    monkeypatch.setattr(
        long_task_module.os, "getpgrp", lambda: 7000, raising=False
    )
    monkeypatch.setattr(
        long_task_module.os,
        "killpg",
        lambda *_args: pytest.fail("parent process group was signaled"),
        raising=False,
    )
    executor = LongTaskExecutor(
        {"echo": _echo_task},
        max_queue=0,
        terminate_grace_seconds=0.01,
        multiprocessing_context=process_context,
        resource_governor=_TrackingGovernor(),
    )
    try:
        result = executor.submit(
            "echo",
            {"value": "fallback"},
            context=_context(2),
            instance_identity=InstanceIdentity("parent-group", 1, 1),
        ).result(timeout=1)

        assert result.status == "succeeded"
        assert process.terminated
        assert not process.killed
    finally:
        executor.shutdown(deadline_monotonic=time.monotonic() + 1)


def test_non_posix_long_task_uses_direct_process_termination(monkeypatch):
    process = _DirectSignalStopsProcess()
    process.pid = 6545
    process_context = _NeverExitProcessContext(
        message=("succeeded", {"value": "direct"}, None, None),
        process=process,
    )
    monkeypatch.setattr(long_task_module, "_is_posix_platform", lambda: False)
    monkeypatch.setattr(
        long_task_module.os,
        "killpg",
        lambda *_args: pytest.fail("non-POSIX path called killpg"),
        raising=False,
    )
    executor = LongTaskExecutor(
        {"echo": _echo_task},
        max_queue=0,
        terminate_grace_seconds=0.01,
        multiprocessing_context=process_context,
        resource_governor=_TrackingGovernor(),
    )
    try:
        result = executor.submit(
            "echo",
            {"value": "direct"},
            context=_context(2),
            instance_identity=InstanceIdentity("direct", 1, 1),
        ).result(timeout=1)

        assert result.status == "succeeded"
        assert process.terminated
        assert not process.killed
    finally:
        executor.shutdown(deadline_monotonic=time.monotonic() + 1)


def test_posix_child_entry_creates_one_session_and_marks_nested_renderers(monkeypatch):
    observations = []

    class Sender:
        def __init__(self):
            self.messages = []

        def send(self, message):
            self.messages.append(message)

        def close(self):
            return None

    sender = Sender()
    monkeypatch.setattr(long_task_module, "_is_posix_platform", lambda: True)
    monkeypatch.setattr(
        long_task_module,
        "_LONG_TASK_CHILD_PROCESS_GROUP_ACTIVE",
        False,
        raising=False,
    )
    monkeypatch.setattr(
        long_task_module.os,
        "setsid",
        lambda: observations.append("setsid"),
        raising=False,
    )

    def task(_payload, _cancel_event):
        observations.append(long_task_module.long_task_child_process_group_active())
        return {"ok": True}

    process_group_ready = threading.Event()
    terminal_hold = threading.Event()
    terminal_hold.set()
    long_task_module._child_main(
        task,
        {},
        threading.Event(),
        process_group_ready,
        terminal_hold,
        sender,
    )

    assert observations == ["setsid", True]
    assert process_group_ready.is_set()
    assert sender.messages == [("succeeded", {"ok": True}, None, None)]


def test_posix_child_group_leader_stays_alive_after_sending_terminal_result(
    monkeypatch,
):
    sent = threading.Event()

    class Sender:
        def __init__(self):
            self.messages = []

        def send(self, message):
            self.messages.append(message)
            sent.set()

        def close(self):
            return None

    sender = Sender()
    ready = threading.Event()
    terminal_hold = threading.Event()
    monkeypatch.setattr(long_task_module, "_is_posix_platform", lambda: True)
    monkeypatch.setattr(
        long_task_module,
        "_LONG_TASK_CHILD_PROCESS_GROUP_ACTIVE",
        False,
        raising=False,
    )
    monkeypatch.setattr(
        long_task_module.os,
        "setsid",
        lambda: None,
        raising=False,
    )
    child = threading.Thread(
        target=long_task_module._child_main,
        args=(
            lambda _payload, _cancel: {"ok": True},
            {},
            threading.Event(),
            ready,
            terminal_hold,
            sender,
        ),
    )

    child.start()
    assert sent.wait(1)
    assert ready.is_set()
    assert child.is_alive()

    terminal_hold.set()
    child.join(timeout=1)
    assert not child.is_alive()


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
    assert current_instance_identity_validator() is None
    with bind_long_task_runtime(context, identity):
        assert current_task_context() is context
        assert current_instance_identity() == identity
        assert current_instance_identity_validator() is None
        with pytest.raises(Exception):
            identity.settings_revision = 6

    assert current_task_context() is None
    assert current_instance_identity() is None
    assert current_instance_identity_validator() is None


def test_runtime_binding_scopes_optional_parent_identity_validator():
    context = _context(3)
    identity = InstanceIdentity("instance", 2, 5)
    validator = lambda candidate: candidate == identity

    with bind_long_task_runtime(
        context,
        identity,
        identity_validator=validator,
    ):
        assert current_instance_identity_validator() is validator
        with bind_long_task_runtime(context, identity):
            assert current_instance_identity_validator() is None
        assert current_instance_identity_validator() is validator

    assert current_instance_identity_validator() is None


def test_runtime_binding_scopes_optional_parallel_image_runner():
    context = _context(3)
    identity = InstanceIdentity("instance", 2, 5)
    outer_runner = object()
    inner_runner = object()

    assert current_parallel_image_runner() is None
    with bind_long_task_runtime(
        context,
        identity,
        parallel_image_runner=outer_runner,
    ):
        assert current_parallel_image_runner() is outer_runner
        with bind_long_task_runtime(
            context,
            identity,
            parallel_image_runner=inner_runner,
        ):
            assert current_parallel_image_runner() is inner_runner
        assert current_parallel_image_runner() is outer_runner

    assert current_parallel_image_runner() is None


def test_runtime_binding_restores_parallel_image_runner_after_exception():
    context = _context(3)
    identity = InstanceIdentity("instance", 2, 5)
    runner = object()

    with pytest.raises(RuntimeError, match="boom"):
        with bind_long_task_runtime(
            context,
            identity,
            parallel_image_runner=runner,
        ):
            assert current_parallel_image_runner() is runner
            raise RuntimeError("boom")

    assert current_parallel_image_runner() is None


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
