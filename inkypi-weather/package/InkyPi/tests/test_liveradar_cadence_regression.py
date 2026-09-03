"""Small deterministic cadence probe using the production scheduler and queue.

Only provider/panel durations and host resource measurements are synthetic.
There is no network, real sleep, or physical hardware access.
"""

from collections import Counter
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
import os
import random
import sys
import uuid

from PIL import Image

from tests.test_freshness_virtual_day import CADENCES
from tests.test_refresh_task import (
    JobStatus,
    CommandKind,
    NoChangePresentationPlugin,
    PresentationBankPlugin,
    RefreshIntent,
    RefreshLane,
    RefreshTask,
    ResourceSample,
    RuntimeClock,
    RuntimeDeviceConfig,
    _runtime_playlist,
    _runtime_plugin_data,
    _presentation_manifest,
    _theme_manifest,
    _write_runtime_cache,
)


def _run_cadence_scenario(
    tmp_path, monkeypatch, *, setup=None, horizon_seconds=7200,
    actual_schedules=False, sports_live_seconds=0,
):
    start = datetime(2026, 9, 3, 12, tzinfo=timezone.utc)
    clock = RuntimeClock(wall=start.timestamp())
    now = lambda: datetime.fromtimestamp(clock.wall_time(), timezone.utc)
    provider_seconds = 5
    panel_seconds = 60
    writes = []
    renders = []
    receipt_ids = set()

    class VirtualDisplay:
        def bind_runtime_state(self, store):
            self.store = store
            return object()

        def display_image(self, image, image_settings=(), *, task_context=None,
                          logical_target=None, **kwargs):
            began = clock.monotonic()
            clock.advance(panel_seconds)
            task_context.raise_if_cancelled()
            commit = uuid.uuid4().hex
            self.store.set_display_state(
                "committed", commit, instance_uuid=logical_target["instance_uuid"],
                changed_at=now().isoformat(),
            )
            writes.append(began)
            return SimpleNamespace(commit_id=commit, committed_at=now().isoformat(), hardware_written=True)

    cadences = {
        key: value if actual_schedules or not isinstance(value, str) else 3600
        for key, value in CADENCES.items()
    }
    cadences["live_radar"] = 300
    cadences["simple_calendar"] = 3600
    entries = []
    for index, (key, cadence) in enumerate(cadences.items()):
        entry = _runtime_plugin_data(key, key, latest_refresh_time=None, interval=300)
        entry["refresh"] = {"scheduled": cadence} if isinstance(cadence, str) else {"interval": cadence}
        entry["instance_uuid"] = f"{index:032x}"
        entries.append(entry)
    playlist = _runtime_playlist(*entries)
    monkeypatch.setattr(sys.modules[type(playlist).__module__], "random", random.Random(0))
    config = RuntimeDeviceConfig(tmp_path, [playlist])
    config.config.update({
        "theme_mode": "day", "active_theme": "day", "plugin_cycle_interval_seconds": 300,
        "display_triggered_refresh_enabled": False,
    })
    config.refresh_info.refresh_time = start.isoformat()
    manifests = {key: _theme_manifest(key, supported=False) for key in cadences}
    config.get_plugin = lambda key: {"id": key, "_manifest": manifests[key]}
    task = RefreshTask(
        config, VirtualDisplay(), clock=clock.monotonic, wall_clock=clock.wall_time,
        disk_usage=lambda _path: SimpleNamespace(total=10**10, used=10**9, free=9*10**9),
    )
    monkeypatch.setattr(task, "_get_current_datetime", now)
    monkeypatch.setattr(task, "_memory_watchdog_should_restart", lambda: False)
    monkeypatch.setattr(task, "_run_memory_maintenance", lambda *_a, **_k: None)
    monkeypatch.setattr(task, "_run_cache_lifecycle_maintenance", lambda *_a, **_k: False)
    monkeypatch.setattr(task, "_resource_sample", lambda: ResourceSample(512, 0))
    if setup is not None:
        setup(task, monkeypatch, clock)

    def render(command, resolved, context):
        began = clock.monotonic()
        clock.advance(sports_live_seconds if command.intent is RefreshIntent.LIVE_REFRESH else provider_seconds)
        context.raise_if_cancelled()
        renders.append((command.plugin_id, began, command.intent))
        task._set_render_metadata(True, True, config.get_plugin(command.plugin_id))
        return Image.new("RGB", (32, 16), "white")

    monkeypatch.setattr(task, "_render_playlist_command", render)
    for plugin in playlist.plugins:
        instance = plugin.snapshot()
        path = _write_runtime_cache(task, instance)
        os.utime(path, (start.timestamp(), start.timestamp()))
        task.runtime_state.record_success(
            instance.instance_uuid,
            (start - timedelta(seconds=(
                86400 if isinstance(cadences[plugin.plugin_id], str) else cadences[plugin.plugin_id]
            ))).isoformat(),
            lane=RefreshLane.DATA,
        )

    while clock.monotonic() < horizon_seconds:
        began = clock.monotonic()
        entry = task._run_one_iteration_for_test()
        if entry is not None and entry.command.intent is RefreshIntent.DATA_REFRESH:
            assert task.refresh_queue.get_job(entry.job.id).status is JobStatus.SUCCEEDED
            cache_path = Path(task._snapshot_cache_path(entry.command, None))
            os.utime(cache_path, (clock.wall_time(), clock.wall_time()))
        if entry is not None and entry.command.kind is CommandKind.DISPLAY:
            state = task.runtime_state.snapshot().instances.get(entry.command.instance_uuid)
            if state is not None and state.presentation_receipt is not None:
                receipt_ids.add(state.presentation_receipt.request_id)
        if clock.monotonic() == began:
            deadlines = [task.scheduler_state.snapshot().next_attempt_monotonic, task._ian_retry_not_before, horizon_seconds]
            future = [value for value in deadlines if value is not None and value > began]
            clock.advance(min(future) - began if future else 1)

    data = [(key, began) for key, began, intent in renders if intent is RefreshIntent.DATA_REFRESH]
    radar = [began for key, began in data if key == "live_radar"]
    gaps = [end - begin for begin, end in zip(radar, radar[1:])]
    write_gaps = [end - begin for begin, end in zip(writes, writes[1:])]
    utilization = sum(
        provider_seconds / (86400 if isinstance(cadence, str) else cadence)
        for cadence in cadences.values()
    ) + (panel_seconds + sports_live_seconds) / 300
    result = {
        "synthetic_utilization": utilization,
        "radar_starts": radar,
        "radar_max_gap": max(gaps),
        "data_counts": dict(Counter(key for key, _ in data)),
        "short_cadence_starts": {
            key: [began for name, began in data if name == key]
            for key, cadence in cadences.items() if cadence == 300
        },
        "render_starts": data,
        "write_starts": writes,
        "write_gap_seconds": [min(write_gaps), max(write_gaps)],
        "probe_metrics": getattr(task, "_cadence_probe_metrics", {}),
        "prepared_commit_count": len(receipt_ids),
        "sports_live_count": sum(intent is RefreshIntent.LIVE_REFRESH for _, _, intent in renders),
    }
    assert utilization < (0.7 if sports_live_seconds else 0.5)
    assert len(radar) >= 2
    assert set(key for key, _ in data) == set(cadences)
    assert min(write_gaps) >= 300
    assert max(write_gaps) <= 365
    assert task._restart_request is None
    return result


