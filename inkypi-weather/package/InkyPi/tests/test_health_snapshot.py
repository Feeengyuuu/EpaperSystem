from pathlib import Path
import time
from types import MappingProxyType, SimpleNamespace

from src.health import HealthCollector, HealthPublisher, ReadinessEvaluator


class FakeClock:
    def __init__(self, value=0.0):
        self.value = float(value)

    def monotonic(self):
        return self.value


def _components(**overrides):
    components = {
        "runtime": {"dev_mode": False},
        "lifecycle": {"state": "running"},
        "config": {
            "valid": True,
            "writable": True,
            "source": "primary",
            "version": 2,
        },
        "display": {"state": "committed", "commit_id": "a" * 32},
        "queue": {
            "depth": 0,
            "capacity": 32,
            "accepting": True,
            "full_since_monotonic": None,
        },
        "scheduler": {
            "heartbeat_monotonic": 150.0,
            "tick_seconds": 30.0,
            "active_deadline_monotonic": None,
        },
        "startup": {"degraded": False, "reason_codes": ()},
        "disk": {
            "free_bytes": 1024 * 1024 * 1024,
            "soft_min_bytes": 256 * 1024 * 1024,
            "hard_min_bytes": 64 * 1024 * 1024,
        },
    }
    components.update(overrides)
    return components


def _snapshot(clock, **overrides):
    publisher = HealthPublisher(
        release_id="release-test",
        boot_id="boot-test",
        clock=clock.monotonic,
        started_monotonic=0.0,
    )
    publisher.publish_components(_components(**overrides))
    return publisher.snapshot()


def test_health_publisher_detaches_components_and_snapshot_read_is_lock_free():
    clock = FakeClock(10)
    publisher = HealthPublisher(
        release_id="release-test",
        boot_id="boot-test",
        clock=clock.monotonic,
        started_monotonic=0,
    )
    source = {"nested": {"values": ["one"]}}
    publisher.publish_component("example", source)
    source["nested"]["values"].append("two")
    snapshot = publisher.snapshot()

    class ExplodingLock:
        def __enter__(self):
            raise AssertionError("snapshot tried to acquire publisher lock")

        def __exit__(self, *_args):
            return False

    publisher._lock = ExplodingLock()

    assert publisher.snapshot() is snapshot
    assert isinstance(snapshot.components, MappingProxyType)
    assert snapshot.components["example"]["nested"]["values"] == ("one",)


def test_health_publisher_redacts_secrets_queries_and_setting_values():
    publisher = HealthPublisher(release_id="release-test")

    publisher.publish_component(
        "diagnostic",
        {
            "api_key": "super-secret",
            "source_url": "https://example.test/path?token=secret#fragment",
            "plugin_settings": {"city": "Seattle", "units": "metric"},
        },
    )
    diagnostic = publisher.snapshot().components["diagnostic"]

    assert diagnostic["api_key"] == "<redacted>"
    assert diagnostic["source_url"] == "https://example.test/path"
    assert diagnostic["plugin_settings"] == ("city", "units")


def test_ready_snapshot_is_ready():
    clock = FakeClock(160)

    result = ReadinessEvaluator().evaluate(_snapshot(clock), now_monotonic=160)

    assert result.status == "ready"
    assert result.error_codes == ()


def test_display_unknown_is_not_ready():
    clock = FakeClock(160)
    snapshot = _snapshot(
        clock,
        display={"state": "display_unknown", "commit_id": "pending"},
    )

    result = ReadinessEvaluator().evaluate(snapshot, now_monotonic=160)

    assert result.status == "not_ready"
    assert "display_unknown" in result.error_codes


def test_queue_full_is_degraded_until_sustained_with_stalled_heartbeat():
    clock = FakeClock(160)
    queue = {
        "depth": 32,
        "capacity": 32,
        "accepting": True,
        "full_since_monotonic": 100.0,
    }
    fresh = _snapshot(
        clock,
        queue=queue,
        scheduler={
            "heartbeat_monotonic": 159.0,
            "tick_seconds": 30.0,
            "active_deadline_monotonic": None,
        },
    )

    assert ReadinessEvaluator().evaluate(fresh, now_monotonic=160).status == "degraded"

    stalled = _snapshot(
        clock,
        queue=queue,
        scheduler={
            "heartbeat_monotonic": 90.0,
            "tick_seconds": 30.0,
            "active_deadline_monotonic": None,
        },
    )
    result = ReadinessEvaluator().evaluate(stalled, now_monotonic=161)

    assert result.status == "not_ready"
    assert "queue_full_stalled" in result.error_codes


