"""Actual 2026-09-02 playlist cadence, with synthetic provider/panel durations.

No network, real sleep, or hardware. Scheduler, queue, DATA bookkeeping, cache
promotion, rotation reservations and display commit validation are production.
"""
from collections import Counter
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from pathlib import Path
import os
import random
import sys
import uuid

import pytest
from PIL import Image

from tests.test_refresh_task import (
    RuntimeClock, RuntimeDeviceConfig, RefreshTask, ResourceSample, RefreshLane,
    RefreshIntent, JobStatus, _runtime_playlist, _runtime_plugin_data, _write_runtime_cache, _theme_manifest,
)


CADENCES = {
    "live_radar": 120, "apod": 1800, "newspaper": 3600,
    "daily_ai_news": "07:30", "steam_profile_dashboard": 300,
    "simple_calendar": 21600, "stocktracker": "13:10", "steam_daily_art": 3600,
    "bambu_monitor": 300, "backtothedate": "00:00", "daily_word_poem": 300,
    "steam_charts": 3600, "box_office_top_movies": 21600, "weather": 300,
    "sports_dashboard": 900, "gcd_comic_covers": 300, "daily_art": 300,
    "lol_info": 7200, "china_box_office_top_movies": 21600,
    "pixiv_r18_ranking": 21600, "daily_wiki_page": "00:15", "tech_pulse": 1800,
    "telegram_digest": 21600, "ticketmaster_events": 10800,
    "ai_ecosystem_pulse": 3600, "orbital_signal": 3600,
    "vehicle_status": 10800, "magazine_covers": 3600,
}
COLD = {
    "steam_profile_dashboard", "lol_info", "ticketmaster_events",
    "ai_ecosystem_pulse", "orbital_signal", "vehicle_status",
}


