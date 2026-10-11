"""Readiness transitions are journaled once, with stable reason codes only."""

import logging
from pathlib import Path
import re
import threading
from types import SimpleNamespace

import pytest

from health import (
    HealthCollector,
    HealthPublisher,
    ReadinessResult,
    ReadinessTransitionLog,
)


TRANSITION_MESSAGE = "Readiness status changed."


class FakeClock:
    def __init__(self, value=0.0):
        self.value = float(value)

    def monotonic(self):
        return self.value


def _healthy_cache_lifecycle(**extra):
    return SimpleNamespace(
        enabled=True,
        disk_tier=SimpleNamespace(value="healthy"),
        ran_at=None,
        dry_run=False,
        scanned_entries=0,
        candidate_entries=0,
        deleted_entries=0,
        deleted_bytes=0,
        retained_current=0,
        retained_last_good=0,
        retained_recent=0,
        skipped_unsafe=0,
        error_count=0,
        backlog_entries=0,
        **extra,
    )


def _collector(clock, **kwargs):
    """Build a collector whose live inputs are driven by a mutable ``state``."""

    state = {
        "lifecycle": "running",
        "data_stalled_count": 0,
        "queue_depth": 0,
        "startup_degraded": False,
    }
    refresh_task = SimpleNamespace(
        lifecycle=SimpleNamespace(
            snapshot=lambda: SimpleNamespace(
                state=SimpleNamespace(value=state["lifecycle"]),
                changed_at_monotonic=1.0,
            )
        ),
        runtime_state=SimpleNamespace(
            snapshot=lambda: SimpleNamespace(
                display_state="committed",
                display_commit_id="a" * 32,
                updated_at="2026-10-10T12:00:00+00:00",
            )
        ),
        refresh_queue=SimpleNamespace(
            snapshot=lambda: SimpleNamespace(
                depth=state["queue_depth"],
                capacity=32,
                rejected_total=0,
                superseded_total=0,
                accepting=True,
            )
        ),
        scheduler_snapshot=lambda: SimpleNamespace(
            last_attempt_monotonic=clock.value,
        ),
        active_operation_snapshot=lambda: None,
        refresh_health_snapshot=lambda: {
            "resource_tier": "healthy",
            "due_counts": {},
            "progress": {
                "enabled": True,
                "observed": True,
                "active_instances": 30,
                "data_stalled_count": state["data_stalled_count"],
                "presentation_stalled_count": 0,
            },
        },
        cache_lifecycle=SimpleNamespace(snapshot=_healthy_cache_lifecycle),
        _scheduler_poll_seconds=lambda: 30.0,
    )
    config_status = SimpleNamespace(
        valid=True,
        writable=True,
        source="primary",
        version=4,
        degraded_reason=None,
    )
    publisher = HealthPublisher(
        release_id="release-test",
        boot_id="boot-test",
        clock=clock.monotonic,
        started_monotonic=0.0,
    )
    collector = HealthCollector(
        publisher,
        refresh_task=refresh_task,
        device_config=SimpleNamespace(
            _config_store=SimpleNamespace(
                current=lambda: SimpleNamespace(status=config_status)
            ),
            get_config=lambda _key, default=None: default,
        ),
        runtime_paths=SimpleNamespace(data_dir=Path("unused")),
        dev_mode=False,
        startup_state=lambda: {
            "degraded": state["startup_degraded"],
            "reasons": {},
        },
        clock=clock.monotonic,
        **{"readiness_settle_seconds": 0.0, **kwargs},
    )
    return collector, state


def _transitions(caplog):
    return [
        record
        for record in caplog.records
        if record.name == "health"
        and record.getMessage().startswith(TRANSITION_MESSAGE)
    ]


def _fields(record):
    parts = record.getMessage().split(" | ")[1:]
    return dict(part.split(": ", 1) for part in parts)


@pytest.fixture
def caplog_health(caplog):
    caplog.set_level(logging.INFO, logger="health")
    return caplog