def test_active_operation_deadline_extends_scheduler_heartbeat_budget():
    clock = FakeClock(170)
    snapshot = _snapshot(
        clock,
        scheduler={
            "heartbeat_monotonic": 90.0,
            "tick_seconds": 30.0,
            "active_deadline_monotonic": 180.0,
        },
    )

    assert ReadinessEvaluator().evaluate(snapshot, now_monotonic=170).status == "ready"
    result = ReadinessEvaluator().evaluate(snapshot, now_monotonic=191)
    assert result.status == "not_ready"
    assert "scheduler_stalled" in result.error_codes


def test_starting_transitions_to_not_ready_after_grace():
    clock = FakeClock(30)
    snapshot = _snapshot(clock, lifecycle={"state": "starting"})
    evaluator = ReadinessEvaluator(startup_grace_seconds=120)

    assert evaluator.evaluate(snapshot, now_monotonic=30).status == "starting"
    assert evaluator.evaluate(snapshot, now_monotonic=121).status == "not_ready"


def test_running_without_scheduler_heartbeat_is_starting_during_grace():
    clock = FakeClock(30)
    snapshot = _snapshot(
        clock,
        scheduler={
            "heartbeat_monotonic": None,
            "tick_seconds": 30,
            "active_deadline_monotonic": None,
        },
    )

    result = ReadinessEvaluator().evaluate(snapshot, now_monotonic=30)

    assert result.status == "starting"
    assert "scheduler_starting" in result.error_codes


def test_malformed_scheduler_values_fail_closed_without_raising():
    clock = FakeClock(160)
    snapshot = _snapshot(
        clock,
        scheduler={
            "heartbeat_monotonic": "not-a-number",
            "tick_seconds": "nan",
            "active_deadline_monotonic": object(),
        },
    )

    result = ReadinessEvaluator().evaluate(snapshot, now_monotonic=160)

    assert result.status == "not_ready"
    assert "scheduler_not_started" in result.error_codes


def test_development_without_committed_display_is_degraded_not_failed():
    clock = FakeClock(160)
    snapshot = _snapshot(
        clock,
        runtime={"dev_mode": True},
        display={"state": "not_ready", "commit_id": None},
    )

    result = ReadinessEvaluator().evaluate(snapshot, now_monotonic=160)

    assert result.status == "degraded"
    assert "development_display_unavailable" in result.error_codes


def test_health_collector_start_never_waits_for_core_component_lock(tmp_path):
    class BlockingLifecycle:
        def snapshot(self):
            time.sleep(0.3)
            raise RuntimeError("locked")

    refresh_task = SimpleNamespace(lifecycle=BlockingLifecycle())
    publisher = HealthPublisher(release_id="release-test")
    collector = HealthCollector(
        publisher,
        refresh_task=refresh_task,
        device_config=SimpleNamespace(
            _config_store=SimpleNamespace(
                current=lambda: (_ for _ in ()).throw(RuntimeError("locked"))
            ),
            get_config=lambda _key, default=None: default,
        ),
        runtime_paths=SimpleNamespace(data_dir=Path(tmp_path)),
        dev_mode=True,
        startup_state=lambda: {"degraded": False, "reasons": {}},
    )

    started = time.monotonic()
    collector.start()
    elapsed = time.monotonic() - started

    assert elapsed < 0.2
    collector.stop(join_timeout=1.0)


def test_health_collection_triggers_only_lightweight_due_cache_maintenance(tmp_path):
    calls = []
    cache_manager = SimpleNamespace(
        maintenance_if_due=lambda: calls.append("maintenance") or False,
    )
    refresh_task = SimpleNamespace(
        lifecycle=SimpleNamespace(
            snapshot=lambda: (_ for _ in ()).throw(RuntimeError("unavailable"))
        ),
    )
    collector = HealthCollector(
        HealthPublisher(release_id="release-test"),
        refresh_task=refresh_task,
        device_config=SimpleNamespace(
            _config_store=SimpleNamespace(
                current=lambda: (_ for _ in ()).throw(RuntimeError("unavailable"))
            ),
            get_config=lambda _key, default=None: default,
        ),
        runtime_paths=SimpleNamespace(data_dir=Path(tmp_path)),
        dev_mode=True,
        startup_state=lambda: {"degraded": False, "reasons": {}},
        cache_manager=cache_manager,
    )

    collector.collect_once()

    assert calls == ["maintenance"]
