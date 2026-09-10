"""Replay sustained provider failures through the scheduler's actual lane seam."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from runtime.refresh_contracts import CommandKind, CommandSource, RefreshIntent
from runtime.refresh_policy import RefreshLane
from runtime.scheduler_state import RetryRegistry
from runtime.retry_policy import DEFAULT_RETRY_POLICY, RetryPolicy, source_retry_policy
from tests.test_refresh_task import _make_runtime_task, _runtime_playlist, _runtime_plugin_data


@pytest.mark.parametrize("plugin", ["apod", "ticketmaster_events"])
def test_sustained_source_failures_back_off_without_blocking_other_lanes(tmp_path, plugin):
    now = datetime(2026, 9, 10, 13, tzinfo=timezone.utc)
    playlist = _runtime_playlist(_runtime_plugin_data(plugin, "Source", interval=1800))
    task, _, clock = _make_runtime_task(tmp_path, playlists=[playlist])
    task.retry_registry = RetryRegistry(jitter=lambda delay: delay)
    instance = playlist.plugins[0].snapshot()
    command = task._playlist_command(
        playlist.name,
        instance,
        source=CommandSource.BACKGROUND,
        intent=RefreshIntent.DATA_REFRESH,
        kind=CommandKind.CACHE_REFRESH,
    )
    delays = []
    for _ in range(7):
        delay = task._record_intent_failure(command, RuntimeError("provider unavailable"), now)
        state = task.runtime_state.snapshot().instances[instance.instance_uuid]
        remaining = task.retry_registry.next_delay(f"{instance.instance_uuid}:data", clock.monotonic())
        assert remaining == delay
        assert datetime.fromisoformat(state.data.next_retry_at) == now + timedelta(seconds=delay)
        assert state.live.next_retry_at is None
        delays.append(delay)
        now += timedelta(seconds=delay)
        clock.advance(delay)
    assert delays == [30, 60, 120, 300, 600, 900, 900]
    task._record_intent_success(command, instance, now, "day")
    assert task.runtime_state.snapshot().instances[instance.instance_uuid].data.next_retry_at is None
    assert task._record_intent_failure(command, RuntimeError("new failure"), now) == 30


@pytest.mark.parametrize(
    "plugin,intent",
    [
        ("sports_dashboard", RefreshIntent.LIVE_REFRESH),
        ("weather", RefreshIntent.DATA_REFRESH),
        ("apod", RefreshIntent.THEME_REDRAW),
        ("apod", RefreshIntent.DISPLAY_CACHE),
        ("ticketmaster_events", RefreshIntent.PRESENTATION_REFRESH),
    ],
)
def test_source_policy_does_not_slow_unrelated_work(plugin, intent):
    assert source_retry_policy(SimpleNamespace(plugin_id=plugin, intent=intent, payload={})) is DEFAULT_RETRY_POLICY


@pytest.mark.parametrize(
    "interval,maximum",
    [
        (120, 120),
        (1800, 900),
        (10800, 900),
        (True, 900),
        (0, 900),
        (-10, 900),
        (float("nan"), 900),
        ("bad", 900),
        (None, 900),
    ],
)
def test_source_retry_is_bounded_by_valid_saved_cadence(interval, maximum):
    policy = source_retry_policy(
        SimpleNamespace(
            plugin_id="apod", intent=RefreshIntent.DATA_REFRESH, payload={"refresh": {"interval": interval}}
        )
    )
    registry = RetryRegistry(jitter=lambda seconds: seconds)
    for _ in range(9):
        delay = registry.mark_failure("source:data", 0, policy=policy)
        assert delay <= maximum
        assert registry.next_delay("source:data", 0) == delay
    assert delay == maximum
    assert registry.next_delay("source:data", -100000) == maximum
    assert registry.consume_manual_bypass("source:data") is True
    assert registry.consume_manual_bypass("source:data") is False


def test_extended_retry_jitter_remains_bounded_and_invalid_result_is_atomic(monkeypatch):
    policy = RetryPolicy((30, 60, 120, 300, 600, 900))
    monkeypatch.setattr("runtime.scheduler_state.random.uniform", lambda *args: 1.1)
    registry = RetryRegistry()
    assert [registry.mark_failure("source", 0, policy=policy) for _ in range(7)] == [33, 66, 132, 330, 660, 900, 900]
    broken = RetryRegistry(jitter=lambda seconds: seconds if seconds < 900 else 901)
    for _ in range(5):
        broken.mark_failure("source", 0, policy=policy)
    before = broken.snapshot()
    with pytest.raises(ValueError):
        broken.mark_failure("source", 0, policy=policy)
    assert broken.snapshot() == before


@pytest.mark.parametrize("steps", [(), (0,), (True,), (-1,), (float("nan"),), (901,), (60, 30), [30, 60]])
def test_retry_policy_rejects_invalid_delay_sequences(steps):
    with pytest.raises(ValueError):
        RetryPolicy(steps)


def test_degraded_ticketmaster_keeps_original_freshness_while_backing_off(tmp_path):
    from plugins.base_plugin.render_provenance import SourceProvenance

    now = datetime(2026, 9, 10, 13, tzinfo=timezone.utc)
    playlist = _runtime_playlist(_runtime_plugin_data("ticketmaster_events", "Events", interval=10800))
    task, _, _ = _make_runtime_task(tmp_path, playlists=[playlist])
    task.retry_registry = RetryRegistry(jitter=lambda seconds: seconds)
    instance = playlist.plugins[0].snapshot()
    succeeded = (now - timedelta(hours=3)).isoformat()
    task.runtime_state.record_success(instance.instance_uuid, succeeded, lane=RefreshLane.DATA)
    command = task._playlist_command(
        playlist.name,
        instance,
        source=CommandSource.BACKGROUND,
        intent=RefreshIntent.DATA_REFRESH,
        kind=CommandKind.CACHE_REFRESH,
    )
    for _ in range(6):
        task._record_degraded_data_result(command, SourceProvenance.STALE_CACHE, now)
    state = task.runtime_state.snapshot().instances[instance.instance_uuid].data
    assert state.last_success_at == succeeded
    assert datetime.fromisoformat(state.next_retry_at) == now + timedelta(minutes=15)
    assert task._snapshot_retry_delayed(instance, now + timedelta(minutes=14)) is True
    assert task._snapshot_retry_delayed(instance, now + timedelta(minutes=15)) is False