def test_first_settled_non_ready_verdict_after_start_is_a_warning(caplog_health):
    clock = FakeClock(300)
    collector, state = _collector(clock)
    state["data_stalled_count"] = 1

    collector.collect_once()

    [record] = _transitions(caplog_health)
    assert record.levelno == logging.WARNING
    assert _fields(record) == {
        "status": "degraded",
        "previous_status": "startup",
        "error_codes": "data_progress_stalled",
        "added": "data_progress_stalled",
        "removed": "none",
    }


def test_unchanged_readiness_is_logged_only_once(caplog_health):
    clock = FakeClock(300)
    collector, state = _collector(clock)
    state["data_stalled_count"] = 1

    for step in range(5):
        clock.value = 300 + step
        collector.collect_once()

    assert len(_transitions(caplog_health)) == 1


def test_first_ready_verdict_is_logged_once_at_info(caplog_health):
    clock = FakeClock(300)
    collector, _state = _collector(clock)

    collector.collect_once()
    collector.collect_once()

    [record] = _transitions(caplog_health)
    assert record.levelno == logging.INFO
    assert _fields(record)["status"] == "ready"
    assert _fields(record)["error_codes"] == "none"


def test_ready_to_degraded_warns_and_recovery_to_ready_is_info(caplog_health):
    clock = FakeClock(300)
    collector, state = _collector(clock)
    collector.collect_once()

    state["data_stalled_count"] = 2
    clock.value = 301
    collector.collect_once()
    state["data_stalled_count"] = 0
    clock.value = 302
    collector.collect_once()

    first, worsened, recovered = _transitions(caplog_health)
    assert first.levelno == logging.INFO
    assert worsened.levelno == logging.WARNING
    assert _fields(worsened) == {
        "status": "degraded",
        "previous_status": "ready",
        "error_codes": "data_progress_stalled",
        "added": "data_progress_stalled",
        "removed": "none",
    }
    assert recovered.levelno == logging.INFO
    assert _fields(recovered) == {
        "status": "ready",
        "previous_status": "degraded",
        "error_codes": "none",
        "added": "none",
        "removed": "data_progress_stalled",
    }


def test_new_reason_code_with_same_status_is_a_warning(caplog_health):
    clock = FakeClock(300)
    collector, state = _collector(clock)
    state["data_stalled_count"] = 1
    collector.collect_once()

    state["queue_depth"] = 32
    clock.value = 301
    collector.collect_once()

    _first, changed = _transitions(caplog_health)
    assert changed.levelno == logging.WARNING
    fields = _fields(changed)
    assert fields["status"] == "degraded"
    assert fields["previous_status"] == "degraded"
    assert fields["error_codes"] == "data_progress_stalled,queue_full"
    assert fields["added"] == "queue_full"
    assert fields["removed"] == "none"


def test_removed_reason_code_while_still_degraded_is_info(caplog_health):
    clock = FakeClock(300)
    collector, state = _collector(clock)
    state["data_stalled_count"] = 1
    state["queue_depth"] = 32
    collector.collect_once()

    state["queue_depth"] = 0
    clock.value = 301
    collector.collect_once()

    _first, improved = _transitions(caplog_health)
    assert improved.levelno == logging.INFO
    assert _fields(improved)["status"] == "degraded"
    assert _fields(improved)["removed"] == "queue_full"
    assert _fields(improved)["added"] == "none"


def test_reaching_not_ready_from_degraded_is_a_warning(caplog_health):
    clock = FakeClock(300)
    collector, state = _collector(clock)
    state["data_stalled_count"] = 1
    collector.collect_once()

    state["lifecycle"] = "stopping"
    clock.value = 301
    collector.collect_once()

    _first, worsened = _transitions(caplog_health)
    assert worsened.levelno == logging.WARNING
    assert _fields(worsened)["status"] == "not_ready"
    assert _fields(worsened)["previous_status"] == "degraded"
    assert "lifecycle_not_running" in _fields(worsened)["added"].split(",")


