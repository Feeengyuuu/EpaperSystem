import os
from pathlib import Path
import subprocess
import threading
import time
from types import SimpleNamespace

from PIL import Image
import pytest

from src.runtime.cache_lifecycle import (
    CleanupBudget,
    LifecycleAggregate,
    LifecycleAllowance,
)
from runtime.refresh_contracts import TaskContext
from runtime.refresh_policy import ResourceSample
from runtime.resource_deferral import ResourcePressureDeferred
from runtime.resource_governor import CHROMIUM, RuntimeResourceGovernor
from runtime.long_task_executor import (
    InstanceIdentity,
    bind_long_task_runtime,
    current_task_context,
)
from src.utils import browser_renderer as browser_renderer_module
from src.utils.browser_renderer import BrowserRenderer


def _context(seconds=2):
    return TaskContext.never_cancelled(
        deadline_monotonic=time.monotonic() + seconds,
    )


def _route_fake_process_group_signals(monkeypatch, processes):
    """Keep fake PIDs away from the host while exercising POSIX cleanup."""

    if os.name == "nt":
        return

    def killpg(pid, signal_number):
        process = next(
            (
                candidate
                for candidate in reversed(tuple(processes()))
                if candidate.pid == pid and candidate.poll() is None
            ),
            None,
        )
        if process is None:
            raise ProcessLookupError(pid)
        if signal_number == 0:
            return
        if signal_number == browser_renderer_module.signal.SIGTERM:
            process.terminate()
            return
        if signal_number == browser_renderer_module.signal.SIGKILL:
            process.kill()
            return
        raise AssertionError(f"unexpected signal: {signal_number}")

    monkeypatch.setattr(browser_renderer_module.os, "killpg", killpg)


def _cleanup_allowance(
    *,
    scanned=64,
    deleted=16,
    deleted_bytes=1024 * 1024,
    duration=1.0,
    clock=lambda: 0.0,
):
    return LifecycleAllowance(
        CleanupBudget(
            max_scanned_entries=scanned,
            max_deleted_entries=deleted,
            max_deleted_bytes=deleted_bytes,
            max_duration_seconds=duration,
        ).start(clock()),
        LifecycleAggregate(),
        clock=clock,
    )


def _abandoned_job(root, name, *, now, age, payload=b"residue"):
    job = root / name
    job.mkdir(parents=True)
    (job / "payload.bin").write_bytes(payload)
    modified = now - age
    os.utime(job / "payload.bin", (modified, modified))
    os.utime(job, (modified, modified))
    return job


class CleanupSlot:
    def __init__(self, available=True):
        self.available = available
        self.acquire_calls = []
        self.release_calls = 0

    def acquire(self, blocking=True, timeout=None):
        self.acquire_calls.append((blocking, timeout))
        return self.available

    def release(self):
        self.release_calls += 1


class TrackingBrowserLease:
    def __init__(self):
        self.release_calls = 0

    def release(self):
        self.release_calls += 1

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.release()


class TrackingBrowserGovernor:
    def __init__(self):
        self.leases = []

    def acquire(self, _kind, _claim, context):
        context.raise_if_cancelled()
        lease = TrackingBrowserLease()
        self.leases.append(lease)
        return lease


class TimeoutProcess:
    returncode = None
    pid = 1234

    def __init__(self):
        self.terminated = False
        self.killed = False
        self.wait_calls = 0

    def wait(self, timeout=None):
        self.wait_calls += 1
        if self.wait_calls <= 2:
            raise subprocess.TimeoutExpired("chromium", timeout)
        self.returncode = -9
        return self.returncode

    def terminate(self):
        self.terminated = True

    def kill(self):
        self.killed = True

    def poll(self):
        return self.returncode


def test_timeout_terminates_kills_waits_and_removes_all_temp_paths(
    tmp_path,
    monkeypatch,
):
    process = TimeoutProcess()
    renderer = BrowserRenderer(
        binary="chromium",
        temp_root=tmp_path,
        popen=lambda *_args, **_kwargs: process,
    )
    _route_fake_process_group_signals(monkeypatch, lambda: (process,))

    result = renderer.render_html(
        "<p>x</p>",
        viewport=(800, 480),
        context=_context(),
        timeout_seconds=0.01,
    )

    assert result is None
    assert process.terminated
    assert process.killed
    assert process.wait_calls == 3
    assert renderer.active_processes == ()
    assert list(tmp_path.iterdir()) == []


def test_nested_long_task_browser_stays_in_parent_process_group(
    tmp_path,
    monkeypatch,
):
    launches = []
    in_long_task_group = {"value": False}

    class CompletingProcess:
        returncode = 0

        def __init__(self, command, pid):
            self.pid = pid
            output = next(
                item.split("=", 1)[1]
                for item in command
                if item.startswith("--screenshot=")
            )
            Image.new("RGB", (80, 48), "white").save(output)

        def wait(self, timeout=None):
            return self.returncode

        def poll(self):
            return self.returncode

    def popen(command, **kwargs):
        launches.append(kwargs)
        return CompletingProcess(command, 8100 + len(launches))

    monkeypatch.setattr(
        browser_renderer_module,
        "_is_posix_platform",
        lambda: True,
        raising=False,
    )
    monkeypatch.setattr(
        browser_renderer_module,
        "long_task_child_process_group_active",
        lambda: in_long_task_group["value"],
        raising=False,
    )
    monkeypatch.setattr(
        browser_renderer_module.os,
        "getpgrp",
        lambda: 9000,
        raising=False,
    )
    monkeypatch.setattr(
        browser_renderer_module.os,
        "getpgid",
        lambda pid: pid,
        raising=False,
    )

    def no_remaining_group(_pgid, signal_number):
        if signal_number == 0:
            raise ProcessLookupError
        pytest.fail("completed standalone group was signaled")

    monkeypatch.setattr(
        browser_renderer_module.os,
        "killpg",
        no_remaining_group,
        raising=False,
    )
    renderer = BrowserRenderer(
        binary="chromium",
        temp_root=tmp_path,
        popen=popen,
        run_as_root=False,
    )

    assert renderer.render_html(
        "<p>standalone</p>",
        viewport=(80, 48),
        context=_context(),
    ) is not None
    in_long_task_group["value"] = True
    assert renderer.render_html(
        "<p>nested</p>",
        viewport=(80, 48),
        context=_context(),
    ) is not None

    assert launches[0]["start_new_session"] is True
    assert "start_new_session" not in launches[1]


def test_nested_long_task_browser_uses_direct_stop_not_its_parent_group(
    tmp_path,
    monkeypatch,
):
    class StubbornProcess:
        pid = 8201
        returncode = None

        def __init__(self):
            self.terminated = False
            self.killed = False

        def poll(self):
            return self.returncode

        def terminate(self):
            self.terminated = True

        def kill(self):
            self.killed = True
            self.returncode = -9

        def wait(self, timeout=None):
            if self.returncode is None:
                raise subprocess.TimeoutExpired("chromium", timeout)
            return self.returncode

    process = StubbornProcess()
    monkeypatch.setattr(
        browser_renderer_module,
        "_is_posix_platform",
        lambda: True,
        raising=False,
    )
    monkeypatch.setattr(
        browser_renderer_module,
        "long_task_child_process_group_active",
        lambda: True,
        raising=False,
    )
    monkeypatch.setattr(
        browser_renderer_module.os,
        "killpg",
        lambda *_args: pytest.fail("nested browser signaled its parent group"),
        raising=False,
    )
    renderer = BrowserRenderer(binary="chromium", temp_root=tmp_path)

    state = renderer._stop_process(process)

    assert state is browser_renderer_module._ProcessStopState.SIGNALLED_EXITED
    assert process.terminated
    assert process.killed


def test_completed_browser_result_is_rejected_when_process_cannot_be_reaped(
    tmp_path,
    monkeypatch,
):
    slot = CleanupSlot()
    governor = TrackingBrowserGovernor()

    class SuccessThenNeverExit:
        returncode = None
        pid = 4123

        def __init__(self, command):
            self.wait_calls = 0
            self.terminated = False
            self.killed = False
            output = next(
                item.split("=", 1)[1]
                for item in command
                if item.startswith("--screenshot=")
            )
            Image.new("RGB", (80, 48), "white").save(output)

        def wait(self, timeout=None):
            self.wait_calls += 1
            if self.wait_calls == 1:
                return 0
            raise subprocess.TimeoutExpired("chromium", timeout)

        def poll(self):
            return None

        def terminate(self):
            self.terminated = True

        def kill(self):
            self.killed = True

    process = None

    def popen(command, **_kwargs):
        nonlocal process
        process = SuccessThenNeverExit(command)
        return process

    monkeypatch.setattr(browser_renderer_module, "_GLOBAL_BROWSER_SLOT", slot)
    renderer = BrowserRenderer(
        binary="chromium",
        temp_root=tmp_path,
        popen=popen,
        resource_governor=governor,
    )
    _route_fake_process_group_signals(monkeypatch, lambda: (process,))

    with pytest.raises(browser_renderer_module.BrowserProcessLeakError):
        renderer.render_html(
            "<p>completed-but-live</p>",
            viewport=(80, 48),
            context=_context(),
        )

    assert renderer.active_processes == (4123,)
    assert renderer.quarantined_processes == (4123,)
    assert process.terminated
    assert process.killed
    assert slot.release_calls == 0
    assert [lease.release_calls for lease in governor.leases] == [0]
    assert any(tmp_path.iterdir())


