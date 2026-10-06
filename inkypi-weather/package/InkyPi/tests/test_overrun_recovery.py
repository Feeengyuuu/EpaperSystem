from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import threading

from flask import Flask

from refresh_task import ActiveOperationSnapshot
from runtime.overrun_recovery import (
    OVERRUN_REASON,
    OverrunPolicy,
    WorkerOverrunGuard,
)
from runtime.runtime_status import record_supervised_recovery, recovery_events


def _active(command_id="cmd-1", plugin_id="backtothedate"):
    return ActiveOperationSnapshot(
        command_id=command_id,
        kind="refresh",
        source="background",
        intent="data_refresh",
        plugin_id=plugin_id,
        instance_uuid="instance-1",
        started_monotonic=100.0,
        deadline_monotonic=280.0,
    )


def _guard(history=(), *, grace=240.0, interval=3600.0, enabled=True, now=None):
    reads = []
    now = now or datetime(2026, 10, 5, 12, 0, tzinfo=timezone.utc)

    def read_history():
        reads.append(1)
        return list(history)

    guard = WorkerOverrunGuard(
        OverrunPolicy(enabled=enabled, grace_seconds=grace, min_interval_seconds=interval),
        history=read_history,
        wall_clock=lambda: now.timestamp(),
    )
    return guard, reads


def test_no_active_operation_or_short_overrun_does_not_recover():
    guard, reads = _guard()

    assert guard.evaluate(None, None) is None
    assert guard.evaluate(_active(), -5.0) is None
    assert guard.evaluate(_active(), 239.0) is None
    assert reads == []


def test_sustained_overrun_requests_supervised_replacement():
    guard, _reads = _guard()

    request = guard.evaluate(_active(), 241.5)

    assert request == {
        "reason": OVERRUN_REASON,
        "plugin_id": "backtothedate",
        "intent": "data_refresh",
        "overrun_seconds": 241.5,
    }


def test_disabled_policy_never_recovers():
    guard, _reads = _guard(enabled=False)

    assert guard.evaluate(_active(), 10_000.0) is None


def test_recent_overrun_recovery_suppresses_restart_loop_once_per_command():
    now = datetime(2026, 10, 5, 12, 0, tzinfo=timezone.utc)
    recent = {"at": (now - timedelta(minutes=20)).isoformat(), "reason": OVERRUN_REASON}
    guard, reads = _guard([recent], now=now)

    assert guard.evaluate(_active(), 500.0) is None
    assert guard.evaluate(_active(), 900.0) is None
    assert reads == [1]
    # A different stuck command is evaluated again rather than silently ignored.
    assert guard.evaluate(_active("cmd-2"), 500.0) is None
    assert reads == [1, 1]


def test_old_or_unrelated_recoveries_do_not_suppress():
    now = datetime(2026, 10, 5, 12, 0, tzinfo=timezone.utc)
    history = [
        {"at": (now - timedelta(hours=2)).isoformat(), "reason": OVERRUN_REASON},
        {"at": (now - timedelta(minutes=1)).isoformat(), "reason": "memory_pressure"},
        {"at": None, "reason": OVERRUN_REASON},
    ]
    guard, _reads = _guard(history, now=now)

    assert guard.evaluate(_active(), 300.0)["reason"] == OVERRUN_REASON


def test_policy_reads_bounded_configuration():
    values = {
        "refresh_overrun_recovery_enabled": False,
        "refresh_overrun_grace_seconds": 5,
        "refresh_overrun_min_interval_seconds": 10 ** 9,
    }
    config = SimpleNamespace(get_config=lambda key, default=None: values.get(key, default))

    policy = OverrunPolicy.from_config(config)

    assert policy.enabled is False
    assert policy.grace_seconds == 60.0
    assert policy.min_interval_seconds == 86_400.0
    assert OverrunPolicy.from_config(None) == OverrunPolicy()


def test_policy_ignores_invalid_configuration():
    values = {
        "refresh_overrun_grace_seconds": "soon",
        "refresh_overrun_min_interval_seconds": float("nan"),
    }
    config = SimpleNamespace(get_config=lambda key, default=None: values.get(key, default))

    assert OverrunPolicy.from_config(config) == OverrunPolicy()


def test_recovery_ledger_keeps_overrun_reason(tmp_path):
    config = SimpleNamespace(runtime_paths=SimpleNamespace(data_dir=tmp_path, release_id="r1"))

    record_supervised_recovery(config, OVERRUN_REASON)

    assert recovery_events(tmp_path)[-1]["reason"] == OVERRUN_REASON