def test_startup_grace_is_not_logged_until_the_first_settled_verdict(caplog_health):
    clock = FakeClock(5)
    collector, state = _collector(clock)
    state["lifecycle"] = "starting"

    for step in range(3):
        clock.value = 5 + step
        collector.collect_once()
    assert _transitions(caplog_health) == []

    state["lifecycle"] = "running"
    clock.value = 9
    collector.collect_once()

    [record] = _transitions(caplog_health)
    assert record.levelno == logging.INFO
    assert _fields(record)["status"] == "ready"
    assert _fields(record)["previous_status"] == "startup"


def test_startup_that_never_settles_warns_once_grace_expires(caplog_health):
    clock = FakeClock(5)
    collector, state = _collector(clock)
    state["lifecycle"] = "starting"
    collector.collect_once()

    clock.value = 121
    collector.collect_once()
    clock.value = 122
    collector.collect_once()

    [record] = _transitions(caplog_health)
    assert record.levelno == logging.WARNING
    assert _fields(record)["status"] == "not_ready"
    assert "lifecycle_not_running" in _fields(record)["error_codes"].split(",")


def test_readiness_logs_never_include_component_values_or_secrets(caplog_health):
    clock = FakeClock(300)
    collector, state = _collector(clock)
    secrets = (
        "super-secret-api-key",
        "C:/secret/config/device.json",
        "/home/inkypi/.config/secret",
        "secret-plugin-name",
        "sensitive-instance-uuid",
        "provider exploded with secret text",
        "Seattle",
    )
    task = collector.refresh_task
    task.active_operation_snapshot = lambda: SimpleNamespace(
        intent=SimpleNamespace(value="data_refresh"),
        deadline_monotonic=clock.value + 60,
        plugin_id="secret-plugin-name",
        instance_uuid="sensitive-instance-uuid",
        settings={"api_key": "super-secret-api-key", "city": "Seattle"},
        error="provider exploded with secret text",
    )
    task.cache_lifecycle = SimpleNamespace(
        snapshot=lambda: _healthy_cache_lifecycle(
            candidate_paths=("/home/inkypi/.config/secret",),
        )
    )
    collector.startup_state = lambda: {
        "degraded": state["startup_degraded"],
        "reasons": {
            "provider": "provider exploded with secret text",
            "path": "C:/secret/config/device.json",
        },
    }
    collector.device_config._config_store.current = lambda: SimpleNamespace(
        status=SimpleNamespace(
            valid=True,
            writable=False,
            source="C:/secret/config/device.json",
            version=4,
            degraded_reason="super-secret-api-key",
        )
    )
    state["data_stalled_count"] = 1
    state["startup_degraded"] = True
    collector.collect_once()
    state["data_stalled_count"] = 0
    state["queue_depth"] = 32
    clock.value = 301
    collector.collect_once()

    records = _transitions(caplog_health)
    assert len(records) == 2
    for secret in secrets:
        assert secret not in caplog_health.text
    for record in records:
        fields = _fields(record)
        assert set(fields) == {
            "status", "previous_status", "error_codes", "added", "removed",
        }
        for value in fields.values():
            assert re.fullmatch(r"[a-z0-9_,]+", value), value


def test_unexpected_status_and_codes_are_logged_as_placeholders(caplog_health):
    log = ReadinessTransitionLog()

    log.observe(
        ReadinessResult(
            status="Degraded: C:/secret",
            error_codes=("api_key=super-secret", "queue_full"),
        )
    )

    [record] = _transitions(caplog_health)
    assert record.levelno == logging.WARNING
    assert "super-secret" not in caplog_health.text
    assert "C:/secret" not in caplog_health.text
    assert _fields(record)["status"] == "unknown"
    assert _fields(record)["error_codes"] == "unrecognized_code,queue_full"


def test_concurrent_observers_log_one_transition_once(caplog_health):
    log = ReadinessTransitionLog()
    result = ReadinessResult(
        status="degraded",
        error_codes=("data_progress_stalled",),
    )
    barrier = threading.Barrier(8)

    def observe_many():
        barrier.wait()
        for _ in range(200):
            log.observe(result)

    threads = [threading.Thread(target=observe_many) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5.0)

    assert len(_transitions(caplog_health)) == 1