def test_standalone_leader_exit_with_live_descendant_quarantines_capacity(
    tmp_path,
    monkeypatch,
):
    slot = threading.BoundedSemaphore(1)
    governor = TrackingBrowserGovernor()
    launches = []
    group_signals = []
    sigterm = 15
    sigkill = 9

    class ExitedLeader:
        pid = 4190
        returncode = 0

        def __init__(self, command):
            output = next(
                item.split("=", 1)[1]
                for item in command
                if item.startswith("--screenshot=")
            )
            Image.new("RGB", (80, 48), "white").save(output)

        def wait(self, timeout=None):
            return self.returncode

        def poll(self):
            return self.returncode

    process = None

    def popen(command, **_kwargs):
        nonlocal process
        launches.append(command)
        process = ExitedLeader(command)
        return process

    monkeypatch.setattr(browser_renderer_module, "_GLOBAL_BROWSER_SLOT", slot)
    monkeypatch.setattr(browser_renderer_module, "_is_posix_platform", lambda: True)
    monkeypatch.setattr(
        browser_renderer_module,
        "long_task_child_process_group_active",
        lambda: False,
    )
    monkeypatch.setattr(
        browser_renderer_module.signal, "SIGTERM", sigterm, raising=False
    )
    monkeypatch.setattr(
        browser_renderer_module.signal, "SIGKILL", sigkill, raising=False
    )
    monkeypatch.setattr(
        browser_renderer_module.os,
        "getpgid",
        lambda pid: pid,
        raising=False,
    )
    monkeypatch.setattr(
        browser_renderer_module.os,
        "getpgrp",
        lambda: 9000,
        raising=False,
    )
    monkeypatch.setattr(
        browser_renderer_module,
        "PROCESS_GROUP_REAP_SECONDS",
        0.02,
        raising=False,
    )

    def killpg(pgid, signal_number):
        assert pgid == process.pid
        group_signals.append(signal_number)
        # Signal 0 deliberately keeps reporting a stubborn descendant after
        # both group termination signals and after the leader has exited.

    monkeypatch.setattr(
        browser_renderer_module.os,
        "killpg",
        killpg,
        raising=False,
    )
    renderer = BrowserRenderer(
        binary="chromium",
        temp_root=tmp_path,
        popen=popen,
        resource_governor=governor,
        run_as_root=False,
    )

    with pytest.raises(browser_renderer_module.BrowserProcessLeakError):
        renderer.render_html(
            "<p>leader-exited-descendant-live</p>",
            viewport=(80, 48),
            context=_context(),
        )

    assert sigterm in group_signals
    assert sigkill in group_signals
    assert 0 in group_signals
    assert renderer.active_processes == (process.pid,)
    assert renderer.quarantined_processes == (process.pid,)
    assert [lease.release_calls for lease in governor.leases] == [0]
    assert not slot.acquire(blocking=False)

    second_launches = []
    second = BrowserRenderer(
        binary="chromium",
        temp_root=tmp_path / "second",
        popen=lambda *_args, **_kwargs: second_launches.append(True),
        resource_governor=governor,
        run_as_root=False,
    )
    assert second.render_html(
        "<p>must-not-launch</p>",
        viewport=(80, 48),
        context=_context(0.05),
    ) is None
    assert second_launches == []
    assert [lease.release_calls for lease in governor.leases] == [0]


def test_cancelled_unreapable_browser_is_quarantined_without_reusing_permit(
    tmp_path,
    monkeypatch,
):
    slot = CleanupSlot()
    governor = TrackingBrowserGovernor()
    cancel_event = threading.Event()

    class CancelThenNeverExit:
        returncode = None
        pid = 4124

        def __init__(self):
            self.terminated = False
            self.killed = False

        def wait(self, timeout=None):
            cancel_event.set()
            raise subprocess.TimeoutExpired("chromium", timeout)

        def poll(self):
            return None

        def terminate(self):
            self.terminated = True

        def kill(self):
            self.killed = True

    process = CancelThenNeverExit()
    monkeypatch.setattr(browser_renderer_module, "_GLOBAL_BROWSER_SLOT", slot)
    renderer = BrowserRenderer(
        binary="chromium",
        temp_root=tmp_path,
        popen=lambda *_args, **_kwargs: process,
        resource_governor=governor,
    )
    _route_fake_process_group_signals(monkeypatch, lambda: (process,))
    context = TaskContext(cancel_event, time.monotonic() + 2)

    with pytest.raises(browser_renderer_module.BrowserProcessLeakError):
        renderer.render_html(
            "<p>cancelled-but-live</p>",
            viewport=(80, 48),
            context=context,
        )

    assert renderer.active_processes == (4124,)
    assert renderer.quarantined_processes == (4124,)
    assert process.terminated
    assert process.killed
    assert slot.release_calls == 0
    assert [lease.release_calls for lease in governor.leases] == [0]
    assert any(tmp_path.iterdir())


def test_html_render_inherits_bound_task_context_without_leaking_it(
    tmp_path,
    caplog,
):
    launches = []

    class FailedProcess:
        returncode = 1
        pid = 4321

        def wait(self, timeout=None):
            return self.returncode

        def poll(self):
            return self.returncode

    renderer = BrowserRenderer(
        binary="chromium",
        temp_root=tmp_path,
        popen=lambda command, **_kwargs: launches.append(command)
        or FailedProcess(),
    )
    cancel_event = threading.Event()
    cancel_event.set()
    context = TaskContext(
        cancel_event,
        time.monotonic() + 60,
    )

    with bind_long_task_runtime(
        context,
        InstanceIdentity("weather-instance", 1, 1),
    ):
        caplog.set_level("WARNING", logger="src.utils.browser_renderer")
        result = renderer.render_html(
            "<p>weather</p>",
            viewport=(80, 48),
            retry_once=True,
        )
        assert current_task_context() is context

    assert result is None
    assert launches == []
    assert current_task_context() is None
    assert renderer.negative_cache_size == 0
    assert not any(
        record.getMessage().startswith("Retrying Chromium")
        for record in caplog.records
    )

    renderer.render_html(
        "<p>weather</p>",
        viewport=(80, 48),
        context=_context(),
    )

    assert len(launches) == 1


def test_html_retry_cancellation_after_first_launch_does_not_poison_cache(
    tmp_path,
    caplog,
    monkeypatch,
):
    cancel_event = threading.Event()
    launches = []

    class CancelingTimeoutProcess(TimeoutProcess):
        def wait(self, timeout=None):
            cancel_event.set()
            return super().wait(timeout)

    class SuccessProcess:
        returncode = 0
        pid = 7654

        def __init__(self, command):
            output = next(
                item.split("=", 1)[1]
                for item in command
                if item.startswith("--screenshot=")
            )
            Image.new("RGB", (80, 48), "white").save(output)

        def wait(self, timeout=None):
            return self.returncode

        def poll(self):
            return self.returncode

    first = CancelingTimeoutProcess()

    def popen(command, **_kwargs):
        launches.append(command)
        return first if len(launches) == 1 else SuccessProcess(command)

    renderer = BrowserRenderer(
        binary="chromium",
        temp_root=tmp_path,
        popen=popen,
        resource_sampler=lambda: ResourceSample(
            available_mb=512,
            swap_percent=0,
        ),
    )
    _route_fake_process_group_signals(monkeypatch, lambda: (first,))
    context = TaskContext(
        cancel_event,
        time.monotonic() + 60,
    )
    caplog.set_level("WARNING", logger="src.utils.browser_renderer")

    with bind_long_task_runtime(
        context,
        InstanceIdentity("weather-instance", 1, 1),
    ):
        canceled = renderer.render_html(
            "<p>weather</p>",
            viewport=(80, 48),
            timeout_seconds=0.01,
            failure_domain="weather:weather.html",
            retry_once=True,
        )

    assert canceled is None
    assert len(launches) == 1
    assert renderer.negative_cache_size == 0
    assert renderer.html_circuit_size == 0
    assert current_task_context() is None
    assert not any(
        record.getMessage().startswith("Retrying Chromium")
        for record in caplog.records
    )

    recovered = renderer.render_html(
        "<p>weather</p>",
        viewport=(80, 48),
        context=_context(),
        timeout_seconds=0.01,
        failure_domain="weather:weather.html",
    )

    assert recovered is not None
    assert recovered.size == (80, 48)
    assert len(launches) == 2