def _with_prepared_and_background_sports(task, monkeypatch, clock):
    config = task.device_config
    original_get_plugin = config.get_plugin
    prepared_ids = {"backtothedate", "gcd_comic_covers", "magazine_covers"}
    config.config["display_triggered_refresh_enabled"] = True
    manifests = {key: _presentation_manifest(key, provider_free=True) for key in prepared_ids}
    sports_manifest = original_get_plugin("sports_dashboard")["_manifest"]
    sports_manifest = replace(
        sports_manifest,
        capabilities=replace(sports_manifest.capabilities, supports_live_refresh=True),
    )
    metrics = {"preparations": 0, "prepared_guard_blocks": 0}
    task._cadence_probe_metrics = metrics

    class PreparedBank(PresentationBankPlugin):
        def prepare_presentation(self, *args, **kwargs):
            clock.advance(5)
            metrics["preparations"] += 1
            return super().prepare_presentation(*args, **kwargs)

    class SportsStream(NoChangePresentationPlugin):
        def get_live_refresh_state(self, settings, current_dt):
            return {"active": True, "interval_seconds": 300}

        def wants_background_live_refresh(self, settings, current_dt):
            return True

    plugins = {key: PreparedBank() for key in prepared_ids}
    plugins["sports_dashboard"] = SportsStream()

    def get_plugin(key):
        if key in prepared_ids:
            return {"id": key, "refresh_on_display": True, "_manifest": manifests[key]}
        if key == "sports_dashboard":
            return {"id": key, "_manifest": sports_manifest}
        return original_get_plugin(key)

    monkeypatch.setattr(config, "get_plugin", get_plugin)
    monkeypatch.setattr("src.refresh_task.get_plugin_instance", lambda item: plugins[item["id"]])
    original_select = task._select_independent_refresh_command

    def select(*args, **kwargs):
        candidate = original_select(*args, **kwargs)
        if candidate is None and task._due_counts["data"] and task._get_rotation_wait_seconds() <= 120:
            manager = config.get_playlist_manager()
            active = manager.snapshot_active_playlist(args[0])
            for key, state in task.runtime_state.snapshot().instances.items():
                request = state.presentation_request
                if (request is not None and request.prepared_at is not None and active is not None
                    and manager.validate_rotation_reservation(key, expected_playlist_name=active.name)):
                    metrics["prepared_guard_blocks"] += 1
                    break
        return candidate

    monkeypatch.setattr(task, "_select_independent_refresh_command", select)