def test_reason_code_order_alone_is_not_a_transition(caplog_health):
    log = ReadinessTransitionLog()

    assert log.observe(
        ReadinessResult("degraded", ("queue_full", "data_progress_stalled"))
    ) is True
    assert log.observe(
        ReadinessResult("degraded", ("data_progress_stalled", "queue_full"))
    ) is False

    assert len(_transitions(caplog_health)) == 1


def test_readiness_check_failure_does_not_break_snapshot_collection(caplog_health):
    class ExplodingEvaluator:
        def evaluate(self, _snapshot, *, now_monotonic=None):
            raise RuntimeError("evaluator exploded")

    clock = FakeClock(300)
    collector, _state = _collector(
        clock,
        readiness_evaluator=ExplodingEvaluator(),
    )

    first = collector.collect_once()
    second = collector.collect_once()

    assert first.components["lifecycle"]["state"] == "running"
    assert second is collector.publisher.snapshot()
    failures = [
        record
        for record in caplog_health.records
        if record.getMessage().startswith("Readiness transition check failed")
    ]
    assert len(failures) == 1
    assert failures[0].levelno == logging.WARNING
    assert failures[0].getMessage().endswith("| error_code: RuntimeError")
    assert "evaluator exploded" not in caplog_health.text
    assert _transitions(caplog_health) == []


def test_collector_settles_worse_verdicts_by_default():
    assert HealthCollector.__init__.__kwdefaults__["readiness_settle_seconds"] >= 30


def test_transient_worse_verdict_that_clears_within_settle_is_not_logged(caplog_health):
    # The scheduler heartbeat blips to not_ready for 1-4 s around each display
    # command; those must not journal a WARNING/INFO pair every rotation.
    clock = FakeClock(300)
    collector, state = _collector(clock, readiness_settle_seconds=30.0)
    collector.collect_once()
    caplog_health.clear()
    state["data_stalled_count"] = 1
    for step in range(1, 5):
        clock.value = 300 + step
        collector.collect_once()
    state["data_stalled_count"] = 0
    clock.value = 306
    collector.collect_once()
    assert _transitions(caplog_health) == []


def test_sustained_worse_verdict_is_logged_once_after_settle(caplog_health):
    clock = FakeClock(300)
    collector, state = _collector(clock, readiness_settle_seconds=30.0)
    collector.collect_once()
    caplog_health.clear()
    state["data_stalled_count"] = 1
    for step in range(1, 40):
        clock.value = 300 + step
        collector.collect_once()
    [record] = _transitions(caplog_health)
    assert record.levelno == logging.WARNING
    assert record.created and _fields(record)["status"] == "degraded"
    state["data_stalled_count"] = 0
    clock.value = 341
    collector.collect_once()
    records = _transitions(caplog_health)
    assert len(records) == 2 and records[1].levelno == logging.INFO
    assert _fields(records[1])["status"] == "ready"


def test_settle_timer_restarts_when_a_blip_clears():
    log = ReadinessTransitionLog(settle_seconds=30.0)
    ready = ReadinessResult(status="ready", error_codes=())
    degraded = ReadinessResult(status="degraded", error_codes=("data_progress_stalled",))
    assert log.observe(ready, now=0) is True
    assert log.observe(degraded, now=10) is False
    assert log.observe(ready, now=20) is False
    assert log.observe(degraded, now=35) is False
    assert log.observe(degraded, now=60) is False
    assert log.observe(degraded, now=65) is True


def test_reason_code_churn_inside_a_worse_status_still_settles():
    log = ReadinessTransitionLog(settle_seconds=30.0)
    assert log.observe(ReadinessResult(status="ready", error_codes=()), now=0) is True
    first = ReadinessResult(status="degraded", error_codes=("data_progress_stalled",))
    second = ReadinessResult(status="degraded", error_codes=("data_progress_stalled", "queue_full"))
    assert log.observe(first, now=1) is False
    assert log.observe(second, now=15) is False
    assert log.observe(first, now=29) is False
    assert log.observe(second, now=32) is True
