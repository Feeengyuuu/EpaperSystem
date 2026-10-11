"""Display-safe but unhealthy DATA follows each instance's saved cadence."""

import hashlib
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from PIL import Image

from plugins.base_plugin.render_provenance import SourceProvenance, attach_source_provenance
from runtime.refresh_contracts import CommandKind, CommandSource, RefreshIntent
from runtime.scheduler_state import RetryRegistry
from tests.test_refresh_task import _make_runtime_task, _runtime_playlist, _runtime_plugin_data

NOW = datetime(2026, 10, 8, 7, 2, tzinfo=timezone.utc)
LAST_SUCCESS = "2026-10-08T07:02:19+00:00"


def _degraded_runtime(tmp_path, plugin_id="simple_calendar", refresh=None, **config):
    data = _runtime_plugin_data(plugin_id, plugin_id, latest_refresh_time=LAST_SUCCESS)
    data["refresh"] = dict(refresh or {"interval": 3600})
    playlist = _runtime_playlist(data)
    task, device_config, clock = _make_runtime_task(
        tmp_path, playlists=[playlist], retry_registry=RetryRegistry(jitter=lambda delay: delay),
    )
    device_config.config.update(config)
    instance = playlist.plugins[0].snapshot()
    command = task._playlist_command(
        playlist.name, instance, source=CommandSource.BACKGROUND, intent=RefreshIntent.DATA_REFRESH,
        display_cached_only=False, kind=CommandKind.CACHE_REFRESH, current_dt=NOW,
    )
    return task, instance, command


def _retry_seconds(task, instance, attempted_at):
    state = task.runtime_state.snapshot().instances[instance.instance_uuid].data
    return (datetime.fromisoformat(state.next_retry_at) - attempted_at).total_seconds()


def _commit(task, command, provenance, attempted_at):
    image = attach_source_provenance(Image.new("RGB", (32, 16), "white"), provenance)
    task._set_render_metadata(True, True, {})
    task._commit_command_result(command, task._resolve_playlist_command(command), image, attempted_at)


def test_hourly_calendar_degraded_replay_retries_at_saved_cadence_not_every_five_minutes(tmp_path):
    # 10-08..10-10: simple_calendar returned stale_cache for 51 hours and was
    # retried 468 times on the generic 30..300 s failure ladder.
    task, instance, command = _degraded_runtime(tmp_path)
    attempted_at, attempts = NOW, 0
    while attempted_at < NOW + timedelta(hours=51):
        task._record_degraded_data_result(command, SourceProvenance.STALE_CACHE, attempted_at)
        delay = _retry_seconds(task, instance, attempted_at)
        assert 1800 <= delay <= 3600
        attempts += 1
        attempted_at += timedelta(seconds=delay)
    assert attempts <= 51 * 3600 // 1800 + 1


@pytest.mark.parametrize(
    ("plugin_id", "refresh", "config", "expected"),
    [
        # A short saved cadence caps the floor; the plugin is never slower than configured.
        ("bambu_monitor", {"interval": 300}, {}, 300),
        ("live_radar", {"interval": 60}, {"degraded_data_retry_seconds": 3600}, 60),
        # The shared key tunes every plugin.
        ("simple_calendar", {"interval": 3600}, {"degraded_data_retry_seconds": 600}, 600),
        ("simple_calendar", {"interval": 3600}, {"degraded_data_retry_seconds": 0}, 30),
        # Daily scheduled refreshes have no numeric interval: configured floor only.
        ("daily_ai_news", {"scheduled": "07:30"}, {}, 1800),
        ("daily_wiki_page", {"scheduled": "00:15"}, {"degraded_data_retry_seconds": 10 * 86400}, 86400),
        # Invalid shared values fall back to the 30-minute default.
        ("simple_calendar", {"interval": 10800}, {"degraded_data_retry_seconds": True}, 1800),
        ("simple_calendar", {"interval": 10800}, {"degraded_data_retry_seconds": -5}, 1800),
        ("simple_calendar", {"interval": 10800}, {"degraded_data_retry_seconds": float("nan")}, 1800),
        ("simple_calendar", {"interval": 10800}, {"degraded_data_retry_seconds": "soon"}, 1800),
        # The legacy per-plugin key still tunes Vehicle Status and wins over the shared key.
        ("vehicle_status", {"interval": 10800}, {"vehicle_status_degraded_retry_seconds": 900}, 900),
        (
            "vehicle_status", {"interval": 10800},
            {"vehicle_status_degraded_retry_seconds": 900, "degraded_data_retry_seconds": 2400}, 900,
        ),
        ("vehicle_status", {"interval": 10800}, {"degraded_data_retry_seconds": 2400}, 2400),
        (
            "vehicle_status", {"interval": 10800},
            {"vehicle_status_degraded_retry_seconds": "bad", "degraded_data_retry_seconds": 2400}, 2400,
        ),
    ],
)
def test_degraded_retry_floor_is_generic_and_bounded_by_saved_interval(tmp_path, plugin_id, refresh, config, expected):
    task, instance, command = _degraded_runtime(tmp_path, plugin_id, refresh, **config)
    task._record_degraded_data_result(command, SourceProvenance.LOCAL_FALLBACK, NOW)
    assert _retry_seconds(task, instance, NOW) == expected


