import json
import re
from pathlib import Path


from runtime.execution_policy import ExecutionClass, plugin_execution_class


PLUGIN_ROOT = Path(__file__).resolve().parents[1] / "src" / "plugins"


def _registered_plugin_ids():
    return {
        json.loads(path.read_text(encoding="utf-8"))["id"]
        for path in PLUGIN_ROOT.rglob("plugin-info.json")
    }


def test_every_registered_plugin_has_an_explicit_execution_class():
    classified = {
        plugin_id
        for plugin_id in _registered_plugin_ids()
        if plugin_execution_class(plugin_id) is not ExecutionClass.INLINE_UNKNOWN
    }

    assert classified == _registered_plugin_ids()


def test_unknown_plugins_fail_closed_to_inline_serial_execution():
    assert plugin_execution_class("future_unreviewed_plugin") is ExecutionClass.INLINE_UNKNOWN


def test_heavy_and_nested_plugins_cannot_enter_parallel_image_lane():
    for plugin_id in (
        "weather",
        "sports_dashboard",
        "newspaper",
        "ai_image",
        "daily_ai_news",
        "steam_charts",
    ):
        assert plugin_execution_class(plugin_id) is not ExecutionClass.PARALLEL_IMAGE


def test_simple_calendar_stays_inline_after_parallel_stage_cost_review():
    assert plugin_execution_class("simple_calendar") is ExecutionClass.INLINE


def test_species_radar_stays_inline_to_preserve_optional_media_isolation():
    assert plugin_execution_class("species_radar") is ExecutionClass.INLINE


def test_nested_provider_plugins_do_not_create_more_than_four_local_workers():
    daily_ai_news = (
        PLUGIN_ROOT / "daily_ai_news" / "daily_ai_news.py"
    ).read_text(encoding="utf-8")
    steam_charts = (
        PLUGIN_ROOT / "steam_charts" / "steam_charts.py"
    ).read_text(encoding="utf-8")

    assert "MAX_PROVIDER_IO_WORKERS = 4" in daily_ai_news
    assert re.search(r"min\(\s*MAX_PROVIDER_IO_WORKERS\s*,", daily_ai_news)
    assert "MAX_PROVIDER_IO_WORKERS = 4" in steam_charts
    assert "ThreadPoolExecutor(max_workers=MAX_PROVIDER_IO_WORKERS)" in steam_charts


def test_direct_transport_plugins_use_the_central_provider_capacity():
    apod = (PLUGIN_ROOT / "apod" / "apod.py").read_text(encoding="utf-8")
    telegram = (
        PLUGIN_ROOT / "telegram_digest" / "telegram_digest.py"
    ).read_text(encoding="utf-8")

    assert "with provider_io_lease(" in apod
    assert telegram.count("with provider_io_lease(") >= 3