@pytest.mark.parametrize("pressure", [False, True])
def test_actual_28_cadences_complete_a_virtual_day(tmp_path, monkeypatch, pressure):
    start = datetime(2026, 9, 2, tzinfo=timezone(timedelta(hours=-7)))
    clock = RuntimeClock(wall=start.timestamp())
    now = lambda: datetime.fromtimestamp(clock.wall_time(), start.tzinfo)
    writes = []
    renders = []
    retry_after = {}

    class VirtualDisplay:
        def bind_runtime_state(self, store):
            self.store = store
            return object()

        def display_image(self, image, image_settings=(), *, task_context=None,
                          logical_target=None, **kwargs):
            began = clock.monotonic()
            clock.advance(60)
            task_context.raise_if_cancelled()
            commit = uuid.uuid4().hex
            self.store.set_display_state(
                "committed", commit, instance_uuid=logical_target["instance_uuid"],
                changed_at=now().isoformat(),
            )
            writes.append(began)
            return SimpleNamespace(commit_id=commit, committed_at=now().isoformat(), hardware_written=True)

    entries = []
    for key, cadence in CADENCES.items():
        entry = _runtime_plugin_data(key, key, latest_refresh_time=None, interval=300)
        entry["instance_uuid"] = uuid.uuid5(uuid.NAMESPACE_URL, key).hex
        entry["refresh"] = {"scheduled": cadence} if isinstance(cadence, str) else {"interval": cadence}
        entries.append(entry)
    playlist = _runtime_playlist(*entries)
    monkeypatch.setattr(sys.modules[type(playlist).__module__], "random", random.Random(0))
    config = RuntimeDeviceConfig(tmp_path, [playlist])
    config.config.update({
        "theme_mode": "day", "active_theme": "day", "plugin_cycle_interval_seconds": 300,
        "display_triggered_refresh_enabled": False,
    })
    config.refresh_info.refresh_time = start.isoformat()
    manifests = {key: _theme_manifest(key, supported=False) for key in CADENCES}
    config.get_plugin = lambda key: {"id": key, "_manifest": manifests[key]}
    task = RefreshTask(config, VirtualDisplay(), clock=clock.monotonic, wall_clock=clock.wall_time,
                       disk_usage=lambda _path: SimpleNamespace(total=10**10, used=10**9, free=9*10**9))
    monkeypatch.setattr(task, "_get_current_datetime", now)
    monkeypatch.setattr(task, "_memory_watchdog_should_restart", lambda: False)
    monkeypatch.setattr(task, "_run_memory_maintenance", lambda *_a, **_k: None)
    monkeypatch.setattr(task, "_run_cache_lifecycle_maintenance", lambda *_a, **_k: False)
    monkeypatch.setattr("src.refresh_task.get_plugin_instance", lambda *_a: pytest.fail("real plugin was loaded"))

    def resource():
        elapsed = clock.monotonic()
        if pressure and elapsed < 600:
            return ResourceSample(40, 85)
        if pressure and elapsed < 1800:
            return ResourceSample(140, 40)
        return ResourceSample(512, 0)

    monkeypatch.setattr(task, "_resource_sample", resource)
    failed_once = set()

    def render(command, resolved, context):
        began = clock.monotonic()
        assert not (pressure and began < 600), "renderer admitted during HARD pressure"
        if command.plugin_id in retry_after:
            assert now() >= retry_after[command.plugin_id], "provider retry started before backoff ended"
        clock.advance(20)
        context.raise_if_cancelled()
        renders.append((command.plugin_id, began, command.intent))
        if pressure and command.plugin_id in COLD and command.plugin_id not in failed_once:
            failed_once.add(command.plugin_id)
            raise RuntimeError("synthetic first provider failure")
        task._set_render_metadata(True, True, config.get_plugin(command.plugin_id))
        return Image.new("RGB", (32, 16), "white")

    monkeypatch.setattr(task, "_render_playlist_command", render)
    for plugin in playlist.plugins:
        instance = plugin.snapshot()
        path = _write_runtime_cache(task, instance)
        # Keep fixture mtimes coherent with virtual commit times.
        os.utime(path, (start.timestamp(), start.timestamp()))
        if plugin.plugin_id not in COLD:
            task.runtime_state.record_success(instance.instance_uuid,
                (start - timedelta(hours=24)).isoformat(), lane=RefreshLane.DATA)

    while clock.monotonic() < 86400:
        began = clock.monotonic()
        entry = task._run_one_iteration_for_test()
        if entry is not None and entry.command.intent is RefreshIntent.DATA_REFRESH:
            status = task.refresh_queue.get_job(entry.job.id).status
            if status is JobStatus.SUCCEEDED:
                cache_path = Path(task._snapshot_cache_path(entry.command, None))
                os.utime(cache_path, (clock.wall_time(), clock.wall_time()))
            elif status is JobStatus.FAILED and entry.command.plugin_id in COLD:
                retry = task.runtime_state.snapshot().instances[entry.command.instance_uuid].data.next_retry_at
                assert retry is not None
                retry_after[entry.command.plugin_id] = datetime.fromisoformat(retry)
        if clock.monotonic() == began:
            deadlines = [task.scheduler_state.snapshot().next_attempt_monotonic,
                         task._ian_retry_not_before, 86400]
            future = [value for value in deadlines if value is not None and value > began]
            clock.advance(min(future) - began if future else 1)

    data = [(key, began) for key, began, intent in renders if intent is RefreshIntent.DATA_REFRESH]
    counts = Counter(key for key, _ in data)
    first = {key: next(began for name, began in data if name == key) for key in COLD}
    states = task.runtime_state.snapshot().instances
    assert set(counts) == set(CADENCES)
    assert max(first.values()) < 3600
    for plugin in playlist.plugins:
        success = datetime.fromisoformat(states[plugin.instance_uuid].data.last_success_at)
        assert success >= start
        cadence = CADENCES[plugin.plugin_id]
        if isinstance(cadence, int):
            assert (now() - success).total_seconds() <= cadence + 3600
        else:
            hour, minute = map(int, cadence.split(":"))
            due = now().replace(hour=hour, minute=minute, second=0, microsecond=0)
            if due > now():
                due -= timedelta(days=1)
            assert success >= due or (now() - due).total_seconds() < 3600
    if pressure:
        assert failed_once == COLD
    assert all(counts[key] >= 2 for key in COLD)
    assert len(writes) >= 240
    gaps = [b - a for a, b in zip(writes, writes[1:])]
    assert min(gaps) >= 300
    assert max(gaps) <= 365
    assert task._restart_request is None
    print({"pressure": pressure, "first_data_seconds": first, "data_counts": dict(counts),
           "writes": len(writes), "write_gap_seconds": [min(gaps), max(gaps)]})