def test_degraded_retry_never_exceeds_a_valid_saved_interval(tmp_path):
    task, instance, command = _degraded_runtime(tmp_path, refresh={"interval": 600})
    for attempt in range(8):
        attempted_at = NOW + timedelta(minutes=10 * attempt)
        task._record_degraded_data_result(command, SourceProvenance.STALE_CACHE, attempted_at)
        assert _retry_seconds(task, instance, attempted_at) == 600


def test_recovered_degraded_instance_leaves_data_progress_stall(tmp_path):
    task, instance, command = _degraded_runtime(tmp_path)
    task.runtime_state.record_success(instance.instance_uuid, LAST_SUCCESS)
    late = NOW + timedelta(hours=3)
    _commit(task, command, SourceProvenance.STALE_CACHE, late)
    assert Path(task._snapshot_cache_path(instance)).is_file()
    assert _retry_seconds(task, instance, late) == 1800
    task._observe_refresh_progress(late + timedelta(seconds=1800))
    assert task.refresh_health_snapshot()["progress"]["data_stalled_count"] == 1

    recovered = late + timedelta(seconds=1800)
    _commit(task, command, SourceProvenance.LIVE, recovered)
    task._observe_refresh_progress(recovered + timedelta(seconds=1))
    progress = task.refresh_health_snapshot()["progress"]
    assert progress["data_stalled_count"] == 0
    assert progress["data_overdue_count"] == 0


def _hash(instance):
    return hashlib.sha256(instance.instance_uuid.encode("utf-8")).hexdigest()[:16]


def _records(caplog, level, text):
    return [
        record for record in caplog.records
        if record.name == "refresh_task" and record.levelno == level and text in record.getMessage()
    ]


def test_degraded_data_warns_once_per_episode_and_logs_recovery(tmp_path, caplog):
    caplog.set_level(logging.INFO, logger="refresh_task")
    task, instance, command = _degraded_runtime(tmp_path)

    for attempt in range(3):
        _commit(task, command, SourceProvenance.STALE_CACHE, NOW + timedelta(minutes=30 * attempt))

    warnings = [record for record in caplog.records if record.levelno == logging.WARNING]
    assert len(warnings) == 1
    message = warnings[0].getMessage()
    assert "Degraded DATA" in message
    assert "plugin_id: simple_calendar" in message
    assert f"instance_uuid_hash: {_hash(instance)}" in message
    assert instance.instance_uuid not in message
    assert "provenance: stale_cache" in message
    assert "retry_seconds: 1800.0" in message
    assert len(_records(caplog, logging.INFO, "Degraded DATA retry floor applied")) == 2
    assert not _records(caplog, logging.INFO, "Degraded DATA recovered")

    _commit(task, command, SourceProvenance.LIVE, NOW + timedelta(hours=2))
    recovered = _records(caplog, logging.INFO, "Degraded DATA recovered")
    assert len(recovered) == 1
    assert f"instance_uuid_hash: {_hash(instance)}" in recovered[0].getMessage()

    _commit(task, command, SourceProvenance.LIVE, NOW + timedelta(hours=3))
    assert len(_records(caplog, logging.INFO, "Degraded DATA recovered")) == 1

    _commit(task, command, SourceProvenance.LOCAL_FALLBACK, NOW + timedelta(hours=4))
    warnings = [record for record in caplog.records if record.levelno == logging.WARNING]
    assert len(warnings) == 2
    assert "provenance: local_fallback" in warnings[1].getMessage()
    assert not [record for record in caplog.records if record.levelno >= logging.ERROR]


def test_recovery_from_a_persisted_degraded_episode_is_logged_after_restart(tmp_path, caplog):
    caplog.set_level(logging.INFO, logger="refresh_task")
    task, instance, command = _degraded_runtime(tmp_path)
    task.runtime_state.record_success(instance.instance_uuid, LAST_SUCCESS)
    task.runtime_state.record_failure(
        instance.instance_uuid, (NOW + timedelta(hours=40)).isoformat(),
        "DATA source is display-safe but unhealthy: stale_cache",
        (NOW + timedelta(hours=40, minutes=30)).isoformat(),
    )

    _commit(task, command, SourceProvenance.LIVE, NOW + timedelta(hours=41))

    assert len(_records(caplog, logging.INFO, "Degraded DATA recovered")) == 1
    assert not [record for record in caplog.records if record.levelno >= logging.WARNING]


def test_ordinary_success_after_a_non_degraded_failure_is_not_called_recovery(tmp_path, caplog):
    caplog.set_level(logging.INFO, logger="refresh_task")
    task, instance, command = _degraded_runtime(tmp_path)
    task.runtime_state.record_failure(
        instance.instance_uuid, NOW.isoformat(), "provider timed out",
        (NOW + timedelta(seconds=30)).isoformat(),
    )

    _commit(task, command, SourceProvenance.LIVE, NOW + timedelta(minutes=1))

    assert not _records(caplog, logging.INFO, "Degraded DATA recovered")