def test_html_single_attempt_deadline_timeout_does_not_poison_cache(
    tmp_path,
    monkeypatch,
):
    now = {"value": 0.0}

    class DeadlineTimeoutProcess(TimeoutProcess):
        def wait(self, timeout=None):
            now["value"] = 1.0
            return super().wait(timeout)

    process = DeadlineTimeoutProcess()
    renderer = BrowserRenderer(
        binary="chromium",
        temp_root=tmp_path,
        popen=lambda *_args, **_kwargs: process,
    )
    _route_fake_process_group_signals(monkeypatch, lambda: (process,))
    context = TaskContext.never_cancelled(
        deadline_monotonic=0.5,
        clock=lambda: now["value"],
    )

    result = renderer.render_html(
        "<p>deadline</p>",
        viewport=(80, 48),
        context=context,
        timeout_seconds=0.01,
        failure_domain="weather:weather.html",
    )

    assert result is None
    assert renderer.negative_cache_size == 0
    assert renderer.html_circuit_size == 0


def test_html_second_attempt_cancellation_does_not_poison_cache(
    tmp_path,
    monkeypatch,
):
    cancel_event = threading.Event()
    processes = []

    class CancelingTimeoutProcess(TimeoutProcess):
        def wait(self, timeout=None):
            cancel_event.set()
            return super().wait(timeout)

    def popen(*_args, **_kwargs):
        process = (
            TimeoutProcess()
            if not processes
            else CancelingTimeoutProcess()
        )
        processes.append(process)
        return process

    renderer = BrowserRenderer(
        binary="chromium",
        temp_root=tmp_path,
        popen=popen,
        resource_sampler=lambda: ResourceSample(
            available_mb=512,
            swap_percent=0,
        ),
    )
    _route_fake_process_group_signals(monkeypatch, lambda: processes)
    context = TaskContext(
        cancel_event,
        time.monotonic() + 60,
    )

    result = renderer.render_html(
        "<p>cancel on retry</p>",
        viewport=(80, 48),
        context=context,
        timeout_seconds=0.01,
        failure_domain="weather:weather.html",
        retry_once=True,
    )

    assert result is None
    assert len(processes) == 2
    assert renderer.negative_cache_size == 0
    assert renderer.html_circuit_size == 0


def test_html_render_can_retry_once_after_a_transient_chromium_timeout(
    tmp_path,
    monkeypatch,
):
    first = TimeoutProcess()
    launches = []

    class SuccessProcess:
        returncode = 0
        pid = 5678

        def __init__(self, command):
            output = next(
                item.split("=", 1)[1]
                for item in command
                if item.startswith("--screenshot=")
            )
            Image.new("RGB", (80, 48), "white").save(output)

        def wait(self, timeout=None):
            return 0

        def poll(self):
            return self.returncode

        def terminate(self):
            self.returncode = -15

        def kill(self):
            self.returncode = -9

    def popen(command, **_kwargs):
        launches.append(command)
        return first if len(launches) == 1 else SuccessProcess(command)

    renderer = BrowserRenderer(
        binary="chromium",
        temp_root=tmp_path,
        popen=popen,
        resource_sampler=lambda: ResourceSample(
            available_mb=512,
            swap_percent=0,
        ),
    )
    _route_fake_process_group_signals(monkeypatch, lambda: (first,))

    result = renderer.render_html(
        "<p>weather</p>",
        viewport=(80, 48),
        timeout_seconds=0.01,
        failure_domain="weather:weather.html",
        retry_once=True,
    )

    assert result is not None
    assert result.size == (80, 48)
    assert len(launches) == 2
    assert renderer.active_processes == ()
    assert renderer.negative_cache_size == 0
    assert renderer.html_circuit_size == 0
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("available_mb", [100, 60])
def test_html_retry_once_skips_second_launch_under_resource_pressure(
    tmp_path,
    caplog,
    available_mb,
    monkeypatch,
):
    first = TimeoutProcess()
    launches = []

    class FailedProcess:
        returncode = 1
        pid = 9876

        def wait(self, timeout=None):
            return self.returncode

        def poll(self):
            return self.returncode

    def popen(command, **_kwargs):
        launches.append(command)
        return first if len(launches) == 1 else FailedProcess()

    renderer = BrowserRenderer(
        binary="chromium",
        temp_root=tmp_path,
        popen=popen,
        resource_sampler=lambda: ResourceSample(
            available_mb=available_mb,
            swap_percent=0,
        ),
    )
    _route_fake_process_group_signals(monkeypatch, lambda: (first,))

    caplog.set_level("WARNING", logger="src.utils.browser_renderer")
    result = renderer.render_html(
        "<p>weather</p>",
        viewport=(80, 48),
        context=_context(),
        timeout_seconds=0.01,
        failure_domain="weather:weather.html",
        retry_once=True,
    )

    assert result is None
    assert len(launches) == 1
    assert renderer.active_processes == ()
    assert renderer.negative_cache_size == 1
    assert renderer.html_circuit_size == 1
    assert list(tmp_path.iterdir()) == []
    assert not any(
        record.getMessage().startswith("Retrying Chromium")
        for record in caplog.records
    )


@pytest.mark.parametrize("available_mb", [100, 60])
@pytest.mark.parametrize("failure_mode", ["timeout", "exit"])
def test_html_first_failure_defers_retry_when_pressure_is_soft_or_hard(
    tmp_path,
    available_mb,
    failure_mode,
    monkeypatch,
):
    launches = []

    class FailedProcess:
        returncode = 1
        pid = 9877

        def wait(self, timeout=None):
            return self.returncode

        def poll(self):
            return self.returncode

    process = TimeoutProcess() if failure_mode == "timeout" else FailedProcess()
    pressure_sample_after = 3 if failure_mode == "timeout" else 2
    sample_calls = 0

    def sample_resources():
        nonlocal sample_calls
        sample_calls += 1
        if sample_calls < pressure_sample_after:
            return ResourceSample(available_mb=512, swap_percent=0)
        return ResourceSample(available_mb=available_mb, swap_percent=0)

    renderer = BrowserRenderer(
        binary="chromium",
        temp_root=tmp_path,
        popen=lambda *_args, **_kwargs: launches.append(process) or process,
        resource_sampler=sample_resources,
    )
    _route_fake_process_group_signals(monkeypatch, lambda: launches)

    with pytest.raises(ResourcePressureDeferred) as deferred:
        renderer.render_html(
            "<p>weather</p>",
            viewport=(80, 48),
            context=_context(),
            timeout_seconds=0.01,
            failure_domain="weather:weather.html",
            retry_once=True,
            abort_on_hard_pressure=True,
        )

    assert deferred.value.reason == "browser_resource_pressure"
    assert deferred.value.phase == "retry"
    assert deferred.value.available_mb == available_mb
    assert deferred.value.swap_percent == 0
    assert len(launches) == 1
    assert renderer.active_processes == ()
    assert renderer.negative_cache_size == 0
    assert renderer.html_circuit_size == 0
    assert list(tmp_path.iterdir()) == []


def test_html_render_aborts_running_chromium_when_pressure_becomes_hard(
    tmp_path,
    caplog,
    monkeypatch,
):
    class RunningProcess:
        returncode = None
        pid = 2468

        def __init__(self):
            self.terminated = False
            self.killed = False
            self.wait_calls = 0

        def wait(self, timeout=None):
            self.wait_calls += 1
            if self.returncode is None:
                raise subprocess.TimeoutExpired("chromium", timeout)
            return self.returncode

        def poll(self):
            return self.returncode

        def terminate(self):
            self.terminated = True
            self.returncode = -15

        def kill(self):
            self.killed = True
            self.returncode = -9

    process = RunningProcess()
    samples = iter(
        (
            ResourceSample(available_mb=512, swap_percent=0),
            ResourceSample(available_mb=60, swap_percent=0),
        )
    )
    renderer = BrowserRenderer(
        binary="chromium",
        temp_root=tmp_path,
        popen=lambda *_args, **_kwargs: process,
        resource_sampler=lambda: next(samples),
    )
    _route_fake_process_group_signals(monkeypatch, lambda: (process,))

    with caplog.at_level("WARNING", logger="src.utils.browser_renderer"):
        with pytest.raises(ResourcePressureDeferred) as deferred:
            renderer.render_html(
                "<p>weather</p>",
                viewport=(80, 48),
                context=_context(),
                timeout_seconds=5,
                failure_domain="weather:weather.html",
                abort_on_hard_pressure=True,
            )

    assert deferred.value.reason == "browser_resource_pressure"
    assert deferred.value.phase == "in_flight"
    assert deferred.value.available_mb == 60
    assert deferred.value.swap_percent == 0
    assert process.terminated
    assert not process.killed
    assert renderer.active_processes == ()
    assert renderer.negative_cache_size == 0
    assert renderer.html_circuit_size == 0
    assert list(tmp_path.iterdir()) == []
    assert any(
        record.getMessage().startswith(
            "Aborting Chromium render due to hard resource pressure"
        )
        for record in caplog.records
    )