def test_liveradar_cadence_with_a_feasible_mixed_workload(tmp_path, monkeypatch):
    result = _run_cadence_scenario(tmp_path, monkeypatch)
    print({key: value for key, value in result.items() if key not in {"render_starts", "write_starts"}})
    # Under this bounded workload, allow one non-preemptible provider, a full
    # panel write, and one scheduler poll beyond the requested data interval.
    assert result["radar_max_gap"] <= 300 + 5 + 60 + 30


def test_liveradar_cadence_preserves_prepared_commits_and_background_sports(tmp_path, monkeypatch):
    result = _run_cadence_scenario(
        tmp_path, monkeypatch, setup=_with_prepared_and_background_sports,
        actual_schedules=True, sports_live_seconds=55,
    )
    print({key: value for key, value in result.items() if key not in {"render_starts", "write_starts"}})
    assert result["probe_metrics"]["preparations"] >= 3
    assert result["probe_metrics"]["prepared_guard_blocks"] > 0
    assert result["prepared_commit_count"] >= 3
    assert result["sports_live_count"] >= 18
    # Preserve the prior release's worst physical cadence; allowing 365s here
    # would hide a long LIVE job crossing an unreserved rotation deadline.
    assert result["write_gap_seconds"][1] <= 310
    # Keep the actual 120s guard. Allow its delay plus one panel write, one
    # non-preemptible Sports request, the own provider, and the normal poll.
    assert result["radar_max_gap"] <= 300 + 120 + 60 + 55 + 5 + 30


def test_current_28_refresh_schedules_recover_through_a_virtual_day(tmp_path, monkeypatch):
    from tests import test_freshness_virtual_day as virtual_day

    # Reuse the established production-boundary simulation, preserving actual
    # daily schedules, six first-success failures, backoff, and HARD/SOFT gates.
    monkeypatch.setitem(virtual_day.CADENCES, "live_radar", 300)
    monkeypatch.setitem(virtual_day.CADENCES, "simple_calendar", 3600)
    virtual_day.test_actual_28_cadences_complete_a_virtual_day(tmp_path, monkeypatch, pressure=True)
