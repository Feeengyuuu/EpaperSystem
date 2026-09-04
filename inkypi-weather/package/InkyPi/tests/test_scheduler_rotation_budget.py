"""Short data work fits before rotation; heavy work waits until after it."""

from dataclasses import replace
from datetime import timedelta
import os
from pathlib import Path

import pytest
from PIL import Image

from tests.test_refresh_completion_admission import (
    JobStatus, RefreshIntent, _completion_case,
)
from tests.test_refresh_task import (
    NoChangePresentationPlugin, RefreshLane, _write_runtime_cache,
)


@pytest.mark.parametrize("heavy_intent", [RefreshIntent.LIVE_REFRESH, RefreshIntent.DATA_REFRESH])
def test_unreserved_rotation_runs_short_data_then_displays_before_heavy_refresh(
    tmp_path, monkeypatch, heavy_intent,
):
    case = _completion_case(tmp_path, monkeypatch, first_plugin="sports_dashboard")
    case.clock.advance(285)
    original_get_plugin = case.config.get_plugin
    sports_manifest = original_get_plugin("sports_dashboard")["_manifest"]
    sports_manifest = replace(
        sports_manifest,
        capabilities=replace(sports_manifest.capabilities, supports_live_refresh=True),
    )
    case.config.config["display_triggered_refresh_enabled"] = True
    case.config.get_plugin = lambda key: (
        {"id": key, "_manifest": sports_manifest}
        if key == "sports_dashboard" else original_get_plugin(key)
    )

    class SportsLive(NoChangePresentationPlugin):
        def get_live_refresh_state(self, settings, current_dt):
            return {"active": heavy_intent is RefreshIntent.LIVE_REFRESH, "interval_seconds": 300}

        def wants_background_live_refresh(self, settings, current_dt):
            return True

    monkeypatch.setattr("refresh_task.get_plugin_instance", lambda _config: SportsLive())
    for plugin in case.playlist.plugins:
        instance = plugin.snapshot()
        cache_path = _write_runtime_cache(case.task, instance)
        os.utime(cache_path, (case.clock.wall_time(), case.clock.wall_time()))
        is_due = instance.plugin_id == "bambu_monitor" or heavy_intent is RefreshIntent.DATA_REFRESH
        age_seconds = 300 if is_due else 0
        if (
            instance.plugin_id == "sports_dashboard"
            and heavy_intent is RefreshIntent.LIVE_REFRESH
        ):
            # The test needs an already-due offscreen Sports LIVE candidate;
            # its production floor is intentionally longer than DATA cadence.
            age_seconds = 900
        succeeded_at = case.now() - timedelta(seconds=age_seconds)
        case.task.runtime_state.record_success(instance.instance_uuid, succeeded_at.isoformat(), lane=RefreshLane.DATA)

    renders = []
    writes = []

    def render(command, resolved, context):
        began = case.clock.monotonic()
        case.clock.advance(55 if command.plugin_id == "sports_dashboard" else 5)
        context.raise_if_cancelled()
        case.task._set_render_metadata(True, True, case.config.get_plugin(command.plugin_id))
        renders.append((command.plugin_id, command.intent, began, case.clock.monotonic()))
        return Image.new("RGB", (32, 16), "white")

    original_display = case.task.display_manager.display_image

    def display(image, image_settings=None):
        writes.append(case.clock.monotonic())
        case.clock.advance(60)
        return original_display(image, image_settings)

    monkeypatch.setattr(case.task, "_render_playlist_command", render)
    monkeypatch.setattr(case.task.display_manager, "display_image", display)
    manager = case.config.get_playlist_manager()
    assert case.task._get_rotation_wait_seconds() == 15
    assert not any(manager.validate_rotation_reservation(
        plugin.instance_uuid, expected_playlist_name=case.playlist.name,
    ) for plugin in case.playlist.plugins)

    first = case.task._run_one_iteration_for_test()

    assert first is not None and first.command.plugin_id == "bambu_monitor"
    assert first.command.intent is RefreshIntent.DATA_REFRESH
    assert case.task.refresh_queue.get_job(first.job.id).status is JobStatus.SUCCEEDED
    assert renders == [("bambu_monitor", RefreshIntent.DATA_REFRESH, 285.0, 290.0)]

    for _ in range(8):
        for cache_file in Path(case.config.plugin_image_dir).rglob("*.png"):
            os.utime(cache_file, (case.clock.wall_time(), case.clock.wall_time()))
        began = case.clock.monotonic()
        entry = case.task._run_one_iteration_for_test()
        if entry is not None and entry.command.plugin_id == "sports_dashboard" and entry.command.intent is heavy_intent:
            assert case.task.refresh_queue.get_job(entry.job.id).status is JobStatus.SUCCEEDED
            break
        if case.clock.monotonic() == began:
            next_attempt = case.task.scheduler_snapshot().next_attempt_monotonic
            assert next_attempt is not None and next_attempt > began
            case.clock.advance(next_attempt - began)

    assert writes == [300.0], "the pending heavy render must not push back the original rotation deadline"
    assert renders == [
        ("bambu_monitor", RefreshIntent.DATA_REFRESH, 285.0, 290.0),
        ("sports_dashboard", heavy_intent, 360.0, 415.0),
    ]