def test_html_pressure_abort_does_not_retry_same_call_or_poison_next_request(
    tmp_path,
    monkeypatch,
):
    launches = []
    sample_calls = 0

    class Process:
        returncode = None
        pid = 2470

        def __init__(self, command, *, succeeds):
            self.terminated = False
            if succeeds:
                output = next(
                    item.split("=", 1)[1]
                    for item in command
                    if item.startswith("--screenshot=")
                )
                Image.new("RGB", (80, 48), "white").save(output)
                self.returncode = 0

        def wait(self, timeout=None):
            if self.returncode is None:
                raise subprocess.TimeoutExpired("chromium", timeout)
            return self.returncode

        def poll(self):
            return self.returncode

        def terminate(self):
            self.terminated = True
            self.returncode = -15

        def kill(self):
            self.returncode = -9

    def popen(command, **_kwargs):
        process = Process(command, succeeds=bool(launches))
        launches.append(process)
        return process

    def sample_resources():
        nonlocal sample_calls
        sample_calls += 1
        if sample_calls == 1:
            return ResourceSample(available_mb=512, swap_percent=0)
        if sample_calls == 2:
            return ResourceSample(available_mb=100, swap_percent=80)
        return ResourceSample(available_mb=512, swap_percent=0)

    renderer = BrowserRenderer(
        binary="chromium",
        temp_root=tmp_path,
        popen=popen,
        resource_sampler=sample_resources,
    )
    _route_fake_process_group_signals(monkeypatch, lambda: launches)

    with pytest.raises(ResourcePressureDeferred) as deferred:
        renderer.render_html(
            "<p>weather</p>",
            viewport=(80, 48),
            context=_context(),
            timeout_seconds=5,
            failure_domain="weather:weather.html",
            retry_once=True,
            abort_on_hard_pressure=True,
        )

    second_result = renderer.render_html(
        "<p>weather</p>",
        viewport=(80, 48),
        context=_context(),
        timeout_seconds=5,
        failure_domain="weather:weather.html",
        abort_on_hard_pressure=True,
    )

    assert deferred.value.phase == "in_flight"
    assert second_result is not None
    assert second_result.size == (80, 48)
    assert len(launches) == 2
    assert launches[0].terminated
    assert not launches[1].terminated
    assert renderer.negative_cache_size == 0
    assert renderer.html_circuit_size == 0


def test_delayed_pressure_kill_does_not_poison_next_request(
    tmp_path,
    monkeypatch,
):
    launches = []
    sample_calls = 0

    class LateKilledProcess:
        returncode = None
        pid = 2475

        def __init__(self):
            self.wait_calls = 0
            self.terminated = False
            self.killed = False

        def wait(self, timeout=None):
            self.wait_calls += 1
            if self.wait_calls <= 3:
                raise subprocess.TimeoutExpired("chromium", timeout)
            self.returncode = -9
            return self.returncode

        def poll(self):
            return self.returncode

        def terminate(self):
            self.terminated = True

        def kill(self):
            self.killed = True

    class SuccessProcess:
        returncode = 0
        pid = 2476

        def __init__(self, command):
            output = next(
                item.split("=", 1)[1]
                for item in command
                if item.startswith("--screenshot=")
            )
            Image.new("RGB", (80, 48), "white").save(output)

        def wait(self, timeout=None):
            return self.returncode

        def poll(self):
            return self.returncode

    def popen(command, **_kwargs):
        process = (
            LateKilledProcess()
            if not launches
            else SuccessProcess(command)
        )
        launches.append(process)
        return process

    def sample_resources():
        nonlocal sample_calls
        sample_calls += 1
        if sample_calls == 1:
            return ResourceSample(available_mb=512, swap_percent=0)
        if sample_calls == 2:
            return ResourceSample(available_mb=60, swap_percent=80)
        return ResourceSample(available_mb=512, swap_percent=0)

    renderer = BrowserRenderer(
        binary="chromium",
        temp_root=tmp_path,
        popen=popen,
        resource_sampler=sample_resources,
    )
    _route_fake_process_group_signals(monkeypatch, lambda: launches)

    with pytest.raises(ResourcePressureDeferred) as deferred:
        renderer.render_html(
            "<p>weather</p>",
            viewport=(80, 48),
            context=_context(seconds=10),
            timeout_seconds=10,
            failure_domain="weather:weather.html",
            abort_on_hard_pressure=True,
        )
    second_result = renderer.render_html(
        "<p>weather</p>",
        viewport=(80, 48),
        context=_context(),
        timeout_seconds=5,
        failure_domain="weather:weather.html",
        abort_on_hard_pressure=True,
    )

    assert deferred.value.phase == "in_flight"
    assert launches[0].terminated
    assert launches[0].killed
    assert launches[0].wait_calls == 4
    assert second_result is not None
    assert second_result.size == (80, 48)
    assert len(launches) == 2
    assert renderer.negative_cache_size == 0
    assert renderer.html_circuit_size == 0


def test_pressure_stop_pending_at_timeout_quarantines_browser_capacity(
    tmp_path,
    monkeypatch,
):
    class NeverExitProcess:
        returncode = None
        pid = 2477

        def __init__(self):
            self.wait_calls = 0
            self.terminated = False
            self.killed = False

        def wait(self, timeout=None):
            self.wait_calls += 1
            raise subprocess.TimeoutExpired("chromium", timeout)

        def poll(self):
            return self.returncode

        def terminate(self):
            self.terminated = True

        def kill(self):
            self.killed = True

    process = NeverExitProcess()
    slot = CleanupSlot()
    governor = TrackingBrowserGovernor()
    samples = iter(
        (
            ResourceSample(available_mb=512, swap_percent=0),
            ResourceSample(available_mb=60, swap_percent=80),
        )
    )
    renderer = BrowserRenderer(
        binary="chromium",
        temp_root=tmp_path,
        popen=lambda *_args, **_kwargs: process,
        resource_sampler=lambda: next(samples),
        resource_governor=governor,
    )
    monkeypatch.setattr(browser_renderer_module, "_GLOBAL_BROWSER_SLOT", slot)
    _route_fake_process_group_signals(monkeypatch, lambda: (process,))

    with pytest.raises(browser_renderer_module.BrowserProcessLeakError):
        renderer.render_html(
            "<p>weather</p>",
            viewport=(80, 48),
            context=_context(seconds=10),
            timeout_seconds=0.01,
            failure_domain="weather:weather.html",
            abort_on_hard_pressure=True,
        )

    assert process.terminated
    assert process.killed
    assert renderer.active_processes == (2477,)
    assert renderer.quarantined_processes == (2477,)
    assert slot.release_calls == 0
    assert [lease.release_calls for lease in governor.leases] == [0]
    assert renderer.negative_cache_size == 0
    assert renderer.html_circuit_size == 0


def test_html_render_keeps_screenshot_completed_during_pressure_sample(tmp_path):
    process = None

    class CompletingProcess:
        returncode = None
        pid = 2471

        def __init__(self, command):
            self.output = next(
                item.split("=", 1)[1]
                for item in command
                if item.startswith("--screenshot=")
            )
            self.terminated = False

        def wait(self, timeout=None):
            if self.returncode is None:
                raise subprocess.TimeoutExpired("chromium", timeout)
            return self.returncode

        def poll(self):
            return self.returncode

        def terminate(self):
            self.terminated = True
            self.returncode = -15

        def kill(self):
            self.returncode = -9

    def popen(command, **_kwargs):
        nonlocal process
        process = CompletingProcess(command)
        return process

    def complete_during_sample():
        Image.new("RGB", (80, 48), "white").save(process.output)
        process.returncode = 0
        return ResourceSample(available_mb=60, swap_percent=80)

    renderer = BrowserRenderer(
        binary="chromium",
        temp_root=tmp_path,
        popen=popen,
        resource_sampler=complete_during_sample,
    )

    result = renderer.render_html(
        "<p>weather</p>",
        viewport=(80, 48),
        context=_context(),
        timeout_seconds=5,
        failure_domain="weather:weather.html",
        abort_on_hard_pressure=True,
    )

    assert result is not None
    assert result.size == (80, 48)
    assert not process.terminated
    assert renderer.negative_cache_size == 0
    assert renderer.html_circuit_size == 0