def test_restart_monitor_stages_overrun_and_bounds_shutdown(monkeypatch):
    import inkypi

    staged = []
    interrupted = threading.Event()
    timers = []

    class FakeTimer:
        def __init__(self, seconds, callback):
            timers.append((seconds, callback))
            self.daemon = False

        def start(self):
            timers.append("started")

    class Task:
        device_config = SimpleNamespace(get_config=lambda key, default=None: default)
        restart_request = None

        def active_operation_snapshot(self):
            return _active()

        def active_operation_overrun_seconds(self):
            return 400.0

        def stage_restart_request(self, request):
            staged.append(request)
            self.restart_request = dict(request)
            return self.restart_request

    monkeypatch.setattr(inkypi, "RESTART_REQUEST_POLL_SECONDS", 0.01)
    monkeypatch.setattr(inkypi, "_interrupt_waitress_for_restart", interrupted.set)
    monkeypatch.setattr(inkypi.threading, "Timer", FakeTimer)
    monkeypatch.setattr(
        "runtime.runtime_status.record_supervised_recovery",
        lambda _config, reason: staged.append(("ledger", reason)),
    )
    stop = threading.Event()

    inkypi._monitor_restart_request(Task(), stop)

    assert interrupted.is_set()
    assert staged[0]["reason"] == OVERRUN_REASON
    assert staged[1] == ("ledger", OVERRUN_REASON)
    assert timers[0][0] == inkypi.ISOLATION_RECOVERY_EXIT_TIMEOUT_SECONDS
    assert timers[-1] == "started"


def test_restart_monitor_ignores_tasks_without_overrun_support(monkeypatch):
    import inkypi

    class Task:
        restart_request = None

    monkeypatch.setattr(inkypi, "RESTART_REQUEST_POLL_SECONDS", 0.01)
    stop = threading.Event()
    thread = threading.Thread(target=inkypi._monitor_restart_request, args=(Task(), stop))
    thread.start()
    stop.set()
    thread.join(1.0)

    assert not thread.is_alive()


def test_refresh_task_exposes_overrun_and_keeps_first_restart_request(tmp_path):
    from refresh_task import RefreshTask

    clock = [1_000.0]
    task = RefreshTask.__new__(RefreshTask)
    task._clock = lambda: clock[0]
    task._active_operation = None
    task._restart_request = None
    task.refresh_queue = SimpleNamespace(wake=lambda: None)

    assert task.active_operation_overrun_seconds() is None
    task._active_operation = _active()
    assert task.active_operation_overrun_seconds() == 720.0

    first = task.stage_restart_request({"reason": OVERRUN_REASON})
    second = task.stage_restart_request({"reason": "memory_pressure"})

    assert first == second == {"reason": OVERRUN_REASON}
    assert task.restart_request == {"reason": OVERRUN_REASON}


def test_run_returns_supervised_exit_after_overrun(monkeypatch):
    import inkypi
    import waitress

    interrupted = threading.Event()

    class Config:
        def get_config(self, key, default=None):
            return False if key == "startup" else default

    class Task:
        device_config = Config()
        restart_request = None
        stop_timeouts = []

        def start(self):
            pass

        def stop(self, join_timeout=None):
            self.stop_timeouts.append(join_timeout)

        def active_operation_snapshot(self):
            return _active()

        def active_operation_overrun_seconds(self):
            return 1_000.0

        def stage_restart_request(self, request):
            self.restart_request = dict(request)
            return self.restart_request

    task = Task()
    app = Flask(__name__)
    app.config.update(DEVICE_CONFIG=Config(), DISPLAY_MANAGER=object(), REFRESH_TASK=task)

    monkeypatch.setattr(waitress, "serve", lambda *_a, **_k: interrupted.wait(1.0))
    monkeypatch.setattr(inkypi, "_interrupt_waitress_for_restart", interrupted.set)
    monkeypatch.setattr(inkypi, "RESTART_REQUEST_POLL_SECONDS", 0.01)
    monkeypatch.setattr(inkypi, "shutdown_long_task_executors", lambda **_k: None)
    monkeypatch.setattr(inkypi, "close_browser_renderer", lambda: None)
    monkeypatch.setattr(inkypi, "close_http_session", lambda: None)
    monkeypatch.setattr(inkypi.threading, "Timer", lambda *_a: SimpleNamespace(start=lambda: None, daemon=True))
    monkeypatch.setattr("runtime.runtime_status.record_supervised_recovery", lambda *_a: None)

    assert inkypi.run(app, dev_mode=False, port=80) == inkypi.MEMORY_PRESSURE_RESTART_EXIT_CODE
    assert task.stop_timeouts == [inkypi.MEMORY_PRESSURE_WORKER_STOP_TIMEOUT_SECONDS]
