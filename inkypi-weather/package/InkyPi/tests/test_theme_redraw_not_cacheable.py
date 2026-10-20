"""A theme-only redraw that cannot be cached must back off its THEME lane.

2026-10-17 19:00:06-19:03:15 PDT: the displayed bambu_monitor rendered a
non-cacheable theme-only image (status snapshot older than cacheSeconds). The
THEME_REDRAW ended as "succeeded" with nothing recorded, so the scheduler chose
it again after every completion: 89 renders about 2 s apart until a DATA refresh
promoted the night cache.
"""

from datetime import datetime, timedelta, timezone

from tests.test_refresh_task import (
    JobStatus,
    RefreshIntent,
    ResourceSample,
    ThemeOnlyRecordingPlugin,
    _prepare_independent_theme_candidate,
    _theme_transition_runtime,
    refresh_task_module,
)

NOW = datetime(2026, 7, 11, 22, 0, tzinfo=timezone.utc)


class NoncacheableThemePlugin(ThemeOnlyRecordingPlugin):
    def render_themed_image(self, *args, **kwargs):
        image = super().render_themed_image(*args, **kwargs)
        image.info[refresh_task_module.SKIP_CACHE_IMAGE_INFO_KEY] = True
        return image


def _noncacheable_redraw(name, monkeypatch):
    task, device_config, playlist, configs = _theme_transition_runtime(name)
    instance = _prepare_independent_theme_candidate(task, playlist, NOW)
    plugin = NoncacheableThemePlugin(configs["displayed"])
    monkeypatch.setattr("refresh_task.get_plugin_instance", lambda _config: plugin)
    monkeypatch.setattr(
        task,
        "_resource_sample",
        lambda: ResourceSample(available_mb=512, swap_percent=0),
    )
    monkeypatch.setattr(task, "_get_current_datetime", lambda: NOW)
    # The device leaves display-triggered refresh at its default (off).
    device_config.config["display_triggered_refresh_enabled"] = False
    command = task._select_independent_refresh_command(NOW)
    assert command.intent is RefreshIntent.THEME_REDRAW
    assert command.instance_uuid == instance.instance_uuid
    submitted = task.refresh_queue.submit(command)
    task._process_queue_entry(task.refresh_queue.take(timeout=0))
    return task, device_config, instance, plugin, submitted


def test_noncacheable_theme_redraw_fails_into_a_theme_lane_backoff(monkeypatch):
    task, device_config, instance, plugin, submitted = _noncacheable_redraw(
        "theme-redraw-noncacheable-backoff",
        monkeypatch,
    )
    state = task.runtime_state.snapshot().instances[instance.instance_uuid]

    assert task.refresh_queue.get_entry(submitted.id).job.status is JobStatus.FAILED
    assert state.theme.last_failure_at == NOW.isoformat()
    assert state.theme.next_retry_at is not None
    assert state.last_good_cache.theme_mode == "day"
    # Only the instance lane backs off: the transition itself stays pending so
    # the next exact-theme rotation display can still persist it.
    assert device_config.config["active_theme"] == "day"
    assert task._get_config_value("active_theme_refresh_failure", None) is None
    theme_context = refresh_task_module.get_theme_context(device_config, now=NOW)
    assert task._has_theme_changed(theme_context, NOW) is True
    assert len(plugin.calls) == 1


def test_noncacheable_theme_redraw_is_not_reselected_until_its_backoff_ends(
    monkeypatch,
):
    task, _device_config, instance, plugin, _submitted = _noncacheable_redraw(
        "theme-redraw-noncacheable-no-loop",
        monkeypatch,
    )
    retry_at = datetime.fromisoformat(
        task.runtime_state.snapshot().instances[instance.instance_uuid].theme.next_retry_at
    )

    for seconds in (2, 4, 10):
        again = task._select_independent_refresh_command(NOW + timedelta(seconds=seconds))
        assert again is None or again.intent is not RefreshIntent.THEME_REDRAW
    assert len(plugin.calls) == 1

    retry = task._select_independent_refresh_command(retry_at + timedelta(seconds=1))
    assert retry is not None
    assert retry.intent is RefreshIntent.THEME_REDRAW
    assert retry.instance_uuid == instance.instance_uuid