def test_pressure_stop_waits_for_late_process_exit_without_caching_failure(
    tmp_path,
    monkeypatch,
):
    class LateExitProcess:
        returncode = None
        pid = 2474

        def __init__(self, command):
            self.output = next(
                item.split("=", 1)[1]
                for item in command
                if item.startswith("--screenshot=")
            )
            self.wait_calls = 0
            self.terminated = False
            self.killed = False

        def wait(self, timeout=None):
            self.wait_calls += 1
            if self.wait_calls <= 3:
                raise subprocess.TimeoutExpired("chromium", timeout)
            Image.new("RGB", (80, 48), "white").save(self.output)
            self.returncode = 0
            return self.returncode

        def poll(self):
            return self.returncode

        def terminate(self):
            self.terminated = True

        def kill(self):
            self.killed = True

    process = None

    def popen(command, **_kwargs):
        nonlocal process
        process = LateExitProcess(command)
        return process

    samples = iter(
        (
            ResourceSample(available_mb=512, swap_percent=0),
            ResourceSample(available_mb=60, swap_percent=80),
        )
    )
    renderer = BrowserRenderer(
        binary="chromium",
        temp_root=tmp_path,
        popen=popen,
        resource_sampler=lambda: next(samples),
    )
    _route_fake_process_group_signals(monkeypatch, lambda: (process,))

    with pytest.raises(ResourcePressureDeferred) as deferred:
        renderer.render_html(
            "<p>weather</p>",
            viewport=(80, 48),
            context=_context(seconds=10),
            timeout_seconds=10,
            failure_domain="weather:weather.html",
            abort_on_hard_pressure=True,
        )

    assert deferred.value.phase == "in_flight"
    assert process.terminated
    assert process.killed
    assert process.wait_calls == 4
    assert renderer.active_processes == ()
    assert renderer.negative_cache_size == 0
    assert renderer.html_circuit_size == 0
    assert list(tmp_path.iterdir()) == []


def test_html_pressure_polling_uses_bounded_budget_with_static_clock(
    tmp_path,
    monkeypatch,
):
    class TimeoutProcess:
        returncode = None
        pid = 2472

        def __init__(self):
            self.wait_calls = 0
            self.terminated = False

        def wait(self, timeout=None):
            self.wait_calls += 1
            if self.returncode is not None:
                return self.returncode
            if self.wait_calls >= 50:
                self.returncode = 1
                return self.returncode
            raise subprocess.TimeoutExpired("chromium", timeout)

        def poll(self):
            return self.returncode

        def terminate(self):
            self.terminated = True
            self.returncode = -15

        def kill(self):
            self.returncode = -9

    process = TimeoutProcess()
    renderer = BrowserRenderer(
        binary="chromium",
        temp_root=tmp_path,
        popen=lambda *_args, **_kwargs: process,
        clock=lambda: 1.0,
        resource_sampler=lambda: ResourceSample(
            available_mb=100,
            swap_percent=0,
        ),
    )
    _route_fake_process_group_signals(monkeypatch, lambda: (process,))

    result = renderer.render_html(
        "<p>weather</p>",
        viewport=(80, 48),
        timeout_seconds=0.01,
        failure_domain="weather:weather.html",
        abort_on_hard_pressure=True,
    )

    assert result is None
    assert process.terminated
    assert process.wait_calls < 10


def test_html_render_does_not_abort_running_chromium_under_soft_pressure(tmp_path):
    class RecoveringProcess:
        returncode = None
        pid = 2469

        def __init__(self, command):
            self.output = next(
                item.split("=", 1)[1]
                for item in command
                if item.startswith("--screenshot=")
            )
            self.wait_calls = 0
            self.terminated = False

        def wait(self, timeout=None):
            self.wait_calls += 1
            if self.wait_calls == 1:
                raise subprocess.TimeoutExpired("chromium", timeout)
            Image.new("RGB", (80, 48), "white").save(self.output)
            self.returncode = 0
            return self.returncode

        def poll(self):
            return self.returncode

        def terminate(self):
            self.terminated = True
            self.returncode = -15

        def kill(self):
            self.returncode = -9

    processes = []

    def popen(command, **_kwargs):
        process = RecoveringProcess(command)
        processes.append(process)
        return process

    renderer = BrowserRenderer(
        binary="chromium",
        temp_root=tmp_path,
        popen=popen,
        resource_sampler=lambda: ResourceSample(
            available_mb=100,
            swap_percent=0,
        ),
    )

    result = renderer.render_html(
        "<p>weather</p>",
        viewport=(80, 48),
        context=_context(),
        timeout_seconds=5,
        failure_domain="weather:weather.html",
        abort_on_hard_pressure=True,
    )

    assert result is not None
    assert result.size == (80, 48)
    assert len(processes) == 1
    assert not processes[0].terminated
    assert renderer.negative_cache_size == 0
    assert renderer.html_circuit_size == 0
    assert list(tmp_path.iterdir()) == []


def test_html_render_continues_with_high_swap_when_memory_headroom_remains(
    tmp_path,
):
    class CompletingProcess:
        returncode = None
        pid = 2473

        def __init__(self, command):
            self.output = next(
                item.split("=", 1)[1]
                for item in command
                if item.startswith("--screenshot=")
            )
            self.wait_calls = 0
            self.terminated = False

        def wait(self, timeout=None):
            self.wait_calls += 1
            if self.wait_calls == 1:
                raise subprocess.TimeoutExpired("chromium", timeout)
            Image.new("RGB", (80, 48), "white").save(self.output)
            self.returncode = 0
            return self.returncode

        def poll(self):
            return self.returncode

        def terminate(self):
            self.terminated = True
            self.returncode = -15

        def kill(self):
            self.returncode = -9

    process = None

    def popen(command, **_kwargs):
        nonlocal process
        process = CompletingProcess(command)
        return process

    renderer = BrowserRenderer(
        binary="chromium",
        temp_root=tmp_path,
        popen=popen,
        resource_sampler=lambda: ResourceSample(
            available_mb=146,
            swap_percent=75.3,
        ),
    )

    result = renderer.render_html(
        "<p>weather</p>",
        viewport=(80, 48),
        context=_context(),
        timeout_seconds=5,
        failure_domain="weather:weather.html",
        abort_on_hard_pressure=True,
    )

    assert result is not None
    assert result.size == (80, 48)
    assert not process.terminated
    assert renderer.negative_cache_size == 0
    assert renderer.html_circuit_size == 0
    assert list(tmp_path.iterdir()) == []


def test_html_start_admission_allows_high_swap_headroom_and_defers_hard_low_memory(
    tmp_path,
):
    launches = []

    class SuccessProcess:
        returncode = 0
        pid = 2478

        def __init__(self, command):
            launches.append(command)
            output = next(
                item.split("=", 1)[1]
                for item in command
                if item.startswith("--screenshot=")
            )
            Image.new("RGB", (80, 48), "white").save(output)

        def wait(self, timeout=None):
            return self.returncode

        def poll(self):
            return self.returncode

    high_root = tmp_path / "high"
    high_renderer = BrowserRenderer(
        binary="chromium",
        temp_root=high_root,
        popen=lambda command, **_kwargs: SuccessProcess(command),
        resource_sampler=lambda: ResourceSample(
            available_mb=150,
            swap_percent=90,
        ),
    )

    image = high_renderer.render_html(
        "<p>weather</p>",
        viewport=(80, 48),
        context=_context(),
        timeout_seconds=5,
        failure_domain="weather:weather.html",
        abort_on_hard_pressure=True,
    )

    assert image is not None
    assert image.size == (80, 48)
    assert len(launches) == 1
    assert high_renderer.negative_cache_size == 0
    assert high_renderer.html_circuit_size == 0
    assert list(high_root.iterdir()) == []

    low_launches = []
    low_root = tmp_path / "low"
    low_renderer = BrowserRenderer(
        binary="chromium",
        temp_root=low_root,
        popen=lambda *_args, **_kwargs: low_launches.append(True),
        resource_sampler=lambda: ResourceSample(
            available_mb=69,
            swap_percent=0,
        ),
    )

    with pytest.raises(ResourcePressureDeferred) as deferred:
        low_renderer.render_html(
            "<p>weather</p>",
            viewport=(80, 48),
            context=_context(),
            timeout_seconds=5,
            failure_domain="weather:weather.html",
            abort_on_hard_pressure=True,
        )

    assert deferred.value.reason == "browser_resource_pressure"
    assert deferred.value.phase == "start"
    assert deferred.value.available_mb == 69
    assert deferred.value.swap_percent == 0
    assert low_launches == []
    assert low_renderer.active_processes == ()
    assert low_renderer.negative_cache_size == 0
    assert low_renderer.html_circuit_size == 0
    assert list(low_root.iterdir()) == []


