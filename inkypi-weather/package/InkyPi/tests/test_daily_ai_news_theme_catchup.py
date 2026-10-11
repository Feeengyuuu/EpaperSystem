"""Scheduler acceptance for Daily AI News theme catch-up after local midnight."""

from PIL import Image

from plugins.daily_ai_news.daily_ai_news import DailyAINews
import refresh_task as refresh_task_module
from runtime.refresh_contracts import JobStatus, RefreshIntent
from runtime.refresh_policy import ResourceSample
from runtime.runtime_state import InstanceRuntimeState
from tests.test_daily_ai_news import (
    _forbid_daily_ai_news_side_effects,
    _freeze_daily_ai_news_clock,
    _seed_scheduled_daily_brief,
)
from tests.test_refresh_task import (  # noqa: F401 - shared scratch root cleanup
    TEST_STATE_ROOT,
    _prepare_theme_catchup_runtime,
    _queue_and_process,
    _write_runtime_theme_cache,
)


def test_midnight_theme_catchup_promotes_the_exact_night_cache(monkeypatch, tmp_path):
    task, device_config, playlist, configs, current_dt = (
        _prepare_theme_catchup_runtime("daily-ai-news-midnight-catchup")
    )
    device_config.get_resolution = lambda: (800, 480)
    monkeypatch.setattr(
        task,
        "_resource_sample",
        lambda: ResourceSample(available_mb=512, swap_percent=0),
    )
    monkeypatch.setattr(task, "_cache_refresh_under_resource_pressure", lambda: False)
    monkeypatch.setattr(task, "_get_current_datetime", lambda: current_dt)
    displayed = playlist.plugins[0].snapshot()
    target = playlist.plugins[1].snapshot()
    _write_runtime_theme_cache(task, displayed, "night")

    plugin = DailyAINews(configs["fallback"])
    monkeypatch.setattr(plugin, "_cache_dir", lambda create=True: tmp_path)
    moment = _freeze_daily_ai_news_clock(monkeypatch)
    _seed_scheduled_daily_brief(monkeypatch, plugin, moment, dict(target.settings))
    _forbid_daily_ai_news_side_effects(monkeypatch, plugin)
    moment["value"] = (2026, 10, 9, 1, 43)
    monkeypatch.setattr(
        refresh_task_module,
        "get_plugin_instance",
        lambda _config: plugin,
    )

    command = task._select_independent_refresh_command(current_dt)
    assert command.intent is RefreshIntent.THEME_CATCHUP
    assert command.instance_uuid == target.instance_uuid
    assert command.payload["resolved_theme_context"]["mode"] == "night"

    result = _queue_and_process(task, command)

    assert result.job.status is JobStatus.SUCCEEDED
    runtime_instance = task.runtime_state.snapshot().instances.get(
        target.instance_uuid,
        InstanceRuntimeState(),
    )
    # Rotation only selects exact current-theme caches through this resolver.
    exact = task.cache_catalog.resolve_exact(target, "night", runtime_instance)
    assert exact is not None
    with Image.open(exact.cache_path) as cached:
        assert cached.size == (800, 480)