def test_each_render_uses_clean_profile_without_disabling_sandbox(tmp_path):
    commands = []

    class SuccessProcess:
        returncode = 0
        pid = 2222

        def __init__(self, command):
            commands.append(command)
            output = next(
                item.split("=", 1)[1]
                for item in command
                if item.startswith("--screenshot=")
            )
            Image.new("RGB", (8, 8), "white").save(output)

        def wait(self, timeout=None):
            return 0

        def poll(self):
            return self.returncode

        def terminate(self):
            self.returncode = -15

        def kill(self):
            self.returncode = -9

    renderer = BrowserRenderer(
        binary="chromium",
        temp_root=tmp_path,
        popen=lambda command, **_kwargs: SuccessProcess(command),
        run_as_root=False,
    )

    first = renderer.render_html("<p>one</p>", viewport=(800, 480), context=_context())
    second = renderer.render_html("<p>two</p>", viewport=(800, 480), context=_context())

    assert first.size == (8, 8)
    assert second.size == (8, 8)
    profiles = [
        next(arg for arg in command if arg.startswith("--user-data-dir="))
        for command in commands
    ]
    assert profiles[0] != profiles[1]
    assert all("--no-sandbox" not in command for command in commands)
    assert all("--no-zygote" not in command for command in commands)
    assert all("--disk-cache-size=1" in command for command in commands)
    assert all("--in-process-gpu" in command for command in commands)
    assert all("--use-gl=swiftshader" in command for command in commands)
    assert all("--js-flags=--jitless" in command for command in commands)
    assert all("--disable-zero-copy" in command for command in commands)
    assert all("--virtual-time-budget=2000" in command for command in commands)
    assert all("--virtual-time-budget=60000" not in command for command in commands)
    assert all(
        "--disable-gpu-memory-buffer-compositor-resources" in command
        for command in commands
    )
    assert all(
        any(argument.startswith("--proxy-server=http://127.0.0.1:") for argument in command)
        for command in commands
    )
    assert all("--proxy-bypass-list=<-loopback>" in command for command in commands)
    assert list(tmp_path.iterdir()) == []


def test_root_renderer_adds_required_no_sandbox_without_disabling_zygote(tmp_path):
    commands = []

    class FailedProcess:
        returncode = 1
        pid = 2444

        def __init__(self, command):
            commands.append(command)

        def wait(self, timeout=None):
            return self.returncode

        def poll(self):
            return self.returncode

        def terminate(self):
            self.returncode = -15

        def kill(self):
            self.returncode = -9

    renderer = BrowserRenderer(
        binary="chromium",
        temp_root=tmp_path,
        popen=lambda command, **_kwargs: FailedProcess(command),
        run_as_root=True,
    )

    assert renderer.render_html("<p>root</p>", viewport=(80, 48), context=_context()) is None
    assert "--no-sandbox" in commands[0]
    assert "--no-zygote" not in commands[0]


def test_html_timeout_circuit_isolated_by_failure_domain_until_cooldown(
    tmp_path,
    monkeypatch,
):
    now = {"value": 0.0}
    launches = []

    def popen(*_args, **_kwargs):
        process = TimeoutProcess()
        process.pid += len(launches)
        launches.append(process)
        return process

    renderer = BrowserRenderer(
        binary="chromium",
        temp_root=tmp_path,
        popen=popen,
        clock=lambda: now["value"],
        html_circuit_ttl_seconds=60,
    )
    _route_fake_process_group_signals(monkeypatch, lambda: launches)

    assert renderer.render_html(
        "<p>first timestamp</p>",
        viewport=(80, 48),
        context=_context(),
        timeout_seconds=0.01,
        failure_domain="weather:weather.html",
    ) is None
    assert renderer.render_html(
        "<p>different timestamp</p>",
        viewport=(80, 48),
        context=_context(),
        timeout_seconds=0.01,
        failure_domain="weather:weather.html",
    ) is None
    assert len(launches) == 1

    assert renderer.render_html(
        "<p>first timestamp</p>",
        viewport=(80, 48),
        context=_context(),
        timeout_seconds=0.01,
        failure_domain="steam_charts:steam_charts.html",
    ) is None
    assert len(launches) == 2

    assert renderer.render_html(
        "<p>system failure circuit</p>",
        viewport=(80, 48),
        context=_context(),
        timeout_seconds=0.01,
        failure_domain="daily_ai_news:brief.html",
    ) is None
    assert len(launches) == 2

    now["value"] = 61.0
    assert renderer.render_html(
        "<p>after cooldown</p>",
        viewport=(80, 48),
        context=_context(),
        timeout_seconds=0.01,
        failure_domain="weather:weather.html",
    ) is None
    assert len(launches) == 3


def test_egress_proxy_start_failures_count_toward_html_system_circuit(tmp_path):
    class UnavailableProxy:
        def __init__(self):
            self.start_calls = 0

        def start(self):
            self.start_calls += 1
            return False

        def close(self):
            pass

    proxy = UnavailableProxy()
    renderer = BrowserRenderer(
        binary="chromium",
        temp_root=tmp_path,
        popen=lambda *_args, **_kwargs: pytest.fail(
            "proxy failure started Chromium"
        ),
        egress_proxy=proxy,
        html_circuit_ttl_seconds=60,
    )

    for domain in (
        "weather:weather.html",
        "steam_charts:steam_charts.html",
        "daily_ai_news:brief.html",
    ):
        assert renderer.render_html(
            f"<p>{domain}</p>",
            viewport=(80, 48),
            context=_context(),
            failure_domain=domain,
        ) is None

    assert proxy.start_calls == 2


def test_html_failure_domain_table_is_bounded_and_prunes_expired_entries(tmp_path):
    now = {"value": 0.0}
    renderer = BrowserRenderer(
        binary="chromium",
        temp_root=tmp_path,
        clock=lambda: now["value"],
        html_circuit_ttl_seconds=60,
    )

    for index in range(browser_renderer_module.MAX_HTML_CIRCUIT_DOMAINS + 10):
        renderer._remember_html_failure(f"plugin-{index}:template.html")

    assert renderer.html_circuit_size == browser_renderer_module.MAX_HTML_CIRCUIT_DOMAINS

    now["value"] = 61.0
    assert renderer.html_circuit_size == 0


def test_two_renderer_instances_never_overlap(tmp_path):
    state_lock = threading.Lock()
    active = 0
    maximum = 0
    next_pid = 3000

    class SlowProcess:
        returncode = 0

        def __init__(self, command):
            nonlocal active, maximum, next_pid
            with state_lock:
                next_pid += 1
                self.pid = next_pid
                active += 1
                maximum = max(maximum, active)
            output = next(
                item.split("=", 1)[1]
                for item in command
                if item.startswith("--screenshot=")
            )
            Image.new("RGB", (4, 4), "white").save(output)

        def wait(self, timeout=None):
            nonlocal active
            time.sleep(0.05)
            with state_lock:
                active -= 1
            return 0

        def poll(self):
            return self.returncode

        def terminate(self):
            pass

        def kill(self):
            pass

    popen = lambda command, **_kwargs: SlowProcess(command)
    renderers = [
        BrowserRenderer(binary="chromium", temp_root=tmp_path, popen=popen),
        BrowserRenderer(binary="chromium", temp_root=tmp_path, popen=popen),
    ]
    results = []

    threads = [
        threading.Thread(
            target=lambda renderer=renderer: results.append(
                renderer.render_html("<p>x</p>", viewport=(80, 48), context=_context())
            )
        )
        for renderer in renderers
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=2)

    assert len(results) == 2
    assert maximum == 1


def test_chromium_admission_honors_central_permit_and_deadline(tmp_path):
    launches = []

    class SuccessProcess:
        returncode = 0
        pid = 3999

        def __init__(self, command):
            output = next(
                item.split("=", 1)[1]
                for item in command
                if item.startswith("--screenshot=")
            )
            Image.new("RGB", (80, 48), "white").save(output)

        def wait(self, timeout=None):
            return self.returncode

        def poll(self):
            return self.returncode

    governor = RuntimeResourceGovernor()
    renderer = BrowserRenderer(
        binary="chromium",
        temp_root=tmp_path,
        popen=lambda command, **_kwargs: launches.append(command)
        or SuccessProcess(command),
        resource_governor=RuntimeResourceGovernor(),
    )
    held = governor.acquire(CHROMIUM, {}, _context())
    try:
        assert renderer.render_html(
            "<p>blocked</p>",
            viewport=(80, 48),
            context=_context(0.1),
        ) is None
        assert launches == []
    finally:
        held.release()

    image = renderer.render_html(
        "<p>released</p>",
        viewport=(80, 48),
        context=_context(),
    )
    assert image.size == (80, 48)
    assert len(launches) == 1


def test_remote_url_requires_validator_and_negative_cache_is_bounded(tmp_path):
    calls = []
    renderer = BrowserRenderer(
        binary="chromium",
        temp_root=tmp_path,
        popen=lambda *_args, **_kwargs: calls.append(True),
    )

    assert renderer.render_url(
        "https://example.test/page?secret=value",
        viewport=(800, 480),
        context=_context(),
        validator=None,
    ) is None
    assert renderer.render_url(
        "https://example.test/page?secret=value",
        viewport=(800, 480),
        context=_context(),
        validator=lambda _url: False,
    ) is None

    assert calls == []
    assert renderer.negative_cache_size <= 1


def test_url_deadline_timeout_does_not_poison_negative_cache(
    tmp_path,
    caplog,
    monkeypatch,
):
    now = {"value": 0.0}
    launches = []
    processes = []

    class DeadlineTimeoutProcess(TimeoutProcess):
        def wait(self, timeout=None):
            now["value"] = 1.0
            return super().wait(timeout)

    class SuccessProcess:
        returncode = 0
        pid = 8765

        def __init__(self, command):
            output = next(
                item.split("=", 1)[1]
                for item in command
                if item.startswith("--screenshot=")
            )
            Image.new("RGB", (80, 48), "white").save(output)

        def wait(self, timeout=None):
            return self.returncode

        def poll(self):
            return self.returncode

    class AllowPolicy:
        @staticmethod
        def resolve_and_validate(url):
            return SimpleNamespace(normalized_url=url)

    class AvailableProxy:
        proxy_url = "http://127.0.0.1:12345"

        @staticmethod
        def start():
            return True

        @staticmethod
        def close():
            pass

    def popen(command, **_kwargs):
        launches.append(command)
        process = (
            DeadlineTimeoutProcess()
            if len(launches) == 1
            else SuccessProcess(command)
        )
        processes.append(process)
        return process

    renderer = BrowserRenderer(
        binary="chromium",
        temp_root=tmp_path,
        popen=popen,
        ssrf_policy=AllowPolicy(),
        egress_proxy=AvailableProxy(),
    )
    _route_fake_process_group_signals(monkeypatch, lambda: processes)
    deadline_context = TaskContext.never_cancelled(
        deadline_monotonic=0.5,
        clock=lambda: now["value"],
    )

    with caplog.at_level("WARNING", logger="src.utils.browser_renderer"):
        timed_out = renderer.render_url(
            "https://example.test/page",
            viewport=(80, 48),
            context=deadline_context,
            validator=lambda url: url,
            timeout_seconds=0.01,
        )
    recovered = renderer.render_url(
        "https://example.test/page",
        viewport=(80, 48),
        context=_context(),
        validator=lambda url: url,
        timeout_seconds=0.01,
    )

    assert timed_out is None
    assert recovered is not None
    assert recovered.size == (80, 48)
    assert len(launches) == 2
    assert renderer.negative_cache_size == 0
    assert not any(
        record.getMessage().startswith("Chromium render timed out")
        for record in caplog.records
    )


def test_repeated_failures_leave_no_processes_or_temp_growth(tmp_path):
    next_pid = 5000

    class FailedProcess:
        returncode = 1

        def __init__(self):
            nonlocal next_pid
            next_pid += 1
            self.pid = next_pid

        def wait(self, timeout=None):
            return 1

        def poll(self):
            return self.returncode

        def terminate(self):
            pass

        def kill(self):
            pass

    renderer = BrowserRenderer(
        binary="chromium",
        temp_root=tmp_path,
        popen=lambda *_args, **_kwargs: FailedProcess(),
    )

    for index in range(100):
        assert renderer.render_html(
            f"<p>{index}</p>",
            viewport=(80, 48),
            context=_context(),
        ) is None

    assert renderer.active_processes == ()
    assert next_pid == 5001
    assert renderer.negative_cache_size == 1
    assert list(tmp_path.iterdir()) == []


def test_abandoned_browser_job_cleanup_uses_global_slot_and_two_hour_grace(
    tmp_path,
    monkeypatch,
):
    now = 20_000.0
    stale = 2 * 60 * 60
    old_job = _abandoned_job(tmp_path, "render-old", now=now, age=stale + 1)
    recent_job = _abandoned_job(tmp_path, "render-recent", now=now, age=stale)
    slot = CleanupSlot()
    monkeypatch.setattr(browser_renderer_module, "_GLOBAL_BROWSER_SLOT", slot)
    renderer = BrowserRenderer(
        binary="chromium",
        temp_root=tmp_path,
        popen=lambda *_args, **_kwargs: pytest.fail("cleanup started Chromium"),
        clock=lambda: 0.0,
    )

    aggregate = renderer.cleanup_abandoned_jobs(
        now_epoch=now,
        stale_seconds=stale,
        allowance=_cleanup_allowance(),
        dry_run=False,
    )

    assert not old_job.exists()
    assert recent_job.is_dir()
    assert aggregate.deleted_entries == 1
    assert aggregate.deleted_bytes == len(b"residue")
    assert slot.acquire_calls == [(False, None)]
    assert slot.release_calls == 1


def test_active_browser_process_or_busy_slot_skips_cleanup(tmp_path, monkeypatch):
    now = 20_000.0
    stale = 2 * 60 * 60
    job = _abandoned_job(tmp_path, "render-busy", now=now, age=stale + 1)
    renderer = BrowserRenderer(binary="chromium", temp_root=tmp_path, clock=lambda: 0.0)

    busy_slot = CleanupSlot(available=False)
    monkeypatch.setattr(browser_renderer_module, "_GLOBAL_BROWSER_SLOT", busy_slot)
    busy = renderer.cleanup_abandoned_jobs(
        now_epoch=now,
        stale_seconds=stale,
        allowance=_cleanup_allowance(),
        dry_run=False,
    )

    assert job.is_dir()
    assert busy.deleted_entries == 0
    assert busy.backlog_entries == 1
    assert busy_slot.release_calls == 0

    active_slot = CleanupSlot()
    monkeypatch.setattr(browser_renderer_module, "_GLOBAL_BROWSER_SLOT", active_slot)
    active_process = SimpleNamespace(pid=8822)
    renderer._register_process(active_process)
    try:
        active = renderer.cleanup_abandoned_jobs(
            now_epoch=now,
            stale_seconds=stale,
            allowance=_cleanup_allowance(),
            dry_run=False,
        )
    finally:
        renderer._unregister_process(active_process)

    assert job.is_dir()
    assert active.deleted_entries == 0
    assert active.backlog_entries == 1
    assert active_slot.release_calls == 1


def test_browser_cleanup_renames_to_gc_before_rmtree_and_recovers_gc_tombstone(
    tmp_path,
    monkeypatch,
):
    now = 20_000.0
    stale = 2 * 60 * 60
    job = _abandoned_job(tmp_path, "render-crashed", now=now, age=stale + 1)
    renderer = BrowserRenderer(binary="chromium", temp_root=tmp_path, clock=lambda: 0.0)
    real_rmtree = browser_renderer_module.shutil.rmtree
    removals = []

    def interrupted_rmtree(path, *args, **kwargs):
        removals.append(Path(path).name)
        raise OSError("simulated cleanup interruption")

    monkeypatch.setattr(browser_renderer_module.shutil, "rmtree", interrupted_rmtree)
    interrupted = renderer.cleanup_abandoned_jobs(
        now_epoch=now,
        stale_seconds=stale,
        allowance=_cleanup_allowance(),
        dry_run=False,
    )

    tombstone = tmp_path / ".gc-render-crashed"
    assert not job.exists()
    assert tombstone.is_dir()
    assert removals == [tombstone.name]
    assert interrupted.deleted_entries == 0
    assert interrupted.error_count == 1

    monkeypatch.setattr(browser_renderer_module.shutil, "rmtree", real_rmtree)
    recovered = renderer.cleanup_abandoned_jobs(
        now_epoch=now,
        stale_seconds=stale,
        allowance=_cleanup_allowance(),
        dry_run=False,
    )

    assert not tombstone.exists()
    assert recovered.deleted_entries == 1


def test_browser_cleanup_rejects_symlink_reparse_and_unknown_children(
    tmp_path,
    monkeypatch,
):
    now = 20_000.0
    stale = 2 * 60 * 60
    outside = tmp_path.parent / f"{tmp_path.name}-outside"
    outside.mkdir()
    sentinel = outside / "sentinel.bin"
    sentinel.write_bytes(b"keep")
    symlink = tmp_path / "render-symlink"
    try:
        symlink.symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):
        symlink = None
    unknown = _abandoned_job(tmp_path, "unknown-job", now=now, age=stale + 1)
    reparse = _abandoned_job(tmp_path, "render-reparse", now=now, age=stale + 1)
    real_lstat = browser_renderer_module.os.lstat

    def mark_reparse(path, *args, **kwargs):
        info = real_lstat(path, *args, **kwargs)
        if Path(path) != reparse:
            return info
        return SimpleNamespace(
            st_mode=info.st_mode,
            st_dev=info.st_dev,
            st_ino=info.st_ino,
            st_size=info.st_size,
            st_mtime=info.st_mtime,
            st_mtime_ns=info.st_mtime_ns,
            st_file_attributes=0x400,
        )

    monkeypatch.setattr(browser_renderer_module.os, "lstat", mark_reparse)
    renderer = BrowserRenderer(binary="chromium", temp_root=tmp_path, clock=lambda: 0.0)

    aggregate = renderer.cleanup_abandoned_jobs(
        now_epoch=now,
        stale_seconds=stale,
        allowance=_cleanup_allowance(),
        dry_run=False,
    )

    assert sentinel.read_bytes() == b"keep"
    if symlink is not None:
        assert symlink.is_symlink()
    assert unknown.is_dir()
    assert reparse.is_dir()
    assert aggregate.deleted_entries == 0
    assert aggregate.skipped_unsafe >= 2 + int(symlink is not None)


def test_browser_cleanup_rejects_symlink_or_reparse_temp_root(tmp_path, monkeypatch):
    now = 20_000.0
    stale = 2 * 60 * 60
    target = tmp_path / "target"
    target.mkdir()
    job = _abandoned_job(target, "render-root", now=now, age=stale + 1)
    root = tmp_path / "root-link"
    try:
        root.symlink_to(target, target_is_directory=True)
    except (OSError, NotImplementedError):
        root = target
        real_lstat = browser_renderer_module.os.lstat

        def mark_root_reparse(path, *args, **kwargs):
            info = real_lstat(path, *args, **kwargs)
            if Path(path) != root:
                return info
            return SimpleNamespace(
                st_mode=info.st_mode,
                st_file_attributes=0x400,
            )

        monkeypatch.setattr(browser_renderer_module.os, "lstat", mark_root_reparse)
    slot = CleanupSlot()
    monkeypatch.setattr(browser_renderer_module, "_GLOBAL_BROWSER_SLOT", slot)
    renderer = BrowserRenderer(binary="chromium", temp_root=root, clock=lambda: 0.0)

    aggregate = renderer.cleanup_abandoned_jobs(
        now_epoch=now,
        stale_seconds=stale,
        allowance=_cleanup_allowance(),
        dry_run=False,
    )

    assert job.is_dir()
    assert aggregate.deleted_entries == 0
    assert aggregate.skipped_unsafe == 1
    assert aggregate.backlog_entries == 1
    assert slot.release_calls == 1


def test_browser_cleanup_scan_limit_stops_before_any_delete(tmp_path):
    now = 20_000.0
    stale = 2 * 60 * 60
    jobs = {
        _abandoned_job(
            tmp_path,
            f"render-scan-{index}",
            now=now,
            age=stale + 1,
        )
        for index in range(2)
    }
    renderer = BrowserRenderer(binary="chromium", temp_root=tmp_path, clock=lambda: 0.0)

    aggregate = renderer.cleanup_abandoned_jobs(
        now_epoch=now,
        stale_seconds=stale,
        allowance=_cleanup_allowance(scanned=1),
        dry_run=False,
    )

    assert all(job.is_dir() for job in jobs)
    assert aggregate.scanned_entries == 1
    assert aggregate.candidate_entries == 0
    assert aggregate.deleted_entries == 0
    assert aggregate.backlog_entries == 1


def test_browser_cleanup_stat_change_skips_rename_and_remove(tmp_path, monkeypatch):
    now = 20_000.0
    stale = 2 * 60 * 60
    job = _abandoned_job(tmp_path, "render-raced", now=now, age=stale + 1)
    real_lstat = browser_renderer_module.os.lstat
    job_stats = 0

    def change_second_job_stat(path, *args, **kwargs):
        nonlocal job_stats
        info = real_lstat(path, *args, **kwargs)
        if Path(path) != job:
            return info
        job_stats += 1
        if job_stats == 1:
            return info
        return SimpleNamespace(
            st_mode=info.st_mode,
            st_dev=info.st_dev,
            st_ino=info.st_ino,
            st_size=info.st_size,
            st_mtime=info.st_mtime,
            st_mtime_ns=info.st_mtime_ns + 1,
            st_file_attributes=getattr(info, "st_file_attributes", 0),
        )

    monkeypatch.setattr(browser_renderer_module.os, "lstat", change_second_job_stat)
    monkeypatch.setattr(
        browser_renderer_module.os,
        "rename",
        lambda *_args, **_kwargs: pytest.fail("changed job was renamed"),
    )
    monkeypatch.setattr(
        browser_renderer_module.shutil,
        "rmtree",
        lambda *_args, **_kwargs: pytest.fail("changed job was removed"),
    )
    renderer = BrowserRenderer(binary="chromium", temp_root=tmp_path, clock=lambda: 0.0)

    aggregate = renderer.cleanup_abandoned_jobs(
        now_epoch=now,
        stale_seconds=stale,
        allowance=_cleanup_allowance(),
        dry_run=False,
    )

    assert job.is_dir()
    assert aggregate.candidate_entries == 1
    assert aggregate.deleted_entries == 0
    assert aggregate.skipped_unsafe == 1
    assert aggregate.backlog_entries == 1


def test_browser_cleanup_records_job_tree_io_failure_as_error(tmp_path, monkeypatch):
    now = 20_000.0
    stale = 2 * 60 * 60
    job = _abandoned_job(tmp_path, "render-io-error", now=now, age=stale + 1)
    real_scandir = browser_renderer_module.os.scandir

    def fail_job_scan(path):
        if Path(path) == job:
            raise PermissionError("simulated unreadable job")
        return real_scandir(path)

    monkeypatch.setattr(browser_renderer_module.os, "scandir", fail_job_scan)
    renderer = BrowserRenderer(binary="chromium", temp_root=tmp_path, clock=lambda: 0.0)

    aggregate = renderer.cleanup_abandoned_jobs(
        now_epoch=now,
        stale_seconds=stale,
        allowance=_cleanup_allowance(),
        dry_run=False,
    )

    assert job.is_dir()
    assert aggregate.deleted_entries == 0
    assert aggregate.error_count == 1
    assert aggregate.skipped_unsafe == 0
    assert aggregate.backlog_entries == 1


def test_browser_cleanup_obeys_shared_budget_and_returns_aggregate_only(
    tmp_path,
    monkeypatch,
):
    now = 20_000.0
    stale = 2 * 60 * 60
    for index in range(3):
        _abandoned_job(
            tmp_path,
            f"render-budget-{index}",
            now=now,
            age=stale + 1,
            payload=b"four",
        )
    renderer = BrowserRenderer(binary="chromium", temp_root=tmp_path, clock=lambda: 0.0)

    limited_allowance = _cleanup_allowance(
        scanned=8,
        deleted=1,
        deleted_bytes=4,
    )
    limited = renderer.cleanup_abandoned_jobs(
        now_epoch=now,
        stale_seconds=stale,
        allowance=limited_allowance,
        dry_run=False,
    )

    expected_keys = {
        "scanned_entries",
        "candidate_entries",
        "deleted_entries",
        "deleted_bytes",
        "retained_current",
        "retained_last_good",
        "retained_recent",
        "skipped_unsafe",
        "error_count",
        "backlog_entries",
    }
    assert limited is limited_allowance.aggregate
    assert set(vars(limited)) == expected_keys
    assert limited.deleted_entries == 1
    assert limited.deleted_bytes == 4
    assert limited.backlog_entries == 1
    assert all(not isinstance(value, (Path, str)) for value in vars(limited).values())
    remaining_after_first = set(tmp_path.glob("render-budget-*"))
    repeated = renderer.cleanup_abandoned_jobs(
        now_epoch=now,
        stale_seconds=stale,
        allowance=limited_allowance,
        dry_run=False,
    )
    assert repeated is limited
    assert repeated.deleted_entries == 1
    assert set(tmp_path.glob("render-budget-*")) == remaining_after_first

    dry_root = tmp_path / "dry"
    dry_job = _abandoned_job(
        dry_root,
        "render-dry",
        now=now,
        age=stale + 1,
        payload=b"four",
    )
    dry_renderer = BrowserRenderer(binary="chromium", temp_root=dry_root, clock=lambda: 0.0)
    dry_allowance = _cleanup_allowance()
    dry = dry_renderer.cleanup_abandoned_jobs(
        now_epoch=now,
        stale_seconds=stale,
        allowance=dry_allowance,
        dry_run=True,
    )
    assert dry_job.is_dir()
    assert dry is dry_allowance.aggregate
    assert dry.candidate_entries == 1
    assert dry.deleted_entries == 0
    real = dry_renderer.cleanup_abandoned_jobs(
        now_epoch=now,
        stale_seconds=stale,
        allowance=_cleanup_allowance(),
        dry_run=False,
    )
    assert not dry_job.exists()
    assert real.candidate_entries == dry.candidate_entries

    deadline_root = tmp_path / "deadline"
    deadline_job = _abandoned_job(
        deadline_root,
        "render-deadline",
        now=now,
        age=stale + 1,
    )
    ticks = iter((0.0, 1.0))
    deadline_clock = lambda: next(ticks, 1.0)
    deadline_renderer = BrowserRenderer(
        binary="chromium",
        temp_root=deadline_root,
        clock=deadline_clock,
    )
    timed_out = deadline_renderer.cleanup_abandoned_jobs(
        now_epoch=now,
        stale_seconds=stale,
        allowance=_cleanup_allowance(duration=0.5, clock=deadline_clock),
        dry_run=False,
    )
    assert deadline_job.is_dir()
    assert timed_out.deleted_entries == 0
    assert timed_out.backlog_entries == 1
