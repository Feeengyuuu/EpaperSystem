import ast
import copy
import hashlib
import json
import inspect
import logging
import os
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
from pathlib import Path
from types import MappingProxyType, SimpleNamespace
import threading
import time
import uuid

import pytest
from PIL import Image, ImageFont

from plugins.base_plugin.base_plugin import BasePlugin
from plugins.base_plugin.refresh_on_display_presentation import RefreshOnDisplayPresentationMixin
from plugins.base_plugin.render_provenance import (
    SourceProvenance,
    attach_source_provenance,
)
from plugins.base_plugin.presentation import (
    PresentationMode,
    PresentationPreparation,
    PresentationRequestContext,
)
from plugins.base_plugin import presentation as presentation_contract
from plugins.newspaper.newspaper import Newspaper
from plugins import plugin_registry, plugin_settings
from plugins.plugin_settings import PluginSettingError
from src.model import Playlist, PlaylistManager, RefreshInfo
from src.plugins.plugin_manifest import PluginCapabilities, PluginManifest, PluginTheme
from src.refresh_task import ManualRefresh, PlaylistRefresh, RefreshTask
import src.refresh_task as refresh_task_module
from runtime.refresh_contracts import (
    CommandKind,
    CommandSource,
    JobStatus,
    LifecycleState,
    RefreshCommand,
    RefreshIntent,
    TaskCancelled,
    TaskContext,
    TaskDeadlineExceeded,
)
from runtime.refresh_queue import QueueFullError, QueueStoppingError, RefreshQueue
from runtime.resource_deferral import ResourcePressureDeferred
from runtime.plugin_deferral import PluginRefreshDeferred
from runtime.ian import IanResourceSample
from runtime.cache_catalog import authoritative_cache_path
from runtime.cache_lifecycle import DiskPressureTier
from runtime.presentation_cache import (
    PreparedPresentationCandidate,
    PresentationCache,
    prepared_presentation_path,
)
from runtime.refresh_policy import (
    AdmissionState,
    DueCandidate,
    DueReason,
    ResourceSample,
)
from runtime.render_arbiter import RenderArbiter
from runtime.sports_isolated_renderer import SportsIsolatedCheckpointPending
from runtime.runtime_state import (
    LastGoodCacheState,
    PresentationCommitReceipt,
    PresentationRequestState,
    RefreshLane,
)
from runtime.long_task_executor import (
    InstanceIdentity,
    current_instance_identity,
    current_instance_identity_validator,
    current_parallel_image_runner,
    current_task_context,
)
from runtime.bounded_parallel_stage import (
    BoundedParallelStageRunner,
    ImmutableImageWorkset,
)
from runtime.resource_governor import RuntimeResourceGovernor
from runtime.scheduler_state import LifecycleController, RetryRegistry, SchedulerState
from utils.image_utils import compute_image_hash
from utils.theme_utils import EFFECTIVE_THEME_CONTEXT_INFO_KEY


TEST_STATE_ROOT = Path(__file__).resolve().parents[4] / ".tmp" / "refresh_task_tests"
PLUGIN_SOURCE_ROOT = Path(__file__).resolve().parents[1] / "src" / "plugins"

LIVE_PRESENTATION_REFERENCE_ROWS = (
    ("backtothedate", "BacktotheDate", {"scheduled": "00:00"}, {}),
    ("bambu_monitor", "Bambu", {"interval": 300}, {}),
    ("box_office_top_movies", "BoxOfficeTopMovies", {"interval": 21600}, {}),
    ("china_box_office_top_movies", "China Movie Hot", {"interval": 21600}, {}),
    ("daily_ai_news", "Daily AI News", {"scheduled": "07:30"}, {"refreshOnDisplay": True}),
    ("daily_art", "DailyArt", {"interval": 300}, {}),
    ("daily_wiki_page", "DailyWiki", {"scheduled": "00:15"}, {}),
    ("daily_word_poem", "DailyWord", {"interval": 300}, {}),
    ("gcd_comic_covers", "ComicCovers", {"interval": 300}, {}),
    ("live_radar", "LiveRadar", {"interval": 120}, {}),
    ("magazine_covers", "MagazineCovers", {"interval": 300}, {}),
    ("newspaper", "ChinaDaily", {"scheduled": "15:00"}, {"mediaRotationMode": "rotate"}),
    ("pixiv_r18_ranking", "DailyPorn", {"interval": 21600}, {}),
    ("simple_calendar", "Date", {"interval": 21600}, {}),
    ("species_radar", "SpeciesRadar", {"interval": 21600}, {}),
    ("sports_dashboard", "SportsDashboard", {"interval": 900}, {}),
    ("steam_charts", "Steam Charts", {"interval": 3600}, {}),
    ("steam_daily_art", "SteamDailyArt", {"interval": 3600}, {}),
    ("stocktracker", "Money", {"scheduled": "13:10"}, {}),
    ("tech_pulse", "TechPulse", {"interval": 1800}, {}),
    ("weather", "AwesomeWeather", {"interval": 300}, {}),
)

LIVE_CADENCE_DIGEST = "c930d1d19ed71d9579aaa4a7fee086d5d8d5446fd14d7156cd8bc11f72bccbd8"


def _settings_default_refresh_on_display_plugin_ids():
    plugin_ids = set()
    for settings_path in PLUGIN_SOURCE_ROOT.glob("*/settings.html"):
        text = settings_path.read_text(encoding="utf-8")
        if "refreshOnDisplay" not in text:
            continue
        if (
            'value="true"' in text
            or "value='true'" in text
            or 'refreshOnDisplay: "true"' in text
            or "refreshOnDisplay: 'true'" in text
            or ".checked = true" in text
            or "!== 'false'" in text
            or '!== "false"' in text
        ):
            plugin_ids.add(settings_path.parent.name)
    return plugin_ids


def _refresh_on_display_plugin_info_ids():
    plugin_ids = set()
    for info_path in PLUGIN_SOURCE_ROOT.glob("*/plugin-info.json"):
        data = json.loads(info_path.read_text(encoding="utf-8"))
        if data.get("refresh_on_display"):
            plugin_ids.add(info_path.parent.name)
    return plugin_ids


def test_refresh_on_display_settings_defaults_have_runtime_fallback():
    expected_plugin_ids = _settings_default_refresh_on_display_plugin_ids()

    assert expected_plugin_ids <= _refresh_on_display_plugin_info_ids()


def test_non_live_refresh_on_display_plugins_have_background_presentation_lane():
    expected = {
        "dota_profile_dashboard",
        "flight_radar",
        "lol_info",
        "reddit_rule34_hot",
        "telegram_digest",
        "wow_profile_dashboard",
    }

    for plugin_id in expected:
        info = json.loads(
            (PLUGIN_SOURCE_ROOT / plugin_id / "plugin-info.json").read_text(
                encoding="utf-8"
            )
        )
        capabilities = info.get("capabilities") or {}
        assert capabilities.get("supports_live_refresh") is False
        assert info.get("refresh_on_display") is True
        assert capabilities.get("supports_presentation_refresh") is True


def test_static_no_change_plugins_do_not_opt_into_presentation_preflight():
    for plugin_id in ("daily_ai_news", "tech_pulse"):
        info = json.loads(
            (PLUGIN_SOURCE_ROOT / plugin_id / "plugin-info.json").read_text(
                encoding="utf-8"
            )
        )

        capabilities = info.get("capabilities") or {}
        assert capabilities.get("supports_presentation_refresh") is False


@pytest.mark.parametrize(
    ("settings", "plugin_config", "expected"),
    [
        ({}, {"id": "manifest-default", "refresh_on_display": True}, True),
        (
            {"refreshOnDisplay": True},
            {"id": "saved-true", "refresh_on_display": False},
            True,
        ),
        (
            {"refreshOnDisplay": False},
            {"id": "saved-false", "refresh_on_display": True},
            False,
        ),
        (
            {},
            {
                "id": "manifest-object",
                "_manifest": SimpleNamespace(refresh_on_display=True),
            },
            True,
        ),
        ({"mediaRotationMode": "rotate"}, {"id": "newspaper"}, True),
        ({}, {"id": "newspaper"}, True),
        ({"mediaRotationMode": "single"}, {"id": "newspaper"}, False),
        (
            {"mediaRotationMode": "rotate"},
            {"id": "newspaper", "refresh_on_display": False},
            False,
        ),
        (
            {"mediaRotationMode": "rotate", "refreshOnDisplay": False},
            {"id": "newspaper", "refresh_on_display": True},
            False,
        ),
    ],
)
def test_refresh_on_display_for_config_preserves_strict_precedence(
    settings,
    plugin_config,
    expected,
):
    assert (
        plugin_settings.resolve_refresh_on_display_for_config(
            settings,
            plugin_config,
        )
        is expected
    )


def test_refresh_on_display_for_config_rejects_invalid_saved_value():
    with pytest.raises(PluginSettingError, match="refreshOnDisplay"):
        plugin_settings.resolve_refresh_on_display_for_config(
            {"refreshOnDisplay": "sometimes"},
            {"id": "newspaper", "refresh_on_display": True},
        )


def _reference_plugin_config(plugin_id):
    manifest_path = PLUGIN_SOURCE_ROOT / plugin_id / "plugin-info.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    config = {"id": plugin_id}
    if "refresh_on_display" in manifest:
        config["refresh_on_display"] = manifest["refresh_on_display"]
    return config


def _canonical_reference_json(value):
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _resolve_live_reference_reasons(resolver):
    manifest_snapshots = {}
    for plugin_id, _instance_name, _refresh, _settings in LIVE_PRESENTATION_REFERENCE_ROWS:
        manifest_path = PLUGIN_SOURCE_ROOT / plugin_id / "plugin-info.json"
        manifest_bytes = manifest_path.read_bytes()
        manifest_text = manifest_path.read_text(encoding="utf-8")
        manifest_snapshots[manifest_path] = (
            manifest_bytes,
            hashlib.sha256(manifest_text.encode("utf-8")).hexdigest(),
        )

    resolved_reasons = {}

    for plugin_id, _instance_name, _refresh, settings in LIVE_PRESENTATION_REFERENCE_ROWS:
        assert set(settings) <= {"refreshOnDisplay", "mediaRotationMode"}
        plugin_config = _reference_plugin_config(plugin_id)
        plugin_config_copy = copy.deepcopy(plugin_config)
        plugin_config_json = _canonical_reference_json(plugin_config_copy)
        resolved = resolver(settings, plugin_config)
        assert plugin_config == plugin_config_copy, (
            f"resolver mutated plugin config for {plugin_id}"
        )
        assert _canonical_reference_json(plugin_config) == plugin_config_json, (
            f"resolver changed canonical plugin config for {plugin_id}"
        )
        if not resolved:
            continue
        if settings.get("refreshOnDisplay") is True:
            reason = "saved_explicit"
        elif plugin_config.get("refresh_on_display") is True:
            reason = "manifest_default"
        else:
            assert plugin_id == "newspaper"
            assert settings.get("mediaRotationMode") == "rotate"
            reason = "newspaper_media_rotation"
        resolved_reasons[plugin_id] = reason

    for manifest_path, (manifest_bytes, manifest_text_hash) in manifest_snapshots.items():
        assert manifest_path.read_bytes() == manifest_bytes, (
            f"resolver mutated manifest bytes for {manifest_path.parent.name}"
        )
        current_text = manifest_path.read_text(encoding="utf-8")
        assert hashlib.sha256(current_text.encode("utf-8")).hexdigest() == (
            manifest_text_hash
        ), f"resolver mutated manifest text for {manifest_path.parent.name}"

    return resolved_reasons


def test_live_reference_slice_preserves_saved_cadence_digest():
    identities = {
        (plugin_id, instance_name)
        for plugin_id, instance_name, _refresh, _settings in LIVE_PRESENTATION_REFERENCE_ROWS
    }
    assert len(LIVE_PRESENTATION_REFERENCE_ROWS) == len(identities) == 21
    assert sum("scheduled" in row[2] for row in LIVE_PRESENTATION_REFERENCE_ROWS) == 5
    assert sum("interval" in row[2] for row in LIVE_PRESENTATION_REFERENCE_ROWS) == 16
    assert all(
        len(refresh) == 1 and set(refresh) <= {"interval", "scheduled"}
        for _plugin_id, _instance_name, refresh, _settings in LIVE_PRESENTATION_REFERENCE_ROWS
    )
    canonical = json.dumps(
        [
            {
                "plugin": plugin_id,
                "instance": instance_name,
                "interval": refresh.get("interval"),
                "scheduled": refresh.get("scheduled"),
            }
            for plugin_id, instance_name, refresh, _settings in sorted(
                LIVE_PRESENTATION_REFERENCE_ROWS,
                key=lambda row: (row[0], row[1]),
            )
        ],
        ensure_ascii=False,
        separators=(",", ":"),
    )

    assert hashlib.sha256(canonical.encode("utf-8")).hexdigest() == LIVE_CADENCE_DIGEST


def test_live_reference_slice_resolves_exact_effective_triggers_and_reasons():
    manifest_default_ids = {
        "backtothedate",
        "daily_art",
        "daily_wiki_page",
        "gcd_comic_covers",
        "live_radar",
        "magazine_covers",
        "pixiv_r18_ranking",
        "simple_calendar",
        "species_radar",
        "steam_daily_art",
        "stocktracker",
        "tech_pulse",
    }
    expected_reasons = {
        **{plugin_id: "manifest_default" for plugin_id in manifest_default_ids},
        "daily_ai_news": "saved_explicit",
        "newspaper": "newspaper_media_rotation",
    }
    before = json.dumps(LIVE_PRESENTATION_REFERENCE_ROWS, sort_keys=True)
    resolved_reasons = _resolve_live_reference_reasons(
        plugin_settings.resolve_refresh_on_display_for_config
    )

    assert resolved_reasons == expected_reasons
    assert set(resolved_reasons) == manifest_default_ids | {"daily_ai_news", "newspaper"}
    assert len(set(resolved_reasons) - {"newspaper"}) == 13
    assert len(resolved_reasons) == 14
    assert json.dumps(LIVE_PRESENTATION_REFERENCE_ROWS, sort_keys=True) == before


def test_live_reference_mutation_probe_rejects_resolver_config_mutation():
    real_resolver = plugin_settings.resolve_refresh_on_display_for_config

    def mutating_resolver(settings, plugin_config):
        result = real_resolver(settings, plugin_config)
        plugin_config["mutation_probe"] = True
        return result

    with pytest.raises(AssertionError, match="mutated plugin config"):
        _resolve_live_reference_reasons(mutating_resolver)


def test_presentation_capability_lookup_is_metadata_only(monkeypatch):
    manifest = SimpleNamespace(
        capabilities=SimpleNamespace(
            supports_presentation_refresh=True,
            presentation_refresh_is_provider_free=True,
        ),
    )
    monkeypatch.setattr(
        plugin_registry,
        "get_plugin_instance",
        lambda *_args, **_kwargs: pytest.fail("capability lookup instantiated plugin"),
    )
    monkeypatch.setattr(
        plugin_registry.importlib,
        "import_module",
        lambda *_args, **_kwargs: pytest.fail("capability lookup imported plugin"),
    )

    assert plugin_registry.plugin_supports_presentation_refresh(
        {"id": "prepared", "_manifest": manifest}
    ) is True
    assert plugin_registry.plugin_supports_presentation_refresh(
        {"id": "legacy-metadata-free"}
    ) is False
    assert plugin_registry.plugin_presentation_refresh_is_provider_free(
        {"id": "prepared", "_manifest": manifest}
    ) is True
    assert plugin_registry.plugin_presentation_refresh_is_provider_free(
        {"id": "legacy-metadata-free"}
    ) is False
    contradictory = SimpleNamespace(
        capabilities=SimpleNamespace(
            supports_presentation_refresh=False,
            presentation_refresh_is_provider_free=True,
        ),
    )
    assert plugin_registry.plugin_presentation_refresh_is_provider_free(
        {"id": "contradictory", "_manifest": contradictory}
    ) is False


def test_refresh_task_routes_every_plugin_render_through_theme_wrapper():
    source = inspect.getsource(refresh_task_module)

    assert source.count("plugin.generate_image(") == 0
    # Seven original render sites plus the dedicated theme-only UI path.
    assert source.count("plugin.render_themed_image(") == 8


def make_test_dir(name):
    path = TEST_STATE_ROOT / f"{name}-{uuid.uuid4().hex}"
    path.mkdir(parents=True, exist_ok=True)
    return path


class FakeDeviceConfig:
    def __init__(self, plugin_image_dir):
        self.plugin_image_dir = str(plugin_image_dir)
        self.write_count = 0
        self.config = {}

    def get_plugin(self, plugin_id):
        return {"id": plugin_id}

    def get_config(self, key=None, default=None):
        if key is None:
            return self.config
        return self.config.get(key, default)

    def update_value(self, key, value, write=False):
        self.config[key] = value
        if write:
            self.write_config()

    def write_config(self):
        self.write_count += 1


def _theme_manifest(
    plugin_id="themed_plugin",
    *,
    supported=True,
    presentation="ui",
):
    theme = None
    if supported:
        theme = PluginTheme(
            presentation=presentation,
            day=MappingProxyType(
                {"background": "#f7f1e3", "accent": "#9b3424"}
            ),
            night=MappingProxyType(
                {"background": "#101820", "accent": "#f2aa4c"}
            ),
        )
    return PluginManifest(
        schema_version=2,
        id=plugin_id,
        class_name="ThemedPlugin",
        display_name="Themed Plugin",
        refresh_on_display=False,
        capabilities=PluginCapabilities(supports_day_night_theme=supported),
        raw={},
        theme=theme,
    )


class DelegatingThemeWrapper:
    def render_themed_image(
        self,
        settings,
        device_config,
        **_kwargs,
    ):
        return self.generate_image(settings, device_config)


class FakePlugin(DelegatingThemeWrapper):
    REFRESH_ON_DISPLAY_IDS = {
        "backtothedate",
        "live_radar",
        "riot-page",
        "simple-calendar",
        "steam-art",
    }

    def __init__(self, calls, refresh_on_display=False, live_state=None):
        self.calls = calls
        self.refresh_on_display = refresh_on_display
        self.live_state = live_state

    def wants_refresh_on_display(self, settings):
        if callable(self.refresh_on_display):
            return bool(self.refresh_on_display(settings or {}))
        if self.refresh_on_display:
            return True
        settings = settings or {}
        if str(settings.get("mediaRotationMode") or "").lower() == "rotate":
            return True
        return settings.get("id") in self.REFRESH_ON_DISPLAY_IDS

    def get_live_refresh_state(self, settings, current_dt):
        if callable(self.live_state):
            return self.live_state(settings or {}, current_dt)
        return self.live_state

    def generate_image(self, settings, device_config):
        self.calls.append(settings["id"])
        return Image.new("RGB", (1, 1), "white")


def _fake_sports_isolated_renderer(calls):
    def render(**kwargs):
        calls.append(kwargs["settings"]["id"])
        return Image.new("RGB", (1, 1), "white")

    return render


class CapturePlugin(DelegatingThemeWrapper):
    def __init__(self, calls):
        self.calls = calls
        self.config = {}

    def generate_image(self, settings, device_config):
        self.calls.append(dict(settings))
        return Image.new("RGB", (1, 1), "white")


class ThreadedDeviceConfig(FakeDeviceConfig):
    def __init__(self, plugin_image_dir, playlist):
        super().__init__(plugin_image_dir)
        self.playlist_manager = PlaylistManager([playlist])
        self.refresh_info = RefreshInfo(
            refresh_type="Playlist",
            plugin_id="old",
            playlist="DailyDoseOfDay",
            plugin_instance="Old",
            refresh_time="2000-01-01T00:00:00+00:00",
            image_hash="old",
        )

    def get_playlist_manager(self):
        return self.playlist_manager

    def get_refresh_info(self):
        return self.refresh_info


class BlockingDisplayManager:
    def __init__(self):
        self.first_display_started = threading.Event()
        self.release_first_display = threading.Event()
        self.display_count = 0

    def display_image(self, image, image_settings=None):
        self.display_count += 1
        if self.display_count == 1:
            self.first_display_started.set()
            self.release_first_display.wait(timeout=1)


def test_refresh_due_plugin_instances_updates_due_cache_only(monkeypatch):
    calls = []
    tmp_path = make_test_dir("due-cache")
    device_config = FakeDeviceConfig(tmp_path)
    task = RefreshTask(device_config, display_manager=None)
    playlist = Playlist(
        "DailyDoseOfDay",
        "00:00",
        "24:00",
        plugins=[
            {
                "plugin_id": "due",
                "name": "Due Plugin",
                "plugin_settings": {"id": "due"},
                "refresh": {"interval": 300},
                "latest_refresh_time": "2026-05-26T07:00:00+00:00",
            },
            {
                "plugin_id": "fresh",
                "name": "Fresh Plugin",
                "plugin_settings": {"id": "fresh"},
                "refresh": {"interval": 300},
                "latest_refresh_time": "2026-05-26T07:04:00+00:00",
            },
        ],
    )

    fresh_path = tmp_path / "fresh_Fresh_Plugin.png"
    Image.new("RGB", (1, 1), "black").save(fresh_path)

    monkeypatch.setattr(
        "src.refresh_task.get_plugin_instance",
        lambda config: FakePlugin(calls),
    )

    task._refresh_due_plugin_instances(
        playlist,
        datetime(2026, 5, 26, 7, 5, tzinfo=timezone.utc),
    )

    assert calls == ["due"]
    assert (tmp_path / "due_Due_Plugin.png").exists()
    assert playlist.find_plugin("due", "Due Plugin").latest_refresh_time == "2026-05-26T07:05:00+00:00"
    assert playlist.find_plugin("fresh", "Fresh Plugin").latest_refresh_time == "2026-05-26T07:04:00+00:00"
    assert device_config.write_count == 1


def test_refresh_due_plugin_instances_prefers_oldest_due_cache_when_limited(monkeypatch):
    calls = []
    tmp_path = make_test_dir("oldest-due-cache")
    device_config = FakeDeviceConfig(tmp_path)
    task = RefreshTask(device_config, display_manager=None)
    playlist = Playlist(
        "DailyDoseOfDay",
        "00:00",
        "24:00",
        plugins=[
            {
                "plugin_id": "live_radar",
                "name": "LiveRadar",
                "plugin_settings": {"id": "live_radar"},
                "refresh": {"interval": 60},
                "latest_refresh_time": "2026-05-26T07:04:00+00:00",
            },
            {
                "plugin_id": "steam_charts",
                "name": "Steam Charts",
                "plugin_settings": {"id": "steam_charts"},
                "refresh": {"interval": 21600},
                "latest_refresh_time": "2026-05-25T07:00:00+00:00",
            },
        ],
    )
    for plugin_instance in playlist.plugins:
        Image.new("RGB", (1, 1), "black").save(tmp_path / plugin_instance.get_image_path())

    monkeypatch.setattr(
        "src.refresh_task.get_plugin_instance",
        lambda config: FakePlugin(calls),
    )

    task._refresh_due_plugin_instances(
        playlist,
        datetime(2026, 5, 26, 7, 5, tzinfo=timezone.utc),
        max_updates=1,
    )

    assert calls == ["steam_charts"]
    assert playlist.find_plugin("steam_charts", "Steam Charts").latest_refresh_time == "2026-05-26T07:05:00+00:00"
    assert playlist.find_plugin("live_radar", "LiveRadar").latest_refresh_time == "2026-05-26T07:04:00+00:00"
    assert device_config.write_count == 1


def test_playlist_cache_refresh_due_detects_stale_long_interval_plugin(monkeypatch):
    tmp_path = make_test_dir("playlist-cache-due")
    device_config = FakeDeviceConfig(tmp_path)
    task = RefreshTask(device_config, display_manager=None)
    monkeypatch.setattr(
        "src.refresh_task.get_plugin_instance",
        lambda config: FakePlugin([], refresh_on_display=config["id"] == "live_radar"),
    )
    current_dt = datetime(2026, 5, 26, 7, 5, tzinfo=timezone.utc)
    playlist = Playlist(
        "DailyDoseOfDay",
        "00:00",
        "24:00",
        plugins=[
            {
                "plugin_id": "live_radar",
                "name": "LiveRadar",
                "plugin_settings": {"id": "live_radar"},
                "refresh": {"interval": 999999999},
                "latest_refresh_time": current_dt.isoformat(),
            },
            {
                "plugin_id": "steam_charts",
                "name": "Steam Charts",
                "plugin_settings": {"id": "steam_charts"},
                "refresh": {"interval": 21600},
                "latest_refresh_time": "2026-05-25T07:00:00+00:00",
            },
        ],
    )
    for plugin_instance in playlist.plugins:
        Image.new("RGB", (1, 1), "black").save(tmp_path / plugin_instance.get_image_path())

    live_radar = playlist.find_plugin("live_radar", "LiveRadar")
    steam_charts = playlist.find_plugin("steam_charts", "Steam Charts")

    assert task._plugin_instance_cache_refresh_due(live_radar, current_dt) is False
    assert task._plugin_instance_cache_refresh_due(
        live_radar,
        current_dt,
        displayed_plugin_instance=live_radar,
    ) is False
    device_config.config["display_triggered_refresh_enabled"] = True
    assert task._plugin_instance_cache_refresh_due(
        live_radar,
        current_dt,
        displayed_plugin_instance=live_radar,
    ) is True
    assert task._playlist_has_cache_refresh_due(playlist, current_dt) is True

    steam_charts.latest_refresh_time = current_dt.isoformat()
    assert task._playlist_has_cache_refresh_due(playlist, current_dt) is False


def test_refresh_due_plugin_instances_refreshes_missing_image(monkeypatch):
    calls = []
    tmp_path = make_test_dir("missing-cache")
    device_config = FakeDeviceConfig(tmp_path)
    task = RefreshTask(device_config, display_manager=None)
    playlist = Playlist(
        "DailyDoseOfDay",
        "00:00",
        "24:00",
        plugins=[
            {
                "plugin_id": "missing",
                "name": "Missing Plugin",
                "plugin_settings": {"id": "missing"},
                "refresh": {"interval": 3600},
                "latest_refresh_time": "2026-05-26T07:04:00+00:00",
            },
        ],
    )

    monkeypatch.setattr(
        "src.refresh_task.get_plugin_instance",
        lambda config: FakePlugin(calls),
    )

    task._refresh_due_plugin_instances(
        playlist,
        datetime(2026, 5, 26, 7, 5, tzinfo=timezone.utc),
    )

    assert calls == ["missing"]
    assert (tmp_path / "missing_Missing_Plugin.png").exists()
    assert playlist.find_plugin("missing", "Missing Plugin").latest_refresh_time == "2026-05-26T07:05:00+00:00"
    assert device_config.write_count == 1


def test_refresh_due_plugin_instances_updates_live_hook_cache_early(monkeypatch):
    calls = []
    tmp_path = make_test_dir("live-hook-cache")
    device_config = FakeDeviceConfig(tmp_path)
    task = RefreshTask(device_config, display_manager=None)
    live_plugin = FakePlugin(calls, live_state={"active": True, "interval_seconds": 180})
    playlist = Playlist(
        "DailyDoseOfDay",
        "00:00",
        "24:00",
        plugins=[
            {
                "plugin_id": "live_radar",
                "name": "LiveRadar",
                "plugin_settings": {"id": "live_radar"},
                "refresh": {"interval": 60},
                "latest_refresh_time": "2026-05-26T07:00:00+00:00",
            },
            {
                "plugin_id": "live_plugin",
                "name": "LivePlugin",
                "plugin_settings": {"id": "live"},
                "refresh": {"interval": 3600},
                "latest_refresh_time": "2026-05-26T07:00:00+00:00",
            },
        ],
    )
    plugin_instance = playlist.find_plugin("live_plugin", "LivePlugin")
    other_plugin = playlist.find_plugin("live_radar", "LiveRadar")
    Image.new("RGB", (1, 1), "black").save(tmp_path / other_plugin.get_image_path())
    Image.new("RGB", (1, 1), "black").save(tmp_path / plugin_instance.get_image_path())

    monkeypatch.setattr(
        "src.refresh_task.get_plugin_instance",
        lambda config: live_plugin if config["id"] == "live_plugin" else FakePlugin(calls),
    )

    task._refresh_due_plugin_instances(
        playlist,
        datetime(2026, 5, 26, 7, 4, tzinfo=timezone.utc),
        only_plugin_id="live_plugin",
    )

    assert calls == ["live"]
    assert other_plugin.latest_refresh_time == "2026-05-26T07:00:00+00:00"
    assert plugin_instance.latest_refresh_time == "2026-05-26T07:04:00+00:00"
    assert device_config.write_count == 1


def test_refresh_due_plugin_instances_skips_sports_dashboard_live_background_by_default(monkeypatch):
    calls = []
    tmp_path = make_test_dir("sports-dashboard-live-background-skip")
    device_config = FakeDeviceConfig(tmp_path)
    task = RefreshTask(device_config, display_manager=None)
    live_plugin = FakePlugin(calls, live_state={"active": True, "interval_seconds": 60})
    playlist = Playlist(
        "DailyDoseOfDay",
        "00:00",
        "24:00",
        plugins=[
            {
                "plugin_id": "sports_dashboard",
                "name": "SportsDashboard",
                "plugin_settings": {"id": "sports_dashboard"},
                "refresh": {"interval": 60},
                "latest_refresh_time": "2026-05-26T07:00:00+00:00",
            },
        ],
    )
    monkeypatch.setattr("src.refresh_task.get_plugin_instance", lambda config: live_plugin)

    task._refresh_due_plugin_instances(
        playlist,
        datetime(2026, 5, 26, 7, 5, tzinfo=timezone.utc),
        only_plugin_id="sports_dashboard",
    )

    assert calls == []
    assert playlist.find_plugin("sports_dashboard", "SportsDashboard").latest_refresh_time == "2026-05-26T07:00:00+00:00"
    assert device_config.write_count == 0


def test_refresh_due_plugin_instances_skips_sports_dashboard_background_without_live_state(monkeypatch):
    calls = []
    tmp_path = make_test_dir("sports-dashboard-display-only-background")
    device_config = FakeDeviceConfig(tmp_path)
    task = RefreshTask(device_config, display_manager=None)
    plugin = FakePlugin(calls, live_state=None)
    playlist = Playlist(
        "DailyDoseOfDay",
        "00:00",
        "24:00",
        plugins=[
            {
                "plugin_id": "sports_dashboard",
                "name": "SportsDashboard",
                "plugin_settings": {"id": "sports_dashboard"},
                "refresh": {"interval": 60},
                "latest_refresh_time": "2026-05-26T07:00:00+00:00",
            },
        ],
    )
    monkeypatch.setattr("src.refresh_task.get_plugin_instance", lambda config: plugin)

    task._refresh_due_plugin_instances(
        playlist,
        datetime(2026, 5, 26, 7, 5, tzinfo=timezone.utc),
        only_plugin_id="sports_dashboard",
    )

    assert calls == []
    assert playlist.find_plugin("sports_dashboard", "SportsDashboard").latest_refresh_time == "2026-05-26T07:00:00+00:00"
    assert device_config.write_count == 0


def test_refresh_due_plugin_instances_allows_sports_dashboard_background_when_enabled(monkeypatch):
    calls = []
    tmp_path = make_test_dir("sports-dashboard-background-enabled")
    device_config = FakeDeviceConfig(tmp_path)
    task = RefreshTask(device_config, display_manager=None)
    live_plugin = FakePlugin(calls, live_state={"active": True, "interval_seconds": 60})
    playlist = Playlist(
        "DailyDoseOfDay",
        "00:00",
        "24:00",
        plugins=[
            {
                "plugin_id": "sports_dashboard",
                "name": "SportsDashboard",
                "plugin_settings": {"id": "sports_dashboard", "backgroundCacheRefreshEnabled": "true"},
                "refresh": {"interval": 60},
                "latest_refresh_time": "2026-05-26T07:00:00+00:00",
            },
        ],
    )
    monkeypatch.setattr("src.refresh_task.get_plugin_instance", lambda config: live_plugin)

    task._refresh_due_plugin_instances(
        playlist,
        datetime(2026, 5, 26, 7, 5, tzinfo=timezone.utc),
        only_plugin_id="sports_dashboard",
    )

    assert calls == ["sports_dashboard"]
    assert playlist.find_plugin("sports_dashboard", "SportsDashboard").latest_refresh_time == "2026-05-26T07:05:00+00:00"
    assert device_config.write_count == 1


def test_background_cache_refresh_does_not_target_only_display_only_live_plugin(monkeypatch):
    tmp_path = make_test_dir("background-cache-display-only-live")
    device_config = FakeDeviceConfig(tmp_path)
    task = RefreshTask(device_config, display_manager=None)
    playlist = Playlist(
        "DailyDoseOfDay",
        "00:00",
        "24:00",
        plugins=[
            {
                "plugin_id": "sports_dashboard",
                "name": "SportsDashboard",
                "plugin_settings": {"id": "sports_dashboard"},
                "refresh": {"interval": 3600},
                "latest_refresh_time": "2026-05-26T07:00:00+00:00",
            },
            {
                "plugin_id": "tech_pulse",
                "name": "TechPulse",
                "plugin_settings": {"id": "tech_pulse"},
                "refresh": {"interval": 60},
                "latest_refresh_time": "2026-05-26T07:00:00+00:00",
            },
            {
                "plugin_id": "simple-calendar",
                "name": "Calendar",
                "plugin_settings": {"id": "calendar"},
                "refresh": {"interval": 3600},
                "latest_refresh_time": "2026-05-26T07:05:00+00:00",
            },
        ],
    )
    for plugin_instance in playlist.plugins:
        Image.new("RGB", (1, 1), "black").save(tmp_path / plugin_instance.get_image_path())
    plugins = {
        "sports_dashboard": FakePlugin([], live_state={"active": True, "interval_seconds": 60}),
        "tech_pulse": FakePlugin([]),
        "simple-calendar": FakePlugin([]),
    }
    monkeypatch.setattr("src.refresh_task.get_plugin_instance", lambda config: plugins[config["id"]])
    captured = []

    def capture_start(*args, **kwargs):
        captured.append({"args": args, "kwargs": kwargs})

    monkeypatch.setattr(task, "_start_due_plugin_cache_refresh", capture_start)

    task._maybe_start_background_cache_refresh(
        playlist,
        playlist.find_plugin("simple-calendar", "Calendar"),
        datetime(2026, 5, 26, 7, 5, tzinfo=timezone.utc),
    )

    assert len(captured) == 1
    assert captured[0]["kwargs"]["only_plugin_id"] is None


def test_background_cache_refresh_skips_when_only_display_only_live_plugin_is_due(monkeypatch):
    tmp_path = make_test_dir("background-cache-only-display-only-live")
    device_config = FakeDeviceConfig(tmp_path)
    task = RefreshTask(device_config, display_manager=None)
    playlist = Playlist(
        "DailyDoseOfDay",
        "00:00",
        "24:00",
        plugins=[
            {
                "plugin_id": "sports_dashboard",
                "name": "SportsDashboard",
                "plugin_settings": {"id": "sports_dashboard"},
                "refresh": {"interval": 3600},
                "latest_refresh_time": "2026-05-26T07:00:00+00:00",
            },
            {
                "plugin_id": "simple-calendar",
                "name": "Calendar",
                "plugin_settings": {"id": "calendar"},
                "refresh": {"interval": 3600},
                "latest_refresh_time": "2026-05-26T07:05:00+00:00",
            },
        ],
    )
    for plugin_instance in playlist.plugins:
        Image.new("RGB", (1, 1), "black").save(tmp_path / plugin_instance.get_image_path())
    plugins = {
        "sports_dashboard": FakePlugin([], live_state={"active": True, "interval_seconds": 60}),
        "simple-calendar": FakePlugin([]),
    }
    monkeypatch.setattr("src.refresh_task.get_plugin_instance", lambda config: plugins[config["id"]])
    captured = []
    monkeypatch.setattr(task, "_start_due_plugin_cache_refresh", lambda *args, **kwargs: captured.append(kwargs))

    task._maybe_start_background_cache_refresh(
        playlist,
        playlist.find_plugin("simple-calendar", "Calendar"),
        datetime(2026, 5, 26, 7, 5, tzinfo=timezone.utc),
    )

    assert captured == []


def test_refresh_due_plugin_instances_stops_before_generation_under_resource_pressure(monkeypatch):
    calls = []
    tmp_path = make_test_dir("background-cache-pressure-before-generation")
    device_config = FakeDeviceConfig(tmp_path)
    task = RefreshTask(device_config, display_manager=None)
    plugin = FakePlugin(calls)
    playlist = Playlist(
        "DailyDoseOfDay",
        "00:00",
        "24:00",
        plugins=[
            {
                "plugin_id": "tech_pulse",
                "name": "TechPulse",
                "plugin_settings": {"id": "tech_pulse"},
                "refresh": {"interval": 60},
                "latest_refresh_time": "2026-05-26T07:00:00+00:00",
            },
        ],
    )
    Image.new("RGB", (1, 1), "black").save(tmp_path / "tech_pulse_TechPulse.png")
    monkeypatch.setattr("src.refresh_task.get_plugin_instance", lambda config: plugin)
    monkeypatch.setattr(task, "_cache_refresh_under_resource_pressure", lambda: True)

    task._refresh_due_plugin_instances(
        playlist,
        datetime(2026, 5, 26, 7, 5, tzinfo=timezone.utc),
    )

    assert calls == []
    assert playlist.find_plugin("tech_pulse", "TechPulse").latest_refresh_time == "2026-05-26T07:00:00+00:00"
    assert device_config.write_count == 0


def test_live_refresh_wait_seconds_uses_plugin_hook(monkeypatch):
    tmp_path = make_test_dir("live-hook-wait")
    live_plugin = FakePlugin([], live_state={"active": True, "interval_seconds": 180})
    playlist = Playlist(
        "DailyDoseOfDay",
        "00:00",
        "24:00",
        plugins=[
            {
                "plugin_id": "live_plugin",
                "name": "LivePlugin",
                "plugin_settings": {"id": "live"},
                "refresh": {"interval": 3600},
                "latest_refresh_time": "2026-05-26T07:00:00+00:00",
            },
        ],
    )
    device_config = ThreadedDeviceConfig(tmp_path, playlist)
    task = RefreshTask(device_config, display_manager=None)
    monkeypatch.setattr("src.refresh_task.get_plugin_instance", lambda config: live_plugin)

    wait_seconds = task._live_refresh_wait_seconds(
        datetime(2026, 5, 26, 7, 2, tzinfo=timezone.utc)
    )

    assert wait_seconds == 60


def test_live_refresh_wait_seconds_is_due_without_prior_refresh(monkeypatch):
    tmp_path = make_test_dir("live-hook-no-prior-refresh")
    live_plugin = FakePlugin([], live_state={"active": True, "interval_seconds": 180})
    playlist = Playlist(
        "DailyDoseOfDay",
        "00:00",
        "24:00",
        plugins=[
            {
                "plugin_id": "live_plugin",
                "name": "LivePlugin",
                "plugin_settings": {"id": "live"},
                "refresh": {"interval": 3600},
            },
        ],
    )
    device_config = ThreadedDeviceConfig(tmp_path, playlist)
    task = RefreshTask(device_config, display_manager=None)
    monkeypatch.setattr("src.refresh_task.get_plugin_instance", lambda config: live_plugin)

    wait_seconds = task._live_refresh_wait_seconds(
        datetime(2026, 5, 26, 7, 2, tzinfo=timezone.utc)
    )

    assert wait_seconds == 0


def test_live_refresh_is_not_due_without_active_hook(monkeypatch):
    tmp_path = make_test_dir("live-hook-inactive")
    live_plugin = FakePlugin([], live_state=None)
    playlist = Playlist(
        "DailyDoseOfDay",
        "00:00",
        "24:00",
        plugins=[
            {
                "plugin_id": "live_plugin",
                "name": "LivePlugin",
                "plugin_settings": {"id": "live"},
                "refresh": {"interval": 3600},
                "latest_refresh_time": "2026-05-26T07:00:00+00:00",
            },
        ],
    )
    device_config = ThreadedDeviceConfig(tmp_path, playlist)
    task = RefreshTask(device_config, display_manager=None)
    monkeypatch.setattr("src.refresh_task.get_plugin_instance", lambda config: live_plugin)
    plugin_instance = playlist.find_plugin("live_plugin", "LivePlugin")

    live_due = task._plugin_live_refresh_due(
        plugin_instance,
        datetime(2026, 5, 26, 7, 10, tzinfo=timezone.utc),
    )
    wait_seconds = task._live_refresh_wait_seconds(
        datetime(2026, 5, 26, 7, 10, tzinfo=timezone.utc)
    )

    assert live_due is False
    assert wait_seconds is None


def test_live_refresh_scan_skips_plugin_without_manifest_capability(monkeypatch):
    tmp_path = make_test_dir("manifest-live-scan-lazy")
    playlist = Playlist(
        "DailyDoseOfDay",
        "00:00",
        "24:00",
        plugins=[
            {
                "plugin_id": "ordinary_plugin",
                "name": "Ordinary Plugin",
                "plugin_settings": {"id": "ordinary"},
                "refresh": {"interval": 3600},
                "latest_refresh_time": "2026-05-26T07:00:00+00:00",
            },
        ],
    )
    device_config = ThreadedDeviceConfig(tmp_path, playlist)
    manifest = PluginManifest(
        schema_version=2,
        id="ordinary_plugin",
        class_name="OrdinaryPlugin",
        display_name="Ordinary Plugin",
        refresh_on_display=False,
        capabilities=PluginCapabilities(supports_live_refresh=False),
        raw={},
    )
    device_config.get_plugin = lambda plugin_id: {
        "id": plugin_id,
        "_manifest": manifest,
    }
    loaded = []
    monkeypatch.setattr(
        "src.refresh_task.get_plugin_instance",
        lambda config: loaded.append(config) or FakePlugin([], live_state=None),
    )
    task = RefreshTask(device_config, display_manager=None)

    wait_seconds = task._live_refresh_wait_seconds(
        datetime(2026, 5, 26, 7, 2, tzinfo=timezone.utc)
    )
    snapshot_due = task._snapshot_live_refresh_due(
        playlist.plugins[0].snapshot(),
        datetime(2026, 5, 26, 7, 2, tzinfo=timezone.utc),
    )

    assert wait_seconds is None
    assert snapshot_due is False
    assert loaded == []


def test_playlist_refresh_uses_cached_image_without_generating_for_scheduled_display():
    calls = []
    tmp_path = make_test_dir("scheduled-cache")
    device_config = FakeDeviceConfig(tmp_path)
    playlist = Playlist(
        "DailyDoseOfDay",
        "00:00",
        "24:00",
        plugins=[
            {
                "plugin_id": "slow",
                "name": "Slow Plugin",
                "plugin_settings": {"id": "slow"},
                "refresh": {"interval": 300},
                "latest_refresh_time": "2026-05-26T07:00:00+00:00",
            },
        ],
    )
    plugin_instance = playlist.find_plugin("slow", "Slow Plugin")
    Image.new("RGB", (2, 1), "black").save(tmp_path / "slow_Slow_Plugin.png")

    image = PlaylistRefresh(playlist, plugin_instance, display_cached_only=True).execute(
        FakePlugin(calls),
        device_config,
        datetime(2026, 5, 26, 7, 5, tzinfo=timezone.utc),
    )

    assert calls == []
    assert image.size == (2, 1)
    assert image.getpixel((0, 0)) == (0, 0, 0)
    assert plugin_instance.latest_refresh_time == "2026-05-26T07:00:00+00:00"


def test_playlist_refresh_instance_false_overrides_manifest_refresh_on_display():
    calls = []
    tmp_path = make_test_dir("instance-refresh-on-display-false")
    device_config = FakeDeviceConfig(tmp_path)
    device_config.config["display_refresh_resource_guard_enabled"] = False
    playlist = Playlist(
        "DailyDoseOfDay",
        "00:00",
        "24:00",
        plugins=[
            {
                "plugin_id": "base_plugin",
                "name": "ManifestDefaultTrue",
                "plugin_settings": {
                    "id": "base-instance",
                    "refreshOnDisplay": False,
                },
                "refresh": {"interval": 300},
                "latest_refresh_time": "2999-01-01T00:00:00+00:00",
            },
        ],
    )
    plugin_instance = playlist.plugins[0]
    Image.new("RGB", (2, 1), "black").save(
        tmp_path / plugin_instance.get_image_path()
    )
    plugin = BasePlugin({"id": "base_plugin", "refresh_on_display": True})
    plugin.generate_image = lambda *_args: calls.append("rendered") or Image.new(
        "RGB", (2, 1), "white"
    )

    image = PlaylistRefresh(
        playlist,
        plugin_instance,
        display_cached_only=True,
    ).execute(
        plugin,
        device_config,
        datetime(2026, 5, 26, 7, 5, tzinfo=timezone.utc),
    )

    assert calls == []
    assert image.getpixel((0, 0)) == (0, 0, 0)


def test_playlist_refresh_newspaper_refresh_on_display_false_overrides_rotation_default():
    calls = []
    tmp_path = make_test_dir("newspaper-instance-refresh-on-display-false")
    device_config = FakeDeviceConfig(tmp_path)
    device_config.config["display_refresh_resource_guard_enabled"] = False
    playlist = Playlist(
        "DailyDoseOfDay",
        "00:00",
        "24:00",
        plugins=[
            {
                "plugin_id": "newspaper",
                "name": "RotatingNewspaper",
                "plugin_settings": {
                    "id": "rotating-news",
                    "mediaRotationMode": "rotate",
                    "refreshOnDisplay": " false ",
                },
                "refresh": {"scheduled": "15:00"},
                "latest_refresh_time": "2999-01-01T00:00:00+00:00",
            },
        ],
    )
    plugin_instance = playlist.plugins[0]
    Image.new("RGB", (2, 1), "black").save(
        tmp_path / plugin_instance.get_image_path()
    )
    plugin = Newspaper({"id": "newspaper"})
    plugin.generate_image = lambda *_args: calls.append("rendered") or Image.new(
        "RGB", (2, 1), "white"
    )

    image = PlaylistRefresh(
        playlist,
        plugin_instance,
        display_cached_only=True,
    ).execute(
        plugin,
        device_config,
        datetime(2026, 5, 26, 16, 0, tzinfo=timezone.utc),
    )

    assert calls == []
    assert image.getpixel((0, 0)) == (0, 0, 0)


@pytest.mark.parametrize(
    ("settings", "expected"),
    [
        ({"mediaRotationMode": "rotate"}, True),
        ({"mediaRotationMode": "single"}, False),
        (
            {"mediaRotationMode": "rotate", "refreshOnDisplay": False},
            False,
        ),
        (
            {"mediaRotationMode": "single", "refreshOnDisplay": " true "},
            True,
        ),
    ],
)
def test_newspaper_refresh_on_display_uses_rotation_only_as_missing_value_default(
    settings,
    expected,
):
    plugin = Newspaper({"id": "newspaper"})

    assert plugin.wants_refresh_on_display(settings) is expected


def test_playlist_refresh_uses_cache_when_live_refresh_is_due_on_scheduled_display():
    calls = []
    tmp_path = make_test_dir("scheduled-live-refresh")
    device_config = FakeDeviceConfig(tmp_path)
    playlist = Playlist(
        "DailyDoseOfDay",
        "00:00",
        "24:00",
        plugins=[
            {
                "plugin_id": "sports_dashboard",
                "name": "SportsDashboard",
                "plugin_settings": {"id": "sports_dashboard"},
                "refresh": {"interval": 900},
                "latest_refresh_time": "2026-05-26T07:00:00+00:00",
            },
        ],
    )
    plugin_instance = playlist.find_plugin("sports_dashboard", "SportsDashboard")
    Image.new("RGB", (2, 1), "black").save(tmp_path / "sports_dashboard_SportsDashboard.png")

    image = PlaylistRefresh(playlist, plugin_instance, display_cached_only=True).execute(
        FakePlugin(calls, live_state={"active": True, "interval_seconds": 900}),
        device_config,
        datetime(2026, 5, 26, 7, 15, tzinfo=timezone.utc),
    )

    assert calls == []
    assert image.size == (2, 1)
    assert image.getpixel((0, 0)) == (0, 0, 0)
    assert plugin_instance.latest_refresh_time == "2026-05-26T07:00:00+00:00"


def test_playlist_refresh_uses_cached_image_for_live_refresh_under_resource_pressure(monkeypatch):
    calls = []
    tmp_path = make_test_dir("scheduled-live-refresh-pressure")
    device_config = FakeDeviceConfig(tmp_path)
    device_config.config["display_refresh_min_available_mb"] = 150
    device_config.config["display_refresh_max_swap_percent"] = 30
    playlist = Playlist(
        "DailyDoseOfDay",
        "00:00",
        "24:00",
        plugins=[
            {
                "plugin_id": "sports_dashboard",
                "name": "SportsDashboard",
                "plugin_settings": {"id": "sports_dashboard"},
                "refresh": {"interval": 900},
                "latest_refresh_time": "2026-05-26T07:00:00+00:00",
            },
        ],
    )
    plugin_instance = playlist.find_plugin("sports_dashboard", "SportsDashboard")
    Image.new("RGB", (2, 1), "black").save(tmp_path / "sports_dashboard_SportsDashboard.png")
    memory = type("Memory", (), {"available": 134 * 1024 * 1024, "percent": 71.0})()
    swap = type("Swap", (), {"percent": 31.0})()
    monkeypatch.setattr("src.refresh_task.psutil.virtual_memory", lambda: memory)
    monkeypatch.setattr("src.refresh_task.psutil.swap_memory", lambda: swap)

    image = PlaylistRefresh(playlist, plugin_instance, display_cached_only=True).execute(
        FakePlugin(calls, live_state={"active": True, "interval_seconds": 900}),
        device_config,
        datetime(2026, 5, 26, 7, 15, tzinfo=timezone.utc),
    )

    assert calls == []
    assert image.size == (2, 1)
    assert image.getpixel((0, 0)) == (0, 0, 0)
    assert plugin_instance.latest_refresh_time == "2026-05-26T07:00:00+00:00"


def test_playlist_refresh_uses_sports_cache_when_display_interval_is_due():
    calls = []
    tmp_path = make_test_dir("scheduled-sports-dashboard-refresh")
    device_config = FakeDeviceConfig(tmp_path)
    playlist = Playlist(
        "DailyDoseOfDay",
        "00:00",
        "24:00",
        plugins=[
            {
                "plugin_id": "sports_dashboard",
                "name": "SportsDashboard",
                "plugin_settings": {"id": "sports_dashboard"},
                "refresh": {"interval": 900},
                "latest_refresh_time": "2026-05-26T07:00:00+00:00",
            },
        ],
    )
    plugin_instance = playlist.find_plugin("sports_dashboard", "SportsDashboard")
    Image.new("RGB", (2, 1), "black").save(tmp_path / "sports_dashboard_SportsDashboard.png")

    image = PlaylistRefresh(playlist, plugin_instance, display_cached_only=True).execute(
        FakePlugin(calls, live_state=None),
        device_config,
        datetime(2026, 5, 26, 7, 15, tzinfo=timezone.utc),
    )

    assert calls == []
    assert image.size == (2, 1)
    assert image.getpixel((0, 0)) == (0, 0, 0)
    assert plugin_instance.latest_refresh_time == "2026-05-26T07:00:00+00:00"


def test_playlist_refresh_uses_lol_info_cache_on_scheduled_display():
    calls = []
    tmp_path = make_test_dir("scheduled-lol-info-refresh")
    device_config = FakeDeviceConfig(tmp_path)
    playlist = Playlist(
        "DailyDoseOfDay",
        "00:00",
        "24:00",
        plugins=[
            {
                "plugin_id": "lol_info",
                "name": "LoLInfo",
                "plugin_settings": {"id": "riot-page"},
                "refresh": {"interval": 7200},
                "latest_refresh_time": "2026-05-26T07:00:00+00:00",
            },
        ],
    )
    plugin_instance = playlist.find_plugin("lol_info", "LoLInfo")
    Image.new("RGB", (2, 1), "black").save(tmp_path / "lol_info_LoLInfo.png")

    image = PlaylistRefresh(playlist, plugin_instance, display_cached_only=True).execute(
        FakePlugin(calls),
        device_config,
        datetime(2026, 5, 26, 7, 5, tzinfo=timezone.utc),
    )

    assert calls == []
    assert image.size == (2, 1)
    assert image.getpixel((0, 0)) == (0, 0, 0)
    assert plugin_instance.latest_refresh_time == "2026-05-26T07:00:00+00:00"


def test_playlist_refresh_uses_simple_calendar_cache_on_scheduled_display():
    calls = []
    tmp_path = make_test_dir("scheduled-simple-calendar-refresh")
    device_config = FakeDeviceConfig(tmp_path)
    playlist = Playlist(
        "DailyDoseOfDay",
        "00:00",
        "24:00",
        plugins=[
            {
                "plugin_id": "simple_calendar",
                "name": "Date",
                "plugin_settings": {"id": "simple-calendar"},
                "refresh": {"scheduled": "00:00"},
                "latest_refresh_time": "2026-06-29T00:01:00+00:00",
            },
        ],
    )
    plugin_instance = playlist.find_plugin("simple_calendar", "Date")
    Image.new("RGB", (2, 1), "black").save(tmp_path / "simple_calendar_Date.png")

    image = PlaylistRefresh(playlist, plugin_instance, display_cached_only=True).execute(
        FakePlugin(calls),
        device_config,
        datetime(2026, 6, 29, 7, 5, tzinfo=timezone.utc),
    )

    assert calls == []
    assert image.size == (2, 1)
    assert image.getpixel((0, 0)) == (0, 0, 0)
    assert plugin_instance.latest_refresh_time == "2026-06-29T00:01:00+00:00"



def test_playlist_refresh_uses_steam_daily_art_cache_on_scheduled_display():
    calls = []
    tmp_path = make_test_dir("scheduled-steam-daily-art-refresh")
    device_config = FakeDeviceConfig(tmp_path)
    playlist = Playlist(
        "DailyDoseOfDay",
        "00:00",
        "24:00",
        plugins=[
            {
                "plugin_id": "steam_daily_art",
                "name": "SteamDailyArt",
                "plugin_settings": {"id": "steam-art"},
                "refresh": {"scheduled": "00:00"},
                "latest_refresh_time": "2026-06-29T00:01:00+00:00",
            },
        ],
    )
    plugin_instance = playlist.find_plugin("steam_daily_art", "SteamDailyArt")
    Image.new("RGB", (2, 1), "black").save(tmp_path / "steam_daily_art_SteamDailyArt.png")

    image = PlaylistRefresh(playlist, plugin_instance, display_cached_only=True).execute(
        FakePlugin(calls),
        device_config,
        datetime(2026, 6, 29, 7, 5, tzinfo=timezone.utc),
    )

    assert calls == []
    assert image.size == (2, 1)
    assert image.getpixel((0, 0)) == (0, 0, 0)
    assert plugin_instance.latest_refresh_time == "2026-06-29T00:01:00+00:00"



def test_playlist_refresh_uses_placeholder_when_scheduled_cache_is_missing():
    calls = []
    tmp_path = make_test_dir("scheduled-placeholder")
    device_config = FakeDeviceConfig(tmp_path)
    device_config.config["resolution"] = [200, 120]
    playlist = Playlist(
        "DailyDoseOfDay",
        "00:00",
        "24:00",
        plugins=[
            {
                "plugin_id": "missing",
                "name": "Missing Plugin",
                "plugin_settings": {"id": "missing"},
                "refresh": {"interval": 300},
                "latest_refresh_time": "2026-05-26T07:00:00+00:00",
            },
        ],
    )
    plugin_instance = playlist.find_plugin("missing", "Missing Plugin")

    image = PlaylistRefresh(playlist, plugin_instance, display_cached_only=True).execute(
        FakePlugin(calls),
        device_config,
        datetime(2026, 5, 26, 7, 5, tzinfo=timezone.utc),
    )

    assert calls == []
    assert image.size == (200, 120)
    assert plugin_instance.latest_refresh_time == "2026-05-26T07:00:00+00:00"
    assert not (tmp_path / "missing_Missing_Plugin.png").exists()


def test_playlist_refresh_uses_placeholder_when_scheduled_cache_is_corrupt():
    calls = []
    tmp_path = make_test_dir("scheduled-corrupt-cache")
    device_config = FakeDeviceConfig(tmp_path)
    playlist = Playlist(
        "DailyDoseOfDay",
        "00:00",
        "24:00",
        plugins=[
            {
                "plugin_id": "daily_art",
                "name": "DailyArt",
                "plugin_settings": {"id": "daily-art"},
                "refresh": {"interval": 300},
                "latest_refresh_time": "2026-05-26T07:00:00+00:00",
            },
        ],
    )
    plugin_instance = playlist.find_plugin("daily_art", "DailyArt")
    cache_path = tmp_path / "daily_art_DailyArt.png"
    cache_path.write_bytes(b"\x89PNG\r\n\x1a\n")

    image = PlaylistRefresh(playlist, plugin_instance, display_cached_only=True).execute(
        FakePlugin(calls),
        device_config,
        datetime(2026, 5, 26, 7, 5, tzinfo=timezone.utc),
    )

    assert calls == []
    assert image.size != (1, 1)
    assert plugin_instance.latest_refresh_time == "2026-05-26T07:00:00+00:00"
    assert cache_path.read_bytes() == b"\x89PNG\r\n\x1a\n"


def test_playlist_force_refresh_marks_plugin_settings():
    calls = []
    tmp_path = make_test_dir("playlist-force-settings")
    device_config = FakeDeviceConfig(tmp_path)
    playlist = Playlist(
        "DailyDoseOfDay",
        "00:00",
        "24:00",
        plugins=[
            {
                "plugin_id": "sports_dashboard",
                "name": "WorldCup",
                "plugin_settings": {"id": "worldcup", "forceRefresh": "false"},
                "refresh": {"interval": 3600},
                "latest_refresh_time": "2026-05-26T07:04:00+00:00",
            },
        ],
    )
    plugin_instance = playlist.find_plugin("sports_dashboard", "WorldCup")
    Image.new("RGB", (1, 1), "black").save(tmp_path / "sports_dashboard_WorldCup.png")

    PlaylistRefresh(playlist, plugin_instance, force=True).execute(
        CapturePlugin(calls),
        device_config,
        datetime(2026, 5, 26, 7, 5, tzinfo=timezone.utc),
    )

    assert calls == [{"id": "worldcup", "forceRefresh": True, "force_refresh": True, "_inkypiDisplayRender": True}]
    assert plugin_instance.settings == {"id": "worldcup", "forceRefresh": "false"}


def test_manual_refresh_marks_plugin_settings():
    calls = []

    ManualRefresh("sports_dashboard", {"id": "worldcup"}).execute(
        CapturePlugin(calls),
        device_config=None,
        current_dt=datetime(2026, 5, 26, 7, 5, tzinfo=timezone.utc),
    )

    assert calls == [{"id": "worldcup", "forceRefresh": True, "force_refresh": True, "_inkypiDisplayRender": True}]


def test_manual_update_times_out_instead_of_waiting_forever():
    tmp_path = make_test_dir("manual-timeout")
    device_config = FakeDeviceConfig(tmp_path)
    device_config.config["manual_update_timeout_seconds"] = 0.01
    task = RefreshTask(device_config, display_manager=None)
    task.running = True
    action = ManualRefresh("sports_dashboard", {"id": "worldcup"})

    try:
        task.manual_update(action)
    except TimeoutError as exc:
        assert "Manual update timed out" in str(exc)
    else:
        raise AssertionError("manual_update should time out without a running worker thread")

    assert task.manual_update_request == ()


def test_manual_update_runs_after_in_flight_playlist_refresh(monkeypatch):
    calls = []
    tmp_path = make_test_dir("manual-after-inflight")
    playlist = Playlist(
        "DailyDoseOfDay",
        "00:00",
        "24:00",
        plugins=[
            {
                "plugin_id": "live_radar",
                "name": "LiveRadar",
                "plugin_settings": {"id": "live_radar"},
                "refresh": {"interval": 999999999},
                "latest_refresh_time": "2999-01-01T00:00:00+00:00",
            },
        ],
    )
    plugin_instance = playlist.find_plugin("live_radar", "LiveRadar")
    Image.new("RGB", (1, 1), "black").save(tmp_path / plugin_instance.get_image_path())

    device_config = ThreadedDeviceConfig(tmp_path, playlist)
    device_config.config["manual_update_timeout_seconds"] = 1
    device_config.config.update({"theme_mode": "day", "active_theme": "day"})
    display_manager = BlockingDisplayManager()
    task = RefreshTask(device_config, display_manager=display_manager)
    _write_runtime_cache(task, plugin_instance)
    monkeypatch.setattr(
        "src.refresh_task.get_plugin_instance",
        lambda config: CapturePlugin(calls),
    )

    task.start()
    try:
        assert display_manager.first_display_started.wait(timeout=1)

        errors = []
        manual_thread = threading.Thread(
            target=lambda: _run_manual_update(task, playlist, plugin_instance, errors),
            daemon=True,
        )
        manual_thread.start()
        time.sleep(0.05)
        display_manager.release_first_display.set()
        manual_thread.join(timeout=1)

        assert not manual_thread.is_alive()
        assert errors == []
        assert calls
        assert calls[-1] == {
            "id": "live_radar",
            "forceRefresh": True,
            "force_refresh": True,
            "_inkypiDisplayRender": True,
        }
    finally:
        display_manager.release_first_display.set()
        task.stop()


def _run_manual_update(task, playlist, plugin_instance, errors):
    try:
        task.manual_update(PlaylistRefresh(playlist, plugin_instance, force=True))
    except Exception as exc:
        errors.append(exc)


def test_refresh_due_plugin_instances_skips_displayed_plugin(monkeypatch):
    calls = []
    tmp_path = make_test_dir("skip-displayed")
    device_config = FakeDeviceConfig(tmp_path)
    task = RefreshTask(device_config, display_manager=None)
    playlist = Playlist(
        "DailyDoseOfDay",
        "00:00",
        "24:00",
        plugins=[
            {
                "plugin_id": "displayed",
                "name": "Displayed Plugin",
                "plugin_settings": {"id": "displayed"},
                "refresh": {"interval": 300},
                "latest_refresh_time": "2026-05-26T07:00:00+00:00",
            },
            {
                "plugin_id": "other",
                "name": "Other Plugin",
                "plugin_settings": {"id": "other"},
                "refresh": {"interval": 300},
                "latest_refresh_time": "2026-05-26T07:00:00+00:00",
            },
        ],
    )

    monkeypatch.setattr(
        "src.refresh_task.get_plugin_instance",
        lambda config: FakePlugin(calls),
    )

    displayed = playlist.find_plugin("displayed", "Displayed Plugin")
    task._refresh_due_plugin_instances(
        playlist,
        datetime(2026, 5, 26, 7, 5, tzinfo=timezone.utc),
        skip_plugin_instance=displayed,
    )

    assert calls == ["other"]
    assert not (tmp_path / "displayed_Displayed_Plugin.png").exists()
    assert (tmp_path / "other_Other_Plugin.png").exists()
    assert displayed.latest_refresh_time == "2026-05-26T07:00:00+00:00"
    assert playlist.find_plugin("other", "Other Plugin").latest_refresh_time == "2026-05-26T07:05:00+00:00"
    assert device_config.write_count == 1


def test_refresh_due_plugin_instances_refreshes_displayed_refresh_on_display_only(monkeypatch):
    calls = []
    tmp_path = make_test_dir("displayed-on-display-cache")
    device_config = FakeDeviceConfig(tmp_path)
    device_config.config["display_triggered_refresh_enabled"] = True
    current_dt = datetime(2026, 5, 26, 16, 0, tzinfo=timezone.utc)
    playlist = Playlist(
        "DailyDoseOfDay",
        "00:00",
        "24:00",
        plugins=[
            {
                "plugin_id": "newspaper",
                "name": "Displayed News",
                "plugin_settings": {"id": "displayed", "mediaRotationMode": "rotate"},
                "refresh": {"scheduled": "15:00"},
                "latest_refresh_time": "2026-05-26T15:01:00+00:00",
            },
            {
                "plugin_id": "newspaper",
                "name": "Other News",
                "plugin_settings": {"id": "other", "mediaRotationMode": "rotate"},
                "refresh": {"scheduled": "15:00"},
                "latest_refresh_time": "2026-05-26T15:01:00+00:00",
            },
        ],
    )
    Image.new("RGB", (1, 1), "black").save(tmp_path / "newspaper_Displayed_News.png")
    Image.new("RGB", (1, 1), "black").save(tmp_path / "newspaper_Other_News.png")
    monkeypatch.setattr(
        "src.refresh_task.get_plugin_instance",
        lambda config: FakePlugin(calls),
    )

    displayed = playlist.find_plugin("newspaper", "Displayed News")
    task = RefreshTask(device_config, display_manager=None)
    task._refresh_due_plugin_instances(
        playlist,
        current_dt,
        displayed_plugin_instance=displayed,
    )

    assert calls == ["displayed"]
    assert displayed.latest_refresh_time == "2026-05-26T16:00:00+00:00"
    assert playlist.find_plugin("newspaper", "Other News").latest_refresh_time == "2026-05-26T15:01:00+00:00"
    assert device_config.write_count == 1


def test_refresh_due_plugin_instances_refreshes_displayed_lol_info_by_default(monkeypatch):
    calls = []
    tmp_path = make_test_dir("displayed-lol-info-refresh")
    device_config = FakeDeviceConfig(tmp_path)
    device_config.config["display_triggered_refresh_enabled"] = True
    current_dt = datetime(2026, 6, 4, 16, 0, tzinfo=timezone.utc)
    playlist = Playlist(
        "DailyDoseOfDay",
        "00:00",
        "24:00",
        plugins=[
            {
                "plugin_id": "lol_info",
                "name": "LoL Daily",
                "plugin_settings": {"id": "displayed"},
                "refresh": {"scheduled": "15:00"},
                "latest_refresh_time": "2026-06-04T15:01:00+00:00",
            },
            {
                "plugin_id": "lol_info",
                "name": "LoL Other",
                "plugin_settings": {"id": "other"},
                "refresh": {"scheduled": "15:00"},
                "latest_refresh_time": "2026-06-04T15:01:00+00:00",
            },
        ],
    )
    Image.new("RGB", (1, 1), "black").save(tmp_path / "lol_info_LoL_Daily.png")
    Image.new("RGB", (1, 1), "black").save(tmp_path / "lol_info_LoL_Other.png")
    monkeypatch.setattr(
        "src.refresh_task.get_plugin_instance",
        lambda config: FakePlugin(calls, refresh_on_display=True),
    )

    displayed = playlist.find_plugin("lol_info", "LoL Daily")
    task = RefreshTask(device_config, display_manager=None)
    task._refresh_due_plugin_instances(
        playlist,
        current_dt,
        displayed_plugin_instance=displayed,
    )

    assert calls == ["displayed"]
    assert displayed.latest_refresh_time == "2026-06-04T16:00:00+00:00"
    assert playlist.find_plugin("lol_info", "LoL Other").latest_refresh_time == "2026-06-04T15:01:00+00:00"
    assert device_config.write_count == 1


def test_refresh_due_plugin_instances_force_refreshes_fresh_cache(monkeypatch):
    calls = []
    tmp_path = make_test_dir("force-cache")
    device_config = FakeDeviceConfig(tmp_path)
    task = RefreshTask(device_config, display_manager=None)
    playlist = Playlist(
        "DailyDoseOfDay",
        "00:00",
        "24:00",
        plugins=[
            {
                "plugin_id": "fresh",
                "name": "Fresh Plugin",
                "plugin_settings": {"id": "fresh"},
                "refresh": {"interval": 3600},
                "latest_refresh_time": "2026-05-26T07:04:00+00:00",
            },
        ],
    )

    Image.new("RGB", (1, 1), "black").save(tmp_path / "fresh_Fresh_Plugin.png")
    monkeypatch.setattr(
        "src.refresh_task.get_plugin_instance",
        lambda config: FakePlugin(calls),
    )

    task._refresh_due_plugin_instances(
        playlist,
        datetime(2026, 5, 26, 7, 5, tzinfo=timezone.utc),
        force=True,
    )

    assert calls == ["fresh"]
    assert playlist.find_plugin("fresh", "Fresh Plugin").latest_refresh_time == "2026-05-26T07:05:00+00:00"
    assert device_config.write_count == 1


def test_theme_refresh_prefers_currently_displayed_playlist_plugin():
    tmp_path = make_test_dir("theme-current-plugin")
    device_config = FakeDeviceConfig(tmp_path)
    task = RefreshTask(device_config, display_manager=None)
    playlist = Playlist(
        "DailyDoseOfDay",
        "00:00",
        "24:00",
        plugins=[
            {
                "plugin_id": "one",
                "name": "One",
                "plugin_settings": {"id": "one"},
                "refresh": {"interval": 3600},
            },
            {
                "plugin_id": "two",
                "name": "Two",
                "plugin_settings": {"id": "two"},
                "refresh": {"interval": 3600},
            },
        ],
    )
    manager = PlaylistManager([playlist])
    latest = RefreshInfo(
        refresh_type="Playlist",
        plugin_id="one",
        playlist="DailyDoseOfDay",
        plugin_instance="One",
        refresh_time="2026-05-26T07:00:00+00:00",
        image_hash="old",
    )

    _playlist, plugin = task._determine_theme_refresh_plugin(
        manager,
        latest,
        datetime(2026, 5, 26, 7, 5, tzinfo=timezone.utc),
    )

    assert plugin.name == "One"


def test_theme_state_persists_after_forced_theme_refresh():
    tmp_path = make_test_dir("theme-persist")
    device_config = FakeDeviceConfig(tmp_path)
    device_config.config["active_theme"] = "night"
    task = RefreshTask(device_config, display_manager=None)
    current_dt = datetime(2026, 5, 26, 7, 5, tzinfo=timezone.utc)
    theme_context = {
        "mode": "day",
        "source": "weather",
        "reason": "sunrise/sunset",
        "date": "2026-05-26",
        "sunrise": "2026-05-26T05:50:00-07:00",
        "sunset": "2026-05-26T20:15:00-07:00",
    }

    assert task._has_theme_changed(theme_context)
    task._persist_active_theme(theme_context, current_dt)

    assert device_config.config["active_theme"] == "day"
    assert device_config.config["active_theme_info"]["source"] == "weather"


def test_theme_refresh_failure_suppresses_immediate_same_mode_retry():
    tmp_path = make_test_dir("theme-failure-cooldown")
    device_config = FakeDeviceConfig(tmp_path)
    device_config.config["active_theme"] = "day"
    device_config.config["theme_refresh_retry_cooldown_seconds"] = 120
    task = RefreshTask(device_config, display_manager=None)
    current_dt = datetime(2026, 5, 26, 22, 8, tzinfo=timezone.utc)
    theme_context = {
        "mode": "night",
        "source": "weather",
        "reason": "sunrise/sunset",
        "date": "2026-05-26",
    }

    assert task._has_theme_changed(theme_context, current_dt)
    task._mark_theme_refresh_failed(theme_context, current_dt, RuntimeError("screenshot timeout"))

    assert device_config.config["active_theme"] == "day"
    assert device_config.config["active_theme_refresh_failure"]["mode"] == "night"
    assert not task._has_theme_changed(theme_context, current_dt + timedelta(seconds=30))


def test_theme_refresh_failure_allows_retry_after_cooldown():
    tmp_path = make_test_dir("theme-failure-retry")
    device_config = FakeDeviceConfig(tmp_path)
    device_config.config["active_theme"] = "day"
    device_config.config["theme_refresh_retry_cooldown_seconds"] = 120
    task = RefreshTask(device_config, display_manager=None)
    current_dt = datetime(2026, 5, 26, 22, 8, tzinfo=timezone.utc)
    theme_context = {
        "mode": "night",
        "source": "weather",
        "reason": "sunrise/sunset",
        "date": "2026-05-26",
    }

    task._mark_theme_refresh_failed(theme_context, current_dt, RuntimeError("screenshot timeout"))

    assert task._has_theme_changed(theme_context, current_dt + timedelta(seconds=121))


def test_playlist_refresh_refreshes_rotating_newspaper_on_display():
    calls = []
    tmp_path = make_test_dir("newspaper-on-display")
    device_config = FakeDeviceConfig(tmp_path)
    playlist = Playlist(
        "DailyDoseOfDay",
        "00:00",
        "24:00",
        plugins=[
            {
                "plugin_id": "newspaper",
                "name": "ChinaDaily",
                "plugin_settings": {
                    "id": "rotating-news",
                    "mediaRotationMode": "rotate",
                },
                "refresh": {"scheduled": "15:00"},
                "latest_refresh_time": "2026-05-26T15:01:00+00:00",
            },
        ],
    )
    plugin_instance = playlist.find_plugin("newspaper", "ChinaDaily")
    old_image = tmp_path / "newspaper_ChinaDaily.png"
    Image.new("RGB", (1, 1), "black").save(old_image)

    action = PlaylistRefresh(playlist, plugin_instance)
    action.execute(
        FakePlugin(calls),
        device_config,
        datetime(2026, 5, 26, 16, 0, tzinfo=timezone.utc),
    )

    assert calls == ["rotating-news"]
    assert plugin_instance.latest_refresh_time == "2026-05-26T16:00:00+00:00"


def test_playlist_refresh_refreshes_backtothedate_on_display():
    calls = []
    tmp_path = make_test_dir("backtothedate-on-display")
    device_config = FakeDeviceConfig(tmp_path)
    playlist = Playlist(
        "DailyDoseOfDay",
        "00:00",
        "24:00",
        plugins=[
            {
                "plugin_id": "backtothedate",
                "name": "BacktotheDate",
                "plugin_settings": {"id": "backtothedate"},
                "refresh": {"scheduled": "15:00"},
                "latest_refresh_time": "2026-05-26T15:01:00+00:00",
            },
        ],
    )
    plugin_instance = playlist.find_plugin("backtothedate", "BacktotheDate")
    old_image = tmp_path / "backtothedate_BacktotheDate.png"
    Image.new("RGB", (1, 1), "black").save(old_image)

    action = PlaylistRefresh(playlist, plugin_instance)
    action.execute(
        FakePlugin(calls),
        device_config,
        datetime(2026, 5, 26, 16, 0, tzinfo=timezone.utc),
    )

    assert calls == ["backtothedate"]
    assert plugin_instance.latest_refresh_time == "2026-05-26T16:00:00+00:00"


def test_playlist_refresh_creates_plugin_image_directory_before_save():
    calls = []
    tmp_path = make_test_dir("create-image-dir")
    plugin_image_dir = tmp_path / "missing" / "plugins"
    device_config = FakeDeviceConfig(plugin_image_dir)
    playlist = Playlist(
        "DailyDoseOfDay",
        "00:00",
        "24:00",
        plugins=[
            {
                "plugin_id": "clock",
                "name": "Clock",
                "plugin_settings": {"id": "clock"},
                "refresh": {"interval": 300},
            },
        ],
    )
    plugin_instance = playlist.find_plugin("clock", "Clock")

    PlaylistRefresh(playlist, plugin_instance).execute(
        FakePlugin(calls),
        device_config,
        datetime(2026, 5, 26, 16, 0, tzinfo=timezone.utc),
    )

    assert calls == ["clock"]
    assert (plugin_image_dir / "clock_Clock.png").exists()


def test_get_current_datetime_falls_back_to_utc_for_invalid_timezone():
    tmp_path = make_test_dir("invalid-timezone")
    device_config = FakeDeviceConfig(tmp_path)
    device_config.config["timezone"] = "Not/AZone"
    task = RefreshTask(device_config, display_manager=None)

    current_dt = task._get_current_datetime()

    assert current_dt.tzinfo is not None
    assert current_dt.tzinfo.zone == "UTC"


class NonCacheablePlugin(DelegatingThemeWrapper):
    def __init__(self, calls):
        self.calls = calls

    def generate_image(self, settings, device_config):
        self.calls.append(settings["id"])
        image = Image.new("RGB", (1, 1), "red")
        image.info["inkypi_skip_cache"] = True
        return image


def test_refresh_due_plugin_instances_preserves_cache_for_non_cacheable_image(monkeypatch):
    calls = []
    tmp_path = make_test_dir("non-cacheable-cache")
    device_config = FakeDeviceConfig(tmp_path)
    task = RefreshTask(device_config, display_manager=None)
    playlist = Playlist(
        "DailyDoseOfDay",
        "00:00",
        "24:00",
        plugins=[
            {
                "plugin_id": "bambu_monitor",
                "name": "Bambu",
                "plugin_settings": {"id": "bambu"},
                "refresh": {"interval": 300},
                "latest_refresh_time": "2026-05-26T07:00:00+00:00",
            },
        ],
    )
    cache_path = tmp_path / "bambu_monitor_Bambu.png"
    Image.new("RGB", (2, 1), "black").save(cache_path)

    monkeypatch.setattr(
        "src.refresh_task.get_plugin_instance",
        lambda config: NonCacheablePlugin(calls),
    )

    task._refresh_due_plugin_instances(
        playlist,
        datetime(2026, 5, 26, 7, 5, tzinfo=timezone.utc),
    )

    assert calls == ["bambu"]
    assert playlist.find_plugin("bambu_monitor", "Bambu").latest_refresh_time == "2026-05-26T07:00:00+00:00"
    assert device_config.write_count == 0
    with Image.open(cache_path) as saved:
        assert saved.size == (2, 1)
        assert saved.getpixel((0, 0)) == (0, 0, 0)


def test_playlist_refresh_uses_previous_cache_for_non_cacheable_display_image():
    calls = []
    tmp_path = make_test_dir("non-cacheable-display")
    device_config = FakeDeviceConfig(tmp_path)
    playlist = Playlist(
        "DailyDoseOfDay",
        "00:00",
        "24:00",
        plugins=[
            {
                "plugin_id": "bambu_monitor",
                "name": "Bambu",
                "plugin_settings": {"id": "bambu"},
                "refresh": {"interval": 300},
                "latest_refresh_time": "2026-05-26T07:00:00+00:00",
            },
        ],
    )
    plugin_instance = playlist.find_plugin("bambu_monitor", "Bambu")
    cache_path = tmp_path / "bambu_monitor_Bambu.png"
    Image.new("RGB", (2, 1), "black").save(cache_path)

    image = PlaylistRefresh(playlist, plugin_instance, force=True).execute(
        NonCacheablePlugin(calls),
        device_config,
        datetime(2026, 5, 26, 7, 5, tzinfo=timezone.utc),
    )

    assert calls == ["bambu"]
    assert image.size == (2, 1)
    assert image.getpixel((0, 0)) == (0, 0, 0)
    assert plugin_instance.latest_refresh_time == "2026-05-26T07:00:00+00:00"
    with Image.open(cache_path) as saved:
        assert saved.size == (2, 1)
        assert saved.getpixel((0, 0)) == (0, 0, 0)


def test_playlist_worker_uses_previous_cache_for_non_cacheable_display(monkeypatch):
    tmp_path = make_test_dir("non-cacheable-worker-display")
    playlist = _runtime_playlist(
        _runtime_plugin_data(
            "bambu_monitor",
            "Bambu",
            latest_refresh_time="2999-01-01T00:00:00+00:00",
        )
    )
    task, device_config, _clock = _make_runtime_task(tmp_path, playlists=[playlist])
    device_config.config.update({"theme_mode": "day", "active_theme": "day"})
    cache_path = _write_runtime_cache(
        task,
        playlist.plugins[0],
        Image.new("RGB", (2, 1), "black"),
    )
    monkeypatch.setattr(
        "src.refresh_task.get_plugin_instance",
        lambda config: NonCacheablePlugin([]),
    )
    task.start()
    try:
        assert task.wait_until_waiting(timeout=1.0)
        job = task.submit_playlist_display(device_config.playlist_manager.first_instance_uuid())
        result = task.wait_for_job(job["id"], timeout=1.0)

        assert result["status"] == "completed"
        displayed = task.display_manager.calls[0][0]
        assert displayed.size == (2, 1)
        assert displayed.getpixel((0, 0)) == (0, 0, 0)
    finally:
        task.stop(join_timeout=1.0)
    with Image.open(cache_path) as saved:
        assert saved.size == (2, 1)
        assert saved.getpixel((0, 0)) == (0, 0, 0)


def test_cache_only_playlist_worker_does_not_evaluate_refresh_on_display(monkeypatch):
    tmp_path = make_test_dir("invalid-refresh-on-display-worker")
    plugin_data = _runtime_plugin_data(
        "base_plugin",
        "Invalid Explicit Boolean",
    )
    plugin_data["plugin_settings"]["refreshOnDisplay"] = "sometimes"
    playlist = _runtime_playlist(plugin_data)
    task, device_config, _clock = _make_runtime_task(tmp_path, playlists=[playlist])
    _write_runtime_cache(
        task,
        playlist.plugins[0],
        Image.new("RGB", (2, 1), "black"),
    )
    monkeypatch.setattr(
        "src.refresh_task.get_plugin_instance",
        lambda _config: pytest.fail("cache-only display instantiated a plugin"),
    )

    task.start()
    try:
        assert task.wait_until_waiting(timeout=1.0)
        job = task.submit_playlist_display(
            device_config.playlist_manager.first_instance_uuid(),
            force=False,
            display_cached_only=True,
        )
        result = task.wait_for_job(job["id"], timeout=1.0)

        assert result["status"] == "completed"
        assert task.display_manager.calls
    finally:
        task.stop(join_timeout=1.0)


def test_scheduler_selection_rejects_invalid_explicit_refresh_on_display():
    tmp_path = make_test_dir("invalid-refresh-on-display-selection")
    plugin_data = _runtime_plugin_data("base_plugin", "Invalid Selection Boolean")
    plugin_data["plugin_settings"]["refreshOnDisplay"] = "sometimes"
    playlist = _runtime_playlist(plugin_data)
    task, _device_config, _clock = _make_runtime_task(
        tmp_path,
        playlists=[playlist],
    )
    plugin = BasePlugin({"id": "base_plugin", "refresh_on_display": False})

    with pytest.raises(PluginSettingError, match="refreshOnDisplay"):
        task._plugin_wants_refresh_on_display(playlist.plugins[0], plugin=plugin)


class FailingPlugin(DelegatingThemeWrapper):
    def generate_image(self, settings, device_config):
        raise RuntimeError("boom")


def test_refresh_due_plugin_instances_limits_background_pass(monkeypatch):
    calls = []
    tmp_path = make_test_dir("due-cache-limit")
    device_config = FakeDeviceConfig(tmp_path)
    task = RefreshTask(device_config, display_manager=None)
    playlist = Playlist(
        "DailyDoseOfDay",
        "00:00",
        "24:00",
        plugins=[
            {
                "plugin_id": "one",
                "name": "One",
                "plugin_settings": {"id": "one"},
                "refresh": {"interval": 300},
                "latest_refresh_time": "2026-05-26T07:00:00+00:00",
            },
            {
                "plugin_id": "two",
                "name": "Two",
                "plugin_settings": {"id": "two"},
                "refresh": {"interval": 300},
                "latest_refresh_time": "2026-05-26T07:00:00+00:00",
            },
        ],
    )

    monkeypatch.setattr(
        "src.refresh_task.get_plugin_instance",
        lambda config: FakePlugin(calls),
    )

    task._refresh_due_plugin_instances(
        playlist,
        datetime(2026, 5, 26, 7, 5, tzinfo=timezone.utc),
        max_updates=1,
    )

    assert calls == ["one"]
    assert (tmp_path / "one_One.png").exists()
    assert not (tmp_path / "two_Two.png").exists()
    assert playlist.find_plugin("one", "One").latest_refresh_time == "2026-05-26T07:05:00+00:00"
    assert playlist.find_plugin("two", "Two").latest_refresh_time == "2026-05-26T07:00:00+00:00"
    assert device_config.write_count == 1


def test_failed_due_cache_refresh_records_failure_without_advancing_success(monkeypatch):
    tmp_path = make_test_dir("due-cache-failure-cooldown")
    device_config = FakeDeviceConfig(tmp_path)
    task = RefreshTask(
        device_config,
        display_manager=None,
        retry_registry=RetryRegistry(jitter=lambda delay: delay),
    )
    playlist = Playlist(
        "DailyDoseOfDay",
        "00:00",
        "24:00",
        plugins=[
            {
                "plugin_id": "bad",
                "name": "Bad Plugin",
                "plugin_settings": {"id": "bad"},
                "refresh": {"interval": 300},
                "latest_refresh_time": "2026-05-26T07:00:00+00:00",
            },
        ],
    )

    attempts = []

    def failing_plugin(_config):
        attempts.append("attempt")
        return FailingPlugin()

    monkeypatch.setattr("src.refresh_task.get_plugin_instance", failing_plugin)

    task._refresh_due_plugin_instances(
        playlist,
        datetime(2026, 5, 26, 7, 5, tzinfo=timezone.utc),
        max_updates=1,
    )

    instance = playlist.find_plugin("bad", "Bad Plugin")
    state = task.runtime_state.snapshot().instances[instance.instance_uuid]
    assert instance.latest_refresh_time == "2026-05-26T07:00:00+00:00"
    assert state.last_success_at is None
    assert state.last_failure_at == "2026-05-26T07:05:00+00:00"
    assert state.next_retry_at == "2026-05-26T07:05:30+00:00"
    assert not (tmp_path / "bad_Bad_Plugin.png").exists()
    assert device_config.write_count == 0
    attempts_before_cooldown_probe = list(attempts)

    task._refresh_due_plugin_instances(
        playlist,
        datetime(2026, 5, 26, 7, 5, 10, tzinfo=timezone.utc),
        max_updates=1,
    )
    assert attempts == attempts_before_cooldown_probe

    attempts_before_retry = len(attempts)
    task._refresh_due_plugin_instances(
        playlist,
        datetime(2026, 5, 26, 7, 5, 31, tzinfo=timezone.utc),
        max_updates=1,
    )
    assert len(attempts) > attempts_before_retry


def test_memory_maintenance_collects_and_trims_when_forced(monkeypatch):
    tmp_path = make_test_dir("memory-maintenance")
    device_config = FakeDeviceConfig(tmp_path)
    task = RefreshTask(device_config, display_manager=None)
    collected = []

    monkeypatch.setattr("src.refresh_task.gc.collect", lambda: collected.append("gc") or 7)
    monkeypatch.setattr(task, "_malloc_trim", lambda: True)
    monkeypatch.setattr(
        task,
        "_read_memory_stats",
        lambda: {"available_mb": 64.0, "memory_percent": 91.0, "swap_percent": 99.0},
    )

    result = task._run_memory_maintenance("test", force=True)

    assert collected == ["gc"]
    assert result["collected_objects"] == 7
    assert result["malloc_trim"] is True
    assert result["after"]["swap_percent"] == 99.0


def test_queue_command_final_memory_log_includes_command_and_process_usage(
    monkeypatch,
    caplog,
):
    current_dt = datetime(2026, 7, 29, 12, 0, tzinfo=timezone.utc)
    clock = RuntimeClock(wall=current_dt.timestamp())
    task, _device_config, _clock = _make_runtime_task(
        make_test_dir("command-final-memory-log"),
        clock=clock,
    )
    monkeypatch.setattr(task, "_execute_command", lambda _command: None)
    monkeypatch.setattr(
        task,
        "_read_memory_stats",
        lambda: {
            "available_mb": 256.0,
            "memory_percent": 50.0,
            "swap_percent": 10.0,
        },
    )
    monkeypatch.setattr(refresh_task_module.gc, "collect", lambda: 0)
    monkeypatch.setattr(task, "_malloc_trim", lambda: True)
    monkeypatch.setattr(
        refresh_task_module.psutil,
        "Process",
        lambda _pid: SimpleNamespace(
            memory_info=lambda: SimpleNamespace(
                rss=42 * 1024 * 1024,
                peak_wset=64 * 1024 * 1024,
            )
        ),
    )
    command = RefreshCommand.create(
        kind=CommandKind.CACHE_REFRESH,
        source=CommandSource.BACKGROUND,
        plugin_id="weather",
        instance_uuid="weather-instance",
        structural_generation=1,
        settings_revision=1,
        payload={"playlist_name": "Daily"},
        now_monotonic=clock.monotonic(),
        deadline_monotonic=clock.monotonic() + 180,
        priority=10,
        intent=RefreshIntent.DATA_REFRESH,
    )
    second_command = RefreshCommand.create(
        kind=CommandKind.CACHE_REFRESH,
        source=CommandSource.BACKGROUND,
        plugin_id="weather",
        instance_uuid="weather-instance",
        structural_generation=1,
        settings_revision=1,
        payload={"playlist_name": "Daily"},
        now_monotonic=clock.monotonic(),
        deadline_monotonic=clock.monotonic() + 180,
        priority=10,
        intent=RefreshIntent.DATA_REFRESH,
    )

    with caplog.at_level(logging.INFO, logger="src.refresh_task"):
        completed = _queue_and_process(task, command)
        second_completed = _queue_and_process(task, second_command)

    assert completed.job.status is JobStatus.SUCCEEDED
    assert second_completed.job.status is JobStatus.SUCCEEDED
    memory_logs = [
        record.getMessage()
        for record in caplog.records
        if "reason: refresh-command-finally" in record.getMessage()
    ]
    assert len(memory_logs) == 2
    for memory_log in memory_logs:
        assert "plugin_id: weather" in memory_log
        assert "source: background" in memory_log
        assert "intent: data_refresh" in memory_log
        assert "process_rss_mb: 42.0" in memory_log
        assert "process_hwm_mb: 64.0" in memory_log


@pytest.mark.parametrize(
    ("intent", "expected_force"),
    [
        (RefreshIntent.DATA_REFRESH, True),
        (RefreshIntent.PRESENTATION_REFRESH, True),
        (RefreshIntent.DISPLAY_CACHE, False),
    ],
)
def test_telegram_refresh_forces_command_final_memory_maintenance(
    monkeypatch,
    intent,
    expected_force,
):
    clock = RuntimeClock()
    task, _device_config, _clock = _make_runtime_task(
        make_test_dir(f"telegram-final-maintenance-{intent.value}"),
        playlists=[],
        clock=clock,
    )
    command = RefreshCommand.create(
        kind=CommandKind.CACHE_REFRESH,
        source=CommandSource.BACKGROUND,
        plugin_id="telegram_digest",
        instance_uuid="telegram-instance",
        structural_generation=1,
        settings_revision=1,
        payload={"playlist_name": "Daily"},
        now_monotonic=clock.monotonic(),
        deadline_monotonic=clock.monotonic() + 180,
        priority=10,
        intent=intent,
    )
    calls = []
    monkeypatch.setattr(task, "_execute_command", lambda _command: None)
    monkeypatch.setattr(
        task,
        "_run_memory_maintenance",
        lambda reason, force=False, *, command=None: calls.append(
            (reason, force, command)
        ),
    )

    completed = _queue_and_process(task, command)

    assert completed.job.status is JobStatus.SUCCEEDED
    assert calls == [("refresh-command-finally", expected_force, command)]


def test_memory_watchdog_first_swap_only_pressure_maintains_without_restart(monkeypatch):
    tmp_path = make_test_dir("memory-watchdog-swap")
    device_config = FakeDeviceConfig(tmp_path)
    device_config.config["memory_watchdog_min_available_mb"] = 70
    device_config.config["memory_watchdog_max_swap_percent"] = 98
    device_config.config["memory_watchdog_restart_min_interval_seconds"] = 1800
    task = RefreshTask(device_config, display_manager=None)
    captured = []
    maintenance = []

    memory = type("Memory", (), {"available": 200 * 1024 * 1024, "percent": 82.0})()
    swap = type("Swap", (), {"percent": 99.0})()
    monkeypatch.setattr("src.refresh_task.psutil.virtual_memory", lambda: memory)
    monkeypatch.setattr("src.refresh_task.psutil.swap_memory", lambda: swap)
    monkeypatch.setattr("src.refresh_task.time.monotonic", lambda: 1000.0)
    monkeypatch.setattr("src.refresh_task.time.time", lambda: 2000.0)
    monkeypatch.setattr(
        task,
        "_restart_process_for_memory_pressure",
        lambda stats, min_available_mb, max_swap_percent: captured.append(
            (stats, min_available_mb, max_swap_percent)
        ),
    )
    monkeypatch.setattr(
        task,
        "_run_memory_maintenance",
        lambda reason, force=False: maintenance.append((reason, force)),
    )

    assert task._memory_watchdog_should_restart() is False

    assert captured == []
    assert maintenance == [("memory-watchdog-pressure", True)]
    assert not (tmp_path / ".memory_watchdog_last_restart").exists()


def test_memory_watchdog_persistent_swap_only_pressure_never_restarts(monkeypatch):
    tmp_path = make_test_dir("memory-watchdog-persistent-swap-only")
    device_config = FakeDeviceConfig(tmp_path)
    device_config.config["memory_watchdog_pressure_confirmation_seconds"] = 15
    task = RefreshTask(device_config, display_manager=None)
    monotonic = [1000.0]
    captured = []
    maintenance = []
    monkeypatch.setattr(
        task,
        "_read_memory_stats",
        lambda: {
            "available_mb": 200.0,
            "memory_percent": 82.0,
            "swap_percent": 80.0,
        },
    )
    monkeypatch.setattr("src.refresh_task.time.monotonic", lambda: monotonic[0])
    monkeypatch.setattr(
        task,
        "_run_memory_maintenance",
        lambda reason, force=False: maintenance.append((reason, force)),
    )
    monkeypatch.setattr(
        task,
        "_restart_process_for_memory_pressure",
        lambda *args: captured.append(args),
    )

    assert task._memory_watchdog_should_restart() is False
    monotonic[0] += 60
    assert task._memory_watchdog_should_restart() is False
    assert captured == []
    assert maintenance == [("memory-watchdog-pressure", True)]


def test_memory_watchdog_recovery_starts_a_new_maintenance_episode(monkeypatch):
    task = RefreshTask(
        FakeDeviceConfig(make_test_dir("memory-watchdog-recovery-episode")),
        display_manager=None,
    )
    current = {
        "available_mb": 100.0,
        "memory_percent": 82.0,
        "swap_percent": 80.0,
    }
    monotonic = [1000.0]
    maintenance = []
    restarts = []
    monkeypatch.setattr(task, "_read_memory_stats", lambda: dict(current))
    monkeypatch.setattr("src.refresh_task.time.monotonic", lambda: monotonic[0])
    monkeypatch.setattr(
        task,
        "_run_memory_maintenance",
        lambda reason, force=False: maintenance.append((reason, force)),
    )
    monkeypatch.setattr(
        task,
        "_restart_process_for_memory_pressure",
        lambda *args: restarts.append(args),
    )

    assert task._memory_watchdog_should_restart() is False
    current["swap_percent"] = 10.0
    assert task._memory_watchdog_should_restart() is False
    monotonic[0] += 60
    current["swap_percent"] = 80.0
    assert task._memory_watchdog_should_restart() is False

    assert restarts == []
    assert maintenance == [
        ("memory-watchdog-pressure", True),
        ("memory-watchdog-pressure", True),
    ]


def test_memory_watchdog_unknown_sample_restarts_dual_pressure_confirmation(
    monkeypatch,
):
    device_config = FakeDeviceConfig(
        make_test_dir("memory-watchdog-unknown-sample-reset")
    )
    device_config.config["memory_watchdog_pressure_confirmation_seconds"] = 15
    task = RefreshTask(device_config, display_manager=None)
    monotonic = [1000.0]
    current = [
        {
            "available_mb": 100.0,
            "memory_percent": 82.0,
            "swap_percent": 80.0,
        }
    ]
    maintenance = []
    restarts = []
    monkeypatch.setattr(
        task,
        "_read_memory_stats",
        lambda: None if current[0] is None else dict(current[0]),
    )
    monkeypatch.setattr("src.refresh_task.time.monotonic", lambda: monotonic[0])
    monkeypatch.setattr(
        task,
        "_run_memory_maintenance",
        lambda reason, force=False: maintenance.append((reason, force)),
    )
    monkeypatch.setattr(
        task,
        "_restart_process_for_memory_pressure",
        lambda *args: restarts.append(args),
    )

    assert task._memory_watchdog_should_restart() is False
    monotonic[0] += 5
    current[0] = None
    assert task._memory_watchdog_should_restart() is False
    monotonic[0] += 15
    current[0] = {
        "available_mb": 100.0,
        "memory_percent": 82.0,
        "swap_percent": 80.0,
    }
    assert task._memory_watchdog_should_restart() is False

    assert restarts == []
    assert maintenance == [
        ("memory-watchdog-pressure", True),
        ("memory-watchdog-pressure", True),
    ]


def test_disabling_watchdog_resets_pressure_confirmation_episode(monkeypatch):
    device_config = FakeDeviceConfig(
        make_test_dir("memory-watchdog-disabled-reset")
    )
    device_config.config["memory_watchdog_pressure_confirmation_seconds"] = 15
    task = RefreshTask(device_config, display_manager=None)
    monotonic = [1000.0]
    maintenance = []
    restarts = []
    monkeypatch.setattr(
        task,
        "_read_memory_stats",
        lambda: {
            "available_mb": 100.0,
            "memory_percent": 82.0,
            "swap_percent": 80.0,
        },
    )
    monkeypatch.setattr("src.refresh_task.time.monotonic", lambda: monotonic[0])
    monkeypatch.setattr(
        task,
        "_run_memory_maintenance",
        lambda reason, force=False: maintenance.append((reason, force)),
    )
    monkeypatch.setattr(
        task,
        "_restart_process_for_memory_pressure",
        lambda *args: restarts.append(args),
    )

    assert task._memory_watchdog_should_restart() is False
    device_config.config["memory_watchdog_enabled"] = False
    monotonic[0] += 60
    assert task._memory_watchdog_should_restart() is False
    device_config.config["memory_watchdog_enabled"] = True
    assert task._memory_watchdog_should_restart() is False

    assert restarts == []
    assert maintenance == [
        ("memory-watchdog-pressure", True),
        ("memory-watchdog-pressure", True),
    ]


def test_swap_only_watchdog_pressure_keeps_ordinary_scheduler_lane_progressing(
    monkeypatch,
):
    current_dt = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)
    clock = RuntimeClock(wall=current_dt.timestamp())
    playlist = _runtime_playlist(
        _runtime_plugin_data("ordinary", "Ordinary", latest_refresh_time=None)
    )
    task, _device_config, _clock = _make_runtime_task(
        make_test_dir("memory-watchdog-swap-only-ordinary-progress"),
        playlists=[playlist],
        clock=clock,
    )
    sample = ResourceSample(available_mb=200.0, swap_percent=80.0)
    monkeypatch.setattr(
        task,
        "_read_memory_stats",
        lambda: {
            "available_mb": sample.available_mb,
            "memory_percent": 82.0,
            "swap_percent": sample.swap_percent,
        },
    )
    monkeypatch.setattr(task, "_resource_sample", lambda: sample)
    monkeypatch.setattr(task, "_run_memory_maintenance", lambda *_a, **_k: None)
    monkeypatch.setattr(task, "_sample_disk_pressure", lambda: DiskPressureTier.HEALTHY)
    monkeypatch.setattr(task, "_run_cache_lifecycle_maintenance", lambda _tier: None)
    monkeypatch.setattr(task, "_cache_lifecycle_should_yield", lambda: False)
    monkeypatch.setattr(task, "_select_prepared_display_retry_command", lambda _dt: None)
    monkeypatch.setattr(task, "_select_cached_display_command", lambda _dt: None)
    monkeypatch.setattr(task, "_get_current_datetime", lambda: current_dt)

    task._schedule_if_due()
    entry = task.refresh_queue.take(timeout=0)

    assert task.restart_request is None
    assert entry is not None
    assert entry.command.plugin_id == "ordinary"
    assert entry.command.intent is RefreshIntent.DATA_REFRESH


def test_watchdog_dual_pressure_confirmation_shortens_next_scheduler_probe(
    monkeypatch,
):
    clock = RuntimeClock(monotonic=1000.0, wall=2000.0)
    task, device_config, _clock = _make_runtime_task(
        make_test_dir("memory-watchdog-confirmation-probe"),
        playlists=[],
        clock=clock,
    )
    device_config.config["memory_watchdog_pressure_confirmation_seconds"] = 15
    monkeypatch.setattr("src.refresh_task.time.monotonic", clock.monotonic)
    monkeypatch.setattr(
        task,
        "_read_memory_stats",
        lambda: {
            "available_mb": 100.0,
            "memory_percent": 82.0,
            "swap_percent": 80.0,
        },
    )
    monkeypatch.setattr(task, "_run_memory_maintenance", lambda *_a, **_k: None)
    monkeypatch.setattr(task, "_sample_disk_pressure", lambda: DiskPressureTier.HEALTHY)
    monkeypatch.setattr(task, "_run_cache_lifecycle_maintenance", lambda _tier: None)
    monkeypatch.setattr(task, "_cache_lifecycle_should_yield", lambda: False)
    monkeypatch.setattr(task, "_select_prepared_display_retry_command", lambda _dt: None)
    monkeypatch.setattr(task, "_select_cached_display_command", lambda _dt: None)

    task._schedule_if_due()

    assert task.restart_request is None
    assert task.scheduler_state.snapshot().next_attempt_monotonic == 1015.0


def test_watchdog_confirmation_keeps_cached_display_work_running(monkeypatch):
    clock = RuntimeClock(monotonic=1000.0, wall=2000.0)
    task, device_config, _clock = _make_runtime_task(
        make_test_dir("memory-watchdog-cached-display-progress"),
        playlists=[],
        clock=clock,
    )
    device_config.config["memory_watchdog_pressure_confirmation_seconds"] = 15
    display_command = RefreshCommand.create(
        kind=CommandKind.DISPLAY,
        source=CommandSource.SCHEDULER,
        plugin_id="cached",
        payload={},
        now_monotonic=clock.monotonic(),
        deadline_monotonic=clock.monotonic() + 60,
        intent=RefreshIntent.DISPLAY_CACHE,
    )
    monkeypatch.setattr("src.refresh_task.time.monotonic", clock.monotonic)
    monkeypatch.setattr(
        task,
        "_read_memory_stats",
        lambda: {
            "available_mb": 100.0,
            "memory_percent": 82.0,
            "swap_percent": 80.0,
        },
    )
    monkeypatch.setattr(task, "_run_memory_maintenance", lambda *_a, **_k: None)
    monkeypatch.setattr(task, "_sample_disk_pressure", lambda: DiskPressureTier.HEALTHY)
    monkeypatch.setattr(
        task,
        "_select_prepared_display_retry_command",
        lambda _dt: display_command,
    )
    monkeypatch.setattr(
        task,
        "_select_cached_display_command",
        lambda _dt: pytest.fail("prepared cached display selection fell through"),
    )

    selected = task._schedule_if_due()
    entry = task.refresh_queue.take(timeout=0)

    assert selected is display_command
    assert entry is not None
    assert entry.command == display_command
    assert task.restart_request is None
    assert task.scheduler_state.snapshot().next_attempt_monotonic == 1015.0


def test_memory_restart_request_never_exits_from_refresh_worker(monkeypatch):
    tmp_path = make_test_dir("memory-restart-request")
    task = RefreshTask(FakeDeviceConfig(tmp_path), display_manager=None)
    exits = []
    monkeypatch.setattr("src.refresh_task.os._exit", lambda code: exits.append(code))
    stats = {"available_mb": 40.0, "swap_percent": 90.0}

    task._restart_process_for_memory_pressure(stats, 70.0, 75.0)

    assert exits == []
    assert task.restart_request == {
        "reason": "memory_pressure",
        "available_mb": 40.0,
        "min_available_mb": 70.0,
        "swap_percent": 90.0,
        "max_swap_percent": 75.0,
    }


def test_memory_watchdog_restarts_after_dual_pressure_is_confirmed(monkeypatch):
    tmp_path = make_test_dir("memory-watchdog-default-swap")
    device_config = FakeDeviceConfig(tmp_path)
    device_config.config["memory_watchdog_restart_min_interval_seconds"] = 1800
    device_config.config["memory_watchdog_pressure_confirmation_seconds"] = 15
    task = RefreshTask(device_config, display_manager=None)
    captured = []
    monotonic = [1000.0]

    memory = type("Memory", (), {"available": 100 * 1024 * 1024, "percent": 82.0})()
    swap = type("Swap", (), {"percent": 80.0})()
    monkeypatch.setattr("src.refresh_task.psutil.virtual_memory", lambda: memory)
    monkeypatch.setattr("src.refresh_task.psutil.swap_memory", lambda: swap)
    monkeypatch.setattr("src.refresh_task.time.monotonic", lambda: monotonic[0])
    monkeypatch.setattr("src.refresh_task.time.time", lambda: 2000.0)
    monkeypatch.setattr(
        task,
        "_restart_process_for_memory_pressure",
        lambda stats, min_available_mb, max_swap_percent: captured.append(
            (stats, min_available_mb, max_swap_percent)
        ),
    )

    assert task._memory_watchdog_should_restart() is False
    monotonic[0] += 15
    assert task._memory_watchdog_should_restart() is True
    monotonic[0] += 15
    assert task._memory_watchdog_should_restart() is False
    monotonic[0] += 15
    assert task._memory_watchdog_should_restart() is False

    assert len(captured) == 1
    assert captured[0][0]["available_mb"] == 100.0
    assert captured[0][0]["swap_percent"] == 80.0
    assert captured[0][1:] == (70.0, 75.0)


def test_memory_watchdog_escalates_persistent_low_memory_immediately(monkeypatch):
    task = RefreshTask(
        FakeDeviceConfig(make_test_dir("memory-watchdog-low-memory-immediate")),
        display_manager=None,
    )
    maintenance = []
    captured = []
    monkeypatch.setattr(
        task,
        "_read_memory_stats",
        lambda: {
            "available_mb": 60.0,
            "memory_percent": 92.0,
            "swap_percent": 20.0,
        },
    )
    monkeypatch.setattr("src.refresh_task.time.monotonic", lambda: 1000.0)
    monkeypatch.setattr("src.refresh_task.time.time", lambda: 2000.0)
    monkeypatch.setattr(
        task,
        "_run_memory_maintenance",
        lambda reason, force=False: maintenance.append((reason, force)),
    )
    monkeypatch.setattr(
        task,
        "_restart_process_for_memory_pressure",
        lambda *args: captured.append(args),
    )

    assert task._memory_watchdog_should_restart() is True

    assert maintenance == [("memory-watchdog-pressure", True)]
    assert len(captured) == 1
    assert captured[0][0]["available_mb"] == 60.0


def test_memory_watchdog_respects_persisted_restart_cooldown(monkeypatch):
    tmp_path = make_test_dir("memory-watchdog-cooldown")
    (tmp_path / ".memory_watchdog_last_restart").write_text("1990.0", encoding="utf-8")
    device_config = FakeDeviceConfig(tmp_path)
    device_config.config["memory_watchdog_min_available_mb"] = 70
    device_config.config["memory_watchdog_max_swap_percent"] = 98
    device_config.config["memory_watchdog_restart_min_interval_seconds"] = 60
    task = RefreshTask(device_config, display_manager=None)
    captured = []

    memory = type("Memory", (), {"available": 50 * 1024 * 1024, "percent": 95.0})()
    swap = type("Swap", (), {"percent": 99.0})()
    monkeypatch.setattr("src.refresh_task.psutil.virtual_memory", lambda: memory)
    monkeypatch.setattr("src.refresh_task.psutil.swap_memory", lambda: swap)
    monkeypatch.setattr("src.refresh_task.time.monotonic", lambda: 1000.0)
    monkeypatch.setattr("src.refresh_task.time.time", lambda: 2000.0)
    monkeypatch.setattr(task, "_restart_process_for_memory_pressure", lambda *args: captured.append(args))

    assert task._memory_watchdog_should_restart() is False
    assert captured == []

def test_cache_refresh_resource_pressure_ignores_swap_when_memory_is_available(monkeypatch):
    tmp_path = make_test_dir("cache-pressure")
    device_config = FakeDeviceConfig(tmp_path)
    device_config.config["background_cache_refresh_min_available_mb"] = 80
    device_config.config["background_cache_refresh_max_swap_percent"] = 70
    task = RefreshTask(device_config, display_manager=None)

    memory = type("Memory", (), {"available": 200 * 1024 * 1024})()
    swap = type("Swap", (), {"percent": 91.0})()
    monkeypatch.setattr("src.refresh_task.psutil.virtual_memory", lambda: memory)
    monkeypatch.setattr("src.refresh_task.psutil.swap_memory", lambda: swap)

    assert task._cache_refresh_under_resource_pressure() is False


def test_cache_refresh_resource_pressure_respects_low_available_memory(monkeypatch):
    tmp_path = make_test_dir("cache-pressure-low-memory")
    device_config = FakeDeviceConfig(tmp_path)
    device_config.config["background_cache_refresh_min_available_mb"] = 80
    device_config.config["background_cache_refresh_max_swap_percent"] = 70
    task = RefreshTask(device_config, display_manager=None)

    memory = type("Memory", (), {"available": 60 * 1024 * 1024})()
    swap = type("Swap", (), {"percent": 10.0})()
    monkeypatch.setattr("src.refresh_task.psutil.virtual_memory", lambda: memory)
    monkeypatch.setattr("src.refresh_task.psutil.swap_memory", lambda: swap)

    assert task._cache_refresh_under_resource_pressure() is True


def test_cache_refresh_default_pressure_gate_blocks_low_zero2w_headroom(monkeypatch):
    tmp_path = make_test_dir("cache-pressure-zero2w-default")
    device_config = FakeDeviceConfig(tmp_path)
    task = RefreshTask(device_config, display_manager=None)

    memory = type("Memory", (), {"available": 134 * 1024 * 1024})()
    swap = type("Swap", (), {"percent": 29.0})()
    monkeypatch.setattr("src.refresh_task.psutil.virtual_memory", lambda: memory)
    monkeypatch.setattr("src.refresh_task.psutil.swap_memory", lambda: swap)

    assert task._cache_refresh_under_resource_pressure() is True


def test_live_cache_refresh_can_ignore_swap_pressure(monkeypatch):
    tmp_path = make_test_dir("cache-pressure-live")
    device_config = FakeDeviceConfig(tmp_path)
    device_config.config["background_cache_refresh_min_available_mb"] = 80
    device_config.config["background_cache_refresh_max_swap_percent"] = 70
    task = RefreshTask(device_config, display_manager=None)

    memory = type("Memory", (), {"available": 200 * 1024 * 1024})()
    swap = type("Swap", (), {"percent": 82.0})()
    monkeypatch.setattr("src.refresh_task.psutil.virtual_memory", lambda: memory)
    monkeypatch.setattr("src.refresh_task.psutil.swap_memory", lambda: swap)

    assert task._cache_refresh_under_resource_pressure(allow_high_swap=True) is False


def test_live_cache_refresh_still_respects_low_memory(monkeypatch):
    tmp_path = make_test_dir("cache-pressure-live-low-memory")
    device_config = FakeDeviceConfig(tmp_path)
    device_config.config["background_cache_refresh_min_available_mb"] = 80
    device_config.config["background_cache_refresh_max_swap_percent"] = 70
    task = RefreshTask(device_config, display_manager=None)

    memory = type("Memory", (), {"available": 60 * 1024 * 1024})()
    swap = type("Swap", (), {"percent": 82.0})()
    monkeypatch.setattr("src.refresh_task.psutil.virtual_memory", lambda: memory)
    monkeypatch.setattr("src.refresh_task.psutil.swap_memory", lambda: swap)

    assert task._cache_refresh_under_resource_pressure(allow_high_swap=True) is True

def test_targeted_cache_refresh_passes_swap_pressure_override(monkeypatch):
    tmp_path = make_test_dir("cache-pressure-live-path")
    device_config = FakeDeviceConfig(tmp_path)
    task = RefreshTask(device_config, display_manager=None)
    task.running = True
    captured = []

    def fake_pressure(**kwargs):
        captured.append(kwargs)
        return True

    monkeypatch.setattr(task, "_cache_refresh_under_resource_pressure", fake_pressure)

    task._start_due_plugin_cache_refresh(
        playlist=None,
        current_dt=datetime(2026, 5, 26, 7, 5, tzinfo=timezone.utc),
        only_plugin_id="live_plugin",
    )
    task._start_due_plugin_cache_refresh(
        playlist=None,
        current_dt=datetime(2026, 5, 26, 7, 5, tzinfo=timezone.utc),
    )

    assert captured == [{"allow_high_swap": True}, {"allow_high_swap": False}]


def test_cache_refresh_in_progress_reflects_background_lock():
    tmp_path = make_test_dir("cache-refresh-in-progress")
    task = RefreshTask(FakeDeviceConfig(tmp_path), display_manager=None)

    assert task.cache_refresh_in_progress() is False
    assert task.cache_refresh_lock.acquire(blocking=False) is True
    try:
        assert task.cache_refresh_in_progress() is True
    finally:
        task.cache_refresh_lock.release()


def test_manual_update_in_progress_reflects_manual_refresh_lock():
    tmp_path = make_test_dir("manual-refresh-in-progress")
    task = RefreshTask(FakeDeviceConfig(tmp_path), display_manager=None)

    assert task.manual_update_in_progress() is False
    assert task.manual_refresh_lock.acquire(blocking=False) is True
    try:
        assert task.manual_update_in_progress() is True
    finally:
        task.manual_refresh_lock.release()


def test_background_cache_refresh_skips_while_manual_update_running(monkeypatch):
    tmp_path = make_test_dir("manual-refresh-skips-background-cache")
    task = RefreshTask(FakeDeviceConfig(tmp_path), display_manager=None)
    task.running = True
    pressure_checks = []
    monkeypatch.setattr(task, "_cache_refresh_under_resource_pressure", lambda **kwargs: pressure_checks.append(kwargs) or False)

    assert task.manual_refresh_lock.acquire(blocking=False) is True
    try:
        task._start_due_plugin_cache_refresh(
            playlist=None,
            current_dt=datetime(2026, 5, 26, 7, 5, tzinfo=timezone.utc),
        )
    finally:
        task.manual_refresh_lock.release()

    assert pressure_checks == []
    assert task.cache_refresh_in_progress() is False


def test_refresh_due_plugin_instances_stops_when_manual_update_starts(monkeypatch):
    calls = []
    tmp_path = make_test_dir("manual-refresh-stops-cache-pass")
    device_config = FakeDeviceConfig(tmp_path)
    task = RefreshTask(device_config, display_manager=None)
    playlist = Playlist(
        "DailyDoseOfDay",
        "00:00",
        "24:00",
        plugins=[
            {
                "plugin_id": "a_plugin",
                "name": "A Plugin",
                "plugin_settings": {"id": "a_plugin"},
                "refresh": {"interval": 300},
                "latest_refresh_time": "2026-05-26T07:00:00+00:00",
            },
            {
                "plugin_id": "b_plugin",
                "name": "B Plugin",
                "plugin_settings": {"id": "b_plugin"},
                "refresh": {"interval": 300},
                "latest_refresh_time": "2026-05-26T07:00:00+00:00",
            },
        ],
    )
    manual_states = iter([False, False, True])
    monkeypatch.setattr(task, "manual_update_in_progress", lambda: next(manual_states, True))
    monkeypatch.setattr("src.refresh_task.get_plugin_instance", lambda config: FakePlugin(calls))

    task._refresh_due_plugin_instances(
        playlist,
        datetime(2026, 5, 26, 7, 6, tzinfo=timezone.utc),
    )

    assert calls == ["a_plugin"]
    assert playlist.find_plugin("a_plugin", "A Plugin").latest_refresh_time == "2026-05-26T07:06:00+00:00"
    assert playlist.find_plugin("b_plugin", "B Plugin").latest_refresh_time == "2026-05-26T07:00:00+00:00"


def test_submit_manual_update_returns_job_without_waiting_for_inflight_refresh(monkeypatch):
    calls = []
    tmp_path = make_test_dir("async-manual-after-inflight")
    playlist = Playlist(
        "DailyDoseOfDay",
        "00:00",
        "24:00",
        plugins=[
            {
                "plugin_id": "live_radar",
                "name": "LiveRadar",
                "plugin_settings": {"id": "live_radar"},
                "refresh": {"interval": 999999999},
                "latest_refresh_time": "2999-01-01T00:00:00+00:00",
            },
        ],
    )
    plugin_instance = playlist.find_plugin("live_radar", "LiveRadar")
    Image.new("RGB", (1, 1), "black").save(tmp_path / plugin_instance.get_image_path())

    device_config = ThreadedDeviceConfig(tmp_path, playlist)
    device_config.config.update({"theme_mode": "day", "active_theme": "day"})
    display_manager = BlockingDisplayManager()
    task = RefreshTask(device_config, display_manager=display_manager)
    _write_runtime_cache(task, plugin_instance)
    monkeypatch.setattr(
        "src.refresh_task.get_plugin_instance",
        lambda config: CapturePlugin(calls),
    )

    task.start()
    try:
        assert display_manager.first_display_started.wait(timeout=1)

        started = time.monotonic()
        job = task.submit_manual_update(PlaylistRefresh(playlist, plugin_instance, force=True))
        elapsed = time.monotonic() - started

        assert elapsed < 0.2
        assert job["status"] == "queued"
        assert job["plugin_id"] == "live_radar"
        assert task.get_manual_update_job(job["id"])["status"] == "queued"

        display_manager.release_first_display.set()
        for _ in range(30):
            latest_job = task.get_manual_update_job(job["id"])
            if latest_job and latest_job["status"] == "completed":
                break
            time.sleep(0.05)

        latest_job = task.get_manual_update_job(job["id"])
        assert latest_job["status"] == "completed"
        assert {"id": "live_radar", "forceRefresh": True, "force_refresh": True, "_inkypiDisplayRender": True} in calls
    finally:
        display_manager.release_first_display.set()
        task.stop()


class RuntimeClock:
    def __init__(self, monotonic=0.0, wall=1000.0):
        self.monotonic_value = float(monotonic)
        self.wall_value = float(wall)

    def monotonic(self):
        return self.monotonic_value

    def wall_time(self):
        return self.wall_value

    def advance(self, seconds):
        self.monotonic_value += seconds
        self.wall_value += seconds


class RuntimeDeviceConfig(FakeDeviceConfig):
    def __init__(self, plugin_image_dir, playlists=(), refresh_info=None):
        super().__init__(plugin_image_dir)
        self.playlist_manager = PlaylistManager(list(playlists))
        self.refresh_info = refresh_info or RefreshInfo(
            refresh_time="2999-01-01T00:00:00+00:00",
            image_hash="current",
        )

    def get_playlist_manager(self):
        return self.playlist_manager

    def get_refresh_info(self):
        return self.refresh_info

    def get_resolution(self):
        return (32, 16)


class RecordingDisplayManager:
    def __init__(self):
        self.calls = []

    def display_image(self, image, image_settings=None):
        self.calls.append((image.copy(), list(image_settings or [])))


class TransactionRecordingDisplayManager:
    def __init__(self):
        self.calls = []
        self.bound_runtime_state = None
        self.recovery_context = None

    def bind_runtime_state(self, runtime_state):
        self.bound_runtime_state = runtime_state
        return object()

    def recover_display(self, *, task_context):
        self.recovery_context = task_context
        return None

    def display_image(
        self,
        image,
        image_settings=(),
        *,
        task_context=None,
        logical_target=None,
        instance_revision=None,
        force_hardware_write=False,
    ):
        self.calls.append(
            {
                "image": image.copy(),
                "image_settings": tuple(image_settings),
                "task_context": task_context,
                "logical_target": dict(logical_target or {}),
                "instance_revision": instance_revision,
                "force_hardware_write": force_hardware_write,
            }
        )


class BlockingRuntimePlugin(DelegatingThemeWrapper):
    def __init__(self, render_started, allow_render, calls=None, fail_first=False):
        self.render_started = render_started
        self.allow_render = allow_render
        self.calls = [] if calls is None else calls
        self.fail_first = fail_first
        self.config = {}

    def generate_image(self, settings, device_config):
        self.calls.append(dict(settings))
        self.render_started.set()
        assert self.allow_render.wait(1.0)
        if self.fail_first and len(self.calls) == 1:
            raise RuntimeError("theme render failed")
        return Image.new("RGB", (2, 1), "white")


class FalseyRefreshQueue(RefreshQueue):
    def __bool__(self):
        return False


def _runtime_playlist(*plugins, name="DailyDoseOfDay", start="00:00", end="24:00"):
    return Playlist(name, start, end, plugins=list(plugins))


def _runtime_plugin_data(
    plugin_id="runtime_plugin",
    name="Runtime Plugin",
    *,
    latest_refresh_time="2999-01-01T00:00:00+00:00",
    interval=3600,
):
    data = {
        "plugin_id": plugin_id,
        "name": name,
        "plugin_settings": {"id": plugin_id},
        "refresh": {"interval": interval},
    }
    if latest_refresh_time is not None:
        data["latest_refresh_time"] = latest_refresh_time
    return data


def _make_runtime_task(
    tmp_path,
    *,
    playlists=(),
    clock=None,
    cycle_seconds=300,
    **task_kwargs,
):
    clock = clock or RuntimeClock()
    device_config = RuntimeDeviceConfig(tmp_path, playlists)
    device_config.config["plugin_cycle_interval_seconds"] = cycle_seconds
    task = RefreshTask(
        device_config,
        RecordingDisplayManager(),
        clock=clock.monotonic,
        wall_clock=clock.wall_time,
        **task_kwargs,
    )
    return task, device_config, clock


class RecordingCacheLifecycle:
    def __init__(self, events, *, due=True):
        self.events = events
        self.is_due = due
        self.snapshot_value = SimpleNamespace(
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
        )

    def due(self, now_monotonic, tier):
        self.events.append(("due", now_monotonic, getattr(tier, "value", tier)))
        return self.is_due

    def maintain(self, _retention, **kwargs):
        self.events.append(("maintain", kwargs))
        return self.snapshot_value

    def snapshot(self):
        return self.snapshot_value


class RecordingLifecycleComponent:
    def __init__(self, events, name, temp_root=None):
        self.events = events
        self.name = name
        self.temp_root = temp_root

    def cleanup_abandoned_jobs(self, **kwargs):
        self.events.append((self.name, kwargs))
        return kwargs["allowance"].aggregate

    def maintenance(self, **kwargs):
        self.events.append((self.name, kwargs))
        return kwargs["allowance"].aggregate


def _disk_usage(total, used):
    return SimpleNamespace(total=total, used=used, free=total - used)


def _isolate_scheduler_for_lifecycle_test(monkeypatch, task):
    monkeypatch.setattr(task, "_memory_watchdog_should_restart", lambda: False)
    monkeypatch.setattr(task, "_select_prepared_display_retry_command", lambda _dt: None)
    monkeypatch.setattr(task, "_select_cached_display_command", lambda _dt: None)
    monkeypatch.setattr(task, "_select_independent_refresh_command", lambda _dt: None)


def test_lifecycle_maintenance_runs_on_idle_refresh_worker(monkeypatch):
    tmp_path = make_test_dir("runtime-lifecycle-idle-worker")
    clock = RuntimeClock()
    events = []
    lifecycle = RecordingCacheLifecycle(events)
    browser = RecordingLifecycleComponent(events, "browser", tmp_path / "browser")
    display = RecordingLifecycleComponent(events, "display")
    task, _device_config, _clock = _make_runtime_task(
        tmp_path,
        playlists=[],
        clock=clock,
        cache_lifecycle_manager=lifecycle,
        browser_renderer=browser,
        display_transaction=display,
        disk_usage=lambda _root: _disk_usage(10_000_000_000, 1_000_000_000),
    )
    _isolate_scheduler_for_lifecycle_test(monkeypatch, task)

    task._run_one_iteration_for_test()

    assert [event[0] for event in events] == ["due", "browser", "maintain", "display"]
    allowances = [event[1]["allowance"] for event in events[1:]]
    assert allowances[0] is allowances[1] is allowances[2]


def test_health_reads_only_old_or_new_frozen_lifecycle_snapshot_during_cleanup():
    tmp_path = make_test_dir("runtime-lifecycle-atomic-health-snapshot")
    events = []
    started = threading.Event()
    release = threading.Event()

    class BlockingBrowser:
        def cleanup_abandoned_jobs(self, **kwargs):
            for _index in range(17):
                assert kwargs["allowance"].consume_scan() is True
            started.set()
            assert release.wait(1.0)
            return kwargs["allowance"].aggregate

    lifecycle = RecordingCacheLifecycle(events)
    lifecycle.snapshot_value.ran_at = "2026-07-11T12:00:00+00:00"
    lifecycle.snapshot_value.scanned_entries = 5
    task, _device_config, _clock = _make_runtime_task(
        tmp_path,
        playlists=[],
        cache_lifecycle_manager=lifecycle,
        browser_renderer=BlockingBrowser(),
        display_transaction=None,
        disk_usage=lambda _root: _disk_usage(10_000_000_000, 1_000_000_000),
    )
    before = task.cache_lifecycle_snapshot()
    failures = []

    def maintain():
        try:
            task._run_cache_lifecycle_maintenance(DiskPressureTier.HEALTHY)
        except BaseException as error:
            failures.append(error)

    worker = threading.Thread(target=maintain)
    worker.start()
    try:
        assert started.wait(1.0)
        during = task.cache_lifecycle_snapshot()
        assert during is before
        assert during.ran_at == "2026-07-11T12:00:00+00:00"
        assert during.scanned_entries == 5
    finally:
        release.set()
        worker.join(1.0)

    assert worker.is_alive() is False
    assert failures == []
    after = task.cache_lifecycle_snapshot()
    assert after is not before
    assert after.ran_at != before.ran_at
    assert after.scanned_entries == 17


def test_pending_manual_job_preempts_healthy_lifecycle_cleanup(monkeypatch):
    tmp_path = make_test_dir("runtime-lifecycle-manual-preempts")
    events = []
    lifecycle = RecordingCacheLifecycle(events)
    task, _device_config, clock = _make_runtime_task(
        tmp_path,
        playlists=[],
        cache_lifecycle_manager=lifecycle,
        browser_renderer=RecordingLifecycleComponent(events, "browser", tmp_path / "browser"),
        display_transaction=RecordingLifecycleComponent(events, "display"),
        disk_usage=lambda _root: _disk_usage(10_000_000_000, 1_000_000_000),
    )
    command = RefreshCommand.create(
        kind=CommandKind.DISPLAY,
        source=CommandSource.MANUAL,
        plugin_id="manual",
        payload={"refresh_type": "Manual Update", "settings": {}},
        now_monotonic=clock.monotonic(),
        deadline_monotonic=clock.monotonic() + 60,
        intent=RefreshIntent.DISPLAY_CACHE,
    )
    task.refresh_queue.submit(command)
    monkeypatch.setattr(task, "_execute_command", lambda _command: None)

    task._run_one_iteration_for_test()

    assert events == []


def test_selected_display_preempts_cleanup_and_renderer_admission(monkeypatch):
    tmp_path = make_test_dir("runtime-lifecycle-display-preempts-probe")
    events = []
    task, device_config, clock = _make_runtime_task(
        tmp_path,
        playlists=[],
        cache_lifecycle_manager=RecordingCacheLifecycle(events),
        browser_renderer=RecordingLifecycleComponent(events, "browser"),
        display_transaction=RecordingLifecycleComponent(events, "display"),
        disk_usage=lambda _root: _disk_usage(10_000_000_000, 1_000_000_000),
    )
    device_config.config["display_triggered_refresh_enabled"] = True
    display_command = RefreshCommand.create(
        kind=CommandKind.DISPLAY,
        source=CommandSource.SCHEDULER,
        plugin_id="cached",
        payload={},
        now_monotonic=clock.monotonic(),
        deadline_monotonic=clock.monotonic() + 60,
        intent=RefreshIntent.DISPLAY_CACHE,
    )
    monkeypatch.setattr(task, "_memory_watchdog_should_restart", lambda: False)
    monkeypatch.setattr(
        task,
        "_select_prepared_display_retry_command",
        lambda _dt: display_command,
    )
    monkeypatch.setattr(
        task,
        "_select_cached_display_command",
        lambda _dt: pytest.fail("prepared display selection fell through"),
    )
    monkeypatch.setattr(
        task,
        "_select_independent_refresh_command",
        lambda _dt: pytest.fail("display probe also admitted a renderer"),
    )

    selected = task._schedule_if_due()

    assert selected is display_command
    assert task.refresh_queue.snapshot().depth == 1
    assert task.refresh_queue.take(timeout=0).command == display_command
    assert events == []


@pytest.mark.parametrize("initial_used", [9_000_000_000, 9_500_000_000])
def test_soft_or_hard_disk_maintains_then_resamples_before_renderer_admission(
    monkeypatch,
    initial_used,
):
    tmp_path = make_test_dir(f"runtime-lifecycle-pressure-{initial_used}")
    events = []
    samples = iter(
        [
            _disk_usage(10_000_000_000, initial_used),
            _disk_usage(10_000_000_000, 1_000_000_000),
        ]
    )
    sample_calls = []

    def sample(root):
        sample_calls.append(Path(root))
        events.append(("sample",))
        return next(samples)

    task, _device_config, _clock = _make_runtime_task(
        tmp_path,
        playlists=[],
        cache_lifecycle_manager=RecordingCacheLifecycle(events),
        browser_renderer=RecordingLifecycleComponent(events, "browser", tmp_path / "browser"),
        display_transaction=RecordingLifecycleComponent(events, "display"),
        disk_usage=sample,
    )
    _isolate_scheduler_for_lifecycle_test(monkeypatch, task)

    task._run_one_iteration_for_test()

    assert len(sample_calls) == 2
    assert [event[0] for event in events] == [
        "sample",
        "due",
        "browser",
        "maintain",
        "display",
        "sample",
    ]


@pytest.mark.parametrize(
    "intent",
    [
        RefreshIntent.DATA_REFRESH,
        RefreshIntent.PRESENTATION_REFRESH,
        RefreshIntent.LIVE_REFRESH,
        RefreshIntent.THEME_REDRAW,
        RefreshIntent.THEME_CATCHUP,
        RefreshIntent.MANUAL_RENDER,
    ],
)
def test_persistent_hard_disk_blocks_renderer_before_any_state_mutation(
    monkeypatch,
    intent,
    caplog,
):
    tmp_path = make_test_dir(f"runtime-lifecycle-hard-gate-{intent.value}")
    playlist = _runtime_playlist(
        _runtime_plugin_data("one", "One"),
        _runtime_plugin_data("two", "Two"),
    )
    playlist.current_plugin_index = 1
    playlist.plugin_rotation_queue = [playlist.plugins[0].instance_uuid]
    playlist.plugin_rotation_pool = [
        instance.instance_uuid for instance in playlist.plugins
    ]
    playlist.plugin_rotation_recent_history = [playlist.plugins[1].instance_uuid]
    events = []
    task, device_config, clock = _make_runtime_task(
        tmp_path,
        playlists=[playlist],
        cache_lifecycle_manager=RecordingCacheLifecycle(events),
        browser_renderer=RecordingLifecycleComponent(
            events,
            "browser",
            tmp_path / "browser-jobs",
        ),
        display_transaction=RecordingLifecycleComponent(events, "display"),
        disk_usage=lambda _root: _disk_usage(10_000_000_000, 9_500_000_000),
    )
    instance = playlist.plugins[0].snapshot()
    task._admission_state = AdmissionState(2, 17.0, 19.0)
    task.runtime_state.record_success(
        instance.instance_uuid,
        "2026-07-12T12:00:00+00:00",
        lane=RefreshLane.DATA,
        last_good_cache=LastGoodCacheState(
            theme_mode="night",
            structural_generation=instance.structural_generation,
            settings_revision=instance.settings_revision,
            promoted_at="2026-07-12T12:00:00+00:00",
        ),
    )
    task.runtime_state.request_presentation(
        instance.instance_uuid,
        PresentationRequestState(
            request_id=uuid.uuid4().hex,
            requested_at="2026-07-12T12:01:00+00:00",
            structural_generation=instance.structural_generation,
            settings_revision=instance.settings_revision,
            origin_theme_mode="night",
            origin_display_commit_id="display-sentinel",
        ),
    )
    task.retry_registry.mark_failure("sentinel", clock.monotonic())
    device_config.config.update(
        {
            "active_theme": "night",
            "active_theme_info": {"mode": "night", "sentinel": True},
        }
    )

    before_runtime = task.runtime_state.snapshot()
    before_admission = task._admission_state
    before_playlist = copy.deepcopy(playlist.to_dict())
    before_anchor = copy.deepcopy(device_config.refresh_info.to_dict())
    before_theme = copy.deepcopy(device_config.config)
    before_retry = task.retry_registry.snapshot()
    before_scheduler = task.scheduler_state.snapshot()

    source = (
        CommandSource.MANUAL
        if intent is RefreshIntent.MANUAL_RENDER
        else CommandSource.SCHEDULER
    )
    kind = (
        CommandKind.DISPLAY
        if intent is RefreshIntent.MANUAL_RENDER
        else CommandKind.CACHE_REFRESH
    )
    command = RefreshCommand.create(
        kind=kind,
        source=source,
        plugin_id=instance.plugin_id,
        instance_uuid=instance.instance_uuid,
        structural_generation=instance.structural_generation,
        settings_revision=instance.settings_revision,
        payload={"settings": {"sentinel": True}},
        now_monotonic=clock.monotonic(),
        deadline_monotonic=clock.monotonic() + 60,
        intent=intent,
    )
    submitted = task.refresh_queue.submit(command)
    entry = task.refresh_queue.take(timeout=0)
    monkeypatch.setattr(
        task,
        "_record_runtime_attempt",
        lambda _command: pytest.fail("hard-gated renderer recorded a lane attempt"),
    )
    monkeypatch.setattr(
        task,
        "_execute_command",
        lambda _command: pytest.fail("hard-gated renderer reached plugin/provider work"),
    )
    monkeypatch.setattr(task, "_run_memory_maintenance", lambda _reason: None)

    with caplog.at_level("INFO", logger=refresh_task_module.__name__):
        task._process_queue_entry(entry)

    finished = task.refresh_queue.get_job(submitted.id)
    assert finished.status is JobStatus.CANCELED
    assert finished.error_code == "disk_pressure_hard"
    assert "Refresh command started." not in caplog.text
    assert [event[0] for event in events] == [
        "due",
        "browser",
        "maintain",
        "display",
    ]
    assert task.runtime_state.snapshot() == before_runtime
    assert task._admission_state == before_admission
    assert playlist.to_dict() == before_playlist
    assert device_config.refresh_info.to_dict() == before_anchor
    assert device_config.config == before_theme
    assert task.retry_registry.snapshot() == before_retry
    assert task.scheduler_state.snapshot() == before_scheduler


def test_display_cache_is_allowed_while_disk_remains_hard(monkeypatch):
    tmp_path = make_test_dir("runtime-lifecycle-hard-allows-display-cache")
    events = []
    task, _device_config, clock = _make_runtime_task(
        tmp_path,
        playlists=[],
        cache_lifecycle_manager=RecordingCacheLifecycle(events),
        browser_renderer=RecordingLifecycleComponent(events, "browser"),
        display_transaction=RecordingLifecycleComponent(events, "display"),
        disk_usage=lambda _root: _disk_usage(10_000_000_000, 9_500_000_000),
    )
    command = RefreshCommand.create(
        kind=CommandKind.DISPLAY,
        source=CommandSource.MANUAL,
        plugin_id="cached",
        payload={},
        now_monotonic=clock.monotonic(),
        deadline_monotonic=clock.monotonic() + 60,
        intent=RefreshIntent.DISPLAY_CACHE,
    )
    submitted = task.refresh_queue.submit(command)
    entry = task.refresh_queue.take(timeout=0)
    executed = []
    monkeypatch.setattr(task, "_execute_command", executed.append)
    monkeypatch.setattr(task, "_run_memory_maintenance", lambda _reason: None)

    task._process_queue_entry(entry)

    assert executed == [command]
    assert task.refresh_queue.get_job(submitted.id).status is JobStatus.SUCCEEDED
    assert events == []


def test_hard_disk_gate_prevents_admission_state_selection(monkeypatch):
    tmp_path = make_test_dir("runtime-lifecycle-hard-before-admission")
    events = []
    task, _device_config, _clock = _make_runtime_task(
        tmp_path,
        playlists=[],
        cache_lifecycle_manager=RecordingCacheLifecycle(events),
        browser_renderer=RecordingLifecycleComponent(events, "browser"),
        display_transaction=RecordingLifecycleComponent(events, "display"),
        disk_usage=lambda _root: _disk_usage(10_000_000_000, 9_500_000_000),
    )
    task._admission_state = AdmissionState(3, 27.0, 29.0)
    before = task._admission_state
    monkeypatch.setattr(task, "_memory_watchdog_should_restart", lambda: False)
    monkeypatch.setattr(task, "_select_prepared_display_retry_command", lambda _dt: None)
    monkeypatch.setattr(task, "_select_cached_display_command", lambda _dt: None)
    monkeypatch.setattr(
        task,
        "_select_independent_refresh_command",
        lambda _dt: pytest.fail("hard disk reached admission chooser"),
    )

    task._schedule_if_due()

    assert task._admission_state == before
    assert [event[0] for event in events] == [
        "due",
        "browser",
        "maintain",
        "display",
    ]


def test_cache_lifecycle_yields_to_stop_or_new_queue_work():
    tmp_path = make_test_dir("runtime-lifecycle-yield-signals")
    task, _device_config, clock = _make_runtime_task(tmp_path, playlists=[])

    assert task._cache_lifecycle_should_yield() is False
    command = RefreshCommand.create(
        kind=CommandKind.DISPLAY,
        source=CommandSource.MANUAL,
        plugin_id="queued",
        payload={},
        now_monotonic=clock.monotonic(),
        deadline_monotonic=clock.monotonic() + 60,
        intent=RefreshIntent.DISPLAY_CACHE,
    )
    task.refresh_queue.submit(command)
    assert task._cache_lifecycle_should_yield() is True

    task.refresh_queue.take(timeout=0)
    task.stop_event.set()
    assert task._cache_lifecycle_should_yield() is True


@pytest.mark.parametrize("preemption", ["queue", "stop"])
def test_cleanup_preemption_aborts_remaining_components_and_renderer_admission(
    monkeypatch,
    preemption,
):
    tmp_path = make_test_dir(f"runtime-lifecycle-preemption-{preemption}")
    events = []

    class PreemptingBrowser:
        action = None

        def cleanup_abandoned_jobs(self, **kwargs):
            events.append(("browser", kwargs))
            self.action()
            assert kwargs["allowance"].consume_scan() is False
            return kwargs["allowance"].aggregate

    browser = PreemptingBrowser()
    task, _device_config, clock = _make_runtime_task(
        tmp_path,
        playlists=[],
        cache_lifecycle_manager=RecordingCacheLifecycle(events),
        browser_renderer=browser,
        display_transaction=RecordingLifecycleComponent(events, "display"),
        disk_usage=lambda _root: _disk_usage(10_000_000_000, 1_000_000_000),
    )
    queued_command = RefreshCommand.create(
        kind=CommandKind.DISPLAY,
        source=CommandSource.MANUAL,
        plugin_id="preempting-display",
        payload={},
        now_monotonic=clock.monotonic(),
        deadline_monotonic=clock.monotonic() + 60,
        intent=RefreshIntent.DISPLAY_CACHE,
    )
    browser.action = (
        (lambda: task.refresh_queue.submit(queued_command))
        if preemption == "queue"
        else task.stop_event.set
    )
    monkeypatch.setattr(task, "_memory_watchdog_should_restart", lambda: False)
    monkeypatch.setattr(task, "_select_prepared_display_retry_command", lambda _dt: None)
    monkeypatch.setattr(task, "_select_cached_display_command", lambda _dt: None)
    monkeypatch.setattr(
        task,
        "_select_independent_refresh_command",
        lambda _dt: pytest.fail("cleanup preemption still admitted a renderer"),
    )

    task._schedule_if_due()

    assert [event[0] for event in events] == ["due", "browser"]
    assert task.refresh_queue.snapshot().depth == (1 if preemption == "queue" else 0)


def test_cleanup_exception_is_redacted_and_does_not_create_scheduler_backoff(
    monkeypatch,
    caplog,
):
    tmp_path = make_test_dir("runtime-lifecycle-cleanup-exception")
    lifecycle = RecordingCacheLifecycle([])

    class ExplodingBrowser:
        def cleanup_abandoned_jobs(self, **_kwargs):
            raise RuntimeError("secret C:/private/uuid-cache-path")

    task, _device_config, _clock = _make_runtime_task(
        tmp_path,
        playlists=[],
        cache_lifecycle_manager=lifecycle,
        browser_renderer=ExplodingBrowser(),
        display_transaction=RecordingLifecycleComponent([], "display"),
        disk_usage=lambda _root: _disk_usage(10_000_000_000, 1_000_000_000),
    )
    _isolate_scheduler_for_lifecycle_test(monkeypatch, task)
    before_retry = task.retry_registry.snapshot()
    before_failure = task.scheduler_state.snapshot().last_failure_wall

    with caplog.at_level("WARNING"):
        task._schedule_if_due()

    snapshot = task.cache_lifecycle_snapshot()
    assert snapshot.error_count == lifecycle.snapshot().error_count + 1
    assert task.retry_registry.snapshot() == before_retry
    assert task.scheduler_state.snapshot().last_failure_wall == before_failure
    assert "secret C:/private" not in caplog.text
    assert "uuid-cache-path" not in caplog.text


def test_default_lifecycle_uses_runtime_cache_browser_and_display_roots(monkeypatch):
    tmp_path = make_test_dir("runtime-lifecycle-actual-roots")
    events = []
    browser = RecordingLifecycleComponent(
        events,
        "browser",
        tmp_path / "actual-browser-root",
    )
    display = RecordingLifecycleComponent(events, "display")
    display_manager = RecordingDisplayManager()
    display_manager.transaction = display
    device_config = RuntimeDeviceConfig(tmp_path, [])
    clock = RuntimeClock()
    monkeypatch.setattr(refresh_task_module, "get_browser_renderer", lambda: browser)
    task = RefreshTask(
        device_config,
        display_manager,
        clock=clock.monotonic,
        wall_clock=clock.wall_time,
        disk_usage=lambda _root: _disk_usage(10_000_000_000, 1_000_000_000),
    )
    _isolate_scheduler_for_lifecycle_test(monkeypatch, task)

    task._schedule_if_due()

    assert task.cache_lifecycle.plugin_image_dir == Path(tmp_path).resolve()
    assert task._browser_renderer is browser
    assert task._browser_renderer.temp_root == tmp_path / "actual-browser-root"
    assert task._display_transaction is display
    assert [event[0] for event in events] == ["browser", "display"]


@pytest.mark.parametrize(
    ("plugin_mode", "device_mode", "expected_mode"),
    [("night", "day", "night"), ("auto", "day", "day")],
)
def test_playlist_command_pins_full_plugin_context_and_theme_cache_suffix(
    plugin_mode,
    device_mode,
    expected_mode,
):
    tmp_path = make_test_dir(f"runtime-theme-context-{plugin_mode}")
    plugin_data = _runtime_plugin_data("themed_plugin", "Themed Plugin")
    plugin_data["plugin_settings"]["themeMode"] = plugin_mode
    playlist = _runtime_playlist(plugin_data)
    task, device_config, _clock = _make_runtime_task(tmp_path, playlists=[playlist])
    device_config.config["theme_mode"] = device_mode
    device_config.get_plugin = lambda plugin_id: {
        "id": plugin_id,
        "_manifest": _theme_manifest(plugin_id),
    }
    instance = playlist.plugins[0].snapshot()

    command = task._playlist_command(
        playlist.name,
        instance,
        source=CommandSource.SCHEDULER,
        intent=RefreshIntent.DATA_REFRESH,
        current_dt=datetime(2026, 7, 11, 12, 0, tzinfo=timezone.utc),
    )

    context = command.payload["resolved_theme_context"]
    assert context["requested_mode"] == plugin_mode
    assert context["mode"] == expected_mode
    assert context["palette"]["background"] == (
        (16, 24, 32) if expected_mode == "night" else (247, 241, 227)
    )
    expected_name = task._cache_identity_filename(
        instance.instance_uuid,
        instance.structural_generation,
        instance.settings_revision,
        expected_mode,
    )
    assert Path(task._snapshot_cache_path(instance, expected_mode)).name == expected_name
    assert Path(task._staging_cache_path(instance, expected_mode)).name == expected_name


def test_theme_unaware_command_keeps_exact_legacy_unsuffixed_cache_identity():
    tmp_path = make_test_dir("runtime-theme-unaware-cache")
    playlist = _runtime_playlist(_runtime_plugin_data("plain_plugin", "Plain Plugin"))
    task, device_config, _clock = _make_runtime_task(tmp_path, playlists=[playlist])
    device_config.get_plugin = lambda plugin_id: {
        "id": plugin_id,
        "_manifest": _theme_manifest(plugin_id, supported=False),
    }
    instance = playlist.plugins[0].snapshot()

    command = task._playlist_command(
        playlist.name,
        instance,
        source=CommandSource.BACKGROUND,
        intent=RefreshIntent.DATA_REFRESH,
    )

    assert "resolved_theme_context" not in command.payload
    prefix = task._cache_identity_prefix(instance.instance_uuid)
    assert task._cache_identity_filename(
        instance.instance_uuid,
        instance.structural_generation,
        instance.settings_revision,
    ) == (
        f"{prefix}-{instance.structural_generation}-"
        f"{instance.settings_revision}.png"
    )


class RecordingThemeWrapperPlugin:
    def __init__(self, config):
        self.config = config
        self.contexts = []

    def render_themed_image(
        self,
        settings,
        device_config,
        *,
        theme_render_only=False,
        resolved_theme_context=None,
    ):
        self.contexts.append(resolved_theme_context)
        return Image.new("RGB", (2, 1), "white")


def _canonical_runtime_theme(
    mode,
    *,
    date="2026-07-12",
    sunrise="2026-07-12T05:56:00-07:00",
    sunset="2026-07-12T20:31:00-07:00",
):
    palette = {
        "background": [0, 0, 0] if mode == "night" else [255, 255, 255],
        "panel": [0, 0, 0] if mode == "night" else [255, 255, 255],
        "ink": [255, 255, 255] if mode == "night" else [0, 0, 0],
        "muted": [194, 196, 202] if mode == "night" else [74, 78, 84],
        "rule": [46, 48, 56] if mode == "night" else [185, 188, 194],
        "accent": [107, 204, 255] if mode == "night" else [24, 92, 150],
    }
    return {
        "requested_mode": "auto",
        "mode": mode,
        "source": "weather",
        "reason": "sunrise/sunset",
        "date": date,
        "timezone": "America/Los_Angeles",
        "sunrise": sunrise,
        "sunset": sunset,
        "palette": palette,
        "css": {
            key: "#%02x%02x%02x" % tuple(value)
            for key, value in palette.items()
        },
    }


class EffectiveWeatherWrapperPlugin:
    def __init__(self, config, effective_context, *, fail=False):
        self.config = config
        self.effective_context = effective_context
        self.fail = fail
        self.calls = []

    def wants_refresh_on_display(self, _settings):
        return False

    def render_themed_image(
        self,
        settings,
        device_config,
        *,
        theme_render_only=False,
        resolved_theme_context=None,
    ):
        self.calls.append(
            {
                "theme_render_only": theme_render_only,
                "resolved": {
                    "mode": (resolved_theme_context or {}).get("mode"),
                    "source": (resolved_theme_context or {}).get("source"),
                },
            }
        )
        if self.fail:
            raise RuntimeError("weather theme render failed")
        image = Image.new("RGB", device_config.get_resolution(), "black")
        image.info["inkypi_theme_mode"] = self.effective_context.get("mode")
        image.info[EFFECTIVE_THEME_CONTEXT_INFO_KEY] = copy.deepcopy(
            self.effective_context
        )
        return image


def _weather_effective_runtime(
    name,
    monkeypatch,
    effective_context,
    *,
    plugin_theme_mode="auto",
    device_theme_mode="day",
):
    tmp_path = make_test_dir(name)
    plugin_data = _runtime_plugin_data(
        "weather",
        "Weather",
        latest_refresh_time="2999-01-01T00:00:00+00:00",
        interval=300,
    )
    plugin_data["plugin_settings"]["themeMode"] = plugin_theme_mode
    playlist = _runtime_playlist(plugin_data)
    task, device_config, _clock = _make_runtime_task(
        tmp_path,
        playlists=[playlist],
    )
    device_config.config.update(
        {
            "theme_mode": device_theme_mode,
            "active_theme": "day",
            "timezone": "America/Los_Angeles",
        }
    )
    plugin_config = {
        "id": "weather",
        "_manifest": _theme_manifest("weather"),
    }
    device_config.get_plugin = lambda _plugin_id: plugin_config
    plugin = EffectiveWeatherWrapperPlugin(plugin_config, effective_context)
    monkeypatch.setattr(
        "src.refresh_task.get_plugin_instance",
        lambda _config: plugin,
    )
    current_dt = datetime(
        2026,
        7,
        12,
        12,
        0,
        tzinfo=timezone(timedelta(hours=-7)),
    )
    monkeypatch.setattr(task, "_get_current_datetime", lambda: current_dt)
    instance = playlist.plugins[0].snapshot()
    task.runtime_state.set_display_state(
        "committed",
        instance_uuid=instance.instance_uuid,
        changed_at=current_dt.isoformat(),
    )
    command = task._playlist_command(
        playlist.name,
        instance,
        source=CommandSource.BACKGROUND,
        intent=RefreshIntent.DATA_REFRESH,
        force=False,
        display_cached_only=False,
        priority=10,
        kind=CommandKind.CACHE_REFRESH,
        current_dt=current_dt,
    )
    expected_queued_mode = (
        plugin_theme_mode
        if plugin_theme_mode in {"day", "night"}
        else "day"
    )
    assert command.payload["resolved_theme_context"]["mode"] == expected_queued_mode
    return task, device_config, playlist, instance, plugin, command, current_dt


def test_weather_data_refresh_promotes_under_effective_not_initial_mode(
    monkeypatch,
):
    effective = _canonical_runtime_theme("night")
    (
        task,
        device_config,
        _playlist,
        instance,
        _plugin,
        command,
        current_dt,
    ) = _weather_effective_runtime(
        "weather-effective-cache-identity",
        monkeypatch,
        effective,
    )

    result = task._execute_command(command)

    night_path = Path(task._snapshot_cache_path(instance, "night"))
    assert night_path.exists()
    assert not Path(task._snapshot_cache_path(instance, "day")).exists()
    assert not Path(task._staging_cache_path(instance, "night")).exists()
    assert result.getpixel((0, 0)) == (0, 0, 0)
    assert result.info["inkypi_theme_mode"] == "night"
    state = task.runtime_state.snapshot().instances[instance.instance_uuid]
    assert state.data.last_success_at == current_dt.isoformat()
    assert state.last_good_cache.theme_mode == "night"
    assert device_config.config["active_theme"] == "day"


def test_weather_effective_context_controls_stage_and_last_good_record(
    monkeypatch,
):
    effective = _canonical_runtime_theme("night")
    (
        task,
        _device_config,
        _playlist,
        instance,
        _plugin,
        command,
        _current_dt,
    ) = _weather_effective_runtime(
        "weather-effective-stage-and-last-good",
        monkeypatch,
        effective,
    )
    original_stage_path = task._staging_cache_path
    staged_modes = []

    def observe_stage_path(observed_instance, mode):
        staged_modes.append(mode)
        return original_stage_path(observed_instance, mode)

    monkeypatch.setattr(task, "_staging_cache_path", observe_stage_path)

    task._execute_command(command)

    state = task.runtime_state.snapshot().instances[instance.instance_uuid]
    catalog_entry = task.cache_catalog.resolve(instance, "night", state)
    assert staged_modes == ["night"]
    assert catalog_entry is not None
    assert catalog_entry.theme_mode == "night"
    assert state.last_good_cache.theme_mode == "night"


def test_active_theme_info_contains_timezone_and_exact_weather_projection(
    monkeypatch,
):
    effective = _canonical_runtime_theme("night")
    (
        task,
        device_config,
        _playlist,
        _instance,
        _plugin,
        command,
        _current_dt,
    ) = _weather_effective_runtime(
        "weather-effective-active-info",
        monkeypatch,
        effective,
    )

    task._execute_command(command)

    info = device_config.config["active_theme_info"]
    shared = ("source", "date", "timezone", "sunrise", "sunset")
    assert {key: info[key] for key in shared} == {
        key: effective[key]
        for key in shared
    }
    assert info["mode"] == "night"
    assert device_config.config["active_theme"] == "day"
    assert device_config.write_count == 1


def test_forced_weather_effective_mode_does_not_replace_global_theme_info(
    monkeypatch,
):
    effective = _canonical_runtime_theme("night")
    effective["requested_mode"] = "night"
    (
        task,
        device_config,
        _playlist,
        instance,
        _plugin,
        command,
        current_dt,
    ) = _weather_effective_runtime(
        "weather-forced-effective-stays-local",
        monkeypatch,
        effective,
        plugin_theme_mode="night",
        device_theme_mode="auto",
    )
    shared_global = _canonical_runtime_theme("day")
    device_config.config["active_theme_info"] = task._theme_status_info(
        shared_global,
        current_dt,
    )
    before = copy.deepcopy(device_config.config["active_theme_info"])

    task._execute_command(command)

    assert Path(task._snapshot_cache_path(instance, "night")).exists()
    assert not Path(task._snapshot_cache_path(instance, "day")).exists()
    assert device_config.config["active_theme_info"] == before
    assert device_config.config["active_theme"] == "day"


def _weather_status_probe_runtime(name, monkeypatch, theme_context):
    tmp_path = make_test_dir(name)
    plugin_data = _runtime_plugin_data(
        "weather",
        "Weather",
        latest_refresh_time="2999-01-01T00:00:00+00:00",
    )
    plugin_data["plugin_settings"]["themeMode"] = "auto"
    playlist = _runtime_playlist(plugin_data)
    task, device_config, _clock = _make_runtime_task(
        tmp_path,
        playlists=[playlist],
    )
    device_config.config.update(
        {
            "theme_mode": theme_context["mode"],
            "active_theme": "day",
            "timezone": theme_context["timezone"],
        }
    )
    plugin_config = {
        "id": "weather",
        "_manifest": _theme_manifest("weather"),
    }
    device_config.get_plugin = lambda _plugin_id: plugin_config
    monkeypatch.setattr(
        "src.refresh_task.get_theme_context",
        lambda *_args, **_kwargs: copy.deepcopy(theme_context),
    )
    monkeypatch.setattr(
        "src.refresh_task.get_plugin_instance",
        lambda _config: pytest.fail("metadata-only probe instantiated a plugin"),
    )
    monkeypatch.setattr(
        task,
        "_resource_sample",
        lambda: ResourceSample(available_mb=512, swap_percent=0),
    )
    instance = playlist.plugins[0].snapshot()
    for mode in ("day", "night"):
        _write_runtime_theme_cache(
            task,
            instance,
            mode,
            Image.new("RGB", (32, 16), "white" if mode == "day" else "black"),
        )
    current_dt = datetime(
        2026,
        7,
        12,
        12 if theme_context["mode"] == "day" else 22,
        0,
        tzinfo=timezone(timedelta(hours=-7)),
    )
    task.runtime_state.set_display_state(
        "committed",
        instance_uuid=instance.instance_uuid,
        changed_at=current_dt.isoformat(),
    )
    for lane in RefreshLane:
        task.runtime_state.record_success(
            instance.instance_uuid,
            (current_dt - timedelta(minutes=5)).isoformat(),
            lane=lane,
        )
    playlist.current_plugin_index = 0
    playlist.plugin_rotation_queue = [instance.instance_uuid]
    playlist.plugin_rotation_pool = [instance.instance_uuid]
    playlist.plugin_rotation_recent_history = [instance.instance_uuid]
    return task, device_config, playlist, instance, current_dt


def test_same_mode_new_astronomy_updates_info_without_render_or_provider(
    monkeypatch,
):
    context = _canonical_runtime_theme(
        "day",
        sunrise="2026-07-12T05:57:00-07:00",
        sunset="2026-07-12T20:30:00-07:00",
    )
    task, device_config, playlist, instance, current_dt = (
        _weather_status_probe_runtime(
            "weather-status-same-mode",
            monkeypatch,
            context,
        )
    )
    before_rotation = (
        playlist.current_plugin_index,
        list(playlist.plugin_rotation_queue),
        list(playlist.plugin_rotation_pool),
        list(playlist.plugin_rotation_recent_history),
    )
    before_anchor = device_config.refresh_info.to_dict()
    before_lanes = dict(task.runtime_state.snapshot().instances)
    before_cache = {
        mode: Path(task._snapshot_cache_path(instance, mode)).read_bytes()
        for mode in ("day", "night")
    }
    monkeypatch.setattr(
        task,
        "_staging_cache_path",
        lambda *_args, **_kwargs: pytest.fail(
            "metadata-only sync attempted cache promotion"
        ),
    )

    assert task._select_independent_refresh_command(current_dt) is None

    info = device_config.config["active_theme_info"]
    assert info == {
        "mode": "day",
        "source": "weather",
        "reason": "sunrise/sunset",
        "date": "2026-07-12",
        "timezone": "America/Los_Angeles",
        "sunrise": "2026-07-12T05:57:00-07:00",
        "sunset": "2026-07-12T20:30:00-07:00",
        "updated_at": current_dt.isoformat(),
    }
    assert device_config.config["active_theme"] == "day"
    assert device_config.write_count == 1
    assert task._select_independent_refresh_command(
        current_dt + timedelta(minutes=1)
    ) is None
    assert device_config.write_count == 1
    assert device_config.config["active_theme_info"] == info
    assert before_rotation == (
        playlist.current_plugin_index,
        list(playlist.plugin_rotation_queue),
        list(playlist.plugin_rotation_pool),
        list(playlist.plugin_rotation_recent_history),
    )
    assert device_config.refresh_info.to_dict() == before_anchor
    assert dict(task.runtime_state.snapshot().instances) == before_lanes
    assert {
        mode: Path(task._snapshot_cache_path(instance, mode)).read_bytes()
        for mode in ("day", "night")
    } == before_cache
    assert task.display_manager.calls == []


def test_info_only_sync_preserves_rotation_anchor_random_bag_and_all_lanes(
    monkeypatch,
):
    context = _canonical_runtime_theme(
        "day",
        sunrise="2026-07-12T05:58:00-07:00",
        sunset="2026-07-12T20:29:00-07:00",
    )
    task, device_config, playlist, instance, current_dt = (
        _weather_status_probe_runtime(
            "weather-status-preserves-scheduler-state",
            monkeypatch,
            context,
        )
    )
    before_anchor = device_config.refresh_info.to_dict()
    before_rotation = (
        playlist.current_plugin_index,
        tuple(playlist.plugin_rotation_queue),
        tuple(playlist.plugin_rotation_pool),
        tuple(playlist.plugin_rotation_recent_history),
    )
    before_runtime = task.runtime_state.snapshot().instances[instance.instance_uuid]

    assert task._select_independent_refresh_command(current_dt) is None

    assert device_config.refresh_info.to_dict() == before_anchor
    assert before_rotation == (
        playlist.current_plugin_index,
        tuple(playlist.plugin_rotation_queue),
        tuple(playlist.plugin_rotation_pool),
        tuple(playlist.plugin_rotation_recent_history),
    )
    assert (
        task.runtime_state.snapshot().instances[instance.instance_uuid]
        == before_runtime
    )
    assert device_config.write_count == 1


def test_same_projection_does_not_rewrite_config_each_poll(monkeypatch):
    context = _canonical_runtime_theme("day")
    task, device_config, _playlist, _instance, current_dt = (
        _weather_status_probe_runtime(
            "weather-status-same-projection",
            monkeypatch,
            context,
        )
    )

    assert task._select_independent_refresh_command(current_dt) is None
    first_info = copy.deepcopy(device_config.config["active_theme_info"])
    assert device_config.write_count == 1

    assert task._select_independent_refresh_command(
        current_dt + timedelta(minutes=1)
    ) is None
    assert device_config.write_count == 1
    assert device_config.config["active_theme_info"] == first_info


def test_mode_change_updates_info_but_not_active_theme_before_redraw_commit(
    monkeypatch,
):
    context = _canonical_runtime_theme("night")
    task, device_config, _playlist, instance, current_dt = (
        _weather_status_probe_runtime(
            "weather-status-mode-change",
            monkeypatch,
            context,
        )
    )

    command = task._select_independent_refresh_command(current_dt)

    assert command is not None
    assert command.intent is RefreshIntent.THEME_REDRAW
    assert command.instance_uuid == instance.instance_uuid
    assert command.payload["theme_context"]["mode"] == "night"
    assert device_config.config["active_theme"] == "day"
    assert device_config.config["active_theme_info"]["mode"] == "night"
    assert device_config.config["active_theme_info"]["timezone"] == (
        "America/Los_Angeles"
    )
    assert device_config.write_count == 1
    assert task.display_manager.calls == []


def test_malformed_effective_context_cannot_change_cache_identity(monkeypatch):
    malformed = {**_canonical_runtime_theme("night"), "mode": "sepia"}
    (
        task,
        _device_config,
        _playlist,
        instance,
        _plugin,
        command,
        _current_dt,
    ) = _weather_effective_runtime(
        "weather-malformed-effective-context",
        monkeypatch,
        malformed,
    )

    result = task._execute_command(command)

    assert Path(task._snapshot_cache_path(instance, "day")).exists()
    assert not Path(task._snapshot_cache_path(instance, "night")).exists()
    assert EFFECTIVE_THEME_CONTEXT_INFO_KEY not in result.info
    state = task.runtime_state.snapshot().instances[instance.instance_uuid]
    assert state.last_good_cache.theme_mode == "day"


def test_theme_redraw_rejects_effective_context_override_and_stays_pinned(
    monkeypatch,
):
    effective = _canonical_runtime_theme("night")
    (
        task,
        _device_config,
        playlist,
        instance,
        plugin,
        _data_command,
        current_dt,
    ) = _weather_effective_runtime(
        "weather-theme-redraw-rejects-effective",
        monkeypatch,
        effective,
    )
    queued = _canonical_runtime_theme("day")
    command = task._playlist_command(
        playlist.name,
        instance,
        source=CommandSource.SCHEDULER,
        intent=RefreshIntent.THEME_REDRAW,
        force=False,
        display_cached_only=False,
        priority=80,
        kind=CommandKind.CACHE_REFRESH,
        theme_context=queued,
        theme_render_only=True,
        current_dt=current_dt,
        resolved_theme_context=queued,
    )

    result = task._execute_command(command)

    assert plugin.calls[-1]["theme_render_only"] is True
    assert Path(task._snapshot_cache_path(instance, "day")).exists()
    assert not Path(task._snapshot_cache_path(instance, "night")).exists()
    assert EFFECTIVE_THEME_CONTEXT_INFO_KEY not in result.info
    state = task.runtime_state.snapshot().instances[instance.instance_uuid]
    assert state.theme.last_success_at == current_dt.isoformat()
    assert state.last_good_cache.theme_mode == "day"


def test_internal_effective_context_is_not_persisted_as_png_metadata(
    monkeypatch,
):
    effective = _canonical_runtime_theme("night")
    (
        task,
        _device_config,
        _playlist,
        instance,
        _plugin,
        command,
        _current_dt,
    ) = _weather_effective_runtime(
        "weather-internal-metadata-stripped",
        monkeypatch,
        effective,
    )

    result = task._execute_command(command)

    assert EFFECTIVE_THEME_CONTEXT_INFO_KEY not in result.info
    with Image.open(task._snapshot_cache_path(instance, "night")) as saved:
        assert EFFECTIVE_THEME_CONTEXT_INFO_KEY not in saved.info


def test_pinned_mode_survives_environment_flip_through_render_stage_and_commit(
    monkeypatch,
):
    tmp_path = make_test_dir("runtime-theme-pinned-commit")
    plugin_data = _runtime_plugin_data("themed_plugin", "Themed Plugin")
    plugin_data["plugin_settings"]["themeMode"] = "auto"
    playlist = _runtime_playlist(plugin_data)
    task, device_config, _clock = _make_runtime_task(tmp_path, playlists=[playlist])
    manifest = _theme_manifest("themed_plugin")
    plugin_config = {
        "id": "themed_plugin",
        "_manifest": manifest,
    }
    device_config.get_plugin = lambda _plugin_id: plugin_config
    device_config.config["theme_mode"] = "night"
    plugin = RecordingThemeWrapperPlugin(plugin_config)
    monkeypatch.setattr("src.refresh_task.get_plugin_instance", lambda _config: plugin)
    instance = playlist.plugins[0].snapshot()
    command = task._playlist_command(
        playlist.name,
        instance,
        source=CommandSource.SCHEDULER,
        intent=RefreshIntent.DATA_REFRESH,
        force=True,
        display_cached_only=False,
        current_dt=datetime(2026, 7, 11, 22, 0, tzinfo=timezone.utc),
    )
    device_config.config["theme_mode"] = "day"
    monkeypatch.setattr(
        task,
        "_get_current_datetime",
        lambda: datetime(2026, 7, 12, 8, 0, tzinfo=timezone.utc),
    )

    result = task._execute_command(command)

    assert result is not None
    assert plugin.contexts[0]["mode"] == "night"
    night_path = Path(task._snapshot_cache_path(instance, "night"))
    assert night_path.exists()
    assert not Path(task._snapshot_cache_path(instance, "day")).exists()
    assert not Path(task._snapshot_cache_path(instance)).exists()
    assert not Path(task._staging_cache_path(instance, "night")).exists()


def _write_runtime_cache(task, instance, image=None):
    """Seed the UUID/revision cache used by the production command worker."""
    snapshot = instance.snapshot() if hasattr(instance, "snapshot") else instance
    cache_path = Path(task.cache_path_for_snapshot(snapshot))
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    (image or Image.new("RGB", (1, 1), "black")).save(cache_path)
    return cache_path


def _write_runtime_theme_cache(task, instance, mode, image=None):
    snapshot = instance.snapshot() if hasattr(instance, "snapshot") else instance
    cache_path = Path(task._snapshot_cache_path(snapshot, mode))
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    (image or Image.new("RGB", (32, 16), "black")).save(cache_path)
    return cache_path


def test_public_cache_path_prefers_current_theme_then_last_good_fallback():
    tmp_path = make_test_dir("public-themed-cache-path")
    plugin_data = _runtime_plugin_data("themed_plugin", "Themed Plugin")
    plugin_data["plugin_settings"]["themeMode"] = "night"
    playlist = _runtime_playlist(plugin_data)
    task, device_config, _clock = _make_runtime_task(
        tmp_path,
        playlists=[playlist],
    )
    device_config.get_plugin = lambda _plugin_id: {
        "id": "themed_plugin",
        "_manifest": _theme_manifest("themed_plugin"),
    }
    instance = playlist.plugins[0].snapshot()
    current_theme = _write_runtime_theme_cache(task, instance, "night")
    last_good = _write_runtime_theme_cache(task, instance, "day")
    succeeded_at = "2026-07-12T12:00:00+00:00"
    task.runtime_state.record_success(
        instance.instance_uuid,
        succeeded_at,
        lane=RefreshLane.DATA,
        last_good_cache=LastGoodCacheState(
            theme_mode="day",
            structural_generation=instance.structural_generation,
            settings_revision=instance.settings_revision,
            promoted_at=succeeded_at,
        ),
    )

    assert Path(task.cache_path_for_snapshot(instance)) == current_theme

    current_theme.unlink()

    assert Path(task.cache_path_for_snapshot(instance)) == last_good


class ThemeOnlyRecordingPlugin:
    def __init__(self, config, *, fail=False, color="white"):
        self.config = config
        self.fail = fail
        self.color = color
        self.calls = []

    def wants_refresh_on_display(self, _settings):
        return False

    def render_themed_image(
        self,
        settings,
        device_config,
        *,
        theme_render_only=False,
        resolved_theme_context=None,
    ):
        self.calls.append(
            {
                "settings": dict(settings),
                "theme_render_only": theme_render_only,
                "resolved_theme_context": dict(resolved_theme_context or {}),
            }
        )
        if self.fail:
            raise RuntimeError("theme presentation failed")
        image = Image.new("RGB", device_config.get_resolution(), self.color)
        if resolved_theme_context:
            image.info["inkypi_theme_mode"] = resolved_theme_context["mode"]
        return image


def _theme_transition_runtime(
    name,
    *,
    displayed_mode="auto",
    displayed_supported=True,
    displayed_uuid="current",
):
    tmp_path = make_test_dir(name)
    displayed = _runtime_plugin_data("displayed", "Displayed")
    displayed["plugin_settings"]["themeMode"] = displayed_mode
    fallback = _runtime_plugin_data("fallback", "Fallback")
    fallback["plugin_settings"]["themeMode"] = "auto"
    playlist = _runtime_playlist(displayed, fallback)
    task, device_config, _clock = _make_runtime_task(
        tmp_path,
        playlists=[playlist],
        cycle_seconds=300,
    )
    device_config.config.update(
        {
            "theme_mode": "night",
            "active_theme": "day",
            "display_triggered_refresh_enabled": True,
        }
    )
    device_config.refresh_info = RefreshInfo(
        refresh_type="Playlist",
        playlist=playlist.name,
        plugin_id=playlist.plugins[0].plugin_id,
        plugin_instance=playlist.plugins[0].name,
        refresh_time="2026-07-11T21:59:00+00:00",
        image_hash="day-image",
    )
    runtime_displayed_uuid = (
        playlist.plugins[0].instance_uuid
        if displayed_uuid == "current"
        else displayed_uuid
    )
    task.runtime_state.set_display_state(
        "committed",
        instance_uuid=runtime_displayed_uuid,
        changed_at="2026-07-11T21:59:00+00:00",
    )
    configs = {
        "displayed": {
            "id": "displayed",
            "_manifest": _theme_manifest(
                "displayed",
                supported=displayed_supported,
            ),
        },
        "fallback": {
            "id": "fallback",
            "_manifest": _theme_manifest("fallback"),
        },
    }
    device_config.get_plugin = lambda plugin_id: configs[plugin_id]
    return task, device_config, playlist, configs


def test_successful_redraw_aligns_active_theme_and_existing_info(monkeypatch):
    task, device_config, playlist, configs = _theme_transition_runtime(
        "weather-status-redraw-success"
    )
    current_dt = datetime(
        2026,
        7,
        12,
        22,
        0,
        tzinfo=timezone(timedelta(hours=-7)),
    )
    context = _canonical_runtime_theme("night")
    instance = playlist.plugins[0].snapshot()
    _write_runtime_theme_cache(
        task,
        instance,
        "day",
        Image.new("RGB", (32, 16), "white"),
    )
    plugin = ThemeOnlyRecordingPlugin(configs["displayed"])
    monkeypatch.setattr("src.refresh_task.get_plugin_instance", lambda _config: plugin)
    monkeypatch.setattr(
        "src.refresh_task.get_theme_context",
        lambda *_args, **_kwargs: copy.deepcopy(context),
    )
    monkeypatch.setattr(task, "_get_current_datetime", lambda: current_dt)

    command = task._select_scheduled_command(current_dt)
    info_before_commit = copy.deepcopy(device_config.config["active_theme_info"])
    result = task._execute_command(command)

    assert result is not None
    assert device_config.config["active_theme"] == "night"
    assert device_config.config["active_theme_info"] == info_before_commit
    assert {
        key: info_before_commit[key]
        for key in ("source", "date", "timezone", "sunrise", "sunset")
    } == {
        key: context[key]
        for key in ("source", "date", "timezone", "sunrise", "sunset")
    }
    state = task.runtime_state.snapshot().instances[instance.instance_uuid]
    assert state.last_good_cache.theme_mode == "night"
    assert device_config.write_count == 2


def test_failed_redraw_keeps_active_theme_last_good_and_info_current(monkeypatch):
    task, device_config, playlist, configs = _theme_transition_runtime(
        "weather-status-redraw-failure"
    )
    current_dt = datetime(
        2026,
        7,
        12,
        22,
        0,
        tzinfo=timezone(timedelta(hours=-7)),
    )
    context = _canonical_runtime_theme("night")
    instance = playlist.plugins[0].snapshot()
    day_path = _write_runtime_theme_cache(
        task,
        instance,
        "day",
        Image.new("RGB", (32, 16), "white"),
    )
    day_bytes = day_path.read_bytes()
    plugin = ThemeOnlyRecordingPlugin(configs["displayed"], fail=True)
    monkeypatch.setattr("src.refresh_task.get_plugin_instance", lambda _config: plugin)
    monkeypatch.setattr(
        "src.refresh_task.get_theme_context",
        lambda *_args, **_kwargs: copy.deepcopy(context),
    )
    monkeypatch.setattr(task, "_get_current_datetime", lambda: current_dt)
    command = task._select_scheduled_command(current_dt)
    expected_info = copy.deepcopy(device_config.config["active_theme_info"])

    submitted = task.refresh_queue.submit(command)
    task._process_queue_entry(task.refresh_queue.take(timeout=0))
    job = task.refresh_queue.get_entry(submitted.id).job

    assert job.status is JobStatus.FAILED
    assert device_config.config["active_theme"] == "day"
    assert device_config.config["active_theme_info"] == expected_info
    assert device_config.config["active_theme_info"]["mode"] == "night"
    assert device_config.config["active_theme_refresh_failure"]["mode"] == "night"
    assert day_path.read_bytes() == day_bytes
    assert not Path(task._snapshot_cache_path(instance, "night")).exists()
    assert task.display_manager.calls == []
    assert device_config.write_count == 2


def test_theme_transition_selects_exact_displayed_auto_instance_without_fallback():
    task, _device_config, playlist, _configs = _theme_transition_runtime(
        "theme-transition-exact-auto"
    )
    manager = task.device_config.playlist_manager
    original_select = manager.select_theme_instance
    observed = {}

    def select_with_observation(*args, **kwargs):
        observed.update(kwargs)
        return original_select(*args, **kwargs)

    manager.select_theme_instance = select_with_observation

    command = task._select_scheduled_command(
        datetime(2026, 7, 11, 22, 0, tzinfo=timezone.utc)
    )

    assert command.instance_uuid == playlist.plugins[0].instance_uuid
    assert observed["allow_fallback"] is False
    assert observed["is_eligible"](playlist.plugins[0].snapshot()) is True
    assert observed["is_eligible"](playlist.plugins[1].snapshot()) is True
    assert command.force is False
    assert command.payload["theme_render_only"] is True
    assert command.payload["expected_displayed_instance_uuid"] == (
        playlist.plugins[0].instance_uuid
    )
    assert command.payload["resolved_theme_context"]["requested_mode"] == "auto"


@pytest.mark.parametrize(
    ("displayed_mode", "displayed_supported", "displayed_uuid"),
    [
        ("day", True, "current"),
        ("auto", False, "current"),
        ("auto", True, "missing-instance-uuid"),
    ],
)
def test_ineligible_or_missing_displayed_theme_target_persists_noop_without_rotation(
    displayed_mode,
    displayed_supported,
    displayed_uuid,
):
    task, device_config, playlist, _configs = _theme_transition_runtime(
        "theme-transition-noop",
        displayed_mode=displayed_mode,
        displayed_supported=displayed_supported,
        displayed_uuid=displayed_uuid,
    )
    before_rotation = (
        playlist.current_plugin_index,
        list(playlist.plugin_rotation_queue),
        list(playlist.plugin_rotation_pool),
        list(playlist.plugin_rotation_recent_history),
    )

    command = task._select_scheduled_command(
        datetime(2026, 7, 11, 22, 0, tzinfo=timezone.utc)
    )

    assert command is None
    assert device_config.config["active_theme"] == "night"
    assert device_config.config["active_theme_info"]["mode"] == "night"
    assert device_config.write_count == 1
    assert before_rotation == (
        playlist.current_plugin_index,
        list(playlist.plugin_rotation_queue),
        list(playlist.plugin_rotation_pool),
        list(playlist.plugin_rotation_recent_history),
    )


def test_immediate_ui_theme_redraw_is_pinned_force_free_and_preserves_data_cadence(
    monkeypatch,
):
    task, device_config, playlist, configs = _theme_transition_runtime(
        "theme-transition-ui-cadence"
    )
    current_dt = datetime(2026, 7, 11, 22, 0, tzinfo=timezone.utc)
    anchor = device_config.refresh_info.refresh_time
    instance = playlist.plugins[0].snapshot()
    _write_runtime_theme_cache(
        task,
        instance,
        "day",
        Image.new("RGB", (32, 16), "black"),
    )
    plugin = ThemeOnlyRecordingPlugin(configs["displayed"], color="white")
    monkeypatch.setattr("src.refresh_task.get_plugin_instance", lambda _config: plugin)
    monkeypatch.setattr(task, "_get_current_datetime", lambda: current_dt)

    command = task._select_scheduled_command(current_dt)
    result = task._execute_command(command)

    assert result is not None
    assert plugin.calls == [
        {
            "settings": {"id": "displayed", "themeMode": "auto", "_inkypiDisplayRender": True},
            "theme_render_only": True,
            "resolved_theme_context": dict(command.payload["resolved_theme_context"]),
        }
    ]
    assert command.force is False
    assert device_config.refresh_info.refresh_time == anchor
    state = task.runtime_state.snapshot().instances.get(instance.instance_uuid)
    assert state is None or state.last_success_at is None
    assert device_config.config["active_theme"] == "night"
    assert Path(task._snapshot_cache_path(instance, "night")).exists()
    assert not Path(task._staging_cache_path(instance, "night")).exists()


def test_failed_immediate_theme_redraw_keeps_last_good_and_enters_cooldown(
    monkeypatch,
):
    task, device_config, playlist, configs = _theme_transition_runtime(
        "theme-transition-last-good"
    )
    current_dt = datetime(2026, 7, 11, 22, 0, tzinfo=timezone.utc)
    instance = playlist.plugins[0].snapshot()
    day_path = _write_runtime_theme_cache(
        task,
        instance,
        "day",
        Image.new("RGB", (32, 16), "black"),
    )
    original = day_path.read_bytes()
    plugin = ThemeOnlyRecordingPlugin(configs["displayed"], fail=True)
    monkeypatch.setattr("src.refresh_task.get_plugin_instance", lambda _config: plugin)
    monkeypatch.setattr(task, "_get_current_datetime", lambda: current_dt)
    command = task._select_scheduled_command(current_dt)

    submitted = task.refresh_queue.submit(command)
    task._process_queue_entry(task.refresh_queue.take(timeout=0))
    result = task.refresh_queue.get_entry(submitted.id).job

    assert command.force is False
    assert command.payload["theme_render_only"] is True
    assert result.status is JobStatus.FAILED
    assert day_path.read_bytes() == original
    assert not Path(task._snapshot_cache_path(instance, "night")).exists()
    assert task.display_manager.calls == []
    assert device_config.config["active_theme"] == "day"
    assert task._theme_refresh_retry_delayed(
        command.payload["theme_context"],
        current_dt + timedelta(seconds=1),
    )


def test_queued_theme_transition_is_stale_if_display_changes_before_render(
    monkeypatch,
):
    task, device_config, playlist, configs = _theme_transition_runtime(
        "theme-transition-stale-before-render"
    )
    current_dt = datetime(2026, 7, 11, 22, 0, tzinfo=timezone.utc)
    target = playlist.plugins[0].snapshot()
    other = playlist.plugins[1].snapshot()
    plugin = ThemeOnlyRecordingPlugin(configs["displayed"])
    monkeypatch.setattr("src.refresh_task.get_plugin_instance", lambda _config: plugin)
    monkeypatch.setattr(task, "_get_current_datetime", lambda: current_dt)
    command = task._select_scheduled_command(current_dt)
    task.runtime_state.set_display_state(
        "committed",
        instance_uuid=other.instance_uuid,
        changed_at=(current_dt + timedelta(seconds=1)).isoformat(),
    )

    submitted = task.refresh_queue.submit(command)
    task._process_queue_entry(task.refresh_queue.take(timeout=0))
    result = task.refresh_queue.get_entry(submitted.id).job

    assert command.payload["expected_displayed_instance_uuid"] == target.instance_uuid
    assert result.status is JobStatus.CANCELED
    assert result.error_code == "stale_selection"
    assert plugin.calls == []
    assert task.display_manager.calls == []
    assert not Path(task._snapshot_cache_path(target, "night")).exists()
    assert device_config.config["active_theme"] == "day"
    assert device_config.config["active_theme_info"]["mode"] == "night"
    assert "active_theme_refresh_failure" not in device_config.config
    assert device_config.write_count == 1


def test_theme_transition_is_stale_if_display_changes_during_render(
    monkeypatch,
):
    task, device_config, playlist, configs = _theme_transition_runtime(
        "theme-transition-stale-after-render"
    )
    current_dt = datetime(2026, 7, 11, 22, 0, tzinfo=timezone.utc)
    target = playlist.plugins[0].snapshot()
    other = playlist.plugins[1].snapshot()
    day_path = _write_runtime_theme_cache(
        task,
        target,
        "day",
        Image.new("RGB", (32, 16), "black"),
    )
    day_bytes = day_path.read_bytes()

    class DisplaySwitchingPlugin(ThemeOnlyRecordingPlugin):
        def render_themed_image(self, *args, **kwargs):
            image = super().render_themed_image(*args, **kwargs)
            task.runtime_state.set_display_state(
                "committed",
                instance_uuid=other.instance_uuid,
                changed_at=(current_dt + timedelta(seconds=1)).isoformat(),
            )
            return image

    plugin = DisplaySwitchingPlugin(configs["displayed"])
    monkeypatch.setattr("src.refresh_task.get_plugin_instance", lambda _config: plugin)
    monkeypatch.setattr(task, "_get_current_datetime", lambda: current_dt)
    command = task._select_scheduled_command(current_dt)

    submitted = task.refresh_queue.submit(command)
    task._process_queue_entry(task.refresh_queue.take(timeout=0))
    result = task.refresh_queue.get_entry(submitted.id).job

    assert result.status is JobStatus.CANCELED
    assert result.error_code == "stale_selection"
    assert len(plugin.calls) == 1
    assert day_path.read_bytes() == day_bytes
    assert not Path(task._snapshot_cache_path(target, "night")).exists()
    assert not Path(task._staging_cache_path(target, "night")).exists()
    assert task.display_manager.calls == []
    state = task.runtime_state.snapshot().instances[target.instance_uuid]
    assert state.last_success_at is None
    assert device_config.config["active_theme"] == "day"
    assert device_config.config["active_theme_info"]["mode"] == "night"
    assert "active_theme_refresh_failure" not in device_config.config
    assert device_config.write_count == 1


def test_theme_transition_without_runtime_uuid_does_not_use_refresh_info_fallback(
    monkeypatch,
):
    task, device_config, playlist, configs = _theme_transition_runtime(
        "theme-transition-refresh-info-compat"
    )
    current_dt = datetime(2026, 7, 11, 22, 0, tzinfo=timezone.utc)
    task.runtime_state.set_display_state(
        "committed",
        instance_uuid=None,
        changed_at=current_dt.isoformat(),
    )
    _prepare_independent_theme_candidate(task, playlist, current_dt)
    plugin = ThemeOnlyRecordingPlugin(configs["displayed"])
    monkeypatch.setattr("src.refresh_task.get_plugin_instance", lambda _config: plugin)
    monkeypatch.setattr(
        task,
        "_resource_sample",
        lambda: ResourceSample(available_mb=512, swap_percent=0),
    )
    monkeypatch.setattr(task, "_get_current_datetime", lambda: current_dt)

    command = task._select_independent_refresh_command(current_dt)

    assert command is None
    assert plugin.calls == []
    assert device_config.config["active_theme"] == "night"


@pytest.mark.parametrize("presentation", ["ui", "media"])
def test_missing_theme_cache_under_pressure_keeps_last_good_without_render(
    monkeypatch,
    presentation,
):
    task, device_config, playlist, configs = _theme_transition_runtime(
        f"theme-transition-pressure-{presentation}"
    )
    current_dt = datetime(2026, 7, 11, 22, 0, tzinfo=timezone.utc)
    target = playlist.plugins[0].snapshot()
    configs["displayed"]["_manifest"] = _theme_manifest(
        "displayed",
        presentation=presentation,
    )
    dimensions = (40, 24) if presentation == "media" else (32, 16)
    device_config.get_resolution = lambda: dimensions
    day_path = _write_runtime_theme_cache(
        task,
        target,
        "day",
        Image.new("RGB", dimensions, "black"),
    )
    day_bytes = day_path.read_bytes()
    plugin = ThemeOnlyRecordingPlugin(configs["displayed"])
    chrome_calls = []
    original_chrome = refresh_task_module.apply_media_theme_chrome
    monkeypatch.setattr("src.refresh_task.get_plugin_instance", lambda _config: plugin)

    def record_chrome(*args, **kwargs):
        chrome_calls.append((args, kwargs))
        return original_chrome(*args, **kwargs)

    monkeypatch.setattr(
        "src.refresh_task.apply_media_theme_chrome",
        record_chrome,
    )
    monkeypatch.setattr(
        "src.refresh_task._display_refresh_under_resource_pressure",
        lambda _device_config, **_kwargs: True,
    )
    monkeypatch.setattr(task, "_get_current_datetime", lambda: current_dt)
    before_refresh = device_config.refresh_info.to_dict()
    command = task._select_scheduled_command(current_dt)

    result = task._execute_command(command)

    assert result is None
    assert plugin.calls == []
    assert chrome_calls == []
    assert day_path.read_bytes() == day_bytes
    assert not Path(task._snapshot_cache_path(target, "night")).exists()
    assert not Path(task._staging_cache_path(target, "night")).exists()
    assert task.display_manager.calls == []
    assert device_config.refresh_info.to_dict() == before_refresh
    state = task.runtime_state.snapshot().instances.get(target.instance_uuid)
    assert state is None or state.last_success_at is None
    assert device_config.config["active_theme"] == "day"
    assert device_config.config["active_theme_info"]["mode"] == "night"
    assert "active_theme_refresh_failure" not in device_config.config
    assert device_config.write_count == 1


def test_existing_target_theme_cache_is_safe_to_promote_under_pressure(monkeypatch):
    task, device_config, playlist, configs = _theme_transition_runtime(
        "theme-transition-pressure-cached"
    )
    current_dt = datetime(2026, 7, 11, 22, 0, tzinfo=timezone.utc)
    target = playlist.plugins[0].snapshot()
    _write_runtime_theme_cache(
        task,
        target,
        "night",
        Image.new("RGB", (32, 16), "white"),
    )
    plugin = ThemeOnlyRecordingPlugin(configs["displayed"], fail=True)
    monkeypatch.setattr("src.refresh_task.get_plugin_instance", lambda _config: plugin)
    monkeypatch.setattr(
        "src.refresh_task._display_refresh_under_resource_pressure",
        lambda _device_config, **_kwargs: True,
    )
    monkeypatch.setattr(task, "_get_current_datetime", lambda: current_dt)
    anchor = device_config.refresh_info.refresh_time
    command = task._select_scheduled_command(current_dt)

    result = task._execute_command(command)

    assert result is not None
    assert plugin.calls == []
    assert device_config.config["active_theme"] == "night"
    assert device_config.refresh_info.refresh_time == anchor
    state = task.runtime_state.snapshot().instances.get(target.instance_uuid)
    assert state is None or state.last_success_at is None


def test_manual_force_display_still_renders_under_display_pressure(monkeypatch):
    tmp_path = make_test_dir("manual-force-under-pressure")
    playlist = _runtime_playlist(
        _runtime_plugin_data("manual_force", "Manual Force")
    )
    task, _device_config, _clock = _make_runtime_task(tmp_path, playlists=[playlist])
    current_dt = datetime(2026, 7, 11, 22, 0, tzinfo=timezone.utc)
    calls = []
    monkeypatch.setattr(
        "src.refresh_task.get_plugin_instance",
        lambda _config: CapturePlugin(calls),
    )
    monkeypatch.setattr(
        "src.refresh_task._display_refresh_under_resource_pressure",
        lambda _device_config, **_kwargs: True,
    )
    monkeypatch.setattr(task, "_get_current_datetime", lambda: current_dt)
    instance = playlist.plugins[0].snapshot()
    command = task._playlist_command(
        playlist.name,
        instance,
        source=CommandSource.MANUAL,
        intent=RefreshIntent.MANUAL_RENDER,
        force=True,
        display_cached_only=True,
        require_active=True,
    )

    result = task._execute_command(command)

    assert result is not None
    assert calls == [
        {
            "id": "manual_force",
            "forceRefresh": True,
            "force_refresh": True,
            "_inkypiDisplayRender": True,
        }
    ]


@pytest.mark.parametrize("source_mode", ["day", None])
def test_media_theme_redraw_reuses_opposite_or_legacy_cache_without_provider(
    monkeypatch,
    source_mode,
):
    task, device_config, playlist, configs = _theme_transition_runtime(
        f"theme-transition-media-{source_mode or 'legacy'}"
    )
    current_dt = datetime(2026, 7, 11, 22, 0, tzinfo=timezone.utc)
    instance = playlist.plugins[0].snapshot()
    configs["displayed"]["_manifest"] = _theme_manifest(
        "displayed",
        presentation="media",
    )
    device_config.get_resolution = lambda: (40, 24)
    source = Image.new("RGB", (40, 24), (180, 20, 30))
    source_path = Path(task._snapshot_cache_path(instance, source_mode))
    source_path.parent.mkdir(parents=True, exist_ok=True)
    source.save(source_path)
    plugin = ThemeOnlyRecordingPlugin(configs["displayed"], color="white")
    monkeypatch.setattr("src.refresh_task.get_plugin_instance", lambda _config: plugin)
    monkeypatch.setattr(task, "_get_current_datetime", lambda: current_dt)

    command = task._select_scheduled_command(current_dt)
    result = task._execute_command(command)

    assert plugin.calls == []
    assert result.getpixel((20, 12)) == (180, 20, 30)
    assert result.info["inkypi_theme_mode"] == "night"
    assert Path(task._snapshot_cache_path(instance, "night")).exists()
    assert not Path(task._staging_cache_path(instance, "night")).exists()


def test_opposite_theme_cache_is_not_background_missing_until_data_is_due(
    monkeypatch,
):
    tmp_path = make_test_dir("theme-background-lazy")
    plugin_data = _runtime_plugin_data(
        "themed_plugin",
        "Themed Plugin",
        latest_refresh_time="2999-01-01T00:00:00+00:00",
        interval=3600,
    )
    plugin_data["plugin_settings"]["themeMode"] = "auto"
    playlist = _runtime_playlist(plugin_data)
    task, device_config, _clock = _make_runtime_task(tmp_path, playlists=[playlist])
    device_config.config.update({"theme_mode": "night", "active_theme": "night"})
    plugin_config = {
        "id": "themed_plugin",
        "_manifest": _theme_manifest("themed_plugin"),
    }
    device_config.get_plugin = lambda _plugin_id: plugin_config
    instance = playlist.plugins[0]
    _write_runtime_theme_cache(task, instance, "day")
    current_dt = datetime(2026, 7, 11, 22, 0, tzinfo=timezone.utc)

    assert task._select_background_commands(current_dt) == ()

    task.runtime_state.record_success(
        instance.instance_uuid,
        (current_dt - timedelta(hours=2)).isoformat(),
    )
    commands = task._select_background_commands(current_dt)

    assert len(commands) == 1
    assert commands[0].kind is CommandKind.CACHE_REFRESH
    assert commands[0].payload.get("theme_render_only") is None
    plugin = ThemeOnlyRecordingPlugin(plugin_config, color="white")
    monkeypatch.setattr("src.refresh_task.get_plugin_instance", lambda _config: plugin)
    monkeypatch.setattr(task, "_cache_refresh_under_resource_pressure", lambda: False)
    monkeypatch.setattr(task, "_get_current_datetime", lambda: current_dt)

    task._execute_command(commands[0])

    assert plugin.calls[0]["theme_render_only"] is False
    state = task.runtime_state.snapshot().instances[instance.instance_uuid]
    assert state.last_success_at == current_dt.isoformat()
    assert Path(task._snapshot_cache_path(instance.snapshot(), "night")).exists()


def test_theme_retry_cooldown_does_not_block_independently_due_background_data(
    monkeypatch,
):
    tmp_path = make_test_dir("theme-cooldown-keeps-data-refresh")
    current_dt = datetime(2026, 7, 11, 22, 0, tzinfo=timezone.utc)
    due = _runtime_plugin_data(
        "ordinary_due",
        "Ordinary Due",
        latest_refresh_time=(current_dt - timedelta(hours=2)).isoformat(),
        interval=3600,
    )
    presentation_only = _runtime_plugin_data(
        "presentation_only",
        "Presentation Only",
        latest_refresh_time="2999-01-01T00:00:00+00:00",
        interval=3600,
    )
    presentation_only["plugin_settings"]["themeMode"] = "auto"
    playlist = _runtime_playlist(due, presentation_only)
    task, device_config, _clock = _make_runtime_task(tmp_path, playlists=[playlist])
    failure = {
        "mode": "night",
        "retry_after": (current_dt + timedelta(minutes=10)).isoformat(),
        "error": "theme render failed",
    }
    device_config.config.update(
        {
            "theme_mode": "night",
            "active_theme": "day",
            "active_theme_refresh_failure": failure,
        }
    )

    def plugin_config(plugin_id):
        return {
            "id": plugin_id,
            "_manifest": _theme_manifest(
                plugin_id,
                supported=plugin_id == "presentation_only",
            ),
        }

    device_config.get_plugin = plugin_config

    commands = task._select_background_commands(current_dt)

    assert [command.instance_uuid for command in commands] == [
        playlist.plugins[0].instance_uuid
    ]
    assert commands[0].plugin_id == "ordinary_due"
    assert commands[0].payload.get("theme_render_only") is None
    calls = []
    monkeypatch.setattr(
        "src.refresh_task.get_plugin_instance",
        lambda _config: CapturePlugin(calls),
    )
    monkeypatch.setattr(task, "_cache_refresh_under_resource_pressure", lambda: False)
    monkeypatch.setattr(task, "_get_current_datetime", lambda: current_dt)

    task._execute_command(commands[0])

    assert calls == [
        {
            "id": "ordinary_due",
        }
    ]
    state = task.runtime_state.snapshot().instances[playlist.plugins[0].instance_uuid]
    assert state.last_success_at == current_dt.isoformat()
    assert device_config.config["active_theme"] == "day"
    assert device_config.config["active_theme_refresh_failure"] == failure


def test_ordinary_random_display_excludes_last_good_opposite_theme_cache(
    monkeypatch,
):
    tmp_path = make_test_dir("theme-lazy-next-display")
    plugin_data = _runtime_plugin_data(
        "themed_plugin",
        "Themed Plugin",
        latest_refresh_time="2999-01-01T00:00:00+00:00",
        interval=3600,
    )
    plugin_data["plugin_settings"]["themeMode"] = "auto"
    playlist = _runtime_playlist(plugin_data)
    task, device_config, _clock = _make_runtime_task(
        tmp_path,
        playlists=[playlist],
        cycle_seconds=300,
    )
    current_dt = datetime(2026, 7, 11, 22, 0, tzinfo=timezone.utc)
    device_config.config.update({"theme_mode": "night", "active_theme": "night"})
    device_config.refresh_info = RefreshInfo(
        refresh_type="Playlist",
        playlist=playlist.name,
        plugin_id=playlist.plugins[0].plugin_id,
        plugin_instance=playlist.plugins[0].name,
        refresh_time="2026-07-11T21:50:00+00:00",
        image_hash="day-image",
    )
    plugin_config = {
        "id": "themed_plugin",
        "_manifest": _theme_manifest("themed_plugin"),
    }
    device_config.get_plugin = lambda _plugin_id: plugin_config
    instance = playlist.plugins[0].snapshot()
    _write_runtime_theme_cache(task, instance, "day")
    seeded_at = current_dt - timedelta(minutes=10)
    _seed_theme_last_good(task, instance, "day", seeded_at)
    plugin = ThemeOnlyRecordingPlugin(plugin_config, color="white")
    monkeypatch.setattr("src.refresh_task.get_plugin_instance", lambda _config: plugin)
    monkeypatch.setattr(task, "_get_current_datetime", lambda: current_dt)
    before_rotation = (
        playlist.current_plugin_index,
        list(playlist.plugin_rotation_queue),
        list(playlist.plugin_rotation_pool),
        list(playlist.plugin_rotation_recent_history),
    )
    before_refresh_info = device_config.refresh_info

    command = task._select_cached_display_command(current_dt)

    assert command is None
    assert plugin.calls == []
    state = task.runtime_state.snapshot().instances.get(instance.instance_uuid)
    assert state.data.last_success_at == seeded_at.isoformat()
    assert device_config.refresh_info == before_refresh_info
    assert before_rotation == (
        playlist.current_plugin_index,
        list(playlist.plugin_rotation_queue),
        list(playlist.plugin_rotation_pool),
        list(playlist.plugin_rotation_recent_history),
    )
    assert not Path(task._snapshot_cache_path(instance, "night")).exists()
    assert not Path(task._staging_cache_path(instance, "night")).exists()


def test_refresh_task_binds_shared_runtime_state_and_recovers_display_on_start():
    tmp_path = make_test_dir("runtime-display-recovery")
    manager = TransactionRecordingDisplayManager()
    task = RefreshTask(RuntimeDeviceConfig(tmp_path), manager)

    assert manager.bound_runtime_state is task.runtime_state

    task.start()
    try:
        assert task.wait_until_waiting(timeout=1.0)
        assert isinstance(manager.recovery_context, TaskContext)
    finally:
        task.stop(join_timeout=1.0)


def test_playlist_display_commit_passes_target_revision_and_task_context(monkeypatch):
    tmp_path = make_test_dir("runtime-display-transaction-metadata")
    playlist = _runtime_playlist(
        _runtime_plugin_data("transactional", "Transactional Plugin")
    )
    manager = TransactionRecordingDisplayManager()
    device_config = RuntimeDeviceConfig(tmp_path, [playlist])
    task = RefreshTask(device_config, manager)
    instance = playlist.plugins[0]
    _write_runtime_cache(task, instance, Image.new("RGB", (2, 1), "white"))
    monkeypatch.setattr(
        "src.refresh_task.get_plugin_instance",
        lambda _config: FakePlugin([]),
    )

    task.start()
    try:
        assert task.wait_until_waiting(timeout=1.0)
        job = task.submit_playlist_display(
            instance.instance_uuid,
            force=False,
            display_cached_only=True,
            expected_playlist_name=playlist.name,
            expected_generation=instance.structural_generation,
            expected_settings_revision=instance.settings_revision,
        )
        result = task.wait_for_job(job["id"], timeout=1.0)

        assert result["status"] == "completed"
        assert len(manager.calls) == 1
        call = manager.calls[0]
        assert isinstance(call["task_context"], TaskContext)
        assert call["logical_target"] == {
            "kind": "playlist",
            "playlist": playlist.name,
            "plugin_id": instance.plugin_id,
            "plugin_instance": instance.name,
            "instance_uuid": instance.instance_uuid,
        }
        assert call["instance_revision"] == (
            instance.structural_generation,
            instance.settings_revision,
        )
    finally:
        task.stop(join_timeout=1.0)


def test_global_policy_overrides_manual_presentation_opt_in():
    playlist = _runtime_playlist(
        _runtime_plugin_data("presentation_plugin", "Presentation Plugin")
    )
    task, device_config, _clock = _make_runtime_task(
        make_test_dir("manual-presentation-opt-in-policy-off"),
        playlists=[playlist],
    )
    device_config.config["display_triggered_refresh_enabled"] = False
    instance = playlist.plugins[0].snapshot()

    command = task._playlist_command(
        playlist.name,
        instance,
        source=CommandSource.MANUAL,
        intent=RefreshIntent.DISPLAY_CACHE,
        display_cached_only=True,
        allow_prepared_presentation=True,
    )

    assert command.allow_prepared_presentation is False


def _wait_for_legacy_job(task, job_id, timeout=1.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = task.get_manual_update_job(job_id)
        if job and job["status"] not in {"queued", "running"}:
            return job
        time.sleep(0.01)
    return task.get_manual_update_job(job_id)


def test_runtime_render_skips_live_hook_without_manifest_capability(monkeypatch):
    tmp_path = make_test_dir("runtime-manifest-live-render-gate")
    playlist = _runtime_playlist(
        _runtime_plugin_data("ordinary_plugin", "Ordinary Plugin")
    )
    task, device_config, _clock = _make_runtime_task(
        tmp_path,
        playlists=[playlist],
    )
    instance = playlist.plugins[0]
    _write_runtime_cache(task, instance)
    manifest = PluginManifest(
        schema_version=2,
        id="ordinary_plugin",
        class_name="OrdinaryPlugin",
        display_name="Ordinary Plugin",
        refresh_on_display=False,
        capabilities=PluginCapabilities(supports_live_refresh=False),
        raw={},
    )
    device_config.get_plugin = lambda plugin_id: {
        "id": plugin_id,
        "_manifest": manifest,
    }
    hook_calls = []
    plugin = FakePlugin(
        [],
        live_state=lambda *_args: hook_calls.append("called")
        or {"active": True, "interval_seconds": 1},
    )
    monkeypatch.setattr("src.refresh_task.get_plugin_instance", lambda _config: plugin)

    task.start()
    try:
        assert task.wait_until_waiting(timeout=1.0)
        job = task.submit_playlist_display(
            instance.instance_uuid,
            force=False,
            display_cached_only=True,
            expected_playlist_name=playlist.name,
            expected_generation=instance.structural_generation,
            expected_settings_revision=instance.settings_revision,
        )
        result = task.wait_for_job(job["id"], timeout=1.0)

        assert result["status"] == "completed"
        assert hook_calls == []
    finally:
        task.stop(join_timeout=1.0)


def test_manual_playlist_display_can_target_inactive_playlist_with_exact_cas(
    monkeypatch,
):
    tmp_path = make_test_dir("manual-inactive-playlist-display")
    active = _runtime_playlist(
        _runtime_plugin_data("active", "Active"),
        name="Active",
    )
    inactive = _runtime_playlist(
        _runtime_plugin_data("inactive", "Inactive"),
        name="Inactive",
    )
    task, device_config, _clock = _make_runtime_task(
        tmp_path,
        playlists=[active, inactive],
    )
    device_config.config.update({"theme_mode": "day", "active_theme": "day"})
    target = device_config.playlist_manager.resolve_plugin_instance_snapshot(
        "Inactive",
        "inactive",
        "Inactive",
    ).instance
    _write_runtime_cache(task, target)
    _write_runtime_cache(task, active.plugins[0])
    calls = []
    monkeypatch.setattr(
        "src.refresh_task.get_plugin_instance",
        lambda _config: CapturePlugin(calls),
    )

    task.start()
    try:
        assert task.wait_until_waiting(timeout=1.0)
        job = task.submit_playlist_display(
            target.instance_uuid,
            force=True,
            display_cached_only=False,
            expected_playlist_name="Inactive",
            expected_generation=target.structural_generation,
            expected_settings_revision=target.settings_revision,
            require_active=False,
        )
        result = task.wait_for_job(job["id"], timeout=1.0)

        assert result["status"] == "completed"
        assert calls == []
        assert device_config.refresh_info.playlist == "Inactive"
    finally:
        task.stop(join_timeout=1.0)


def test_manual_playlist_data_refresh_queues_forced_exact_inactive_cache_command():
    tmp_path = make_test_dir("manual-inactive-playlist-data-refresh")
    active = _runtime_playlist(
        _runtime_plugin_data("active", "Active"),
        name="Active",
    )
    inactive = _runtime_playlist(
        _runtime_plugin_data("inactive", "Inactive"),
        name="Inactive",
    )
    task, device_config, _clock = _make_runtime_task(
        tmp_path,
        playlists=[active, inactive],
    )
    target = device_config.playlist_manager.resolve_plugin_instance_snapshot(
        "Inactive",
        "inactive",
        "Inactive",
    ).instance
    task.running = True

    job = task.submit_playlist_data_refresh(
        target.instance_uuid,
        expected_playlist_name="Inactive",
        expected_generation=target.structural_generation,
        expected_settings_revision=target.settings_revision,
        require_active=False,
    )

    entry = task.refresh_queue.get_entry(job["id"])
    command = entry.command
    assert command.source is CommandSource.MANUAL
    assert command.intent is RefreshIntent.DATA_REFRESH
    assert command.kind is CommandKind.CACHE_REFRESH
    assert command.force is True
    assert command.priority == 100
    assert command.instance_uuid == target.instance_uuid
    assert command.structural_generation == target.structural_generation
    assert command.settings_revision == target.settings_revision
    assert command.payload["playlist_name"] == "Inactive"
    assert command.payload["display_cached_only"] is False
    assert command.payload["require_active"] is False


def test_manual_inactive_data_refresh_executes_after_scheduled_job_coalesces(
    monkeypatch,
):
    tmp_path = make_test_dir("manual-inactive-data-refresh-coalesced-execution")
    active = _runtime_playlist(
        _runtime_plugin_data("active", "Active"),
        name="Active",
    )
    inactive = _runtime_playlist(
        _runtime_plugin_data("inactive", "Inactive"),
        name="Inactive",
    )
    task, device_config, _clock = _make_runtime_task(
        tmp_path,
        playlists=[active, inactive],
    )
    current_dt = datetime(2026, 7, 13, 12, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(task, "_get_current_datetime", lambda: current_dt)
    target = device_config.playlist_manager.resolve_plugin_instance_snapshot(
        "Inactive",
        "inactive",
        "Inactive",
    ).instance
    scheduled = task._playlist_command(
        "Inactive",
        target,
        source=CommandSource.BACKGROUND,
        intent=RefreshIntent.DATA_REFRESH,
        force=False,
        display_cached_only=False,
        priority=10,
        kind=CommandKind.CACHE_REFRESH,
        current_dt=current_dt,
        require_active=True,
    )
    scheduled_job = task.refresh_queue.submit(scheduled)
    calls = []
    monkeypatch.setattr(
        "src.refresh_task.get_plugin_instance",
        lambda _config: CapturePlugin(calls),
    )
    task.running = True

    manual_job = task.submit_playlist_data_refresh(
        target.instance_uuid,
        expected_playlist_name="Inactive",
        expected_generation=target.structural_generation,
        expected_settings_revision=target.settings_revision,
        require_active=False,
    )
    entry = task.refresh_queue.take(timeout=0)

    assert manual_job["id"] == scheduled_job.id
    assert entry.command.source is CommandSource.MANUAL
    assert entry.command.payload["require_active"] is False
    task._process_queue_entry(entry)

    result = task.refresh_queue.get_entry(scheduled_job.id).job
    state = task.runtime_state.snapshot().instances[target.instance_uuid]
    assert result.status is JobStatus.SUCCEEDED
    assert calls == [{"id": "inactive", "forceRefresh": True, "force_refresh": True}]
    assert state.data.last_success_at == current_dt.isoformat()
    assert state.last_good_cache.structural_generation == target.structural_generation
    assert state.last_good_cache.settings_revision == target.settings_revision
    assert task.display_manager.calls == []


def test_manual_playlist_data_refresh_rejects_changed_exact_cas():
    tmp_path = make_test_dir("manual-stale-playlist-data-refresh")
    playlist = _runtime_playlist(_runtime_plugin_data("weather", "Home"))
    task, _device_config, _clock = _make_runtime_task(
        tmp_path,
        playlists=[playlist],
    )
    target = playlist.plugins[0]
    task.running = True

    with pytest.raises(ValueError, match="not found or changed"):
        task.submit_playlist_data_refresh(
            target.instance_uuid,
            expected_playlist_name=playlist.name,
            expected_generation=target.structural_generation,
            expected_settings_revision=target.settings_revision + 1,
            require_active=False,
        )

    assert task.refresh_queue.snapshot().depth == 0


def test_playlist_refresh_uses_non_sports_cache_when_live_refresh_is_due():
    calls = []
    tmp_path = make_test_dir("scheduled-non-sports-live-refresh")
    device_config = FakeDeviceConfig(tmp_path)
    device_config.config["display_refresh_resource_guard_enabled"] = False
    playlist = _runtime_playlist(
        _runtime_plugin_data(
            "live_plugin",
            "Live Plugin",
            latest_refresh_time="2026-05-26T07:00:00+00:00",
            interval=3600,
        )
    )
    plugin_instance = playlist.plugins[0]
    Image.new("RGB", (2, 1), "black").save(tmp_path / plugin_instance.get_image_path())

    image = PlaylistRefresh(playlist, plugin_instance, display_cached_only=True).execute(
        FakePlugin(calls, live_state={"active": True, "interval_seconds": 60}),
        device_config,
        datetime(2026, 5, 26, 7, 2, tzinfo=timezone.utc),
    )

    assert calls == []
    assert image.getpixel((0, 0)) == (0, 0, 0)


def test_background_cache_rechecks_pressure_before_second_candidate(monkeypatch):
    calls = []
    tmp_path = make_test_dir("background-pressure-recheck")
    device_config = FakeDeviceConfig(tmp_path)
    task = RefreshTask(device_config, display_manager=None)
    playlist = _runtime_playlist(
        _runtime_plugin_data("a_plugin", "A Plugin", latest_refresh_time="2026-05-26T07:00:00+00:00", interval=60),
        _runtime_plugin_data("b_plugin", "B Plugin", latest_refresh_time="2026-05-26T07:00:00+00:00", interval=60),
    )
    for instance in playlist.plugins:
        Image.new("RGB", (1, 1), "black").save(tmp_path / instance.get_image_path())
    pressure = iter([False, True])
    monkeypatch.setattr(task, "_cache_refresh_under_resource_pressure", lambda: next(pressure))
    monkeypatch.setattr("src.refresh_task.get_plugin_instance", lambda config: FakePlugin(calls))

    task._refresh_due_plugin_instances(
        playlist,
        datetime(2026, 5, 26, 7, 2, tzinfo=timezone.utc),
    )

    assert calls == ["a_plugin"]
    assert playlist.plugins[0].latest_refresh_time == "2026-05-26T07:02:00+00:00"
    assert playlist.plugins[1].latest_refresh_time == "2026-05-26T07:00:00+00:00"


def test_default_display_pressure_trips_below_150_mib_with_safe_swap(monkeypatch):
    calls = []
    tmp_path = make_test_dir("display-pressure-default-memory")
    device_config = FakeDeviceConfig(tmp_path)
    playlist = _runtime_playlist(
        _runtime_plugin_data("live_plugin", "Live Plugin", latest_refresh_time="2026-05-26T07:00:00+00:00")
    )
    instance = playlist.plugins[0]
    Image.new("RGB", (2, 1), "black").save(tmp_path / instance.get_image_path())
    memory = type("Memory", (), {"available": 149 * 1024 * 1024})()
    swap = type("Swap", (), {"percent": 0.0})()
    monkeypatch.setattr("src.refresh_task.psutil.virtual_memory", lambda: memory)
    monkeypatch.setattr("src.refresh_task.psutil.swap_memory", lambda: swap)

    image = PlaylistRefresh(playlist, instance, display_cached_only=True).execute(
        FakePlugin(calls, live_state={"active": True, "interval_seconds": 60}),
        device_config,
        datetime(2026, 5, 26, 7, 2, tzinfo=timezone.utc),
    )

    assert calls == []
    assert image.getpixel((0, 0)) == (0, 0, 0)


def test_default_display_pressure_trips_at_30_percent_swap_with_safe_memory(monkeypatch):
    calls = []
    tmp_path = make_test_dir("display-pressure-default-swap")
    device_config = FakeDeviceConfig(tmp_path)
    playlist = _runtime_playlist(
        _runtime_plugin_data("live_plugin", "Live Plugin", latest_refresh_time="2026-05-26T07:00:00+00:00")
    )
    instance = playlist.plugins[0]
    Image.new("RGB", (2, 1), "black").save(tmp_path / instance.get_image_path())
    memory = type("Memory", (), {"available": 512 * 1024 * 1024})()
    swap = type("Swap", (), {"percent": 30.0})()
    monkeypatch.setattr("src.refresh_task.psutil.virtual_memory", lambda: memory)
    monkeypatch.setattr("src.refresh_task.psutil.swap_memory", lambda: swap)

    image = PlaylistRefresh(playlist, instance, display_cached_only=True).execute(
        FakePlugin(calls, live_state={"active": True, "interval_seconds": 60}),
        device_config,
        datetime(2026, 5, 26, 7, 2, tzinfo=timezone.utc),
    )

    assert calls == []
    assert image.getpixel((0, 0)) == (0, 0, 0)


def _make_scheduler_fairness_task(name, *, refresh_time, clock=None):
    tmp_path = make_test_dir(name)
    playlist = _runtime_playlist(
        _runtime_plugin_data(
            "sports_dashboard",
            "SportsDashboard",
            latest_refresh_time="2026-05-26T07:00:00+00:00",
            interval=900,
        ),
        _runtime_plugin_data(
            "ordinary_plugin",
            "Ordinary Plugin",
            latest_refresh_time="2026-05-26T07:00:00+00:00",
            interval=3600,
        ),
    )
    sports, ordinary = playlist.plugins
    plugin_keys = [sports.instance_uuid, ordinary.instance_uuid]
    playlist.current_plugin_index = 0
    playlist.plugin_rotation_pool = list(plugin_keys)
    playlist.plugin_rotation_queue = [ordinary.instance_uuid, sports.instance_uuid]

    task, device_config, _clock = _make_runtime_task(
        tmp_path,
        playlists=[playlist],
        clock=clock,
        cycle_seconds=300,
    )
    device_config.config.update({"theme_mode": "day", "active_theme": "day"})
    device_config.refresh_info = RefreshInfo(
        refresh_type="Playlist",
        playlist=playlist.name,
        plugin_id=sports.plugin_id,
        plugin_instance=sports.name,
        refresh_time=refresh_time,
        image_hash="sports",
    )
    task.runtime_state.set_display_state(
        "committed",
        instance_uuid=sports.instance_uuid,
        changed_at=refresh_time,
    )
    return task, device_config, playlist, sports, ordinary


@pytest.mark.parametrize("live_due", [False, True])
def test_playlist_cycle_wins_before_live_or_sports_priority(monkeypatch, live_due):
    task, _device_config, _playlist, sports, ordinary = (
        _make_scheduler_fairness_task(
            "scheduler-cycle-priority",
            refresh_time="2026-05-26T07:00:00+00:00",
        )
    )
    monkeypatch.setattr(
        task,
        "_snapshot_live_refresh_due",
        lambda instance, _current_dt: (
            live_due and instance.instance_uuid == sports.instance_uuid
        ),
    )

    command = task._select_scheduled_command(
        datetime(2026, 5, 26, 7, 20, tzinfo=timezone.utc)
    )

    assert command is not None
    assert command.instance_uuid == ordinary.instance_uuid
    assert command.source is CommandSource.SCHEDULER
    assert command.priority == 85


@pytest.mark.parametrize("under_pressure", [False, True])
def test_live_refresh_cycles_do_not_move_playlist_rotation_anchor(
    monkeypatch,
    under_pressure,
):
    anchor = "2026-05-26T07:00:00+00:00"
    task, device_config, playlist, sports, ordinary = (
        _make_scheduler_fairness_task(
            "scheduler-live-anchor",
            refresh_time=anchor,
        )
    )
    device_config.config["display_triggered_refresh_enabled"] = True
    monkeypatch.setattr(
        task,
        "_snapshot_live_refresh_due",
        lambda instance, _current_dt: instance.instance_uuid == sports.instance_uuid,
    )
    monkeypatch.setattr(
        "src.refresh_task._display_refresh_under_resource_pressure",
        lambda _device_config, **_kwargs: under_pressure,
    )

    for minute in (1, 2):
        current = datetime(2026, 5, 26, 7, minute, tzinfo=timezone.utc)
        command = task._select_scheduled_command(current)
        if under_pressure:
            assert command is None
            continue
        assert command is not None
        assert command.instance_uuid == sports.instance_uuid
        assert command.source is CommandSource.LIVE
        resolved = task._resolve_playlist_command(command)
        task._set_render_metadata(True, True, {})
        task._commit_command_result(
            command,
            resolved,
            Image.new("RGB", (2, 1), (minute, minute, minute)),
            current,
        )
        assert device_config.refresh_info.refresh_time == anchor

    rotation = task._select_scheduled_command(
        datetime(2026, 5, 26, 7, 5, tzinfo=timezone.utc)
    )

    assert rotation is not None
    assert rotation.instance_uuid == ordinary.instance_uuid
    assert rotation.source is CommandSource.SCHEDULER


def test_legacy_scheduled_selector_does_not_emit_display_live_refresh_by_default(
    monkeypatch,
):
    task, device_config, _playlist, sports, _ordinary = (
        _make_scheduler_fairness_task(
            "scheduler-live-default-off",
            refresh_time="2026-05-26T07:19:00+00:00",
        )
    )
    assert "display_triggered_refresh_enabled" not in device_config.config
    monkeypatch.setattr(
        task,
        "_snapshot_live_refresh_due",
        lambda instance, _current_dt: instance.instance_uuid == sports.instance_uuid,
    )
    monkeypatch.setattr(
        "src.refresh_task._display_refresh_under_resource_pressure",
        lambda _device_config, **_kwargs: False,
    )

    command = task._select_scheduled_command(
        datetime(2026, 5, 26, 7, 20, tzinfo=timezone.utc)
    )

    assert command is None


def test_live_refresh_does_not_preempt_a_different_displayed_instance(monkeypatch):
    task, device_config, playlist, sports, ordinary = (
        _make_scheduler_fairness_task(
            "scheduler-live-non-current",
            refresh_time="2026-05-26T07:19:00+00:00",
        )
    )
    device_config.refresh_info = RefreshInfo(
        refresh_type="Playlist",
        playlist=playlist.name,
        plugin_id=ordinary.plugin_id,
        plugin_instance=ordinary.name,
        refresh_time="2026-05-26T07:19:00+00:00",
        image_hash="ordinary",
    )
    task.runtime_state.set_display_state(
        "committed",
        instance_uuid=ordinary.instance_uuid,
        changed_at="2026-05-26T07:19:00+00:00",
    )
    monkeypatch.setattr(
        task,
        "_snapshot_live_refresh_due",
        lambda instance, _current_dt: instance.instance_uuid == sports.instance_uuid,
    )
    monkeypatch.setattr(
        "src.refresh_task._display_refresh_under_resource_pressure",
        lambda _device_config, **_kwargs: False,
    )

    command = task._select_scheduled_command(
        datetime(2026, 5, 26, 7, 20, tzinfo=timezone.utc)
    )

    assert command is None


def test_background_live_opt_in_includes_non_displayed_instance(monkeypatch):
    task, _device_config, playlist, sports, ordinary = (
        _make_scheduler_fairness_task(
            "scheduler-background-live-opt-in",
            refresh_time="2026-05-26T07:19:00+00:00",
        )
    )
    task.runtime_state.set_display_state(
        "committed",
        instance_uuid=ordinary.instance_uuid,
        changed_at="2026-05-26T07:19:00+00:00",
    )
    plugin = SimpleNamespace(
        wants_background_live_refresh=lambda _settings, _current_dt: True,
    )
    monkeypatch.setattr(
        task,
        "_get_plugin_for_snapshot",
        lambda instance, require_live_refresh=False: (
            plugin if instance.instance_uuid == sports.instance_uuid else None
        ),
    )
    monkeypatch.setattr(
        task,
        "_snapshot_live_refresh_state",
        lambda instance, _current_dt, plugin=None: (
            {"active": True, "interval_seconds": 60}
            if instance.instance_uuid == sports.instance_uuid
            else None
        ),
    )

    candidates = task._live_due_candidates(
        playlist,
        task.runtime_state.snapshot().instances,
        datetime(2026, 5, 26, 7, 20, tzinfo=timezone.utc),
        refresh_task_module.ResourceTier.HEALTHY,
    )

    assert len(candidates) == 1
    assert candidates[0].instance.instance_uuid == sports.instance_uuid
    assert candidates[0].lane is RefreshLane.LIVE


def test_background_live_opt_in_command_resolves_when_another_instance_is_displayed(monkeypatch):
    task, _device_config, _playlist, sports, ordinary = (
        _make_scheduler_fairness_task(
            "scheduler-background-live-command",
            refresh_time="2026-05-26T07:19:00+00:00",
        )
    )
    task.runtime_state.set_display_state(
        "committed",
        instance_uuid=ordinary.instance_uuid,
        changed_at="2026-05-26T07:19:00+00:00",
    )
    plugin = SimpleNamespace(
        wants_background_live_refresh=lambda _settings, _current_dt: True,
    )
    monkeypatch.setattr(
        task,
        "_get_plugin_for_snapshot",
        lambda instance, require_live_refresh=False: (
            plugin if instance.instance_uuid == sports.instance_uuid else None
        ),
    )
    monkeypatch.setattr(
        task,
        "_snapshot_live_refresh_state",
        lambda instance, _current_dt, plugin=None: (
            {"active": True, "interval_seconds": 60}
            if instance.instance_uuid == sports.instance_uuid
            else None
        ),
    )

    command = task._select_independent_refresh_command(
        datetime(2026, 5, 26, 7, 20, tzinfo=timezone.utc)
    )

    assert command is not None
    assert command.instance_uuid == sports.instance_uuid
    assert command.intent is RefreshIntent.LIVE_REFRESH
    assert command.payload.get("expected_displayed_instance_uuid") is None
    assert command.payload.get("background_live_refresh") is True
    resolved = task._resolve_playlist_command(command)
    assert resolved is not None
    assert task._enqueue_live_display_followup(
        command,
        resolved,
        datetime(2026, 5, 26, 7, 20, tzinfo=timezone.utc),
        "day",
    ) is None


def test_stale_display_uuid_never_falls_back_to_same_name_live_instance(monkeypatch):
    task, _device_config, _playlist, sports, _ordinary = (
        _make_scheduler_fairness_task(
            "scheduler-live-stale-uuid",
            refresh_time="2026-05-26T07:19:00+00:00",
        )
    )
    task.runtime_state.set_display_state(
        "committed",
        instance_uuid="stale-replaced-instance-uuid",
        changed_at="2026-05-26T07:19:30+00:00",
    )
    monkeypatch.setattr(
        task,
        "_snapshot_live_refresh_due",
        lambda instance, _current_dt: instance.instance_uuid == sports.instance_uuid,
    )
    monkeypatch.setattr(
        "src.refresh_task._display_refresh_under_resource_pressure",
        lambda _device_config, **_kwargs: False,
    )

    command = task._select_scheduled_command(
        datetime(2026, 5, 26, 7, 20, tzinfo=timezone.utc)
    )

    assert command is None


def test_live_command_is_stale_if_display_changes_before_execution(monkeypatch):
    task, device_config, playlist, sports, ordinary = (
        _make_scheduler_fairness_task(
            "scheduler-live-stale-before-execute",
            refresh_time="2026-05-26T07:19:00+00:00",
        )
    )
    device_config.config["display_triggered_refresh_enabled"] = True
    monkeypatch.setattr(
        task,
        "_snapshot_live_refresh_due",
        lambda instance, _current_dt: instance.instance_uuid == sports.instance_uuid,
    )
    monkeypatch.setattr(
        "src.refresh_task._display_refresh_under_resource_pressure",
        lambda _device_config, **_kwargs: False,
    )
    command = task._select_scheduled_command(
        datetime(2026, 5, 26, 7, 20, tzinfo=timezone.utc)
    )
    assert command is not None
    assert command.source is CommandSource.LIVE

    device_config.refresh_info = RefreshInfo(
        refresh_type="Playlist",
        playlist=playlist.name,
        plugin_id=ordinary.plugin_id,
        plugin_instance=ordinary.name,
        refresh_time="2026-05-26T07:19:30+00:00",
        image_hash="ordinary",
    )
    task.runtime_state.set_display_state(
        "committed",
        instance_uuid=ordinary.instance_uuid,
        changed_at="2026-05-26T07:19:30+00:00",
    )

    assert task._resolve_playlist_command(command) is None


def test_live_command_revalidates_current_display_before_commit(monkeypatch):
    task, device_config, playlist, sports, ordinary = (
        _make_scheduler_fairness_task(
            "scheduler-live-stale-before-commit",
            refresh_time="2026-05-26T07:19:00+00:00",
        )
    )
    device_config.config["display_triggered_refresh_enabled"] = True
    monkeypatch.setattr(
        task,
        "_snapshot_live_refresh_due",
        lambda instance, _current_dt: instance.instance_uuid == sports.instance_uuid,
    )
    monkeypatch.setattr(
        "src.refresh_task._display_refresh_under_resource_pressure",
        lambda _device_config, **_kwargs: False,
    )
    command = task._select_scheduled_command(
        datetime(2026, 5, 26, 7, 20, tzinfo=timezone.utc)
    )
    resolved = task._resolve_playlist_command(command)
    assert resolved is not None

    changed_at = "2026-05-26T07:19:30+00:00"
    device_config.refresh_info = RefreshInfo(
        refresh_type="Playlist",
        playlist=playlist.name,
        plugin_id=ordinary.plugin_id,
        plugin_instance=ordinary.name,
        refresh_time=changed_at,
        image_hash="ordinary",
    )
    task.runtime_state.set_display_state(
        "committed",
        instance_uuid=ordinary.instance_uuid,
        changed_at=changed_at,
    )
    task._set_render_metadata(False, False, {})

    with pytest.raises(TaskCancelled, match="live display target changed"):
        task._commit_command_result(
            command,
            resolved,
            Image.new("RGB", (2, 1), "white"),
            datetime(2026, 5, 26, 7, 20, tzinfo=timezone.utc),
        )

    assert device_config.refresh_info.plugin_instance == ordinary.name
    assert task.display_manager.calls == []


def test_sports_interval_does_not_bypass_playlist_cycle(monkeypatch):
    task, _device_config, _playlist, sports, _ordinary = (
        _make_scheduler_fairness_task(
            "scheduler-sports-interval",
            refresh_time="2026-05-26T07:19:00+00:00",
        )
    )
    monkeypatch.setattr(task, "_snapshot_live_refresh_due", lambda *_args: False)

    current = datetime(2026, 5, 26, 7, 20, tzinfo=timezone.utc)
    command = task._select_scheduled_command(current)
    background = task._select_background_commands(current)

    assert command is None
    assert all(item.instance_uuid != sports.instance_uuid for item in background)


def test_live_due_background_policy_remains_reachable(monkeypatch):
    task, _device_config, _playlist, sports, ordinary = (
        _make_scheduler_fairness_task(
            "scheduler-live-background",
            refresh_time="2026-05-26T07:19:00+00:00",
        )
    )
    monkeypatch.setattr(
        task,
        "_snapshot_live_refresh_due",
        lambda instance, _current_dt, plugin=None: (
            instance.instance_uuid == ordinary.instance_uuid
        ),
    )
    monkeypatch.setattr(
        task,
        "_snapshot_background_cache_disabled",
        lambda instance: instance.instance_uuid == sports.instance_uuid,
    )

    current = datetime(2026, 5, 26, 7, 20, tzinfo=timezone.utc)
    display = task._select_scheduled_command(current)
    background = task._select_background_commands(current)

    assert display is None
    ordinary_work = [
        item for item in background if item.instance_uuid == ordinary.instance_uuid
    ]
    assert len(ordinary_work) == 1
    assert ordinary_work[0].kind is CommandKind.CACHE_REFRESH
    assert ordinary_work[0].source is CommandSource.BACKGROUND


def test_live_due_background_candidate_precedes_missing_ordinary_work(monkeypatch):
    task, _device_config, _playlist, sports, ordinary = (
        _make_scheduler_fairness_task(
            "scheduler-live-background-priority",
            refresh_time="2026-05-26T07:19:00+00:00",
        )
    )
    sports.settings["backgroundCacheRefreshEnabled"] = True
    monkeypatch.setattr(
        task,
        "_snapshot_live_refresh_due",
        lambda instance, _current_dt, plugin=None: (
            instance.instance_uuid == sports.instance_uuid
        ),
    )
    monkeypatch.setattr(
        task,
        "_snapshot_should_refresh",
        lambda instance, _current_dt: instance.instance_uuid == ordinary.instance_uuid,
    )

    background = task._select_background_commands(
        datetime(2026, 5, 26, 7, 20, tzinfo=timezone.utc)
    )

    assert len(background) == 1
    assert background[0].instance_uuid == sports.instance_uuid
    assert background[0].source is CommandSource.BACKGROUND


def test_live_failures_never_delay_the_playlist_rotation_deadline(monkeypatch):
    task, device_config, _playlist, sports, current_dt, _anchor = (
        _sports_live_runtime(
            "scheduler-live-failure-fairness",
            background_value=True,
        )
    )
    task.retry_registry = RetryRegistry(jitter=lambda delay: delay)
    monkeypatch.setattr(
        "src.refresh_task.get_plugin_instance",
        lambda _config: FakePlugin(
            [],
            live_state={"active": True, "interval_seconds": 60},
        ),
    )
    monkeypatch.setattr(
        task,
        "_resource_sample",
        lambda: ResourceSample(available_mb=512, swap_percent=0),
    )
    background_probe = SimpleNamespace(
        wants_background_live_refresh=lambda _settings, _current_dt: True,
    )
    monkeypatch.setattr(
        task,
        "_get_plugin_for_snapshot",
        lambda _instance, require_live_refresh=False: background_probe,
    )
    monkeypatch.setattr(
        task,
        "_snapshot_live_refresh_state",
        lambda _instance, _current_dt, plugin=None: {
            "active": True,
            "interval_seconds": 60,
        },
    )
    command = task._select_independent_refresh_command(current_dt)
    assert command.intent is RefreshIntent.LIVE_REFRESH
    monkeypatch.setattr(
        task,
        "_execute_command",
        lambda _command: (_ for _ in ()).throw(RuntimeError("live render failed")),
    )
    submitted = task.refresh_queue.submit(command)
    task._process_queue_entry(task.refresh_queue.take(timeout=0))

    assert task.refresh_queue.get_entry(submitted.id).job.status is JobStatus.FAILED
    assert [entry.key for entry in task.retry_registry.snapshot()] == [
        f"{sports.instance_uuid}:live"
    ]
    device_config.refresh_info.refresh_time = (
        current_dt - timedelta(minutes=6)
    ).isoformat()
    display = task._select_cached_display_command(current_dt)
    assert display is not None
    assert display.intent is RefreshIntent.DISPLAY_CACHE


def test_theme_refresh_failure_default_retry_cooldown_is_600_seconds():
    tmp_path = make_test_dir("theme-default-cooldown")
    device_config = FakeDeviceConfig(tmp_path)
    device_config.config["active_theme"] = "day"
    task = RefreshTask(device_config, display_manager=None)
    failed_at = datetime(2026, 5, 26, 22, 8, tzinfo=timezone.utc)
    theme = {"mode": "night", "source": "weather", "reason": "sunset"}

    task._mark_theme_refresh_failed(theme, failed_at, RuntimeError("render failed"))

    failure = device_config.config["active_theme_refresh_failure"]
    retry_after = datetime.fromisoformat(failure["retry_after"])
    assert (retry_after - failed_at).total_seconds() == 600
    assert not task._has_theme_changed(theme, failed_at + timedelta(seconds=599))
    assert task._has_theme_changed(theme, failed_at + timedelta(seconds=600))


@pytest.mark.parametrize("cache_state", ["missing", "corrupt"])
def test_display_pressure_missing_or_corrupt_cache_uses_placeholder_without_render(monkeypatch, cache_state):
    calls = []
    tmp_path = make_test_dir(f"display-pressure-{cache_state}-cache")
    device_config = FakeDeviceConfig(tmp_path)
    playlist = _runtime_playlist(
        _runtime_plugin_data("live_plugin", "Live Plugin", latest_refresh_time="2026-05-26T07:00:00+00:00")
    )
    instance = playlist.plugins[0]
    cache_path = tmp_path / instance.get_image_path()
    if cache_state == "corrupt":
        cache_path.write_bytes(b"not an image")
    memory = type("Memory", (), {"available": 149 * 1024 * 1024})()
    swap = type("Swap", (), {"percent": 0.0})()
    monkeypatch.setattr("src.refresh_task.psutil.virtual_memory", lambda: memory)
    monkeypatch.setattr("src.refresh_task.psutil.swap_memory", lambda: swap)

    image = PlaylistRefresh(playlist, instance, display_cached_only=True).execute(
        FakePlugin(calls, live_state={"active": True, "interval_seconds": 60}),
        device_config,
        datetime(2026, 5, 26, 7, 2, tzinfo=timezone.utc),
    )

    assert calls == []
    assert image.size == (800, 480)


def test_cache_pending_placeholder_uses_shared_base_ui_fonts(monkeypatch):
    tmp_path = make_test_dir("cache-pending-fonts")
    device_config = FakeDeviceConfig(tmp_path)
    playlist = _runtime_playlist(_runtime_plugin_data("plugin", "Plugin"))
    instance = playlist.plugins[0]
    calls = []
    font_path = Path(__file__).resolve().parents[1] / "src/static/fonts/NotoSansSC-VF.ttf"
    monkeypatch.setattr(
        refresh_task_module,
        "get_base_ui_font",
        lambda size, bold=False: calls.append((size, bold))
        or ImageFont.truetype(font_path, size),
        raising=False,
    )

    image = PlaylistRefresh(playlist, instance)._placeholder_image(device_config)

    assert image.size == (800, 480)
    assert calls == [(40, True), (17, False)]


def test_overdue_empty_playlist_advances_monotonic_attempt_deadline():
    tmp_path = make_test_dir("runtime-empty-deadline")
    task, _device_config, _clock = _make_runtime_task(tmp_path, playlists=[])

    task._run_one_iteration_for_test()
    first = task.scheduler_snapshot().next_attempt_monotonic
    task._run_one_iteration_for_test()

    assert first >= 30.0
    assert task.attempt_count == 1


def test_scheduler_burst_advances_two_reviewed_inline_jobs_without_admitting_heavy(
    monkeypatch,
):
    now = datetime(2026, 8, 14, 12, 0, tzinfo=timezone.utc)
    simple = _runtime_plugin_data(
        "simple_calendar",
        "Simple",
        latest_refresh_time=None,
        interval=300,
    )
    simple["instance_uuid"] = "11111111111111111111111111111111"
    species = _runtime_plugin_data(
        "species_radar",
        "Species",
        latest_refresh_time=None,
        interval=300,
    )
    species["instance_uuid"] = "22222222222222222222222222222222"
    weather = _runtime_plugin_data(
        "weather",
        "Weather",
        latest_refresh_time=None,
        interval=300,
    )
    weather["instance_uuid"] = "33333333333333333333333333333333"
    playlist = _runtime_playlist(simple, species, weather)
    task, device_config, _clock = _make_runtime_task(
        make_test_dir("scheduler-bounded-inline-burst"),
        playlists=[playlist],
        cycle_seconds=300,
    )
    device_config.config["scheduler_lightweight_burst_limit"] = 2
    calls = []
    monkeypatch.setattr(
        "src.refresh_task.get_plugin_instance",
        lambda _config: CapturePlugin(calls),
    )
    monkeypatch.setattr(task, "_get_current_datetime", lambda: now)
    monkeypatch.setattr(
        task,
        "_resource_sample",
        lambda: ResourceSample(available_mb=512, swap_percent=0),
    )
    monkeypatch.setattr(task, "_memory_watchdog_should_restart", lambda: False)
    monkeypatch.setattr(
        task,
        "_sample_disk_pressure",
        lambda: DiskPressureTier.HEALTHY,
    )
    monkeypatch.setattr(
        task,
        "_run_cache_lifecycle_maintenance",
        lambda _tier: None,
    )
    monkeypatch.setattr(task, "_run_memory_maintenance", lambda *_a, **_k: None)

    first = task._run_one_iteration_for_test()
    second = task._run_one_iteration_for_test()
    bounded_stop = task._run_one_iteration_for_test()

    assert first.command.plugin_id == "simple_calendar"
    assert second.command.plugin_id == "species_radar"
    assert bounded_stop is None
    assert [call["id"] for call in calls] == ["simple_calendar", "species_radar"]
    assert task.attempt_count == 2
    runtime_instances = task.runtime_state.snapshot().instances
    assert runtime_instances[
        "11111111111111111111111111111111"
    ].data.last_success_at == now.isoformat()
    assert runtime_instances[
        "22222222222222222222222222222222"
    ].data.last_success_at == now.isoformat()


def test_manual_work_preempts_and_ends_an_armed_lightweight_scheduler_burst(
    monkeypatch,
):
    now = datetime(2026, 8, 14, 12, 0, tzinfo=timezone.utc)
    first_data = _runtime_plugin_data(
        "simple_calendar",
        "Simple",
        latest_refresh_time=None,
        interval=300,
    )
    first_data["instance_uuid"] = "11111111111111111111111111111111"
    followup_data = _runtime_plugin_data(
        "species_radar",
        "Species",
        latest_refresh_time=None,
        interval=300,
    )
    followup_data["instance_uuid"] = "22222222222222222222222222222222"
    playlist = _runtime_playlist(first_data, followup_data)
    task, _device_config, _clock = _make_runtime_task(
        make_test_dir("scheduler-inline-burst-manual-preemption"),
        playlists=[playlist],
        cycle_seconds=300,
    )
    calls = []
    monkeypatch.setattr(
        "src.refresh_task.get_plugin_instance",
        lambda _config: CapturePlugin(calls),
    )
    monkeypatch.setattr(task, "_get_current_datetime", lambda: now)
    monkeypatch.setattr(
        task,
        "_resource_sample",
        lambda: ResourceSample(available_mb=512, swap_percent=0),
    )
    monkeypatch.setattr(task, "_memory_watchdog_should_restart", lambda: False)
    monkeypatch.setattr(
        task,
        "_sample_disk_pressure",
        lambda: DiskPressureTier.HEALTHY,
    )
    monkeypatch.setattr(
        task,
        "_run_cache_lifecycle_maintenance",
        lambda _tier: None,
    )
    monkeypatch.setattr(task, "_run_memory_maintenance", lambda *_a, **_k: None)

    first = task._run_one_iteration_for_test()
    manual_command = task._command_from_refresh_action(
        ManualRefresh("manual_plugin", {"id": "urgent-manual"})
    )
    task.refresh_queue.submit(manual_command)
    urgent = task._run_one_iteration_for_test()
    no_followup = task._run_one_iteration_for_test()

    assert first.command.plugin_id == "simple_calendar"
    assert urgent.command.id == manual_command.id
    assert urgent.command.source is CommandSource.MANUAL
    assert no_followup is None
    assert [call["id"] for call in calls] == [
        "simple_calendar",
        "urgent-manual",
    ]


def test_stop_observed_at_lightweight_terminal_never_schedules_a_followup(
    monkeypatch,
):
    now = datetime(2026, 8, 14, 12, 0, tzinfo=timezone.utc)
    first_data = _runtime_plugin_data(
        "simple_calendar",
        "Simple",
        latest_refresh_time=None,
        interval=300,
    )
    first_data["instance_uuid"] = "11111111111111111111111111111111"
    followup_data = _runtime_plugin_data(
        "species_radar",
        "Species",
        latest_refresh_time=None,
        interval=300,
    )
    followup_data["instance_uuid"] = "22222222222222222222222222222222"
    task, _device_config, _clock = _make_runtime_task(
        make_test_dir("scheduler-inline-burst-stop"),
        playlists=[_runtime_playlist(first_data, followup_data)],
        cycle_seconds=300,
    )
    monkeypatch.setattr(task, "_get_current_datetime", lambda: now)
    monkeypatch.setattr(
        task,
        "_resource_sample",
        lambda: ResourceSample(available_mb=512, swap_percent=0),
    )
    monkeypatch.setattr(task, "_memory_watchdog_should_restart", lambda: False)
    monkeypatch.setattr(
        task,
        "_sample_disk_pressure",
        lambda: DiskPressureTier.HEALTHY,
    )
    monkeypatch.setattr(
        task,
        "_run_cache_lifecycle_maintenance",
        lambda _tier: None,
    )
    monkeypatch.setattr(task, "_run_memory_maintenance", lambda *_a, **_k: None)
    executions = []

    def finish_while_stopping(command):
        executions.append(command.instance_uuid)
        task.stop_event.set()

    monkeypatch.setattr(task, "_execute_command", finish_while_stopping)

    first = task._run_one_iteration_for_test()
    after_stop = task._run_one_iteration_for_test()

    assert first.command.plugin_id == "simple_calendar"
    assert after_stop is None
    assert executions == ["11111111111111111111111111111111"]
    assert task.attempt_count == 1


@pytest.mark.parametrize(
    ("terminal_error", "expected_status", "expected_error_code"),
    [
        (RuntimeError("render failed"), JobStatus.FAILED, "refresh_failed"),
        (
            PluginRefreshDeferred(
                reason="candidate_pool_temporarily_exhausted",
                phase="bank_hydration",
                minimum_seconds=30 * 60,
            ),
            JobStatus.CANCELED,
            "plugin_refresh_deferred",
        ),
        (
            ResourcePressureDeferred(
                reason="image_resource_pressure",
                phase="render",
                available_mb=80,
                swap_percent=70,
            ),
            JobStatus.CANCELED,
            "resource_pressure_deferred",
        ),
    ],
)
def test_failed_or_deferred_lightweight_terminal_never_arms_a_burst(
    monkeypatch,
    terminal_error,
    expected_status,
    expected_error_code,
):
    now = datetime(2026, 8, 14, 12, 0, tzinfo=timezone.utc)
    plugin_data = _runtime_plugin_data(
        "simple_calendar",
        "Simple",
        latest_refresh_time=None,
        interval=300,
    )
    task, _device_config, _clock = _make_runtime_task(
        make_test_dir(f"scheduler-no-burst-{expected_error_code}"),
        playlists=[_runtime_playlist(plugin_data)],
        cycle_seconds=300,
    )
    monkeypatch.setattr(task, "_get_current_datetime", lambda: now)
    monkeypatch.setattr(
        task,
        "_resource_sample",
        lambda: ResourceSample(available_mb=512, swap_percent=0),
    )
    monkeypatch.setattr(task, "_memory_watchdog_should_restart", lambda: False)
    monkeypatch.setattr(
        task,
        "_sample_disk_pressure",
        lambda: DiskPressureTier.HEALTHY,
    )
    monkeypatch.setattr(
        task,
        "_run_cache_lifecycle_maintenance",
        lambda _tier: None,
    )
    monkeypatch.setattr(task, "_run_memory_maintenance", lambda *_a, **_k: None)

    def fail(_command):
        raise terminal_error

    monkeypatch.setattr(task, "_execute_command", fail)

    first = task._run_one_iteration_for_test()
    no_followup = task._run_one_iteration_for_test()
    completed = task.refresh_queue.get_entry(first.job.id)

    assert completed.job.status is expected_status
    assert completed.job.error_code == expected_error_code
    assert no_followup is None
    assert task.attempt_count == 1


def test_ian_arrival_preempts_and_clears_an_armed_lightweight_scheduler_burst(
    monkeypatch,
):
    now = datetime(2026, 8, 14, 12, 0, tzinfo=timezone.utc)
    first_data = _runtime_plugin_data(
        "simple_calendar",
        "Simple",
        latest_refresh_time=None,
        interval=300,
    )
    first_data["instance_uuid"] = "11111111111111111111111111111111"
    followup_data = _runtime_plugin_data(
        "species_radar",
        "Species",
        latest_refresh_time=None,
        interval=300,
    )
    followup_data["instance_uuid"] = "22222222222222222222222222222222"
    task, _device_config, clock = _make_runtime_task(
        make_test_dir("scheduler-inline-burst-ian-preemption"),
        playlists=[_runtime_playlist(first_data, followup_data)],
        cycle_seconds=300,
        ian_resource_sampler=lambda: IanResourceSample(
            available_mb=100,
            swap_percent=0,
        ),
    )
    calls = []
    monkeypatch.setattr(
        "src.refresh_task.get_plugin_instance",
        lambda _config: CapturePlugin(calls),
    )
    monkeypatch.setattr(task, "_get_current_datetime", lambda: now)
    monkeypatch.setattr(
        task,
        "_resource_sample",
        lambda: ResourceSample(available_mb=512, swap_percent=0),
    )
    monkeypatch.setattr(task, "_memory_watchdog_should_restart", lambda: False)
    monkeypatch.setattr(
        task,
        "_sample_disk_pressure",
        lambda: DiskPressureTier.HEALTHY,
    )
    monkeypatch.setattr(
        task,
        "_run_cache_lifecycle_maintenance",
        lambda _tier: None,
    )
    monkeypatch.setattr(task, "_run_memory_maintenance", lambda *_a, **_k: None)

    first = task._run_one_iteration_for_test()
    sports = RefreshCommand.create(
        kind=CommandKind.CACHE_REFRESH,
        source=CommandSource.BACKGROUND,
        plugin_id="sports_dashboard",
        instance_uuid="33333333333333333333333333333333",
        structural_generation=1,
        settings_revision=1,
        payload={"playlist_name": "DailyDoseOfDay"},
        now_monotonic=clock.monotonic(),
        deadline_monotonic=clock.monotonic() + 60,
        priority=10,
        intent=RefreshIntent.DATA_REFRESH,
    )
    sports_job = task.refresh_queue.submit(sports)
    retained = task._run_one_iteration_for_test()
    no_followup = task._run_one_iteration_for_test()

    assert first.command.plugin_id == "simple_calendar"
    assert retained.command.id == sports.id
    assert task.refresh_queue.get_entry(sports_job.id).job.status is JobStatus.RUNNING
    assert no_followup is None
    assert [call["id"] for call in calls] == ["simple_calendar"]
    assert task.attempt_count == 1


@pytest.mark.parametrize("closed_gate", ["disk", "restart", "resource"])
def test_hard_gate_stops_an_armed_lightweight_scheduler_burst(
    monkeypatch,
    closed_gate,
):
    now = datetime(2026, 8, 14, 12, 0, tzinfo=timezone.utc)
    first_data = _runtime_plugin_data(
        "simple_calendar",
        "Simple",
        latest_refresh_time=None,
        interval=300,
    )
    first_data["instance_uuid"] = "11111111111111111111111111111111"
    followup_data = _runtime_plugin_data(
        "species_radar",
        "Species",
        latest_refresh_time=None,
        interval=300,
    )
    followup_data["instance_uuid"] = "22222222222222222222222222222222"
    task, _device_config, _clock = _make_runtime_task(
        make_test_dir(f"scheduler-inline-burst-hard-{closed_gate}"),
        playlists=[_runtime_playlist(first_data, followup_data)],
        cycle_seconds=300,
    )
    gate = {"closed": False}
    calls = []
    monkeypatch.setattr(
        "src.refresh_task.get_plugin_instance",
        lambda _config: CapturePlugin(calls),
    )
    monkeypatch.setattr(task, "_get_current_datetime", lambda: now)
    monkeypatch.setattr(
        task,
        "_resource_sample",
        lambda: ResourceSample(
            available_mb=(60 if closed_gate == "resource" and gate["closed"] else 512),
            swap_percent=0,
        ),
    )
    monkeypatch.setattr(
        task,
        "_memory_watchdog_should_restart",
        lambda: closed_gate == "restart" and gate["closed"],
    )
    monkeypatch.setattr(
        task,
        "_sample_disk_pressure",
        lambda: (
            DiskPressureTier.HARD
            if closed_gate == "disk" and gate["closed"]
            else DiskPressureTier.HEALTHY
        ),
    )
    monkeypatch.setattr(
        task,
        "_run_cache_lifecycle_maintenance",
        lambda _tier: None,
    )
    monkeypatch.setattr(task, "_run_memory_maintenance", lambda *_a, **_k: None)

    first = task._run_one_iteration_for_test()
    gate["closed"] = True
    blocked = task._run_one_iteration_for_test()
    bounded_stop = task._run_one_iteration_for_test()

    assert first.command.plugin_id == "simple_calendar"
    assert blocked is None
    assert bounded_stop is None
    assert [call["id"] for call in calls] == ["simple_calendar"]
    assert task.attempt_count == 2


def test_followup_admission_early_terminal_clears_remaining_limit_four_budget(
    monkeypatch,
):
    now = datetime(2026, 8, 14, 12, 0, tzinfo=timezone.utc)
    plugins = []
    for plugin_id, name, instance_uuid in (
        (
            "simple_calendar",
            "Simple",
            "11111111111111111111111111111111",
        ),
        (
            "species_radar",
            "Species",
            "22222222222222222222222222222222",
        ),
        (
            "moon_phase",
            "Moon",
            "33333333333333333333333333333333",
        ),
    ):
        plugin_data = _runtime_plugin_data(
            plugin_id,
            name,
            latest_refresh_time=None,
            interval=300,
        )
        plugin_data["instance_uuid"] = instance_uuid
        plugins.append(plugin_data)
    task, _device_config, clock = _make_runtime_task(
        make_test_dir("scheduler-followup-early-terminal-clears-budget"),
        playlists=[_runtime_playlist(*plugins)],
        cycle_seconds=300,
    )
    task.device_config.config["scheduler_lightweight_burst_limit"] = 4
    calls = []
    monkeypatch.setattr(
        "src.refresh_task.get_plugin_instance",
        lambda _config: CapturePlugin(calls),
    )
    monkeypatch.setattr(task, "_get_current_datetime", lambda: now)
    monkeypatch.setattr(
        task,
        "_resource_sample",
        lambda: ResourceSample(available_mb=512, swap_percent=0),
    )
    monkeypatch.setattr(task, "_memory_watchdog_should_restart", lambda: False)
    monkeypatch.setattr(
        task,
        "_sample_disk_pressure",
        lambda: DiskPressureTier.HEALTHY,
    )
    monkeypatch.setattr(
        task,
        "_run_cache_lifecycle_maintenance",
        lambda _tier: None,
    )
    monkeypatch.setattr(task, "_run_memory_maintenance", lambda *_a, **_k: None)
    monkeypatch.setattr(
        task,
        "_renderer_blocked_by_disk_pressure",
        lambda command: command.plugin_id == "species_radar",
    )

    first = task._run_one_iteration_for_test()
    early_terminal = task._run_one_iteration_for_test()
    early_result = task.refresh_queue.get_entry(early_terminal.job.id)
    retry_at = task.scheduler_snapshot().next_attempt_monotonic
    clock.advance(retry_at - clock.monotonic())
    regular_turn = task._run_one_iteration_for_test()

    assert first.command.plugin_id == "simple_calendar"
    assert early_terminal.command.plugin_id == "species_radar"
    assert early_terminal.command.payload["scheduler_lightweight_followup"] is True
    assert early_result.job.status is JobStatus.CANCELED
    assert early_result.job.error_code == "disk_pressure_hard"
    assert regular_turn.command.plugin_id == "species_radar"
    assert "scheduler_lightweight_followup" not in regular_turn.command.payload
    assert [call["id"] for call in calls] == ["simple_calendar"]


def test_memory_watchdog_error_advances_deadline_without_killing_scheduler(monkeypatch):
    tmp_path = make_test_dir("runtime-watchdog-deadline")
    task, _device_config, _clock = _make_runtime_task(tmp_path, playlists=[])
    monkeypatch.setattr(
        task,
        "_memory_watchdog_should_restart",
        lambda: (_ for _ in ()).throw(RuntimeError("watchdog")),
    )

    task._run_one_iteration_for_test()

    assert task.scheduler_snapshot().next_attempt_monotonic >= 30.0
    assert task.attempt_count == 1


def test_start_registers_one_non_daemon_worker():
    tmp_path = make_test_dir("runtime-single-worker")
    task, _device_config, _clock = _make_runtime_task(tmp_path, playlists=[], cycle_seconds=300)

    task.start()
    try:
        assert task.wait_until_waiting(timeout=1.0)
        first_thread = task.thread
        task.start()

        assert task.thread is first_thread
        assert task.thread.daemon is False
        assert task.thread.name == "inkypi-ian-refresh-worker"
    finally:
        task.stop(join_timeout=1.0)


def test_stop_wakes_waiting_refresh_thread_without_cycle_delay():
    tmp_path = make_test_dir("runtime-stop-wake")
    task, _device_config, _clock = _make_runtime_task(tmp_path, playlists=[], cycle_seconds=300)
    task.start()
    assert task.wait_until_waiting(timeout=1.0)

    assert task.stop(join_timeout=1.0) is True
    assert not task.thread.is_alive()
    assert task.lifecycle.state is LifecycleState.STOPPED


def test_stop_serializes_with_the_start_critical_section():
    tmp_path = make_test_dir("runtime-start-stop-serialization")
    task, _device_config, _clock = _make_runtime_task(tmp_path, playlists=[])
    stop_attempted = threading.Event()
    results = []

    def stop_task():
        stop_attempted.set()
        results.append(task.stop(join_timeout=1.0))

    task._start_lock.acquire()
    stop_thread = threading.Thread(target=stop_task)
    try:
        stop_thread.start()
        assert stop_attempted.wait(1.0)
        assert not task.stop_event.wait(0.1)
    finally:
        task._start_lock.release()
        stop_thread.join(timeout=1.0)

    assert not stop_thread.is_alive()
    assert results == [True]
    assert task.lifecycle.state is LifecycleState.STOPPED


def test_worker_exit_clears_running_state_when_queue_closes():
    tmp_path = make_test_dir("runtime-worker-running-state")
    task, _device_config, _clock = _make_runtime_task(tmp_path, playlists=[], cycle_seconds=300)
    task.start()
    try:
        assert task.wait_until_waiting(timeout=1.0)
        task.refresh_queue.begin_quiesce()
        task.thread.join(timeout=1.0)

        assert not task.thread.is_alive()
        assert task.running is False
    finally:
        task.stop(join_timeout=1.0)


def test_constructor_adopts_falsey_injected_collaborators_by_identity():
    tmp_path = make_test_dir("runtime-injected-collaborators")
    device_config = RuntimeDeviceConfig(tmp_path)
    clock = RuntimeClock()
    queue = FalseyRefreshQueue(clock=clock.monotonic, wall_clock=clock.wall_time)
    stop_event = threading.Event()
    lifecycle = LifecycleController(
        stop_event,
        queue,
        clock=clock.monotonic,
        wall_clock=clock.wall_time,
    )
    arbiter = RenderArbiter()
    retries = RetryRegistry(jitter=lambda value: value)
    scheduler = SchedulerState(retries, clock=clock.monotonic, wall_clock=clock.wall_time)

    task = RefreshTask(
        device_config,
        RecordingDisplayManager(),
        clock=clock.monotonic,
        wall_clock=clock.wall_time,
        stop_event=stop_event,
        refresh_queue=queue,
        render_arbiter=arbiter,
        lifecycle=lifecycle,
        retry_registry=retries,
        scheduler_state=scheduler,
    )

    assert task.stop_event is stop_event
    assert task.refresh_queue is queue
    assert task.render_arbiter is arbiter
    assert task.lifecycle is lifecycle
    assert task.retry_registry is retries
    assert task.scheduler_state is scheduler


def test_refresh_worker_binds_one_injected_parallel_image_runner_for_plugin_work(
    monkeypatch,
):
    tmp_path = make_test_dir("runtime-parallel-runner-binding")
    observed_runners = []

    class RunnerAwarePlugin(CapturePlugin):
        def generate_image(self, settings, device_config):
            observed_runners.append(current_parallel_image_runner())
            return super().generate_image(settings, device_config)

    governor = RuntimeResourceGovernor(
        snapshot_provider=lambda: {
            "available_mb": 180,
            "swap_percent": 10,
            "cpu_quota_cores": 3,
        }
    )
    runner = BoundedParallelStageRunner(governor=governor)
    task, _device_config, _clock = _make_runtime_task(
        tmp_path,
        playlists=[],
        resource_governor=governor,
        parallel_image_runner=runner,
    )
    monkeypatch.setattr(
        "src.refresh_task.get_plugin_instance",
        lambda _config: RunnerAwarePlugin([]),
    )

    task.start()
    try:
        job = task.submit_manual_update(ManualRefresh("manual", {"id": "manual"}))
        result = task.wait_for_job(job["id"], timeout=1.0)

        assert result["status"] == "completed"
        assert observed_runners == [runner]
    finally:
        task.stop(join_timeout=1.0)


def test_refresh_health_snapshot_reports_only_parallel_runtime_aggregates():
    tmp_path = make_test_dir("runtime-parallel-health")
    source_path = tmp_path / "source.png"
    Image.new("RGB", (8, 12), "purple").save(source_path, format="PNG")
    proc_root = tmp_path / "proc"
    cgroup_root = tmp_path / "cgroup"
    service_group = cgroup_root / "inkypi"
    (proc_root / "self").mkdir(parents=True)
    service_group.mkdir(parents=True)
    (proc_root / "self" / "cgroup").write_text(
        "0::/inkypi\n",
        encoding="utf-8",
    )
    (service_group / "cpu.stat").write_text(
        "nr_periods 100\n"
        "nr_throttled 8\n"
        "throttled_usec 4321\n",
        encoding="utf-8",
    )
    governor = RuntimeResourceGovernor(
        proc_root=proc_root,
        cgroup_root=cgroup_root,
        snapshot_provider=lambda: {
            "available_mb": 149,
            "swap_percent": 12,
            "cpu_quota_cores": 3,
        }
    )
    runner = BoundedParallelStageRunner(governor=governor)
    identity = InstanceIdentity("sensitive-instance-uuid", 7, 11)
    runner.run(
        ImmutableImageWorkset(
            descriptors=(
                {
                    "ordinal": 0,
                    "source_path": str(source_path.resolve()),
                    "source_sha256": hashlib.sha256(source_path.read_bytes()).hexdigest(),
                },
            ),
            staging_dir=str(tmp_path.resolve()),
            source_roots=(str(tmp_path.resolve()),),
            instance_identity=identity,
        ),
        TaskContext.never_cancelled(deadline_monotonic=time.monotonic() + 5),
        lambda candidate: candidate == identity,
    )
    task, _device_config, _clock = _make_runtime_task(
        tmp_path,
        playlists=[],
        resource_governor=governor,
        parallel_image_runner=runner,
    )

    parallel = task.refresh_health_snapshot()["parallel_runtime"]

    assert set(parallel) == {
        "resource_sample",
        "selected_tier",
        "worker_count",
        "degrade_reason",
        "status",
        "batch_duration_ms",
        "worker_thread_count",
        "child_peak_rss_bytes",
        "cancellation_count",
        "active_child_count",
        "cumulative",
        "cpu_throttling",
    }
    assert parallel["resource_sample"] == {
        "available_mb": 149.0,
        "swap_percent": 12.0,
        "cpu_quota_cores": 3.0,
    }
    assert parallel["worker_count"] == 1
    assert parallel["selected_tier"] == "serial"
    assert parallel["degrade_reason"] == "memory_below_parallel_threshold"
    assert parallel["status"] == "succeeded"
    assert parallel["batch_duration_ms"] >= 0
    assert parallel["worker_thread_count"] == 0
    assert parallel["child_peak_rss_bytes"] is None
    assert parallel["cancellation_count"] == 0
    assert parallel["active_child_count"] == 0
    cumulative = parallel["cumulative"]
    assert set(cumulative) == {
        "admission_tier_counts",
        "serial_fallback_reason_counts",
        "batch_count",
        "batch_duration_ms_total",
        "normalized_work_pixels_total",
        "child_peak_rss_bytes",
        "cancellation_count",
    }
    assert cumulative["admission_tier_counts"] == {
        "serial": 1,
        "2_worker": 0,
        "3_worker": 0,
    }
    assert cumulative["serial_fallback_reason_counts"] == {
        "memory_below_parallel_threshold": 1,
    }
    assert cumulative["batch_count"] == 1
    assert cumulative["batch_duration_ms_total"] >= 0
    assert cumulative["normalized_work_pixels_total"] == 96
    assert cumulative["child_peak_rss_bytes"] is None
    assert cumulative["cancellation_count"] == 0
    assert parallel["cpu_throttling"] == {
        "nr_periods": 100,
        "nr_throttled": 8,
        "throttled_usec": 4321,
    }
    assert "sensitive-instance-uuid" not in repr(parallel)


def test_refresh_health_snapshot_accumulates_identity_free_parallel_runtime_metrics():
    tmp_path = make_test_dir("runtime-parallel-health-cumulative")
    identity = InstanceIdentity("sensitive-instance-uuid", 7, 11)
    governor = RuntimeResourceGovernor(
        snapshot_provider=lambda: {
            "available_mb": 149,
            "swap_percent": 12,
            "cpu_quota_cores": 3,
        }
    )
    runner = BoundedParallelStageRunner(governor=governor)

    for ordinal, size in enumerate(((8, 12), (5, 7))):
        source_path = tmp_path / f"source-{ordinal}.png"
        Image.new("RGB", size, "purple").save(source_path, format="PNG")
        runner.run(
            ImmutableImageWorkset(
                descriptors=(
                    {
                        "ordinal": ordinal,
                        "source_path": str(source_path.resolve()),
                        "source_sha256": hashlib.sha256(
                            source_path.read_bytes()
                        ).hexdigest(),
                    },
                ),
                staging_dir=str(tmp_path.resolve()),
                source_roots=(str(tmp_path.resolve()),),
                instance_identity=identity,
            ),
            TaskContext.never_cancelled(
                deadline_monotonic=time.monotonic() + 5
            ),
            lambda candidate: candidate == identity,
        )

    task, _device_config, _clock = _make_runtime_task(
        tmp_path,
        playlists=[],
        resource_governor=governor,
        parallel_image_runner=runner,
    )

    cumulative = task.refresh_health_snapshot()["parallel_runtime"]["cumulative"]

    assert cumulative["admission_tier_counts"] == {
        "serial": 2,
        "2_worker": 0,
        "3_worker": 0,
    }
    assert cumulative["serial_fallback_reason_counts"] == {
        "memory_below_parallel_threshold": 2,
    }
    assert cumulative["batch_count"] == 2
    assert cumulative["batch_duration_ms_total"] >= 0
    assert cumulative["normalized_work_pixels_total"] == 131
    assert cumulative["child_peak_rss_bytes"] is None
    assert cumulative["cancellation_count"] == 0
    assert "sensitive-instance-uuid" not in repr(cumulative)


def test_refresh_health_snapshot_preserves_child_peak_rss_as_an_integer():
    tmp_path = make_test_dir("runtime-parallel-health-rss")

    class SnapshotRunner:
        active_processes = ()
        last_run_snapshot = {
            "worker_count": 2,
            "reason": None,
            "parallel": True,
            "batch_duration_ms": 10.5,
            "child_pid": 999,
            "worker_thread_count": 2,
            "canceled": False,
            "status": "succeeded",
            "cancellation_count": 0,
            "child_peak_rss_bytes": 63 * 1024 * 1024,
        }

    governor = RuntimeResourceGovernor(
        snapshot_provider=lambda: {
            "available_mb": 180,
            "swap_percent": 10,
            "cpu_quota_cores": 2,
        }
    )
    governor.sample()
    task, _device_config, _clock = _make_runtime_task(
        tmp_path,
        playlists=[],
        resource_governor=governor,
        parallel_image_runner=SnapshotRunner(),
    )

    parallel = task.refresh_health_snapshot()["parallel_runtime"]

    assert parallel["child_peak_rss_bytes"] == 63 * 1024 * 1024
    assert type(parallel["child_peak_rss_bytes"]) is int


def test_constructor_rejects_lifecycle_with_different_queue_or_event():
    tmp_path = make_test_dir("runtime-inconsistent-collaborators")
    device_config = RuntimeDeviceConfig(tmp_path)
    lifecycle_queue = RefreshQueue()
    lifecycle_event = threading.Event()
    lifecycle = LifecycleController(lifecycle_event, lifecycle_queue)

    with pytest.raises(ValueError, match="lifecycle"):
        RefreshTask(
            device_config,
            RecordingDisplayManager(),
            stop_event=threading.Event(),
            refresh_queue=RefreshQueue(),
            lifecycle=lifecycle,
        )


def test_direct_queue_submission_wakes_idle_worker(monkeypatch):
    tmp_path = make_test_dir("runtime-direct-queue-wake")
    task, _device_config, clock = _make_runtime_task(tmp_path, playlists=[], cycle_seconds=300)
    calls = []
    monkeypatch.setattr("src.refresh_task.get_plugin_instance", lambda config: CapturePlugin(calls))
    task.start()
    try:
        assert task.wait_until_waiting(timeout=1.0)
        command = RefreshCommand.create(
            kind=CommandKind.DISPLAY,
            source=CommandSource.MANUAL,
            plugin_id="direct_plugin",
            payload={"refresh_type": "Manual Update", "settings": {"id": "direct"}},
            now_monotonic=clock.monotonic(),
            deadline_monotonic=clock.monotonic() + 60,
            force=True,
            priority=100,
        )

        job = task.refresh_queue.submit(command)
        result = task.wait_for_job(job.id, timeout=1.0)

        assert result["status"] == "completed"
        assert calls == [{"id": "direct", "forceRefresh": True, "force_refresh": True, "_inkypiDisplayRender": True}]
    finally:
        task.stop(join_timeout=1.0)


def test_manual_worker_preserves_plugin_image_settings(monkeypatch):
    tmp_path = make_test_dir("runtime-manual-image-settings")
    task, _device_config, _clock = _make_runtime_task(tmp_path, playlists=[])
    plugin = CapturePlugin([])
    plugin.config = {"image_settings": ["rotate-180"]}
    monkeypatch.setattr("src.refresh_task.get_plugin_instance", lambda config: plugin)
    task.start()
    try:
        job = task.submit_manual_update(ManualRefresh("manual", {"id": "manual"}))
        result = task.wait_for_job(job["id"], timeout=1.0)

        assert result["status"] == "completed"
        assert task.display_manager.calls[0][1] == ["rotate-180"]
    finally:
        task.stop(join_timeout=1.0)


def test_manual_wait_reports_pruned_terminal_result_without_timing_out(monkeypatch):
    tmp_path = make_test_dir("runtime-manual-pruned-result")
    task, device_config, _clock = _make_runtime_task(tmp_path, playlists=[])
    task.refresh_queue.terminal_limit = 0
    device_config.config["manual_update_timeout_seconds"] = 0.1
    monkeypatch.setattr("src.refresh_task.get_plugin_instance", lambda config: CapturePlugin([]))
    task.start()
    try:
        with pytest.raises(RuntimeError, match="no longer available"):
            task.manual_update(ManualRefresh("manual", {"id": "manual"}))
    finally:
        task.stop(join_timeout=1.0)


def test_signal_config_change_wakes_and_reprobes_scheduled_selection(monkeypatch):
    tmp_path = make_test_dir("runtime-config-wake")
    empty_playlist = _runtime_playlist()
    task, device_config, _clock = _make_runtime_task(
        tmp_path,
        playlists=[empty_playlist],
        cycle_seconds=300,
    )
    device_config.refresh_info = RefreshInfo(refresh_time="2000-01-01T00:00:00+00:00", image_hash="old")
    calls = []
    monkeypatch.setattr("src.refresh_task.get_plugin_instance", lambda config: CapturePlugin(calls))
    task.start()
    try:
        assert task.wait_until_waiting(timeout=1.0)
        assert device_config.playlist_manager.add_plugin_to_playlist(
            "DailyDoseOfDay",
            _runtime_plugin_data("new_plugin", "New Plugin", latest_refresh_time=None),
        )

        task.signal_config_change()

        deadline = time.monotonic() + 1.0
        while not calls and time.monotonic() < deadline:
            time.sleep(0.01)
        assert calls
        task.signal_config_change()
        deadline = time.monotonic() + 1.0
        while not task.display_manager.calls and time.monotonic() < deadline:
            time.sleep(0.01)
        assert task.display_manager.calls
    finally:
        task.stop(join_timeout=1.0)


def _make_blocked_playlist_task(monkeypatch, name):
    tmp_path = make_test_dir(name)
    playlist = _runtime_playlist(_runtime_plugin_data(latest_refresh_time="2999-01-01T00:00:00+00:00"))
    task, device_config, _clock = _make_runtime_task(tmp_path, playlists=[playlist], cycle_seconds=300)
    device_config.config["theme_mode"] = "day"
    device_config.config["active_theme"] = "day"
    _write_runtime_cache(task, playlist.plugins[0])
    render_started = threading.Event()
    allow_render = threading.Event()
    plugin = BlockingRuntimePlugin(render_started, allow_render)
    monkeypatch.setattr("src.refresh_task.get_plugin_instance", lambda config: plugin)
    task.start()
    assert task.wait_until_waiting(timeout=1.0)
    # The initial scheduler probe legitimately synchronizes canonical theme
    # status. These tests isolate side effects from the subsequent stale job.
    device_config.write_count = 0
    return task, device_config.playlist_manager, render_started, allow_render, plugin, tmp_path


def _submit_blocked_playlist_render(task, manager):
    instance = manager.snapshot_instance(manager.first_instance_uuid())
    command = task._playlist_command(
        "DailyDoseOfDay",
        instance,
        source=CommandSource.MANUAL,
        intent=RefreshIntent.MANUAL_RENDER,
        force=True,
        display_cached_only=False,
        priority=100,
    )
    job = task.refresh_queue.submit(command)
    return task._job_payload(task.refresh_queue.get_entry(job.id))


def test_deleted_instance_result_is_discarded_after_render(monkeypatch):
    task, manager, render_started, allow_render, _plugin, tmp_path = _make_blocked_playlist_task(
        monkeypatch,
        "runtime-stale-delete",
    )
    try:
        instance_uuid = manager.first_instance_uuid()
        cache_path = Path(task.cache_path_for_snapshot(
            manager.snapshot_instance(instance_uuid)
        ))
        original_cache = cache_path.read_bytes()
        job = _submit_blocked_playlist_render(task, manager)
        assert render_started.wait(1.0)

        manager.delete_plugin_instance(instance_uuid)
        allow_render.set()
        result = task.wait_for_job(job["id"], timeout=1.0)

        assert result["status"] == "canceled"
        assert result["error_code"] == "stale_selection"
        assert not task.display_manager.calls
        assert task.device_config.write_count == 0
        assert cache_path.read_bytes() == original_cache
    finally:
        allow_render.set()
        task.stop(join_timeout=1.0)


def test_settings_revision_changed_during_render_discards_all_side_effects(monkeypatch):
    task, manager, render_started, allow_render, _plugin, tmp_path = _make_blocked_playlist_task(
        monkeypatch,
        "runtime-stale-settings",
    )
    try:
        instance_uuid = manager.first_instance_uuid()
        before = manager.snapshot_instance(instance_uuid)
        cache_path = Path(task.cache_path_for_snapshot(before))
        original_cache = cache_path.read_bytes()
        job = _submit_blocked_playlist_render(task, manager)
        assert render_started.wait(1.0)

        manager.update_plugin_instance(
            instance_uuid,
            settings={"id": "changed"},
            expected_generation=before.structural_generation,
            expected_settings_revision=before.settings_revision,
        )
        allow_render.set()
        result = task.wait_for_job(job["id"], timeout=1.0)

        assert result["status"] == "canceled"
        assert not task.display_manager.calls
        assert task.device_config.write_count == 0
        assert cache_path.read_bytes() == original_cache
        assert manager.snapshot_instance(instance_uuid).latest_refresh_time == before.latest_refresh_time
    finally:
        allow_render.set()
        task.stop(join_timeout=1.0)


def test_render_failure_after_instance_deletion_is_stale_without_theme_write(monkeypatch):
    task, manager, render_started, allow_render, plugin, _tmp_path = _make_blocked_playlist_task(
        monkeypatch,
        "runtime-stale-failure",
    )
    plugin.fail_first = True
    try:
        instance_uuid = manager.first_instance_uuid()
        instance = manager.snapshot_instance(instance_uuid)
        command = task._playlist_command(
            "DailyDoseOfDay",
            instance,
            source=CommandSource.SCHEDULER,
            intent=RefreshIntent.THEME_REDRAW,
            force=True,
            display_cached_only=False,
            theme_context={"mode": "night", "source": "weather", "reason": "sunset"},
        )
        submitted = task.refresh_queue.submit(command)
        assert render_started.wait(1.0)

        assert manager.delete_plugin_instance(instance_uuid)
        allow_render.set()
        result = task.wait_for_job(submitted.id, timeout=1.0)

        assert result["status"] == "canceled"
        assert result["error_code"] == "stale_selection"
        assert "active_theme_refresh_failure" not in task.device_config.config
        assert task.device_config.write_count == 0
    finally:
        allow_render.set()
        task.stop(join_timeout=1.0)


def test_theme_render_exception_in_run_records_cooldown_then_success_clears(monkeypatch):
    tmp_path = make_test_dir("runtime-theme-run-cooldown")
    plugin_data = _runtime_plugin_data(
        "themed_plugin",
        "Themed Plugin",
        latest_refresh_time="2999-01-01T00:00:00+00:00",
    )
    plugin_data["plugin_settings"]["themeMode"] = "auto"
    playlist = _runtime_playlist(plugin_data)
    clock = RuntimeClock()
    task, device_config, _clock = _make_runtime_task(tmp_path, playlists=[playlist], clock=clock)
    device_config.config["active_theme"] = "day"
    device_config.config["display_triggered_refresh_enabled"] = True
    device_config.get_plugin = lambda plugin_id: {
        "id": plugin_id,
        "_manifest": _theme_manifest(plugin_id),
    }
    displayed = playlist.plugins[0]
    device_config.refresh_info = RefreshInfo(
        refresh_type="Playlist",
        playlist=playlist.name,
        plugin_id=displayed.plugin_id,
        plugin_instance=displayed.name,
        refresh_time="2026-05-26T22:07:00+00:00",
        image_hash="day-image",
    )
    task.runtime_state.set_display_state(
        "committed",
        instance_uuid=displayed.instance_uuid,
        changed_at="2026-05-26T22:07:00+00:00",
    )
    current_dt = [datetime(2026, 5, 26, 22, 8, tzinfo=timezone.utc)]
    _write_runtime_theme_cache(task, displayed, "day")
    _seed_theme_last_good(
        task,
        displayed.snapshot(),
        "day",
        current_dt[0] - timedelta(minutes=10),
    )
    monkeypatch.setattr(task, "_get_current_datetime", lambda: current_dt[0])
    monkeypatch.setattr(
        "src.refresh_task.get_theme_context",
        lambda config, now: {"mode": "night", "source": "weather", "reason": "sunset"},
    )
    render_started = threading.Event()
    allow_render = threading.Event()
    allow_render.set()
    plugin = BlockingRuntimePlugin(render_started, allow_render, fail_first=True)
    monkeypatch.setattr("src.refresh_task.get_plugin_instance", lambda config: plugin)

    task._run_one_iteration_for_test()
    failure = device_config.config["active_theme_refresh_failure"]
    assert datetime.fromisoformat(failure["retry_after"]) - current_dt[0] == timedelta(seconds=600)

    clock.advance(31)
    current_dt[0] += timedelta(seconds=31)
    task._run_one_iteration_for_test()
    assert len(plugin.calls) == 1

    clock.advance(570)
    current_dt[0] += timedelta(seconds=570)
    task._run_one_iteration_for_test()

    assert len(plugin.calls) == 2
    followup = task.refresh_queue.take(timeout=0)
    assert followup.command.intent is RefreshIntent.DISPLAY_CACHE
    task._process_queue_entry(followup)
    assert device_config.config["active_theme"] == "night"
    assert device_config.config["active_theme_refresh_failure"] is None


def test_shared_plugin_singleton_never_executes_concurrently(monkeypatch):
    tmp_path = make_test_dir("runtime-singleton")
    device_config = RuntimeDeviceConfig(tmp_path)
    device_config.config["plugin_cycle_interval_seconds"] = 300
    task = RefreshTask(device_config, RecordingDisplayManager())
    entered = threading.Event()
    release = threading.Event()
    guard = threading.Lock()
    active = 0
    maximum = 0

    class SingletonPlugin:
        config = {}

        def render_themed_image(self, settings, config, **_kwargs):
            return self.generate_image(settings, config)

        def generate_image(self, settings, config):
            nonlocal active, maximum
            with guard:
                active += 1
                maximum = max(maximum, active)
            entered.set()
            if settings["id"] == "first":
                assert release.wait(1.0)
            with guard:
                active -= 1
            return Image.new("RGB", (1, 1), "white")

    plugin = SingletonPlugin()
    monkeypatch.setattr("src.refresh_task.get_plugin_instance", lambda config: plugin)
    task.start()
    try:
        first = task.submit_manual_update(ManualRefresh("singleton", {"id": "first"}))
        second = task.submit_manual_update(ManualRefresh("singleton", {"id": "second"}))
        assert entered.wait(1.0)
        assert maximum == 1
        release.set()

        assert _wait_for_legacy_job(task, first["id"])["status"] == "completed"
        assert _wait_for_legacy_job(task, second["id"])["status"] == "completed"
        assert maximum == 1
    finally:
        release.set()
        task.stop()


def test_bounded_stop_marks_forced_exit_when_render_does_not_cooperate(monkeypatch):
    tmp_path = make_test_dir("runtime-bounded-stop")
    task, _device_config, _clock = _make_runtime_task(tmp_path, playlists=[])
    render_started = threading.Event()
    allow_render = threading.Event()
    monkeypatch.setattr(
        "src.refresh_task.get_plugin_instance",
        lambda config: BlockingRuntimePlugin(render_started, allow_render),
    )
    task.start()
    try:
        task.submit_manual_update(ManualRefresh("blocked", {"id": "blocked"}))
        assert render_started.wait(1.0)

        assert task.stop(join_timeout=0.01) is False
        assert task.lifecycle.state is LifecycleState.FORCED_EXIT
    finally:
        allow_render.set()
        task.thread.join(timeout=1.0)


def test_cache_only_display_validates_each_visible_side_effect(monkeypatch):
    tmp_path = make_test_dir("runtime-validation-before-side-effects")
    playlist = _runtime_playlist(_runtime_plugin_data(latest_refresh_time="2999-01-01T00:00:00+00:00"))
    task, device_config, _clock = _make_runtime_task(tmp_path, playlists=[playlist])
    device_config.config.update({"theme_mode": "day", "active_theme": "day"})
    cache_path = _write_runtime_cache(task, playlist.plugins[0])
    events = []
    manager = device_config.playlist_manager
    original_validate = manager.validate_selection
    original_record = task.runtime_state.record_success
    original_replace = __import__("os").replace
    inner_arbiter = task.render_arbiter
    lease_depth = 0

    class ObservingArbiter:
        @contextmanager
        def lease(self, plugin_id, context):
            nonlocal lease_depth
            with inner_arbiter.lease(plugin_id, context):
                lease_depth += 1
                try:
                    yield
                finally:
                    lease_depth -= 1

    task.render_arbiter = ObservingArbiter()

    def validate(*args, **kwargs):
        events.append("validate")
        return original_validate(*args, **kwargs)

    def record(*args, **kwargs):
        events.append("timestamp")
        return original_record(*args, **kwargs)

    def replace(source, destination):
        if Path(destination) == cache_path:
            assert lease_depth == 1
            events.append("cache")
        return original_replace(source, destination)

    manager.validate_selection = validate
    task.runtime_state.record_success = record
    task.display_manager.display_image = lambda image, image_settings=None: events.append("display")
    device_config.write_config = lambda: events.append("config")
    monkeypatch.setattr("src.refresh_task.os.replace", replace)
    monkeypatch.setattr("src.refresh_task.get_plugin_instance", lambda config: CapturePlugin([]))

    task.start()
    try:
        assert task.wait_until_waiting(timeout=1.0)
        events.clear()
        job = task.submit_playlist_display(manager.first_instance_uuid())
        result = task.wait_for_job(job["id"], timeout=1.0)

        assert result["status"] == "completed"
        for side_effect in ("display", "config"):
            index = events.index(side_effect)
            assert events[index - 1] == "validate"
        assert "cache" not in events
        assert "timestamp" not in events
    finally:
        task.stop(join_timeout=1.0)


def test_final_playlist_validation_failure_does_not_mutate_shared_config(monkeypatch):
    tmp_path = make_test_dir("runtime-final-config-validation")
    playlist = _runtime_playlist(
        _runtime_plugin_data(latest_refresh_time="2999-01-01T00:00:00+00:00")
    )
    task, device_config, _clock = _make_runtime_task(tmp_path, playlists=[playlist])
    device_config.config.update({"theme_mode": "day", "active_theme": "day"})
    _write_runtime_cache(task, playlist.plugins[0])
    monkeypatch.setattr("src.refresh_task.get_plugin_instance", lambda config: CapturePlugin([]))
    original_require = task._require_fresh_selection
    checks = []

    def fail_final_validation(command, context):
        checks.append(command.id)
        if len(checks) == 4:
            raise TaskCancelled("selection changed at final config check")
        return original_require(command, context)

    monkeypatch.setattr(task, "_require_fresh_selection", fail_final_validation)
    before_refresh = device_config.refresh_info.to_dict()
    instance = device_config.playlist_manager.snapshot_instance(
        device_config.playlist_manager.first_instance_uuid()
    )
    command = task._playlist_command(
        playlist.name,
        instance,
        source=CommandSource.SCHEDULER,
        intent=RefreshIntent.THEME_REDRAW,
        force=False,
        display_cached_only=True,
        theme_context={"mode": "night", "source": "weather", "reason": "sunset"},
    )
    task.start()
    try:
        assert task.wait_until_waiting(timeout=1.0)
        before_config = copy.deepcopy(device_config.config)
        device_config.write_count = 0
        submitted = task.refresh_queue.submit(command)
        result = task.wait_for_job(submitted.id, timeout=1.0)

        assert result["status"] == "canceled"
        assert len(checks) == 4
        assert device_config.refresh_info.to_dict() == before_refresh
        assert "displayed_instance_uuid" not in device_config.config
        assert device_config.config["active_theme"] == "day"
        assert device_config.config == before_config
        assert device_config.write_count == 0
    finally:
        task.stop(join_timeout=1.0)


def test_final_manual_context_failure_does_not_mutate_shared_refresh_info(monkeypatch):
    tmp_path = make_test_dir("runtime-final-manual-context")
    task, device_config, clock = _make_runtime_task(tmp_path, playlists=[])
    command = RefreshCommand.create(
        kind=CommandKind.DISPLAY,
        source=CommandSource.MANUAL,
        plugin_id="manual",
        payload={"refresh_type": "Manual Update", "settings": {}},
        now_monotonic=clock.monotonic(),
        deadline_monotonic=clock.monotonic() + 60,
    )

    class CancelOnFourthCheck:
        def __init__(self):
            self.checks = 0

        def raise_if_cancelled(self):
            self.checks += 1
            if self.checks == 4:
                raise TaskCancelled("cancel at final config check")

    context = CancelOnFourthCheck()
    monkeypatch.setattr(task, "_current_task_context", lambda _command: context)
    before_refresh = device_config.refresh_info.to_dict()

    with pytest.raises(TaskCancelled, match="final config check"):
        task._commit_command_result(
            command,
            None,
            Image.new("RGB", (1, 1), "white"),
            datetime(2026, 5, 26, 7, 0, tzinfo=timezone.utc),
        )

    assert context.checks == 4
    assert device_config.refresh_info.to_dict() == before_refresh
    assert device_config.write_count == 0


def test_running_playlist_cancel_finishes_canceled_not_succeeded(monkeypatch):
    task, manager, render_started, allow_render, _plugin, _tmp_path = _make_blocked_playlist_task(
        monkeypatch,
        "runtime-running-cancel",
    )
    try:
        instance_uuid = manager.first_instance_uuid()
        job = _submit_blocked_playlist_render(task, manager)
        assert render_started.wait(1.0)

        assert task.refresh_queue.cancel_instance(instance_uuid) == 1
        allow_render.set()
        result = task.wait_for_job(job["id"], timeout=1.0)

        assert result["status"] == "canceled"
        assert task.refresh_queue.get_entry(job["id"]).job.status is JobStatus.CANCELED
        assert not task.display_manager.calls
    finally:
        allow_render.set()
        task.stop(join_timeout=1.0)


def test_cancel_requested_after_execute_cannot_kill_worker_or_finish_succeeded(monkeypatch):
    tmp_path = make_test_dir("runtime-cancel-before-finish")
    playlist = _runtime_playlist(_runtime_plugin_data())
    task, device_config, _clock = _make_runtime_task(tmp_path, playlists=[playlist])
    instance = device_config.playlist_manager.snapshot_instance(
        device_config.playlist_manager.first_instance_uuid()
    )
    command = task._playlist_command(
        playlist.name,
        instance,
        source=CommandSource.MANUAL,
        intent=RefreshIntent.MANUAL_RENDER,
    )
    submitted = task.refresh_queue.submit(command)
    entry = task.refresh_queue.take(timeout=0)

    def execute_then_cancel(_command):
        assert task.refresh_queue.cancel_instance(instance.instance_uuid) == 1

    monkeypatch.setattr(task, "_execute_command", execute_then_cancel)

    task._process_queue_entry(entry)

    result = task.get_manual_update_job(submitted.id)
    assert result["status"] == "canceled"
    assert task.refresh_queue.get_entry(submitted.id).job.status is JobStatus.CANCELED


def test_running_command_deadline_finishes_abandoned(monkeypatch):
    tmp_path = make_test_dir("runtime-running-deadline")
    clock = RuntimeClock()
    task, _device_config, _clock = _make_runtime_task(tmp_path, playlists=[], clock=clock)
    render_started = threading.Event()
    allow_render = threading.Event()
    monkeypatch.setattr(
        "src.refresh_task.get_plugin_instance",
        lambda config: BlockingRuntimePlugin(render_started, allow_render),
    )
    task.start()
    try:
        command = RefreshCommand.create(
            kind=CommandKind.DISPLAY,
            source=CommandSource.MANUAL,
            plugin_id="deadline_plugin",
            payload={"refresh_type": "Manual Update", "settings": {"id": "deadline"}},
            now_monotonic=clock.monotonic(),
            deadline_monotonic=clock.monotonic() + 5,
            force=True,
            priority=100,
        )
        job = task.refresh_queue.submit(command)
        assert render_started.wait(1.0)

        clock.advance(5)
        allow_render.set()
        result = task.wait_for_job(job.id, timeout=1.0)

        assert result["status"] == "timed_out"
        assert task.refresh_queue.get_entry(job.id).job.status is JobStatus.ABANDONED
        assert not task.display_manager.calls
    finally:
        allow_render.set()
        task.stop(join_timeout=1.0)


def test_deadline_crossed_after_execute_is_abandoned_before_success(monkeypatch):
    tmp_path = make_test_dir("runtime-deadline-before-finish")
    clock = RuntimeClock()
    task, _device_config, _clock = _make_runtime_task(tmp_path, playlists=[], clock=clock)
    command = RefreshCommand.create(
        kind=CommandKind.DISPLAY,
        source=CommandSource.MANUAL,
        plugin_id="deadline_plugin",
        payload={"refresh_type": "Manual Update", "settings": {}},
        now_monotonic=clock.monotonic(),
        deadline_monotonic=clock.monotonic() + 5,
    )
    submitted = task.refresh_queue.submit(command)
    entry = task.refresh_queue.take(timeout=0)
    monkeypatch.setattr(task, "_execute_command", lambda _command: clock.advance(5))

    task._process_queue_entry(entry)

    result = task.get_manual_update_job(submitted.id)
    assert result["status"] == "timed_out"
    assert result["error_code"] == "deadline_expired"
    assert task.refresh_queue.get_entry(submitted.id).job.status is JobStatus.ABANDONED


def test_active_operation_snapshot_publishes_command_deadline_then_clears(monkeypatch):
    tmp_path = make_test_dir("runtime-active-operation-snapshot")
    clock = RuntimeClock()
    task, _device_config, _clock = _make_runtime_task(
        tmp_path,
        playlists=[],
        clock=clock,
    )
    command = RefreshCommand.create(
        kind=CommandKind.DISPLAY,
        source=CommandSource.MANUAL,
        plugin_id="active_plugin",
        payload={"refresh_type": "Manual Update", "settings": {}},
        now_monotonic=clock.monotonic(),
        deadline_monotonic=clock.monotonic() + 90,
    )
    submitted = task.refresh_queue.submit(command)
    entry = task.refresh_queue.take(timeout=0)
    observed = []

    def capture_active(_command):
        observed.append(task.active_operation_snapshot())

    monkeypatch.setattr(task, "_execute_command", capture_active)

    task._process_queue_entry(entry)

    assert len(observed) == 1
    assert observed[0].command_id == submitted.id
    assert observed[0].plugin_id == "active_plugin"
    assert observed[0].deadline_monotonic == command.deadline_monotonic
    assert task.active_operation_snapshot() is None


def test_process_queue_entry_logs_privacy_safe_command_origin(monkeypatch, caplog):
    tmp_path = make_test_dir("runtime-command-start-audit")
    playlist = _runtime_playlist(_runtime_plugin_data("audit_plugin", "Audit Plugin"))
    task, device_config, _clock = _make_runtime_task(tmp_path, playlists=[playlist])
    instance = device_config.playlist_manager.snapshot_instance(
        device_config.playlist_manager.first_instance_uuid()
    )
    command = task._playlist_command(
        playlist.name,
        instance,
        source=CommandSource.BACKGROUND,
        intent=RefreshIntent.DATA_REFRESH,
        display_cached_only=False,
        kind=CommandKind.CACHE_REFRESH,
    )
    task.refresh_queue.submit(command)
    entry = task.refresh_queue.take(timeout=0)
    monkeypatch.setattr(task, "_execute_command", lambda _command: None)

    with caplog.at_level("INFO", logger=refresh_task_module.__name__):
        task._process_queue_entry(entry)

    expected_hash = hashlib.sha256(instance.instance_uuid.encode("utf-8")).hexdigest()[:16]
    start_messages = [
        record.getMessage()
        for record in caplog.records
        if record.getMessage().startswith("Refresh command started.")
    ]
    assert start_messages == [
        "Refresh command started. | source: background | intent: data_refresh | "
        f"plugin_id: audit_plugin | instance_uuid_hash: {expected_hash}"
    ]


def test_resource_pressure_deferral_preserves_last_good_and_cached_display(
    monkeypatch,
    caplog,
):
    current_dt = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)
    clock = RuntimeClock(wall=current_dt.timestamp())
    playlist = _runtime_playlist(
        _runtime_plugin_data("weather", "Weather", latest_refresh_time=current_dt.isoformat())
    )
    task, _device_config, _clock = _make_runtime_task(
        make_test_dir("resource-pressure-deferral-last-good"),
        playlists=[playlist],
        clock=clock,
    )
    instance = playlist.plugins[0].snapshot()
    cached = _write_runtime_cache(
        task,
        instance,
        Image.new("RGB", (32, 16), (17, 34, 51)),
    )
    cached_bytes = cached.read_bytes()
    cached_mtime_ns = cached.stat().st_mtime_ns
    promoted_at = (current_dt - timedelta(minutes=5)).isoformat()
    last_good = LastGoodCacheState(
        theme_mode=None,
        structural_generation=instance.structural_generation,
        settings_revision=instance.settings_revision,
        promoted_at=promoted_at,
    )
    task.runtime_state.record_success(
        instance.instance_uuid,
        promoted_at,
        lane=RefreshLane.DATA,
        last_good_cache=last_good,
    )
    before_retry = task.retry_registry.snapshot()
    before_scheduler_failure = task.scheduler_state.snapshot().last_failure_wall
    plugin_calls = []
    monkeypatch.setattr(
        refresh_task_module,
        "get_plugin_instance",
        lambda _config: FakePlugin(plugin_calls),
    )
    monkeypatch.setattr(task, "_renderer_blocked_by_disk_pressure", lambda _command: False)
    monkeypatch.setattr(task, "_run_memory_maintenance", lambda *_a, **_k: None)
    original_execute = task._execute_command

    def defer_data(command):
        if command.intent is RefreshIntent.DATA_REFRESH:
            raise ResourcePressureDeferred(
                reason="browser_resource_pressure",
                phase="start",
                available_mb=100.0,
                swap_percent=80.0,
            )
        return original_execute(command)

    monkeypatch.setattr(task, "_execute_command", defer_data)
    data_command = task._playlist_command(
        playlist.name,
        instance,
        source=CommandSource.BACKGROUND,
        intent=RefreshIntent.DATA_REFRESH,
        display_cached_only=False,
        kind=CommandKind.CACHE_REFRESH,
        current_dt=current_dt,
    )

    with caplog.at_level("WARNING", logger=refresh_task_module.__name__):
        deferred = _queue_and_process(task, data_command)

    assert deferred.job.status is JobStatus.CANCELED
    assert deferred.job.error_code == "resource_pressure_deferred"
    assert task.retry_registry.snapshot() == before_retry
    assert task.scheduler_state.snapshot().last_failure_wall == before_scheduler_failure
    state = task.runtime_state.snapshot().instances[instance.instance_uuid]
    assert state.data.last_failure_at is None
    assert state.data.next_retry_at == (current_dt + timedelta(minutes=5)).isoformat()
    assert state.last_good_cache == last_good
    assert cached.read_bytes() == cached_bytes
    assert cached.stat().st_mtime_ns == cached_mtime_ns
    assert "reason: browser_resource_pressure" in caplog.text
    assert "phase: start" in caplog.text
    assert "next_retry_at:" in caplog.text

    display_command = task._playlist_command(
        playlist.name,
        instance,
        source=CommandSource.SCHEDULER,
        intent=RefreshIntent.DISPLAY_CACHE,
        display_cached_only=True,
        kind=CommandKind.DISPLAY,
        current_dt=current_dt,
    )
    displayed = _queue_and_process(task, display_command)

    assert displayed.job.status is JobStatus.SUCCEEDED
    assert plugin_calls == []
    assert len(task.display_manager.calls) == 1
    assert task.display_manager.calls[0][0].getpixel((0, 0)) == (17, 34, 51)


def test_plugin_requested_deferral_records_exact_lane_retry_without_failure(
    monkeypatch,
    caplog,
):
    current_dt = datetime(2026, 8, 14, 12, 0, tzinfo=timezone.utc)
    clock = RuntimeClock(wall=current_dt.timestamp())
    playlist = _runtime_playlist(
        _runtime_plugin_data("pixiv_r18_ranking", "Pixiv", latest_refresh_time=None)
    )
    task, _device_config, _clock = _make_runtime_task(
        make_test_dir("plugin-requested-deferral"),
        playlists=[playlist],
        clock=clock,
    )
    instance = playlist.plugins[0].snapshot()
    before_retry = task.retry_registry.snapshot()
    before_scheduler_failure = task.scheduler_state.snapshot().last_failure_wall
    monkeypatch.setattr(task, "_renderer_blocked_by_disk_pressure", lambda _c: False)
    monkeypatch.setattr(task, "_run_memory_maintenance", lambda *_a, **_k: None)

    def defer(_command):
        raise PluginRefreshDeferred(
            reason="candidate_pool_temporarily_exhausted",
            phase="bank_hydration",
            minimum_seconds=30 * 60,
        )

    monkeypatch.setattr(task, "_execute_command", defer)
    command = task._playlist_command(
        playlist.name,
        instance,
        source=CommandSource.BACKGROUND,
        intent=RefreshIntent.DATA_REFRESH,
        display_cached_only=False,
        kind=CommandKind.CACHE_REFRESH,
        current_dt=current_dt,
    )

    with caplog.at_level("WARNING", logger=refresh_task_module.__name__):
        completed = _queue_and_process(task, command)

    assert completed.job.status is JobStatus.CANCELED
    assert completed.job.error_code == "plugin_refresh_deferred"
    state = task.runtime_state.snapshot().instances[instance.instance_uuid].data
    assert state.next_retry_at == (current_dt + timedelta(minutes=30)).isoformat()
    assert state.last_failure_at is None
    assert task.retry_registry.snapshot() == before_retry
    assert task.scheduler_state.snapshot().last_failure_wall == before_scheduler_failure
    assert "plugin-requested refresh" in caplog.text
    assert "resource pressure" not in caplog.text.lower()


def test_plugin_requested_deferral_completes_manual_waiter_once(monkeypatch):
    task, _device_config, _clock = _make_runtime_task(
        make_test_dir("plugin-deferral-manual-waiter"),
        playlists=[],
    )

    class DeferringPlugin(DelegatingThemeWrapper):
        def generate_image(self, _settings, _device_config):
            raise PluginRefreshDeferred(
                reason="candidate_pool_temporarily_exhausted",
                phase="bank_hydration",
                minimum_seconds=30 * 60,
            )

    monkeypatch.setattr(
        refresh_task_module,
        "get_plugin_instance",
        lambda _config: DeferringPlugin(),
    )
    monkeypatch.setattr(task, "_run_memory_maintenance", lambda *_a, **_k: None)

    task.start()
    try:
        submitted = task.submit_manual_update(
            ManualRefresh("pixiv_r18_ranking", {"id": "manual-pixiv"})
        )
        completed = task.wait_for_job(submitted["id"], timeout=1.0)
    finally:
        task.stop(join_timeout=1.0)

    assert completed["status"] == "canceled"
    assert completed["error_code"] == "plugin_refresh_deferred"
    assert task.manual_update_requests == ()


def test_plugin_requested_deferral_preserves_ian_terminal_queue_semantics(
    monkeypatch,
):
    task, _device_config, clock = _make_runtime_task(
        make_test_dir("plugin-deferral-ian-terminal"),
        ian_resource_sampler=lambda: IanResourceSample(
            available_mb=512,
            swap_percent=0,
        ),
    )
    monkeypatch.setattr(task, "_renderer_blocked_by_disk_pressure", lambda _c: False)
    monkeypatch.setattr(task, "_run_memory_maintenance", lambda *_a, **_k: None)

    def defer(_command):
        raise PluginRefreshDeferred(
            reason="candidate_pool_temporarily_exhausted",
            phase="bank_hydration",
            minimum_seconds=30 * 60,
        )

    monkeypatch.setattr(task, "_execute_command", defer)
    command = RefreshCommand.create(
        kind=CommandKind.CACHE_REFRESH,
        source=CommandSource.BACKGROUND,
        plugin_id="sports_dashboard",
        instance_uuid="sports-instance",
        structural_generation=1,
        settings_revision=1,
        payload={"playlist_name": "DailyDoseOfDay"},
        now_monotonic=clock.monotonic(),
        deadline_monotonic=clock.monotonic() + 60,
        priority=10,
        intent=RefreshIntent.DATA_REFRESH,
    )

    completed = _queue_and_process(task, command)

    assert completed.job.status is JobStatus.CANCELED
    assert completed.job.error_code == "plugin_refresh_deferred"
    assert task.refresh_health_snapshot()["ian_retained"] == 0
    assert task.refresh_health_snapshot()["ian_last_queue_status"] == "canceled"


@pytest.mark.parametrize(
    ("plugin_id", "expected_delay"),
    [
        ("weather", timedelta(minutes=5)),
        ("ordinary", timedelta(seconds=60)),
    ],
)
def test_background_typed_resource_pressure_uses_weather_retry_floor(
    monkeypatch,
    plugin_id,
    expected_delay,
):
    current_dt = datetime(2026, 8, 11, 20, 23, 53, tzinfo=timezone.utc)
    clock = RuntimeClock(wall=current_dt.timestamp())
    playlist = _runtime_playlist(_runtime_plugin_data(plugin_id, "Pressure Test"))
    task, _device_config, _clock = _make_runtime_task(
        make_test_dir(f"typed-pressure-retry-{plugin_id}"),
        playlists=[playlist],
        clock=clock,
    )
    instance = playlist.plugins[0].snapshot()
    monkeypatch.setattr(
        task,
        "_resource_sample",
        lambda: ResourceSample(available_mb=512, swap_percent=0),
    )
    monkeypatch.setattr(task, "_renderer_blocked_by_disk_pressure", lambda _command: False)

    def defer(_command):
        raise ResourcePressureDeferred(
            reason="browser_resource_pressure",
            phase="render",
            available_mb=104.5,
            swap_percent=99.1,
        )

    monkeypatch.setattr(task, "_execute_command", defer)
    command = task._playlist_command(
        playlist.name,
        instance,
        source=CommandSource.BACKGROUND,
        intent=RefreshIntent.DATA_REFRESH,
        display_cached_only=False,
        kind=CommandKind.CACHE_REFRESH,
        current_dt=current_dt,
    )

    completed = _queue_and_process(task, command)

    assert completed.job.status is JobStatus.CANCELED
    state = task.runtime_state.snapshot().instances[instance.instance_uuid].data
    assert state.next_retry_at == (current_dt + expected_delay).isoformat()


def test_cached_load_fallback_reraises_typed_resource_pressure(monkeypatch):
    current_dt = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)
    playlist = _runtime_playlist(
        _runtime_plugin_data("weather", "Weather", latest_refresh_time=current_dt.isoformat())
    )
    task, _device_config, _clock = _make_runtime_task(
        make_test_dir("cached-load-resource-pressure-reraise"),
        playlists=[playlist],
    )
    instance = playlist.plugins[0].snapshot()
    cached = _write_runtime_cache(
        task,
        instance,
        Image.new("RGB", (32, 16), (17, 34, 51)),
    )
    cached_bytes = cached.read_bytes()
    cached_mtime_ns = cached.stat().st_mtime_ns
    deferred = ResourcePressureDeferred(
        reason="browser_resource_pressure",
        phase="start",
        available_mb=100.0,
        swap_percent=80.0,
    )

    class PressurePlugin:
        def wants_refresh_on_display(self, _settings):
            return False

        def render_themed_image(self, *_args, **_kwargs):
            raise deferred

    monkeypatch.setattr(
        refresh_task_module,
        "get_plugin_instance",
        lambda _config: PressurePlugin(),
    )
    monkeypatch.setattr(
        refresh_task_module,
        "_load_image_copy",
        lambda _path: (_ for _ in ()).throw(OSError("cache read failed")),
    )
    monkeypatch.setattr(
        refresh_task_module,
        "_display_refresh_under_resource_pressure",
        lambda *_args, **_kwargs: False,
    )
    command = task._playlist_command(
        playlist.name,
        instance,
        source=CommandSource.SCHEDULER,
        intent=RefreshIntent.THEME_REDRAW,
        display_cached_only=True,
        kind=CommandKind.DISPLAY,
        current_dt=current_dt,
    )

    with pytest.raises(ResourcePressureDeferred) as captured:
        task._execute_command(command)

    assert captured.value is deferred
    assert task.display_manager.calls == []
    assert cached.read_bytes() == cached_bytes
    assert cached.stat().st_mtime_ns == cached_mtime_ns


def test_process_queue_entry_start_log_excludes_private_command_fields(monkeypatch, caplog):
    tmp_path = make_test_dir("runtime-command-start-audit-privacy")
    plugin_data = _runtime_plugin_data("audit_plugin", "private-instance-name")
    plugin_data["plugin_settings"] = {
        "id": "audit_plugin",
        "apiKey": "super-secret-value",
        "url": "https://private.example/secret",
    }
    playlist = _runtime_playlist(plugin_data)
    task, device_config, _clock = _make_runtime_task(tmp_path, playlists=[playlist])
    instance = device_config.playlist_manager.snapshot_instance(
        device_config.playlist_manager.first_instance_uuid()
    )
    command = task._playlist_command(
        playlist.name,
        instance,
        source=CommandSource.BACKGROUND,
        intent=RefreshIntent.DATA_REFRESH,
        display_cached_only=False,
        kind=CommandKind.CACHE_REFRESH,
    )
    task.refresh_queue.submit(command)
    entry = task.refresh_queue.take(timeout=0)
    monkeypatch.setattr(task, "_execute_command", lambda _command: None)

    with caplog.at_level("INFO", logger=refresh_task_module.__name__):
        task._process_queue_entry(entry)

    start_messages = [
        record.getMessage()
        for record in caplog.records
        if record.getMessage().startswith("Refresh command started.")
    ]
    assert len(start_messages) == 1
    start_message = start_messages[0]
    assert instance.instance_uuid not in start_message
    assert "private-instance-name" not in start_message
    assert "super-secret-value" not in start_message
    assert "https://private.example/secret" not in start_message


def test_process_queue_entry_start_log_uses_none_without_instance_uuid(monkeypatch, caplog):
    tmp_path = make_test_dir("runtime-command-start-audit-no-instance")
    task, _device_config, clock = _make_runtime_task(tmp_path, playlists=[])
    command = RefreshCommand.create(
        kind=CommandKind.DISPLAY,
        source=CommandSource.SCHEDULER,
        plugin_id="audit_global",
        payload={},
        now_monotonic=clock.monotonic(),
        deadline_monotonic=clock.monotonic() + 60,
        intent=RefreshIntent.DISPLAY_CACHE,
    )
    task.refresh_queue.submit(command)
    entry = task.refresh_queue.take(timeout=0)
    monkeypatch.setattr(task, "_execute_command", lambda _command: None)

    with caplog.at_level("INFO", logger=refresh_task_module.__name__):
        task._process_queue_entry(entry)

    start_messages = [
        record.getMessage()
        for record in caplog.records
        if record.getMessage().startswith("Refresh command started.")
    ]
    assert start_messages == [
        "Refresh command started. | source: scheduler | intent: display_cache | "
        "plugin_id: audit_global | instance_uuid_hash: none"
    ]


def test_process_queue_entry_binds_context_and_immutable_instance_identity(monkeypatch):
    tmp_path = make_test_dir("runtime-long-task-binding")
    playlist = _runtime_playlist(_runtime_plugin_data())
    task, device_config, _clock = _make_runtime_task(tmp_path, playlists=[playlist])
    instance = device_config.playlist_manager.snapshot_instance(
        device_config.playlist_manager.first_instance_uuid()
    )
    command = task._playlist_command(
        playlist.name,
        instance,
        source=CommandSource.MANUAL,
        intent=RefreshIntent.MANUAL_RENDER,
    )
    submitted = task.refresh_queue.submit(command)
    entry = task.refresh_queue.take(timeout=0)
    observed = []

    def capture_runtime(_command):
        observed.append(
            (
                current_task_context(),
                current_instance_identity(),
                current_instance_identity_validator(),
            )
        )

    monkeypatch.setattr(task, "_execute_command", capture_runtime)

    task._process_queue_entry(entry)

    assert task.refresh_queue.get_entry(submitted.id).job.status is JobStatus.SUCCEEDED
    context, identity, identity_validator = observed[0]
    assert context.cancel_event is entry.cancel_event
    assert context.deadline_monotonic == command.deadline_monotonic
    assert identity.instance_uuid == instance.instance_uuid
    assert identity.structural_generation == instance.structural_generation
    assert identity.settings_revision == instance.settings_revision
    assert identity_validator(identity)
    assert not identity_validator(
        InstanceIdentity(
            identity.instance_uuid,
            identity.structural_generation,
            identity.settings_revision + 1,
        )
    )
    assert current_task_context() is None
    assert current_instance_identity() is None
    assert current_instance_identity_validator() is None


def test_failure_bookkeeping_error_cannot_leave_queue_job_running(monkeypatch):
    tmp_path = make_test_dir("runtime-failure-bookkeeping")
    task, _device_config, clock = _make_runtime_task(tmp_path, playlists=[], clock=RuntimeClock())
    command = RefreshCommand.create(
        kind=CommandKind.DISPLAY,
        source=CommandSource.MANUAL,
        plugin_id="failing",
        payload={"refresh_type": "Manual Update", "settings": {}},
        now_monotonic=clock.monotonic(),
        deadline_monotonic=clock.monotonic() + 60,
    )
    submitted = task.refresh_queue.submit(command)
    entry = task.refresh_queue.take(timeout=0)
    monkeypatch.setattr(task, "_execute_command", lambda _command: (_ for _ in ()).throw(RuntimeError("render")))
    monkeypatch.setattr(task, "_record_command_failure", lambda *_args: (_ for _ in ()).throw(RuntimeError("bookkeeping")))

    task._process_queue_entry(entry)

    finished = task.refresh_queue.get_entry(submitted.id).job
    assert finished.status is JobStatus.FAILED
    assert finished.error == "render"


def test_cancel_arriving_during_failure_bookkeeping_finishes_canceled(monkeypatch):
    tmp_path = make_test_dir("runtime-failure-bookkeeping-cancel")
    playlist = _runtime_playlist(_runtime_plugin_data())
    task, device_config, _clock = _make_runtime_task(tmp_path, playlists=[playlist])
    instance = device_config.playlist_manager.snapshot_instance(
        device_config.playlist_manager.first_instance_uuid()
    )
    command = task._playlist_command(
        playlist.name,
        instance,
        source=CommandSource.SCHEDULER,
        intent=RefreshIntent.DATA_REFRESH,
    )
    submitted = task.refresh_queue.submit(command)
    entry = task.refresh_queue.take(timeout=0)
    monkeypatch.setattr(
        task,
        "_execute_command",
        lambda _command: (_ for _ in ()).throw(RuntimeError("render")),
    )

    def cancel_during_bookkeeping(*_args):
        assert task.refresh_queue.cancel_instance(instance.instance_uuid) == 1

    monkeypatch.setattr(task, "_record_command_failure", cancel_during_bookkeeping)

    task._process_queue_entry(entry)

    finished = task.refresh_queue.get_entry(submitted.id).job
    assert finished.status is JobStatus.CANCELED
    assert finished.error_code == "task_canceled"


def test_deadline_arriving_during_failure_bookkeeping_finishes_abandoned(monkeypatch):
    tmp_path = make_test_dir("runtime-failure-bookkeeping-deadline")
    clock = RuntimeClock()
    task, _device_config, _clock = _make_runtime_task(tmp_path, playlists=[], clock=clock)
    command = RefreshCommand.create(
        kind=CommandKind.DISPLAY,
        source=CommandSource.MANUAL,
        plugin_id="deadline",
        payload={"refresh_type": "Manual Update", "settings": {}},
        now_monotonic=clock.monotonic(),
        deadline_monotonic=clock.monotonic() + 5,
    )
    submitted = task.refresh_queue.submit(command)
    entry = task.refresh_queue.take(timeout=0)
    monkeypatch.setattr(
        task,
        "_execute_command",
        lambda _command: (_ for _ in ()).throw(RuntimeError("render")),
    )
    monkeypatch.setattr(task, "_record_command_failure", lambda *_args: clock.advance(5))

    task._process_queue_entry(entry)

    finished = task.refresh_queue.get_entry(submitted.id).job
    assert finished.status is JobStatus.ABANDONED
    assert finished.error_code == "deadline_expired"


def test_manual_failure_then_success_clears_global_retry_streak(monkeypatch):
    tmp_path = make_test_dir("runtime-manual-retry-success")
    clock = RuntimeClock()
    task, _device_config, _clock = _make_runtime_task(tmp_path, playlists=[], clock=clock)

    def manual_command():
        return RefreshCommand.create(
            kind=CommandKind.DISPLAY,
            source=CommandSource.MANUAL,
            plugin_id="manual",
            payload={"refresh_type": "Manual Update", "settings": {}},
            now_monotonic=clock.monotonic(),
            deadline_monotonic=clock.monotonic() + 60,
        )

    first = task.refresh_queue.submit(manual_command())
    first_entry = task.refresh_queue.take(timeout=0)
    monkeypatch.setattr(
        task,
        "_execute_command",
        lambda _command: (_ for _ in ()).throw(RuntimeError("render")),
    )
    task._process_queue_entry(first_entry)
    assert task.refresh_queue.get_entry(first.id).job.status is JobStatus.FAILED
    assert [entry.key for entry in task.retry_registry.snapshot()] == [RetryRegistry.GLOBAL_KEY]

    second = task.refresh_queue.submit(manual_command())
    second_entry = task.refresh_queue.take(timeout=0)
    monkeypatch.setattr(task, "_execute_command", lambda _command: None)
    task._process_queue_entry(second_entry)

    assert task.refresh_queue.get_entry(second.id).job.status is JobStatus.SUCCEEDED
    assert task.retry_registry.snapshot() == ()


def test_instance_success_does_not_clear_prior_global_selection_retry(monkeypatch):
    tmp_path = make_test_dir("runtime-global-retry-success")
    playlist = _runtime_playlist(_runtime_plugin_data())
    task, device_config, _clock = _make_runtime_task(tmp_path, playlists=[playlist])
    monkeypatch.setattr(
        task,
        "_select_cached_display_command",
        lambda _current_dt: (_ for _ in ()).throw(RuntimeError("selection")),
    )

    task._run_one_iteration_for_test()
    assert [entry.key for entry in task.retry_registry.snapshot()] == [RetryRegistry.GLOBAL_KEY]

    instance = device_config.playlist_manager.snapshot_instance(
        device_config.playlist_manager.first_instance_uuid()
    )
    command = task._playlist_command(
        playlist.name,
        instance,
        source=CommandSource.BACKGROUND,
        intent=RefreshIntent.DATA_REFRESH,
        kind=CommandKind.CACHE_REFRESH,
    )
    submitted = task.refresh_queue.submit(command)
    entry = task.refresh_queue.take(timeout=0)
    monkeypatch.setattr(task, "_execute_command", lambda _command: None)
    task._process_queue_entry(entry)

    assert task.refresh_queue.get_entry(submitted.id).job.status is JobStatus.SUCCEEDED
    assert [entry.key for entry in task.retry_registry.snapshot()] == [
        RetryRegistry.GLOBAL_KEY
    ]


def test_success_bookkeeping_error_cannot_kill_worker_after_terminalization(monkeypatch):
    tmp_path = make_test_dir("runtime-success-bookkeeping")
    task, _device_config, clock = _make_runtime_task(tmp_path, playlists=[], clock=RuntimeClock())
    command = RefreshCommand.create(
        kind=CommandKind.CACHE_REFRESH,
        source=CommandSource.BACKGROUND,
        plugin_id="success",
        payload={"refresh_type": "Playlist", "settings": {}},
        now_monotonic=clock.monotonic(),
        deadline_monotonic=clock.monotonic() + 60,
    )
    submitted = task.refresh_queue.submit(command)
    entry = task.refresh_queue.take(timeout=0)
    monkeypatch.setattr(task, "_execute_command", lambda _command: None)
    monkeypatch.setattr(
        task.scheduler_state,
        "record_success",
        lambda: (_ for _ in ()).throw(RuntimeError("bookkeeping")),
    )

    task._process_queue_entry(entry)

    assert task.refresh_queue.get_entry(submitted.id).job.status is JobStatus.SUCCEEDED


def test_memory_maintenance_error_cannot_kill_worker_after_terminalization(monkeypatch):
    tmp_path = make_test_dir("runtime-maintenance-bookkeeping")
    task, _device_config, clock = _make_runtime_task(tmp_path, playlists=[], clock=RuntimeClock())
    command = RefreshCommand.create(
        kind=CommandKind.DISPLAY,
        source=CommandSource.MANUAL,
        plugin_id="success",
        payload={"refresh_type": "Manual Update", "settings": {}},
        now_monotonic=clock.monotonic(),
        deadline_monotonic=clock.monotonic() + 60,
    )
    submitted = task.refresh_queue.submit(command)
    entry = task.refresh_queue.take(timeout=0)
    monkeypatch.setattr(task, "_execute_command", lambda _command: None)
    monkeypatch.setattr(
        task,
        "_run_memory_maintenance",
        lambda *_args: (_ for _ in ()).throw(RuntimeError("maintenance")),
    )

    task._process_queue_entry(entry)

    assert task.refresh_queue.get_entry(submitted.id).job.status is JobStatus.SUCCEEDED


@pytest.mark.parametrize(
    ("render_error", "expected_status"),
    [
        (None, JobStatus.SUCCEEDED),
        (RuntimeError("render failed"), JobStatus.FAILED),
    ],
)
def test_manual_preview_upload_is_removed_at_job_terminal(
    monkeypatch,
    render_error,
    expected_status,
):
    tmp_path = make_test_dir(f"manual-transient-{expected_status.value}")
    task, _device_config, _clock = _make_runtime_task(tmp_path, playlists=[])
    task.running = True
    upload = tmp_path / "preview.png"
    upload.write_bytes(b"preview")

    job = task.submit_manual_update(
        ManualRefresh("weather", {"imageFile": str(upload)}),
        transient_paths=(str(upload),),
    )
    entry = task.refresh_queue.take(timeout=0)

    def execute(_command):
        assert upload.read_bytes() == b"preview"
        if render_error is not None:
            raise render_error

    monkeypatch.setattr(task, "_execute_command", execute)
    task._process_queue_entry(entry)

    assert not upload.exists()
    assert task.refresh_queue.get_entry(job["id"]).job.status is expected_status


def test_blocking_manual_completion_map_holds_only_events_and_is_removed(monkeypatch):
    tmp_path = make_test_dir("runtime-completion-event-map")
    task, _device_config, _clock = _make_runtime_task(tmp_path, playlists=[])
    render_started = threading.Event()
    allow_render = threading.Event()
    errors = []
    monkeypatch.setattr(
        "src.refresh_task.get_plugin_instance",
        lambda config: BlockingRuntimePlugin(render_started, allow_render),
    )
    task.start()
    caller = threading.Thread(
        target=lambda: _capture_manual_error(
            task,
            ManualRefresh("blocking_manual", {"id": "blocking"}),
            errors,
        )
    )
    caller.start()
    try:
        assert render_started.wait(1.0)
        assert task._completion_events
        assert all(type(event) is threading.Event for event in task._completion_events.values())
        allow_render.set()
        caller.join(timeout=1.0)

        assert not caller.is_alive()
        assert errors == []
        assert task._completion_events == {}
    finally:
        allow_render.set()
        caller.join(timeout=1.0)
        task.stop(join_timeout=1.0)


def _capture_manual_error(task, action, errors):
    try:
        task.manual_update(action)
    except Exception as error:
        errors.append(error)


def test_legacy_background_candidates_are_clamped_to_one_cache_command(monkeypatch):
    tmp_path = make_test_dir("runtime-background-command-per-candidate")
    playlist = _runtime_playlist(
        _runtime_plugin_data("one", "One", latest_refresh_time=None),
        _runtime_plugin_data("two", "Two", latest_refresh_time=None),
    )
    task, device_config, _clock = _make_runtime_task(tmp_path, playlists=[playlist])
    device_config.config.update({"theme_mode": "day", "active_theme": "day"})
    monkeypatch.setattr("src.refresh_task.get_plugin_instance", lambda config: FakePlugin([]))

    commands = task._select_background_commands(
        datetime(2026, 5, 26, 7, 2, tzinfo=timezone.utc)
    )

    assert len(commands) == 1
    assert all(command.kind is CommandKind.CACHE_REFRESH for command in commands)
    assert commands[0].instance_uuid == playlist.plugins[0].instance_uuid


def test_legacy_background_trigger_executes_on_single_command_worker(monkeypatch):
    tmp_path = make_test_dir("runtime-background-single-worker")
    playlist = _runtime_playlist(_runtime_plugin_data())
    task, device_config, _clock = _make_runtime_task(tmp_path, playlists=[playlist])
    device_config.config.update({"theme_mode": "day", "active_theme": "day"})
    _write_runtime_cache(task, playlist.plugins[0])
    called = threading.Event()
    thread_ids = []

    class ThreadRecordingPlugin(FakePlugin):
        def generate_image(self, settings, config):
            thread_ids.append(threading.get_ident())
            called.set()
            return super().generate_image(settings, config)

    monkeypatch.setattr(
        "src.refresh_task.get_plugin_instance",
        lambda config: ThreadRecordingPlugin([]),
    )
    monkeypatch.setattr(task, "_cache_refresh_under_resource_pressure", lambda **kwargs: False)
    task.start()
    try:
        assert task.wait_until_waiting(timeout=1.0)
        task._start_due_plugin_cache_refresh(
            playlist,
            datetime(2026, 5, 26, 7, 2, tzinfo=timezone.utc),
            force=True,
        )

        assert called.wait(1.0)
        assert thread_ids == [task.thread.ident]
    finally:
        task.stop(join_timeout=1.0)


def test_cleanup_context_and_managed_cache_paths_are_bounded_public_contracts():
    tmp_path = make_test_dir("runtime-cleanup-contracts")
    clock = RuntimeClock()
    task, _device_config, _clock = _make_runtime_task(tmp_path, playlists=[], clock=clock)
    staging = tmp_path / ".refresh-staging"
    staging.mkdir()
    cache = tmp_path / ".refresh-cache"
    cache.mkdir()
    filenames = tuple(
        task._cache_identity_filename("target", 1, 2, mode)
        for mode in (None, "day", "night")
    )
    expected_paths = []
    for directory in (staging, cache):
        for filename in filenames:
            path = directory / filename
            path.write_bytes(b"owned")
            expected_paths.append(str(path))
    (staging / task._cache_identity_filename("other", 1, 2)).write_bytes(b"other")

    context = task.make_cleanup_context(timeout_seconds=12)
    paths = task.managed_cache_paths(
        "target",
        plugin_id="weather",
        instance_name="Main View",
    )

    assert context.cancel_event is task.stop_event
    assert context.deadline_monotonic == 12.0
    assert paths == tuple(sorted(expected_paths))
    task.stop(join_timeout=0)
    with pytest.raises(TaskCancelled):
        context.raise_if_cancelled()


def test_authoritative_cache_identity_changes_for_same_name_replacement():
    tmp_path = make_test_dir("runtime-versioned-cache-identity")
    first = _runtime_playlist(_runtime_plugin_data("weather", "Main"))
    second = _runtime_playlist(_runtime_plugin_data("weather", "Main"))
    task, _device_config, _clock = _make_runtime_task(tmp_path, playlists=[])

    first_path = Path(task.cache_path_for_snapshot(first.plugins[0].snapshot()))
    second_path = Path(task.cache_path_for_snapshot(second.plugins[0].snapshot()))

    assert first.plugins[0].instance_uuid != second.plugins[0].instance_uuid
    assert first_path != second_path
    assert first_path.parent.name == ".refresh-cache"
    assert second_path.parent.name == ".refresh-cache"
    assert "weather_Main.png" not in {first_path.name, second_path.name}


def test_manual_submission_propagates_queue_full_and_stopping_errors():
    tmp_path = make_test_dir("runtime-queue-errors")
    queue = RefreshQueue(capacity=1, manual_reserved=0)
    task = RefreshTask(
        RuntimeDeviceConfig(tmp_path),
        RecordingDisplayManager(),
        refresh_queue=queue,
    )
    task.running = True
    task.submit_manual_update(ManualRefresh("one", {"id": "one"}))

    with pytest.raises(QueueFullError):
        task.submit_manual_update(ManualRefresh("two", {"id": "two"}))

    task.stop(join_timeout=0)
    with pytest.raises(QueueStoppingError):
        task.submit_manual_update(ManualRefresh("three", {"id": "three"}))


def test_playlist_uuid_submission_propagates_queue_stopping_error():
    tmp_path = make_test_dir("runtime-playlist-stopping-error")
    playlist = _runtime_playlist(_runtime_plugin_data())
    task, device_config, _clock = _make_runtime_task(tmp_path, playlists=[playlist])
    device_config.config.update({"theme_mode": "day", "active_theme": "day"})
    instance_uuid = device_config.playlist_manager.first_instance_uuid()
    task.stop(join_timeout=0)

    with pytest.raises(QueueStoppingError):
        task.submit_playlist_display(instance_uuid)


def test_runtime_success_state_takes_precedence_over_legacy_refresh_time():
    tmp_path = make_test_dir("runtime-success-precedes-legacy")
    playlist = _runtime_playlist(
        _runtime_plugin_data(latest_refresh_time="2026-05-26T07:00:00+00:00")
    )
    task, _device_config, _clock = _make_runtime_task(tmp_path, playlists=[playlist])
    instance = playlist.plugins[0].snapshot()

    task.runtime_state.record_success(
        instance.instance_uuid,
        "2026-05-26T07:05:00+00:00",
    )
    task.runtime_state.record_failure(
        instance.instance_uuid,
        "2026-05-26T07:06:00+00:00",
        "offline",
        "2026-05-26T07:06:30+00:00",
    )

    assert task._snapshot_latest_refresh_dt(instance) == datetime(
        2026,
        5,
        26,
        7,
        5,
        tzinfo=timezone.utc,
    )


def test_config_change_prunes_deleted_runtime_instance_to_a_tombstone():
    tmp_path = make_test_dir("runtime-config-change-tombstone")
    playlist = _runtime_playlist(
        _runtime_plugin_data("one", "One"),
        _runtime_plugin_data("two", "Two"),
    )
    task, device_config, _clock = _make_runtime_task(tmp_path, playlists=[playlist])
    manager = device_config.playlist_manager
    removed_uuid = playlist.plugins[0].instance_uuid
    current_uuid = playlist.plugins[1].instance_uuid
    task.runtime_state.record_success(
        removed_uuid,
        "2026-05-26T07:05:00+00:00",
    )
    task.runtime_state.record_success(
        current_uuid,
        "2026-05-26T07:05:00+00:00",
    )

    assert manager.delete_plugin_instance(removed_uuid)
    task.signal_config_change()

    snapshot = task.runtime_state.snapshot()
    assert snapshot.instances[removed_uuid].tombstoned_at is not None
    assert snapshot.instances[current_uuid].tombstoned_at is None


def test_background_selection_waits_for_runtime_retry_deadline():
    tmp_path = make_test_dir("runtime-background-retry-deadline")
    playlist = _runtime_playlist(_runtime_plugin_data(latest_refresh_time=None))
    task, device_config, _clock = _make_runtime_task(tmp_path, playlists=[playlist])
    device_config.config.update({"theme_mode": "day", "active_theme": "day"})
    instance = playlist.plugins[0]
    task.runtime_state.record_failure(
        instance.instance_uuid,
        "2026-05-26T07:05:00+00:00",
        "offline",
        "2026-05-26T07:05:30+00:00",
    )

    delayed = task._select_background_commands(
        datetime(2026, 5, 26, 7, 5, 10, tzinfo=timezone.utc)
    )
    due = task._select_background_commands(
        datetime(2026, 5, 26, 7, 5, 31, tzinfo=timezone.utc)
    )

    assert delayed == ()
    assert len(due) == 1
    assert due[0].instance_uuid == instance.instance_uuid


def test_runtime_worker_records_data_failure_without_advancing_seeded_model_success(
    monkeypatch,
):
    class ExplodingPlugin:
        config = {}

        def render_themed_image(self, settings, device_config, **_kwargs):
            return self.generate_image(settings, device_config)

        def generate_image(self, settings, device_config):
            raise RuntimeError("offline")

    tmp_path = make_test_dir("runtime-attempt-failure-state")
    legacy_success = "2026-05-26T07:00:00+00:00"
    playlist = _runtime_playlist(
        _runtime_plugin_data(latest_refresh_time=legacy_success)
    )
    task, device_config, _clock = _make_runtime_task(tmp_path, playlists=[playlist])
    device_config.config.update({"theme_mode": "day", "active_theme": "day"})
    instance = playlist.plugins[0]
    _write_runtime_cache(task, instance)
    monkeypatch.setattr("src.refresh_task.get_plugin_instance", lambda _config: ExplodingPlugin())
    task.start()
    try:
        assert task.wait_until_waiting(timeout=1.0)
        command = task._playlist_command(
            playlist.name,
            device_config.playlist_manager.snapshot_instance(instance.instance_uuid),
            source=CommandSource.BACKGROUND,
            intent=RefreshIntent.DATA_REFRESH,
            force=False,
            display_cached_only=False,
            kind=CommandKind.CACHE_REFRESH,
        )
        submitted = task.refresh_queue.submit(command)
        job = task._job_payload(task.refresh_queue.get_entry(submitted.id))
        result = task.wait_for_job(job["id"], timeout=1.0)

        state = task.runtime_state.snapshot().instances[instance.instance_uuid]
        assert result["status"] == "failed"
        assert state.last_attempt_at is not None
        assert state.last_failure_at is not None
        assert state.data.last_success_at == legacy_success
        assert device_config.playlist_manager.snapshot_instance(
            instance.instance_uuid
        ).latest_refresh_time == legacy_success
    finally:
        task.stop(join_timeout=1.0)


def test_generated_cache_success_uses_runtime_state_not_user_config(monkeypatch):
    tmp_path = make_test_dir("runtime-cache-success-state")
    legacy_success = "2026-05-26T07:00:00+00:00"
    current_dt = datetime(2026, 5, 26, 7, 5, tzinfo=timezone.utc)
    playlist = _runtime_playlist(
        _runtime_plugin_data(latest_refresh_time=legacy_success)
    )
    task, device_config, _clock = _make_runtime_task(tmp_path, playlists=[playlist])
    device_config.config.update({"theme_mode": "day", "active_theme": "day"})
    instance = playlist.plugins[0]
    _write_runtime_cache(task, instance)
    monkeypatch.setattr(task, "_get_current_datetime", lambda: current_dt)
    monkeypatch.setattr("src.refresh_task.get_plugin_instance", lambda _config: CapturePlugin([]))
    task.start()
    try:
        assert task.wait_until_waiting(timeout=1.0)
        command = task._playlist_command(
            playlist.name,
            device_config.playlist_manager.snapshot_instance(instance.instance_uuid),
            source=CommandSource.BACKGROUND,
            intent=RefreshIntent.DATA_REFRESH,
            force=False,
            display_cached_only=False,
            kind=CommandKind.CACHE_REFRESH,
        )
        submitted = task.refresh_queue.submit(command)
        job = task._job_payload(task.refresh_queue.get_entry(submitted.id))
        result = task.wait_for_job(job["id"], timeout=1.0)

        state = task.runtime_state.snapshot().instances[instance.instance_uuid]
        assert result["status"] == "completed"
        assert state.last_success_at == current_dt.isoformat()
        assert device_config.playlist_manager.snapshot_instance(
            instance.instance_uuid
        ).latest_refresh_time == legacy_success
    finally:
        task.stop(join_timeout=1.0)


def test_stop_flushes_runtime_state_synchronously_after_entering_drain():
    states = []
    holder = {}

    class RecordingRuntimeState:
        def flush(self):
            states.append(holder["task"].lifecycle.state)
            return True

    tmp_path = make_test_dir("runtime-state-drain-flush")
    task = RefreshTask(
        RuntimeDeviceConfig(tmp_path),
        RecordingDisplayManager(),
        runtime_state_store=RecordingRuntimeState(),
    )
    holder["task"] = task

    assert task.stop(join_timeout=0) is True
    assert states == [LifecycleState.DRAINING]


class PresentationRuntimeDeviceConfig(RuntimeDeviceConfig):
    """Marks the ``prepared_plugin`` id as a refresh-on-display presenter."""

    def get_plugin(self, plugin_id):
        if plugin_id == "prepared_plugin":
            return {
                "id": plugin_id,
                "refresh_on_display": True,
                "_manifest": SimpleNamespace(
                    capabilities=SimpleNamespace(
                        supports_presentation_refresh=True,
                    ),
                    theme=None,
                ),
            }
        return {"id": plugin_id}


def _prepared_rotation_task(tmp_path):
    playlist = _runtime_playlist(
        _runtime_plugin_data("prepared_plugin", "Prepared"),
    )
    device_config = PresentationRuntimeDeviceConfig(tmp_path, [playlist])
    device_config.config["plugin_cycle_interval_seconds"] = 60
    device_config.config["display_triggered_refresh_enabled"] = True
    device_config.config.update({"theme_mode": "day", "active_theme": "day"})
    device_config.refresh_info.refresh_time = "2026-07-11T11:00:00+00:00"
    task = RefreshTask(device_config, RecordingDisplayManager())
    _write_runtime_cache(task, playlist.plugins[0])
    return task, playlist.plugins[0]


def test_rotation_defers_display_until_presentation_prepared(tmp_path):
    task, instance = _prepared_rotation_task(
        make_test_dir("rotation-prepare-ahead")
    )
    first_dt = datetime(2026, 7, 11, 12, 0, tzinfo=timezone.utc)

    deferred = task._select_cached_display_command(first_dt)

    request = task.runtime_state.snapshot().instances[
        instance.instance_uuid
    ].presentation_request
    assert deferred is None
    assert request is not None
    assert request.prepared_at is None

    still_waiting = task._select_cached_display_command(
        first_dt + timedelta(seconds=30)
    )
    assert still_waiting is None

    assert task.runtime_state.mark_presentation_prepared(
        instance.instance_uuid,
        request.request_id,
        (first_dt + timedelta(seconds=45)).isoformat(),
        None,
    )
    task.presentation_cache.save(
        task._presentation_candidate(instance.snapshot(), request, None),
        Image.new("RGB", (32, 16), "white"),
    )
    ready = task._select_cached_display_command(
        first_dt + timedelta(seconds=60)
    )

    assert ready is not None
    assert ready.intent is RefreshIntent.DISPLAY_CACHE
    assert ready.payload.get("presentation_request_id") == request.request_id


def test_rotation_falls_back_to_cached_display_when_prepare_stalls(tmp_path):
    task, instance = _prepared_rotation_task(
        make_test_dir("rotation-prepare-stall")
    )
    first_dt = datetime(2026, 7, 11, 12, 0, tzinfo=timezone.utc)

    assert task._select_cached_display_command(first_dt) is None
    fallback = task._select_cached_display_command(
        first_dt + timedelta(seconds=181)
    )

    playlist = task.device_config.get_playlist_manager().playlists[0]
    assert fallback is not None
    assert fallback.instance_uuid == instance.instance_uuid
    assert fallback.intent is RefreshIntent.DISPLAY_CACHE
    assert fallback.payload["automatic_rotation"] is True
    assert fallback.payload["display_cached_only"] is True
    assert fallback.allow_prepared_presentation is False
    assert instance.instance_uuid in playlist.plugin_rotation_queue
    assert playlist.is_rotation_reservation_current(instance.instance_uuid) is True


def test_random_display_never_instantiates_plugin_or_calls_renderer(monkeypatch):
    tmp_path = make_test_dir("cache-only-random-display")
    playlist = _runtime_playlist(
        _runtime_plugin_data("one", "One"),
        _runtime_plugin_data("two", "Two"),
    )
    task, device_config, _clock = _make_runtime_task(
        tmp_path,
        playlists=[playlist],
        cycle_seconds=60,
    )
    device_config.config.update({"theme_mode": "day", "active_theme": "day"})
    device_config.refresh_info = RefreshInfo(
        refresh_type="Playlist",
        playlist=playlist.name,
        plugin_id="old",
        plugin_instance="Old",
        refresh_time="2026-07-11T11:00:00+00:00",
        image_hash="old",
    )
    for instance in playlist.plugins:
        _write_runtime_cache(task, instance)
    monkeypatch.setattr(
        "src.refresh_task.get_plugin_instance",
        lambda _config: (_ for _ in ()).throw(
            AssertionError("cache display must not instantiate a plugin")
        ),
    )

    command = task._select_cached_display_command(
        datetime(2026, 7, 11, 12, 0, tzinfo=timezone.utc)
    )
    result = task._execute_command(command)

    assert result is not None
    assert command.intent is RefreshIntent.DISPLAY_CACHE
    assert len(task.display_manager.calls) == 1


def test_catalog_display_never_reopens_path_after_bound_validation(monkeypatch):
    tmp_path = make_test_dir("cache-only-bound-descriptor")
    playlist = _runtime_playlist(_runtime_plugin_data("one", "One"))
    task, device_config, _clock = _make_runtime_task(
        tmp_path,
        playlists=[playlist],
        cycle_seconds=60,
    )
    device_config.config.update({"theme_mode": "day", "active_theme": "day"})
    device_config.refresh_info.refresh_time = "2026-07-11T11:00:00+00:00"
    cache_path = _write_runtime_cache(
        task,
        playlist.plugins[0],
        Image.new("RGB", (2, 1), "red"),
    )
    replacement = tmp_path / "replacement.png"
    Image.new("RGB", (2, 1), "blue").save(replacement)
    command = task._select_cached_display_command(
        datetime(2026, 7, 11, 12, 0, tzinfo=timezone.utc)
    )
    original_bytes = cache_path.read_bytes()
    replacement_bytes = replacement.read_bytes()
    real_path_loader = refresh_task_module._load_image_copy
    reopen_count = 0

    def swap_for_reopen_then_restore(path):
        nonlocal reopen_count
        reopen_count += 1
        Path(path).write_bytes(replacement_bytes)
        try:
            return real_path_loader(path)
        finally:
            Path(path).write_bytes(original_bytes)

    monkeypatch.setattr(
        refresh_task_module,
        "_load_image_copy",
        swap_for_reopen_then_restore,
    )

    image = task._load_catalog_display_image(command, resolved=None)

    try:
        assert image.getpixel((0, 0)) == (255, 0, 0)
        assert reopen_count == 0
    finally:
        image.close()


def test_random_selection_passes_only_catalog_eligible_uuids_to_model():
    tmp_path = make_test_dir("cache-only-random-eligibility")
    playlist = _runtime_playlist(
        _runtime_plugin_data("one", "One"),
        _runtime_plugin_data("two", "Two"),
        _runtime_plugin_data("three", "Three"),
    )
    task, device_config, _clock = _make_runtime_task(
        tmp_path,
        playlists=[playlist],
        cycle_seconds=60,
    )
    device_config.config.update({"theme_mode": "day", "active_theme": "day"})
    device_config.refresh_info.refresh_time = "2026-07-11T11:00:00+00:00"
    expected = {
        playlist.plugins[0].instance_uuid,
        playlist.plugins[2].instance_uuid,
    }
    _write_runtime_cache(task, playlist.plugins[0])
    corrupt = Path(task.cache_path_for_snapshot(playlist.plugins[1].snapshot()))
    corrupt.parent.mkdir(parents=True, exist_ok=True)
    corrupt.write_bytes(b"not-a-png")
    _write_runtime_cache(task, playlist.plugins[2])
    manager = device_config.playlist_manager
    original_select = manager.reserve_next_active_instance
    observed = []

    def select_with_observation(*args, **kwargs):
        observed.append(kwargs["eligible_instance_uuids"])
        return original_select(*args, **kwargs)

    manager.reserve_next_active_instance = select_with_observation

    command = task._select_cached_display_command(
        datetime(2026, 7, 11, 12, 0, tzinfo=timezone.utc)
    )

    assert command is not None
    assert observed == [frozenset(expected)]
    assert command.instance_uuid in expected


def test_random_selection_reserves_full_playlist_bag_without_consuming(monkeypatch):
    tmp_path = make_test_dir("cache-only-full-shuffle-bag-reservation")
    playlist = _runtime_playlist(
        _runtime_plugin_data("one", "One"),
        _runtime_plugin_data("two", "Two"),
        _runtime_plugin_data("three", "Three"),
    )
    task, device_config, _clock = _make_runtime_task(
        tmp_path,
        playlists=[playlist],
        cycle_seconds=60,
    )
    device_config.config.update({"theme_mode": "day", "active_theme": "day"})
    device_config.refresh_info.refresh_time = "2026-07-11T11:00:00+00:00"
    _write_runtime_cache(task, playlist.plugins[1])
    monkeypatch.setattr("src.model.random.shuffle", lambda items: None)

    command = task._select_cached_display_command(
        datetime(2026, 7, 11, 12, 0, tzinfo=timezone.utc)
    )

    assert command.instance_uuid == playlist.plugins[1].instance_uuid
    assert command.payload["automatic_rotation"] is True
    assert playlist.plugin_rotation_pool == [
        instance.instance_uuid for instance in playlist.plugins
    ]
    assert playlist.plugin_rotation_queue == [
        instance.instance_uuid for instance in playlist.plugins
    ]


def test_successful_automatic_display_acknowledges_exactly_one_bag_member(monkeypatch):
    tmp_path = make_test_dir("cache-only-shuffle-bag-success-ack")
    playlist = _runtime_playlist(
        _runtime_plugin_data("one", "One"),
        _runtime_plugin_data("two", "Two"),
    )
    task, device_config, _clock = _make_runtime_task(
        tmp_path,
        playlists=[playlist],
        cycle_seconds=60,
    )
    current_dt = datetime(2026, 7, 11, 12, 0, tzinfo=timezone.utc)
    device_config.config.update({"theme_mode": "day", "active_theme": "day"})
    device_config.refresh_info.refresh_time = "2026-07-11T11:00:00+00:00"
    for instance in playlist.plugins:
        _write_runtime_cache(task, instance)
    monkeypatch.setattr("src.model.random.shuffle", lambda items: None)
    monkeypatch.setattr(task, "_get_current_datetime", lambda: current_dt)
    command = task._select_cached_display_command(current_dt)
    selected_uuid = command.instance_uuid

    submitted = task.refresh_queue.submit(command)
    task._process_queue_entry(task.refresh_queue.take(timeout=0))

    assert task.refresh_queue.get_entry(submitted.id).job.status is JobStatus.SUCCEEDED
    assert playlist.plugin_rotation_pool == [
        instance.instance_uuid for instance in playlist.plugins
    ]
    assert playlist.plugin_rotation_queue == [
        instance.instance_uuid
        for instance in playlist.plugins
        if instance.instance_uuid != selected_uuid
    ]
    assert playlist.plugin_rotation_recent_history == [selected_uuid]
    assert device_config.write_count == 1


def test_automatic_rotation_forces_physical_display_before_ack_for_same_target(
    monkeypatch,
):
    tmp_path = make_test_dir("cache-only-shuffle-bag-forces-panel-write")
    playlist = _runtime_playlist(_runtime_plugin_data("only", "Only"))
    task, device_config, _clock = _make_runtime_task(
        tmp_path,
        playlists=[playlist],
        cycle_seconds=60,
    )
    current_dt = datetime(2026, 7, 11, 12, 0, tzinfo=timezone.utc)
    device_config.config.update({"theme_mode": "day", "active_theme": "day"})
    instance = playlist.plugins[0]
    image = Image.new("RGB", (1, 1), "black")
    _write_runtime_cache(task, instance, image)
    device_config.refresh_info = RefreshInfo(
        refresh_type="Playlist",
        playlist=playlist.name,
        plugin_id=instance.plugin_id,
        plugin_instance=instance.name,
        refresh_time="2026-07-11T11:00:00+00:00",
        image_hash=compute_image_hash(image),
    )
    monkeypatch.setattr(task, "_get_current_datetime", lambda: current_dt)
    command = task._select_cached_display_command(current_dt)

    task._execute_command(command)

    assert len(task.display_manager.calls) == 1
    assert playlist.plugin_rotation_queue == []


def test_failed_automatic_display_keeps_reserved_bag_member(monkeypatch):
    tmp_path = make_test_dir("cache-only-shuffle-bag-display-failure")
    playlist = _runtime_playlist(_runtime_plugin_data("only", "Only"))
    task, device_config, _clock = _make_runtime_task(
        tmp_path,
        playlists=[playlist],
        cycle_seconds=60,
    )
    current_dt = datetime(2026, 7, 11, 12, 0, tzinfo=timezone.utc)
    device_config.config.update({"theme_mode": "day", "active_theme": "day"})
    device_config.refresh_info.refresh_time = "2026-07-11T11:00:00+00:00"
    _write_runtime_cache(task, playlist.plugins[0])
    monkeypatch.setattr(task, "_get_current_datetime", lambda: current_dt)
    command = task._select_cached_display_command(current_dt)
    before = list(playlist.plugin_rotation_queue)
    monkeypatch.setattr(
        task,
        "_display_image",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("panel failed")),
    )

    with pytest.raises(RuntimeError, match="panel failed"):
        task._execute_command(command)

    assert playlist.plugin_rotation_queue == before
    assert playlist.is_rotation_reservation_current(command.instance_uuid) is True
    assert device_config.write_count == 0


def test_unproven_automatic_display_does_not_acknowledge_shuffle_bag(monkeypatch):
    tmp_path = make_test_dir("cache-only-shuffle-bag-evidence-failure")
    playlist = _runtime_playlist(_runtime_plugin_data("only", "Only"))
    task, device_config, _clock = _make_runtime_task(
        tmp_path,
        playlists=[playlist],
        cycle_seconds=60,
    )
    current_dt = datetime(2026, 7, 11, 12, 0, tzinfo=timezone.utc)
    device_config.config.update({"theme_mode": "day", "active_theme": "day"})
    device_config.refresh_info.refresh_time = "2026-07-11T11:00:00+00:00"
    _write_runtime_cache(task, playlist.plugins[0])
    monkeypatch.setattr(task, "_get_current_datetime", lambda: current_dt)
    command = task._select_cached_display_command(current_dt)
    before = list(playlist.plugin_rotation_queue)
    monkeypatch.setattr(
        task,
        "_display_commit_evidence",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("display evidence missing")
        ),
    )

    with pytest.raises(RuntimeError, match="display evidence missing"):
        task._execute_command(command)

    assert playlist.plugin_rotation_queue == before
    assert playlist.is_rotation_reservation_current(command.instance_uuid) is True
    assert device_config.write_count == 0


def test_automatic_display_config_write_failure_rolls_back_bag_ack(monkeypatch):
    tmp_path = make_test_dir("cache-only-shuffle-bag-write-rollback")
    playlist = _runtime_playlist(_runtime_plugin_data("only", "Only"))
    task, device_config, _clock = _make_runtime_task(
        tmp_path,
        playlists=[playlist],
        cycle_seconds=60,
    )
    current_dt = datetime(2026, 7, 11, 12, 0, tzinfo=timezone.utc)
    device_config.config.update({"theme_mode": "day", "active_theme": "day"})
    device_config.refresh_info.refresh_time = "2026-07-11T11:00:00+00:00"
    _write_runtime_cache(task, playlist.plugins[0])
    monkeypatch.setattr(task, "_get_current_datetime", lambda: current_dt)
    command = task._select_cached_display_command(current_dt)
    before = list(playlist.plugin_rotation_queue)
    monkeypatch.setattr(
        device_config,
        "write_config",
        lambda: (_ for _ in ()).throw(RuntimeError("config write failed")),
    )

    with pytest.raises(RuntimeError, match="config write failed"):
        task._execute_command(command)

    assert playlist.plugin_rotation_queue == before
    assert playlist.is_rotation_reservation_current(command.instance_uuid) is True


def test_manual_exact_display_does_not_acknowledge_automatic_shuffle_bag(monkeypatch):
    tmp_path = make_test_dir("cache-only-manual-does-not-consume-shuffle-bag")
    playlist = _runtime_playlist(
        _runtime_plugin_data("one", "One"),
        _runtime_plugin_data("two", "Two"),
    )
    task, device_config, _clock = _make_runtime_task(tmp_path, playlists=[playlist])
    current_dt = datetime(2026, 7, 11, 12, 0, tzinfo=timezone.utc)
    device_config.config.update({"theme_mode": "day", "active_theme": "day"})
    instance = playlist.plugins[0]
    _write_runtime_cache(task, instance)
    monkeypatch.setattr("src.model.random.shuffle", lambda items: None)
    monkeypatch.setattr(task, "_get_current_datetime", lambda: current_dt)
    assert playlist.reserve_next_plugin(
        {item.instance_uuid for item in playlist.plugins}
    ).instance_uuid == instance.instance_uuid
    before = list(playlist.plugin_rotation_queue)
    command = task._playlist_command(
        playlist.name,
        instance.snapshot(),
        source=CommandSource.MANUAL,
        intent=RefreshIntent.DISPLAY_CACHE,
        force=False,
        display_cached_only=True,
        priority=100,
        current_dt=current_dt,
    )

    task._execute_command(command)

    assert playlist.plugin_rotation_queue == before
    assert playlist.is_rotation_reservation_current(instance.instance_uuid) is True


@pytest.mark.parametrize("cache_state", ["missing", "corrupt"])
def test_missing_or_corrupt_cache_skips_candidate_without_placeholder_or_provider_call(
    monkeypatch,
    cache_state,
):
    tmp_path = make_test_dir(f"cache-only-{cache_state}")
    playlist = _runtime_playlist(_runtime_plugin_data("only", "Only"))
    task, device_config, _clock = _make_runtime_task(
        tmp_path,
        playlists=[playlist],
        cycle_seconds=60,
    )
    device_config.config.update({"theme_mode": "day", "active_theme": "day"})
    device_config.refresh_info.refresh_time = "2026-07-11T11:00:00+00:00"
    if cache_state == "corrupt":
        corrupt = Path(task.cache_path_for_snapshot(playlist.plugins[0].snapshot()))
        corrupt.parent.mkdir(parents=True, exist_ok=True)
        corrupt.write_bytes(b"not-a-png")
    monkeypatch.setattr(
        "src.refresh_task.get_plugin_instance",
        lambda _config: (_ for _ in ()).throw(
            AssertionError("cache miss must not instantiate a plugin")
        ),
    )

    command = task._select_cached_display_command(
        datetime(2026, 7, 11, 12, 0, tzinfo=timezone.utc)
    )

    assert command is None
    assert task.display_manager.calls == []
    assert device_config.write_count == 0


def test_no_displayable_candidates_keep_current_display_and_rotation_anchor():
    tmp_path = make_test_dir("cache-only-empty-eligibility")
    playlist = _runtime_playlist(
        _runtime_plugin_data("one", "One"),
        _runtime_plugin_data("two", "Two"),
    )
    task, device_config, _clock = _make_runtime_task(
        tmp_path,
        playlists=[playlist],
        cycle_seconds=60,
    )
    device_config.config.update({"theme_mode": "day", "active_theme": "day"})
    anchor = "2026-07-11T11:00:00+00:00"
    device_config.refresh_info = RefreshInfo(
        refresh_type="Playlist",
        playlist=playlist.name,
        plugin_id=playlist.plugins[0].plugin_id,
        plugin_instance=playlist.plugins[0].name,
        refresh_time=anchor,
        image_hash="current",
    )
    playlist.current_plugin_index = 0
    playlist.plugin_rotation_queue = [playlist.plugins[1].instance_uuid]
    playlist.plugin_rotation_pool = [
        instance.instance_uuid for instance in playlist.plugins
    ]
    playlist.plugin_rotation_recent_history = [playlist.plugins[0].instance_uuid]
    before_rotation = (
        playlist.current_plugin_index,
        list(playlist.plugin_rotation_queue),
        list(playlist.plugin_rotation_pool),
        list(playlist.plugin_rotation_recent_history),
    )

    command = task._select_cached_display_command(
        datetime(2026, 7, 11, 12, 0, tzinfo=timezone.utc)
    )

    assert command is None
    assert device_config.refresh_info.refresh_time == anchor
    assert before_rotation == (
        playlist.current_plugin_index,
        list(playlist.plugin_rotation_queue),
        list(playlist.plugin_rotation_pool),
        list(playlist.plugin_rotation_recent_history),
    )
    assert task.display_manager.calls == []


def test_cache_disappearing_after_selection_cancels_without_refresh_failure():
    tmp_path = make_test_dir("cache-only-toctou-miss")
    playlist = _runtime_playlist(_runtime_plugin_data("only", "Only"))
    task, device_config, _clock = _make_runtime_task(
        tmp_path,
        playlists=[playlist],
        cycle_seconds=60,
    )
    device_config.config.update({"theme_mode": "day", "active_theme": "day"})
    device_config.refresh_info.refresh_time = "2026-07-11T11:00:00+00:00"
    cache_path = _write_runtime_cache(task, playlist.plugins[0])
    command = task._select_cached_display_command(
        datetime(2026, 7, 11, 12, 0, tzinfo=timezone.utc)
    )
    cache_path.unlink()

    submitted = task.refresh_queue.submit(command)
    task._process_queue_entry(task.refresh_queue.take(timeout=0))
    result = task.refresh_queue.get_entry(submitted.id).job

    assert result.status is JobStatus.CANCELED
    assert result.error_code == "cache_unavailable"
    assert task.runtime_state.snapshot().instances == {}
    scheduler = task.scheduler_snapshot()
    assert scheduler.last_failure_wall is None
    assert scheduler.last_error is None
    assert scheduler.retry_entries == ()
    assert task.display_manager.calls == []
    assert device_config.write_count == 0
    assert command.instance_uuid in playlist.plugin_rotation_queue
    assert playlist.is_rotation_reservation_current(command.instance_uuid) is True


def test_cache_superseded_after_selection_is_not_displayed():
    tmp_path = make_test_dir("cache-only-superseded-after-selection")
    plugin_data = _runtime_plugin_data("themed_plugin", "Themed Plugin")
    plugin_data["plugin_settings"]["themeMode"] = "auto"
    playlist = _runtime_playlist(plugin_data)
    task, device_config, _clock = _make_runtime_task(
        tmp_path,
        playlists=[playlist],
        cycle_seconds=60,
    )
    now = datetime(2026, 7, 15, 12, 0, tzinfo=timezone.utc)
    device_config.config.update({"theme_mode": "day", "active_theme": "day"})
    device_config.refresh_info.refresh_time = (now - timedelta(minutes=5)).isoformat()
    device_config.get_plugin = lambda _plugin_id: {
        "id": "themed_plugin",
        "_manifest": _theme_manifest("themed_plugin"),
    }
    instance = playlist.plugins[0].snapshot()
    day_cache = _write_runtime_theme_cache(task, instance, "day")
    day_promoted = now - timedelta(minutes=10)
    os.utime(
        day_cache,
        (day_promoted.timestamp(), day_promoted.timestamp()),
    )
    task.runtime_state.record_success(
        instance.instance_uuid,
        day_promoted.isoformat(),
        lane=RefreshLane.DATA,
        last_good_cache=LastGoodCacheState(
            theme_mode="day",
            structural_generation=instance.structural_generation,
            settings_revision=instance.settings_revision,
            promoted_at=day_promoted.isoformat(),
        ),
    )
    command = task._select_cached_display_command(now)
    assert command.payload["cache_theme_mode"] == "day"

    _write_runtime_theme_cache(task, instance, "night")
    task.runtime_state.record_success(
        instance.instance_uuid,
        now.isoformat(),
        lane=RefreshLane.DATA,
        last_good_cache=LastGoodCacheState(
            theme_mode="night",
            structural_generation=instance.structural_generation,
            settings_revision=instance.settings_revision,
            promoted_at=now.isoformat(),
        ),
    )

    submitted = task.refresh_queue.submit(command)
    task._process_queue_entry(task.refresh_queue.take(timeout=0))
    result = task.refresh_queue.get_entry(submitted.id).job

    assert result.status is JobStatus.CANCELED
    assert result.error_code == "cache_unavailable"
    assert task.display_manager.calls == []


def test_production_playlist_commands_always_have_explicit_intent():
    tree = ast.parse(inspect.getsource(refresh_task_module))
    missing = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = None
        if isinstance(node.func, ast.Attribute):
            name = node.func.attr
        if name not in {"_playlist_command", "create"}:
            continue
        if name == "create" and not (
            isinstance(node.func.value, ast.Name)
            and node.func.value.id == "RefreshCommand"
        ):
            continue
        if not any(keyword.arg == "intent" for keyword in node.keywords):
            missing.append((name, node.lineno))

    assert missing == []


def test_scheduler_enqueues_at_most_one_refresh_candidate_per_probe(monkeypatch):
    tmp_path = make_test_dir("independent-single-admission")
    playlist = _runtime_playlist(
        _runtime_plugin_data("one", "One", latest_refresh_time=None),
        _runtime_plugin_data("two", "Two", latest_refresh_time=None),
        _runtime_plugin_data("three", "Three", latest_refresh_time=None),
    )
    task, _device_config, _clock = _make_runtime_task(
        tmp_path,
        playlists=[playlist],
    )
    current_dt = datetime(2026, 7, 11, 12, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(task, "_get_current_datetime", lambda: current_dt)
    monkeypatch.setattr(task, "_select_cached_display_command", lambda _now: None)
    monkeypatch.setattr(task, "_memory_watchdog_should_restart", lambda: False)
    monkeypatch.setattr(
        task,
        "_resource_sample",
        lambda: ResourceSample(available_mb=512, swap_percent=0),
        raising=False,
    )

    task._schedule_if_due()
    entries = []
    while (entry := task.refresh_queue.take(timeout=0)) is not None:
        entries.append(entry)

    assert len(entries) == 1
    assert entries[0].command.kind is CommandKind.CACHE_REFRESH
    assert entries[0].command.intent is RefreshIntent.DATA_REFRESH


def test_due_refresh_for_rotation_instance_does_not_block_cached_display(
    monkeypatch,
):
    tmp_path = make_test_dir("independent-display-and-refresh")
    current_dt = datetime(2026, 7, 11, 12, 0, tzinfo=timezone.utc)
    playlist = _runtime_playlist(
        _runtime_plugin_data(
            "one",
            "One",
            latest_refresh_time=(current_dt - timedelta(hours=2)).isoformat(),
            interval=60,
        )
    )
    task, device_config, clock = _make_runtime_task(
        tmp_path,
        playlists=[playlist],
        cycle_seconds=60,
    )
    device_config.config.update({"theme_mode": "day", "active_theme": "day"})
    device_config.refresh_info.refresh_time = (
        current_dt - timedelta(minutes=2)
    ).isoformat()
    _write_runtime_cache(task, playlist.plugins[0])
    task.runtime_state.record_success(
        playlist.plugins[0].instance_uuid,
        (current_dt - timedelta(hours=2)).isoformat(),
        lane=RefreshLane.DATA,
    )
    monkeypatch.setattr(task, "_get_current_datetime", lambda: current_dt)
    monkeypatch.setattr(task, "_memory_watchdog_should_restart", lambda: False)
    monkeypatch.setattr(
        task,
        "_resource_sample",
        lambda: ResourceSample(available_mb=512, swap_percent=0),
        raising=False,
    )

    display_command = task._select_cached_display_command(current_dt)

    assert display_command is not None
    assert display_command.intent is RefreshIntent.DISPLAY_CACHE
    assert display_command.payload["display_cached_only"] is True

    submitted = task.refresh_queue.submit(display_command)
    task._process_queue_entry(task.refresh_queue.take(timeout=0))
    assert (
        task.refresh_queue.get_entry(submitted.id).job.status
        is JobStatus.SUCCEEDED
    )
    display_calls_after_rotation = len(task.display_manager.calls)

    refresh_command = task._select_independent_refresh_command(current_dt)

    assert refresh_command is not None
    assert refresh_command.intent is RefreshIntent.DATA_REFRESH
    assert refresh_command.instance_uuid == display_command.instance_uuid
    assert task.refresh_queue.take(timeout=0) is None

    provider_calls = []
    monkeypatch.setattr(
        "src.refresh_task.get_plugin_instance",
        lambda _config: FakePlugin(provider_calls),
    )
    refresh_job = task.refresh_queue.submit(refresh_command)
    task._process_queue_entry(task.refresh_queue.take(timeout=0))

    assert (
        task.refresh_queue.get_entry(refresh_job.id).job.status
        is JobStatus.SUCCEEDED
    )
    assert provider_calls == ["one"]
    assert len(task.display_manager.calls) == display_calls_after_rotation
    refreshed = task.runtime_state.snapshot().instances[
        display_command.instance_uuid
    ]
    assert refreshed.data.last_success_at == current_dt.isoformat()


def test_default_cached_display_uses_last_good_during_data_backoff(monkeypatch):
    current_dt = datetime(2026, 7, 11, 12, 0, tzinfo=timezone.utc)
    playlist = _runtime_playlist(
        _runtime_plugin_data(
            "stale",
            "Stale",
            latest_refresh_time=(current_dt - timedelta(hours=2)).isoformat(),
            interval=60,
        )
    )
    task, device_config, _clock = _make_runtime_task(
        make_test_dir("default-display-uses-last-good-during-backoff"),
        playlists=[playlist],
        cycle_seconds=60,
    )
    instance = playlist.plugins[0].snapshot()
    device_config.config.update({"theme_mode": "day", "active_theme": "day"})
    device_config.refresh_info.refresh_time = (
        current_dt - timedelta(minutes=2)
    ).isoformat()
    _write_runtime_cache(task, instance)
    task.runtime_state.record_success(
        instance.instance_uuid,
        (current_dt - timedelta(hours=2)).isoformat(),
        lane=RefreshLane.DATA,
    )
    task.runtime_state.record_failure(
        instance.instance_uuid,
        current_dt.isoformat(),
        RuntimeError("provider unavailable"),
        (current_dt + timedelta(minutes=10)).isoformat(),
        lane=RefreshLane.DATA,
    )
    monkeypatch.setattr(task, "_get_current_datetime", lambda: current_dt)

    selected = task._select_cached_display_command(current_dt)

    assert selected is not None
    assert selected.intent is RefreshIntent.DISPLAY_CACHE
    assert selected.instance_uuid == instance.instance_uuid


def test_failed_due_rotation_refresh_skips_stale_cache_during_backoff(
    monkeypatch,
):
    current_dt = datetime(2026, 7, 11, 12, 0, tzinfo=timezone.utc)
    playlist = _runtime_playlist(
        _runtime_plugin_data(
            "stale",
            "Stale",
            latest_refresh_time=(current_dt - timedelta(hours=2)).isoformat(),
            interval=60,
        ),
        _runtime_plugin_data(
            "fresh",
            "Fresh",
            latest_refresh_time=current_dt.isoformat(),
            interval=3600,
        ),
    )
    task, device_config, _clock = _make_runtime_task(
        make_test_dir("failed-due-rotation-refresh-skips-stale-cache"),
        playlists=[playlist],
        cycle_seconds=60,
    )
    stale, fresh = [instance.snapshot() for instance in playlist.plugins]
    playlist.plugin_rotation_pool = [stale.instance_uuid, fresh.instance_uuid]
    playlist.plugin_rotation_queue = [stale.instance_uuid, fresh.instance_uuid]
    playlist.plugin_rotation_recent_history = []
    playlist._plugin_rotation_reserved_key = None
    device_config.config.update(
        {
            "theme_mode": "day",
            "active_theme": "day",
            "display_triggered_refresh_enabled": True,
        }
    )
    device_config.refresh_info.refresh_time = (
        current_dt - timedelta(minutes=2)
    ).isoformat()
    for instance in (stale, fresh):
        _write_runtime_cache(task, instance)
    task.runtime_state.record_success(
        stale.instance_uuid,
        (current_dt - timedelta(hours=2)).isoformat(),
        lane=RefreshLane.DATA,
    )
    task.runtime_state.record_success(
        fresh.instance_uuid,
        current_dt.isoformat(),
        lane=RefreshLane.DATA,
    )
    monkeypatch.setattr(task, "_get_current_datetime", lambda: current_dt)
    monkeypatch.setattr(
        task,
        "_resource_sample",
        lambda: ResourceSample(available_mb=512, swap_percent=0),
    )

    refresh = task._select_cached_display_command(current_dt)
    assert refresh is not None
    assert refresh.intent is RefreshIntent.DATA_REFRESH
    assert refresh.instance_uuid == stale.instance_uuid

    task._record_command_failure(refresh, RuntimeError("provider unavailable"))
    selected = task._select_cached_display_command(
        current_dt + timedelta(seconds=1)
    )

    assert selected is not None
    assert selected.intent is RefreshIntent.DISPLAY_CACHE
    assert selected.instance_uuid == fresh.instance_uuid
    assert playlist.is_rotation_reservation_current(stale.instance_uuid) is False


def test_resource_deferral_keeps_last_good_rotation_cache_eligible():
    current_dt = datetime(2026, 7, 26, 12, 0, tzinfo=timezone.utc)
    task, _device_config, _clock = _make_runtime_task(
        make_test_dir("resource-deferral-keeps-rotation-cache"),
        playlists=[],
    )
    instance_uuid = "sports-dashboard-instance"
    candidate = object()
    task.runtime_state.record_failure(
        instance_uuid,
        (current_dt - timedelta(minutes=10)).isoformat(),
        RuntimeError("older provider failure"),
        (current_dt - timedelta(minutes=5)).isoformat(),
        lane=RefreshLane.DATA,
    )
    task.runtime_state.record_deferral(
        instance_uuid,
        current_dt.isoformat(),
        (current_dt + timedelta(minutes=1)).isoformat(),
        lane=RefreshLane.DATA,
    )

    eligible = task._rotation_cache_candidates_outside_refresh_backoff(
        {instance_uuid: candidate},
        current_dt + timedelta(seconds=1),
    )

    assert eligible == {instance_uuid: candidate}


def test_noncacheable_due_rotation_refresh_enters_backoff_without_stale_display(
    monkeypatch,
):
    current_dt = datetime(2026, 7, 11, 12, 0, tzinfo=timezone.utc)
    playlist = _runtime_playlist(
        _runtime_plugin_data(
            "stale",
            "Stale",
            latest_refresh_time=(current_dt - timedelta(hours=2)).isoformat(),
            interval=60,
        ),
        _runtime_plugin_data(
            "fresh",
            "Fresh",
            latest_refresh_time=current_dt.isoformat(),
            interval=3600,
        ),
    )
    task, device_config, _clock = _make_runtime_task(
        make_test_dir("noncacheable-due-rotation-refresh-backoff"),
        playlists=[playlist],
        cycle_seconds=60,
    )
    stale, fresh = [instance.snapshot() for instance in playlist.plugins]
    playlist.plugin_rotation_pool = [stale.instance_uuid, fresh.instance_uuid]
    playlist.plugin_rotation_queue = [stale.instance_uuid, fresh.instance_uuid]
    playlist.plugin_rotation_recent_history = []
    playlist._plugin_rotation_reserved_key = None
    device_config.config.update(
        {
            "theme_mode": "day",
            "active_theme": "day",
            "display_triggered_refresh_enabled": True,
        }
    )
    device_config.refresh_info.refresh_time = (
        current_dt - timedelta(minutes=2)
    ).isoformat()
    for instance in (stale, fresh):
        _write_runtime_cache(task, instance)
    task.runtime_state.record_success(
        stale.instance_uuid,
        (current_dt - timedelta(hours=2)).isoformat(),
        lane=RefreshLane.DATA,
    )
    task.runtime_state.record_success(
        fresh.instance_uuid,
        current_dt.isoformat(),
        lane=RefreshLane.DATA,
    )
    monkeypatch.setattr(task, "_get_current_datetime", lambda: current_dt)
    monkeypatch.setattr(
        task,
        "_resource_sample",
        lambda: ResourceSample(available_mb=512, swap_percent=0),
    )
    monkeypatch.setattr(
        "src.refresh_task.get_plugin_instance",
        lambda _config: NonCacheablePlugin([]),
    )

    refresh = task._select_cached_display_command(current_dt)
    assert refresh is not None
    assert refresh.intent is RefreshIntent.DATA_REFRESH
    task._execute_command(refresh)

    failed = task.runtime_state.snapshot().instances[stale.instance_uuid]
    assert failed.data.next_retry_at is not None
    selected = task._select_cached_display_command(
        current_dt + timedelta(seconds=1)
    )
    assert selected is not None
    assert selected.intent is RefreshIntent.DISPLAY_CACHE
    assert selected.instance_uuid == fresh.instance_uuid


def test_rebuilt_data_lanes_receive_first_attempt_under_continuous_due_load(monkeypatch):
    """A restored cache must not make its first DATA turn perpetually youngest."""
    now = datetime(2026, 9, 2, 12, 0, tzinfo=timezone.utc)
    clock = RuntimeClock(wall=now.timestamp())
    cold_intervals = {
        "steam_profile_dashboard": 300,
        "lol_info": 7200,
        "ticketmaster_events": 10800,
        "ai_ecosystem_pulse": 3600,
        "orbital_signal": 3600,
        "vehicle_status": 10800,
    }
    playlist = _runtime_playlist(*(
        [_runtime_plugin_data(f"warm-{i}", f"Warm {i}", interval=300)
         for i in range(22)]
        + [_runtime_plugin_data(key, key, interval=interval, latest_refresh_time=None)
           for key, interval in cold_intervals.items()]
    ))
    task, config, _clock = _make_runtime_task(
        make_test_dir("rebuilt-data-fairness"), playlists=[playlist], clock=clock,
    )
    config.config.update({"theme_mode": "day", "active_theme": "day"})
    monkeypatch.setattr(task, "_resource_sample", lambda: ResourceSample(512, 0))
    for plugin in playlist.plugins:
        instance = plugin.snapshot()
        _write_runtime_cache(task, instance)
        if instance.plugin_id.startswith("warm-"):
            task.runtime_state.record_success(
                instance.instance_uuid, (now - timedelta(hours=1)).isoformat(),
                lane=RefreshLane.DATA,
            )
    first_attempt = {}
    counts = {}
    for _ in range(100):
        current = datetime.fromtimestamp(clock.wall_time(), timezone.utc)
        command = task._select_independent_refresh_command(current)
        assert command is not None
        assert command.intent is RefreshIntent.DATA_REFRESH
        first_attempt.setdefault(command.plugin_id, clock.monotonic())
        counts[command.plugin_id] = counts.get(command.plugin_id, 0) + 1
        task.runtime_state.record_attempt(command.instance_uuid, current.isoformat(), lane=RefreshLane.DATA)
        task.runtime_state.record_success(command.instance_uuid, current.isoformat(), lane=RefreshLane.DATA)
        clock.advance(20)
    assert set(cold_intervals) <= first_attempt.keys()
    assert max(first_attempt[key] for key in cold_intervals) <= 28 * 20
    assert all(counts[f"warm-{i}"] >= 2 for i in range(22))


def test_soft_pressure_makes_spaced_fair_progress_across_ordinary_instances(
    monkeypatch,
):
    tmp_path = make_test_dir("independent-soft-fairness")
    clock = RuntimeClock()
    current_dt = datetime(2026, 7, 11, 12, 0, tzinfo=timezone.utc)
    playlist = _runtime_playlist(
        _runtime_plugin_data("one", "One", latest_refresh_time=None),
        _runtime_plugin_data("two", "Two", latest_refresh_time=None),
    )
    task, _device_config, _clock = _make_runtime_task(
        tmp_path,
        playlists=[playlist],
        clock=clock,
    )
    monkeypatch.setattr(
        task,
        "_resource_sample",
        lambda: ResourceSample(available_mb=100, swap_percent=0),
        raising=False,
    )

    first = task._select_independent_refresh_command(current_dt)
    immediate = task._select_independent_refresh_command(current_dt)
    task._record_runtime_attempt(first)
    clock.advance(60)
    second = task._select_independent_refresh_command(
        current_dt + timedelta(seconds=60)
    )

    assert first.intent is RefreshIntent.DATA_REFRESH
    assert immediate is None
    assert second.intent is RefreshIntent.DATA_REFRESH
    assert second.instance_uuid != first.instance_uuid


def test_soft_pressure_defers_sports_background_data_at_execution_until_healthy(
    monkeypatch,
):
    tmp_path = make_test_dir("sports-background-execution-pressure")
    current_dt = datetime(2026, 7, 26, 12, 0, tzinfo=timezone.utc)
    clock = RuntimeClock(wall=current_dt.timestamp())
    playlist = _runtime_playlist(
        _runtime_plugin_data(
            "sports_dashboard",
            "SportsDashboard",
            latest_refresh_time=None,
        )
    )
    calls = []
    task, device_config, _clock = _make_runtime_task(
        tmp_path,
        playlists=[playlist],
        clock=clock,
        sports_isolated_renderer=_fake_sports_isolated_renderer(calls),
    )
    device_config.config.update({"theme_mode": "day", "active_theme": "day"})
    resource_sample = {
        "value": ResourceSample(available_mb=512, swap_percent=0),
    }
    monkeypatch.setattr(
        task,
        "_resource_sample",
        lambda: resource_sample["value"],
    )
    monkeypatch.setattr(
        "src.refresh_task.get_plugin_instance",
        lambda _config: FakePlugin(calls),
    )

    command = task._select_independent_refresh_command(current_dt)
    assert command is not None
    assert command.source is CommandSource.BACKGROUND
    assert command.intent is RefreshIntent.DATA_REFRESH

    submitted = task.refresh_queue.submit(command)
    entry = task.refresh_queue.take(timeout=0)
    assert entry is not None
    resource_sample["value"] = ResourceSample(
        available_mb=113,
        swap_percent=50,
    )
    task._process_queue_entry(entry)

    deferred = task.refresh_queue.get_entry(submitted.id)
    assert deferred is not None
    assert deferred.job.status is JobStatus.RUNNING
    assert deferred.job.error_code is None
    assert calls == []
    state = task.runtime_state.snapshot().instances[command.instance_uuid].data
    assert state.last_attempt_at == current_dt.isoformat()
    assert state.next_retry_at == (current_dt + timedelta(seconds=60)).isoformat()
    assert state.last_failure_at is None
    assert state.last_error is None

    resource_sample["value"] = ResourceSample(
        available_mb=512,
        swap_percent=0,
    )
    assert (
        task._select_independent_refresh_command(
            current_dt + timedelta(seconds=1)
        )
        is None
    )

    clock.advance(1)
    task._process_queue_entry(deferred)
    completed = task.refresh_queue.get_entry(submitted.id)
    assert completed.job.status is JobStatus.SUCCEEDED
    assert calls == ["sports_dashboard"]


def test_ticketmaster_background_data_waits_for_memory_reserve_without_blocking_cached_display(
    monkeypatch,
):
    current_dt = datetime(2026, 7, 29, 12, 0, tzinfo=timezone.utc)
    clock = RuntimeClock(wall=current_dt.timestamp())
    playlist = _runtime_playlist(
        _runtime_plugin_data(
            "ticketmaster_events",
            "Ticketmaster",
            latest_refresh_time=None,
        )
    )
    task, _device_config, _clock = _make_runtime_task(
        make_test_dir("ticketmaster-background-memory-reserve"),
        playlists=[playlist],
        clock=clock,
    )
    instance = playlist.plugins[0].snapshot()
    resource_sample = {
        "value": ResourceSample(available_mb=100, swap_percent=0),
    }
    monkeypatch.setattr(
        task,
        "_resource_sample",
        lambda: resource_sample["value"],
    )
    executions = []
    monkeypatch.setattr(
        task,
        "_execute_command",
        lambda command: executions.append(command.id),
    )
    background = RefreshCommand.create(
        kind=CommandKind.CACHE_REFRESH,
        source=CommandSource.BACKGROUND,
        plugin_id="ticketmaster_events",
        instance_uuid=instance.instance_uuid,
        structural_generation=instance.structural_generation,
        settings_revision=instance.settings_revision,
        payload={"playlist_name": playlist.name},
        now_monotonic=clock.monotonic(),
        deadline_monotonic=clock.monotonic() + 180,
        priority=10,
        intent=RefreshIntent.DATA_REFRESH,
    )

    deferred = _queue_and_process(task, background)

    assert deferred.job.status is JobStatus.CANCELED
    assert deferred.job.error_code == "plugin_resource_reserve"
    state = task.runtime_state.snapshot().instances[background.instance_uuid].data
    assert state.next_retry_at == (current_dt + timedelta(seconds=60)).isoformat()
    assert state.last_failure_at is None
    assert executions == []

    cached_display = RefreshCommand.create(
        kind=CommandKind.DISPLAY,
        source=CommandSource.SCHEDULER,
        plugin_id="ticketmaster_events",
        instance_uuid=instance.instance_uuid,
        structural_generation=instance.structural_generation,
        settings_revision=instance.settings_revision,
        payload={
            "playlist_name": playlist.name,
            "display_cached_only": True,
        },
        now_monotonic=clock.monotonic(),
        deadline_monotonic=clock.monotonic() + 180,
        priority=100,
        intent=RefreshIntent.DISPLAY_CACHE,
    )

    displayed = _queue_and_process(task, cached_display)

    assert displayed.job.status is JobStatus.SUCCEEDED
    assert executions == [cached_display.id]

    playlist.plugin_rotation_queue = [instance.instance_uuid]
    playlist._plugin_rotation_reserved_key = instance.instance_uuid
    automatic_rotation = RefreshCommand.create(
        kind=CommandKind.CACHE_REFRESH,
        source=CommandSource.BACKGROUND,
        plugin_id="ticketmaster_events",
        instance_uuid=instance.instance_uuid,
        structural_generation=instance.structural_generation,
        settings_revision=instance.settings_revision,
        payload={
            "playlist_name": playlist.name,
            "automatic_rotation": True,
        },
        now_monotonic=clock.monotonic(),
        deadline_monotonic=clock.monotonic() + 180,
        priority=95,
        intent=RefreshIntent.DATA_REFRESH,
    )

    rotation_refresh = _queue_and_process(task, automatic_rotation)

    assert rotation_refresh.job.status is JobStatus.CANCELED
    assert rotation_refresh.job.error_code == "plugin_resource_reserve"
    assert executions == [cached_display.id]

    playlist._plugin_rotation_reserved_key = None
    resource_sample["value"] = ResourceSample(
        available_mb=115,
        swap_percent=0,
    )
    retry = RefreshCommand.create(
        kind=CommandKind.CACHE_REFRESH,
        source=CommandSource.BACKGROUND,
        plugin_id="ticketmaster_events",
        instance_uuid=instance.instance_uuid,
        structural_generation=instance.structural_generation,
        settings_revision=instance.settings_revision,
        payload={"playlist_name": playlist.name},
        now_monotonic=clock.monotonic(),
        deadline_monotonic=clock.monotonic() + 180,
        priority=10,
        intent=RefreshIntent.DATA_REFRESH,
    )

    completed = _queue_and_process(task, retry)

    assert completed.job.status is JobStatus.SUCCEEDED
    assert executions == [cached_display.id, retry.id]


def test_reserved_ticketmaster_starvation_concession_cannot_bypass_execution_reserve(
    monkeypatch,
):
    current_dt = datetime(2026, 7, 29, 12, 0, tzinfo=timezone.utc)
    clock = RuntimeClock(wall=current_dt.timestamp())
    playlist = _runtime_playlist(
        _runtime_plugin_data(
            "ticketmaster_events",
            "Ticketmaster",
            latest_refresh_time=None,
        )
    )
    task, _device_config, _clock = _make_runtime_task(
        make_test_dir("reserved-ticketmaster-starvation-concession"),
        playlists=[playlist],
        clock=clock,
    )
    instance = playlist.plugins[0].snapshot()
    playlist.plugin_rotation_queue = [instance.instance_uuid]
    playlist._plugin_rotation_reserved_key = instance.instance_uuid
    monkeypatch.setattr(
        task,
        "_resource_sample",
        lambda: ResourceSample(available_mb=100, swap_percent=0),
    )
    executions = []
    monkeypatch.setattr(
        task,
        "_execute_command",
        lambda command: executions.append(command.id),
    )

    def starvation_concession():
        return RefreshCommand.create(
            kind=CommandKind.CACHE_REFRESH,
            source=CommandSource.BACKGROUND,
            plugin_id=instance.plugin_id,
            instance_uuid=instance.instance_uuid,
            structural_generation=instance.structural_generation,
            settings_revision=instance.settings_revision,
            payload={"playlist_name": playlist.name},
            now_monotonic=clock.monotonic(),
            deadline_monotonic=clock.monotonic() + 180,
            priority=96,
            intent=RefreshIntent.DATA_REFRESH,
        )

    reserved = starvation_concession()
    reserved_result = _queue_and_process(task, reserved)

    assert reserved_result.job.status is JobStatus.CANCELED
    assert reserved_result.job.error_code == "plugin_resource_reserve"
    assert executions == []

    playlist._plugin_rotation_reserved_key = None
    ordinary = starvation_concession()
    ordinary_result = _queue_and_process(task, ordinary)

    assert ordinary_result.job.status is JobStatus.CANCELED
    assert ordinary_result.job.error_code == "plugin_resource_reserve"
    assert executions == []


def test_reserved_ticketmaster_is_deferred_before_queue_admission(monkeypatch):
    current_dt = datetime(2026, 7, 31, 12, 0, tzinfo=timezone.utc)
    clock = RuntimeClock(wall=current_dt.timestamp())
    playlist = _runtime_playlist(
        _runtime_plugin_data(
            "ticketmaster_events",
            "Ticketmaster",
            latest_refresh_time=None,
        )
    )
    task, _device_config, _clock = _make_runtime_task(
        make_test_dir("reserved-ticketmaster-scheduler-admission"),
        playlists=[playlist],
        clock=clock,
    )
    instance = playlist.plugins[0].snapshot()
    playlist.plugin_rotation_queue = [instance.instance_uuid]
    playlist._plugin_rotation_reserved_key = instance.instance_uuid
    monkeypatch.setattr(
        task,
        "_resource_sample",
        lambda: ResourceSample(available_mb=100, swap_percent=0),
    )

    command = task._select_independent_refresh_command(current_dt)

    assert command is None
    state = task.runtime_state.snapshot().instances[instance.instance_uuid].data
    assert state.next_retry_at == (current_dt + timedelta(seconds=60)).isoformat()


def test_stale_automatic_rotation_marker_cannot_bypass_ticketmaster_reserve(
    monkeypatch,
):
    current_dt = datetime(2026, 7, 29, 12, 0, tzinfo=timezone.utc)
    clock = RuntimeClock(wall=current_dt.timestamp())
    playlist = _runtime_playlist(
        _runtime_plugin_data(
            "ticketmaster_events",
            "Ticketmaster",
            latest_refresh_time=None,
        )
    )
    task, _device_config, _clock = _make_runtime_task(
        make_test_dir("stale-ticketmaster-automatic-rotation-marker"),
        playlists=[playlist],
        clock=clock,
    )
    instance = playlist.plugins[0].snapshot()
    playlist.plugin_rotation_queue = [instance.instance_uuid]
    playlist._plugin_rotation_reserved_key = instance.instance_uuid
    monkeypatch.setattr(
        task,
        "_resource_sample",
        lambda: ResourceSample(available_mb=100, swap_percent=0),
    )
    executions = []
    monkeypatch.setattr(
        task,
        "_execute_command",
        lambda command: executions.append(command.id),
    )
    command = RefreshCommand.create(
        kind=CommandKind.CACHE_REFRESH,
        source=CommandSource.BACKGROUND,
        plugin_id=instance.plugin_id,
        instance_uuid=instance.instance_uuid,
        structural_generation=instance.structural_generation,
        settings_revision=instance.settings_revision,
        payload={
            "playlist_name": playlist.name,
            "automatic_rotation": True,
        },
        now_monotonic=clock.monotonic(),
        deadline_monotonic=clock.monotonic() + 180,
        priority=95,
        intent=RefreshIntent.DATA_REFRESH,
    )
    submitted = task.refresh_queue.submit(command)
    entry = task.refresh_queue.take(timeout=0)

    playlist._plugin_rotation_reserved_key = None
    task._process_queue_entry(entry)

    completed = task.refresh_queue.get_entry(submitted.id)
    assert completed.job.status is JobStatus.CANCELED
    assert completed.job.error_code == "plugin_resource_reserve"
    assert executions == []


@pytest.mark.parametrize(
    ("available_mb", "swap_percent", "expected_status"),
    [
        (200, 80, JobStatus.CANCELED),
        (115, 75, JobStatus.CANCELED),
        (115, 74.9, JobStatus.SUCCEEDED),
    ],
)
def test_ticketmaster_background_start_margin_closes_memory_and_swap_races(
    monkeypatch,
    available_mb,
    swap_percent,
    expected_status,
):
    current_dt = datetime(2026, 7, 29, 12, 0, tzinfo=timezone.utc)
    clock = RuntimeClock(wall=current_dt.timestamp())
    playlist = _runtime_playlist(
        _runtime_plugin_data(
            "ticketmaster_events",
            "Ticketmaster",
            latest_refresh_time=None,
        )
    )
    task, _device_config, _clock = _make_runtime_task(
        make_test_dir(
            f"ticketmaster-margin-{available_mb}-{swap_percent}"
        ),
        playlists=[playlist],
        clock=clock,
    )
    instance = playlist.plugins[0].snapshot()
    monkeypatch.setattr(
        task,
        "_resource_sample",
        lambda: ResourceSample(
            available_mb=available_mb,
            swap_percent=swap_percent,
        ),
    )
    executions = []
    monkeypatch.setattr(
        task,
        "_execute_command",
        lambda command: executions.append(command.id),
    )
    command = RefreshCommand.create(
        kind=CommandKind.CACHE_REFRESH,
        source=CommandSource.BACKGROUND,
        plugin_id=instance.plugin_id,
        instance_uuid=instance.instance_uuid,
        structural_generation=instance.structural_generation,
        settings_revision=instance.settings_revision,
        payload={"playlist_name": playlist.name},
        now_monotonic=clock.monotonic(),
        deadline_monotonic=clock.monotonic() + 180,
        priority=10,
        intent=RefreshIntent.DATA_REFRESH,
    )

    completed = _queue_and_process(task, command)

    assert completed.job.status is expected_status
    assert executions == (
        [command.id] if expected_status is JobStatus.SUCCEEDED else []
    )


def test_ticketmaster_reserve_deferral_does_not_consume_soft_live_admission(
    monkeypatch,
):
    current_dt = datetime(2026, 7, 29, 12, 0, tzinfo=timezone.utc)
    clock = RuntimeClock(wall=current_dt.timestamp())
    ticketmaster_data = _runtime_plugin_data(
        "ticketmaster_events",
        "Ticketmaster",
        latest_refresh_time=None,
    )
    ticketmaster_data["instance_uuid"] = "00000000000000000000000000000001"
    sports_data = _runtime_plugin_data(
        "sports_dashboard",
        "SportsDashboard",
        latest_refresh_time=current_dt.isoformat(),
        interval=3600,
    )
    sports_data["instance_uuid"] = "11111111111111111111111111111111"
    playlist = _runtime_playlist(ticketmaster_data, sports_data)
    task, device_config, _clock = _make_runtime_task(
        make_test_dir("ticketmaster-reserve-live-fairness"),
        playlists=[playlist],
        clock=clock,
    )
    device_config.config.update({"theme_mode": "day", "active_theme": "day"})
    ticketmaster, sports = [
        instance.snapshot()
        for instance in playlist.plugins
    ]
    _write_runtime_cache(task, sports)
    task.runtime_state.record_success(
        sports.instance_uuid,
        current_dt.isoformat(),
        lane=RefreshLane.DATA,
    )
    monkeypatch.setattr(
        task,
        "_resource_sample",
        lambda: ResourceSample(available_mb=100, swap_percent=0),
    )
    live_due = {"value": False}
    monkeypatch.setattr(
        task,
        "_live_due_candidates",
        lambda *_args, **_kwargs: (
            [
                DueCandidate(
                    instance=sports,
                    lane=RefreshLane.LIVE,
                    due_since=current_dt + timedelta(seconds=1),
                    reason=DueReason.LIVE,
                    last_attempt_at=None,
                    requires_displayed_instance=False,
                )
            ]
            if live_due["value"]
            else []
        ),
    )

    first = task._select_independent_refresh_command(current_dt)

    assert first is None
    ticket_state = task.runtime_state.snapshot().instances[
        ticketmaster.instance_uuid
    ].data
    assert ticket_state.next_retry_at == (
        current_dt + timedelta(seconds=60)
    ).isoformat()

    live_due["value"] = True
    clock.advance(1)
    second = task._select_independent_refresh_command(
        current_dt + timedelta(seconds=1)
    )

    assert second is not None
    assert second.instance_uuid == sports.instance_uuid
    assert second.intent is RefreshIntent.LIVE_REFRESH


def test_starved_ticketmaster_reserves_bounded_window_then_runs_when_margin_recovers(
    monkeypatch,
):
    current_dt = datetime(2026, 7, 31, 12, 0, tzinfo=timezone.utc)
    clock = RuntimeClock(wall=current_dt.timestamp())
    ticketmaster_data = _runtime_plugin_data(
        "ticketmaster_events",
        "Ticketmaster",
        interval=3 * 60 * 60,
    )
    ticketmaster_data["instance_uuid"] = "00000000000000000000000000000001"
    ordinary_data = _runtime_plugin_data(
        "ordinary",
        "Ordinary",
        interval=60,
    )
    ordinary_data["instance_uuid"] = "11111111111111111111111111111111"
    playlist = _runtime_playlist(ticketmaster_data, ordinary_data)
    task, device_config, _clock = _make_runtime_task(
        make_test_dir("ticketmaster-starvation-window-recovers"),
        playlists=[playlist],
        clock=clock,
    )
    device_config.config.update(
        {
            "theme_mode": "day",
            "active_theme": "day",
            "ticketmaster_liveness_starvation_seconds": 300,
            "ticketmaster_liveness_window_seconds": 60,
            "ticketmaster_liveness_cooldown_seconds": 300,
        }
    )
    ticketmaster, ordinary = [
        instance.snapshot()
        for instance in playlist.plugins
    ]
    for instance in (ticketmaster, ordinary):
        _write_runtime_cache(task, instance)
    task.runtime_state.record_success(
        ticketmaster.instance_uuid,
        (current_dt - timedelta(hours=20)).isoformat(),
        lane=RefreshLane.DATA,
    )
    task.runtime_state.record_success(
        ordinary.instance_uuid,
        (current_dt - timedelta(minutes=20)).isoformat(),
        lane=RefreshLane.DATA,
    )
    resource_sample = {
        "value": ResourceSample(available_mb=110, swap_percent=50),
    }
    monkeypatch.setattr(
        task,
        "_resource_sample",
        lambda: resource_sample["value"],
    )

    assert task._select_independent_refresh_command(current_dt) is None
    assert task._ticketmaster_liveness_window is not None
    assert (
        task._ticketmaster_liveness_window.instance_uuid
        == ticketmaster.instance_uuid
    )

    resource_sample["value"] = ResourceSample(
        available_mb=120,
        swap_percent=50,
    )
    clock.advance(1)
    retry = task._select_independent_refresh_command(
        current_dt + timedelta(seconds=1)
    )

    assert retry is not None
    assert retry.plugin_id == "ticketmaster_events"
    assert retry.instance_uuid == ticketmaster.instance_uuid
    assert retry.intent is RefreshIntent.DATA_REFRESH
    assert retry.priority == 97


def test_ticketmaster_liveness_window_expires_before_ordinary_refresh_is_starved(
    monkeypatch,
):
    current_dt = datetime(2026, 7, 31, 12, 0, tzinfo=timezone.utc)
    clock = RuntimeClock(wall=current_dt.timestamp())
    ticketmaster_data = _runtime_plugin_data(
        "ticketmaster_events",
        "Ticketmaster",
        interval=3 * 60 * 60,
    )
    ticketmaster_data["instance_uuid"] = "00000000000000000000000000000001"
    ordinary_data = _runtime_plugin_data(
        "ordinary",
        "Ordinary",
        interval=60,
    )
    ordinary_data["instance_uuid"] = "11111111111111111111111111111111"
    playlist = _runtime_playlist(ticketmaster_data, ordinary_data)
    task, device_config, _clock = _make_runtime_task(
        make_test_dir("ticketmaster-starvation-window-bounded"),
        playlists=[playlist],
        clock=clock,
    )
    device_config.config.update(
        {
            "theme_mode": "day",
            "active_theme": "day",
            "ticketmaster_liveness_starvation_seconds": 300,
            "ticketmaster_liveness_window_seconds": 60,
            "ticketmaster_liveness_cooldown_seconds": 300,
        }
    )
    ticketmaster, ordinary = [
        instance.snapshot()
        for instance in playlist.plugins
    ]
    for instance in (ticketmaster, ordinary):
        _write_runtime_cache(task, instance)
    task.runtime_state.record_success(
        ticketmaster.instance_uuid,
        (current_dt - timedelta(hours=20)).isoformat(),
        lane=RefreshLane.DATA,
    )
    task.runtime_state.record_success(
        ordinary.instance_uuid,
        (current_dt - timedelta(minutes=20)).isoformat(),
        lane=RefreshLane.DATA,
    )
    monkeypatch.setattr(
        task,
        "_resource_sample",
        lambda: ResourceSample(available_mb=110, swap_percent=50),
    )

    assert task._select_independent_refresh_command(current_dt) is None
    assert task._ticketmaster_liveness_window is not None

    clock.advance(61)
    ordinary_refresh = task._select_independent_refresh_command(
        current_dt + timedelta(seconds=61)
    )

    assert ordinary_refresh is not None
    assert ordinary_refresh.instance_uuid == ordinary.instance_uuid
    assert task._ticketmaster_liveness_window is None
    assert (
        task._ticketmaster_liveness_cooldown_until_monotonic
        > clock.monotonic()
    )


def test_missing_ticketmaster_cache_uses_stable_liveness_anchor(monkeypatch):
    current_dt = datetime(2026, 7, 31, 12, 0, tzinfo=timezone.utc)
    clock = RuntimeClock(wall=current_dt.timestamp())
    ticketmaster_data = _runtime_plugin_data(
        "ticketmaster_events",
        "Ticketmaster",
        interval=3 * 60 * 60,
        latest_refresh_time=None,
    )
    playlist = _runtime_playlist(ticketmaster_data)
    task, device_config, _clock = _make_runtime_task(
        make_test_dir("ticketmaster-bootstrap-liveness-anchor"),
        playlists=[playlist],
        clock=clock,
    )
    device_config.config.update(
        {
            "theme_mode": "day",
            "active_theme": "day",
            "ticketmaster_liveness_starvation_seconds": 300,
            "ticketmaster_liveness_window_seconds": 30,
        }
    )
    ticketmaster = playlist.plugins[0].snapshot()
    monkeypatch.setattr(
        task,
        "_resource_sample",
        lambda: ResourceSample(available_mb=110, swap_percent=50),
    )

    for elapsed_seconds in (0, 61, 122, 183, 244):
        if elapsed_seconds:
            clock.advance(61)
        assert (
            task._select_independent_refresh_command(
                current_dt + timedelta(seconds=elapsed_seconds)
            )
            is None
        )
        assert task._ticketmaster_liveness_window is None

    clock.advance(61)
    assert task._select_independent_refresh_command(
        current_dt + timedelta(seconds=305)
    ) is None
    assert task._ticketmaster_liveness_window is not None
    assert (
        task._ticketmaster_liveness_window.instance_uuid
        == ticketmaster.instance_uuid
    )


def test_completed_ticketmaster_window_yields_before_another_window(monkeypatch):
    current_dt = datetime(2026, 7, 31, 12, 0, tzinfo=timezone.utc)
    clock = RuntimeClock(wall=current_dt.timestamp())
    first_data = _runtime_plugin_data(
        "ticketmaster_events",
        "Ticketmaster First",
        interval=3 * 60 * 60,
    )
    first_data["instance_uuid"] = "00000000000000000000000000000001"
    second_data = _runtime_plugin_data(
        "ticketmaster_events",
        "Ticketmaster Second",
        interval=3 * 60 * 60,
    )
    second_data["instance_uuid"] = "00000000000000000000000000000002"
    ordinary_data = _runtime_plugin_data("ordinary", "Ordinary", interval=60)
    ordinary_data["instance_uuid"] = "11111111111111111111111111111111"
    playlist = _runtime_playlist(first_data, second_data, ordinary_data)
    task, device_config, _clock = _make_runtime_task(
        make_test_dir("ticketmaster-window-yields-between-instances"),
        playlists=[playlist],
        clock=clock,
    )
    device_config.config.update(
        {
            "theme_mode": "day",
            "active_theme": "day",
            "ticketmaster_liveness_starvation_seconds": 300,
            "ticketmaster_liveness_window_seconds": 60,
        }
    )
    first, second, ordinary = [
        instance.snapshot()
        for instance in playlist.plugins
    ]
    for instance in (first, second, ordinary):
        _write_runtime_cache(task, instance)
        task.runtime_state.record_success(
            instance.instance_uuid,
            (current_dt - timedelta(hours=20)).isoformat(),
            lane=RefreshLane.DATA,
        )
    monkeypatch.setattr(
        task,
        "_resource_sample",
        lambda: ResourceSample(available_mb=110, swap_percent=50),
    )

    assert task._select_independent_refresh_command(current_dt) is None
    assert task._ticketmaster_liveness_window is not None
    assert task._ticketmaster_liveness_window.instance_uuid == first.instance_uuid

    clock.advance(1)
    task.runtime_state.record_success(
        first.instance_uuid,
        (current_dt + timedelta(seconds=1)).isoformat(),
        lane=RefreshLane.DATA,
    )
    next_command = task._select_independent_refresh_command(
        current_dt + timedelta(seconds=1)
    )

    assert next_command is not None
    assert next_command.instance_uuid == ordinary.instance_uuid
    assert task._ticketmaster_liveness_window is None


def test_stale_ticketmaster_command_cannot_defer_current_instance_revision(
    monkeypatch,
):
    current_dt = datetime(2026, 7, 29, 12, 0, tzinfo=timezone.utc)
    clock = RuntimeClock(wall=current_dt.timestamp())
    playlist = _runtime_playlist(
        _runtime_plugin_data(
            "ticketmaster_events",
            "Ticketmaster",
            latest_refresh_time=None,
        )
    )
    task, _device_config, _clock = _make_runtime_task(
        make_test_dir("stale-ticketmaster-reserve"),
        playlists=[playlist],
        clock=clock,
    )
    instance = playlist.plugins[0].snapshot()
    monkeypatch.setattr(
        task,
        "_resource_sample",
        lambda: ResourceSample(available_mb=100, swap_percent=0),
    )
    stale = RefreshCommand.create(
        kind=CommandKind.CACHE_REFRESH,
        source=CommandSource.BACKGROUND,
        plugin_id="ticketmaster_events",
        instance_uuid=instance.instance_uuid,
        structural_generation=instance.structural_generation,
        settings_revision=instance.settings_revision + 1,
        payload={"playlist_name": playlist.name, "require_active": True},
        now_monotonic=clock.monotonic(),
        deadline_monotonic=clock.monotonic() + 180,
        priority=10,
        intent=RefreshIntent.DATA_REFRESH,
    )

    completed = _queue_and_process(task, stale)

    assert completed.job.status is JobStatus.CANCELED
    assert completed.job.error_code == "stale_selection"
    runtime = task.runtime_state.snapshot().instances.get(instance.instance_uuid)
    assert runtime is None or runtime.data.next_retry_at is None


def test_background_sports_isolated_path_bypasses_legacy_full_render_margin(
    monkeypatch,
):
    tmp_path = make_test_dir("sports-background-heavyweight-margin")
    current_dt = datetime(2026, 7, 26, 12, 0, tzinfo=timezone.utc)
    clock = RuntimeClock(wall=current_dt.timestamp())
    playlist = _runtime_playlist(
        _runtime_plugin_data(
            "sports_dashboard",
            "SportsDashboard",
            latest_refresh_time=None,
        )
    )
    calls = []
    task, device_config, _clock = _make_runtime_task(
        tmp_path,
        playlists=[playlist],
        clock=clock,
        sports_isolated_renderer=_fake_sports_isolated_renderer(calls),
    )
    device_config.config.update({"theme_mode": "day", "active_theme": "day"})
    monkeypatch.setattr(
        task,
        "_resource_sample",
        lambda: ResourceSample(available_mb=300, swap_percent=0),
    )
    monkeypatch.setattr(
        "src.refresh_task.get_plugin_instance",
        lambda _config: FakePlugin(calls),
    )

    command = task._select_independent_refresh_command(current_dt)
    assert command is not None
    completed = _queue_and_process(task, command)

    assert task._resource_tier.value == "healthy"
    assert completed.job.status is JobStatus.SUCCEEDED
    assert calls == ["sports_dashboard"]


def test_background_sports_data_uses_isolated_renderer_below_legacy_margin(
    monkeypatch,
):
    tmp_path = make_test_dir("sports-background-isolated-renderer")
    current_dt = datetime(2026, 7, 26, 12, 0, tzinfo=timezone.utc)
    clock = RuntimeClock(wall=current_dt.timestamp())
    playlist = _runtime_playlist(
        _runtime_plugin_data(
            "sports_dashboard",
            "SportsDashboard",
            latest_refresh_time=None,
        )
    )
    isolated_calls = []

    def isolated_renderer(**kwargs):
        isolated_calls.append(kwargs)
        return Image.new("RGB", (1, 1), "white")

    task, device_config, _clock = _make_runtime_task(
        tmp_path,
        playlists=[playlist],
        clock=clock,
        sports_isolated_renderer=isolated_renderer,
    )
    device_config.config.update({"theme_mode": "day", "active_theme": "day"})
    monkeypatch.setattr(
        task,
        "_resource_sample",
        lambda: ResourceSample(available_mb=300, swap_percent=0),
    )
    monkeypatch.setattr(
        "src.refresh_task.get_plugin_instance",
        lambda _config: FakePlugin([]),
    )

    command = task._select_independent_refresh_command(current_dt)
    assert command is not None
    completed = _queue_and_process(task, command)

    assert completed.job.status is JobStatus.SUCCEEDED
    assert len(isolated_calls) == 1
    assert isolated_calls[0]["settings"]["id"] == "sports_dashboard"
    assert isolated_calls[0]["instance_identity"] == InstanceIdentity(
        command.instance_uuid,
        command.structural_generation,
        command.settings_revision,
    )
    assert isolated_calls[0]["identity_validator"](
        isolated_calls[0]["instance_identity"]
    )


def test_sports_region_checkpoints_continue_the_same_forced_job(monkeypatch):
    tmp_path = make_test_dir("sports-region-checkpoint-pending")
    current_dt = datetime(2026, 8, 11, 12, 0, tzinfo=timezone.utc)
    clock = RuntimeClock(wall=current_dt.timestamp())
    playlist = _runtime_playlist(
        _runtime_plugin_data(
            "sports_dashboard",
            "SportsDashboard",
            latest_refresh_time=None,
        )
    )

    calls = []

    def checkpointing_renderer(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            raise SportsIsolatedCheckpointPending(
                fingerprint="a" * 64,
                completed_regions=("esports",),
                next_region="football",
            )
        if len(calls) == 2:
            raise SportsIsolatedCheckpointPending(
                fingerprint="a" * 64,
                completed_regions=("esports", "football"),
                next_region="lower",
            )
        return Image.new("RGB", (1, 1), "white")

    task, device_config, _clock = _make_runtime_task(
        tmp_path,
        playlists=[playlist],
        clock=clock,
        sports_isolated_renderer=checkpointing_renderer,
    )
    device_config.config.update({"theme_mode": "day", "active_theme": "day"})
    monkeypatch.setattr(
        task,
        "_resource_sample",
        lambda: ResourceSample(available_mb=300, swap_percent=0),
    )

    instance = playlist.plugins[0].snapshot()
    command = task._playlist_command(
        playlist.name,
        instance,
        source=CommandSource.MANUAL,
        intent=RefreshIntent.DATA_REFRESH,
        force=True,
        display_cached_only=False,
        priority=100,
        kind=CommandKind.CACHE_REFRESH,
        current_dt=current_dt,
    )
    submitted = task.refresh_queue.submit(command)
    for expected_calls in (1, 2):
        entry = task.refresh_queue.take(timeout=0)
        assert entry is not None
        assert entry.job.id == submitted.id
        task._process_queue_entry(entry)
        checkpointed = task.refresh_queue.get_entry(submitted.id)
        assert checkpointed.job.status is JobStatus.QUEUED
        assert len(calls) == expected_calls

    entry = task.refresh_queue.take(timeout=0)
    assert entry is not None
    assert entry.job.id == submitted.id
    task._process_queue_entry(entry)
    completed = task.refresh_queue.get_entry(submitted.id)

    assert completed.job.status is JobStatus.SUCCEEDED
    assert completed.job.error_code is None
    assert len(calls) == 3
    assert all(call["settings"]["forceRefresh"] is True for call in calls)
    assert all(call["settings"]["force_refresh"] is True for call in calls)
    assert all(call["attempt_token"] == command.id for call in calls)
    lane = task.runtime_state.snapshot().instances[command.instance_uuid].data
    assert lane.last_failure_at is None
    assert lane.next_retry_at is None


def test_background_sports_checkpoint_returns_the_permit_to_manual_work(monkeypatch):
    tmp_path = make_test_dir("sports-region-checkpoint-interleave")
    current_dt = datetime(2026, 8, 11, 12, 0, tzinfo=timezone.utc)
    clock = RuntimeClock(wall=current_dt.timestamp())
    playlist = _runtime_playlist(
        _runtime_plugin_data(
            "sports_dashboard",
            "SportsDashboard",
            latest_refresh_time=None,
        )
    )
    calls = []

    def checkpointing_renderer(**kwargs):
        calls.append(kwargs)
        if len(calls) < 3:
            completed = ("esports",) if len(calls) == 1 else ("esports", "football")
            raise SportsIsolatedCheckpointPending(
                fingerprint="b" * 64,
                completed_regions=completed,
                next_region="football" if len(calls) == 1 else "lower",
            )
        return Image.new("RGB", (1, 1), "white")

    task, device_config, _clock = _make_runtime_task(
        tmp_path,
        playlists=[playlist],
        clock=clock,
        sports_isolated_renderer=checkpointing_renderer,
    )
    device_config.config.update({"theme_mode": "day", "active_theme": "day"})
    monkeypatch.setattr(
        task,
        "_resource_sample",
        lambda: ResourceSample(available_mb=300, swap_percent=0),
    )
    sports = task._select_independent_refresh_command(current_dt)
    assert sports is not None
    sports_job = task.refresh_queue.submit(sports)
    first = task.refresh_queue.take(timeout=0)
    assert first is not None
    task._process_queue_entry(first)
    assert task.refresh_queue.get_entry(sports_job.id).job.status is JobStatus.QUEUED

    urgent = RefreshCommand.create(
        kind=CommandKind.DISPLAY,
        source=CommandSource.MANUAL,
        plugin_id="audit_global",
        instance_uuid=None,
        payload={"refresh_type": "Manual Update", "settings": {}},
        now_monotonic=clock.monotonic(),
        deadline_monotonic=clock.monotonic() + 60,
        priority=100,
        intent=RefreshIntent.MANUAL_RENDER,
    )
    urgent_job = task.refresh_queue.submit(urgent)
    interleaved = task.refresh_queue.take(timeout=0)
    assert interleaved is not None
    assert interleaved.job.id == urgent_job.id
    task.refresh_queue.finish(interleaved.job.id, JobStatus.SUCCEEDED)

    for expected_status in (JobStatus.QUEUED, JobStatus.SUCCEEDED):
        resumed = task.refresh_queue.take(timeout=0)
        assert resumed is not None
        assert resumed.job.id == sports_job.id
        task._process_queue_entry(resumed)
        assert task.refresh_queue.get_entry(sports_job.id).job.status is expected_status

    assert len(calls) == 3


def test_manual_sports_data_refresh_uses_the_same_isolated_renderer(monkeypatch):
    tmp_path = make_test_dir("sports-manual-isolated-renderer")
    current_dt = datetime(2026, 7, 26, 12, 0, tzinfo=timezone.utc)
    clock = RuntimeClock(wall=current_dt.timestamp())
    playlist = _runtime_playlist(
        _runtime_plugin_data(
            "sports_dashboard",
            "SportsDashboard",
            latest_refresh_time=None,
        )
    )
    isolated_calls = []
    task, device_config, _clock = _make_runtime_task(
        tmp_path,
        playlists=[playlist],
        clock=clock,
        sports_isolated_renderer=lambda **kwargs: (
            isolated_calls.append(kwargs)
            or Image.new("RGB", (1, 1), "white")
        ),
    )
    device_config.config.update({"theme_mode": "day", "active_theme": "day"})
    monkeypatch.setattr(
        task,
        "_resource_sample",
        lambda: ResourceSample(available_mb=300, swap_percent=0),
    )
    instance = playlist.plugins[0].snapshot()
    command = task._playlist_command(
        playlist.name,
        instance,
        source=CommandSource.MANUAL,
        intent=RefreshIntent.DATA_REFRESH,
        force=True,
        display_cached_only=False,
        priority=100,
        kind=CommandKind.CACHE_REFRESH,
        current_dt=current_dt,
        require_active=False,
    )

    completed = _queue_and_process(task, command)

    assert completed.job.status is JobStatus.SUCCEEDED
    assert len(isolated_calls) == 1
    assert isolated_calls[0]["settings"]["forceRefresh"] is True
    assert isolated_calls[0]["settings"]["force_refresh"] is True


def test_heavyweight_margin_allows_sports_with_ample_headroom(monkeypatch):
    tmp_path = make_test_dir("sports-background-heavyweight-headroom")
    current_dt = datetime(2026, 7, 26, 12, 0, tzinfo=timezone.utc)
    clock = RuntimeClock(wall=current_dt.timestamp())
    playlist = _runtime_playlist(
        _runtime_plugin_data(
            "sports_dashboard",
            "SportsDashboard",
            latest_refresh_time=None,
        )
    )
    calls = []
    task, device_config, _clock = _make_runtime_task(
        tmp_path,
        playlists=[playlist],
        clock=clock,
        sports_isolated_renderer=_fake_sports_isolated_renderer(calls),
    )
    device_config.config.update({"theme_mode": "day", "active_theme": "day"})
    monkeypatch.setattr(
        task,
        "_resource_sample",
        lambda: ResourceSample(available_mb=512, swap_percent=0),
    )
    monkeypatch.setattr(
        "src.refresh_task.get_plugin_instance",
        lambda _config: FakePlugin(calls),
    )

    command = task._select_independent_refresh_command(current_dt)
    assert command is not None
    completed = _queue_and_process(task, command)

    assert completed.job.status is JobStatus.SUCCEEDED
    assert calls == ["sports_dashboard"]


def test_sports_isolated_start_margin_is_configurable(monkeypatch):
    tmp_path = make_test_dir("sports-background-heavyweight-config")
    current_dt = datetime(2026, 7, 26, 12, 0, tzinfo=timezone.utc)
    clock = RuntimeClock(wall=current_dt.timestamp())
    playlist = _runtime_playlist(
        _runtime_plugin_data(
            "sports_dashboard",
            "SportsDashboard",
            latest_refresh_time=None,
        )
    )
    calls = []
    task, device_config, _clock = _make_runtime_task(
        tmp_path,
        playlists=[playlist],
        clock=clock,
        sports_isolated_renderer=_fake_sports_isolated_renderer(calls),
    )
    device_config.config.update(
        {
            "theme_mode": "day",
            "active_theme": "day",
            "sports_isolated_start_min_available_mb": 180,
            "sports_isolated_start_max_swap_percent": 50,
        }
    )
    monkeypatch.setattr(
        task,
        "_resource_sample",
        lambda: ResourceSample(available_mb=188.7, swap_percent=43.1),
    )
    monkeypatch.setattr(
        "src.refresh_task.get_plugin_instance",
        lambda _config: FakePlugin(calls),
    )

    command = task._select_independent_refresh_command(current_dt)
    assert command is not None
    completed = _queue_and_process(task, command)

    assert completed.job.status is JobStatus.SUCCEEDED
    assert calls == ["sports_dashboard"]


def test_sports_isolated_abort_defaults_preserve_earlyoom_headroom():
    task, _device_config, _clock = _make_runtime_task(
        make_test_dir("sports-isolated-abort-defaults"),
        playlists=[],
    )

    assert task._sports_isolated_abort_thresholds() == (70, 75)


@pytest.mark.parametrize("unsafe_value", [True, 10**1000])
def test_heavyweight_renderer_invalid_minimum_fails_safe(unsafe_value):
    task, device_config, _clock = _make_runtime_task(
        make_test_dir("sports-heavyweight-invalid-minimum"),
        playlists=[],
    )
    device_config.config["heavyweight_renderer_min_available_mb"] = unsafe_value

    margin_available, required_available_mb, max_swap_percent = (
        task._heavyweight_renderer_resource_margin(
            ResourceSample(available_mb=300, swap_percent=0)
        )
    )

    assert margin_available is False
    assert required_available_mb == 384
    assert max_swap_percent == 30


def test_heavyweight_renderer_margin_blocks_manual_sports_render(monkeypatch):
    tmp_path = make_test_dir("sports-manual-heavyweight-margin")
    current_dt = datetime(2026, 7, 26, 12, 0, tzinfo=timezone.utc)
    clock = RuntimeClock(wall=current_dt.timestamp())
    playlist = _runtime_playlist(
        _runtime_plugin_data(
            "sports_dashboard",
            "SportsDashboard",
            latest_refresh_time=current_dt.isoformat(),
        )
    )
    task, device_config, _clock = _make_runtime_task(
        tmp_path,
        playlists=[playlist],
        clock=clock,
    )
    device_config.config.update({"theme_mode": "day", "active_theme": "day"})
    instance = playlist.plugins[0].snapshot()
    calls = []
    monkeypatch.setattr(
        task,
        "_resource_sample",
        lambda: ResourceSample(available_mb=188.7, swap_percent=43.1),
    )
    monkeypatch.setattr(
        "src.refresh_task.get_plugin_instance",
        lambda _config: FakePlugin(calls),
    )
    command = task._playlist_command(
        playlist.name,
        instance,
        source=CommandSource.MANUAL,
        intent=RefreshIntent.MANUAL_RENDER,
        force=True,
        display_cached_only=False,
        priority=100,
        current_dt=current_dt,
    )

    completed = _queue_and_process(task, command)

    assert completed.job.status is JobStatus.CANCELED
    assert completed.job.error_code == "heavyweight_renderer_margin"
    assert calls == []
    assert instance.instance_uuid not in task.runtime_state.snapshot().instances


def test_live_sports_refresh_uses_isolated_renderer_below_legacy_margin(
    monkeypatch,
):
    tmp_path = make_test_dir("sports-live-heavyweight-margin")
    current_dt = datetime(2026, 7, 26, 12, 0, tzinfo=timezone.utc)
    clock = RuntimeClock(wall=current_dt.timestamp())
    playlist = _runtime_playlist(
        _runtime_plugin_data(
            "sports_dashboard",
            "SportsDashboard",
            latest_refresh_time=current_dt.isoformat(),
        )
    )
    isolated_calls = []
    task, device_config, _clock = _make_runtime_task(
        tmp_path,
        playlists=[playlist],
        clock=clock,
        sports_isolated_renderer=lambda **kwargs: (
            isolated_calls.append(kwargs)
            or Image.new("RGB", (1, 1), "white")
        ),
    )
    device_config.config.update({"theme_mode": "day", "active_theme": "day"})
    instance = playlist.plugins[0].snapshot()
    monkeypatch.setattr(
        task,
        "_resource_sample",
        lambda: ResourceSample(available_mb=188.7, swap_percent=43.1),
    )
    command = task._playlist_command(
        playlist.name,
        instance,
        source=CommandSource.LIVE,
        intent=RefreshIntent.LIVE_REFRESH,
        display_cached_only=False,
        kind=CommandKind.CACHE_REFRESH,
        current_dt=current_dt,
        background_live_refresh=True,
    )

    completed = _queue_and_process(task, command)

    assert completed.job.status is JobStatus.SUCCEEDED
    assert len(isolated_calls) == 1
    state = task.runtime_state.snapshot().instances[instance.instance_uuid]
    assert state.live.last_attempt_at == current_dt.isoformat()
    assert state.live.last_success_at is not None
    assert state.live.next_retry_at is None
    assert state.data.last_attempt_at is None
    assert state.data.next_retry_at is None


@pytest.mark.parametrize(
    ("intent", "expected_lane"),
    [
        (RefreshIntent.DATA_REFRESH, RefreshLane.DATA),
        (RefreshIntent.LIVE_REFRESH, RefreshLane.LIVE),
        (RefreshIntent.THEME_REDRAW, RefreshLane.THEME),
        (RefreshIntent.PRESENTATION_REFRESH, RefreshLane.PRESENTATION),
        (RefreshIntent.THEME_CATCHUP, None),
        (RefreshIntent.MANUAL_RENDER, None),
    ],
)
def test_heavyweight_renderer_low_margin_defers_every_renderer_intent_and_lane(
    monkeypatch,
    intent,
    expected_lane,
):
    tmp_path = make_test_dir(f"sports-renderer-low-margin-{intent.value}")
    current_dt = datetime(2026, 7, 26, 12, 0, tzinfo=timezone.utc)
    clock = RuntimeClock(wall=current_dt.timestamp())
    task, _device_config, _clock = _make_runtime_task(
        tmp_path,
        playlists=[],
        clock=clock,
    )
    monkeypatch.setattr(
        task,
        "_resource_sample",
        lambda: ResourceSample(available_mb=188.7, swap_percent=43.1),
    )
    monkeypatch.setattr(
        task,
        "_execute_command",
        lambda _command: pytest.fail("low-margin renderer reached plugin work"),
    )
    command = RefreshCommand.create(
        kind=CommandKind.CACHE_REFRESH,
        source=CommandSource.MANUAL,
        plugin_id="sports_dashboard",
        instance_uuid="sports-low-margin",
        structural_generation=1,
        settings_revision=1,
        payload={},
        now_monotonic=clock.monotonic(),
        deadline_monotonic=clock.monotonic() + 60,
        intent=intent,
    )

    completed = _queue_and_process(task, command)

    assert completed.job.status is JobStatus.CANCELED
    assert completed.job.error_code == "heavyweight_renderer_margin"
    runtime_instance = task.runtime_state.snapshot().instances.get(
        command.instance_uuid
    )
    if expected_lane is None:
        assert runtime_instance is None
    else:
        lane_state = getattr(runtime_instance, expected_lane.value)
        assert lane_state.last_attempt_at == current_dt.isoformat()
        assert lane_state.next_retry_at == (
            current_dt + timedelta(seconds=60)
        ).isoformat()


def test_heavyweight_renderer_gate_excludes_display_cache_under_low_margin(
    monkeypatch,
):
    tmp_path = make_test_dir("sports-display-cache-low-margin")
    current_dt = datetime(2026, 7, 26, 12, 0, tzinfo=timezone.utc)
    clock = RuntimeClock(wall=current_dt.timestamp())
    playlist = _runtime_playlist(
        _runtime_plugin_data(
            "sports_dashboard",
            "SportsDashboard",
            latest_refresh_time=current_dt.isoformat(),
        )
    )
    task, device_config, _clock = _make_runtime_task(
        tmp_path,
        playlists=[playlist],
        clock=clock,
    )
    device_config.config.update({"theme_mode": "day", "active_theme": "day"})
    instance = playlist.plugins[0].snapshot()
    _write_runtime_cache(task, instance)
    monkeypatch.setattr(
        task,
        "_resource_sample",
        lambda: ResourceSample(available_mb=60, swap_percent=90),
    )
    monkeypatch.setattr(
        "src.refresh_task.get_plugin_instance",
        lambda _config: pytest.fail("DISPLAY_CACHE must not invoke the plugin"),
    )
    command = task._playlist_command(
        playlist.name,
        instance,
        source=CommandSource.SCHEDULER,
        intent=RefreshIntent.DISPLAY_CACHE,
        display_cached_only=True,
        current_dt=current_dt,
        cache_theme_mode=None,
    )

    completed = _queue_and_process(task, command)

    assert completed.job.status is JobStatus.SUCCEEDED
    assert len(task.display_manager.calls) == 1


@pytest.mark.parametrize(
    "intent",
    sorted(refresh_task_module._RENDERER_INTENTS, key=lambda value: value.value),
)
def test_heavyweight_renderer_gate_preserves_all_intents_with_headroom(
    monkeypatch,
    intent,
):
    tmp_path = make_test_dir(f"sports-renderer-headroom-{intent.value}")
    current_dt = datetime(2026, 7, 26, 12, 0, tzinfo=timezone.utc)
    clock = RuntimeClock(wall=current_dt.timestamp())
    task, _device_config, _clock = _make_runtime_task(
        tmp_path,
        playlists=[],
        clock=clock,
    )
    executed = []
    monkeypatch.setattr(
        task,
        "_resource_sample",
        lambda: ResourceSample(available_mb=512, swap_percent=0),
    )
    monkeypatch.setattr(task, "_execute_command", executed.append)
    command = RefreshCommand.create(
        kind=CommandKind.DISPLAY,
        source=CommandSource.MANUAL,
        plugin_id="sports_dashboard",
        instance_uuid="sports-headroom",
        structural_generation=1,
        settings_revision=1,
        payload={},
        now_monotonic=clock.monotonic(),
        deadline_monotonic=clock.monotonic() + 60,
        intent=intent,
    )

    completed = _queue_and_process(task, command)

    assert completed.job.status is JobStatus.SUCCEEDED
    assert executed == [command]


def test_starved_sports_reserves_quiet_window_then_runs_when_margin_recovers(
    monkeypatch,
):
    tmp_path = make_test_dir("sports-starvation-quiet-window-recovers")
    current_dt = datetime(2026, 7, 26, 12, 0, tzinfo=timezone.utc)
    clock = RuntimeClock(wall=current_dt.timestamp())
    sports_data = _runtime_plugin_data(
        "sports_dashboard",
        "SportsDashboard",
        interval=900,
    )
    sports_data["instance_uuid"] = "00000000000000000000000000000001"
    ordinary_data = _runtime_plugin_data(
        "ordinary",
        "Ordinary",
        interval=60,
    )
    ordinary_data["instance_uuid"] = "11111111111111111111111111111111"
    playlist = _runtime_playlist(sports_data, ordinary_data)
    calls = []
    task, device_config, _clock = _make_runtime_task(
        tmp_path,
        playlists=[playlist],
        clock=clock,
        sports_isolated_renderer=_fake_sports_isolated_renderer(calls),
    )
    device_config.config.update(
        {
            "theme_mode": "day",
            "active_theme": "day",
            "sports_isolated_liveness_starvation_seconds": 300,
            "sports_isolated_liveness_window_seconds": 60,
            "sports_isolated_liveness_cooldown_seconds": 300,
        }
    )
    sports, ordinary = [instance.snapshot() for instance in playlist.plugins]
    for instance in (sports, ordinary):
        _write_runtime_cache(task, instance)
    task.runtime_state.record_success(
        sports.instance_uuid,
        (current_dt - timedelta(hours=2)).isoformat(),
        lane=RefreshLane.DATA,
    )
    task.runtime_state.record_success(
        ordinary.instance_uuid,
        (current_dt - timedelta(minutes=20)).isoformat(),
        lane=RefreshLane.DATA,
    )
    resource_sample = {
        "value": ResourceSample(available_mb=113, swap_percent=50),
    }
    monkeypatch.setattr(
        task,
        "_resource_sample",
        lambda: resource_sample["value"],
    )
    monkeypatch.setattr(
        "src.refresh_task.get_plugin_instance",
        lambda _config: FakePlugin(calls),
    )

    assert task._select_independent_refresh_command(current_dt) is None
    assert calls == []

    resource_sample["value"] = ResourceSample(
        available_mb=120,
        swap_percent=50,
    )
    clock.advance(1)
    sports_retry = task._select_independent_refresh_command(
        current_dt + timedelta(seconds=1)
    )

    assert sports_retry is not None
    assert sports_retry.plugin_id == "sports_dashboard"
    assert task._admission_state.consecutive_data_admissions == 1
    assert (
        task._admission_state.last_soft_data_admitted_monotonic
        == clock.monotonic()
    )
    assert (
        task._admission_state.last_soft_renderer_admitted_monotonic
        == clock.monotonic()
    )
    completed = _queue_and_process(task, sports_retry)
    assert completed.job.status is JobStatus.SUCCEEDED
    assert calls == ["sports_dashboard"]

    clock.advance(1)
    assert (
        task._select_independent_refresh_command(
            current_dt + timedelta(seconds=2)
        )
        is None
    )
    assert task._sports_liveness_window is None


def test_completed_sports_window_yields_before_another_window(monkeypatch):
    current_dt = datetime(2026, 7, 31, 12, 0, tzinfo=timezone.utc)
    clock = RuntimeClock(wall=current_dt.timestamp())
    first_data = _runtime_plugin_data(
        "sports_dashboard",
        "Sports First",
        interval=900,
    )
    first_data["instance_uuid"] = "00000000000000000000000000000001"
    second_data = _runtime_plugin_data(
        "sports_dashboard",
        "Sports Second",
        interval=900,
    )
    second_data["instance_uuid"] = "00000000000000000000000000000002"
    ordinary_data = _runtime_plugin_data("ordinary", "Ordinary", interval=60)
    ordinary_data["instance_uuid"] = "11111111111111111111111111111111"
    playlist = _runtime_playlist(first_data, second_data, ordinary_data)
    task, device_config, _clock = _make_runtime_task(
        make_test_dir("sports-window-yields-between-instances"),
        playlists=[playlist],
        clock=clock,
    )
    device_config.config.update(
        {
            "theme_mode": "day",
            "active_theme": "day",
            "sports_isolated_liveness_starvation_seconds": 300,
            "sports_isolated_liveness_window_seconds": 60,
        }
    )
    first, second, ordinary = [
        instance.snapshot()
        for instance in playlist.plugins
    ]
    for instance in (first, second, ordinary):
        _write_runtime_cache(task, instance)
        task.runtime_state.record_success(
            instance.instance_uuid,
            (current_dt - timedelta(hours=20)).isoformat(),
            lane=RefreshLane.DATA,
        )
    monkeypatch.setattr(
        task,
        "_resource_sample",
        lambda: ResourceSample(available_mb=113, swap_percent=50),
    )
    monkeypatch.setattr(
        "src.refresh_task.get_plugin_instance",
        lambda _config: FakePlugin([]),
    )

    assert task._select_independent_refresh_command(current_dt) is None
    assert task._sports_liveness_window is not None
    assert task._sports_liveness_window.instance_uuid == first.instance_uuid

    clock.advance(1)
    task.runtime_state.record_success(
        first.instance_uuid,
        (current_dt + timedelta(seconds=1)).isoformat(),
        lane=RefreshLane.DATA,
    )
    next_command = task._select_independent_refresh_command(
        current_dt + timedelta(seconds=1)
    )

    assert next_command is not None
    assert next_command.instance_uuid == ordinary.instance_uuid
    assert task._sports_liveness_window is None


def test_sports_quiet_window_survives_execution_margin_race(monkeypatch):
    tmp_path = make_test_dir("sports-starvation-execution-margin-race")
    current_dt = datetime(2026, 7, 26, 12, 0, tzinfo=timezone.utc)
    clock = RuntimeClock(wall=current_dt.timestamp())
    sports_data = _runtime_plugin_data(
        "sports_dashboard",
        "SportsDashboard",
        interval=900,
    )
    sports_data["instance_uuid"] = "00000000000000000000000000000001"
    ordinary_data = _runtime_plugin_data(
        "ordinary",
        "Ordinary",
        interval=60,
    )
    ordinary_data["instance_uuid"] = "11111111111111111111111111111111"
    playlist = _runtime_playlist(sports_data, ordinary_data)
    task, device_config, _clock = _make_runtime_task(
        tmp_path,
        playlists=[playlist],
        clock=clock,
    )
    device_config.config.update(
        {
            "theme_mode": "day",
            "active_theme": "day",
            "sports_isolated_liveness_starvation_seconds": 300,
            "sports_isolated_liveness_window_seconds": 60,
            "sports_isolated_liveness_cooldown_seconds": 300,
        }
    )
    sports, ordinary = [instance.snapshot() for instance in playlist.plugins]
    for instance in (sports, ordinary):
        _write_runtime_cache(task, instance)
    task.runtime_state.record_success(
        sports.instance_uuid,
        (current_dt - timedelta(hours=2)).isoformat(),
        lane=RefreshLane.DATA,
    )
    task.runtime_state.record_success(
        ordinary.instance_uuid,
        (current_dt - timedelta(minutes=20)).isoformat(),
        lane=RefreshLane.DATA,
    )
    resource_sample = {
        "value": ResourceSample(available_mb=113, swap_percent=50),
    }
    monkeypatch.setattr(
        task,
        "_resource_sample",
        lambda: resource_sample["value"],
    )

    assert task._select_independent_refresh_command(current_dt) is None
    original_deadline = task._sports_liveness_window.deadline_monotonic

    resource_sample["value"] = ResourceSample(
        available_mb=120,
        swap_percent=50,
    )
    clock.advance(1)
    sports_retry = task._select_independent_refresh_command(
        current_dt + timedelta(seconds=1)
    )

    assert sports_retry is not None
    assert sports_retry.instance_uuid == sports.instance_uuid
    assert (
        task._sports_liveness_window.deadline_monotonic
        == original_deadline
    )

    resource_sample["value"] = ResourceSample(
        available_mb=113,
        swap_percent=50,
    )
    deferred = _queue_and_process(task, sports_retry)

    assert deferred.job.status is JobStatus.RUNNING
    assert deferred.job.error_code is None
    assert (
        task._sports_liveness_window.deadline_monotonic
        == original_deadline
    )

    resource_sample["value"] = ResourceSample(
        available_mb=120,
        swap_percent=50,
    )
    clock.advance(60)
    ordinary_refresh = task._select_independent_refresh_command(
        current_dt + timedelta(seconds=61)
    )

    assert ordinary_refresh is not None
    assert ordinary_refresh.instance_uuid == ordinary.instance_uuid
    assert task._sports_liveness_window is None
    assert (
        task._sports_liveness_cooldown_until_monotonic
        > clock.monotonic()
    )


def test_sports_quiet_window_expires_before_ordinary_refresh_is_starved(
    monkeypatch,
):
    tmp_path = make_test_dir("sports-starvation-quiet-window-bounded")
    current_dt = datetime(2026, 7, 26, 12, 0, tzinfo=timezone.utc)
    clock = RuntimeClock(wall=current_dt.timestamp())
    sports_data = _runtime_plugin_data(
        "sports_dashboard",
        "SportsDashboard",
        interval=900,
    )
    sports_data["plugin_settings"]["backgroundCacheRefreshEnabled"] = "true"
    sports_data["instance_uuid"] = "00000000000000000000000000000001"
    ordinary_data = _runtime_plugin_data(
        "ordinary",
        "Ordinary",
        interval=60,
    )
    ordinary_data["instance_uuid"] = "11111111111111111111111111111111"
    playlist = _runtime_playlist(sports_data, ordinary_data)
    task, device_config, _clock = _make_runtime_task(
        tmp_path,
        playlists=[playlist],
        clock=clock,
    )
    device_config.config.update(
        {
            "theme_mode": "day",
            "active_theme": "day",
            "sports_isolated_liveness_starvation_seconds": 300,
            "sports_isolated_liveness_window_seconds": 60,
            "sports_isolated_liveness_cooldown_seconds": 300,
            "display_triggered_refresh_enabled": True,
        }
    )
    sports_manifest = PluginManifest(
        schema_version=2,
        id="sports_dashboard",
        class_name="SportsDashboard",
        display_name="Sports Dashboard",
        refresh_on_display=False,
        capabilities=PluginCapabilities(supports_live_refresh=True),
        raw={},
    )
    device_config.get_plugin = lambda plugin_id: {
        "id": plugin_id,
        "_manifest": (
            sports_manifest if plugin_id == "sports_dashboard" else None
        ),
    }
    sports, ordinary = [instance.snapshot() for instance in playlist.plugins]
    for instance in (sports, ordinary):
        _write_runtime_cache(task, instance)
    task.runtime_state.record_success(
        sports.instance_uuid,
        (current_dt - timedelta(hours=2)).isoformat(),
        lane=RefreshLane.DATA,
    )
    task.runtime_state.record_success(
        ordinary.instance_uuid,
        (current_dt - timedelta(minutes=20)).isoformat(),
        lane=RefreshLane.DATA,
    )
    task.runtime_state.record_success(
        sports.instance_uuid,
        (current_dt - timedelta(hours=2)).isoformat(),
        lane=RefreshLane.LIVE,
    )
    task.runtime_state.set_display_state(
        "committed",
        instance_uuid=sports.instance_uuid,
        changed_at=(current_dt - timedelta(minutes=1)).isoformat(),
    )
    monkeypatch.setattr(
        "src.refresh_task.get_plugin_instance",
        lambda _config: FakePlugin(
            [],
            live_state={"active": True, "interval_seconds": 60},
        ),
    )
    monkeypatch.setattr(
        task,
        "_resource_sample",
        lambda: ResourceSample(available_mb=113, swap_percent=50),
    )
    background_probe = SimpleNamespace(
        wants_background_live_refresh=lambda _settings, _current_dt: True,
    )
    monkeypatch.setattr(
        task,
        "_get_plugin_for_snapshot",
        lambda _instance, require_live_refresh=False: background_probe,
    )
    monkeypatch.setattr(
        task,
        "_snapshot_live_refresh_state",
        lambda _instance, _current_dt, plugin=None: {
            "active": True,
            "interval_seconds": 60,
        },
    )

    assert task._select_independent_refresh_command(current_dt) is None
    assert task._due_counts[RefreshLane.LIVE.value] == 1

    clock.advance(61)
    ordinary_refresh = task._select_independent_refresh_command(
        current_dt + timedelta(seconds=61)
    )

    assert ordinary_refresh is not None
    assert ordinary_refresh.plugin_id == "ordinary"


def test_never_successful_sports_uses_first_resource_deferral_as_starvation_anchor(
    monkeypatch,
):
    tmp_path = make_test_dir("sports-starvation-first-deferral-anchor")
    current_dt = datetime(2026, 7, 26, 12, 0, tzinfo=timezone.utc)
    clock = RuntimeClock(wall=current_dt.timestamp())
    sports_data = _runtime_plugin_data(
        "sports_dashboard",
        "SportsDashboard",
        latest_refresh_time=None,
        interval=60,
    )
    sports_data["instance_uuid"] = "00000000000000000000000000000001"
    playlist = _runtime_playlist(sports_data)
    task, device_config, _clock = _make_runtime_task(
        tmp_path,
        playlists=[playlist],
        clock=clock,
    )
    device_config.config.update(
        {
            "theme_mode": "day",
            "active_theme": "day",
            "sports_isolated_liveness_starvation_seconds": 300,
            "sports_isolated_liveness_window_seconds": 60,
            "sports_isolated_liveness_cooldown_seconds": 300,
        }
    )
    sports = playlist.plugins[0].snapshot()
    _write_runtime_cache(task, sports)
    monkeypatch.setattr(
        task,
        "_resource_sample",
        lambda: ResourceSample(available_mb=113, swap_percent=50),
    )

    first_attempt = task._select_independent_refresh_command(current_dt)

    assert first_attempt is not None
    assert first_attempt.instance_uuid == sports.instance_uuid
    deferred = _queue_and_process(task, first_attempt)
    assert deferred.job.status is JobStatus.RUNNING
    assert deferred.job.error_code is None
    task.runtime_state.flush()

    restarted_task, restarted_config, _clock = _make_runtime_task(
        tmp_path,
        playlists=[playlist],
        clock=clock,
    )
    restarted_config.config.update(device_config.config)
    monkeypatch.setattr(
        restarted_task,
        "_resource_sample",
        lambda: ResourceSample(available_mb=113, swap_percent=50),
    )

    clock.advance(301)
    after_starvation = restarted_task._select_independent_refresh_command(
        current_dt + timedelta(seconds=301)
    )

    assert after_starvation is None
    assert restarted_task._sports_liveness_window is not None
    assert (
        restarted_task._sports_liveness_window.instance_uuid
        == sports.instance_uuid
    )


def test_sports_quiet_window_cooldown_excludes_all_sports_instances(
    monkeypatch,
):
    tmp_path = make_test_dir("sports-starvation-cooldown-all-instances")
    current_dt = datetime(2026, 7, 26, 12, 0, tzinfo=timezone.utc)
    clock = RuntimeClock(wall=current_dt.timestamp())
    first_sports_data = _runtime_plugin_data(
        "sports_dashboard",
        "SportsOne",
        interval=900,
    )
    first_sports_data["instance_uuid"] = (
        "00000000000000000000000000000001"
    )
    second_sports_data = _runtime_plugin_data(
        "sports_dashboard",
        "SportsTwo",
        interval=900,
    )
    second_sports_data["instance_uuid"] = (
        "00000000000000000000000000000002"
    )
    ordinary_data = _runtime_plugin_data(
        "ordinary",
        "Ordinary",
        interval=60,
    )
    ordinary_data["instance_uuid"] = "11111111111111111111111111111111"
    playlist = _runtime_playlist(
        first_sports_data,
        second_sports_data,
        ordinary_data,
    )
    task, device_config, _clock = _make_runtime_task(
        tmp_path,
        playlists=[playlist],
        clock=clock,
    )
    device_config.config.update(
        {
            "theme_mode": "day",
            "active_theme": "day",
            "sports_isolated_liveness_starvation_seconds": 300,
            "sports_isolated_liveness_window_seconds": 60,
            "sports_isolated_liveness_cooldown_seconds": 300,
        }
    )
    first_sports, second_sports, ordinary = [
        instance.snapshot() for instance in playlist.plugins
    ]
    for instance in (first_sports, second_sports, ordinary):
        _write_runtime_cache(task, instance)
    for sports in (first_sports, second_sports):
        task.runtime_state.record_success(
            sports.instance_uuid,
            (current_dt - timedelta(hours=2)).isoformat(),
            lane=RefreshLane.DATA,
        )
    task.runtime_state.record_success(
        ordinary.instance_uuid,
        (current_dt - timedelta(minutes=20)).isoformat(),
        lane=RefreshLane.DATA,
    )
    monkeypatch.setattr(
        task,
        "_resource_sample",
        lambda: ResourceSample(available_mb=113, swap_percent=50),
    )

    assert task._select_independent_refresh_command(current_dt) is None

    clock.advance(61)
    ordinary_refresh = task._select_independent_refresh_command(
        current_dt + timedelta(seconds=61)
    )

    assert ordinary_refresh is not None
    assert ordinary_refresh.instance_uuid == ordinary.instance_uuid


def test_sports_starvation_entitlement_is_rebuilt_after_runtime_restart(
    monkeypatch,
):
    tmp_path = make_test_dir("sports-starvation-rebuilt-after-restart")
    current_dt = datetime(2026, 7, 26, 12, 0, tzinfo=timezone.utc)
    clock = RuntimeClock(wall=current_dt.timestamp())
    sports_data = _runtime_plugin_data(
        "sports_dashboard",
        "SportsDashboard",
        interval=900,
    )
    sports_data["instance_uuid"] = "00000000000000000000000000000001"
    ordinary_data = _runtime_plugin_data(
        "ordinary",
        "Ordinary",
        interval=60,
    )
    ordinary_data["instance_uuid"] = "11111111111111111111111111111111"
    playlist = _runtime_playlist(sports_data, ordinary_data)
    task, device_config, _clock = _make_runtime_task(
        tmp_path,
        playlists=[playlist],
        clock=clock,
    )
    device_config.config.update(
        {
            "theme_mode": "day",
            "active_theme": "day",
            "sports_isolated_liveness_starvation_seconds": 300,
            "sports_isolated_liveness_window_seconds": 60,
            "sports_isolated_liveness_cooldown_seconds": 300,
        }
    )
    sports, ordinary = [instance.snapshot() for instance in playlist.plugins]
    for instance in (sports, ordinary):
        _write_runtime_cache(task, instance)
    task.runtime_state.record_success(
        sports.instance_uuid,
        (current_dt - timedelta(hours=2)).isoformat(),
        lane=RefreshLane.DATA,
    )
    task.runtime_state.record_success(
        ordinary.instance_uuid,
        (current_dt - timedelta(minutes=20)).isoformat(),
        lane=RefreshLane.DATA,
    )
    task.runtime_state.flush()

    restarted_task, restarted_config, _clock = _make_runtime_task(
        tmp_path,
        playlists=[playlist],
        clock=clock,
    )
    restarted_config.config.update(device_config.config)
    monkeypatch.setattr(
        restarted_task,
        "_resource_sample",
        lambda: ResourceSample(available_mb=113, swap_percent=50),
    )

    assert (
        restarted_task._select_independent_refresh_command(current_dt)
        is None
    )


def test_sustained_soft_pressure_deferral_does_not_starve_other_due_plugin(
    monkeypatch,
):
    tmp_path = make_test_dir("sports-background-soft-pressure-fairness")
    current_dt = datetime(2026, 7, 26, 12, 0, tzinfo=timezone.utc)
    clock = RuntimeClock(wall=current_dt.timestamp())
    sports_data = _runtime_plugin_data(
        "sports_dashboard",
        "SportsDashboard",
        latest_refresh_time=None,
    )
    sports_data["plugin_settings"]["backgroundCacheRefreshEnabled"] = "true"
    sports_data["instance_uuid"] = "00000000000000000000000000000001"
    ordinary_data = _runtime_plugin_data(
        "ordinary",
        "Ordinary",
        latest_refresh_time=None,
    )
    ordinary_data["instance_uuid"] = "11111111111111111111111111111111"
    playlist = _runtime_playlist(sports_data, ordinary_data)
    calls = []
    task, device_config, _clock = _make_runtime_task(
        tmp_path,
        playlists=[playlist],
        clock=clock,
        sports_isolated_renderer=_fake_sports_isolated_renderer(calls),
    )
    device_config.config.update(
        {
            "theme_mode": "day",
            "active_theme": "day",
            "display_triggered_refresh_enabled": True,
        }
    )
    sports_manifest = PluginManifest(
        schema_version=2,
        id="sports_dashboard",
        class_name="SportsDashboard",
        display_name="Sports Dashboard",
        refresh_on_display=False,
        capabilities=PluginCapabilities(supports_live_refresh=True),
        raw={},
    )
    device_config.get_plugin = lambda plugin_id: {
        "id": plugin_id,
        "_manifest": (
            sports_manifest if plugin_id == "sports_dashboard" else None
        ),
    }
    resource_sample = {
        "value": ResourceSample(available_mb=512, swap_percent=0),
    }
    live_state = {"value": None}
    monkeypatch.setattr(
        task,
        "_resource_sample",
        lambda: resource_sample["value"],
    )
    monkeypatch.setattr(
        "src.refresh_task.get_plugin_instance",
        lambda _config: FakePlugin(
            calls,
            live_state=live_state["value"],
        ),
    )

    sports = task._select_independent_refresh_command(current_dt)
    assert sports is not None
    assert sports.plugin_id == "sports_dashboard"
    resource_sample["value"] = ResourceSample(available_mb=113, swap_percent=50)
    deferred = _queue_and_process(task, sports)

    assert deferred.job.status is JobStatus.RUNNING
    assert deferred.job.error_code is None
    assert calls == []

    sports_instance = playlist.plugins[0].snapshot()
    task.runtime_state.record_success(
        sports_instance.instance_uuid,
        (current_dt - timedelta(hours=2)).isoformat(),
        lane=RefreshLane.LIVE,
    )
    task.runtime_state.set_display_state(
        "committed",
        instance_uuid=sports_instance.instance_uuid,
        changed_at=(current_dt - timedelta(minutes=1)).isoformat(),
    )
    live_state["value"] = {"active": True, "interval_seconds": 60}

    clock.advance(1)
    ordinary = task._select_independent_refresh_command(
        current_dt + timedelta(seconds=1)
    )
    assert ordinary is not None
    assert ordinary.plugin_id == "ordinary"
    completed = _queue_and_process(task, ordinary)

    assert completed.job.status is JobStatus.SUCCEEDED
    assert calls == ["ordinary"]

    clock.advance(60)
    assert (
        task._select_independent_refresh_command(
            current_dt + timedelta(seconds=61)
        )
        is None
    )
    assert calls == ["ordinary"]

    resource_sample["value"] = ResourceSample(
        available_mb=120,
        swap_percent=50,
    )
    clock.advance(60)
    task._process_queue_entry(deferred)
    completed_sports = task.refresh_queue.get_entry(deferred.job.id)
    assert completed_sports.job.status is JobStatus.SUCCEEDED
    assert calls == ["ordinary", "sports_dashboard"]


def test_hard_pressure_still_rotates_valid_caches_without_generation(monkeypatch):
    tmp_path = make_test_dir("independent-hard-display")
    current_dt = datetime(2026, 7, 11, 12, 0, tzinfo=timezone.utc)
    playlist = _runtime_playlist(
        _runtime_plugin_data("one", "One", latest_refresh_time=None)
    )
    task, device_config, _clock = _make_runtime_task(
        tmp_path,
        playlists=[playlist],
        cycle_seconds=60,
    )
    device_config.config.update({"theme_mode": "day", "active_theme": "day"})
    device_config.refresh_info.refresh_time = (
        current_dt - timedelta(minutes=2)
    ).isoformat()
    _write_runtime_cache(task, playlist.plugins[0])
    monkeypatch.setattr(
        task,
        "_resource_sample",
        lambda: ResourceSample(available_mb=60, swap_percent=80),
        raising=False,
    )
    monkeypatch.setattr(
        "src.refresh_task.get_plugin_instance",
        lambda _config: (_ for _ in ()).throw(
            AssertionError("hard-tier cache display must not render")
        ),
    )

    display = task._select_cached_display_command(current_dt)
    refresh = task._select_independent_refresh_command(current_dt)
    result = task._execute_command(display)

    assert display.intent is RefreshIntent.DISPLAY_CACHE
    assert refresh is None
    assert result is not None
    assert len(task.display_manager.calls) == 1


def test_watchdog_restart_still_displays_valid_cache_and_blocks_generation(
    monkeypatch,
):
    tmp_path = make_test_dir("watchdog-hard-cache-display")
    current_dt = datetime(2026, 7, 11, 12, 0, tzinfo=timezone.utc)
    playlist = _runtime_playlist(
        _runtime_plugin_data("one", "One", latest_refresh_time=None)
    )
    task, device_config, _clock = _make_runtime_task(
        tmp_path,
        playlists=[playlist],
        cycle_seconds=60,
    )
    device_config.config.update({"theme_mode": "day", "active_theme": "day"})
    device_config.refresh_info.refresh_time = (
        current_dt - timedelta(minutes=2)
    ).isoformat()
    _write_runtime_cache(task, playlist.plugins[0])

    def request_restart():
        task._restart_request = {"reason": "memory_pressure"}
        return True

    monkeypatch.setattr(task, "_get_current_datetime", lambda: current_dt)
    monkeypatch.setattr(task, "_memory_watchdog_should_restart", request_restart)
    monkeypatch.setattr(
        task,
        "_select_independent_refresh_command",
        lambda _now: pytest.fail("hard-tier watchdog admitted renderer generation"),
    )
    monkeypatch.setattr(
        "src.refresh_task.get_plugin_instance",
        lambda _config: pytest.fail("hard-tier cache display instantiated a plugin"),
    )

    processed = task._run_one_iteration_for_test()

    assert processed is not None
    assert processed.command.intent is RefreshIntent.DISPLAY_CACHE
    assert task.restart_request["reason"] == "memory_pressure"
    assert len(task.display_manager.calls) == 1


def test_data_failure_a_does_not_delay_due_instance_b_or_global_poll(monkeypatch):
    tmp_path = make_test_dir("independent-failure-isolation")
    clock = RuntimeClock()
    current_dt = datetime(2026, 7, 11, 12, 0, tzinfo=timezone.utc)
    playlist = _runtime_playlist(
        _runtime_plugin_data("one", "One", latest_refresh_time=None),
        _runtime_plugin_data("two", "Two", latest_refresh_time=None),
    )
    task, _device_config, _clock = _make_runtime_task(
        tmp_path,
        playlists=[playlist],
        clock=clock,
    )
    monkeypatch.setattr(
        task,
        "_resource_sample",
        lambda: ResourceSample(available_mb=512, swap_percent=0),
        raising=False,
    )
    first = task._select_independent_refresh_command(current_dt)
    submitted = task.refresh_queue.submit(first)
    monkeypatch.setattr(
        task,
        "_execute_command",
        lambda _command: (_ for _ in ()).throw(RuntimeError("instance offline")),
    )

    task._process_queue_entry(task.refresh_queue.take(timeout=0))
    clock.advance(task._scheduler_poll_seconds())
    second = task._select_independent_refresh_command(
        current_dt + timedelta(seconds=task._scheduler_poll_seconds())
    )

    assert task.refresh_queue.get_entry(submitted.id).job.status is JobStatus.FAILED
    assert second is not None
    assert second.instance_uuid != first.instance_uuid
    assert task.scheduler_snapshot().next_attempt_monotonic == (
        clock.monotonic()
    )


def test_live_and_theme_failure_do_not_cool_data_lane(monkeypatch):
    tmp_path = make_test_dir("independent-lane-failure-isolation")
    current_dt = datetime(2026, 7, 11, 12, 0, tzinfo=timezone.utc)
    playlist = _runtime_playlist(
        _runtime_plugin_data("one", "One", latest_refresh_time=None)
    )
    task, _device_config, _clock = _make_runtime_task(
        tmp_path,
        playlists=[playlist],
    )
    instance = playlist.plugins[0].snapshot()
    live = task._playlist_command(
        playlist.name,
        instance,
        source=CommandSource.LIVE,
        intent=RefreshIntent.LIVE_REFRESH,
        kind=CommandKind.CACHE_REFRESH,
    )
    theme = task._playlist_command(
        playlist.name,
        instance,
        source=CommandSource.SCHEDULER,
        intent=RefreshIntent.THEME_REDRAW,
        kind=CommandKind.CACHE_REFRESH,
        theme_render_only=True,
    )
    task._record_intent_failure(live, RuntimeError("live failed"), current_dt)
    task._record_intent_failure(theme, RuntimeError("theme failed"), current_dt)
    monkeypatch.setattr(
        task,
        "_resource_sample",
        lambda: ResourceSample(available_mb=512, swap_percent=0),
        raising=False,
    )

    selected = task._select_independent_refresh_command(current_dt)
    state = task.runtime_state.snapshot().instances[instance.instance_uuid]

    assert selected.intent is RefreshIntent.DATA_REFRESH
    assert state.data.next_retry_at is None
    assert state.live.next_retry_at is not None
    assert state.theme.next_retry_at is not None


def test_background_max_per_pass_above_one_is_compatibly_clamped_without_config_write():
    tmp_path = make_test_dir("independent-max-per-pass-clamp")
    device_config = FakeDeviceConfig(tmp_path)
    device_config.config["background_cache_refresh_max_per_pass"] = 9
    task = RefreshTask(device_config, display_manager=None)

    assert task._background_cache_refresh_max_per_pass() == 1
    assert device_config.config["background_cache_refresh_max_per_pass"] == 9
    assert device_config.write_count == 0


def _sports_live_runtime(name, *, background_value="missing"):
    tmp_path = make_test_dir(name)
    current_dt = datetime(2026, 7, 11, 12, 0, tzinfo=timezone.utc)
    plugin_data = _runtime_plugin_data(
        "sports_dashboard",
        "Sports",
        latest_refresh_time=current_dt.isoformat(),
        interval=3600,
    )
    plugin_data["plugin_settings"].update(
        {"worldCupLiveRefreshEnabled": "true"}
    )
    if background_value != "missing":
        plugin_data["plugin_settings"]["backgroundCacheRefreshEnabled"] = (
            background_value
        )
    playlist = _runtime_playlist(plugin_data)
    isolated_calls = []

    def isolated_renderer(**kwargs):
        isolated_calls.append(kwargs)
        return attach_source_provenance(
            Image.new("RGB", (1, 1), "white"),
            SourceProvenance.LIVE,
        )

    task, device_config, _clock = _make_runtime_task(
        tmp_path,
        playlists=[playlist],
        cycle_seconds=300,
        sports_isolated_renderer=isolated_renderer,
    )
    task._test_isolated_sports_calls = isolated_calls
    manifest = PluginManifest(
        schema_version=2,
        id="sports_dashboard",
        class_name="SportsDashboard",
        display_name="Sports Dashboard",
        refresh_on_display=False,
        capabilities=PluginCapabilities(supports_live_refresh=True),
        raw={},
    )
    device_config.get_plugin = lambda plugin_id: {
        "id": plugin_id,
        "_manifest": manifest,
    }
    device_config.config.update(
        {
            "theme_mode": "day",
            "active_theme": "day",
            "display_triggered_refresh_enabled": True,
        }
    )
    instance = playlist.plugins[0]
    _write_runtime_cache(task, instance)
    task.runtime_state.record_success(
        instance.instance_uuid,
        current_dt.isoformat(),
        lane=RefreshLane.DATA,
    )
    task.runtime_state.record_success(
        instance.instance_uuid,
        (current_dt - timedelta(minutes=2)).isoformat(),
        lane=RefreshLane.LIVE,
    )
    task.runtime_state.set_display_state(
        "committed",
        instance_uuid=instance.instance_uuid,
        changed_at=(current_dt - timedelta(minutes=1)).isoformat(),
    )
    anchor = (current_dt - timedelta(minutes=1)).isoformat()
    device_config.refresh_info = RefreshInfo(
        refresh_type="Playlist",
        playlist=playlist.name,
        plugin_id=instance.plugin_id,
        plugin_instance=instance.name,
        refresh_time=anchor,
        image_hash="old",
    )
    return task, device_config, playlist, instance, current_dt, anchor


def _assert_sports_normal_selected(monkeypatch, background_value):
    task, _device_config, _playlist, instance, current_dt, _anchor = (
        _sports_live_runtime(
            f"sports-normal-{background_value}",
            background_value=background_value,
        )
    )
    task.runtime_state.record_success(
        instance.instance_uuid,
        (current_dt - timedelta(hours=2)).isoformat(),
        lane=RefreshLane.DATA,
    )
    monkeypatch.setattr(
        "src.refresh_task.get_plugin_instance",
        lambda _config: FakePlugin([], live_state=None),
    )
    monkeypatch.setattr(
        task,
        "_resource_sample",
        lambda: ResourceSample(available_mb=512, swap_percent=0),
    )

    command = task._select_independent_refresh_command(current_dt)

    assert command is not None
    assert command.instance_uuid == instance.instance_uuid
    assert command.intent is RefreshIntent.DATA_REFRESH


def test_sports_normal_interval_is_selected_when_background_flag_is_missing(
    monkeypatch,
):
    _assert_sports_normal_selected(monkeypatch, "missing")


def test_sports_normal_interval_is_selected_when_background_flag_is_false(
    monkeypatch,
):
    _assert_sports_normal_selected(monkeypatch, False)


def test_sports_live_cache_refresh_is_background_only_when_master_setting_is_missing(
    monkeypatch,
):
    task, _device_config, _playlist, _instance, current_dt, _anchor = (
        _sports_live_runtime("sports-live-master-missing")
    )
    background_probe = SimpleNamespace(
        wants_background_live_refresh=lambda _settings, _current_dt: True,
    )
    monkeypatch.setattr(
        task,
        "_get_plugin_for_snapshot",
        lambda _instance, require_live_refresh=False: background_probe,
    )
    monkeypatch.setattr(
        task,
        "_snapshot_live_refresh_state",
        lambda _instance, _current_dt, plugin=None: {
            "active": True,
            "interval_seconds": 60,
        },
    )
    monkeypatch.setattr(
        task,
        "_resource_sample",
        lambda: ResourceSample(available_mb=512, swap_percent=0),
    )

    command = task._select_independent_refresh_command(current_dt)

    assert command is not None
    assert command.intent is RefreshIntent.LIVE_REFRESH
    assert command.payload["background_live_refresh"] is True
    assert command.payload.get("expected_displayed_instance_uuid") is None


@pytest.mark.parametrize(
    ("enabled", "hook_active", "displayed", "sample", "expected_live"),
    [
        (True, True, True, ResourceSample(512, 0), True),
        (True, False, True, ResourceSample(512, 0), False),
        (True, True, False, ResourceSample(512, 0), True),
        (True, True, True, ResourceSample(100, 0), True),
        (True, True, True, ResourceSample(60, 0), False),
        (False, True, True, ResourceSample(512, 0), False),
    ],
)
def test_sports_background_live_requires_enabled_hook_and_non_hard_tier(
    monkeypatch,
    enabled,
    hook_active,
    displayed,
    sample,
    expected_live,
):
    task, _device_config, _playlist, instance, current_dt, _anchor = (
        _sports_live_runtime(
            "sports-live-gates",
            background_value=enabled,
        )
    )
    if not displayed:
        task.runtime_state.set_display_state(
            "committed",
            instance_uuid="different-instance",
            changed_at=current_dt.isoformat(),
        )
    background_probe = SimpleNamespace(
        wants_background_live_refresh=lambda _settings, _current_dt: (
            enabled and hook_active
        ),
    )
    monkeypatch.setattr(
        task,
        "_get_plugin_for_snapshot",
        lambda _instance, require_live_refresh=False: background_probe,
    )
    monkeypatch.setattr(
        task,
        "_snapshot_live_refresh_state",
        lambda _instance, _current_dt, plugin=None: (
            {"active": True, "interval_seconds": 60}
            if hook_active
            else None
        ),
    )
    monkeypatch.setattr(task, "_resource_sample", lambda: sample)

    command = task._select_independent_refresh_command(current_dt)

    assert (command is not None) is expected_live
    if expected_live:
        assert command.intent is RefreshIntent.LIVE_REFRESH


def test_explicit_false_legacy_background_flag_is_live_master_off_only(
    monkeypatch,
):
    task, _device_config, _playlist, instance, current_dt, _anchor = (
        _sports_live_runtime(
            "sports-live-master-off-data-on",
            background_value=False,
        )
    )
    task.runtime_state.record_success(
        instance.instance_uuid,
        (current_dt - timedelta(hours=2)).isoformat(),
        lane=RefreshLane.DATA,
    )
    monkeypatch.setattr(
        "src.refresh_task.get_plugin_instance",
        lambda _config: FakePlugin(
            [],
            live_state={"active": True, "interval_seconds": 60},
        ),
    )
    monkeypatch.setattr(
        task,
        "_resource_sample",
        lambda: ResourceSample(available_mb=512, swap_percent=0),
    )

    command = task._select_independent_refresh_command(current_dt)

    assert command.intent is RefreshIntent.DATA_REFRESH


@pytest.mark.parametrize("display_policy", [False, True])
def test_sports_live_success_updates_cache_without_redisplaying(
    monkeypatch,
    display_policy,
):
    task, device_config, _playlist, instance, current_dt, anchor = (
        _sports_live_runtime(
            "sports-live-followup",
            background_value=True,
        )
    )
    calls = []
    monkeypatch.setattr(
        "src.refresh_task.get_plugin_instance",
        lambda _config: FakePlugin(
            calls,
            live_state={"active": True, "interval_seconds": 60},
        ),
    )
    monkeypatch.setattr(
        task,
        "_resource_sample",
        lambda: ResourceSample(available_mb=512, swap_percent=0),
    )
    monkeypatch.setattr(task, "_get_current_datetime", lambda: current_dt)
    background_probe = SimpleNamespace(
        wants_background_live_refresh=lambda _settings, _current_dt: True,
    )
    monkeypatch.setattr(
        task,
        "_get_plugin_for_snapshot",
        lambda _instance, require_live_refresh=False: background_probe,
    )
    monkeypatch.setattr(
        task,
        "_snapshot_live_refresh_state",
        lambda _instance, _current_dt, plugin=None: {
            "active": True,
            "interval_seconds": 60,
        },
    )
    device_config.config["display_triggered_refresh_enabled"] = display_policy
    command = task._select_independent_refresh_command(current_dt)
    assert command is not None
    assert command.intent is RefreshIntent.LIVE_REFRESH
    assert command.payload["background_live_refresh"] is True
    assert command.payload.get("expected_displayed_instance_uuid") is None
    display_calls_before = len(task.display_manager.calls)

    submitted = task.refresh_queue.submit(command)
    task._process_queue_entry(task.refresh_queue.take(timeout=0))
    followup = task.refresh_queue.take(timeout=0)

    assert task.refresh_queue.get_entry(submitted.id).job.status is JobStatus.SUCCEEDED
    assert followup is None
    assert calls == []
    assert len(task._test_isolated_sports_calls) == 1
    assert device_config.refresh_info.refresh_time == anchor
    assert len(task.display_manager.calls) == display_calls_before


def test_queued_live_followup_is_canceled_if_display_policy_turns_off(
    monkeypatch,
):
    task, device_config, instance, current_dt, _data_success, _anchor = (
        _live_radar_runtime("live-followup-policy-recheck")
    )
    monkeypatch.setattr(
        "src.refresh_task.get_plugin_instance",
        lambda _config: FakePlugin(
            [],
            live_state={"active": True, "interval_seconds": 60},
        ),
    )
    monkeypatch.setattr(
        task,
        "_resource_sample",
        lambda: ResourceSample(available_mb=512, swap_percent=0),
    )
    monkeypatch.setattr(task, "_get_current_datetime", lambda: current_dt)

    live = task._select_independent_refresh_command(current_dt)
    assert live is not None
    assert live.intent is RefreshIntent.LIVE_REFRESH
    assert live.payload.get("background_live_refresh") is not True
    assert live.payload["expected_displayed_instance_uuid"] == instance.instance_uuid

    task.refresh_queue.submit(live)
    task._process_queue_entry(task.refresh_queue.take(timeout=0))
    followup = task.refresh_queue.take(timeout=0)
    assert followup is not None
    assert followup.command.source is CommandSource.LIVE
    assert followup.command.intent is RefreshIntent.DISPLAY_CACHE

    device_config.config["display_triggered_refresh_enabled"] = False
    display_calls_before = len(task.display_manager.calls)
    task._process_queue_entry(followup)
    result = task.refresh_queue.get_entry(followup.job.id).job

    assert result.status is JobStatus.CANCELED
    assert result.error_code == "stale_selection"
    assert len(task.display_manager.calls) == display_calls_before


def test_live_exact_followup_does_not_merge_with_pending_manual_display(
    monkeypatch,
):
    task, _device_config, instance, current_dt, _data_success, _anchor = (
        _live_radar_runtime("live-exact-followup-scope")
    )
    playlist = task.device_config.get_playlist_manager().snapshot_active_playlist(
        current_dt
    )
    assert playlist is not None
    monkeypatch.setattr(
        "src.refresh_task.get_plugin_instance",
        lambda _config: FakePlugin(
            [],
            live_state={"active": True, "interval_seconds": 60},
        ),
    )
    monkeypatch.setattr(
        task,
        "_resource_sample",
        lambda: ResourceSample(available_mb=512, swap_percent=0),
    )
    monkeypatch.setattr(task, "_get_current_datetime", lambda: current_dt)
    live = task._select_independent_refresh_command(current_dt)
    task.refresh_queue.submit(live)
    running_live = task.refresh_queue.take(timeout=0)
    manual = task._playlist_command(
        playlist.name,
        instance.snapshot(),
        source=CommandSource.MANUAL,
        intent=RefreshIntent.DISPLAY_CACHE,
        display_cached_only=True,
        priority=100,
        current_dt=current_dt,
        cache_theme_mode=None,
        require_active=False,
    )
    task.refresh_queue.submit(manual)

    task._process_queue_entry(running_live)
    entries = [
        task.refresh_queue.take(timeout=0),
        task.refresh_queue.take(timeout=0),
    ]
    assert all(entry is not None for entry in entries)
    pending = [entry.command for entry in entries]
    exact = next(command for command in pending if command.source is CommandSource.LIVE)
    retained_manual = next(
        command for command in pending if command.source is CommandSource.MANUAL
    )

    assert retained_manual.payload["require_active"] is False
    assert exact.payload["expected_displayed_instance_uuid"] == instance.instance_uuid
    assert exact.coalescing_scope is not None
    assert exact.coalescing_scope != retained_manual.coalescing_scope


def test_sports_live_success_does_not_advance_normal_data_cadence(monkeypatch):
    task, _device_config, _playlist, instance, current_dt, _anchor = (
        _sports_live_runtime(
            "sports-live-lane-clock",
            background_value=True,
        )
    )
    data_success = task.runtime_state.snapshot().instances[
        instance.instance_uuid
    ].data.last_success_at
    monkeypatch.setattr(
        "src.refresh_task.get_plugin_instance",
        lambda _config: FakePlugin(
            [],
            live_state={"active": True, "interval_seconds": 60},
        ),
    )
    monkeypatch.setattr(
        task,
        "_resource_sample",
        lambda: ResourceSample(available_mb=512, swap_percent=0),
    )
    monkeypatch.setattr(task, "_get_current_datetime", lambda: current_dt)
    background_probe = SimpleNamespace(
        wants_background_live_refresh=lambda _settings, _current_dt: True,
    )
    monkeypatch.setattr(
        task,
        "_get_plugin_for_snapshot",
        lambda _instance, require_live_refresh=False: background_probe,
    )
    monkeypatch.setattr(
        task,
        "_snapshot_live_refresh_state",
        lambda _instance, _current_dt, plugin=None: {
            "active": True,
            "interval_seconds": 60,
        },
    )
    command = task._select_independent_refresh_command(current_dt)

    submitted = task.refresh_queue.submit(command)
    task._process_queue_entry(task.refresh_queue.take(timeout=0))
    state = task.runtime_state.snapshot().instances[instance.instance_uuid]

    assert task.refresh_queue.get_entry(submitted.id).job.status is JobStatus.SUCCEEDED
    assert state.data.last_success_at == data_success
    assert state.live.last_success_at == current_dt.isoformat()


def _live_radar_runtime(name):
    tmp_path = make_test_dir(name)
    current_dt = datetime(2026, 7, 12, 12, 0, tzinfo=timezone.utc)
    plugin_data = _runtime_plugin_data(
        "live_radar",
        "LiveRadar",
        latest_refresh_time=(current_dt - timedelta(seconds=90)).isoformat(),
        interval=120,
    )
    plugin_data["plugin_settings"].update({"roomsText": "twitch|xqc|xQc", "fetchAvatars": False})
    playlist = _runtime_playlist(plugin_data)
    task, device_config, _clock = _make_runtime_task(
        tmp_path,
        playlists=[playlist],
        cycle_seconds=300,
    )
    manifest = PluginManifest(
        schema_version=2,
        id="live_radar",
        class_name="LiveRadar",
        display_name="LiveRadar",
        refresh_on_display=True,
        capabilities=PluginCapabilities(
            supports_live_refresh=True,
            supports_presentation_refresh=True,
        ),
        raw={},
    )
    device_config.get_plugin = lambda plugin_id: {
        "id": plugin_id,
        "_manifest": manifest,
    }
    device_config.config.update(
        {
            "theme_mode": "day",
            "active_theme": "day",
            "display_triggered_refresh_enabled": True,
        }
    )
    instance = playlist.plugins[0]
    _write_runtime_cache(task, instance)
    data_success = (current_dt - timedelta(seconds=90)).isoformat()
    task.runtime_state.record_success(
        instance.instance_uuid,
        data_success,
        lane=RefreshLane.DATA,
    )
    task.runtime_state.record_success(
        instance.instance_uuid,
        (current_dt - timedelta(seconds=61)).isoformat(),
        lane=RefreshLane.LIVE,
    )
    task.runtime_state.set_display_state(
        "committed",
        instance_uuid=instance.instance_uuid,
        changed_at=(current_dt - timedelta(seconds=30)).isoformat(),
    )
    anchor = (current_dt - timedelta(seconds=30)).isoformat()
    device_config.refresh_info = RefreshInfo(
        refresh_type="Playlist",
        playlist=playlist.name,
        plugin_id=instance.plugin_id,
        plugin_instance=instance.name,
        refresh_time=anchor,
        image_hash="old",
    )
    return task, device_config, instance, current_dt, data_success, anchor


def test_live_radar_display_state_does_not_create_a_live_refresh_candidate_by_default(
    monkeypatch,
):
    task, device_config, instance, current_dt, data_success, anchor = _live_radar_runtime(
        "live-radar-independent-lanes"
    )
    calls = []
    monkeypatch.setattr(
        "src.refresh_task.get_plugin_instance",
        lambda _config: FakePlugin(
            calls,
            live_state={"active": True, "interval_seconds": 60},
        ),
    )
    monkeypatch.setattr(
        task,
        "_resource_sample",
        lambda: ResourceSample(available_mb=512, swap_percent=0),
    )
    monkeypatch.setattr(task, "_get_current_datetime", lambda: current_dt)

    device_config.config["display_triggered_refresh_enabled"] = False
    command = task._select_independent_refresh_command(current_dt)
    state = task.runtime_state.snapshot().instances[instance.instance_uuid]

    assert command is None
    assert calls == []
    assert state.data.last_success_at == data_success
    assert state.live.last_success_at == (
        current_dt - timedelta(seconds=61)
    ).isoformat()
    assert device_config.refresh_info.refresh_time == anchor


def test_live_radar_still_uses_ordinary_data_refresh_when_policy_is_off(
    monkeypatch,
):
    task, device_config, instance, current_dt, _data_success, _anchor = (
        _live_radar_runtime("live-radar-policy-off-data-refresh")
    )
    due_dt = current_dt + timedelta(seconds=31)
    device_config.config["display_triggered_refresh_enabled"] = False
    monkeypatch.setattr(
        "src.refresh_task.get_plugin_instance",
        lambda _config: FakePlugin(
            [],
            live_state={"active": True, "interval_seconds": 60},
        ),
    )
    monkeypatch.setattr(
        task,
        "_resource_sample",
        lambda: ResourceSample(available_mb=512, swap_percent=0),
    )
    monkeypatch.setattr(task, "_get_current_datetime", lambda: due_dt)

    command = task._select_independent_refresh_command(due_dt)

    assert command is not None
    assert command.intent is RefreshIntent.DATA_REFRESH
    assert command.instance_uuid == instance.instance_uuid
    assert command.source is CommandSource.BACKGROUND


def test_queued_display_live_refresh_is_canceled_if_policy_is_disabled(
    monkeypatch,
):
    task, device_config, _instance, current_dt, _data_success, _anchor = (
        _live_radar_runtime("live-radar-policy-disabled-before-execution")
    )
    monkeypatch.setattr(
        "src.refresh_task.get_plugin_instance",
        lambda _config: FakePlugin(
            [],
            live_state={"active": True, "interval_seconds": 60},
        ),
    )
    monkeypatch.setattr(
        task,
        "_resource_sample",
        lambda: ResourceSample(available_mb=512, swap_percent=0),
    )
    monkeypatch.setattr(task, "_get_current_datetime", lambda: current_dt)
    command = task._select_independent_refresh_command(current_dt)
    assert command is not None
    assert command.intent is RefreshIntent.LIVE_REFRESH
    assert command.payload.get("background_live_refresh") is not True

    device_config.config["display_triggered_refresh_enabled"] = False
    monkeypatch.setattr(
        "src.refresh_task.get_plugin_instance",
        lambda _config: pytest.fail("disabled live refresh instantiated plugin"),
    )
    submitted = task.refresh_queue.submit(command)
    task._process_queue_entry(task.refresh_queue.take(timeout=0))
    result = task.refresh_queue.get_entry(submitted.id).job

    assert result.status is JobStatus.CANCELED
    assert result.error_code == "stale_selection"


def test_live_radar_live_lane_never_targets_a_non_displayed_instance(monkeypatch):
    task, _device_config, _instance, current_dt, _data_success, _anchor = _live_radar_runtime(
        "live-radar-exact-display-only"
    )
    task.runtime_state.set_display_state(
        "committed",
        instance_uuid="different-instance",
        changed_at=current_dt.isoformat(),
    )
    monkeypatch.setattr(
        "src.refresh_task.get_plugin_instance",
        lambda _config: FakePlugin(
            [],
            live_state={"active": True, "interval_seconds": 60},
        ),
    )
    monkeypatch.setattr(
        task,
        "_resource_sample",
        lambda: ResourceSample(available_mb=512, swap_percent=0),
    )

    assert task._select_independent_refresh_command(current_dt) is None


def _seed_theme_last_good(task, instance, mode, succeeded_at):
    task.runtime_state.record_success(
        instance.instance_uuid,
        succeeded_at.isoformat(),
        lane=RefreshLane.DATA,
        last_good_cache=LastGoodCacheState(
            theme_mode=mode,
            structural_generation=instance.structural_generation,
            settings_revision=instance.settings_revision,
            promoted_at=succeeded_at.isoformat(),
        ),
    )


def _prepare_independent_theme_candidate(task, playlist, current_dt):
    snapshots = [instance.snapshot() for instance in playlist.plugins]
    for instance in snapshots:
        _write_runtime_theme_cache(task, instance, "day")
        _seed_theme_last_good(
            task,
            instance,
            "day",
            current_dt - timedelta(minutes=10),
        )
    return snapshots[0]


def test_theme_redraw_is_cache_refresh_intent_not_display_intent(monkeypatch):
    task, _device_config, playlist, _configs = _theme_transition_runtime(
        "independent-theme-intent"
    )
    current_dt = datetime(2026, 7, 11, 22, 0, tzinfo=timezone.utc)
    instance = _prepare_independent_theme_candidate(task, playlist, current_dt)
    monkeypatch.setattr(
        task,
        "_resource_sample",
        lambda: ResourceSample(available_mb=512, swap_percent=0),
    )

    command = task._select_independent_refresh_command(current_dt)

    assert command.kind is CommandKind.CACHE_REFRESH
    assert command.intent is RefreshIntent.THEME_REDRAW
    assert command.source is CommandSource.SCHEDULER
    assert command.instance_uuid == instance.instance_uuid
    assert command.force is False


def test_theme_redraw_updates_cache_without_rewriting_the_current_screen_by_default(
    monkeypatch,
):
    task, _device_config, playlist, configs = _theme_transition_runtime(
        "independent-theme-lane-clocks"
    )
    current_dt = datetime(2026, 7, 11, 22, 0, tzinfo=timezone.utc)
    instance = _prepare_independent_theme_candidate(task, playlist, current_dt)
    live_success = current_dt - timedelta(minutes=9)
    task.runtime_state.record_success(
        instance.instance_uuid,
        live_success.isoformat(),
        lane=RefreshLane.LIVE,
    )
    before = task.runtime_state.snapshot().instances[instance.instance_uuid]
    plugin = ThemeOnlyRecordingPlugin(configs["displayed"], color="white")
    monkeypatch.setattr("src.refresh_task.get_plugin_instance", lambda _config: plugin)
    monkeypatch.setattr(
        task,
        "_resource_sample",
        lambda: ResourceSample(available_mb=512, swap_percent=0),
    )
    monkeypatch.setattr(task, "_get_current_datetime", lambda: current_dt)
    task.device_config.config["display_triggered_refresh_enabled"] = False
    command = task._select_independent_refresh_command(current_dt)

    submitted = task.refresh_queue.submit(command)
    task._process_queue_entry(task.refresh_queue.take(timeout=0))
    after = task.runtime_state.snapshot().instances[instance.instance_uuid]

    assert task.refresh_queue.get_entry(submitted.id).job.status is JobStatus.SUCCEEDED
    assert command.payload["theme_render_only"] is True
    assert before.data.last_success_at == after.data.last_success_at
    assert before.live.last_success_at == after.live.last_success_at
    assert after.theme.last_success_at == current_dt.isoformat()
    assert after.last_good_cache.theme_mode == "night"
    assert task.refresh_queue.take(timeout=0) is None


def test_theme_exact_followup_does_not_merge_with_pending_manual_display(
    monkeypatch,
):
    task, _device_config, playlist, configs = _theme_transition_runtime(
        "independent-theme-exact-followup-scope"
    )
    current_dt = datetime(2026, 7, 11, 22, 0, tzinfo=timezone.utc)
    instance = _prepare_independent_theme_candidate(task, playlist, current_dt)
    plugin = ThemeOnlyRecordingPlugin(configs["displayed"], color="white")
    monkeypatch.setattr("src.refresh_task.get_plugin_instance", lambda _config: plugin)
    monkeypatch.setattr(
        task,
        "_resource_sample",
        lambda: ResourceSample(available_mb=512, swap_percent=0),
    )
    monkeypatch.setattr(task, "_get_current_datetime", lambda: current_dt)
    theme = task._select_independent_refresh_command(current_dt)
    task.refresh_queue.submit(theme)
    running_theme = task.refresh_queue.take(timeout=0)
    manual = task._playlist_command(
        playlist.name,
        instance,
        source=CommandSource.MANUAL,
        intent=RefreshIntent.DISPLAY_CACHE,
        display_cached_only=True,
        priority=100,
        current_dt=current_dt,
        cache_theme_mode="day",
        require_active=False,
    )
    task.refresh_queue.submit(manual)

    task._process_queue_entry(running_theme)
    entries = [
        task.refresh_queue.take(timeout=0),
        task.refresh_queue.take(timeout=0),
    ]
    assert all(entry is not None for entry in entries)
    pending = [entry.command for entry in entries]
    exact = next(
        command for command in pending if command.source is CommandSource.SCHEDULER
    )
    retained_manual = next(
        command for command in pending if command.source is CommandSource.MANUAL
    )

    assert retained_manual.payload["require_active"] is False
    assert retained_manual.payload["cache_theme_mode"] == "day"
    assert exact.payload["cache_theme_mode"] == "night"
    assert exact.payload["resolved_theme_context"]["mode"] == "night"
    assert exact.payload["expected_displayed_instance_uuid"] == instance.instance_uuid
    assert exact.payload["preserve_rotation_anchor"] is True
    assert exact.coalescing_scope is not None
    assert exact.coalescing_scope != retained_manual.coalescing_scope


def test_theme_redraw_preserves_rotation_anchor_and_exact_displayed_no_fallback(
    monkeypatch,
):
    current_dt = datetime(2026, 7, 11, 22, 0, tzinfo=timezone.utc)
    task, device_config, playlist, configs = _theme_transition_runtime(
        "independent-theme-exact-display"
    )
    instance = _prepare_independent_theme_candidate(task, playlist, current_dt)
    anchor = device_config.refresh_info.refresh_time
    plugin = ThemeOnlyRecordingPlugin(configs["displayed"], color="white")
    monkeypatch.setattr("src.refresh_task.get_plugin_instance", lambda _config: plugin)
    monkeypatch.setattr(
        task,
        "_resource_sample",
        lambda: ResourceSample(available_mb=512, swap_percent=0),
    )
    monkeypatch.setattr(task, "_get_current_datetime", lambda: current_dt)
    command = task._select_independent_refresh_command(current_dt)

    assert command.payload["expected_displayed_instance_uuid"] == instance.instance_uuid
    task.refresh_queue.submit(command)
    task._process_queue_entry(task.refresh_queue.take(timeout=0))
    task._process_queue_entry(task.refresh_queue.take(timeout=0))
    assert device_config.refresh_info.refresh_time == anchor

    missing, missing_config, missing_playlist, _missing_configs = (
        _theme_transition_runtime(
            "independent-theme-no-refresh-info-fallback",
            displayed_uuid=None,
        )
    )
    _prepare_independent_theme_candidate(missing, missing_playlist, current_dt)
    monkeypatch.setattr(
        missing,
        "_resource_sample",
        lambda: ResourceSample(available_mb=512, swap_percent=0),
    )

    assert missing._select_independent_refresh_command(current_dt) is None
    assert missing_config.config["active_theme"] == "night"
    assert missing_config.write_count == 1


@pytest.mark.parametrize("source_mode", ["day", None])
def test_media_theme_redraw_reuses_opposite_or_legacy_uuid_cache_with_zero_provider_calls(
    monkeypatch,
    source_mode,
):
    task, device_config, playlist, configs = _theme_transition_runtime(
        f"independent-theme-media-{source_mode or 'legacy'}"
    )
    current_dt = datetime(2026, 7, 11, 22, 0, tzinfo=timezone.utc)
    instance = playlist.plugins[0].snapshot()
    configs["displayed"]["_manifest"] = _theme_manifest(
        "displayed",
        presentation="media",
    )
    device_config.get_resolution = lambda: (40, 24)
    source = Image.new("RGB", (40, 24), (180, 20, 30))
    source_path = Path(task._snapshot_cache_path(instance, source_mode))
    source_path.parent.mkdir(parents=True, exist_ok=True)
    source.save(source_path)
    _seed_theme_last_good(
        task,
        instance,
        source_mode,
        current_dt - timedelta(minutes=10),
    )
    fallback = playlist.plugins[1].snapshot()
    _write_runtime_theme_cache(task, fallback, "day")
    _seed_theme_last_good(
        task,
        fallback,
        "day",
        current_dt - timedelta(minutes=10),
    )
    provider_calls = []
    plugin = ThemeOnlyRecordingPlugin(configs["displayed"], fail=True)
    monkeypatch.setattr(
        "src.refresh_task.get_plugin_instance",
        lambda _config: provider_calls.append("provider") or plugin,
    )
    monkeypatch.setattr(
        task,
        "_resource_sample",
        lambda: ResourceSample(available_mb=512, swap_percent=0),
    )
    monkeypatch.setattr(task, "_get_current_datetime", lambda: current_dt)
    command = task._select_independent_refresh_command(current_dt)

    task.refresh_queue.submit(command)
    task._process_queue_entry(task.refresh_queue.take(timeout=0))
    task._process_queue_entry(task.refresh_queue.take(timeout=0))

    assert plugin.calls == []
    assert provider_calls == ["provider"]
    assert Path(task._snapshot_cache_path(instance, "night")).exists()
    assert device_config.refresh_info.refresh_time == "2026-07-11T21:59:00+00:00"


@pytest.mark.parametrize("source_mode", ["day", None])
def test_random_display_excludes_noncurrent_theme_rollback_without_consuming_bag(
    monkeypatch,
    source_mode,
):
    tmp_path = make_test_dir(
        f"nonvisible-{source_mode or 'unsuffixed'}-last-good"
    )
    plugin_data = _runtime_plugin_data("themed_plugin", "Themed Plugin")
    plugin_data["plugin_settings"]["themeMode"] = "auto"
    playlist = _runtime_playlist(plugin_data)
    task, device_config, _clock = _make_runtime_task(
        tmp_path,
        playlists=[playlist],
        cycle_seconds=300,
    )
    current_dt = datetime(2026, 7, 11, 22, 0, tzinfo=timezone.utc)
    device_config.config.update({"theme_mode": "night", "active_theme": "night"})
    device_config.refresh_info = RefreshInfo(
        refresh_type="Playlist",
        playlist=playlist.name,
        plugin_id="previous_plugin",
        plugin_instance="Previous Plugin",
        refresh_time=(current_dt - timedelta(minutes=10)).isoformat(),
        image_hash="previous",
    )
    device_config.get_plugin = lambda plugin_id: {
        "id": plugin_id,
        "_manifest": _theme_manifest(plugin_id),
    }
    task.runtime_state.set_display_state(
        "committed",
        instance_uuid="another-visible-instance",
        changed_at=current_dt.isoformat(),
    )
    instance = playlist.plugins[0].snapshot()
    _write_runtime_theme_cache(task, instance, source_mode)
    _seed_theme_last_good(
        task,
        instance,
        source_mode,
        current_dt - timedelta(minutes=10),
    )
    monkeypatch.setattr(
        "src.refresh_task.get_plugin_instance",
        lambda _config: pytest.fail("DISPLAY_CACHE instantiated a plugin"),
    )
    monkeypatch.setattr(task, "_get_current_datetime", lambda: current_dt)

    before_rotation = (
        playlist.current_plugin_index,
        list(playlist.plugin_rotation_queue),
        list(playlist.plugin_rotation_pool),
        list(playlist.plugin_rotation_recent_history),
    )

    command = task._select_cached_display_command(current_dt)

    assert command is None
    assert before_rotation == (
        playlist.current_plugin_index,
        list(playlist.plugin_rotation_queue),
        list(playlist.plugin_rotation_pool),
        list(playlist.plugin_rotation_recent_history),
    )
    assert not Path(task._snapshot_cache_path(instance, "night")).exists()


def _prepare_theme_catchup_runtime(name, *, active_theme="night"):
    task, device_config, playlist, configs = _theme_transition_runtime(name)
    current_dt = datetime(2026, 7, 11, 22, 0, tzinfo=timezone.utc)
    device_config.config["active_theme"] = active_theme
    _prepare_independent_theme_candidate(task, playlist, current_dt)
    return task, device_config, playlist, configs, current_dt


def _rotation_state(playlist):
    return (
        playlist.current_plugin_index,
        list(playlist.plugin_rotation_queue),
        list(playlist.plugin_rotation_pool),
        list(playlist.plugin_rotation_recent_history),
    )


def test_theme_catchup_waits_for_exact_displayed_transition_then_uses_no_rotation(
    monkeypatch,
):
    task, device_config, playlist, _configs, current_dt = (
        _prepare_theme_catchup_runtime(
            "theme-catchup-displayed-first",
            active_theme="day",
        )
    )
    monkeypatch.setattr(
        task,
        "_resource_sample",
        lambda: ResourceSample(available_mb=512, swap_percent=0),
    )
    before_rotation = _rotation_state(playlist)

    displayed = task._select_independent_refresh_command(current_dt)

    assert displayed.intent is RefreshIntent.THEME_REDRAW
    assert displayed.instance_uuid == playlist.plugins[0].instance_uuid
    assert task.runtime_state.snapshot().theme_catchup_admissions == ()
    assert _rotation_state(playlist) == before_rotation

    device_config.config["active_theme"] = "night"
    displayed_instance = playlist.plugins[0].snapshot()
    _write_runtime_theme_cache(task, displayed_instance, "night")
    before_admission = task._admission_state

    catchup = task._select_independent_refresh_command(current_dt)

    assert catchup.intent is RefreshIntent.THEME_CATCHUP
    assert catchup.kind is CommandKind.CACHE_REFRESH
    assert catchup.source is CommandSource.BACKGROUND
    assert catchup.instance_uuid == playlist.plugins[1].instance_uuid
    assert catchup.payload["theme_render_only"] is True
    assert catchup.payload["resolved_theme_context"]["mode"] == "night"
    assert "expected_displayed_instance_uuid" not in catchup.payload
    assert task._admission_state == before_admission
    assert _rotation_state(playlist) == before_rotation


def test_cache_only_theme_transition_commits_on_normal_display_then_catches_up(
    monkeypatch,
):
    task, device_config, playlist, configs = _theme_transition_runtime(
        "theme-catchup-cache-only-normal-display"
    )
    device_config.config["display_triggered_refresh_enabled"] = False
    configs["displayed"]["_manifest"] = _theme_manifest(
        "displayed",
        presentation="media",
    )
    configs["fallback"]["_manifest"] = _theme_manifest(
        "fallback",
        presentation="media",
    )
    device_config.get_resolution = lambda: (40, 24)
    current_dt = datetime(2026, 7, 11, 22, 0, tzinfo=timezone.utc)
    displayed, fallback = _prepare_independent_theme_candidate(
        task,
        playlist,
        current_dt,
    ), playlist.plugins[1].snapshot()
    playlist.plugin_rotation_pool = [
        displayed.instance_uuid,
        fallback.instance_uuid,
    ]
    playlist.plugin_rotation_queue = [
        displayed.instance_uuid,
        fallback.instance_uuid,
    ]
    playlist.plugin_rotation_recent_history = []
    for instance in (displayed, fallback):
        _write_runtime_theme_cache(
            task,
            instance,
            "day",
            Image.new("RGB", (40, 24), (180, 20, 30)),
        )
    device_config.refresh_info.refresh_time = (
        current_dt - timedelta(minutes=6)
    ).isoformat()
    monkeypatch.setattr(
        task,
        "_resource_sample",
        lambda: ResourceSample(available_mb=512, swap_percent=0),
    )
    monkeypatch.setattr(task, "_cache_refresh_under_resource_pressure", lambda: False)
    now = [current_dt]
    monkeypatch.setattr(task, "_get_current_datetime", lambda: now[0])
    plugins = {
        "displayed": ThemeOnlyRecordingPlugin(configs["displayed"], fail=True),
        "fallback": ThemeOnlyRecordingPlugin(configs["fallback"], fail=True),
    }
    monkeypatch.setattr(
        refresh_task_module,
        "get_plugin_instance",
        lambda config: plugins[config["id"]],
    )

    redraw = task._select_independent_refresh_command(now[0])
    assert redraw is not None
    assert redraw.intent is RefreshIntent.THEME_REDRAW
    redraw_result = _queue_and_process(task, redraw)
    assert redraw_result.job.status is JobStatus.SUCCEEDED
    assert Path(task._snapshot_cache_path(displayed, "night")).is_file()
    assert device_config.config["active_theme"] == "day"
    assert task.display_manager.calls == []
    assert task.refresh_queue.take(timeout=0) is None
    assert plugins["displayed"].calls == []

    assert task._select_independent_refresh_command(now[0]) is None
    monkeypatch.setattr(
        refresh_task_module,
        "get_plugin_instance",
        lambda _config: pytest.fail("DISPLAY_CACHE instantiated a plugin"),
    )
    assert task._select_cached_display_command(now[0]) is None
    now[0] += timedelta(
        seconds=refresh_task_module.DEFAULT_ROTATION_CACHE_RECOVERY_SECONDS + 1
    )
    display_command = task._select_cached_display_command(now[0])
    assert display_command is not None
    assert display_command.intent is RefreshIntent.DISPLAY_CACHE
    assert display_command.payload["theme_context"]["mode"] == "night"
    display_result = _queue_and_process(task, display_command)
    assert display_result.job.status is JobStatus.SUCCEEDED
    assert len(task.display_manager.calls) == 1
    assert device_config.config["active_theme"] == "night"
    anchor = device_config.refresh_info.refresh_time

    monkeypatch.setattr(
        refresh_task_module,
        "get_plugin_instance",
        lambda config: plugins[config["id"]],
    )
    now[0] += timedelta(seconds=1)
    catchup = task._select_independent_refresh_command(now[0])
    assert catchup is not None
    assert catchup.intent is RefreshIntent.THEME_CATCHUP
    assert catchup.instance_uuid == fallback.instance_uuid
    catchup_result = _queue_and_process(task, catchup)

    assert catchup_result.job.status is JobStatus.SUCCEEDED
    assert Path(task._snapshot_cache_path(fallback, "night")).is_file()
    assert plugins["fallback"].calls == []
    assert len(task.display_manager.calls) == 1
    assert device_config.refresh_info.refresh_time == anchor
    assert task.refresh_queue.take(timeout=0) is None


def test_theme_catchup_rebuilds_exact_cache_older_than_latest_data_success(
    monkeypatch,
):
    task, _device_config, playlist, configs, current_dt = (
        _prepare_theme_catchup_runtime("theme-catchup-stale-exact-cache")
    )
    monkeypatch.setattr(
        task,
        "_resource_sample",
        lambda: ResourceSample(available_mb=512, swap_percent=0),
    )
    monkeypatch.setattr(task, "_cache_refresh_under_resource_pressure", lambda: False)
    monkeypatch.setattr(task, "_get_current_datetime", lambda: current_dt)

    displayed = playlist.plugins[0].snapshot()
    target = playlist.plugins[1].snapshot()
    displayed_night = _write_runtime_theme_cache(task, displayed, "night")
    stale_target_night = _write_runtime_theme_cache(
        task,
        target,
        "night",
        Image.new("RGB", (32, 16), "black"),
    )
    fresh_cache_time = (current_dt - timedelta(minutes=5)).timestamp()
    stale_cache_time = (current_dt - timedelta(days=1)).timestamp()
    os.utime(displayed_night, (fresh_cache_time, fresh_cache_time))
    os.utime(stale_target_night, (stale_cache_time, stale_cache_time))

    plugin = ThemeOnlyRecordingPlugin(configs["fallback"], color="white")
    monkeypatch.setattr("src.refresh_task.get_plugin_instance", lambda _config: plugin)

    command = task._select_independent_refresh_command(current_dt)

    assert command.intent is RefreshIntent.THEME_CATCHUP
    assert command.instance_uuid == target.instance_uuid
    submitted = task.refresh_queue.submit(command)
    task._process_queue_entry(task.refresh_queue.take(timeout=0))

    assert task.refresh_queue.get_entry(submitted.id).job.status is JobStatus.SUCCEEDED
    assert len(plugin.calls) == 1
    assert plugin.calls[0]["theme_render_only"] is True
    with Image.open(stale_target_night) as refreshed:
        assert refreshed.getpixel((16, 8)) == (255, 255, 255)


def test_theme_redraw_command_is_display_guarded_but_catchup_is_not():
    task, _device_config, playlist, _configs, current_dt = (
        _prepare_theme_catchup_runtime("theme-command-display-guard")
    )
    instance = playlist.plugins[0].snapshot()

    redraw = task._playlist_command(
        playlist.name,
        instance,
        source=CommandSource.SCHEDULER,
        intent=RefreshIntent.THEME_REDRAW,
        kind=CommandKind.CACHE_REFRESH,
        theme_render_only=True,
        current_dt=current_dt,
    )
    catchup = task._playlist_command(
        playlist.name,
        instance,
        source=CommandSource.BACKGROUND,
        intent=RefreshIntent.THEME_CATCHUP,
        kind=CommandKind.CACHE_REFRESH,
        theme_render_only=True,
        current_dt=current_dt,
    )

    assert redraw.payload["expected_displayed_instance_uuid"] == (
        instance.instance_uuid
    )
    assert "expected_displayed_instance_uuid" not in catchup.payload


def test_theme_catchup_admits_one_per_probe_and_two_per_rolling_minute(monkeypatch):
    task, _device_config, playlist, _configs, current_dt = (
        _prepare_theme_catchup_runtime("theme-catchup-bounds")
    )
    monkeypatch.setattr(
        task,
        "_resource_sample",
        lambda: ResourceSample(available_mb=512, swap_percent=0),
    )
    before_rotation = _rotation_state(playlist)
    before_admission = task._admission_state

    first = task._select_independent_refresh_command(current_dt)
    second = task._select_independent_refresh_command(current_dt)
    limited = task._select_independent_refresh_command(current_dt)

    assert first.intent is RefreshIntent.THEME_CATCHUP
    assert second.intent is RefreshIntent.THEME_CATCHUP
    assert first.instance_uuid != second.instance_uuid
    assert limited is None
    assert len(task.runtime_state.snapshot().theme_catchup_admissions) == 2
    assert task._admission_state == before_admission
    assert _rotation_state(playlist) == before_rotation


@pytest.mark.parametrize(
    "sample",
    [
        ResourceSample(available_mb=100, swap_percent=0),
        ResourceSample(available_mb=50, swap_percent=0),
    ],
)
def test_theme_catchup_is_not_admitted_under_soft_or_hard_pressure(
    monkeypatch,
    sample,
):
    task, _device_config, _playlist, _configs, current_dt = (
        _prepare_theme_catchup_runtime(
            f"theme-catchup-pressure-{sample.available_mb}"
        )
    )
    monkeypatch.setattr(task, "_resource_sample", lambda: sample)

    assert task._select_independent_refresh_command(current_dt) is None
    assert task.runtime_state.snapshot().theme_catchup_admissions == ()


def test_theme_catchup_never_displaces_an_ordinary_data_candidate(monkeypatch):
    task, _device_config, playlist, _configs, current_dt = (
        _prepare_theme_catchup_runtime("theme-catchup-data-first")
    )
    due = playlist.plugins[0].snapshot()
    task.runtime_state.record_success(
        due.instance_uuid,
        (current_dt - timedelta(hours=2)).isoformat(),
        lane=RefreshLane.DATA,
    )
    monkeypatch.setattr(
        task,
        "_resource_sample",
        lambda: ResourceSample(available_mb=512, swap_percent=0),
    )

    command = task._select_independent_refresh_command(current_dt)

    assert command.intent is RefreshIntent.DATA_REFRESH
    assert command.instance_uuid == due.instance_uuid
    assert task.runtime_state.snapshot().theme_catchup_admissions == ()


@pytest.mark.parametrize(
    "ineligible_reason",
    ["fixed", "theme-unaware", "missing-config", "background-disabled"],
)
def test_theme_catchup_skips_ineligible_instances(
    monkeypatch,
    ineligible_reason,
):
    task, device_config, playlist, configs, current_dt = (
        _prepare_theme_catchup_runtime(
            f"theme-catchup-ineligible-{ineligible_reason}"
        )
    )
    displayed = playlist.plugins[0].snapshot()
    target = playlist.plugins[1]
    _write_runtime_theme_cache(task, displayed, "night")
    if ineligible_reason == "fixed":
        target.settings["themeMode"] = "day"
    elif ineligible_reason == "theme-unaware":
        configs["fallback"]["_manifest"] = _theme_manifest(
            "fallback",
            supported=False,
        )
    elif ineligible_reason == "missing-config":
        device_config.get_plugin = lambda plugin_id: configs.get(plugin_id)
        configs.pop("fallback")
    else:
        monkeypatch.setattr(
            task,
            "_snapshot_background_cache_disabled",
            lambda instance: instance.instance_uuid == target.instance_uuid,
        )
    monkeypatch.setattr(
        task,
        "_resource_sample",
        lambda: ResourceSample(available_mb=512, swap_percent=0),
    )
    before_rotation = _rotation_state(playlist)

    assert task._select_independent_refresh_command(current_dt) is None
    assert task.runtime_state.snapshot().theme_catchup_admissions == ()
    assert _rotation_state(playlist) == before_rotation


def _one_pending_theme_catchup(task, playlist, current_dt):
    displayed = playlist.plugins[0].snapshot()
    _write_runtime_theme_cache(task, displayed, "night")
    command = task._select_independent_refresh_command(current_dt)
    assert command.instance_uuid == playlist.plugins[1].instance_uuid
    return command, playlist.plugins[1].snapshot()


def _refresh_lane_state(state):
    return (
        state.data,
        state.live,
        state.theme,
        state.presentation,
        state.last_good_cache,
        state.presentation_request,
        state.presentation_receipt,
    )


def test_theme_catchup_failure_uses_only_persisted_catchup_cooldown(monkeypatch):
    task, device_config, playlist, configs, current_dt = (
        _prepare_theme_catchup_runtime("theme-catchup-failure-cooldown")
    )
    monkeypatch.setattr(
        task,
        "_resource_sample",
        lambda: ResourceSample(available_mb=512, swap_percent=0),
    )
    monkeypatch.setattr(task, "_cache_refresh_under_resource_pressure", lambda: False)
    monkeypatch.setattr(task, "_get_current_datetime", lambda: current_dt)
    plugin = ThemeOnlyRecordingPlugin(configs["fallback"], fail=True)
    monkeypatch.setattr("src.refresh_task.get_plugin_instance", lambda _config: plugin)
    command, target = _one_pending_theme_catchup(task, playlist, current_dt)
    before = task.runtime_state.snapshot().instances[target.instance_uuid]
    anchor = device_config.refresh_info.refresh_time

    submitted = task.refresh_queue.submit(command)
    task._process_queue_entry(task.refresh_queue.take(timeout=0))
    after = task.runtime_state.snapshot().instances[target.instance_uuid]

    assert task.refresh_queue.get_entry(submitted.id).job.status is JobStatus.FAILED
    assert _refresh_lane_state(after) == _refresh_lane_state(before)
    assert after.theme_catchup.target_mode == "night"
    assert after.theme_catchup.last_failure_at == current_dt.isoformat()
    assert after.theme_catchup.next_retry_at is not None
    assert device_config.refresh_info.refresh_time == anchor
    assert task.display_manager.calls == []
    assert not Path(task._snapshot_cache_path(target, "night")).exists()
    assert task._select_independent_refresh_command(
        current_dt + timedelta(seconds=1)
    ) is None


def test_theme_catchup_noncacheable_result_is_failure_not_success(monkeypatch):
    task, _device_config, playlist, configs, current_dt = (
        _prepare_theme_catchup_runtime("theme-catchup-noncacheable")
    )
    monkeypatch.setattr(
        task,
        "_resource_sample",
        lambda: ResourceSample(available_mb=512, swap_percent=0),
    )
    monkeypatch.setattr(task, "_cache_refresh_under_resource_pressure", lambda: False)
    monkeypatch.setattr(task, "_get_current_datetime", lambda: current_dt)

    class NoncacheableThemePlugin(ThemeOnlyRecordingPlugin):
        def render_themed_image(self, *args, **kwargs):
            image = super().render_themed_image(*args, **kwargs)
            image.info[refresh_task_module.SKIP_CACHE_IMAGE_INFO_KEY] = True
            return image

    plugin = NoncacheableThemePlugin(configs["fallback"])
    monkeypatch.setattr("src.refresh_task.get_plugin_instance", lambda _config: plugin)
    command, target = _one_pending_theme_catchup(task, playlist, current_dt)

    submitted = task.refresh_queue.submit(command)
    task._process_queue_entry(task.refresh_queue.take(timeout=0))
    state = task.runtime_state.snapshot().instances[target.instance_uuid]

    assert task.refresh_queue.get_entry(submitted.id).job.status is JobStatus.FAILED
    assert state.theme_catchup.next_retry_at is not None
    assert not Path(task._snapshot_cache_path(target, "night")).exists()


def test_theme_catchup_rechecks_pressure_without_false_success(monkeypatch):
    task, _device_config, playlist, configs, current_dt = (
        _prepare_theme_catchup_runtime("theme-catchup-pressure-recheck")
    )
    monkeypatch.setattr(
        task,
        "_resource_sample",
        lambda: ResourceSample(available_mb=512, swap_percent=0),
    )
    monkeypatch.setattr(task, "_get_current_datetime", lambda: current_dt)
    plugin = ThemeOnlyRecordingPlugin(configs["fallback"])
    monkeypatch.setattr("src.refresh_task.get_plugin_instance", lambda _config: plugin)
    command, target = _one_pending_theme_catchup(task, playlist, current_dt)
    before = task.runtime_state.snapshot().instances[target.instance_uuid]
    monkeypatch.setattr(task, "_cache_refresh_under_resource_pressure", lambda: True)

    submitted = task.refresh_queue.submit(command)
    task._process_queue_entry(task.refresh_queue.take(timeout=0))
    entry = task.refresh_queue.get_entry(submitted.id)
    after = task.runtime_state.snapshot().instances[target.instance_uuid]

    assert entry.job.status is JobStatus.CANCELED
    assert entry.job.error_code == "cache_unavailable"
    assert plugin.calls == []
    assert _refresh_lane_state(after) == _refresh_lane_state(before)
    assert after.theme_catchup.next_retry_at is None
    assert not Path(task._snapshot_cache_path(target, "night")).exists()


def test_theme_catchup_media_success_is_provider_free_and_side_effect_free(
    monkeypatch,
):
    task, device_config, playlist, configs, current_dt = (
        _prepare_theme_catchup_runtime("theme-catchup-provider-free")
    )
    configs["fallback"]["_manifest"] = _theme_manifest(
        "fallback",
        presentation="media",
    )
    device_config.get_resolution = lambda: (40, 24)
    target = playlist.plugins[1].snapshot()
    _write_runtime_theme_cache(
        task,
        target,
        "day",
        Image.new("RGB", (40, 24), (180, 20, 30)),
    )
    monkeypatch.setattr(
        task,
        "_resource_sample",
        lambda: ResourceSample(available_mb=512, swap_percent=0),
    )
    monkeypatch.setattr(task, "_cache_refresh_under_resource_pressure", lambda: False)
    monkeypatch.setattr(task, "_get_current_datetime", lambda: current_dt)
    plugin = ThemeOnlyRecordingPlugin(configs["fallback"], fail=True)
    monkeypatch.setattr("src.refresh_task.get_plugin_instance", lambda _config: plugin)
    before_rotation = _rotation_state(playlist)
    before_display = task.runtime_state.snapshot()
    anchor = device_config.refresh_info.refresh_time
    command, target = _one_pending_theme_catchup(task, playlist, current_dt)
    before = task.runtime_state.snapshot().instances[target.instance_uuid]

    submitted = task.refresh_queue.submit(command)
    task._process_queue_entry(task.refresh_queue.take(timeout=0))
    after_snapshot = task.runtime_state.snapshot()
    after = after_snapshot.instances[target.instance_uuid]

    assert task.refresh_queue.get_entry(submitted.id).job.status is JobStatus.SUCCEEDED
    assert plugin.calls == []
    assert Path(task._snapshot_cache_path(target, "night")).exists()
    assert _refresh_lane_state(after) == _refresh_lane_state(before)
    assert after.last_good_cache.theme_mode == "day"
    assert after_snapshot.display_state == before_display.display_state
    assert after_snapshot.display_commit_id == before_display.display_commit_id
    assert after_snapshot.displayed_instance_uuid == before_display.displayed_instance_uuid
    assert device_config.refresh_info.refresh_time == anchor
    assert task.display_manager.calls == []
    assert task.refresh_queue.take(timeout=0) is None
    assert _rotation_state(playlist) == before_rotation


def test_theme_catchup_revision_change_during_render_cancels_without_promotion(
    monkeypatch,
):
    task, _device_config, playlist, configs, current_dt = (
        _prepare_theme_catchup_runtime("theme-catchup-stale-revision")
    )
    monkeypatch.setattr(
        task,
        "_resource_sample",
        lambda: ResourceSample(available_mb=512, swap_percent=0),
    )
    monkeypatch.setattr(task, "_cache_refresh_under_resource_pressure", lambda: False)
    monkeypatch.setattr(task, "_get_current_datetime", lambda: current_dt)
    command, target = _one_pending_theme_catchup(task, playlist, current_dt)
    before = task.runtime_state.snapshot().instances[target.instance_uuid]

    class RevisionChangingPlugin(ThemeOnlyRecordingPlugin):
        def render_themed_image(self, *args, **kwargs):
            image = super().render_themed_image(*args, **kwargs)
            task.device_config.get_playlist_manager().update_plugin_instance(
                target.instance_uuid,
                settings={"id": "changed", "themeMode": "auto"},
                expected_generation=target.structural_generation,
                expected_settings_revision=target.settings_revision,
            )
            return image

    plugin = RevisionChangingPlugin(configs["fallback"])
    monkeypatch.setattr("src.refresh_task.get_plugin_instance", lambda _config: plugin)

    submitted = task.refresh_queue.submit(command)
    task._process_queue_entry(task.refresh_queue.take(timeout=0))
    after = task.runtime_state.snapshot().instances[target.instance_uuid]

    job = task.refresh_queue.get_entry(submitted.id).job
    assert job.status is JobStatus.CANCELED
    assert job.error_code == "stale_selection"
    assert _refresh_lane_state(after) == _refresh_lane_state(before)
    assert not Path(task._snapshot_cache_path(target, "night")).exists()
    assert not Path(task._staging_cache_path(target, "night")).exists()


def test_theme_failure_cools_theme_lane_only_and_keeps_last_good(monkeypatch):
    task, _device_config, playlist, configs = _theme_transition_runtime(
        "independent-theme-failure-lane"
    )
    current_dt = datetime(2026, 7, 11, 22, 0, tzinfo=timezone.utc)
    instance = _prepare_independent_theme_candidate(task, playlist, current_dt)
    live_success = current_dt - timedelta(minutes=9)
    task.runtime_state.record_success(
        instance.instance_uuid,
        live_success.isoformat(),
        lane=RefreshLane.LIVE,
    )
    before = task.runtime_state.snapshot().instances[instance.instance_uuid]
    plugin = ThemeOnlyRecordingPlugin(configs["displayed"], fail=True)
    monkeypatch.setattr("src.refresh_task.get_plugin_instance", lambda _config: plugin)
    monkeypatch.setattr(
        task,
        "_resource_sample",
        lambda: ResourceSample(available_mb=512, swap_percent=0),
    )
    monkeypatch.setattr(task, "_get_current_datetime", lambda: current_dt)
    command = task._select_independent_refresh_command(current_dt)

    submitted = task.refresh_queue.submit(command)
    task._process_queue_entry(task.refresh_queue.take(timeout=0))
    failed = task.runtime_state.snapshot().instances[instance.instance_uuid]

    assert task.refresh_queue.get_entry(submitted.id).job.status is JobStatus.FAILED
    assert failed.data.last_success_at == before.data.last_success_at
    assert failed.live.last_success_at == before.live.last_success_at
    assert failed.theme.next_retry_at is not None
    assert failed.last_good_cache == before.last_good_cache

    task.runtime_state.record_success(
        instance.instance_uuid,
        (current_dt - timedelta(hours=2)).isoformat(),
        lane=RefreshLane.DATA,
    )
    next_command = task._select_independent_refresh_command(
        current_dt + timedelta(seconds=1)
    )
    assert next_command.intent is RefreshIntent.DATA_REFRESH


def test_startup_seeds_data_clock_from_valid_model_latest_refresh_only(monkeypatch):
    tmp_path = make_test_dir("startup-data-clock-seed")
    valid_time = "2026-07-11T20:00:00+00:00"
    playlist = _runtime_playlist(
        _runtime_plugin_data(
            "valid_latest",
            "Valid Latest",
            latest_refresh_time=valid_time,
        ),
        _runtime_plugin_data(
            "invalid_latest",
            "Invalid Latest",
            latest_refresh_time="not-a-timestamp",
        ),
        _runtime_plugin_data(
            "missing_latest",
            "Missing Latest",
            latest_refresh_time=None,
        ),
    )
    task, _device_config, _clock = _make_runtime_task(tmp_path, playlists=[playlist])
    monkeypatch.setattr(task.runtime_state, "flush", lambda: True)

    task._prune_runtime_state()
    states = task.runtime_state.snapshot().instances

    assert states[playlist.plugins[0].instance_uuid].data.last_success_at == valid_time
    assert states.get(playlist.plugins[1].instance_uuid) is None
    assert states.get(playlist.plugins[2].instance_uuid) is None


def test_startup_discovers_only_valid_exact_revision_last_good_cache(monkeypatch):
    tmp_path = make_test_dir("startup-last-good-discovery")
    playlist = _runtime_playlist(
        _runtime_plugin_data("valid_cache", "Valid Cache"),
        _runtime_plugin_data("invalid_cache", "Invalid Cache"),
    )
    task, _device_config, _clock = _make_runtime_task(tmp_path, playlists=[playlist])
    valid = playlist.plugins[0].snapshot()
    invalid = playlist.plugins[1].snapshot()
    valid_path = Path(task._snapshot_cache_path(valid, "day"))
    valid_path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (2, 1), "white").save(valid_path)
    corrupt_current = Path(task._snapshot_cache_path(invalid, "night"))
    corrupt_current.write_bytes(b"not-an-image")
    stale_path = Path(
        authoritative_cache_path(
            task.cache_catalog.cache_root,
            invalid.instance_uuid,
            invalid.structural_generation,
            invalid.settings_revision + 1,
            "day",
        )
    )
    Image.new("RGB", (2, 1), "black").save(stale_path)
    monkeypatch.setattr(task.runtime_state, "flush", lambda: True)

    task._prune_runtime_state()
    states = task.runtime_state.snapshot().instances

    assert states[valid.instance_uuid].last_good_cache.theme_mode == "day"
    assert states[valid.instance_uuid].last_good_cache.structural_generation == 1
    assert states[valid.instance_uuid].last_good_cache.settings_revision == 1
    assert states[invalid.instance_uuid].last_good_cache is None


def test_startup_migration_does_not_write_playlist_or_user_settings(monkeypatch):
    tmp_path = make_test_dir("startup-migration-read-only-config")
    plugin_data = _runtime_plugin_data(
        "migration_target",
        "Migration Target",
        latest_refresh_time="2026-07-11T20:00:00+00:00",
    )
    plugin_data["plugin_settings"].update({"city": "Seattle", "units": "metric"})
    playlist = _runtime_playlist(plugin_data)
    task, device_config, _clock = _make_runtime_task(tmp_path, playlists=[playlist])
    instance = playlist.plugins[0].snapshot()
    _write_runtime_cache(task, instance)
    before_manager = device_config.get_playlist_manager().to_dict()
    before_config = dict(device_config.config)
    monkeypatch.setattr(task.runtime_state, "flush", lambda: True)

    task._prune_runtime_state()

    assert device_config.write_count == 0
    assert device_config.get_playlist_manager().to_dict() == before_manager
    assert device_config.config == before_config


PRESENTATION_NOW = datetime(2026, 7, 12, 12, 0, tzinfo=timezone.utc)


def test_presentation_instance_identity_rejects_missing_and_json_spoofed_values():
    reserved_key = presentation_contract._PRESENTATION_INSTANCE_IDENTITY_KEY
    spoofed = json.loads(
        json.dumps(
            {
                reserved_key: {
                    "instance_uuid": "json-controlled-instance",
                }
            }
        )
    )

    assert presentation_contract.get_presentation_instance_uuid({}) is None
    assert presentation_contract.get_presentation_instance_uuid(spoofed) is None


def test_presentation_instance_identity_binding_overwrites_spoof_without_mutation():
    reserved_key = presentation_contract._PRESENTATION_INSTANCE_IDENTITY_KEY
    instance_uuid = "trusted-playlist-instance"
    original = {
        "city": "Fremont",
        reserved_key: "json-spoof",
    }
    before = dict(original)

    bound = presentation_contract.bind_presentation_instance_identity(
        original,
        instance_uuid,
    )

    assert bound is not original
    assert original == before
    assert bound["city"] == "Fremont"
    assert bound[reserved_key] != "json-spoof"
    assert presentation_contract.get_presentation_instance_uuid(bound) == instance_uuid
    assert instance_uuid not in repr(bound[reserved_key])


@pytest.mark.parametrize("instance_uuid", [None, "", "   ", " padded-instance "])
def test_presentation_instance_identity_binding_rejects_invalid_uuid(instance_uuid):
    with pytest.raises((TypeError, ValueError), match="instance_uuid"):
        presentation_contract.bind_presentation_instance_identity({}, instance_uuid)


def _presentation_manifest(
    plugin_id="presentation_plugin",
    *,
    provider_free=False,
    provider_refresh=False,
    supports_theme=False,
):
    return PluginManifest(
        schema_version=2,
        id=plugin_id,
        class_name="PresentationPlugin",
        display_name="Presentation Plugin",
        refresh_on_display=True,
        capabilities=PluginCapabilities(
            supports_presentation_refresh=True,
            presentation_refresh_is_provider_free=provider_free,
            allows_display_triggered_provider_refresh=provider_refresh,
            supports_day_night_theme=supports_theme,
        ),
        raw={},
    )


class PresentationTransactionDisplayManager:
    def __init__(self, *, after_display=None, hardware_written=True):
        self.calls = []
        self.bound_runtime_state = None
        self.after_display = after_display
        self.hardware_written = hardware_written

    def bind_runtime_state(self, runtime_state):
        self.bound_runtime_state = runtime_state
        return object()

    def display_image(
        self,
        image,
        image_settings=(),
        *,
        task_context=None,
        logical_target=None,
        instance_revision=None,
        force_hardware_write=False,
    ):
        commit_id = uuid.uuid4().hex
        committed_at = PRESENTATION_NOW.isoformat()
        call = {
            "commit_id": commit_id,
            "committed_at": committed_at,
            "image": image.copy(),
            "image_settings": tuple(image_settings),
            "task_context": task_context,
            "logical_target": dict(logical_target or {}),
            "instance_revision": instance_revision,
            "force_hardware_write": force_hardware_write,
        }
        self.calls.append(call)
        if self.bound_runtime_state is not None:
            self.bound_runtime_state.set_display_state(
                "committed",
                commit_id,
                instance_uuid=call["logical_target"].get("instance_uuid"),
                changed_at=committed_at,
            )
        if self.after_display is not None:
            self.after_display(self, call)
        return SimpleNamespace(
            commit_id=commit_id,
            committed_at=committed_at,
            hardware_written=self.hardware_written,
        )


class PresentationBankPlugin(DelegatingThemeWrapper):
    def __init__(self, *, changed=True, prepared_color="white", data_color="gray"):
        self.changed = changed
        self.prepared_color = prepared_color
        self.data_color = data_color
        self.events = []
        self.contexts = []
        self.identity_events = []
        self.config = {}

    def presentation_mode(self, settings):
        self.identity_events.append(
            ("mode", presentation_contract.get_presentation_instance_uuid(settings))
        )
        self.events.append(("mode", dict(settings or {})))
        return PresentationMode.PREPARED_BANK

    def reconcile_presentation_receipt(self, settings, receipt):
        self.identity_events.append(
            ("reconcile", presentation_contract.get_presentation_instance_uuid(settings))
        )
        self.events.append(("reconcile", receipt))

    def prepare_presentation(
        self,
        settings,
        device_config,
        *,
        request,
        resolved_theme_context,
    ):
        assert isinstance(request, PresentationRequestContext)
        self.identity_events.append(
            ("prepare", presentation_contract.get_presentation_instance_uuid(settings))
        )
        self.events.append(("prepare", request.request_id))
        self.contexts.append(request)
        image = Image.new("RGB", (32, 16), self.prepared_color) if self.changed else None
        return PresentationPreparation(
            request_id=request.request_id,
            image=image,
            changed=self.changed,
        )

    def generate_image(self, settings, device_config):
        self.identity_events.append(
            ("generate", presentation_contract.get_presentation_instance_uuid(settings))
        )
        self.events.append(("generate", dict(settings or {})))
        return Image.new("RGB", (32, 16), self.data_color)


class RefreshOnDisplayRerenderPlugin(RefreshOnDisplayPresentationMixin, BasePlugin):
    def __init__(self, calls):
        self.config = {"id": "presentation_plugin_0", "refresh_on_display": True}
        self.calls = calls

    def generate_image(self, settings, device_config):
        self.calls.append(dict(settings or {}))
        return attach_source_provenance(
            Image.new("RGB", (32, 16), "white"),
            SourceProvenance.LIVE,
        )


class UnattestedRefreshOnDisplayPlugin(RefreshOnDisplayPresentationMixin, BasePlugin):
    def __init__(self):
        self.config = {"id": "unattested", "refresh_on_display": True}

    def generate_image(self, settings, device_config):
        return Image.new("RGB", (32, 16), "white")


class BaseCopyIdentityPlugin(BasePlugin):
    def __init__(self):
        self.config = {}
        self.events = []
        self.identity_events = []

    def resolve_theme(self, settings, device_config, now=None):
        return {"mode": "day"}

    def presentation_mode(self, settings):
        self.identity_events.append(
            ("mode", presentation_contract.get_presentation_instance_uuid(settings))
        )
        self.events.append(("mode", dict(settings or {})))
        return PresentationMode.PREPARED_BANK

    def reconcile_presentation_receipt(self, settings, receipt):
        self.identity_events.append(
            ("reconcile", presentation_contract.get_presentation_instance_uuid(settings))
        )
        self.events.append(("reconcile", receipt))

    def generate_image(self, settings, device_config):
        self.identity_events.append(
            ("generate", presentation_contract.get_presentation_instance_uuid(settings))
        )
        self.events.append(("generate", dict(settings or {})))
        return Image.new("RGB", (32, 16), "gray")


class NoChangePresentationPlugin(PresentationBankPlugin):
    def presentation_mode(self, settings):
        self.events.append(("mode", dict(settings or {})))
        return PresentationMode.NO_CHANGE

    def prepare_presentation(self, *args, **kwargs):
        pytest.fail("NO_CHANGE must not call the preparation hook")


class LegacyPresentationPlugin(PresentationBankPlugin):
    def presentation_mode(self, settings):
        self.events.append(("mode", dict(settings or {})))
        return PresentationMode.LEGACY_ASYNC

    def prepare_presentation(self, *args, **kwargs):
        pytest.fail("LEGACY_ASYNC must remain disabled")


def _make_presentation_task(
    name,
    *,
    plugin_count=1,
    latest_refresh_time="2999-01-01T00:00:00+00:00",
    interval=3600,
    clock=None,
    display_manager=None,
    provider_free=False,
    provider_refresh=False,
    supports_theme=False,
):
    tmp_path = make_test_dir(name)
    plugins = [
        _runtime_plugin_data(
            f"presentation_plugin_{index}",
            f"Presentation Plugin {index}",
            latest_refresh_time=latest_refresh_time,
            interval=interval,
        )
        for index in range(plugin_count)
    ]
    for plugin in plugins:
        plugin["plugin_settings"]["refreshOnDisplay"] = True
    playlist = _runtime_playlist(*plugins, name="Presentation Playlist")
    clock = clock or RuntimeClock()
    device_config = RuntimeDeviceConfig(tmp_path, [playlist])
    device_config.config.update(
        {
            "active_theme": "day",
            "theme_mode": "day",
            "plugin_cycle_interval_seconds": 60,
            "display_triggered_refresh_enabled": True,
        }
    )
    manifests = {
        plugin["plugin_id"]: _presentation_manifest(
            plugin["plugin_id"],
            provider_free=provider_free,
            provider_refresh=provider_refresh,
            supports_theme=supports_theme,
        )
        for plugin in plugins
    }
    device_config.get_plugin = lambda plugin_id: {
        "id": plugin_id,
        "refresh_on_display": True,
        "_manifest": manifests[plugin_id],
    }
    display_manager = display_manager or PresentationTransactionDisplayManager()
    task = RefreshTask(
        device_config,
        display_manager,
        clock=clock.monotonic,
        wall_clock=clock.wall_time,
    )
    return task, device_config, clock, playlist, display_manager


def _install_display_provider_plugin_sentinels(monkeypatch):
    def plugin_sentinel(*_args, **_kwargs):
        pytest.fail("DISPLAY_CACHE instantiated a plugin")

    def provider_sentinel(*_args, **_kwargs):
        pytest.fail("DISPLAY_CACHE reached a provider/live hook")

    monkeypatch.setattr(refresh_task_module, "get_plugin_instance", plugin_sentinel)
    monkeypatch.setattr(
        refresh_task_module,
        "_plugin_live_refresh_state",
        provider_sentinel,
    )


def _seed_presentation_request(
    task,
    instance,
    *,
    request_id=None,
    requested_at=PRESENTATION_NOW,
    origin_commit_id="origin-display-commit",
    origin_theme_mode=None,
):
    request = PresentationRequestState(
        request_id=request_id or uuid.uuid4().hex,
        requested_at=requested_at.isoformat(),
        structural_generation=instance.structural_generation,
        settings_revision=instance.settings_revision,
        origin_theme_mode=origin_theme_mode,
        origin_display_commit_id=origin_commit_id,
    )
    assert task.runtime_state.request_presentation(instance.instance_uuid, request)
    return request


def _prepared_presentation_candidate(task, instance, request, theme_mode=None):
    root = Path(task.device_config.plugin_image_dir) / ".refresh-presentation"
    return PreparedPresentationCandidate(
        instance_uuid=instance.instance_uuid,
        structural_generation=instance.structural_generation,
        settings_revision=instance.settings_revision,
        theme_mode=theme_mode,
        request_id=request.request_id,
        cache_path=prepared_presentation_path(
            root,
            instance.instance_uuid,
            instance.structural_generation,
            instance.settings_revision,
            theme_mode,
            request.request_id,
        ),
    )


def _seed_prepared_presentation(
    task,
    instance,
    request,
    *,
    image=None,
    theme_mode=None,
):
    candidate = _prepared_presentation_candidate(
        task,
        instance,
        request,
        theme_mode,
    )
    PresentationCache(Path(task.device_config.plugin_image_dir) / ".refresh-presentation").save(
        candidate,
        image or Image.new("RGB", (32, 16), "white"),
    )
    assert task.runtime_state.mark_presentation_prepared(
        instance.instance_uuid,
        request.request_id,
        (PRESENTATION_NOW + timedelta(seconds=1)).isoformat(),
        theme_mode,
    )
    return candidate


def _non_presentation_lane_bytes(state):
    return json.dumps(
        {lane: getattr(state, lane).__dict__ for lane in ("data", "live", "theme")},
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _seed_independent_lane_clocks(task, instance):
    for lane, offset in (
        (RefreshLane.DATA, 10),
        (RefreshLane.LIVE, 20),
        (RefreshLane.THEME, 30),
    ):
        task.runtime_state.record_success(
            instance.instance_uuid,
            (PRESENTATION_NOW - timedelta(minutes=offset)).isoformat(),
            lane=lane,
        )


def _queue_and_process(task, command):
    submitted = task.refresh_queue.submit(command)
    entry = task.refresh_queue.take(timeout=0)
    assert entry is not None
    assert entry.job.id == submitted.id
    task._process_queue_entry(entry)
    return task.refresh_queue.get_entry(submitted.id)


@pytest.mark.parametrize(
    "first_sample",
    [
        IanResourceSample(available_mb=100, swap_percent=0),
        IanResourceSample(available_mb=None, swap_percent=None),
    ],
)
def test_ian_resource_deferral_keeps_background_job_running_then_executes_once(
    monkeypatch,
    first_sample,
):
    clock = RuntimeClock()
    samples = iter(
        (
            first_sample,
            IanResourceSample(available_mb=115, swap_percent=0),
        )
    )
    task, _device_config, _clock = _make_runtime_task(
        make_test_dir("ian-background-recovery"),
        clock=clock,
        ian_resource_sampler=lambda: next(samples),
    )
    monkeypatch.setattr(task, "_schedule_if_due", lambda: None)
    executions = []
    monkeypatch.setattr(
        task,
        "_execute_command",
        lambda command: executions.append(command.id),
    )
    command = RefreshCommand.create(
        kind=CommandKind.CACHE_REFRESH,
        source=CommandSource.BACKGROUND,
        plugin_id="sports_dashboard",
        instance_uuid="sports-instance",
        structural_generation=1,
        settings_revision=1,
        payload={"playlist_name": "Daily"},
        now_monotonic=clock.monotonic(),
        deadline_monotonic=clock.monotonic() + 60,
        priority=10,
        intent=RefreshIntent.DATA_REFRESH,
    )
    submitted = task.refresh_queue.submit(command)

    task._run_one_iteration_for_test()

    deferred = task.refresh_queue.get_entry(submitted.id)
    assert deferred.job.status is JobStatus.RUNNING
    assert deferred.command.id == command.id
    assert executions == []

    assert task._run_one_iteration_for_test() is None
    assert task.refresh_queue.get_entry(submitted.id).job.status is JobStatus.RUNNING
    assert executions == []

    clock.advance(1)
    task._run_one_iteration_for_test()
    task._run_one_iteration_for_test()

    completed = task.refresh_queue.get_entry(submitted.id)
    assert completed.job.status is JobStatus.SUCCEEDED
    assert executions == [command.id]


@pytest.mark.parametrize(
    ("urgent_kind", "urgent_source", "urgent_intent"),
    [
        (
            CommandKind.DISPLAY,
            CommandSource.SCHEDULER,
            RefreshIntent.DISPLAY_CACHE,
        ),
        (
            CommandKind.DISPLAY,
            CommandSource.MANUAL,
            RefreshIntent.MANUAL_RENDER,
        ),
    ],
)
def test_display_and_manual_run_before_deferred_ian_background_resume(
    monkeypatch,
    urgent_kind,
    urgent_source,
    urgent_intent,
):
    clock = RuntimeClock()
    samples = iter(
        (
            IanResourceSample(available_mb=100, swap_percent=0),
            IanResourceSample(available_mb=115, swap_percent=0),
        )
    )
    task, _device_config, _clock = _make_runtime_task(
        make_test_dir(f"ian-preemption-{urgent_source.value}"),
        clock=clock,
        ian_resource_sampler=lambda: next(samples),
    )
    monkeypatch.setattr(task, "_schedule_if_due", lambda: None)
    executions = []
    monkeypatch.setattr(
        task,
        "_execute_command",
        lambda command: executions.append(command.id),
    )
    background = RefreshCommand.create(
        kind=CommandKind.CACHE_REFRESH,
        source=CommandSource.BACKGROUND,
        plugin_id="sports_dashboard",
        instance_uuid="sports-instance",
        structural_generation=1,
        settings_revision=1,
        payload={"playlist_name": "Daily"},
        now_monotonic=clock.monotonic(),
        deadline_monotonic=clock.monotonic() + 60,
        priority=10,
        intent=RefreshIntent.DATA_REFRESH,
    )
    urgent = RefreshCommand.create(
        kind=urgent_kind,
        source=urgent_source,
        plugin_id="calendar",
        instance_uuid="calendar-instance",
        structural_generation=1,
        settings_revision=1,
        payload={"playlist_name": "Daily"},
        now_monotonic=clock.monotonic(),
        deadline_monotonic=clock.monotonic() + 60,
        priority=100,
        intent=urgent_intent,
    )
    background_job = task.refresh_queue.submit(background)

    task._run_one_iteration_for_test()
    urgent_job = task.refresh_queue.submit(urgent)
    clock.advance(1)
    task._run_one_iteration_for_test()

    assert executions == [urgent.id]
    assert (
        task.refresh_queue.get_entry(urgent_job.id).job.status
        is JobStatus.SUCCEEDED
    )
    assert (
        task.refresh_queue.get_entry(background_job.id).job.status
        is JobStatus.RUNNING
    )

    task._run_one_iteration_for_test()

    assert executions == [urgent.id, background.id]
    assert (
        task.refresh_queue.get_entry(background_job.id).job.status
        is JobStatus.SUCCEEDED
    )


def test_ordinary_background_progresses_while_sports_is_retained_by_ian(
    monkeypatch,
):
    clock = RuntimeClock()
    samples = iter(
        (
            IanResourceSample(available_mb=100, swap_percent=0),
            IanResourceSample(available_mb=115, swap_percent=0),
        )
    )
    task, _device_config, _clock = _make_runtime_task(
        make_test_dir("ian-background-nonblocking"),
        clock=clock,
        ian_resource_sampler=lambda: next(samples),
    )
    monkeypatch.setattr(task, "_schedule_if_due", lambda: None)
    executions = []
    monkeypatch.setattr(
        task,
        "_execute_command",
        lambda command: executions.append(command.id),
    )
    sports = RefreshCommand.create(
        kind=CommandKind.CACHE_REFRESH,
        source=CommandSource.BACKGROUND,
        plugin_id="sports_dashboard",
        instance_uuid="sports-instance",
        structural_generation=1,
        settings_revision=1,
        payload={"playlist_name": "Daily"},
        now_monotonic=clock.monotonic(),
        deadline_monotonic=clock.monotonic() + 60,
        priority=10,
        intent=RefreshIntent.DATA_REFRESH,
    )
    ordinary = RefreshCommand.create(
        kind=CommandKind.CACHE_REFRESH,
        source=CommandSource.BACKGROUND,
        plugin_id="weather",
        instance_uuid="weather-instance",
        structural_generation=1,
        settings_revision=1,
        payload={"playlist_name": "Daily"},
        now_monotonic=clock.monotonic(),
        deadline_monotonic=clock.monotonic() + 60,
        priority=10,
        intent=RefreshIntent.DATA_REFRESH,
    )
    sports_job = task.refresh_queue.submit(sports)

    task._run_one_iteration_for_test()
    ordinary_job = task.refresh_queue.submit(ordinary)
    task._run_one_iteration_for_test()

    assert executions == [ordinary.id]
    assert (
        task.refresh_queue.get_entry(ordinary_job.id).job.status
        is JobStatus.SUCCEEDED
    )
    assert (
        task.refresh_queue.get_entry(sports_job.id).job.status
        is JobStatus.RUNNING
    )

    clock.advance(1)
    task._run_one_iteration_for_test()

    assert executions == [ordinary.id, sports.id]
    assert (
        task.refresh_queue.get_entry(sports_job.id).job.status
        is JobStatus.SUCCEEDED
    )


def test_ian_retained_limit_preserves_queue_slot_for_manual_display(monkeypatch):
    clock = RuntimeClock()
    refresh_queue = RefreshQueue(
        capacity=2,
        manual_reserved=1,
        clock=clock.monotonic,
        wall_clock=clock.wall_time,
    )
    task, _device_config, _clock = _make_runtime_task(
        make_test_dir("ian-retained-urgent-slot"),
        clock=clock,
        refresh_queue=refresh_queue,
        ian_resource_sampler=lambda: IanResourceSample(
            available_mb=100,
            swap_percent=0,
        ),
    )
    monkeypatch.setattr(task, "_schedule_if_due", lambda: None)
    executions = []
    monkeypatch.setattr(
        task,
        "_execute_command",
        lambda command: executions.append(command.id),
    )

    def sports_command(instance_uuid):
        return RefreshCommand.create(
            kind=CommandKind.CACHE_REFRESH,
            source=CommandSource.BACKGROUND,
            plugin_id="sports_dashboard",
            instance_uuid=instance_uuid,
            structural_generation=1,
            settings_revision=1,
            payload={"playlist_name": "Daily"},
            now_monotonic=clock.monotonic(),
            deadline_monotonic=clock.monotonic() + 60,
            priority=10,
            intent=RefreshIntent.DATA_REFRESH,
        )

    first = sports_command("sports-one")
    second = sports_command("sports-two")
    first_job = task.refresh_queue.submit(first)
    task._run_one_iteration_for_test()
    second_job = task.refresh_queue.submit(second)
    task._run_one_iteration_for_test()

    assert (
        task.refresh_queue.get_entry(first_job.id).job.status
        is JobStatus.RUNNING
    )
    rejected = task.refresh_queue.get_entry(second_job.id)
    assert rejected.job.status is JobStatus.CANCELED
    assert rejected.job.error_code == "ian_retained_capacity"

    manual = RefreshCommand.create(
        kind=CommandKind.DISPLAY,
        source=CommandSource.MANUAL,
        plugin_id="calendar",
        instance_uuid="calendar-instance",
        structural_generation=1,
        settings_revision=1,
        payload={"playlist_name": "Daily"},
        now_monotonic=clock.monotonic(),
        deadline_monotonic=clock.monotonic() + 60,
        priority=100,
        intent=RefreshIntent.MANUAL_RENDER,
    )
    manual_job = task.refresh_queue.submit(manual)
    task._run_one_iteration_for_test()

    assert executions == [manual.id]
    assert (
        task.refresh_queue.get_entry(manual_job.id).job.status
        is JobStatus.SUCCEEDED
    )
    health = task.refresh_health_snapshot()
    assert health["ian_retained"] == 1
    assert health["ian_retained_limit"] == 1
    assert health["ian_status"] == "retained_capacity_deferred"


def test_ian_health_reports_real_queue_terminal_after_executor_terminalization(
    monkeypatch,
):
    clock = RuntimeClock()
    task, _device_config, _clock = _make_runtime_task(
        make_test_dir("ian-terminal-health"),
        clock=clock,
        ian_resource_sampler=lambda: IanResourceSample(
            available_mb=115,
            swap_percent=0,
        ),
    )
    monkeypatch.setattr(task, "_schedule_if_due", lambda: None)

    def fail_execution(_command):
        raise RuntimeError("render failed")

    monkeypatch.setattr(task, "_execute_command", fail_execution)
    command = RefreshCommand.create(
        kind=CommandKind.CACHE_REFRESH,
        source=CommandSource.BACKGROUND,
        plugin_id="sports_dashboard",
        instance_uuid="sports-instance",
        structural_generation=1,
        settings_revision=1,
        payload={"playlist_name": "Daily"},
        now_monotonic=clock.monotonic(),
        deadline_monotonic=clock.monotonic() + 60,
        priority=10,
        intent=RefreshIntent.DATA_REFRESH,
    )
    submitted = task.refresh_queue.submit(command)

    task._run_one_iteration_for_test()

    terminal = task.refresh_queue.get_entry(submitted.id)
    assert terminal.job.status is JobStatus.CANCELED
    health = task.refresh_health_snapshot()
    assert health["ian_status"] == "canceled"
    assert health["ian_last_queue_status"] == "canceled"
    assert health["ian_retained"] == 0


def test_stop_terminalizes_every_ian_retained_queue_entry(monkeypatch):
    clock = RuntimeClock()
    task, _device_config, _clock = _make_runtime_task(
        make_test_dir("ian-stop-retained"),
        clock=clock,
        ian_resource_sampler=lambda: IanResourceSample(
            available_mb=100,
            swap_percent=0,
        ),
    )
    monkeypatch.setattr(task, "_schedule_if_due", lambda: None)
    command = RefreshCommand.create(
        kind=CommandKind.CACHE_REFRESH,
        source=CommandSource.BACKGROUND,
        plugin_id="sports_dashboard",
        instance_uuid="sports-instance",
        structural_generation=1,
        settings_revision=1,
        payload={"playlist_name": "Daily"},
        now_monotonic=clock.monotonic(),
        deadline_monotonic=clock.monotonic() + 60,
        priority=10,
        intent=RefreshIntent.DATA_REFRESH,
    )
    submitted = task.refresh_queue.submit(command)
    task._run_one_iteration_for_test()

    assert task.stop(join_timeout=0) is True

    canceled = task.refresh_queue.get_entry(submitted.id)
    assert canceled.job.status is JobStatus.CANCELED
    assert canceled.job.error_code == "ian_worker_stopped"
    assert task.refresh_health_snapshot()["ian_retained"] == 0


def _normal_cache_display_command(task, playlist, instance, *, source=CommandSource.SCHEDULER):
    return task._playlist_command(
        playlist.name,
        instance,
        source=source,
        intent=RefreshIntent.DISPLAY_CACHE,
        force=False,
        display_cached_only=True,
        priority=50 if source is CommandSource.SCHEDULER else 100,
        current_dt=PRESENTATION_NOW,
        cache_theme_mode=None,
    )


def test_rotation_deadline_policy_prioritizes_five_minute_switches(monkeypatch):
    task, device_config, _clock, _playlist, _display = _make_presentation_task(
        "rotation-deadline-policy"
    )
    device_config.config["plugin_cycle_interval_seconds"] = 300
    device_config.config["rotation_presentation_wait_seconds"] = 999
    device_config.refresh_info.refresh_time = (
        PRESENTATION_NOW - timedelta(seconds=299)
    ).isoformat()
    monkeypatch.setattr(task, "_get_current_datetime", lambda: PRESENTATION_NOW)

    assert refresh_task_module.DEFAULT_ROTATION_PRESENTATION_WAIT_SECONDS == 60
    assert refresh_task_module.DEFAULT_ROTATION_PRESENTATION_DEADLINE_SECONDS == 300
    assert refresh_task_module.DEFAULT_ROTATION_MAX_INTERVAL_SECONDS == 420
    assert task._rotation_presentation_wait_seconds() == 0
    assert task._scheduler_poll_seconds() == 1


def test_automatic_presentation_deadline_ends_before_rotation_tick(monkeypatch):
    clock = RuntimeClock(
        monotonic=1000,
        wall=PRESENTATION_NOW.timestamp(),
    )
    task, device_config, _clock, playlist, _display = _make_presentation_task(
        "automatic-presentation-deadline-before-rotation",
        clock=clock,
    )
    device_config.config["plugin_cycle_interval_seconds"] = 300
    device_config.refresh_info.refresh_time = (
        PRESENTATION_NOW - timedelta(seconds=230)
    ).isoformat()
    monkeypatch.setattr(task, "_get_current_datetime", lambda: PRESENTATION_NOW)
    instance = playlist.plugins[0].snapshot()

    command = task._playlist_command(
        playlist.name,
        instance,
        source=CommandSource.BACKGROUND,
        intent=RefreshIntent.PRESENTATION_REFRESH,
        force=False,
        display_cached_only=False,
        priority=90,
        kind=CommandKind.CACHE_REFRESH,
        current_dt=PRESENTATION_NOW,
        presentation_request_id="rotation-request",
        automatic_rotation=True,
    )

    assert task._get_rotation_wait_seconds() == 70
    assert command.deadline_monotonic == pytest.approx(
        clock.monotonic()
        + 70
        - refresh_task_module.DEFAULT_ROTATION_DEADLINE_CLEANUP_SECONDS
    )


def test_manual_presentation_keeps_manual_timeout_deadline():
    clock = RuntimeClock(
        monotonic=1000,
        wall=PRESENTATION_NOW.timestamp(),
    )
    task, device_config, _clock, playlist, _display = _make_presentation_task(
        "manual-presentation-keeps-timeout",
        clock=clock,
    )
    device_config.config["plugin_cycle_interval_seconds"] = 300
    device_config.refresh_info.refresh_time = (
        PRESENTATION_NOW - timedelta(seconds=230)
    ).isoformat()
    instance = playlist.plugins[0].snapshot()

    command = task._playlist_command(
        playlist.name,
        instance,
        source=CommandSource.MANUAL,
        intent=RefreshIntent.PRESENTATION_REFRESH,
        force=True,
        display_cached_only=False,
        priority=100,
        kind=CommandKind.CACHE_REFRESH,
        current_dt=PRESENTATION_NOW,
        presentation_request_id="manual-request",
        automatic_rotation=False,
    )

    assert command.deadline_monotonic == pytest.approx(
        clock.monotonic() + refresh_task_module.DEFAULT_MANUAL_UPDATE_TIMEOUT_SECONDS
    )


def test_successful_rotation_immediately_requests_the_next_presentation():
    task, _device_config, _clock, playlist, _display = _make_presentation_task(
        "prefetch-next-presentation-after-display",
        plugin_count=2,
    )
    first, second = [plugin.snapshot() for plugin in playlist.plugins]
    playlist.plugin_rotation_pool = [first.instance_uuid, second.instance_uuid]
    playlist.plugin_rotation_queue = [first.instance_uuid, second.instance_uuid]
    playlist.plugin_rotation_recent_history = []
    playlist._plugin_rotation_reserved_key = first.instance_uuid
    acknowledgement = task.device_config.playlist_manager.acknowledge_rotation_display(
        first.instance_uuid,
        expected_playlist_name=playlist.name,
    )
    assert acknowledgement is not None

    requested = task._request_next_presentation_after_display(
        PRESENTATION_NOW,
        "current-display-commit",
        PRESENTATION_NOW.isoformat(),
    )

    states = task.runtime_state.snapshot().instances
    assert requested is True
    assert first.instance_uuid not in states
    assert states[second.instance_uuid].presentation_request is not None
    assert (
        states[second.instance_uuid].presentation_request.origin_display_commit_id
        == "current-display-commit"
    )
    assert playlist.is_rotation_reservation_current(second.instance_uuid) is True


def test_successful_rotation_keeps_existing_next_presentation_on_critical_path(
    monkeypatch,
):
    task, device_config, _clock, playlist, _display = _make_presentation_task(
        "existing-next-presentation-stays-reserved",
        plugin_count=2,
        provider_refresh=True,
        supports_theme=True,
    )
    first, second = [plugin.snapshot() for plugin in playlist.plugins]
    device_config.config["display_triggered_refresh_enabled"] = False
    device_config.config["plugin_cycle_interval_seconds"] = 300
    playlist.plugin_rotation_pool = [first.instance_uuid, second.instance_uuid]
    playlist.plugin_rotation_queue = [first.instance_uuid, second.instance_uuid]
    playlist.plugin_rotation_recent_history = []
    playlist._plugin_rotation_reserved_key = first.instance_uuid
    acknowledgement = task.device_config.playlist_manager.acknowledge_rotation_display(
        first.instance_uuid,
        expected_playlist_name=playlist.name,
    )
    assert acknowledgement is not None
    _write_runtime_theme_cache(task, second, "day")
    request = _seed_presentation_request(
        task,
        second,
        origin_theme_mode="night",
    )
    _seed_prepared_presentation(
        task,
        second,
        request,
        theme_mode="night",
    )
    monkeypatch.setattr(
        task,
        "_resource_sample",
        lambda: ResourceSample(available_mb=512, swap_percent=0),
    )

    requested = task._request_next_presentation_after_display(
        PRESENTATION_NOW,
        "current-display-commit",
        PRESENTATION_NOW.isoformat(),
        displayed_instance_uuid=first.instance_uuid,
    )

    state = task.runtime_state.snapshot().instances[second.instance_uuid]
    assert requested is True
    assert state.presentation_request is not None
    assert state.presentation_request.request_id == request.request_id
    assert state.presentation_request.prepared_theme_mode == "night"
    assert playlist.is_rotation_reservation_current(second.instance_uuid) is True

    presentation = task._select_independent_refresh_command(PRESENTATION_NOW)

    assert presentation is not None
    assert presentation.intent is RefreshIntent.PRESENTATION_REFRESH
    assert presentation.instance_uuid == second.instance_uuid
    assert presentation.priority == 90
    assert presentation.payload["automatic_rotation"] is True
    assert presentation.payload["presentation_request_id"] == request.request_id


@pytest.mark.parametrize("already_prepared", [False, True])
def test_existing_next_presentation_respects_retry_backoff(already_prepared):
    task, device_config, _clock, playlist, _display = _make_presentation_task(
        f"existing-next-presentation-backoff-{already_prepared}",
        plugin_count=2,
        provider_refresh=True,
        supports_theme=True,
    )
    first, second = [plugin.snapshot() for plugin in playlist.plugins]
    device_config.config["display_triggered_refresh_enabled"] = False
    playlist.plugin_rotation_pool = [first.instance_uuid, second.instance_uuid]
    playlist.plugin_rotation_queue = [second.instance_uuid]
    playlist.plugin_rotation_recent_history = [first.instance_uuid]
    playlist._plugin_rotation_reserved_key = None
    request = _seed_presentation_request(task, second, origin_theme_mode="night")
    if already_prepared:
        _seed_prepared_presentation(task, second, request, theme_mode="night")
    task.runtime_state.record_failure(
        second.instance_uuid,
        PRESENTATION_NOW.isoformat(),
        "presentation fetch failed",
        (PRESENTATION_NOW + timedelta(minutes=5)).isoformat(),
        lane=RefreshLane.PRESENTATION,
    )
    before = task.runtime_state.snapshot().instances[second.instance_uuid]

    requested = task._request_next_presentation_after_display(
        PRESENTATION_NOW,
        "current-display-commit",
        PRESENTATION_NOW.isoformat(),
        displayed_instance_uuid=first.instance_uuid,
    )

    after = task.runtime_state.snapshot().instances[second.instance_uuid]
    assert requested is False
    assert after.presentation_request == before.presentation_request
    assert after.presentation == before.presentation
    assert playlist.is_rotation_reservation_current(second.instance_uuid) is False


def test_matching_existing_next_presentation_is_adopted_at_next_rotation(
    monkeypatch,
):
    task, device_config, clock, playlist, display = _make_presentation_task(
        "existing-next-prepared-presentation-adopted-on-cadence",
        plugin_count=2,
        provider_refresh=True,
        supports_theme=True,
    )
    first, second = [plugin.snapshot() for plugin in playlist.plugins]
    device_config.config["display_triggered_refresh_enabled"] = False
    device_config.config["plugin_cycle_interval_seconds"] = 300
    device_config.refresh_info.refresh_time = PRESENTATION_NOW.isoformat()
    playlist.plugin_rotation_pool = [first.instance_uuid, second.instance_uuid]
    playlist.plugin_rotation_queue = [second.instance_uuid]
    playlist.plugin_rotation_recent_history = [first.instance_uuid]
    playlist._plugin_rotation_reserved_key = None
    for instance in (first, second):
        _write_runtime_theme_cache(task, instance, "day")
        task.runtime_state.record_success(
            instance.instance_uuid,
            PRESENTATION_NOW.isoformat(),
            lane=RefreshLane.DATA,
        )
    request = _seed_presentation_request(task, second, origin_theme_mode="day")
    _seed_prepared_presentation(task, second, request, theme_mode="day")
    now = [PRESENTATION_NOW]
    monkeypatch.setattr(task, "_get_current_datetime", lambda: now[0])
    monkeypatch.setattr(
        task,
        "_resource_sample",
        lambda: ResourceSample(available_mb=512, swap_percent=0),
    )
    _install_display_provider_plugin_sentinels(monkeypatch)

    assert task._request_next_presentation_after_display(
        now[0],
        "current-display-commit",
        now[0].isoformat(),
        displayed_instance_uuid=first.instance_uuid,
    ) is True
    assert playlist.is_rotation_reservation_current(second.instance_uuid) is True
    assert task._select_independent_refresh_command(now[0]) is None

    clock.advance(299)
    now[0] += timedelta(seconds=299)
    assert task._select_cached_display_command(now[0]) is None
    assert display.calls == []
    clock.advance(1)
    now[0] += timedelta(seconds=1)
    command = task._select_cached_display_command(now[0])
    assert command is not None
    assert command.instance_uuid == second.instance_uuid
    assert command.intent is RefreshIntent.DISPLAY_CACHE
    assert command.allow_prepared_presentation is True
    assert command.payload["presentation_request_id"] == request.request_id

    result = _queue_and_process(task, command)

    state = task.runtime_state.snapshot().instances[second.instance_uuid]
    assert result.job.status is JobStatus.SUCCEEDED
    assert len(display.calls) == 1
    assert display.calls[0]["image"].getpixel((0, 0)) == (255, 255, 255)
    assert state.presentation_request is None
    assert state.presentation_receipt.request_id == request.request_id
    assert state.presentation_receipt.theme_mode == "day"
    assert device_config.refresh_info.refresh_time == now[0].isoformat()


@pytest.mark.parametrize("provider_free", [False, True])
@pytest.mark.parametrize("fault", [None, "missing", "corrupt", "expired", "write_failure", "theme_change"])
def test_day_prepared_rotates_after_night_cache_supersedes_old_day(monkeypatch, provider_free, fault):
    task, config, clock, playlist, display = _make_presentation_task(
        "day-prepared-with-night-authoritative", plugin_count=2,
        provider_free=provider_free, provider_refresh=not provider_free, supports_theme=True,
    )
    peer, prepared = [plugin.snapshot() for plugin in playlist.plugins]
    config.config["display_triggered_refresh_enabled"] = False
    config.config["plugin_cycle_interval_seconds"] = 300
    original_plugin = config.get_plugin
    config.get_plugin = lambda key: original_plugin(key) if key == prepared.plugin_id else {"id": key}
    config.refresh_info.refresh_time = (PRESENTATION_NOW - timedelta(minutes=5)).isoformat()
    playlist.plugin_rotation_pool = [peer.instance_uuid, prepared.instance_uuid]
    playlist.plugin_rotation_queue = [prepared.instance_uuid, peer.instance_uuid]
    playlist.plugin_rotation_recent_history = []
    playlist._plugin_rotation_reserved_key = None
    _write_runtime_cache(task, peer)
    old_day = _write_runtime_theme_cache(task, prepared, "day")
    os.utime(old_day, (PRESENTATION_NOW.timestamp() - 3600,) * 2)
    _write_runtime_theme_cache(task, prepared, "night")
    task.runtime_state.record_success(
        prepared.instance_uuid, PRESENTATION_NOW.isoformat(), lane=RefreshLane.DATA,
        last_good_cache=LastGoodCacheState(
            theme_mode="night", structural_generation=prepared.structural_generation,
            settings_revision=prepared.settings_revision, promoted_at=PRESENTATION_NOW.isoformat(),
        ),
    )
    request = _seed_presentation_request(task, prepared, origin_theme_mode="day")
    prepared_file = _seed_prepared_presentation(task, prepared, request, theme_mode="day")
    monkeypatch.setattr(task, "_get_current_datetime", lambda: PRESENTATION_NOW)
    monkeypatch.setattr(task, "_resource_sample", lambda: ResourceSample(512, 0))
    _install_display_provider_plugin_sentinels(monkeypatch)
    active = config.playlist_manager.snapshot_active_playlist(PRESENTATION_NOW)
    assert prepared.instance_uuid not in task._active_cache_candidates(
        active, {"mode": "day"}, exact_theme_only=True,
    )
    if fault == "missing":
        Path(prepared_file.cache_path).unlink()
    elif fault == "corrupt":
        Path(prepared_file.cache_path).write_bytes(b"invalid PNG")
    elif fault == "expired":
        os.utime(prepared_file.cache_path, (1, 1))

    command = task._select_cached_display_command(PRESENTATION_NOW)

    if fault in {"missing", "corrupt", "expired"}:
        assert command is None
        state = task.runtime_state.snapshot().instances[prepared.instance_uuid]
        assert state.presentation_request.request_id == request.request_id
        assert state.presentation_request.prepared_at is None
        assert state.presentation.next_retry_at is not None
        assert state.presentation_receipt is None
        assert state.last_good_cache.theme_mode == "night"
        assert display.calls == []
        assert task._select_cached_display_command(PRESENTATION_NOW).instance_uuid == peer.instance_uuid
        return
    assert command is not None
    assert command.instance_uuid == prepared.instance_uuid
    assert command.payload["presentation_request_id"] == request.request_id
    assert task.runtime_state.snapshot().instances[prepared.instance_uuid].presentation_receipt is None
    if fault == "write_failure":
        def failed_write(*args, **kwargs):
            raise RuntimeError("panel write failed")
        monkeypatch.setattr(display, "display_image", failed_write)
    elif fault == "theme_change":
        config.config["theme_mode"] = "night"
    result = _queue_and_process(task, command)
    state = task.runtime_state.snapshot().instances[prepared.instance_uuid]
    if fault is not None:
        assert result.job.status is not JobStatus.SUCCEEDED
        assert state.presentation_receipt is None
        assert state.last_good_cache.theme_mode == "night"
        assert state.presentation_request.request_id == request.request_id
        return
    assert result.job.status is JobStatus.SUCCEEDED
    assert state.presentation_receipt.request_id == request.request_id
    assert state.last_good_cache.theme_mode == "day"
    assert display.calls[0]["image"].getpixel((0, 0)) == (255, 255, 255)


def test_provider_free_automatic_display_requests_next_when_global_provider_policy_is_off(
    monkeypatch,
):
    task, device_config, _clock, playlist, _display = _make_presentation_task(
        "provider-free-automatic-display-prefetches-next",
        plugin_count=2,
        provider_free=True,
    )
    first, second = [plugin.snapshot() for plugin in playlist.plugins]
    device_config.config["display_triggered_refresh_enabled"] = False
    playlist.plugin_rotation_pool = [first.instance_uuid, second.instance_uuid]
    playlist.plugin_rotation_queue = [first.instance_uuid, second.instance_uuid]
    playlist.plugin_rotation_recent_history = []
    playlist._plugin_rotation_reserved_key = first.instance_uuid
    _write_runtime_cache(task, first, Image.new("RGB", (32, 16), "black"))
    monkeypatch.setattr(task, "_get_current_datetime", lambda: PRESENTATION_NOW)
    command = task._playlist_command(
        playlist.name,
        first,
        source=CommandSource.SCHEDULER,
        intent=RefreshIntent.DISPLAY_CACHE,
        force=False,
        display_cached_only=True,
        priority=50,
        current_dt=PRESENTATION_NOW,
        cache_theme_mode=None,
        automatic_rotation=True,
        allow_prepared_presentation=False,
    )

    task._execute_command(command)

    states = task.runtime_state.snapshot().instances
    assert states[second.instance_uuid].presentation_request is not None
    assert playlist.is_rotation_reservation_current(second.instance_uuid) is True


def test_automatic_display_does_not_request_next_presentation_when_policy_is_off(
    monkeypatch,
):
    task, _device_config, _clock, playlist, _display = _make_presentation_task(
        "automatic-display-prefetches-next",
        plugin_count=2,
    )
    first, second = [plugin.snapshot() for plugin in playlist.plugins]
    task.device_config.config["display_triggered_refresh_enabled"] = False
    playlist.plugin_rotation_pool = [first.instance_uuid, second.instance_uuid]
    playlist.plugin_rotation_queue = [first.instance_uuid, second.instance_uuid]
    playlist.plugin_rotation_recent_history = []
    playlist._plugin_rotation_reserved_key = first.instance_uuid
    _write_runtime_cache(task, first, Image.new("RGB", (32, 16), "black"))
    monkeypatch.setattr(task, "_get_current_datetime", lambda: PRESENTATION_NOW)
    command = task._playlist_command(
        playlist.name,
        first,
        source=CommandSource.SCHEDULER,
        intent=RefreshIntent.DISPLAY_CACHE,
        force=False,
        display_cached_only=True,
        priority=50,
        current_dt=PRESENTATION_NOW,
        cache_theme_mode=None,
        automatic_rotation=True,
        allow_prepared_presentation=False,
    )

    task._execute_command(command)

    states = task.runtime_state.snapshot().instances
    assert second.instance_uuid not in states
    assert playlist.is_rotation_reservation_current(second.instance_uuid) is False


def test_next_presentation_reservation_skips_ineligible_members():
    task, device_config, _clock, playlist, _display = _make_presentation_task(
        "next-presentation-skips-ineligible-member",
        plugin_count=3,
    )
    first, ineligible, eligible = [
        plugin.snapshot() for plugin in playlist.plugins
    ]
    playlist.plugins[1].settings["refreshOnDisplay"] = "sometimes"
    ineligible = playlist.plugins[1].snapshot()
    device_config.config["display_triggered_refresh_enabled"] = False
    manifests = {
        first.plugin_id: _presentation_manifest(first.plugin_id),
        ineligible.plugin_id: _presentation_manifest(
            ineligible.plugin_id,
            provider_free=True,
        ),
        eligible.plugin_id: _presentation_manifest(
            eligible.plugin_id,
            provider_free=True,
        ),
    }
    device_config.get_plugin = lambda plugin_id: {
        "id": plugin_id,
        "refresh_on_display": True,
        "_manifest": manifests[plugin_id],
    }
    playlist.plugin_rotation_pool = [
        first.instance_uuid,
        ineligible.instance_uuid,
        eligible.instance_uuid,
    ]
    playlist.plugin_rotation_queue = [
        first.instance_uuid,
        ineligible.instance_uuid,
        eligible.instance_uuid,
    ]
    playlist.plugin_rotation_recent_history = []
    playlist._plugin_rotation_reserved_key = first.instance_uuid
    acknowledgement = task.device_config.playlist_manager.acknowledge_rotation_display(
        first.instance_uuid,
        expected_playlist_name=playlist.name,
    )
    assert acknowledgement is not None

    requested = task._request_next_presentation_after_display(
        PRESENTATION_NOW,
        "current-display-commit",
        PRESENTATION_NOW.isoformat(),
        displayed_instance_uuid=first.instance_uuid,
    )

    states = task.runtime_state.snapshot().instances
    assert requested is True
    assert ineligible.instance_uuid not in states
    assert states[eligible.instance_uuid].presentation_request is not None
    assert playlist.is_rotation_reservation_current(ineligible.instance_uuid) is False
    assert playlist.is_rotation_reservation_current(eligible.instance_uuid) is True


def test_reserved_next_presentation_refreshes_matching_due_data_first(monkeypatch):
    task, device_config, _clock, playlist, _display = _make_presentation_task(
        "reserved-presentation-preempts-data",
        latest_refresh_time=None,
    )
    instance = playlist.plugins[0].snapshot()
    device_config.config["plugin_cycle_interval_seconds"] = 300
    device_config.refresh_info.refresh_time = (
        PRESENTATION_NOW - timedelta(seconds=301)
    ).isoformat()
    _write_runtime_cache(task, instance, Image.new("RGB", (32, 16), "black"))
    selection = device_config.playlist_manager.reserve_next_active_instance(
        PRESENTATION_NOW,
        latest_refresh=device_config.refresh_info.get_refresh_datetime(),
        interval_seconds=300,
        eligible_instance_uuids={instance.instance_uuid},
    )
    assert selection is not None
    _seed_presentation_request(task, instance)
    monkeypatch.setattr(
        task,
        "_resource_sample",
        lambda: ResourceSample(available_mb=512, swap_percent=0),
    )

    command = task._select_independent_refresh_command(PRESENTATION_NOW)

    assert command is not None
    assert command.intent is RefreshIntent.DATA_REFRESH
    assert command.instance_uuid == instance.instance_uuid
    assert command.priority == 95

    task.runtime_state.record_attempt(
        instance.instance_uuid,
        (PRESENTATION_NOW + timedelta(seconds=1)).isoformat(),
        lane=RefreshLane.DATA,
    )
    presentation = task._select_independent_refresh_command(
        PRESENTATION_NOW + timedelta(seconds=1)
    )

    assert presentation is not None
    assert presentation.intent is RefreshIntent.PRESENTATION_REFRESH
    assert presentation.instance_uuid == instance.instance_uuid
    assert presentation.priority == 90


def test_starved_ordinary_data_gets_one_bounded_turn_before_reserved_preflight(
    monkeypatch,
):
    now = datetime(2026, 7, 18, 23, 0, tzinfo=timezone.utc)
    presentation_data = _runtime_plugin_data(
        "presentation_plugin",
        "Presentation",
        latest_refresh_time=None,
        interval=3600,
    )
    presentation_data["plugin_settings"]["refreshOnDisplay"] = True
    ordinary_data = _runtime_plugin_data(
        "ordinary_plugin",
        "Ordinary",
        latest_refresh_time=(now - timedelta(hours=2)).isoformat(),
        interval=3600,
    )
    playlist = _runtime_playlist(
        presentation_data,
        ordinary_data,
        name="Bounded Starvation Playlist",
    )
    task, device_config, _clock = _make_runtime_task(
        make_test_dir("bounded-starvation-before-reserved-preflight"),
        playlists=[playlist],
        cycle_seconds=300,
    )
    presentation, ordinary = [instance.snapshot() for instance in playlist.plugins]
    manifest = _presentation_manifest(presentation.plugin_id)
    device_config.get_plugin = lambda plugin_id: (
        {
            "id": plugin_id,
            "refresh_on_display": True,
            "_manifest": manifest,
        }
        if plugin_id == presentation.plugin_id
        else {"id": plugin_id}
    )
    device_config.config.update(
        {
            "theme_mode": "day",
            "active_theme": "day",
            "display_triggered_refresh_enabled": True,
        }
    )
    device_config.refresh_info.refresh_time = now.isoformat()
    for instance in (presentation, ordinary):
        _write_runtime_cache(task, instance, Image.new("RGB", (32, 16), "black"))
    task.runtime_state.record_success(
        ordinary.instance_uuid,
        (now - timedelta(hours=2)).isoformat(),
        lane=RefreshLane.DATA,
    )
    selection = device_config.playlist_manager.reserve_next_active_instance(
        now,
        latest_refresh=None,
        interval_seconds=0,
        eligible_instance_uuids={presentation.instance_uuid},
    )
    assert selection is not None
    _seed_presentation_request(task, presentation, requested_at=now)
    monkeypatch.setattr(task, "_get_current_datetime", lambda: now)
    monkeypatch.setattr(
        task,
        "_resource_sample",
        lambda: ResourceSample(available_mb=512, swap_percent=0),
    )

    concession = task._select_independent_refresh_command(now)

    assert concession is not None
    assert concession.intent is RefreshIntent.DATA_REFRESH
    assert concession.instance_uuid == ordinary.instance_uuid

    task._record_runtime_attempt(concession)
    reserved = task._select_independent_refresh_command(now + timedelta(seconds=1))

    assert reserved is not None
    assert reserved.intent is RefreshIntent.DATA_REFRESH
    assert reserved.instance_uuid == presentation.instance_uuid
    assert reserved.priority == 95


def test_rotation_guard_stops_new_background_work_before_due_display(monkeypatch):
    task, device_config, _clock, playlist, _display = _make_presentation_task(
        "rotation-guard-background-work",
        latest_refresh_time=None,
    )
    instance = playlist.plugins[0].snapshot()
    device_config.config["plugin_cycle_interval_seconds"] = 300
    _write_runtime_cache(task, instance, Image.new("RGB", (32, 16), "black"))
    monkeypatch.setattr(
        task,
        "_resource_sample",
        lambda: ResourceSample(available_mb=512, swap_percent=0),
    )
    monkeypatch.setattr(task, "_get_current_datetime", lambda: PRESENTATION_NOW)

    device_config.refresh_info.refresh_time = (
        PRESENTATION_NOW - timedelta(seconds=181)
    ).isoformat()
    selection = device_config.playlist_manager.reserve_next_active_instance(
        PRESENTATION_NOW,
        latest_refresh=None,
        interval_seconds=0,
        eligible_instance_uuids={instance.instance_uuid},
    )
    assert selection is not None
    guarded = task._select_independent_refresh_command(PRESENTATION_NOW)

    device_config.refresh_info.refresh_time = (
        PRESENTATION_NOW - timedelta(seconds=60)
    ).isoformat()
    unguarded = task._select_independent_refresh_command(PRESENTATION_NOW)

    assert refresh_task_module.DEFAULT_ROTATION_BACKGROUND_GUARD_SECONDS == 120
    assert guarded is None
    assert unguarded is not None
    assert unguarded.intent is RefreshIntent.DATA_REFRESH


def test_starved_rotation_guards_worker_and_preserves_hardware_budget(monkeypatch):
    now = datetime(2026, 7, 15, 12, 0, tzinfo=timezone.utc)
    playlist = _runtime_playlist(
        _runtime_plugin_data("one", "One", latest_refresh_time=None),
        _runtime_plugin_data("two", "Two", latest_refresh_time=None),
    )
    task, device_config, _clock = _make_runtime_task(
        make_test_dir("starved-rotation-deadline-guard"),
        playlists=[playlist],
        cycle_seconds=300,
    )
    device_config.config.update({"theme_mode": "day", "active_theme": "day"})
    device_config.refresh_info.refresh_time = (
        now - timedelta(seconds=600)
    ).isoformat()
    first, second = playlist.plugins
    playlist.plugin_rotation_pool = [first.instance_uuid, second.instance_uuid]
    playlist.plugin_rotation_queue = [first.instance_uuid]
    playlist.plugin_rotation_recent_history = [second.instance_uuid]
    _write_runtime_cache(task, second, Image.new("RGB", (32, 16), "black"))
    monkeypatch.setattr(
        task,
        "_resource_sample",
        lambda: ResourceSample(available_mb=512, swap_percent=0),
    )

    blocked = task._select_cached_display_command(now)
    background = task._select_independent_refresh_command(now)
    conceded = task._select_cached_display_command(
        now + timedelta(seconds=61)
    )

    assert blocked is None
    assert task._rotation_deadline_guard_active is False
    assert task._rotation_starvation_concession_seconds() == 60
    assert background is None
    assert conceded is not None
    assert conceded.instance_uuid == second.instance_uuid


def test_due_rotation_uses_last_good_theme_cache_after_short_recovery_window(
    monkeypatch,
):
    now = datetime(2026, 7, 15, 22, 0, tzinfo=timezone.utc)
    plugin_data = _runtime_plugin_data(
        "themed_plugin",
        "Themed Plugin",
        latest_refresh_time=None,
    )
    plugin_data["plugin_settings"]["themeMode"] = "auto"
    playlist = _runtime_playlist(plugin_data)
    task, device_config, _clock = _make_runtime_task(
        make_test_dir("rotation-theme-cache-recovery"),
        playlists=[playlist],
        cycle_seconds=300,
    )
    device_config.config.update({"theme_mode": "auto", "active_theme": "day"})
    device_config.refresh_info.refresh_time = (
        now - timedelta(seconds=600)
    ).isoformat()
    device_config.get_plugin = lambda _plugin_id: {
        "id": "themed_plugin",
        "_manifest": _theme_manifest("themed_plugin"),
    }
    instance = playlist.plugins[0].snapshot()
    _write_runtime_theme_cache(
        task,
        instance,
        "day",
        Image.new("RGB", (32, 16), "white"),
    )
    task.runtime_state.record_success(
        instance.instance_uuid,
        (now - timedelta(minutes=10)).isoformat(),
        lane=RefreshLane.DATA,
        last_good_cache=LastGoodCacheState(
            theme_mode="day",
            structural_generation=instance.structural_generation,
            settings_revision=instance.settings_revision,
            promoted_at=(now - timedelta(minutes=10)).isoformat(),
        ),
    )
    monkeypatch.setattr(
        "src.refresh_task.get_theme_context",
        lambda _config, now=None: {
            "mode": "night",
            "source": "weather",
            "reason": "sunset",
        },
    )

    initial = task._select_cached_display_command(now)
    still_recovering = task._select_cached_display_command(
        now + timedelta(seconds=29)
    )
    fallback = task._select_cached_display_command(
        now + timedelta(seconds=31)
    )

    assert initial is None
    assert still_recovering is None
    assert refresh_task_module.DEFAULT_ROTATION_CACHE_RECOVERY_SECONDS == 30
    assert fallback is not None
    assert fallback.instance_uuid == instance.instance_uuid
    assert fallback.payload["cache_theme_mode"] == "day"
    assert fallback.payload.get("theme_context") is None

    task._execute_command(fallback)

    assert len(task.display_manager.calls) == 1
    assert task.display_manager.calls[0][0].getpixel((0, 0)) == (255, 255, 255)
    assert device_config.config["active_theme"] == "day"


def _presentation_followup_command(task, playlist, instance, request):
    return task._playlist_command(
        playlist.name,
        instance,
        source=CommandSource.BACKGROUND,
        intent=RefreshIntent.DISPLAY_CACHE,
        force=False,
        display_cached_only=True,
        priority=65,
        kind=CommandKind.DISPLAY,
        current_dt=PRESENTATION_NOW,
        cache_theme_mode=None,
        expected_displayed_instance_uuid=instance.instance_uuid,
        preserve_rotation_anchor=True,
        coalescing_scope=f"presentation-followup:{request.request_id}",
        allow_prepared_presentation=True,
        presentation_request_id=request.request_id,
    )


def test_automatic_rotation_keeps_member_when_transaction_skips_hardware_write(
    monkeypatch,
):
    display = PresentationTransactionDisplayManager(hardware_written=False)
    task, device_config, _clock, playlist, _display = _make_presentation_task(
        "automatic-shuffle-bag-requires-hardware-write",
        display_manager=display,
    )
    instance = playlist.plugins[0].snapshot()
    _write_runtime_cache(task, instance, Image.new("RGB", (32, 16), "black"))
    device_config.refresh_info.refresh_time = (
        PRESENTATION_NOW - timedelta(minutes=2)
    ).isoformat()
    monkeypatch.setattr(task, "_get_current_datetime", lambda: PRESENTATION_NOW)
    _install_display_provider_plugin_sentinels(monkeypatch)
    assert task._select_cached_display_command(PRESENTATION_NOW) is None
    request = task.runtime_state.snapshot().instances[
        instance.instance_uuid
    ].presentation_request
    _seed_prepared_presentation(task, instance, request)
    command = task._select_cached_display_command(
        PRESENTATION_NOW + timedelta(seconds=1)
    )
    before = list(playlist.plugin_rotation_queue)

    with pytest.raises(RuntimeError, match="did not write the panel"):
        task._execute_command(command)

    assert len(display.calls) == 1
    assert playlist.plugin_rotation_queue == before
    assert playlist.is_rotation_reservation_current(instance.instance_uuid) is True
    assert device_config.write_count == 0


def test_exact_manual_display_forces_hardware_without_consuming_shuffle_bag(
    monkeypatch,
):
    task, device_config, _clock, playlist, display = _make_presentation_task(
        "exact-manual-display-forces-panel"
    )
    instance = playlist.plugins[0].snapshot()
    _write_runtime_cache(task, instance, Image.new("RGB", (32, 16), "black"))
    monkeypatch.setattr(task, "_get_current_datetime", lambda: PRESENTATION_NOW)
    _install_display_provider_plugin_sentinels(monkeypatch)
    before = list(playlist.plugin_rotation_queue)
    command = task._playlist_command(
        playlist.name,
        instance,
        source=CommandSource.MANUAL,
        intent=RefreshIntent.DISPLAY_CACHE,
        force=False,
        display_cached_only=True,
        priority=100,
        current_dt=PRESENTATION_NOW,
        force_hardware_write=True,
    )

    task._execute_command(command)

    assert display.calls[0]["force_hardware_write"] is True
    assert playlist.plugin_rotation_queue == before
    assert playlist.is_rotation_reservation_current(instance.instance_uuid) is False


def test_exact_manual_display_rejects_unproven_hardware_write(monkeypatch):
    display = PresentationTransactionDisplayManager(hardware_written=False)
    task, device_config, _clock, playlist, _display = _make_presentation_task(
        "exact-manual-display-requires-panel-proof",
        display_manager=display,
    )
    instance = playlist.plugins[0].snapshot()
    _write_runtime_cache(task, instance, Image.new("RGB", (32, 16), "black"))
    monkeypatch.setattr(task, "_get_current_datetime", lambda: PRESENTATION_NOW)
    _install_display_provider_plugin_sentinels(monkeypatch)
    before = list(playlist.plugin_rotation_queue)
    command = task._playlist_command(
        playlist.name,
        instance,
        source=CommandSource.MANUAL,
        intent=RefreshIntent.DISPLAY_CACHE,
        force=False,
        display_cached_only=True,
        priority=100,
        current_dt=PRESENTATION_NOW,
        force_hardware_write=True,
    )

    with pytest.raises(RuntimeError, match="did not write the panel"):
        task._execute_command(command)

    assert display.calls[0]["force_hardware_write"] is True
    assert playlist.plugin_rotation_queue == before
    assert device_config.write_count == 0


def test_rotation_preflight_keeps_one_coalesced_request_when_deadline_falls_open(
    monkeypatch,
):
    task, device_config, clock, playlist, _display = _make_presentation_task("presentation-normal-display-request")
    instance = playlist.plugins[0].snapshot()
    _write_runtime_cache(task, instance, Image.new("RGB", (32, 16), "black"))
    now = [PRESENTATION_NOW]
    device_config.refresh_info.refresh_time = (now[0] - timedelta(minutes=2)).isoformat()
    monkeypatch.setattr(task, "_get_current_datetime", lambda: now[0])
    monkeypatch.setattr(task, "_memory_watchdog_should_restart", lambda: False)
    monkeypatch.setattr(
        task,
        "_resource_sample",
        lambda: ResourceSample(available_mb=512, swap_percent=0),
    )
    _install_display_provider_plugin_sentinels(monkeypatch)

    assert task._select_cached_display_command(now[0]) is None
    original = task.runtime_state.snapshot().instances[instance.instance_uuid].presentation_request

    assert original is not None
    clock.advance(61)
    now[0] += timedelta(seconds=61)
    fallback = task._select_cached_display_command(now[0])

    assert fallback is not None
    assert fallback.instance_uuid == instance.instance_uuid
    assert fallback.intent is RefreshIntent.DISPLAY_CACHE
    assert fallback.payload["automatic_rotation"] is True
    assert fallback.allow_prepared_presentation is False
    assert task.runtime_state.snapshot().instances[instance.instance_uuid].presentation_request == original


def test_rotation_displays_cached_presentation_plugin_without_request_when_policy_is_off(
    monkeypatch,
):
    task, device_config, _clock, playlist, _display = _make_presentation_task(
        "presentation-policy-off-cached-display"
    )
    device_config.config["display_triggered_refresh_enabled"] = False
    instance = playlist.plugins[0].snapshot()
    _write_runtime_cache(task, instance, Image.new("RGB", (32, 16), "black"))
    device_config.refresh_info.refresh_time = (
        PRESENTATION_NOW - timedelta(minutes=2)
    ).isoformat()
    monkeypatch.setattr(task, "_get_current_datetime", lambda: PRESENTATION_NOW)

    command = task._select_cached_display_command(PRESENTATION_NOW)

    assert command is not None
    assert command.intent is RefreshIntent.DISPLAY_CACHE
    assert command.allow_prepared_presentation is False
    state = task.runtime_state.snapshot().instances.get(instance.instance_uuid)
    assert state is None or state.presentation_request is None


def test_unattested_presentation_fails_closed_when_global_policy_is_off(
    monkeypatch,
):
    task, device_config, _clock, playlist, _display = _make_presentation_task(
        "unattested-policy-off-cached-display",
    )
    device_config.config["display_triggered_refresh_enabled"] = False
    instance = playlist.plugins[0].snapshot()
    _write_runtime_cache(task, instance, Image.new("RGB", (32, 16), "black"))
    device_config.refresh_info.refresh_time = (
        PRESENTATION_NOW - timedelta(minutes=2)
    ).isoformat()
    monkeypatch.setattr(task, "_get_current_datetime", lambda: PRESENTATION_NOW)

    command = task._select_cached_display_command(PRESENTATION_NOW)

    assert command is not None
    assert command.intent is RefreshIntent.DISPLAY_CACHE
    assert command.allow_prepared_presentation is False
    state = task.runtime_state.snapshot().instances.get(instance.instance_uuid)
    assert state is None or state.presentation_request is None


def test_provider_free_presentation_prepares_under_cache_only_and_waits_for_next_display(
    monkeypatch,
):
    task, device_config, _clock, playlist, display = _make_presentation_task(
        "provider-free-presentation-policy-off",
        provider_free=True,
    )
    device_config.config["display_triggered_refresh_enabled"] = False
    instance = playlist.plugins[0].snapshot()
    _write_runtime_cache(task, instance, Image.new("RGB", (32, 16), "black"))
    task.runtime_state.record_success(
        instance.instance_uuid,
        PRESENTATION_NOW.isoformat(),
        lane=RefreshLane.DATA,
    )
    device_config.refresh_info.refresh_time = (
        PRESENTATION_NOW - timedelta(minutes=2)
    ).isoformat()
    now = [PRESENTATION_NOW]
    monkeypatch.setattr(task, "_get_current_datetime", lambda: now[0])
    monkeypatch.setattr(
        task,
        "_resource_sample",
        lambda: ResourceSample(available_mb=512, swap_percent=0),
    )
    plugin = PresentationBankPlugin(prepared_color="white")
    monkeypatch.setattr(
        refresh_task_module,
        "get_plugin_instance",
        lambda _config: plugin,
    )

    assert task._select_cached_display_command(now[0]) is None
    request = task.runtime_state.snapshot().instances[
        instance.instance_uuid
    ].presentation_request
    assert request is not None
    presentation = task._select_independent_refresh_command(now[0])
    assert presentation is not None
    assert presentation.intent is RefreshIntent.PRESENTATION_REFRESH

    prepared_result = _queue_and_process(task, presentation)
    prepared_state = task.runtime_state.snapshot().instances[
        instance.instance_uuid
    ].presentation_request
    candidate = _prepared_presentation_candidate(task, instance, request)
    assert prepared_result.job.status is JobStatus.SUCCEEDED
    assert prepared_state is not None
    assert prepared_state.prepared_at is not None
    assert Path(candidate.cache_path).is_file()
    assert len(display.calls) == 0
    assert task.refresh_queue.take(timeout=0) is None
    assert [event[0] for event in plugin.events] == [
        "mode",
        "reconcile",
        "prepare",
    ]

    monkeypatch.setattr(
        refresh_task_module,
        "get_plugin_instance",
        lambda _config: pytest.fail("DISPLAY_CACHE instantiated a plugin"),
    )
    now[0] += timedelta(seconds=1)
    display_command = task._select_cached_display_command(now[0])
    assert display_command is not None
    assert display_command.intent is RefreshIntent.DISPLAY_CACHE
    assert display_command.allow_prepared_presentation is True
    assert display_command.payload["presentation_request_id"] == request.request_id

    display_result = _queue_and_process(task, display_command)
    final_state = task.runtime_state.snapshot().instances[instance.instance_uuid]
    assert display_result.job.status is JobStatus.SUCCEEDED
    assert final_state.presentation_request is None
    assert final_state.presentation_receipt.request_id == request.request_id
    assert len(display.calls) == 1
    assert display.calls[0]["image"].getpixel((0, 0)) == (255, 255, 255)
    assert Path(candidate.cache_path).exists() is False
    assert task.refresh_queue.take(timeout=0) is None


def test_provider_free_prepared_display_requests_its_next_presentation_when_policy_is_off(
    monkeypatch,
):
    task, device_config, _clock, playlist, display = _make_presentation_task(
        "provider-free-prepared-display-requests-next",
        provider_free=True,
    )
    device_config.config["display_triggered_refresh_enabled"] = False
    instance = playlist.plugins[0].snapshot()
    _write_runtime_cache(task, instance, Image.new("RGB", (32, 16), "black"))
    request = _seed_presentation_request(task, instance)
    _seed_prepared_presentation(task, instance, request)
    task.runtime_state.set_display_state(
        "committed",
        request.origin_display_commit_id,
        instance_uuid=instance.instance_uuid,
        changed_at=request.requested_at,
    )
    monkeypatch.setattr(task, "_get_current_datetime", lambda: PRESENTATION_NOW)
    _install_display_provider_plugin_sentinels(monkeypatch)

    result = _queue_and_process(
        task,
        _presentation_followup_command(task, playlist, instance, request),
    )

    state = task.runtime_state.snapshot().instances[instance.instance_uuid]
    assert result.job.status is JobStatus.SUCCEEDED
    assert state.presentation_receipt.request_id == request.request_id
    assert state.presentation_request is not None
    assert state.presentation_request.request_id != request.request_id
    assert len(display.calls) == 1


def test_provider_refresh_prepares_fresh_image_when_global_policy_is_on(
    monkeypatch,
):
    task, device_config, _clock, playlist, display = _make_presentation_task(
        "provider-refresh-global-policy-on",
        latest_refresh_time=PRESENTATION_NOW.isoformat(),
        interval=60,
        provider_refresh=True,
    )
    device_config.config["display_triggered_refresh_enabled"] = True
    instance = playlist.plugins[0].snapshot()
    _write_runtime_cache(task, instance, Image.new("RGB", (32, 16), "black"))
    task.runtime_state.record_success(
        instance.instance_uuid,
        PRESENTATION_NOW.isoformat(),
        lane=RefreshLane.DATA,
    )
    device_config.refresh_info.refresh_time = (
        PRESENTATION_NOW - timedelta(minutes=2)
    ).isoformat()
    now = [PRESENTATION_NOW]
    monkeypatch.setattr(task, "_get_current_datetime", lambda: now[0])
    monkeypatch.setattr(
        task,
        "_resource_sample",
        lambda: ResourceSample(available_mb=512, swap_percent=0),
    )
    provider_calls = []
    plugin = RefreshOnDisplayRerenderPlugin(provider_calls)
    monkeypatch.setattr(
        refresh_task_module,
        "get_plugin_instance",
        lambda _config: plugin,
    )

    assert task._select_cached_display_command(now[0]) is None
    first_request = task.runtime_state.snapshot().instances[
        instance.instance_uuid
    ].presentation_request
    assert first_request is not None
    first_refresh = task._select_independent_refresh_command(now[0])
    assert first_refresh is not None
    assert first_refresh.intent is RefreshIntent.PRESENTATION_REFRESH
    assert first_refresh.kind is CommandKind.CACHE_REFRESH

    first_prepared = _queue_and_process(task, first_refresh)

    assert first_prepared.job.status is JobStatus.SUCCEEDED
    assert len(provider_calls) == 1
    assert len(display.calls) == 0
    first_prepared_state = task.runtime_state.snapshot().instances[
        instance.instance_uuid
    ]
    assert (
        first_prepared_state.data.last_success_at
        == first_prepared_state.presentation_request.prepared_at
    )
    monkeypatch.setattr(
        refresh_task_module,
        "get_plugin_instance",
        lambda _config: pytest.fail("DISPLAY_CACHE instantiated a plugin"),
    )
    first_display = task._select_cached_display_command(now[0])
    assert first_display is not None
    assert first_display.intent is RefreshIntent.DISPLAY_CACHE
    assert first_display.allow_prepared_presentation is True

    first_display_result = _queue_and_process(task, first_display)

    assert first_display_result.job.status is JobStatus.SUCCEEDED
    assert len(provider_calls) == 1
    assert len(display.calls) == 1
    assert display.calls[-1]["image"].getpixel((0, 0)) == (255, 255, 255)
    first_display_state = task.runtime_state.snapshot().instances[
        instance.instance_uuid
    ]
    assert (
        first_display_state.data.last_success_at
        == first_prepared_state.data.last_success_at
    )
    assert (
        task._select_independent_refresh_command(
            now[0] + timedelta(seconds=1)
        )
        is None
    )

    now[0] += timedelta(seconds=61)
    task.runtime_state.record_success(
        instance.instance_uuid,
        now[0].isoformat(),
        lane=RefreshLane.DATA,
    )
    monkeypatch.setattr(
        refresh_task_module,
        "get_plugin_instance",
        lambda _config: plugin,
    )
    assert task._select_cached_display_command(now[0]) is None
    second_request = task.runtime_state.snapshot().instances[
        instance.instance_uuid
    ].presentation_request
    assert second_request is not None
    assert second_request.request_id != first_request.request_id
    second_refresh = task._select_independent_refresh_command(now[0])
    assert second_refresh is not None
    assert second_refresh.intent is RefreshIntent.PRESENTATION_REFRESH

    second_prepared = _queue_and_process(task, second_refresh)

    assert second_prepared.job.status is JobStatus.SUCCEEDED
    assert len(provider_calls) == 2
    followup = task.refresh_queue.take(timeout=0)
    assert followup is not None
    assert followup.command.intent is RefreshIntent.DISPLAY_CACHE
    task._process_queue_entry(followup)
    monkeypatch.setattr(
        refresh_task_module,
        "get_plugin_instance",
        lambda _config: pytest.fail("DISPLAY_CACHE instantiated a plugin"),
    )
    assert len(provider_calls) == 2
    assert len(display.calls) == 2
    assert display.calls[-1]["image"].getpixel((0, 0)) == (255, 255, 255)


def test_default_cached_display_ignores_pending_presentation_backoff(
    monkeypatch,
):
    task, device_config, _clock, playlist, _display = _make_presentation_task(
        "presentation-policy-off-ignores-backoff"
    )
    device_config.config["display_triggered_refresh_enabled"] = False
    instance = playlist.plugins[0].snapshot()
    _write_runtime_cache(task, instance, Image.new("RGB", (32, 16), "black"))
    device_config.refresh_info.refresh_time = (
        PRESENTATION_NOW - timedelta(minutes=2)
    ).isoformat()
    request = _seed_presentation_request(
        task,
        instance,
        requested_at=PRESENTATION_NOW - timedelta(minutes=1),
    )
    task.runtime_state.record_failure(
        instance.instance_uuid,
        PRESENTATION_NOW.isoformat(),
        RuntimeError("presentation unavailable"),
        (PRESENTATION_NOW + timedelta(minutes=10)).isoformat(),
        lane=RefreshLane.PRESENTATION,
    )
    monkeypatch.setattr(task, "_get_current_datetime", lambda: PRESENTATION_NOW)

    command = task._select_cached_display_command(PRESENTATION_NOW)

    assert command is not None
    assert command.intent is RefreshIntent.DISPLAY_CACHE
    assert command.instance_uuid == instance.instance_uuid
    state = task.runtime_state.snapshot().instances[instance.instance_uuid]
    assert state.presentation_request == request


def test_pending_presentation_is_not_admitted_when_policy_is_off(monkeypatch):
    task, device_config, _clock, playlist, _display = _make_presentation_task(
        "presentation-policy-off-no-admission"
    )
    device_config.config["display_triggered_refresh_enabled"] = False
    instance = playlist.plugins[0].snapshot()
    _write_runtime_cache(task, instance, Image.new("RGB", (32, 16), "black"))
    _seed_presentation_request(
        task,
        instance,
        requested_at=PRESENTATION_NOW - timedelta(minutes=1),
    )
    task.runtime_state.record_success(
        instance.instance_uuid,
        PRESENTATION_NOW.isoformat(),
        lane=RefreshLane.DATA,
    )
    monkeypatch.setattr(
        task,
        "_resource_sample",
        lambda: ResourceSample(available_mb=512, swap_percent=0),
    )

    command = task._select_independent_refresh_command(PRESENTATION_NOW)

    assert command is None


def test_queued_presentation_refresh_is_canceled_if_policy_is_disabled(
    monkeypatch,
):
    task, device_config, _clock, playlist, _display = _make_presentation_task(
        "presentation-policy-disabled-before-execution"
    )
    instance = playlist.plugins[0].snapshot()
    request = _seed_presentation_request(task, instance)
    command = task._playlist_command(
        playlist.name,
        instance,
        source=CommandSource.BACKGROUND,
        intent=RefreshIntent.PRESENTATION_REFRESH,
        force=False,
        display_cached_only=False,
        priority=90,
        kind=CommandKind.CACHE_REFRESH,
        current_dt=PRESENTATION_NOW,
        presentation_request_id=request.request_id,
    )
    device_config.config["display_triggered_refresh_enabled"] = False
    monkeypatch.setattr(
        "src.refresh_task.get_plugin_instance",
        lambda _config: pytest.fail("disabled presentation instantiated plugin"),
    )

    submitted = task.refresh_queue.submit(command)
    task._process_queue_entry(task.refresh_queue.take(timeout=0))
    result = task.refresh_queue.get_entry(submitted.id).job

    assert result.status is JobStatus.CANCELED
    assert result.error_code == "stale_selection"


def test_provider_free_attestation_revoked_during_execution_fails_before_plugin(
    monkeypatch,
):
    task, device_config, _clock, playlist, _display = _make_presentation_task(
        "provider-free-attestation-revoked-during-execution",
        provider_free=True,
    )
    device_config.config["display_triggered_refresh_enabled"] = False
    instance = playlist.plugins[0].snapshot()
    request = _seed_presentation_request(task, instance)
    command = task._playlist_command(
        playlist.name,
        instance,
        source=CommandSource.BACKGROUND,
        intent=RefreshIntent.PRESENTATION_REFRESH,
        force=False,
        display_cached_only=False,
        priority=90,
        kind=CommandKind.CACHE_REFRESH,
        current_dt=PRESENTATION_NOW,
        presentation_request_id=request.request_id,
    )
    attested = _presentation_manifest(instance.plugin_id, provider_free=True)
    revoked = _presentation_manifest(instance.plugin_id, provider_free=False)
    manifests = iter((attested, revoked))
    device_config.get_plugin = lambda plugin_id: {
        "id": plugin_id,
        "refresh_on_display": True,
        "_manifest": next(manifests),
    }
    monkeypatch.setattr(
        refresh_task_module,
        "get_plugin_instance",
        lambda _config: pytest.fail("revoked presentation instantiated plugin"),
    )

    with pytest.raises(
        refresh_task_module._StaleSelection,
        match="presentation refresh policy is no longer enabled",
    ):
        task._execute_command(command)


def test_rotation_preflight_timeout_fails_open_to_cached_display_and_keeps_shuffle_member(
    monkeypatch,
):
    task, device_config, _clock, playlist, display = _make_presentation_task(
        "presentation-timeout-defers-stale-cache",
        plugin_count=2,
    )
    first, second = [plugin.snapshot() for plugin in playlist.plugins]
    playlist.plugin_rotation_pool = [first.instance_uuid, second.instance_uuid]
    playlist.plugin_rotation_queue = [first.instance_uuid, second.instance_uuid]
    playlist.plugin_rotation_recent_history = []
    playlist._plugin_rotation_reserved_key = None
    for instance in (first, second):
        _write_runtime_cache(task, instance, Image.new("RGB", (32, 16), "black"))
    device_config.config["plugin_cycle_interval_seconds"] = 300
    device_config.refresh_info.refresh_time = (
        PRESENTATION_NOW - timedelta(seconds=300)
    ).isoformat()
    device_config.config["rotation_presentation_wait_seconds"] = 999
    now = [PRESENTATION_NOW]
    monkeypatch.setattr(task, "_get_current_datetime", lambda: now[0])
    monkeypatch.setattr(
        task,
        "_resource_sample",
        lambda: ResourceSample(available_mb=512, swap_percent=0),
    )
    _install_display_provider_plugin_sentinels(monkeypatch)

    command = task._select_cached_display_command(PRESENTATION_NOW)

    assert command is not None
    assert command.instance_uuid == first.instance_uuid
    assert command.intent is RefreshIntent.DISPLAY_CACHE
    assert command.payload["automatic_rotation"] is True
    assert command.payload["display_cached_only"] is True
    assert playlist.plugin_rotation_queue == [first.instance_uuid, second.instance_uuid]
    assert playlist.plugin_rotation_recent_history == []
    assert playlist.is_rotation_reservation_current(first.instance_uuid) is True

    result = _queue_and_process(task, command)

    assert result.job.status is JobStatus.SUCCEEDED
    assert len(display.calls) == 1
    assert display.calls[0]["force_hardware_write"] is True
    assert display.calls[0]["logical_target"]["instance_uuid"] == first.instance_uuid
    assert playlist.plugin_rotation_queue == [second.instance_uuid]
    assert playlist.plugin_rotation_recent_history == [first.instance_uuid]
    assert device_config.refresh_info.refresh_time == PRESENTATION_NOW.isoformat()

    now[0] += timedelta(seconds=299)
    assert task._select_cached_display_command(now[0]) is None

    now[0] += timedelta(seconds=1)
    next_command = task._select_cached_display_command(now[0])

    assert next_command is not None
    assert next_command.instance_uuid == second.instance_uuid
    assert next_command.intent is RefreshIntent.DISPLAY_CACHE
    assert next_command.payload["automatic_rotation"] is True

    next_result = _queue_and_process(task, next_command)
    assert next_result.job.status is JobStatus.SUCCEEDED
    assert len(display.calls) == 2
    assert display.calls[1]["force_hardware_write"] is True
    assert display.calls[1]["logical_target"]["instance_uuid"] == second.instance_uuid


def test_rotation_preflight_timeout_fails_open_for_last_shuffle_member(
    monkeypatch,
):
    task, device_config, _clock, playlist, _display = _make_presentation_task(
        "presentation-timeout-last-member-stays-critical",
        plugin_count=2,
    )
    first, second = [plugin.snapshot() for plugin in playlist.plugins]
    playlist.plugin_rotation_pool = [first.instance_uuid, second.instance_uuid]
    playlist.plugin_rotation_queue = [first.instance_uuid]
    playlist.plugin_rotation_recent_history = [second.instance_uuid]
    playlist._plugin_rotation_reserved_key = None
    for instance in (first, second):
        _write_runtime_cache(task, instance, Image.new("RGB", (32, 16), "black"))
    device_config.refresh_info.refresh_time = (
        PRESENTATION_NOW - timedelta(minutes=2)
    ).isoformat()
    device_config.config["rotation_presentation_wait_seconds"] = 0
    monkeypatch.setattr(
        task,
        "_resource_sample",
        lambda: ResourceSample(available_mb=512, swap_percent=0),
    )

    command = task._select_cached_display_command(PRESENTATION_NOW)

    assert command is not None
    assert command.instance_uuid == first.instance_uuid
    assert command.intent is RefreshIntent.DISPLAY_CACHE
    assert command.payload["automatic_rotation"] is True
    assert command.payload["display_cached_only"] is True
    assert playlist.plugin_rotation_queue == [first.instance_uuid]
    assert playlist.is_rotation_reservation_current(first.instance_uuid) is True
    assert device_config.write_count == 0


def test_rotation_preflight_timeout_fails_open_for_only_eligible_member(
    monkeypatch,
):
    task, device_config, _clock, playlist, _display = _make_presentation_task(
        "presentation-timeout-only-eligible-member-stays-critical",
        plugin_count=2,
    )
    first, second = [plugin.snapshot() for plugin in playlist.plugins]
    playlist.plugin_rotation_pool = [first.instance_uuid, second.instance_uuid]
    playlist.plugin_rotation_queue = [first.instance_uuid, second.instance_uuid]
    playlist.plugin_rotation_recent_history = []
    playlist._plugin_rotation_reserved_key = None
    _write_runtime_cache(task, first, Image.new("RGB", (32, 16), "black"))
    device_config.refresh_info.refresh_time = (
        PRESENTATION_NOW - timedelta(minutes=2)
    ).isoformat()
    device_config.config["rotation_presentation_wait_seconds"] = 0
    monkeypatch.setattr(
        task,
        "_resource_sample",
        lambda: ResourceSample(available_mb=512, swap_percent=0),
    )

    command = task._select_cached_display_command(PRESENTATION_NOW)

    assert command is not None
    assert command.instance_uuid == first.instance_uuid
    assert command.intent is RefreshIntent.DISPLAY_CACHE
    assert command.payload["automatic_rotation"] is True
    assert command.payload["display_cached_only"] is True
    assert playlist.plugin_rotation_queue == [
        first.instance_uuid,
        second.instance_uuid,
    ]
    assert playlist.is_rotation_reservation_current(first.instance_uuid) is True
    assert device_config.write_count == 0


def test_rotation_preflight_hard_pressure_still_defers_cached_display(
    monkeypatch,
):
    task, device_config, _clock, playlist, _display = _make_presentation_task(
        "presentation-hard-pressure-defers-cached-display",
        plugin_count=2,
    )
    first, second = [plugin.snapshot() for plugin in playlist.plugins]
    playlist.plugin_rotation_pool = [first.instance_uuid, second.instance_uuid]
    playlist.plugin_rotation_queue = [first.instance_uuid, second.instance_uuid]
    playlist.plugin_rotation_recent_history = []
    playlist._plugin_rotation_reserved_key = None
    for instance in (first, second):
        _write_runtime_cache(task, instance, Image.new("RGB", (32, 16), "black"))
    device_config.refresh_info.refresh_time = (
        PRESENTATION_NOW - timedelta(minutes=2)
    ).isoformat()
    device_config.config["rotation_presentation_wait_seconds"] = 60
    monkeypatch.setattr(
        task,
        "_resource_sample",
        lambda: ResourceSample(available_mb=50, swap_percent=0),
    )

    command = task._select_cached_display_command(PRESENTATION_NOW)

    assert command is None
    assert playlist.plugin_rotation_queue == [
        second.instance_uuid,
        first.instance_uuid,
    ]
    assert playlist.plugin_rotation_recent_history == []
    assert playlist.is_rotation_reservation_current(first.instance_uuid) is False


@pytest.mark.parametrize(
    ("display_policy", "provider_refresh", "provider_free"),
    [(True, False, False), (False, True, False), (False, False, True)],
)
def test_failed_rotation_presentation_releases_member_during_retry_backoff(
    monkeypatch,
    display_policy,
    provider_refresh,
    provider_free,
):
    task, device_config, _clock, playlist, _display = _make_presentation_task(
        "failed-rotation-presentation-yields-during-backoff",
        plugin_count=2,
        provider_refresh=provider_refresh,
        provider_free=provider_free,
    )
    device_config.config["display_triggered_refresh_enabled"] = display_policy
    first, second = [plugin.snapshot() for plugin in playlist.plugins]
    playlist.plugin_rotation_pool = [first.instance_uuid, second.instance_uuid]
    playlist.plugin_rotation_queue = [first.instance_uuid, second.instance_uuid]
    playlist.plugin_rotation_recent_history = []
    playlist._plugin_rotation_reserved_key = None
    _write_runtime_cache(task, first, Image.new("RGB", (32, 16), "black"))
    device_config.refresh_info.refresh_time = (
        PRESENTATION_NOW - timedelta(minutes=2)
    ).isoformat()
    device_config.config["rotation_presentation_wait_seconds"] = 60
    now = [PRESENTATION_NOW]
    monkeypatch.setattr(task, "_get_current_datetime", lambda: now[0])
    monkeypatch.setattr(
        task,
        "_resource_sample",
        lambda: ResourceSample(available_mb=512, swap_percent=0),
    )

    assert task._select_cached_display_command(now[0]) is None
    task.runtime_state.record_attempt(
        first.instance_uuid,
        (now[0] + timedelta(seconds=1)).isoformat(),
        lane=RefreshLane.DATA,
    )
    now[0] += timedelta(seconds=1)
    presentation = task._select_independent_refresh_command(now[0])
    assert presentation is not None
    assert presentation.instance_uuid == first.instance_uuid
    assert presentation.intent is RefreshIntent.PRESENTATION_REFRESH

    task._record_command_failure(
        presentation,
        RuntimeError("presentation bank has no decoded media"),
    )

    failed_state = task.runtime_state.snapshot().instances[first.instance_uuid]
    assert failed_state.presentation.next_retry_at is not None
    assert playlist.is_rotation_reservation_current(first.instance_uuid) is False

    now[0] += timedelta(seconds=1)
    assert task._select_cached_display_command(now[0]) is None
    recovery = task._select_independent_refresh_command(now[0])
    assert recovery is not None
    assert recovery.instance_uuid == second.instance_uuid
    assert recovery.intent is RefreshIntent.DATA_REFRESH


def test_pending_rotation_presentation_deadline_records_backoff_and_releases_member(
    monkeypatch,
):
    task, device_config, _clock, playlist, _display = _make_presentation_task(
        "presentation-deadline-releases-reservation",
        plugin_count=2,
    )
    first, second = [plugin.snapshot() for plugin in playlist.plugins]
    playlist.plugin_rotation_pool = [first.instance_uuid, second.instance_uuid]
    playlist.plugin_rotation_queue = [first.instance_uuid, second.instance_uuid]
    playlist.plugin_rotation_recent_history = []
    playlist._plugin_rotation_reserved_key = first.instance_uuid
    for instance in (first, second):
        _write_runtime_cache(task, instance, Image.new("RGB", (32, 16), "black"))
    device_config.refresh_info.refresh_time = (
        PRESENTATION_NOW - timedelta(minutes=2)
    ).isoformat()
    request = _seed_presentation_request(task, first)
    command = task._playlist_command(
        playlist.name,
        first,
        source=CommandSource.BACKGROUND,
        intent=RefreshIntent.PRESENTATION_REFRESH,
        force=False,
        display_cached_only=False,
        priority=90,
        kind=CommandKind.CACHE_REFRESH,
        current_dt=PRESENTATION_NOW,
        automatic_rotation=True,
        presentation_request_id=request.request_id,
    )
    monkeypatch.setattr(task, "_get_current_datetime", lambda: PRESENTATION_NOW)
    monkeypatch.setattr(
        task,
        "_execute_command",
        lambda _command: (_ for _ in ()).throw(
            TaskDeadlineExceeded("presentation deadline expired")
        ),
    )

    submitted = task.refresh_queue.submit(command)
    task._process_queue_entry(task.refresh_queue.take(timeout=0))

    result = task.refresh_queue.get_entry(submitted.id).job
    state = task.runtime_state.snapshot().instances[first.instance_uuid]
    assert result.status is JobStatus.ABANDONED
    assert result.error_code == "deadline_expired"
    assert state.presentation.last_failure_at is not None
    assert state.presentation.next_retry_at is not None
    assert playlist.is_rotation_reservation_current(first.instance_uuid) is False

    device_config.config["rotation_presentation_wait_seconds"] = 0
    recovery = task._select_cached_display_command(
        PRESENTATION_NOW + timedelta(seconds=1)
    )
    assert recovery is not None
    assert recovery.instance_uuid == second.instance_uuid
    assert recovery.intent is RefreshIntent.DISPLAY_CACHE


@pytest.mark.parametrize(
    ("intent", "kind", "display_cached_only"),
    [
        (RefreshIntent.DATA_REFRESH, CommandKind.CACHE_REFRESH, False),
        (RefreshIntent.DISPLAY_CACHE, CommandKind.DISPLAY, True),
    ],
)
def test_automatic_rotation_command_deadline_backs_off_member_and_moves_on(
    monkeypatch,
    intent,
    kind,
    display_cached_only,
):
    task, device_config, _clock, playlist, _display = _make_presentation_task(
        f"automatic-{intent.value}-deadline-releases-reservation",
        plugin_count=2,
    )
    first, second = [plugin.snapshot() for plugin in playlist.plugins]
    playlist.plugin_rotation_pool = [first.instance_uuid, second.instance_uuid]
    playlist.plugin_rotation_queue = [first.instance_uuid, second.instance_uuid]
    playlist.plugin_rotation_recent_history = []
    playlist._plugin_rotation_reserved_key = first.instance_uuid
    for instance in (first, second):
        _write_runtime_cache(task, instance, Image.new("RGB", (32, 16), "black"))
    device_config.refresh_info.refresh_time = (
        PRESENTATION_NOW - timedelta(minutes=5)
    ).isoformat()
    device_config.config["rotation_presentation_wait_seconds"] = 0
    command = task._playlist_command(
        playlist.name,
        first,
        source=(
            CommandSource.BACKGROUND
            if intent is RefreshIntent.DATA_REFRESH
            else CommandSource.SCHEDULER
        ),
        intent=intent,
        force=False,
        display_cached_only=display_cached_only,
        priority=95,
        kind=kind,
        current_dt=PRESENTATION_NOW,
        automatic_rotation=True,
    )
    monkeypatch.setattr(task, "_get_current_datetime", lambda: PRESENTATION_NOW)
    monkeypatch.setattr(
        task,
        "_execute_command",
        lambda _command: (_ for _ in ()).throw(
            TaskDeadlineExceeded(f"{intent.value} deadline expired")
        ),
    )

    submitted = task.refresh_queue.submit(command)
    task._process_queue_entry(task.refresh_queue.take(timeout=0))

    result = task.refresh_queue.get_entry(submitted.id).job
    assert result.status is JobStatus.ABANDONED
    assert result.error_code == "deadline_expired"
    failed_state = task.runtime_state.snapshot().instances.get(first.instance_uuid)
    if intent is RefreshIntent.DATA_REFRESH:
        assert failed_state is not None
        assert failed_state.data.last_failure_at is not None
        assert failed_state.data.next_retry_at is not None
    else:
        assert failed_state is None or failed_state.data.last_failure_at is None
        assert failed_state is None or failed_state.data.next_retry_at is None
        assert task.retry_registry.next_delay(
            task._rotation_display_retry_key(first.instance_uuid),
            task._clock(),
        ) > 0
    assert playlist.is_rotation_reservation_current(first.instance_uuid) is False

    recovery = task._select_cached_display_command(
        PRESENTATION_NOW + timedelta(seconds=1)
    )
    assert recovery is not None
    assert recovery.instance_uuid == second.instance_uuid
    assert recovery.intent is RefreshIntent.DISPLAY_CACHE


def test_queued_automatic_rotation_deadline_runs_cleanup_before_retry(
    monkeypatch,
):
    task, device_config, clock, playlist, display = _make_presentation_task(
        "queued-automatic-display-deadline-runs-cleanup",
        plugin_count=2,
    )
    first, second = [plugin.snapshot() for plugin in playlist.plugins]
    playlist.plugin_rotation_pool = [first.instance_uuid, second.instance_uuid]
    playlist.plugin_rotation_queue = [first.instance_uuid, second.instance_uuid]
    playlist.plugin_rotation_recent_history = []
    playlist._plugin_rotation_reserved_key = first.instance_uuid
    for instance in (first, second):
        _write_runtime_cache(task, instance, Image.new("RGB", (32, 16), "black"))
    device_config.refresh_info.refresh_time = (
        PRESENTATION_NOW - timedelta(minutes=5)
    ).isoformat()
    device_config.config["rotation_presentation_wait_seconds"] = 0
    command = task._playlist_command(
        playlist.name,
        first,
        source=CommandSource.SCHEDULER,
        intent=RefreshIntent.DISPLAY_CACHE,
        force=False,
        display_cached_only=True,
        priority=95,
        kind=CommandKind.DISPLAY,
        current_dt=PRESENTATION_NOW,
        automatic_rotation=True,
    )
    submitted = task.refresh_queue.submit(command)
    clock.advance(181)
    entry = task.refresh_queue.take(timeout=0)
    assert entry is not None
    monkeypatch.setattr(task, "_get_current_datetime", lambda: PRESENTATION_NOW)

    task._process_queue_entry(entry)

    result = task.refresh_queue.get_entry(submitted.id).job
    assert result.status is JobStatus.ABANDONED
    assert result.error_code == "deadline_expired"
    assert display.calls == []
    assert playlist.is_rotation_reservation_current(first.instance_uuid) is False
    assert task.retry_registry.next_delay(
        task._rotation_display_retry_key(first.instance_uuid),
        task._clock(),
    ) > 0
    recovery = task._select_cached_display_command(
        PRESENTATION_NOW + timedelta(seconds=1)
    )
    assert recovery is not None
    assert recovery.instance_uuid == second.instance_uuid


def test_expired_automatic_data_is_cleaned_before_ian_admission(
    monkeypatch,
):
    task, _device_config, clock, playlist, _display = _make_presentation_task(
        "expired-automatic-data-is-cleaned-before-ian",
        plugin_count=2,
    )
    first, second = [plugin.snapshot() for plugin in playlist.plugins]
    playlist.plugin_rotation_pool = [first.instance_uuid, second.instance_uuid]
    playlist.plugin_rotation_queue = [first.instance_uuid, second.instance_uuid]
    playlist.plugin_rotation_recent_history = []
    playlist._plugin_rotation_reserved_key = first.instance_uuid
    historical_success = task._runtime_now_iso(offset_seconds=-3600)
    task.runtime_state.record_success(
        first.instance_uuid,
        historical_success,
        lane=RefreshLane.DATA,
    )
    command = task._playlist_command(
        playlist.name,
        first,
        source=CommandSource.BACKGROUND,
        intent=RefreshIntent.DATA_REFRESH,
        force=False,
        display_cached_only=False,
        priority=95,
        kind=CommandKind.CACHE_REFRESH,
        current_dt=PRESENTATION_NOW,
        automatic_rotation=True,
    )
    submitted = task.refresh_queue.submit(command)
    clock.advance(181)
    entry = task.refresh_queue.take(timeout=0)
    assert entry is not None
    monkeypatch.setattr(task, "_get_current_datetime", lambda: PRESENTATION_NOW)
    monkeypatch.setattr(task, "_uses_ian_admission", lambda _command: True)
    monkeypatch.setattr(
        task,
        "_process_ian_queue_entry",
        lambda _entry: pytest.fail("expired work reached Ian admission"),
    )

    task._process_queue_entry(entry)

    result = task.refresh_queue.get_entry(submitted.id).job
    state = task.runtime_state.snapshot().instances[first.instance_uuid]
    assert result.status is JobStatus.ABANDONED
    assert result.error_code == "deadline_expired"
    assert state.data.last_success_at == historical_success
    assert state.data.last_failure_at is not None
    assert state.data.next_retry_at is not None
    assert playlist.is_rotation_reservation_current(first.instance_uuid) is False


def test_expired_retained_ian_rotation_entry_is_removed_before_cleanup_returns(
    monkeypatch,
):
    task, _device_config, clock, playlist, _display = _make_presentation_task(
        "expired-retained-ian-entry-is-removed"
    )
    instance = playlist.plugins[0].snapshot()
    playlist.plugin_rotation_pool = [instance.instance_uuid]
    playlist.plugin_rotation_queue = [instance.instance_uuid]
    playlist.plugin_rotation_recent_history = []
    playlist._plugin_rotation_reserved_key = instance.instance_uuid
    command = task._playlist_command(
        playlist.name,
        instance,
        source=CommandSource.BACKGROUND,
        intent=RefreshIntent.DATA_REFRESH,
        force=False,
        display_cached_only=False,
        priority=95,
        kind=CommandKind.CACHE_REFRESH,
        current_dt=PRESENTATION_NOW,
        automatic_rotation=True,
    )
    submitted = task.refresh_queue.submit(command)
    entry = task.refresh_queue.take(timeout=0)
    assert entry is not None
    task._ian_retained_entries[command.id] = entry
    task._ian_recorded_deferrals.add(command.id)
    clock.advance(181)
    monkeypatch.setattr(task, "_get_current_datetime", lambda: PRESENTATION_NOW)

    task._process_queue_entry(entry)

    result = task.refresh_queue.get_entry(submitted.id).job
    assert result.status is JobStatus.ABANDONED
    assert result.error_code == "deadline_expired"
    assert command.id not in task._ian_retained_entries
    assert command.id not in task._ian_recorded_deferrals
    assert task._ian_entry_ready_to_resume() is None
    assert playlist.is_rotation_reservation_current(instance.instance_uuid) is False


def test_ian_deadline_after_executor_success_does_not_reclassify_rotation(
    monkeypatch,
):
    task, _device_config, _clock, playlist, _display = _make_presentation_task(
        "ian-deadline-after-executor-success"
    )
    instance = playlist.plugins[0].snapshot()
    playlist.plugin_rotation_pool = [instance.instance_uuid]
    playlist.plugin_rotation_queue = [instance.instance_uuid]
    playlist.plugin_rotation_recent_history = []
    playlist._plugin_rotation_reserved_key = instance.instance_uuid
    historical_success = task._runtime_now_iso(offset_seconds=-3600)
    task.runtime_state.record_success(
        instance.instance_uuid,
        historical_success,
        lane=RefreshLane.DATA,
    )
    command = task._playlist_command(
        playlist.name,
        instance,
        source=CommandSource.BACKGROUND,
        intent=RefreshIntent.DATA_REFRESH,
        force=False,
        display_cached_only=False,
        priority=95,
        kind=CommandKind.CACHE_REFRESH,
        current_dt=PRESENTATION_NOW,
        automatic_rotation=True,
    )
    submitted = task.refresh_queue.submit(command)
    entry = task.refresh_queue.take(timeout=0)
    assert entry is not None
    request = task._ian_request_adapter(command)
    monkeypatch.setattr(task, "_execute_command", lambda _command: None)

    def deadline_after_success():
        task._execute_queue_entry(entry, ian_admitted=True)
        return SimpleNamespace(
            status=refresh_task_module.IanTurnStatus.DEADLINE_EXPIRED,
            request=request,
            reason="ian_deadline_expired_after_execution",
        )

    monkeypatch.setattr(task._ian, "run_turn", deadline_after_success)

    task._process_ian_queue_entry(entry)

    result = task.refresh_queue.get_entry(submitted.id).job
    state = task.runtime_state.snapshot().instances[instance.instance_uuid]
    assert result.status is JobStatus.SUCCEEDED
    assert state.data.last_success_at == historical_success
    assert state.data.last_failure_at is None
    assert state.data.next_retry_at is None
    assert playlist.is_rotation_reservation_current(instance.instance_uuid) is True
    assert command.id not in task._ian_retained_entries
    assert command.id not in task._ian_recorded_deferrals


def test_single_rotation_member_display_deadline_waits_for_transient_backoff(
    monkeypatch,
):
    task, device_config, clock, playlist, _display = _make_presentation_task(
        "single-member-display-deadline-waits-for-backoff"
    )
    instance = playlist.plugins[0].snapshot()
    playlist.plugin_rotation_pool = [instance.instance_uuid]
    playlist.plugin_rotation_queue = [instance.instance_uuid]
    playlist.plugin_rotation_recent_history = []
    playlist._plugin_rotation_reserved_key = instance.instance_uuid
    _write_runtime_cache(task, instance, Image.new("RGB", (32, 16), "black"))
    device_config.refresh_info.refresh_time = (
        PRESENTATION_NOW - timedelta(minutes=5)
    ).isoformat()
    device_config.config["rotation_presentation_wait_seconds"] = 0
    command = task._playlist_command(
        playlist.name,
        instance,
        source=CommandSource.SCHEDULER,
        intent=RefreshIntent.DISPLAY_CACHE,
        force=False,
        display_cached_only=True,
        priority=95,
        kind=CommandKind.DISPLAY,
        current_dt=PRESENTATION_NOW,
        automatic_rotation=True,
    )
    monkeypatch.setattr(task, "_get_current_datetime", lambda: PRESENTATION_NOW)
    monkeypatch.setattr(
        task,
        "_execute_command",
        lambda _command: (_ for _ in ()).throw(
            TaskDeadlineExceeded("single member display deadline")
        ),
    )

    _queue_and_process(task, command)

    assert playlist.plugin_rotation_queue == [instance.instance_uuid]
    assert playlist.plugin_rotation_recent_history == []
    assert playlist.is_rotation_reservation_current(instance.instance_uuid) is False
    assert task._select_cached_display_command(
        PRESENTATION_NOW + timedelta(seconds=1)
    ) is None

    clock.advance(34)
    retry = task._select_cached_display_command(
        PRESENTATION_NOW + timedelta(seconds=35)
    )
    assert retry is not None
    assert retry.instance_uuid == instance.instance_uuid
    assert retry.intent is RefreshIntent.DISPLAY_CACHE
    assert playlist.is_rotation_reservation_current(instance.instance_uuid) is True


@pytest.mark.parametrize("manual_first", [False, True])
def test_manual_data_coalesced_with_automatic_rotation_keeps_failure_ownership(
    monkeypatch,
    manual_first,
):
    task, _device_config, _clock, playlist, _display = _make_presentation_task(
        "manual-data-coalesced-with-automatic-rotation",
        plugin_count=2,
    )
    first, second = [plugin.snapshot() for plugin in playlist.plugins]
    playlist.plugin_rotation_pool = [first.instance_uuid, second.instance_uuid]
    playlist.plugin_rotation_queue = [first.instance_uuid, second.instance_uuid]
    playlist.plugin_rotation_recent_history = []
    playlist._plugin_rotation_reserved_key = first.instance_uuid
    automatic = task._playlist_command(
        playlist.name,
        first,
        source=CommandSource.BACKGROUND,
        intent=RefreshIntent.DATA_REFRESH,
        force=False,
        display_cached_only=False,
        priority=95,
        kind=CommandKind.CACHE_REFRESH,
        current_dt=PRESENTATION_NOW,
        automatic_rotation=True,
    )
    manual = task._playlist_command(
        playlist.name,
        first,
        source=CommandSource.MANUAL,
        intent=RefreshIntent.DATA_REFRESH,
        force=True,
        display_cached_only=False,
        priority=100,
        kind=CommandKind.CACHE_REFRESH,
        current_dt=PRESENTATION_NOW,
        automatic_rotation=False,
    )
    first_command, second_command = (
        (manual, automatic) if manual_first else (automatic, manual)
    )
    first_job = task.refresh_queue.submit(first_command)
    second_job = task.refresh_queue.submit(second_command)
    entry = task.refresh_queue.take(timeout=0)
    assert second_job.id == first_job.id
    assert entry.command.source is CommandSource.MANUAL
    assert entry.command.payload["automatic_rotation"] is True
    assert entry.command.payload["rotation_deadline_cleanup"] is True
    monkeypatch.setattr(task, "_get_current_datetime", lambda: PRESENTATION_NOW)
    monkeypatch.setattr(
        task,
        "_execute_command",
        lambda _command: (_ for _ in ()).throw(
            TaskDeadlineExceeded("coalesced data deadline expired")
        ),
    )

    task._process_queue_entry(entry)

    result = task.refresh_queue.get_entry(first_job.id).job
    state = task.runtime_state.snapshot().instances[first.instance_uuid]
    assert result.status is JobStatus.ABANDONED
    assert result.error_code == "deadline_expired"
    assert state.data.last_failure_at is not None
    assert state.data.next_retry_at is not None
    assert playlist.is_rotation_reservation_current(first.instance_uuid) is False


def test_automatic_rotation_exception_after_deadline_backs_off_member_and_moves_on(
    monkeypatch,
):
    task, device_config, clock, playlist, _display = _make_presentation_task(
        "automatic-exception-after-deadline-releases-reservation",
        plugin_count=2,
    )
    first, second = [plugin.snapshot() for plugin in playlist.plugins]
    playlist.plugin_rotation_pool = [first.instance_uuid, second.instance_uuid]
    playlist.plugin_rotation_queue = [first.instance_uuid, second.instance_uuid]
    playlist.plugin_rotation_recent_history = []
    playlist._plugin_rotation_reserved_key = first.instance_uuid
    for instance in (first, second):
        _write_runtime_cache(task, instance, Image.new("RGB", (32, 16), "black"))
    device_config.refresh_info.refresh_time = (
        PRESENTATION_NOW - timedelta(minutes=5)
    ).isoformat()
    device_config.config["rotation_presentation_wait_seconds"] = 0
    command = task._playlist_command(
        playlist.name,
        first,
        source=CommandSource.BACKGROUND,
        intent=RefreshIntent.DATA_REFRESH,
        force=False,
        display_cached_only=False,
        priority=95,
        kind=CommandKind.CACHE_REFRESH,
        current_dt=PRESENTATION_NOW,
        automatic_rotation=True,
    )
    monkeypatch.setattr(task, "_get_current_datetime", lambda: PRESENTATION_NOW)

    def fail_after_deadline(_command):
        clock.advance(181)
        raise RuntimeError("renderer failed after its deadline")

    monkeypatch.setattr(task, "_execute_command", fail_after_deadline)

    submitted = task.refresh_queue.submit(command)
    task._process_queue_entry(task.refresh_queue.take(timeout=0))

    result = task.refresh_queue.get_entry(submitted.id).job
    failed_state = task.runtime_state.snapshot().instances[first.instance_uuid]
    assert result.status is JobStatus.ABANDONED
    assert result.error_code == "deadline_expired"
    assert failed_state.data.last_failure_at is not None
    assert failed_state.data.next_retry_at is not None
    assert playlist.is_rotation_reservation_current(first.instance_uuid) is False

    recovery = task._select_cached_display_command(
        PRESENTATION_NOW + timedelta(seconds=1)
    )
    assert recovery is not None
    assert recovery.instance_uuid == second.instance_uuid
    assert recovery.intent is RefreshIntent.DISPLAY_CACHE


def test_automatic_rotation_checkpoint_after_deadline_releases_member(
    monkeypatch,
):
    task, _device_config, clock, playlist, _display = _make_presentation_task(
        "automatic-checkpoint-after-deadline-releases-reservation",
        plugin_count=2,
    )
    first, second = [plugin.snapshot() for plugin in playlist.plugins]
    playlist.plugin_rotation_pool = [first.instance_uuid, second.instance_uuid]
    playlist.plugin_rotation_queue = [first.instance_uuid, second.instance_uuid]
    playlist.plugin_rotation_recent_history = []
    playlist._plugin_rotation_reserved_key = first.instance_uuid
    command = task._playlist_command(
        playlist.name,
        first,
        source=CommandSource.BACKGROUND,
        intent=RefreshIntent.DATA_REFRESH,
        force=False,
        display_cached_only=False,
        priority=95,
        kind=CommandKind.CACHE_REFRESH,
        current_dt=PRESENTATION_NOW,
        automatic_rotation=True,
    )
    monkeypatch.setattr(task, "_get_current_datetime", lambda: PRESENTATION_NOW)

    def checkpoint_after_deadline(_command):
        clock.advance(181)
        raise SportsIsolatedCheckpointPending(
            fingerprint="c" * 64,
            completed_regions=("esports",),
            next_region="football",
        )

    monkeypatch.setattr(task, "_execute_command", checkpoint_after_deadline)

    submitted = task.refresh_queue.submit(command)
    task._process_queue_entry(task.refresh_queue.take(timeout=0))

    result = task.refresh_queue.get_entry(submitted.id).job
    failed_state = task.runtime_state.snapshot().instances[first.instance_uuid]
    assert result.status is JobStatus.ABANDONED
    assert result.error_code == "deadline_expired"
    assert failed_state.data.last_failure_at is not None
    assert failed_state.data.next_retry_at is not None
    assert playlist.is_rotation_reservation_current(first.instance_uuid) is False


def test_automatic_rotation_deadline_during_checkpoint_yield_releases_member(
    monkeypatch,
):
    task, device_config, clock, playlist, _display = _make_presentation_task(
        "automatic-deadline-during-checkpoint-yield-releases-reservation",
        plugin_count=2,
    )
    first, second = [plugin.snapshot() for plugin in playlist.plugins]
    playlist.plugin_rotation_pool = [first.instance_uuid, second.instance_uuid]
    playlist.plugin_rotation_queue = [first.instance_uuid, second.instance_uuid]
    playlist.plugin_rotation_recent_history = []
    playlist._plugin_rotation_reserved_key = first.instance_uuid
    for instance in (first, second):
        _write_runtime_cache(task, instance, Image.new("RGB", (32, 16), "black"))
    device_config.refresh_info.refresh_time = (
        PRESENTATION_NOW - timedelta(minutes=5)
    ).isoformat()
    device_config.config["rotation_presentation_wait_seconds"] = 0
    command = task._playlist_command(
        playlist.name,
        first,
        source=CommandSource.BACKGROUND,
        intent=RefreshIntent.DATA_REFRESH,
        force=False,
        display_cached_only=False,
        priority=95,
        kind=CommandKind.CACHE_REFRESH,
        current_dt=PRESENTATION_NOW,
        automatic_rotation=True,
    )
    monkeypatch.setattr(task, "_get_current_datetime", lambda: PRESENTATION_NOW)
    monkeypatch.setattr(
        task,
        "_execute_command",
        lambda _command: (_ for _ in ()).throw(
            SportsIsolatedCheckpointPending(
                fingerprint="d" * 64,
                completed_regions=("esports",),
                next_region="football",
            )
        ),
    )
    original_yield_running = task.refresh_queue.yield_running

    def deadline_during_yield(job_id):
        clock.advance(181)
        return original_yield_running(job_id)

    monkeypatch.setattr(
        task.refresh_queue,
        "yield_running",
        deadline_during_yield,
    )

    submitted = task.refresh_queue.submit(command)
    task._process_queue_entry(task.refresh_queue.take(timeout=0))

    result = task.refresh_queue.get_entry(submitted.id).job
    failed_state = task.runtime_state.snapshot().instances[first.instance_uuid]
    assert result.status is JobStatus.CANCELED
    assert result.error_code == "deadline_expired"
    assert failed_state.data.last_failure_at is not None
    assert failed_state.data.next_retry_at is not None
    assert playlist.is_rotation_reservation_current(first.instance_uuid) is False
    recovery = task._select_cached_display_command(
        PRESENTATION_NOW + timedelta(seconds=1)
    )
    assert recovery is not None
    assert recovery.instance_uuid == second.instance_uuid


def test_canceled_rotation_during_checkpoint_yield_keeps_reservation(
    monkeypatch,
):
    task, _device_config, clock, playlist, _display = _make_presentation_task(
        "canceled-during-checkpoint-yield-keeps-reservation"
    )
    instance = playlist.plugins[0].snapshot()
    playlist.plugin_rotation_pool = [instance.instance_uuid]
    playlist.plugin_rotation_queue = [instance.instance_uuid]
    playlist.plugin_rotation_recent_history = []
    playlist._plugin_rotation_reserved_key = instance.instance_uuid
    command = task._playlist_command(
        playlist.name,
        instance,
        source=CommandSource.BACKGROUND,
        intent=RefreshIntent.DATA_REFRESH,
        force=False,
        display_cached_only=False,
        priority=95,
        kind=CommandKind.CACHE_REFRESH,
        current_dt=PRESENTATION_NOW,
        automatic_rotation=True,
    )
    monkeypatch.setattr(task, "_get_current_datetime", lambda: PRESENTATION_NOW)
    monkeypatch.setattr(
        task,
        "_execute_command",
        lambda _command: (_ for _ in ()).throw(
            SportsIsolatedCheckpointPending(
                fingerprint="e" * 64,
                completed_regions=("esports",),
                next_region="football",
            )
        ),
    )
    original_yield_running = task.refresh_queue.yield_running

    def cancel_and_expire_during_yield(job_id):
        assert task.refresh_queue.cancel_instance(instance.instance_uuid) == 1
        clock.advance(181)
        return original_yield_running(job_id)

    monkeypatch.setattr(
        task.refresh_queue,
        "yield_running",
        cancel_and_expire_during_yield,
    )

    submitted = task.refresh_queue.submit(command)
    task._process_queue_entry(task.refresh_queue.take(timeout=0))

    result = task.refresh_queue.get_entry(submitted.id).job
    state = task.runtime_state.snapshot().instances[instance.instance_uuid]
    assert result.status is JobStatus.CANCELED
    assert result.error_code == "deadline_expired"
    assert result.cancel_requested_at is not None
    assert state.data.last_failure_at is None
    assert state.data.next_retry_at is None
    assert playlist.is_rotation_reservation_current(instance.instance_uuid) is True


def test_automatic_rotation_expired_at_submit_releases_member(monkeypatch):
    task, device_config, clock, playlist, _display = _make_presentation_task(
        "automatic-expired-at-submit-releases-reservation",
        plugin_count=2,
    )
    first, second = [plugin.snapshot() for plugin in playlist.plugins]
    playlist.plugin_rotation_pool = [first.instance_uuid, second.instance_uuid]
    playlist.plugin_rotation_queue = [first.instance_uuid, second.instance_uuid]
    playlist.plugin_rotation_recent_history = []
    playlist._plugin_rotation_reserved_key = first.instance_uuid
    for instance in (first, second):
        _write_runtime_cache(task, instance, Image.new("RGB", (32, 16), "black"))
    device_config.refresh_info.refresh_time = (
        PRESENTATION_NOW - timedelta(minutes=5)
    ).isoformat()
    device_config.config["rotation_presentation_wait_seconds"] = 0
    command = task._playlist_command(
        playlist.name,
        first,
        source=CommandSource.BACKGROUND,
        intent=RefreshIntent.DATA_REFRESH,
        force=False,
        display_cached_only=False,
        priority=95,
        kind=CommandKind.CACHE_REFRESH,
        current_dt=PRESENTATION_NOW,
        deadline_monotonic=clock.monotonic(),
        automatic_rotation=True,
    )
    monkeypatch.setattr(task, "_get_current_datetime", lambda: PRESENTATION_NOW)

    submitted = task.refresh_queue.submit(command)
    entry = task.refresh_queue.take(timeout=0)
    assert entry is not None
    task._process_queue_entry(entry)

    result = task.refresh_queue.get_entry(submitted.id).job
    failed_state = task.runtime_state.snapshot().instances[first.instance_uuid]
    assert result.status is JobStatus.ABANDONED
    assert result.error_code == "deadline_expired"
    assert failed_state.data.last_failure_at is not None
    assert failed_state.data.next_retry_at is not None
    assert playlist.is_rotation_reservation_current(first.instance_uuid) is False
    recovery = task._select_cached_display_command(
        PRESENTATION_NOW + timedelta(seconds=1)
    )
    assert recovery is not None
    assert recovery.instance_uuid == second.instance_uuid


def test_successful_data_result_before_deadline_abort_is_not_reclassified_as_failure(
    monkeypatch,
):
    task, _device_config, clock, playlist, _display = _make_presentation_task(
        "successful-data-result-before-deadline-abort-keeps-reservation"
    )
    instance = playlist.plugins[0].snapshot()
    playlist.plugin_rotation_pool = [instance.instance_uuid]
    playlist.plugin_rotation_queue = [instance.instance_uuid]
    playlist.plugin_rotation_recent_history = []
    playlist._plugin_rotation_reserved_key = instance.instance_uuid
    command = task._playlist_command(
        playlist.name,
        instance,
        source=CommandSource.BACKGROUND,
        intent=RefreshIntent.DATA_REFRESH,
        force=False,
        display_cached_only=False,
        priority=95,
        kind=CommandKind.CACHE_REFRESH,
        current_dt=PRESENTATION_NOW,
        automatic_rotation=True,
    )
    monkeypatch.setattr(task, "_get_current_datetime", lambda: PRESENTATION_NOW)

    def succeed_then_fail_after_deadline(_command):
        task.runtime_state.record_success(
            instance.instance_uuid,
            task._runtime_now_iso(offset_seconds=1),
            lane=RefreshLane.DATA,
        )
        clock.advance(181)
        raise RuntimeError("post-commit bookkeeping failed after deadline")

    monkeypatch.setattr(task, "_execute_command", succeed_then_fail_after_deadline)

    submitted = task.refresh_queue.submit(command)
    task._process_queue_entry(task.refresh_queue.take(timeout=0))

    result = task.refresh_queue.get_entry(submitted.id).job
    state = task.runtime_state.snapshot().instances[instance.instance_uuid]
    assert result.status is JobStatus.ABANDONED
    assert result.error_code == "deadline_expired"
    assert state.data.last_success_at is not None
    assert state.data.last_failure_at is None
    assert state.data.next_retry_at is None
    assert playlist.is_rotation_reservation_current(instance.instance_uuid) is True


def test_existing_data_failure_is_not_counted_twice_by_deadline_classification(
    monkeypatch,
):
    task, _device_config, _clock, playlist, _display = _make_presentation_task(
        "existing-data-failure-is-not-counted-twice"
    )
    instance = playlist.plugins[0].snapshot()
    playlist.plugin_rotation_pool = [instance.instance_uuid]
    playlist.plugin_rotation_queue = [instance.instance_uuid]
    playlist.plugin_rotation_recent_history = []
    playlist._plugin_rotation_reserved_key = instance.instance_uuid
    command = task._playlist_command(
        playlist.name,
        instance,
        source=CommandSource.BACKGROUND,
        intent=RefreshIntent.DATA_REFRESH,
        force=False,
        display_cached_only=False,
        priority=95,
        kind=CommandKind.CACHE_REFRESH,
        current_dt=PRESENTATION_NOW,
        automatic_rotation=True,
    )
    monkeypatch.setattr(task, "_get_current_datetime", lambda: PRESENTATION_NOW)

    def record_failure_then_hit_deadline(_command):
        task._record_intent_failure(
            command,
            RuntimeError("data attempt already failed"),
            PRESENTATION_NOW,
        )
        raise TaskDeadlineExceeded("deadline after recorded data failure")

    monkeypatch.setattr(task, "_execute_command", record_failure_then_hit_deadline)

    result = _queue_and_process(task, command)

    retry_key = task._lane_retry_key(instance.instance_uuid, RefreshLane.DATA)
    retry_entry = next(
        entry
        for entry in task.retry_registry.snapshot()
        if entry.key == retry_key
    )
    state = task.runtime_state.snapshot().instances[instance.instance_uuid]
    assert result.job.status is JobStatus.ABANDONED
    assert retry_entry.failure_count == 1
    assert state.data.last_failure_at is not None
    assert state.data.next_retry_at is not None
    assert playlist.is_rotation_reservation_current(instance.instance_uuid) is True


def test_nonautomatic_presentation_deadline_records_request_backoff_without_rotation_mutation(
    monkeypatch,
):
    task, _device_config, _clock, playlist, _display = _make_presentation_task(
        "nonautomatic-presentation-deadline-keeps-rotation-reservation"
    )
    instance = playlist.plugins[0].snapshot()
    playlist.plugin_rotation_pool = [instance.instance_uuid]
    playlist.plugin_rotation_queue = [instance.instance_uuid]
    playlist.plugin_rotation_recent_history = []
    playlist._plugin_rotation_reserved_key = instance.instance_uuid
    request = _seed_presentation_request(task, instance)
    command = task._playlist_command(
        playlist.name,
        instance,
        source=CommandSource.BACKGROUND,
        intent=RefreshIntent.PRESENTATION_REFRESH,
        force=False,
        display_cached_only=False,
        priority=90,
        kind=CommandKind.CACHE_REFRESH,
        current_dt=PRESENTATION_NOW,
        automatic_rotation=False,
        presentation_request_id=request.request_id,
    )
    monkeypatch.setattr(task, "_get_current_datetime", lambda: PRESENTATION_NOW)
    monkeypatch.setattr(
        task,
        "_execute_command",
        lambda _command: (_ for _ in ()).throw(
            TaskDeadlineExceeded("nonautomatic presentation deadline expired")
        ),
    )

    submitted = task.refresh_queue.submit(command)
    task._process_queue_entry(task.refresh_queue.take(timeout=0))

    result = task.refresh_queue.get_entry(submitted.id).job
    state = task.runtime_state.snapshot().instances[instance.instance_uuid]
    assert result.status is JobStatus.ABANDONED
    assert result.error_code == "deadline_expired"
    assert state.presentation.last_failure_at is not None
    assert state.presentation.next_retry_at is not None
    assert playlist.is_rotation_reservation_current(instance.instance_uuid) is True


def test_stale_automatic_rotation_deadline_does_not_pollute_current_reservation(
    monkeypatch,
):
    task, _device_config, _clock, playlist, _display = _make_presentation_task(
        "stale-automatic-deadline-keeps-current-reservation",
        plugin_count=2,
    )
    first, second = [plugin.snapshot() for plugin in playlist.plugins]
    playlist.plugin_rotation_pool = [first.instance_uuid, second.instance_uuid]
    playlist.plugin_rotation_queue = [first.instance_uuid, second.instance_uuid]
    playlist.plugin_rotation_recent_history = []
    command = task._playlist_command(
        playlist.name,
        first,
        source=CommandSource.BACKGROUND,
        intent=RefreshIntent.DATA_REFRESH,
        force=False,
        display_cached_only=False,
        priority=95,
        kind=CommandKind.CACHE_REFRESH,
        current_dt=PRESENTATION_NOW,
        automatic_rotation=True,
    )
    playlist._plugin_rotation_reserved_key = second.instance_uuid
    monkeypatch.setattr(task, "_get_current_datetime", lambda: PRESENTATION_NOW)
    monkeypatch.setattr(
        task,
        "_execute_command",
        lambda _command: (_ for _ in ()).throw(
            TaskDeadlineExceeded("stale automatic deadline")
        ),
    )

    submitted = task.refresh_queue.submit(command)
    task._process_queue_entry(task.refresh_queue.take(timeout=0))

    result = task.refresh_queue.get_entry(submitted.id).job
    state = task.runtime_state.snapshot().instances[first.instance_uuid]
    assert result.status is JobStatus.ABANDONED
    assert result.error_code == "deadline_expired"
    assert state.data.last_failure_at is None
    assert state.data.next_retry_at is None
    assert playlist.is_rotation_reservation_current(second.instance_uuid) is True


def test_canceled_automatic_command_at_deadline_does_not_record_failure_or_release(
    monkeypatch,
):
    task, _device_config, clock, playlist, _display = _make_presentation_task(
        "canceled-automatic-command-at-deadline-keeps-reservation"
    )
    instance = playlist.plugins[0].snapshot()
    playlist.plugin_rotation_pool = [instance.instance_uuid]
    playlist.plugin_rotation_queue = [instance.instance_uuid]
    playlist.plugin_rotation_recent_history = []
    playlist._plugin_rotation_reserved_key = instance.instance_uuid
    historical_success = task._runtime_now_iso(offset_seconds=-3600)
    task.runtime_state.record_success(
        instance.instance_uuid,
        historical_success,
        lane=RefreshLane.DATA,
    )
    command = task._playlist_command(
        playlist.name,
        instance,
        source=CommandSource.BACKGROUND,
        intent=RefreshIntent.DATA_REFRESH,
        force=False,
        display_cached_only=False,
        priority=95,
        kind=CommandKind.CACHE_REFRESH,
        current_dt=PRESENTATION_NOW,
        automatic_rotation=True,
    )
    submitted = task.refresh_queue.submit(command)
    entry = task.refresh_queue.take(timeout=0)
    assert entry is not None
    assert task.refresh_queue.cancel_instance(instance.instance_uuid) == 1
    clock.advance(181)
    monkeypatch.setattr(task, "_get_current_datetime", lambda: PRESENTATION_NOW)
    monkeypatch.setattr(
        task,
        "_execute_command",
        lambda _command: (_ for _ in ()).throw(
            TaskDeadlineExceeded("deadline during shutdown cancellation")
        ),
    )

    task._process_queue_entry(entry)

    result = task.refresh_queue.get_entry(submitted.id).job
    state = task.runtime_state.snapshot().instances[instance.instance_uuid]
    assert result.status is JobStatus.CANCELED
    assert result.error_code == "task_canceled"
    assert state.data.last_success_at == historical_success
    assert state.data.last_failure_at is None
    assert state.data.next_retry_at is None
    assert playlist.is_rotation_reservation_current(instance.instance_uuid) is True


def test_successful_automatic_display_clears_transient_rotation_backoff(
    monkeypatch,
):
    task, _device_config, _clock, playlist, _display = _make_presentation_task(
        "successful-automatic-display-clears-transient-backoff"
    )
    instance = playlist.plugins[0].snapshot()
    playlist.plugin_rotation_pool = [instance.instance_uuid]
    playlist.plugin_rotation_queue = [instance.instance_uuid]
    playlist.plugin_rotation_recent_history = []
    playlist._plugin_rotation_reserved_key = instance.instance_uuid
    _write_runtime_cache(task, instance, Image.new("RGB", (32, 16), "black"))
    retry_key = task._rotation_display_retry_key(instance.instance_uuid)
    task.retry_registry.mark_failure(retry_key, task._clock())
    command = task._playlist_command(
        playlist.name,
        instance,
        source=CommandSource.SCHEDULER,
        intent=RefreshIntent.DISPLAY_CACHE,
        force=False,
        display_cached_only=True,
        priority=95,
        kind=CommandKind.DISPLAY,
        current_dt=PRESENTATION_NOW,
        automatic_rotation=True,
        force_hardware_write=True,
    )
    monkeypatch.setattr(task, "_get_current_datetime", lambda: PRESENTATION_NOW)
    _install_display_provider_plugin_sentinels(monkeypatch)

    result = _queue_and_process(task, command)

    assert result.job.status is JobStatus.SUCCEEDED
    assert task.retry_registry.next_delay(retry_key, task._clock()) == 0


def test_committed_automatic_display_is_not_reclassified_as_rotation_failure(
    monkeypatch,
):
    task, _device_config, clock, playlist, display = _make_presentation_task(
        "committed-display-is-not-reclassified-as-rotation-failure"
    )
    _device_config.config["display_triggered_refresh_enabled"] = False
    instance = playlist.plugins[0].snapshot()
    playlist.plugin_rotation_pool = [instance.instance_uuid]
    playlist.plugin_rotation_queue = [instance.instance_uuid]
    playlist.plugin_rotation_recent_history = []
    playlist._plugin_rotation_reserved_key = instance.instance_uuid
    _write_runtime_cache(task, instance, Image.new("RGB", (32, 16), "black"))
    retry_key = task._rotation_display_retry_key(instance.instance_uuid)
    command = task._playlist_command(
        playlist.name,
        instance,
        source=CommandSource.SCHEDULER,
        intent=RefreshIntent.DISPLAY_CACHE,
        force=False,
        display_cached_only=True,
        priority=95,
        kind=CommandKind.DISPLAY,
        current_dt=PRESENTATION_NOW,
        automatic_rotation=True,
        force_hardware_write=True,
    )
    monkeypatch.setattr(task, "_get_current_datetime", lambda: PRESENTATION_NOW)
    _install_display_provider_plugin_sentinels(monkeypatch)
    execute = task._execute_command

    def commit_then_cross_deadline(current_command):
        result = execute(current_command)
        clock.advance(181)
        return result

    monkeypatch.setattr(task, "_execute_command", commit_then_cross_deadline)

    result = _queue_and_process(task, command)

    assert result.job.status is JobStatus.ABANDONED
    assert result.job.error_code == "deadline_expired"
    assert len(display.calls) == 1
    assert playlist.plugin_rotation_queue == []
    assert playlist.plugin_rotation_recent_history == [instance.instance_uuid]
    assert playlist.is_rotation_reservation_current(instance.instance_uuid) is False
    assert task.retry_registry.next_delay(retry_key, task._clock()) == 0


def test_manual_display_deadline_does_not_release_automatic_rotation_reservation(
    monkeypatch,
):
    task, _device_config, _clock, playlist, _display = _make_presentation_task(
        "manual-display-deadline-keeps-rotation-reservation"
    )
    instance = playlist.plugins[0].snapshot()
    playlist.plugin_rotation_pool = [instance.instance_uuid]
    playlist.plugin_rotation_queue = [instance.instance_uuid]
    playlist.plugin_rotation_recent_history = []
    playlist._plugin_rotation_reserved_key = instance.instance_uuid
    task.runtime_state.record_attempt(
        instance.instance_uuid,
        PRESENTATION_NOW.isoformat(),
        lane=RefreshLane.DATA,
    )
    command = task._playlist_command(
        playlist.name,
        instance,
        source=CommandSource.MANUAL,
        intent=RefreshIntent.DISPLAY_CACHE,
        force=False,
        display_cached_only=True,
        priority=100,
        kind=CommandKind.DISPLAY,
        current_dt=PRESENTATION_NOW,
        automatic_rotation=False,
    )
    monkeypatch.setattr(task, "_get_current_datetime", lambda: PRESENTATION_NOW)
    monkeypatch.setattr(
        task,
        "_execute_command",
        lambda _command: (_ for _ in ()).throw(
            TaskDeadlineExceeded("manual display deadline expired")
        ),
    )

    submitted = task.refresh_queue.submit(command)
    task._process_queue_entry(task.refresh_queue.take(timeout=0))

    result = task.refresh_queue.get_entry(submitted.id).job
    state = task.runtime_state.snapshot().instances[instance.instance_uuid]
    assert result.status is JobStatus.ABANDONED
    assert result.error_code == "deadline_expired"
    assert state.data.last_failure_at is None
    assert state.data.next_retry_at is None
    assert playlist.is_rotation_reservation_current(instance.instance_uuid) is True


def test_prepared_rotation_presentation_deadline_does_not_record_false_failure(
    monkeypatch,
):
    task, _device_config, _clock, playlist, _display = _make_presentation_task(
        "prepared-presentation-deadline-keeps-result"
    )
    instance = playlist.plugins[0].snapshot()
    playlist.plugin_rotation_pool = [instance.instance_uuid]
    playlist.plugin_rotation_queue = [instance.instance_uuid]
    playlist.plugin_rotation_recent_history = []
    playlist._plugin_rotation_reserved_key = instance.instance_uuid
    request = _seed_presentation_request(task, instance)
    _seed_prepared_presentation(task, instance, request)
    command = task._playlist_command(
        playlist.name,
        instance,
        source=CommandSource.BACKGROUND,
        intent=RefreshIntent.PRESENTATION_REFRESH,
        force=False,
        display_cached_only=False,
        priority=90,
        kind=CommandKind.CACHE_REFRESH,
        current_dt=PRESENTATION_NOW,
        automatic_rotation=True,
        presentation_request_id=request.request_id,
    )
    monkeypatch.setattr(task, "_get_current_datetime", lambda: PRESENTATION_NOW)
    monkeypatch.setattr(
        task,
        "_execute_command",
        lambda _command: (_ for _ in ()).throw(
            TaskDeadlineExceeded("deadline after prepared publication")
        ),
    )

    submitted = task.refresh_queue.submit(command)
    task._process_queue_entry(task.refresh_queue.take(timeout=0))

    result = task.refresh_queue.get_entry(submitted.id).job
    state = task.runtime_state.snapshot().instances[instance.instance_uuid]
    assert result.status is JobStatus.ABANDONED
    assert state.presentation.last_failure_at is None
    assert state.presentation.next_retry_at is None
    assert state.presentation_request is not None
    assert state.presentation_request.request_id == request.request_id
    assert state.presentation_request.prepared_at is not None
    assert playlist.is_rotation_reservation_current(instance.instance_uuid) is True


def test_replaced_rotation_presentation_deadline_does_not_pollute_new_request(
    monkeypatch,
):
    task, _device_config, _clock, playlist, _display = _make_presentation_task(
        "replaced-presentation-deadline-keeps-new-request"
    )
    instance = playlist.plugins[0].snapshot()
    playlist.plugin_rotation_pool = [instance.instance_uuid]
    playlist.plugin_rotation_queue = [instance.instance_uuid]
    playlist.plugin_rotation_recent_history = []
    playlist._plugin_rotation_reserved_key = instance.instance_uuid
    original = _seed_presentation_request(task, instance)
    command = task._playlist_command(
        playlist.name,
        instance,
        source=CommandSource.BACKGROUND,
        intent=RefreshIntent.PRESENTATION_REFRESH,
        force=False,
        display_cached_only=False,
        priority=90,
        kind=CommandKind.CACHE_REFRESH,
        current_dt=PRESENTATION_NOW,
        automatic_rotation=True,
        presentation_request_id=original.request_id,
    )
    assert task.runtime_state.satisfy_presentation_no_change(
        instance.instance_uuid,
        original.request_id,
        original.requested_at,
    )
    replacement = _seed_presentation_request(
        task,
        instance,
        request_id=uuid.uuid4().hex,
        requested_at=PRESENTATION_NOW + timedelta(seconds=1),
    )
    monkeypatch.setattr(task, "_get_current_datetime", lambda: PRESENTATION_NOW)
    monkeypatch.setattr(
        task,
        "_execute_command",
        lambda _command: (_ for _ in ()).throw(
            TaskDeadlineExceeded("stale presentation deadline")
        ),
    )

    submitted = task.refresh_queue.submit(command)
    task._process_queue_entry(task.refresh_queue.take(timeout=0))

    result = task.refresh_queue.get_entry(submitted.id).job
    state = task.runtime_state.snapshot().instances[instance.instance_uuid]
    assert result.status is JobStatus.ABANDONED
    assert state.presentation.last_failure_at is None
    assert state.presentation.next_retry_at is None
    assert state.presentation_request == replacement
    assert playlist.is_rotation_reservation_current(instance.instance_uuid) is True


def test_rotation_preflight_no_change_displays_cached_member_without_request_loop(
    monkeypatch,
):
    task, device_config, _clock, playlist, _display = _make_presentation_task(
        "presentation-no-change-breaks-request-loop"
    )
    instance = playlist.plugins[0].snapshot()
    _write_runtime_cache(task, instance, Image.new("RGB", (32, 16), "black"))
    device_config.refresh_info.refresh_time = (
        PRESENTATION_NOW - timedelta(minutes=2)
    ).isoformat()
    monkeypatch.setattr(
        task,
        "_resource_sample",
        lambda: ResourceSample(available_mb=512, swap_percent=0),
    )

    assert task._select_cached_display_command(PRESENTATION_NOW) is None
    request = task.runtime_state.snapshot().instances[
        instance.instance_uuid
    ].presentation_request
    assert request is not None
    assert task.runtime_state.satisfy_presentation_no_change(
        instance.instance_uuid,
        request.request_id,
        request.requested_at,
    )

    command = task._select_cached_display_command(
        PRESENTATION_NOW + timedelta(seconds=1)
    )

    assert command is not None
    assert command.intent is RefreshIntent.DISPLAY_CACHE
    assert command.payload["automatic_rotation"] is True
    assert command.allow_prepared_presentation is False
    assert task.runtime_state.snapshot().instances[
        instance.instance_uuid
    ].presentation_request is None


def test_prepared_refresh_on_display_rotation_consumes_shuffle_bag_once(
    monkeypatch,
):
    task, device_config, _clock, playlist, _display = _make_presentation_task(
        "presentation-followup-preserves-shuffle-bag"
    )
    instance = playlist.plugins[0].snapshot()
    _write_runtime_cache(task, instance, Image.new("RGB", (32, 16), "black"))
    device_config.refresh_info.refresh_time = (
        PRESENTATION_NOW - timedelta(minutes=2)
    ).isoformat()
    monkeypatch.setattr(task, "_get_current_datetime", lambda: PRESENTATION_NOW)
    _install_display_provider_plugin_sentinels(monkeypatch)

    assert task._select_cached_display_command(PRESENTATION_NOW) is None
    request = task.runtime_state.snapshot().instances[
        instance.instance_uuid
    ].presentation_request
    assert request is not None
    _seed_prepared_presentation(
        task,
        instance,
        request,
        image=Image.new("RGB", (32, 16), "white"),
    )
    automatic = task._select_cached_display_command(
        PRESENTATION_NOW + timedelta(seconds=1)
    )
    assert automatic.payload["automatic_rotation"] is True
    first = _queue_and_process(task, automatic)
    assert first.job.status is JobStatus.SUCCEEDED
    rotation_after_automatic = (
        list(playlist.plugin_rotation_queue),
        list(playlist.plugin_rotation_recent_history),
    )
    assert rotation_after_automatic == ([], [instance.instance_uuid])
    assert task.runtime_state.snapshot().instances[
        instance.instance_uuid
    ].presentation_request is None


def test_manual_cache_display_records_request_but_live_theme_followups_do_not(
    monkeypatch,
):
    _install_display_provider_plugin_sentinels(monkeypatch)
    results = {}
    for label, source, scope, expected_request in (
        ("manual", CommandSource.MANUAL, None, True),
        ("live", CommandSource.LIVE, "live-followup:source-command", False),
        (
            "theme",
            CommandSource.SCHEDULER,
            "theme-followup:source-command",
            False,
        ),
    ):
        task, _config, _clock, playlist, _display = _make_presentation_task(f"presentation-{label}-display-rule")
        instance = playlist.plugins[0].snapshot()
        _write_runtime_cache(task, instance, Image.new("RGB", (32, 16), "black"))
        task.runtime_state.set_display_state(
            "committed",
            f"{label}-origin",
            instance_uuid=instance.instance_uuid,
            changed_at=PRESENTATION_NOW.isoformat(),
        )
        command = task._playlist_command(
            playlist.name,
            instance,
            source=source,
            intent=RefreshIntent.DISPLAY_CACHE,
            force=False,
            display_cached_only=True,
            priority=100 if label == "manual" else 75,
            current_dt=PRESENTATION_NOW,
            cache_theme_mode=None,
            expected_displayed_instance_uuid=(None if label == "manual" else instance.instance_uuid),
            preserve_rotation_anchor=label == "theme",
            coalescing_scope=scope,
        )

        assert command.allow_prepared_presentation is expected_request
        _queue_and_process(task, command)
        state = task.runtime_state.snapshot().instances.get(instance.instance_uuid)
        results[label] = None if state is None else state.presentation_request

    assert results["manual"] is not None
    assert results["live"] is None
    assert results["theme"] is None


def test_exact_manual_display_can_suppress_redundant_presentation_request(
    monkeypatch,
):
    task, _config, _clock, playlist, _display = _make_presentation_task(
        "manual-display-suppresses-presentation"
    )
    instance = playlist.plugins[0].snapshot()
    _write_runtime_cache(task, instance, Image.new("RGB", (32, 16), "black"))
    monkeypatch.setattr(task, "_get_current_datetime", lambda: PRESENTATION_NOW)
    _install_display_provider_plugin_sentinels(monkeypatch)
    task.start()
    try:
        assert task.wait_until_waiting(timeout=1.0)
        job = task.submit_playlist_display(
            instance.instance_uuid,
            expected_playlist_name=playlist.name,
            expected_generation=instance.structural_generation,
            expected_settings_revision=instance.settings_revision,
            request_presentation_after_display=False,
        )
        result = task.wait_for_job(job["id"], timeout=1.0)
    finally:
        task.stop(join_timeout=1.0)

    assert result["status"] == "completed"
    state = task.runtime_state.snapshot().instances.get(instance.instance_uuid)
    assert state is None or state.presentation_request is None


def test_refresh_on_display_rerender_rejects_unattested_output():
    plugin = UnattestedRefreshOnDisplayPlugin()
    request = PresentationRequestContext(
        request_id="d" * 32,
        requested_at="2026-07-13T20:00:00+00:00",
        origin_display_commit_id="display-commit",
        last_receipt=None,
    )

    with pytest.raises(RuntimeError, match="fresh cacheable image"):
        plugin.prepare_presentation(
            {},
            SimpleNamespace(),
            request=request,
            resolved_theme_context={"mode": "day"},
        )


def test_refresh_on_display_rerender_prepares_latest_then_commits_without_loop(
    monkeypatch,
):
    task, _config, _clock, playlist, display = _make_presentation_task(
        "refresh-on-display-rerender-adapter"
    )
    instance = playlist.plugins[0].snapshot()
    _write_runtime_cache(task, instance, Image.new("RGB", (32, 16), "black"))
    _seed_independent_lane_clocks(task, instance)
    provider_calls = []
    plugin = RefreshOnDisplayRerenderPlugin(provider_calls)
    monkeypatch.setattr(refresh_task_module, "get_plugin_instance", lambda _config: plugin)

    display_result = _queue_and_process(
        task,
        _normal_cache_display_command(task, playlist, instance),
    )
    request = task.runtime_state.snapshot().instances[instance.instance_uuid].presentation_request

    assert display_result.job.status is JobStatus.SUCCEEDED
    assert request is not None
    assert provider_calls == []
    refresh_command = task._select_independent_refresh_command(PRESENTATION_NOW)
    assert refresh_command.intent is RefreshIntent.PRESENTATION_REFRESH

    refresh_result = _queue_and_process(task, refresh_command)
    followup = task.refresh_queue.take(timeout=0)
    assert refresh_result.job.status is JobStatus.SUCCEEDED
    assert provider_calls and len(provider_calls) == 1
    assert provider_calls[0]["forceRefresh"] is True
    assert provider_calls[0]["_inkypiPresentationRefresh"] is True
    assert followup is not None
    assert followup.command.intent is RefreshIntent.DISPLAY_CACHE
    task._process_queue_entry(followup)

    final_state = task.runtime_state.snapshot().instances[instance.instance_uuid]
    assert final_state.presentation_request is not None
    assert final_state.presentation_request.request_id != request.request_id
    assert final_state.presentation_receipt.request_id == request.request_id
    assert len(display.calls) == 2
    assert display.calls[-1]["image"].getpixel((0, 0)) == (255, 255, 255)
    assert task.refresh_queue.take(timeout=0) is None
    next_refresh = task._select_independent_refresh_command(PRESENTATION_NOW)
    assert next_refresh is not None
    assert next_refresh.intent is RefreshIntent.PRESENTATION_REFRESH


def test_display_cache_never_instantiates_plugin_with_pending_presentation(
    monkeypatch,
):
    task, _config, _clock, playlist, display = _make_presentation_task("presentation-pending-display-cache")
    instance = playlist.plugins[0].snapshot()
    _write_runtime_cache(task, instance, Image.new("RGB", (32, 16), "black"))
    request = _seed_presentation_request(task, instance)
    _install_display_provider_plugin_sentinels(monkeypatch)

    result = _queue_and_process(
        task,
        _normal_cache_display_command(task, playlist, instance),
    )

    assert result.job.status is JobStatus.SUCCEEDED
    assert len(display.calls) == 1
    assert display.calls[0]["image"].getpixel((0, 0)) == (0, 0, 0)
    assert task.runtime_state.snapshot().instances[instance.instance_uuid].presentation_request == request


def test_data_due_wins_same_instance_and_cannot_record_presentation_success(
    monkeypatch,
):
    task, device_config, _clock, playlist, _display = _make_presentation_task(
        "presentation-data-wins",
        latest_refresh_time=None,
    )
    instance = playlist.plugins[0].snapshot()
    _write_runtime_cache(task, instance, Image.new("RGB", (32, 16), "black"))
    prior_request = _seed_presentation_request(
        task,
        instance,
        requested_at=PRESENTATION_NOW - timedelta(minutes=40),
        origin_commit_id="prior-origin-display",
    )
    prior_prepared_at = (PRESENTATION_NOW - timedelta(minutes=30)).isoformat()
    assert task.runtime_state.mark_presentation_prepared(
        instance.instance_uuid,
        prior_request.request_id,
        prior_prepared_at,
        None,
    )
    prior_receipt = PresentationCommitReceipt(
        request_id=prior_request.request_id,
        committed_at=(PRESENTATION_NOW - timedelta(minutes=20)).isoformat(),
        display_commit_id="prior-prepared-display",
        structural_generation=instance.structural_generation,
        settings_revision=instance.settings_revision,
        theme_mode=None,
    )
    assert task.runtime_state.commit_presentation(
        instance.instance_uuid,
        prior_receipt,
        last_good_cache=LastGoodCacheState(
            theme_mode=None,
            structural_generation=instance.structural_generation,
            settings_revision=instance.settings_revision,
            promoted_at=prior_receipt.committed_at,
        ),
    )
    request = _seed_presentation_request(task, instance)
    plugin = BaseCopyIdentityPlugin()
    monkeypatch.setattr(
        refresh_task_module,
        "get_plugin_instance",
        lambda _config: plugin,
    )
    monkeypatch.setattr(task, "_get_current_datetime", lambda: PRESENTATION_NOW)
    monkeypatch.setattr(task, "_select_cached_display_command", lambda _now: None)
    monkeypatch.setattr(task, "_memory_watchdog_should_restart", lambda: False)
    monkeypatch.setattr(
        task,
        "_resource_sample",
        lambda: ResourceSample(available_mb=512, swap_percent=0),
    )

    task._schedule_if_due()
    entry = task.refresh_queue.take(timeout=0)
    assert entry is not None
    assert entry.command.intent is RefreshIntent.DATA_REFRESH
    payload_before = json.dumps(
        refresh_task_module.thaw_payload(entry.command.payload),
        sort_keys=True,
    )
    playlist_before = json.dumps(
        device_config.get_playlist_manager().to_dict(),
        sort_keys=True,
    )
    task._process_queue_entry(entry)
    state = task.runtime_state.snapshot().instances[instance.instance_uuid]

    assert state.data.last_success_at is not None
    assert state.presentation.last_success_at == prior_receipt.committed_at
    assert state.presentation_request == request
    assert [event[0] for event in plugin.events] == [
        "mode",
        "reconcile",
        "generate",
    ]
    assert plugin.events[1][1] == prior_receipt
    assert plugin.identity_events == [
        ("mode", instance.instance_uuid),
        ("reconcile", instance.instance_uuid),
        ("generate", instance.instance_uuid),
    ]
    assert json.dumps(
        refresh_task_module.thaw_payload(entry.command.payload),
        sort_keys=True,
    ) == payload_before
    assert json.dumps(
        device_config.get_playlist_manager().to_dict(),
        sort_keys=True,
    ) == playlist_before


def test_soft_scheduler_prioritizes_presentation_after_post_request_data_attempt(
    monkeypatch,
):
    task, _device_config, _clock, playlist, _display = _make_presentation_task(
        "presentation-soft-post-request-data-attempt",
        plugin_count=2,
        latest_refresh_time=None,
        interval=120,
    )
    instances = [plugin.snapshot() for plugin in playlist.plugins]
    unrelated, pending = instances
    for instance in instances:
        _write_runtime_cache(task, instance, Image.new("RGB", (32, 16), "black"))

    request = _seed_presentation_request(
        task,
        pending,
        requested_at=PRESENTATION_NOW - timedelta(minutes=1),
    )
    task.runtime_state.record_attempt(
        pending.instance_uuid,
        (PRESENTATION_NOW - timedelta(seconds=30)).isoformat(),
        lane=RefreshLane.DATA,
    )
    monkeypatch.setattr(task, "_get_current_datetime", lambda: PRESENTATION_NOW)
    monkeypatch.setattr(task, "_select_cached_display_command", lambda _now: None)
    monkeypatch.setattr(task, "_memory_watchdog_should_restart", lambda: False)
    monkeypatch.setattr(
        task,
        "_resource_sample",
        lambda: ResourceSample(available_mb=100, swap_percent=0),
    )

    task._schedule_if_due()
    entry = task.refresh_queue.take(timeout=0)

    assert unrelated.instance_uuid != pending.instance_uuid
    assert entry is not None
    assert entry.command.intent is RefreshIntent.PRESENTATION_REFRESH
    assert entry.command.source is CommandSource.BACKGROUND
    assert entry.command.instance_uuid == pending.instance_uuid
    assert entry.command.payload["presentation_request_id"] == request.request_id


def test_non_presentation_capable_data_render_receives_no_trusted_identity(
    monkeypatch,
):
    task, device_config, _clock, playlist, _display = _make_presentation_task(
        "non-presentation-data-identity",
        latest_refresh_time=None,
    )
    instance = playlist.plugins[0].snapshot()
    manifest = PluginManifest(
        schema_version=2,
        id=instance.plugin_id,
        class_name="OrdinaryPlugin",
        display_name="Ordinary Plugin",
        refresh_on_display=False,
        capabilities=PluginCapabilities(supports_presentation_refresh=False),
        raw={},
    )
    device_config.get_plugin = lambda plugin_id: {
        "id": plugin_id,
        "_manifest": manifest,
    }
    plugin = BaseCopyIdentityPlugin()
    monkeypatch.setattr(
        refresh_task_module,
        "get_plugin_instance",
        lambda _config: plugin,
    )
    monkeypatch.setattr(task, "_get_current_datetime", lambda: PRESENTATION_NOW)
    monkeypatch.setattr(task, "_select_cached_display_command", lambda _now: None)
    monkeypatch.setattr(task, "_memory_watchdog_should_restart", lambda: False)
    monkeypatch.setattr(
        task,
        "_resource_sample",
        lambda: ResourceSample(available_mb=512, swap_percent=0),
    )

    task._schedule_if_due()
    entry = task.refresh_queue.take(timeout=0)
    assert entry is not None
    assert entry.command.intent is RefreshIntent.DATA_REFRESH
    task._process_queue_entry(entry)

    assert plugin.identity_events == [("generate", None)]


@pytest.mark.parametrize(
    ("intent", "source", "kind", "force", "theme_render_only"),
    [
        (
            RefreshIntent.LIVE_REFRESH,
            CommandSource.LIVE,
            CommandKind.CACHE_REFRESH,
            False,
            False,
        ),
        (
            RefreshIntent.THEME_REDRAW,
            CommandSource.SCHEDULER,
            CommandKind.CACHE_REFRESH,
            False,
            True,
        ),
        (
            RefreshIntent.MANUAL_RENDER,
            CommandSource.MANUAL,
            CommandKind.DISPLAY,
            True,
            False,
        ),
    ],
)
def test_presentation_capable_playlist_renderer_binds_identity_before_generate(
    monkeypatch,
    intent,
    source,
    kind,
    force,
    theme_render_only,
):
    task, device_config, _clock, playlist, _display = _make_presentation_task(
        f"presentation-{intent.value}-identity"
    )
    instance = playlist.plugins[0].snapshot()
    plugin = BaseCopyIdentityPlugin()
    monkeypatch.setattr(
        refresh_task_module,
        "get_plugin_instance",
        lambda _config: plugin,
    )
    monkeypatch.setattr(task, "_get_current_datetime", lambda: PRESENTATION_NOW)
    task.runtime_state.set_display_state(
        "committed",
        "theme-redraw-origin",
        instance_uuid=instance.instance_uuid,
        changed_at=PRESENTATION_NOW.isoformat(),
    )
    command = task._playlist_command(
        playlist.name,
        instance,
        source=source,
        intent=intent,
        force=force,
        display_cached_only=not force,
        priority=85,
        kind=kind,
        theme_render_only=theme_render_only,
        current_dt=PRESENTATION_NOW,
    )
    payload_before = json.dumps(
        refresh_task_module.thaw_payload(command.payload),
        sort_keys=True,
    )
    playlist_before = json.dumps(
        device_config.get_playlist_manager().to_dict(),
        sort_keys=True,
    )
    config_before = json.dumps(device_config.config, sort_keys=True)

    result = _queue_and_process(task, command)

    assert result.job.status is JobStatus.SUCCEEDED
    assert plugin.identity_events == [("generate", instance.instance_uuid)]
    assert json.dumps(
        refresh_task_module.thaw_payload(command.payload),
        sort_keys=True,
    ) == payload_before
    assert json.dumps(
        device_config.get_playlist_manager().to_dict(),
        sort_keys=True,
    ) == playlist_before
    assert json.dumps(device_config.config, sort_keys=True) == config_before


def test_presentation_prepare_does_not_promote_last_good_or_change_lane_success(
    monkeypatch,
):
    task, _config, _clock, playlist, display = _make_presentation_task("presentation-prepare-only")
    instance = playlist.plugins[0].snapshot()
    _write_runtime_cache(task, instance, Image.new("RGB", (32, 16), "black"))
    _seed_independent_lane_clocks(task, instance)
    baseline_last_good = LastGoodCacheState(
        theme_mode=None,
        structural_generation=instance.structural_generation,
        settings_revision=instance.settings_revision,
        promoted_at=(PRESENTATION_NOW - timedelta(minutes=10)).isoformat(),
    )
    task.runtime_state.record_success(
        instance.instance_uuid,
        baseline_last_good.promoted_at,
        lane=RefreshLane.DATA,
        last_good_cache=baseline_last_good,
    )
    request = _seed_presentation_request(task, instance)
    task.runtime_state.set_display_state(
        "committed",
        request.origin_display_commit_id,
        instance_uuid=instance.instance_uuid,
        changed_at=request.requested_at,
    )
    before = task.runtime_state.snapshot().instances[instance.instance_uuid]
    before_lanes = _non_presentation_lane_bytes(before)
    plugin = PresentationBankPlugin(prepared_color="white")
    monkeypatch.setattr(
        refresh_task_module,
        "get_plugin_instance",
        lambda _config: plugin,
    )
    monkeypatch.setattr(task, "_get_current_datetime", lambda: PRESENTATION_NOW)
    monkeypatch.setattr(task, "_select_cached_display_command", lambda _now: None)
    monkeypatch.setattr(task, "_memory_watchdog_should_restart", lambda: False)
    monkeypatch.setattr(
        task,
        "_resource_sample",
        lambda: ResourceSample(available_mb=512, swap_percent=0),
    )

    task._schedule_if_due()
    entry = task.refresh_queue.take(timeout=0)
    assert entry is not None
    assert entry.command.intent is RefreshIntent.PRESENTATION_REFRESH
    task._process_queue_entry(entry)
    state = task.runtime_state.snapshot().instances[instance.instance_uuid]
    candidate = _prepared_presentation_candidate(task, instance, request)

    assert state.presentation_request.prepared_at is not None
    assert state.presentation.last_success_at is None
    assert state.last_good_cache == baseline_last_good
    assert _non_presentation_lane_bytes(state) == before_lanes
    assert PresentationCache(Path(task.device_config.plugin_image_dir) / ".refresh-presentation").validate(candidate)
    assert display.calls == []
    origin_receipt = PresentationCommitReceipt(
        request_id=request.request_id,
        committed_at=request.requested_at,
        display_commit_id=request.origin_display_commit_id,
        structural_generation=instance.structural_generation,
        settings_revision=instance.settings_revision,
        theme_mode=request.origin_theme_mode,
    )
    assert [event[0] for event in plugin.events[:3]] == [
        "mode",
        "reconcile",
        "prepare",
    ]
    assert plugin.events[1:] == [
        ("reconcile", origin_receipt),
        ("prepare", request.request_id),
    ]
    assert plugin.contexts == [
        PresentationRequestContext(
            request_id=request.request_id,
            requested_at=request.requested_at,
            origin_display_commit_id=request.origin_display_commit_id,
            last_receipt=None,
        )
    ]
    followup = task.refresh_queue.take(timeout=0)
    assert followup is not None
    assert followup.command.coalescing_scope == (f"presentation-followup:{request.request_id}")


def test_presentation_prepare_reconciles_origin_then_prior_receipt_before_selection(
    monkeypatch,
):
    task, device_config, _clock, playlist, _display = _make_presentation_task(
        "presentation-origin-before-prior-receipt"
    )
    instance = playlist.plugins[0].snapshot()
    _write_runtime_cache(task, instance, Image.new("RGB", (32, 16), "black"))
    _seed_independent_lane_clocks(task, instance)
    prior_request = _seed_presentation_request(
        task,
        instance,
        request_id="a" * 32,
        requested_at=PRESENTATION_NOW - timedelta(minutes=20),
        origin_commit_id="prior-origin-display",
        origin_theme_mode="night",
    )
    assert task.runtime_state.mark_presentation_prepared(
        instance.instance_uuid,
        prior_request.request_id,
        (PRESENTATION_NOW - timedelta(minutes=15)).isoformat(),
        "night",
    )
    prior_receipt = PresentationCommitReceipt(
        request_id=prior_request.request_id,
        committed_at=(PRESENTATION_NOW - timedelta(minutes=10)).isoformat(),
        display_commit_id="prior-prepared-display",
        structural_generation=instance.structural_generation,
        settings_revision=instance.settings_revision,
        theme_mode="night",
    )
    assert task.runtime_state.commit_presentation(
        instance.instance_uuid,
        prior_receipt,
        last_good_cache=LastGoodCacheState(
            theme_mode="night",
            structural_generation=instance.structural_generation,
            settings_revision=instance.settings_revision,
            promoted_at=prior_receipt.committed_at,
        ),
    )
    request = _seed_presentation_request(
        task,
        instance,
        request_id="b" * 32,
        origin_commit_id="current-origin-display",
        origin_theme_mode="day",
    )
    task.runtime_state.set_display_state(
        "committed",
        request.origin_display_commit_id,
        instance_uuid=instance.instance_uuid,
        changed_at=request.requested_at,
    )
    plugin = PresentationBankPlugin()
    observed_protected_request_ids = []
    original_save = task.presentation_cache.save

    def save_with_observation(candidate, image, *, protected_request_ids=()):
        observed_protected_request_ids.append(frozenset(protected_request_ids))
        return original_save(
            candidate,
            image,
            protected_request_ids=protected_request_ids,
        )

    monkeypatch.setattr(task.presentation_cache, "save", save_with_observation)
    monkeypatch.setattr(
        refresh_task_module,
        "get_plugin_instance",
        lambda _config: plugin,
    )
    monkeypatch.setattr(task, "_get_current_datetime", lambda: PRESENTATION_NOW)
    monkeypatch.setattr(task, "_select_cached_display_command", lambda _now: None)
    monkeypatch.setattr(task, "_memory_watchdog_should_restart", lambda: False)
    monkeypatch.setattr(
        task,
        "_resource_sample",
        lambda: ResourceSample(available_mb=512, swap_percent=0),
    )

    task._schedule_if_due()
    entry = task.refresh_queue.take(timeout=0)
    assert entry is not None
    payload_before = json.dumps(
        refresh_task_module.thaw_payload(entry.command.payload),
        sort_keys=True,
    )
    playlist_before = json.dumps(
        device_config.get_playlist_manager().to_dict(),
        sort_keys=True,
    )
    task._process_queue_entry(entry)

    origin_receipt = PresentationCommitReceipt(
        request_id=request.request_id,
        committed_at=request.requested_at,
        display_commit_id=request.origin_display_commit_id,
        structural_generation=instance.structural_generation,
        settings_revision=instance.settings_revision,
        theme_mode=request.origin_theme_mode,
    )
    assert [event[0] for event in plugin.events[:4]] == [
        "mode",
        "reconcile",
        "reconcile",
        "prepare",
    ]
    assert plugin.events[1:4] == [
        ("reconcile", origin_receipt),
        ("reconcile", prior_receipt),
        ("prepare", request.request_id),
    ]
    assert plugin.identity_events[:4] == [
        ("mode", instance.instance_uuid),
        ("reconcile", instance.instance_uuid),
        ("reconcile", instance.instance_uuid),
        ("prepare", instance.instance_uuid),
    ]
    assert observed_protected_request_ids == [
        frozenset({request.request_id, prior_receipt.request_id})
    ]
    followup = task.refresh_queue.take(timeout=0)
    assert followup is not None
    assert followup.command.intent is RefreshIntent.DISPLAY_CACHE
    assert followup.command.payload["presentation_request_id"] == request.request_id
    assert json.dumps(
        refresh_task_module.thaw_payload(entry.command.payload),
        sort_keys=True,
    ) == payload_before
    assert json.dumps(
        device_config.get_playlist_manager().to_dict(),
        sort_keys=True,
    ) == playlist_before


def test_prepared_followup_commit_records_receipt_success_and_preserves_anchor(
    monkeypatch,
):
    task, device_config, _clock, playlist, display = _make_presentation_task("presentation-followup-commit")
    instance = playlist.plugins[0].snapshot()
    canonical = _write_runtime_cache(
        task,
        instance,
        Image.new("RGB", (32, 16), "black"),
    )
    _seed_independent_lane_clocks(task, instance)
    request = _seed_presentation_request(task, instance)
    candidate = _seed_prepared_presentation(
        task,
        instance,
        request,
        image=Image.new("RGB", (32, 16), "white"),
    )
    task.runtime_state.set_display_state(
        "committed",
        request.origin_display_commit_id,
        instance_uuid=instance.instance_uuid,
        changed_at=request.requested_at,
    )
    anchor = (PRESENTATION_NOW - timedelta(minutes=5)).isoformat()
    device_config.refresh_info = RefreshInfo(
        refresh_type="Playlist",
        playlist=playlist.name,
        plugin_id=instance.plugin_id,
        plugin_instance=instance.name,
        refresh_time=anchor,
        image_hash=compute_image_hash(Image.new("RGB", (32, 16), "black")),
    )
    before_lanes = _non_presentation_lane_bytes(task.runtime_state.snapshot().instances[instance.instance_uuid])
    _install_display_provider_plugin_sentinels(monkeypatch)

    result = _queue_and_process(
        task,
        _presentation_followup_command(task, playlist, instance, request),
    )
    state = task.runtime_state.snapshot().instances[instance.instance_uuid]

    assert result.job.status is JobStatus.SUCCEEDED
    assert len(display.calls) == 1
    assert state.presentation_request is not None
    assert state.presentation_request.request_id != request.request_id
    assert state.presentation_receipt == PresentationCommitReceipt(
        request_id=request.request_id,
        committed_at=display.calls[0]["committed_at"],
        display_commit_id=display.calls[0]["commit_id"],
        structural_generation=instance.structural_generation,
        settings_revision=instance.settings_revision,
        theme_mode=None,
    )
    assert state.presentation.last_success_at == display.calls[0]["committed_at"]
    assert state.last_good_cache.promoted_at == display.calls[0]["committed_at"]
    assert _non_presentation_lane_bytes(state) == before_lanes
    assert device_config.refresh_info.refresh_time == anchor
    assert Image.open(canonical).getpixel((0, 0)) == (255, 255, 255)
    assert not Path(candidate.cache_path).exists()


def test_changed_target_keeps_prepared_item_for_next_normal_selection(monkeypatch):
    other_uuid = str(uuid.uuid4())

    def change_target(manager, call):
        manager.bound_runtime_state.set_display_state(
            "committed",
            "new-target-commit",
            instance_uuid=other_uuid,
            changed_at=(PRESENTATION_NOW + timedelta(seconds=2)).isoformat(),
        )

    display = PresentationTransactionDisplayManager(after_display=change_target)
    task, device_config, _clock, playlist, _display = _make_presentation_task(
        "presentation-target-changed",
        display_manager=display,
    )
    instance = playlist.plugins[0].snapshot()
    canonical = _write_runtime_cache(
        task,
        instance,
        Image.new("RGB", (32, 16), "black"),
    )
    _seed_independent_lane_clocks(task, instance)
    request = _seed_presentation_request(task, instance)
    candidate = _seed_prepared_presentation(
        task,
        instance,
        request,
        image=Image.new("RGB", (32, 16), "white"),
    )
    task.runtime_state.set_display_state(
        "committed",
        request.origin_display_commit_id,
        instance_uuid=instance.instance_uuid,
        changed_at=request.requested_at,
    )
    original_refresh = device_config.refresh_info
    before = task.runtime_state.snapshot().instances[instance.instance_uuid]
    before_lanes = _non_presentation_lane_bytes(before)
    _install_display_provider_plugin_sentinels(monkeypatch)

    result = _queue_and_process(
        task,
        _presentation_followup_command(task, playlist, instance, request),
    )
    state = task.runtime_state.snapshot().instances[instance.instance_uuid]

    assert result.job.status is JobStatus.CANCELED
    assert len(display.calls) == 1
    assert state.presentation_request == before.presentation_request
    assert state.presentation_receipt is None
    assert state.presentation.last_success_at is None
    assert _non_presentation_lane_bytes(state) == before_lanes
    assert Path(candidate.cache_path).exists()
    assert Image.open(canonical).getpixel((0, 0)) == (0, 0, 0)
    assert device_config.refresh_info is original_refresh


def test_normal_display_consuming_prepared_item_requests_the_next_item(
    monkeypatch,
):
    task, _config, _clock, playlist, display = _make_presentation_task("presentation-normal-consume")
    instance = playlist.plugins[0].snapshot()
    _write_runtime_cache(task, instance, Image.new("RGB", (32, 16), "black"))
    request = _seed_presentation_request(task, instance)
    candidate = _seed_prepared_presentation(
        task,
        instance,
        request,
        image=Image.new("RGB", (32, 16), "white"),
    )
    _install_display_provider_plugin_sentinels(monkeypatch)

    command = _normal_cache_display_command(task, playlist, instance)
    assert command.allow_prepared_presentation is True
    result = _queue_and_process(task, command)
    state = task.runtime_state.snapshot().instances[instance.instance_uuid]

    assert result.job.status is JobStatus.SUCCEEDED
    assert len(display.calls) == 1
    assert state.presentation_request is not None
    assert state.presentation_request.request_id != request.request_id
    assert state.presentation_receipt.request_id == request.request_id
    assert state.presentation.last_success_at == display.calls[0]["committed_at"]
    assert not Path(candidate.cache_path).exists()


def test_same_pixel_prepared_item_gets_a_new_display_commit_receipt(monkeypatch):
    task, _config, _clock, playlist, display = _make_presentation_task("presentation-same-pixel-commit")
    instance = playlist.plugins[0].snapshot()
    pixels = Image.new("RGB", (32, 16), "black")
    _write_runtime_cache(task, instance, pixels)
    request = _seed_presentation_request(
        task,
        instance,
        origin_commit_id="same-pixel-origin-commit",
    )
    _seed_prepared_presentation(task, instance, request, image=pixels)
    task.runtime_state.set_display_state(
        "committed",
        request.origin_display_commit_id,
        instance_uuid=instance.instance_uuid,
        changed_at=request.requested_at,
    )
    _install_display_provider_plugin_sentinels(monkeypatch)

    _queue_and_process(
        task,
        _normal_cache_display_command(task, playlist, instance),
    )
    receipt = task.runtime_state.snapshot().instances[instance.instance_uuid].presentation_receipt

    assert len(display.calls) == 1
    assert receipt.display_commit_id == display.calls[0]["commit_id"]
    assert receipt.display_commit_id != request.origin_display_commit_id


def test_corrupt_prepared_png_cools_only_presentation_and_keeps_authoritative_cache(
    monkeypatch,
):
    task, _config, _clock, playlist, display = _make_presentation_task("presentation-corrupt-prepared")
    instance = playlist.plugins[0].snapshot()
    canonical = _write_runtime_cache(
        task,
        instance,
        Image.new("RGB", (32, 16), "black"),
    )
    authoritative_bytes = canonical.read_bytes()
    _seed_independent_lane_clocks(task, instance)
    baseline_last_good = LastGoodCacheState(
        theme_mode=None,
        structural_generation=instance.structural_generation,
        settings_revision=instance.settings_revision,
        promoted_at=(PRESENTATION_NOW - timedelta(minutes=10)).isoformat(),
    )
    task.runtime_state.record_success(
        instance.instance_uuid,
        baseline_last_good.promoted_at,
        lane=RefreshLane.DATA,
        last_good_cache=baseline_last_good,
    )
    request = _seed_presentation_request(task, instance)
    candidate = _seed_prepared_presentation(task, instance, request)
    Path(candidate.cache_path).write_bytes(b"not-a-png")
    before = task.runtime_state.snapshot().instances[instance.instance_uuid]
    before_lanes = _non_presentation_lane_bytes(before)
    _install_display_provider_plugin_sentinels(monkeypatch)

    result = _queue_and_process(
        task,
        _normal_cache_display_command(task, playlist, instance),
    )
    state = task.runtime_state.snapshot().instances[instance.instance_uuid]

    assert result.job.status is JobStatus.CANCELED
    assert display.calls == []
    assert canonical.read_bytes() == authoritative_bytes
    assert state.last_good_cache == baseline_last_good
    assert state.presentation_request.request_id == request.request_id
    assert state.presentation_request.prepared_at is None
    assert state.presentation_request.prepared_theme_mode is None
    assert state.presentation.last_failure_at is not None
    assert state.presentation.next_retry_at is not None
    assert state.presentation.last_success_at is None
    assert _non_presentation_lane_bytes(state) == before_lanes


@pytest.mark.parametrize("restart_state", ["requested", "prepared"])
def test_restart_replays_requested_or_prepared_presentation_without_duplicate_selection(
    monkeypatch,
    restart_state,
):
    task, device_config, clock, playlist, _display = _make_presentation_task(f"presentation-restart-{restart_state}")
    instance = playlist.plugins[0].snapshot()
    _write_runtime_cache(task, instance, Image.new("RGB", (32, 16), "black"))
    task.runtime_state.record_success(
        instance.instance_uuid,
        PRESENTATION_NOW.isoformat(),
        lane=RefreshLane.DATA,
    )
    request = _seed_presentation_request(task, instance)
    task.runtime_state.set_display_state(
        "committed",
        request.origin_display_commit_id,
        instance_uuid=instance.instance_uuid,
        changed_at=request.requested_at,
    )
    if restart_state == "prepared":
        _seed_prepared_presentation(task, instance, request)
    assert task.runtime_state.flush()

    plugin = PresentationBankPlugin()
    first_restart = RefreshTask(
        device_config,
        PresentationTransactionDisplayManager(),
        clock=clock.monotonic,
        wall_clock=clock.wall_time,
    )
    if restart_state == "requested":
        monkeypatch.setattr(
            refresh_task_module,
            "get_plugin_instance",
            lambda _config: plugin,
        )
        monkeypatch.setattr(
            first_restart,
            "_get_current_datetime",
            lambda: PRESENTATION_NOW,
        )
        monkeypatch.setattr(
            first_restart,
            "_select_cached_display_command",
            lambda _now: None,
        )
        monkeypatch.setattr(
            first_restart,
            "_memory_watchdog_should_restart",
            lambda: False,
        )
        monkeypatch.setattr(
            first_restart,
            "_resource_sample",
            lambda: ResourceSample(available_mb=512, swap_percent=0),
        )
        first_restart._schedule_if_due()
        prepared_entry = first_restart.refresh_queue.take(timeout=0)
        assert prepared_entry is not None
        assert prepared_entry.command.intent is RefreshIntent.PRESENTATION_REFRESH
        first_restart._process_queue_entry(prepared_entry)
        assert first_restart.runtime_state.flush()

    second_display = PresentationTransactionDisplayManager()
    second_restart = RefreshTask(
        device_config,
        second_display,
        clock=clock.monotonic,
        wall_clock=clock.wall_time,
    )
    _install_display_provider_plugin_sentinels(monkeypatch)
    _queue_and_process(
        second_restart,
        _normal_cache_display_command(
            second_restart,
            playlist,
            instance,
        ),
    )
    state = second_restart.runtime_state.snapshot().instances[instance.instance_uuid]

    assert state.presentation_request is not None
    assert state.presentation_request.request_id != request.request_id
    assert state.presentation_receipt.request_id == request.request_id
    assert len(second_display.calls) == 1
    assert [event[0] for event in plugin.events].count("prepare") == (1 if restart_state == "requested" else 0)


def test_restart_does_not_reuse_committed_receipt_as_next_presentation_preflight(
    monkeypatch,
):
    task, device_config, clock, playlist, _display = _make_presentation_task(
        "presentation-restart-committed-receipt"
    )
    instance = playlist.plugins[0].snapshot()
    _write_runtime_cache(task, instance, Image.new("RGB", (32, 16), "black"))
    request = _seed_presentation_request(task, instance)
    _seed_prepared_presentation(task, instance, request)
    committed_at = PRESENTATION_NOW + timedelta(seconds=2)
    receipt = PresentationCommitReceipt(
        request_id=request.request_id,
        committed_at=committed_at.isoformat(),
        display_commit_id="committed-presentation-image",
        structural_generation=instance.structural_generation,
        settings_revision=instance.settings_revision,
        theme_mode=None,
    )
    assert task.runtime_state.commit_presentation(
        instance.instance_uuid,
        receipt,
        last_good_cache=LastGoodCacheState(
            theme_mode=None,
            structural_generation=instance.structural_generation,
            settings_revision=instance.settings_revision,
            promoted_at=receipt.committed_at,
        ),
    )
    task.runtime_state.set_display_state(
        "committed",
        receipt.display_commit_id,
        instance_uuid=instance.instance_uuid,
        changed_at=receipt.committed_at,
    )
    assert task.runtime_state.flush()

    # RefreshInfo is sampled before the hardware-backed display manifest commit,
    # so its persisted rotation anchor can be slightly older than the receipt.
    device_config.refresh_info.refresh_time = (
        committed_at - timedelta(milliseconds=1)
    ).isoformat()
    restarted = RefreshTask(
        device_config,
        PresentationTransactionDisplayManager(),
        clock=clock.monotonic,
        wall_clock=clock.wall_time,
    )
    monkeypatch.setattr(
        restarted,
        "_resource_sample",
        lambda: ResourceSample(available_mb=512, swap_percent=0),
    )

    command = restarted._select_cached_display_command(
        committed_at + timedelta(seconds=61)
    )
    state = restarted.runtime_state.snapshot().instances[instance.instance_uuid]

    assert command is None
    assert state.presentation_request is not None
    assert state.presentation_request.request_id != receipt.request_id
    assert (
        state.presentation_request.origin_display_commit_id
        == receipt.display_commit_id
    )
    assert state.presentation_receipt == receipt
    assert playlist.is_rotation_reservation_current(instance.instance_uuid) is True


def test_restart_allows_later_no_change_success_after_prior_receipt(monkeypatch):
    task, device_config, clock, playlist, _display = _make_presentation_task(
        "presentation-restart-no-change-after-receipt"
    )
    instance = playlist.plugins[0].snapshot()
    _write_runtime_cache(task, instance, Image.new("RGB", (32, 16), "black"))
    prior_request = _seed_presentation_request(
        task,
        instance,
        request_id="a" * 32,
        requested_at=PRESENTATION_NOW - timedelta(minutes=20),
    )
    assert task.runtime_state.mark_presentation_prepared(
        instance.instance_uuid,
        prior_request.request_id,
        (PRESENTATION_NOW - timedelta(minutes=19)).isoformat(),
        None,
    )
    prior_receipt = PresentationCommitReceipt(
        request_id=prior_request.request_id,
        committed_at=(PRESENTATION_NOW - timedelta(minutes=18)).isoformat(),
        display_commit_id="prior-prepared-display",
        structural_generation=instance.structural_generation,
        settings_revision=instance.settings_revision,
        theme_mode=None,
    )
    assert task.runtime_state.commit_presentation(
        instance.instance_uuid,
        prior_receipt,
        last_good_cache=LastGoodCacheState(
            theme_mode=None,
            structural_generation=instance.structural_generation,
            settings_revision=instance.settings_revision,
            promoted_at=prior_receipt.committed_at,
        ),
    )
    no_change_request = _seed_presentation_request(
        task,
        instance,
        request_id="b" * 32,
        requested_at=PRESENTATION_NOW,
        origin_commit_id="no-change-origin",
    )
    assert task.runtime_state.satisfy_presentation_no_change(
        instance.instance_uuid,
        no_change_request.request_id,
        no_change_request.requested_at,
    )
    assert task.runtime_state.flush()

    device_config.refresh_info.refresh_time = (
        PRESENTATION_NOW - timedelta(seconds=1)
    ).isoformat()
    restarted = RefreshTask(
        device_config,
        PresentationTransactionDisplayManager(),
        clock=clock.monotonic,
        wall_clock=clock.wall_time,
    )
    monkeypatch.setattr(
        restarted,
        "_resource_sample",
        lambda: ResourceSample(available_mb=512, swap_percent=0),
    )

    command = restarted._select_cached_display_command(
        PRESENTATION_NOW + timedelta(seconds=61)
    )
    state = restarted.runtime_state.snapshot().instances[instance.instance_uuid]

    assert command is not None
    assert command.intent is RefreshIntent.DISPLAY_CACHE
    assert command.allow_prepared_presentation is False
    assert state.presentation_request is None
    assert state.presentation_receipt == prior_receipt


@pytest.mark.parametrize(
    "receipt_committed_at",
    [
        "not-an-iso-timestamp",
        (PRESENTATION_NOW + timedelta(seconds=1)).isoformat(),
    ],
)
def test_presentation_satisfaction_fails_closed_for_invalid_or_reverse_receipt_time(
    receipt_committed_at,
):
    task, _device_config, _clock, _playlist, _display = _make_presentation_task(
        "presentation-invalid-receipt-time"
    )
    state = SimpleNamespace(
        presentation=SimpleNamespace(last_success_at=PRESENTATION_NOW.isoformat()),
        presentation_receipt=SimpleNamespace(committed_at=receipt_committed_at),
    )

    assert task._presentation_succeeded_since_display(
        state,
        PRESENTATION_NOW - timedelta(seconds=1),
        PRESENTATION_NOW + timedelta(seconds=2),
    ) is False


def test_hard_pressure_defers_stale_cache_without_presentation_renderer(monkeypatch):
    task, device_config, _clock, playlist, display = _make_presentation_task("presentation-hard-pressure")
    instance = playlist.plugins[0].snapshot()
    _write_runtime_cache(task, instance, Image.new("RGB", (32, 16), "black"))
    request = _seed_presentation_request(task, instance)
    device_config.refresh_info.refresh_time = (PRESENTATION_NOW - timedelta(minutes=2)).isoformat()
    monkeypatch.setattr(task, "_get_current_datetime", lambda: PRESENTATION_NOW)
    monkeypatch.setattr(task, "_memory_watchdog_should_restart", lambda: False)
    monkeypatch.setattr(
        task,
        "_resource_sample",
        lambda: ResourceSample(available_mb=60, swap_percent=80),
    )
    _install_display_provider_plugin_sentinels(monkeypatch)

    task._schedule_if_due()
    entry = task.refresh_queue.take(timeout=0)

    assert entry is None
    assert len(display.calls) == 0
    assert instance.instance_uuid in playlist.plugin_rotation_queue
    assert playlist.is_rotation_reservation_current(instance.instance_uuid) is False
    assert task.runtime_state.snapshot().instances[instance.instance_uuid].presentation_request == request


def test_soft_pressure_makes_bounded_data_and_presentation_progress(monkeypatch):
    clock = RuntimeClock()
    task, _config, _unused, playlist, display = _make_presentation_task(
        "presentation-soft-fairness",
        plugin_count=4,
        latest_refresh_time=None,
        clock=clock,
    )
    instances = [plugin.snapshot() for plugin in playlist.plugins]
    presentation_instance = instances[-1]
    _write_runtime_cache(
        task,
        presentation_instance,
        Image.new("RGB", (32, 16), "black"),
    )
    task.runtime_state.record_success(
        presentation_instance.instance_uuid,
        PRESENTATION_NOW.isoformat(),
        lane=RefreshLane.DATA,
    )
    request = _seed_presentation_request(task, presentation_instance)
    task.runtime_state.set_display_state(
        "committed",
        request.origin_display_commit_id,
        instance_uuid=presentation_instance.instance_uuid,
        changed_at=request.requested_at,
    )
    before_lanes = _non_presentation_lane_bytes(
        task.runtime_state.snapshot().instances[presentation_instance.instance_uuid]
    )
    plugins = {instance.plugin_id: PresentationBankPlugin() for instance in instances}
    monkeypatch.setattr(
        refresh_task_module,
        "get_plugin_instance",
        lambda config: plugins[config["id"]],
    )
    current_dt = [PRESENTATION_NOW]
    monkeypatch.setattr(task, "_get_current_datetime", lambda: current_dt[0])
    monkeypatch.setattr(task, "_select_cached_display_command", lambda _now: None)
    monkeypatch.setattr(task, "_memory_watchdog_should_restart", lambda: False)
    monkeypatch.setattr(
        task,
        "_resource_sample",
        lambda: ResourceSample(available_mb=100, swap_percent=0),
    )

    intents = []
    for _ in range(4):
        task._schedule_if_due()
        entry = task.refresh_queue.take(timeout=0)
        assert entry is not None
        intents.append(entry.command.intent)
        task._process_queue_entry(entry)
        clock.advance(60)
        current_dt[0] += timedelta(seconds=60)

    state = task.runtime_state.snapshot().instances[presentation_instance.instance_uuid]
    assert intents == [
        RefreshIntent.DATA_REFRESH,
        RefreshIntent.DATA_REFRESH,
        RefreshIntent.DATA_REFRESH,
        RefreshIntent.PRESENTATION_REFRESH,
    ]
    assert state.presentation_request.prepared_at is not None
    assert state.presentation.last_success_at is None
    assert _non_presentation_lane_bytes(state) == before_lanes
    assert display.calls == []


def test_presentation_no_change_succeeds_at_committed_origin_without_display(
    monkeypatch,
):
    task, _config, _clock, playlist, display = _make_presentation_task("presentation-no-change-origin")
    instance = playlist.plugins[0].snapshot()
    _write_runtime_cache(task, instance, Image.new("RGB", (32, 16), "black"))
    _seed_independent_lane_clocks(task, instance)
    prior_request = _seed_presentation_request(
        task,
        instance,
        request_id="c" * 32,
        requested_at=PRESENTATION_NOW - timedelta(minutes=20),
        origin_commit_id="no-change-prior-origin",
    )
    assert task.runtime_state.mark_presentation_prepared(
        instance.instance_uuid,
        prior_request.request_id,
        (PRESENTATION_NOW - timedelta(minutes=15)).isoformat(),
        None,
    )
    prior_receipt = PresentationCommitReceipt(
        request_id=prior_request.request_id,
        committed_at=(PRESENTATION_NOW - timedelta(minutes=10)).isoformat(),
        display_commit_id="no-change-prior-prepared",
        structural_generation=instance.structural_generation,
        settings_revision=instance.settings_revision,
        theme_mode=None,
    )
    assert task.runtime_state.commit_presentation(
        instance.instance_uuid,
        prior_receipt,
        last_good_cache=LastGoodCacheState(
            theme_mode=None,
            structural_generation=instance.structural_generation,
            settings_revision=instance.settings_revision,
            promoted_at=prior_receipt.committed_at,
        ),
    )
    request = _seed_presentation_request(
        task,
        instance,
        request_id="d" * 32,
        origin_commit_id="no-change-current-origin",
    )
    task.runtime_state.set_display_state(
        "committed",
        request.origin_display_commit_id,
        instance_uuid=instance.instance_uuid,
        changed_at=request.requested_at,
    )
    before_lanes = _non_presentation_lane_bytes(task.runtime_state.snapshot().instances[instance.instance_uuid])
    plugin = NoChangePresentationPlugin()

    def forbidden(*_args, **_kwargs):
        raise AssertionError("NO_CHANGE must not reconcile or prepare")

    plugin.reconcile_presentation_receipt = forbidden
    plugin.prepare_presentation = forbidden
    plugin.generate_image = forbidden
    monkeypatch.setattr(task.presentation_cache, "save", forbidden)
    monkeypatch.setattr(refresh_task_module, "PresentationCommitReceipt", forbidden)
    monkeypatch.setattr(refresh_task_module, "PresentationRequestContext", forbidden)
    monkeypatch.setattr(
        refresh_task_module,
        "get_plugin_instance",
        lambda _config: plugin,
    )
    monkeypatch.setattr(task, "_get_current_datetime", lambda: PRESENTATION_NOW)
    monkeypatch.setattr(task, "_select_cached_display_command", lambda _now: None)
    monkeypatch.setattr(task, "_memory_watchdog_should_restart", lambda: False)
    monkeypatch.setattr(
        task,
        "_resource_sample",
        lambda: ResourceSample(available_mb=512, swap_percent=0),
    )

    task._schedule_if_due()
    entry = task.refresh_queue.take(timeout=0)
    assert entry is not None
    task._process_queue_entry(entry)
    state = task.runtime_state.snapshot().instances[instance.instance_uuid]

    assert state.presentation_request is None
    assert state.presentation.last_success_at == request.requested_at
    assert state.presentation_receipt == prior_receipt
    assert _non_presentation_lane_bytes(state) == before_lanes
    assert [event[0] for event in plugin.events] == ["mode"]
    assert (
        presentation_contract.get_presentation_instance_uuid(plugin.events[0][1])
        == instance.instance_uuid
    )
    assert display.calls == []


def test_invalid_refresh_on_display_is_safe_false_after_scheduler_display_probe(
    monkeypatch,
):
    task, device_config, _clock, playlist, display = _make_presentation_task("presentation-invalid-trigger")
    instance = playlist.plugins[0].snapshot()
    playlist.plugins[0].settings["refreshOnDisplay"] = "sometimes"
    instance = playlist.plugins[0].snapshot()
    _write_runtime_cache(task, instance, Image.new("RGB", (32, 16), "black"))
    device_config.refresh_info.refresh_time = (PRESENTATION_NOW - timedelta(minutes=2)).isoformat()
    monkeypatch.setattr(task, "_get_current_datetime", lambda: PRESENTATION_NOW)
    monkeypatch.setattr(task, "_memory_watchdog_should_restart", lambda: False)
    monkeypatch.setattr(
        task,
        "_resource_sample",
        lambda: ResourceSample(available_mb=512, swap_percent=0),
    )
    _install_display_provider_plugin_sentinels(monkeypatch)

    task._schedule_if_due()
    entry = task.refresh_queue.take(timeout=0)
    assert entry is not None
    task._process_queue_entry(entry)
    state = task.runtime_state.snapshot().instances.get(instance.instance_uuid)

    assert len(display.calls) == 1
    assert state is None or state.presentation_request is None
    assert task.scheduler_snapshot().last_error is None


def test_legacy_async_presentation_mode_fails_closed_without_renderer(monkeypatch):
    task, _config, _clock, playlist, display = _make_presentation_task("presentation-legacy-disabled")
    instance = playlist.plugins[0].snapshot()
    _write_runtime_cache(task, instance, Image.new("RGB", (32, 16), "black"))
    task.runtime_state.record_success(
        instance.instance_uuid,
        PRESENTATION_NOW.isoformat(),
        lane=RefreshLane.DATA,
    )
    request = _seed_presentation_request(task, instance)
    plugin = LegacyPresentationPlugin()
    monkeypatch.setattr(
        refresh_task_module,
        "get_plugin_instance",
        lambda _config: plugin,
    )
    monkeypatch.setattr(task, "_get_current_datetime", lambda: PRESENTATION_NOW)
    monkeypatch.setattr(task, "_select_cached_display_command", lambda _now: None)
    monkeypatch.setattr(task, "_memory_watchdog_should_restart", lambda: False)
    monkeypatch.setattr(
        task,
        "_resource_sample",
        lambda: ResourceSample(available_mb=512, swap_percent=0),
    )

    task._schedule_if_due()
    entry = task.refresh_queue.take(timeout=0)
    assert entry is not None
    task._process_queue_entry(entry)
    state = task.runtime_state.snapshot().instances[instance.instance_uuid]

    assert task.refresh_queue.get_entry(entry.job.id).job.status is JobStatus.FAILED
    assert state.presentation_request == request
    assert state.presentation.last_failure_at is not None
    assert state.presentation.next_retry_at is not None
    assert state.presentation.last_success_at is None
    assert [event[0] for event in plugin.events] == ["mode"]
    assert display.calls == []


def test_presentation_commit_cas_false_retains_prepared_candidate(monkeypatch):
    task, _config, _clock, playlist, display = _make_presentation_task("presentation-commit-cas-false")
    instance = playlist.plugins[0].snapshot()
    _write_runtime_cache(task, instance, Image.new("RGB", (32, 16), "black"))
    request = _seed_presentation_request(task, instance)
    candidate = _seed_prepared_presentation(
        task,
        instance,
        request,
        image=Image.new("RGB", (32, 16), "white"),
    )
    _install_display_provider_plugin_sentinels(monkeypatch)
    monkeypatch.setattr(task.runtime_state, "commit_presentation", lambda *a, **k: False)

    result = _queue_and_process(
        task,
        _normal_cache_display_command(task, playlist, instance),
    )
    state = task.runtime_state.snapshot().instances[instance.instance_uuid]

    assert result.job.status is JobStatus.CANCELED
    assert len(display.calls) == 1
    assert state.presentation_request.request_id == request.request_id
    assert state.presentation_receipt is None
    assert state.presentation.last_success_at is None
    assert Path(candidate.cache_path).exists()


@pytest.mark.parametrize("failure_point", ["display", "commit"])
@pytest.mark.parametrize(
    ("display_policy", "provider_refresh", "provider_free"),
    [(True, False, False), (False, True, False), (False, False, True)],
)
def test_prepared_display_exception_cools_only_presentation_and_schedules_exact_retry(
    monkeypatch,
    failure_point,
    display_policy,
    provider_refresh,
    provider_free,
):
    def fail_after_display(_manager, _call):
        if failure_point == "display":
            raise RuntimeError("prepared display failed")

    display = PresentationTransactionDisplayManager(after_display=fail_after_display)
    task, device_config, clock, playlist, _display = _make_presentation_task(
        f"presentation-{failure_point}-exception",
        display_manager=display,
        provider_refresh=provider_refresh,
        provider_free=provider_free,
    )
    device_config.config["display_triggered_refresh_enabled"] = display_policy
    instance = playlist.plugins[0].snapshot()
    _write_runtime_cache(task, instance, Image.new("RGB", (32, 16), "black"))
    _seed_independent_lane_clocks(task, instance)
    request = _seed_presentation_request(task, instance)
    candidate = _seed_prepared_presentation(task, instance, request)
    task.runtime_state.set_display_state(
        "committed",
        request.origin_display_commit_id,
        instance_uuid=instance.instance_uuid,
        changed_at=request.requested_at,
    )
    before_lanes = _non_presentation_lane_bytes(
        task.runtime_state.snapshot().instances[instance.instance_uuid]
    )
    if failure_point == "commit":
        def fail_commit(*_args, **_kwargs):
            raise RuntimeError("presentation commit failed")

        monkeypatch.setattr(task.runtime_state, "commit_presentation", fail_commit)
    now = [PRESENTATION_NOW]
    monkeypatch.setattr(task, "_get_current_datetime", lambda: now[0])
    monkeypatch.setattr(task, "_memory_watchdog_should_restart", lambda: False)
    monkeypatch.setattr(
        task,
        "_resource_sample",
        lambda: ResourceSample(available_mb=512, swap_percent=0),
    )
    _install_display_provider_plugin_sentinels(monkeypatch)

    result = _queue_and_process(
        task,
        _presentation_followup_command(task, playlist, instance, request),
    )
    failed_state = task.runtime_state.snapshot().instances[instance.instance_uuid]

    assert result.job.status is JobStatus.FAILED
    assert _non_presentation_lane_bytes(failed_state) == before_lanes
    assert failed_state.presentation.last_failure_at is not None
    assert failed_state.presentation.next_retry_at is not None
    assert failed_state.presentation_request.request_id == request.request_id
    assert failed_state.presentation_request.prepared_at is not None
    assert Path(candidate.cache_path).exists()
    assert task.refresh_queue.take(timeout=0) is None

    clock.advance(3601)
    now[0] += timedelta(seconds=3601)
    task._schedule_if_due()
    retry = task.refresh_queue.take(timeout=0)

    assert retry is not None
    assert retry.command.intent is RefreshIntent.DISPLAY_CACHE
    assert retry.command.payload["presentation_request_id"] == request.request_id
    assert retry.command.coalescing_scope == f"presentation-followup:{request.request_id}"
    assert retry.command.allow_prepared_presentation is True


def test_presentation_commit_published_then_raised_finishes_as_committed(
    monkeypatch,
):
    task, _config, _clock, playlist, display = _make_presentation_task(
        "presentation-commit-published-then-raised"
    )
    instance = playlist.plugins[0].snapshot()
    _write_runtime_cache(task, instance, Image.new("RGB", (32, 16), "black"))
    request = _seed_presentation_request(task, instance)
    candidate = _seed_prepared_presentation(task, instance, request)
    task.runtime_state.set_display_state(
        "committed",
        request.origin_display_commit_id,
        instance_uuid=instance.instance_uuid,
        changed_at=request.requested_at,
    )
    original_commit = task.runtime_state.commit_presentation

    def commit_then_raise(*args, **kwargs):
        assert original_commit(*args, **kwargs) is True
        raise RuntimeError("runtime persistence failed after publication")

    monkeypatch.setattr(
        task.runtime_state,
        "commit_presentation",
        commit_then_raise,
    )
    _install_display_provider_plugin_sentinels(monkeypatch)

    result = _queue_and_process(
        task,
        _presentation_followup_command(task, playlist, instance, request),
    )
    state = task.runtime_state.snapshot().instances[instance.instance_uuid]

    assert result.job.status is JobStatus.SUCCEEDED
    assert len(display.calls) == 1
    assert state.presentation_request is not None
    assert state.presentation_request.request_id != request.request_id
    assert state.presentation_receipt.request_id == request.request_id
    assert state.presentation.last_failure_at is None
    assert not Path(candidate.cache_path).exists()


def test_prepared_display_commits_theme_only_after_hardware_write(monkeypatch):
    task, device_config, _clock, playlist, display = _make_presentation_task(
        "prepared-display-theme-commit"
    )
    instance = playlist.plugins[0].snapshot()
    device_config.config["theme_mode"] = "night"
    manifest = PluginManifest(
        schema_version=2,
        id=instance.plugin_id,
        class_name="PresentationPlugin",
        display_name="Presentation Plugin",
        refresh_on_display=True,
        capabilities=PluginCapabilities(
            supports_presentation_refresh=True,
            supports_day_night_theme=True,
        ),
        raw={},
    )
    device_config.get_plugin = lambda plugin_id: {
        "id": plugin_id,
        "refresh_on_display": True,
        "_manifest": manifest,
    }
    request = _seed_presentation_request(
        task,
        instance,
        origin_theme_mode="day",
    )
    candidate = _seed_prepared_presentation(
        task,
        instance,
        request,
        image=Image.new("RGB", (32, 16), "white"),
        theme_mode="night",
    )
    task.runtime_state.set_display_state(
        "committed",
        request.origin_display_commit_id,
        instance_uuid=instance.instance_uuid,
        changed_at=request.requested_at,
    )
    _install_display_provider_plugin_sentinels(monkeypatch)
    command = task._playlist_command(
        playlist.name,
        instance,
        source=CommandSource.BACKGROUND,
        intent=RefreshIntent.DISPLAY_CACHE,
        force=False,
        display_cached_only=True,
        priority=65,
        kind=CommandKind.DISPLAY,
        current_dt=PRESENTATION_NOW,
        theme_context={"mode": "night", "source": "config"},
        cache_theme_mode="night",
        expected_displayed_instance_uuid=instance.instance_uuid,
        preserve_rotation_anchor=True,
        coalescing_scope=f"presentation-followup:{request.request_id}",
        allow_prepared_presentation=True,
        presentation_request_id=request.request_id,
    )

    result = _queue_and_process(task, command)
    state = task.runtime_state.snapshot().instances[instance.instance_uuid]

    assert result.job.status is JobStatus.SUCCEEDED
    assert len(display.calls) == 1
    assert device_config.config["active_theme"] == "night"
    assert state.presentation_request is not None
    assert state.presentation_request.request_id != request.request_id
    assert state.presentation_receipt.theme_mode == "night"
    assert not Path(candidate.cache_path).exists()


def test_exact_presentation_followup_with_revoked_capability_never_falls_back(
    monkeypatch,
):
    task, device_config, _clock, playlist, display = _make_presentation_task(
        "presentation-capability-revoked"
    )
    instance = playlist.plugins[0].snapshot()
    canonical = _write_runtime_cache(
        task,
        instance,
        Image.new("RGB", (32, 16), "black"),
    )
    authoritative_bytes = canonical.read_bytes()
    request = _seed_presentation_request(task, instance)
    candidate = _seed_prepared_presentation(task, instance, request)
    task.runtime_state.set_display_state(
        "committed",
        request.origin_display_commit_id,
        instance_uuid=instance.instance_uuid,
        changed_at=request.requested_at,
    )
    revoked = PluginManifest(
        schema_version=2,
        id=instance.plugin_id,
        class_name="PresentationPlugin",
        display_name="Presentation Plugin",
        refresh_on_display=True,
        capabilities=PluginCapabilities(supports_presentation_refresh=False),
        raw={},
    )
    device_config.get_plugin = lambda plugin_id: {
        "id": plugin_id,
        "refresh_on_display": True,
        "_manifest": revoked,
    }
    _install_display_provider_plugin_sentinels(monkeypatch)

    result = _queue_and_process(
        task,
        _presentation_followup_command(task, playlist, instance, request),
    )
    state = task.runtime_state.snapshot().instances[instance.instance_uuid]

    assert result.job.status is JobStatus.CANCELED
    assert display.calls == []
    assert state.presentation_request.request_id == request.request_id
    assert state.presentation_receipt is None
    assert Path(candidate.cache_path).exists()
    assert canonical.read_bytes() == authoritative_bytes


def test_prepared_display_rechecks_provider_free_attestation_before_consumption(
    monkeypatch,
):
    task, device_config, _clock, playlist, display = _make_presentation_task(
        "prepared-provider-free-revoked-during-consumption",
        provider_free=True,
    )
    device_config.config["display_triggered_refresh_enabled"] = False
    instance = playlist.plugins[0].snapshot()
    _write_runtime_cache(task, instance, Image.new("RGB", (32, 16), "black"))
    request = _seed_presentation_request(task, instance)
    candidate = _seed_prepared_presentation(task, instance, request)
    task.runtime_state.set_display_state(
        "committed",
        request.origin_display_commit_id,
        instance_uuid=instance.instance_uuid,
        changed_at=request.requested_at,
    )
    command = _presentation_followup_command(task, playlist, instance, request)
    attested = _presentation_manifest(instance.plugin_id, provider_free=True)
    revoked = _presentation_manifest(instance.plugin_id, provider_free=False)
    manifest_calls = []

    def current_plugin_config(plugin_id):
        manifest_calls.append(plugin_id)
        return {
            "id": plugin_id,
            "refresh_on_display": True,
            "_manifest": attested if len(manifest_calls) == 1 else revoked,
        }

    device_config.get_plugin = current_plugin_config
    _install_display_provider_plugin_sentinels(monkeypatch)

    with pytest.raises(
        refresh_task_module._StaleSelection,
        match="presentation refresh policy is no longer enabled",
    ):
        task._execute_command(command)

    assert display.calls == []
    assert Path(candidate.cache_path).exists()


def _task6_provenance_api():
    try:
        from plugins.base_plugin.render_provenance import (
            SourceProvenance,
            attach_source_provenance,
            read_source_provenance,
        )
    except ModuleNotFoundError:
        pytest.fail("Task 6 render provenance contract is missing")
    return SourceProvenance, attach_source_provenance, read_source_provenance


def test_source_provenance_attestation_cannot_be_forged_or_persisted(tmp_path):
    SourceProvenance, attach, read = _task6_provenance_api()
    forged = Image.new("RGB", (2, 1), "white")
    forged.info["inkypi_source_provenance"] = "live"
    forged.info["inkypi_source_detail"] = "task6_test"
    assert read(forged) is None

    forged.info["inkypi_source_provenance"] = {"value": "live"}
    assert read(forged) is None

    image = Image.new("RGB", (2, 1), "white")
    unsafe = "sk-secret https://provider.example/user-feed?token=abc {payload}" * 20

    result = attach(image, SourceProvenance.LIVE, detail=unsafe)

    assert result is image
    assert read(image) is SourceProvenance.LIVE
    assert "inkypi_source_provenance" not in image.info
    assert "inkypi_source_detail" not in image.info

    saved = tmp_path / "provenance.png"
    image.save(saved)
    with Image.open(saved) as persisted:
        persisted.load()
        assert read(persisted) is None


@pytest.mark.parametrize(
    ("provenance_name", "degraded"),
    [
        ("LIVE", False),
        ("FRESH_CACHE", False),
        ("STALE_CACHE", True),
        ("LOCAL_FALLBACK", True),
        ("RAW_VALID", False),
        ("RAW_MALFORMED", False),
        (None, False),
    ],
)
def test_data_source_provenance_controls_success_without_blocking_image_promotion(
    provenance_name,
    degraded,
):
    SourceProvenance, attach, _read = _task6_provenance_api()
    tmp_path = make_test_dir(f"task6-provenance-{provenance_name or 'legacy'}")
    legacy_success = "2026-07-12T07:30:00+00:00"
    current = datetime(2026, 7, 12, 8, 0, tzinfo=timezone.utc)
    playlist = _runtime_playlist(
        _runtime_plugin_data(latest_refresh_time=legacy_success)
    )
    task, _device_config, _clock = _make_runtime_task(tmp_path, playlists=[playlist])
    instance = playlist.plugins[0].snapshot()
    for lane in RefreshLane:
        task.runtime_state.record_success(
            instance.instance_uuid,
            legacy_success,
            lane=lane,
        )
    command = task._playlist_command(
        playlist.name,
        instance,
        source=CommandSource.BACKGROUND,
        intent=RefreshIntent.DATA_REFRESH,
        display_cached_only=False,
        kind=CommandKind.CACHE_REFRESH,
        current_dt=current,
    )
    resolved = task._resolve_playlist_command(command)
    image = Image.new("RGB", (32, 16), "white")
    if provenance_name == "RAW_VALID":
        image.info["inkypi_source_provenance"] = "stale_cache"
    elif provenance_name == "RAW_MALFORMED":
        image.info["inkypi_source_provenance"] = {"value": "stale_cache"}
    elif provenance_name is not None:
        attach(image, SourceProvenance[provenance_name], detail="task6_test")
    task._set_render_metadata(True, True, {})

    task._commit_command_result(command, resolved, image, current)

    state = task.runtime_state.snapshot().instances[instance.instance_uuid]
    assert Path(task._snapshot_cache_path(instance)).is_file()
    if degraded:
        assert state.data.last_success_at == legacy_success
        assert state.data.last_failure_at == current.isoformat()
        assert state.data.next_retry_at is not None
        retry_entries = task.retry_registry.snapshot()
        assert [(entry.key, entry.failure_count) for entry in retry_entries] == [
            (task._lane_retry_key(instance.instance_uuid, RefreshLane.DATA), 1)
        ]
    else:
        assert state.data.last_success_at == current.isoformat()
        assert state.data.last_failure_at is None
        assert state.data.next_retry_at is None
    assert state.live.last_success_at == legacy_success
    assert state.theme.last_success_at == legacy_success
    assert state.presentation.last_success_at == legacy_success


@pytest.mark.parametrize(
    ("intent", "lane"),
    [
        (RefreshIntent.LIVE_REFRESH, RefreshLane.LIVE),
        (RefreshIntent.THEME_REDRAW, RefreshLane.THEME),
        (RefreshIntent.PRESENTATION_REFRESH, RefreshLane.PRESENTATION),
    ],
)
def test_non_data_lanes_ignore_degraded_source_provenance(intent, lane):
    SourceProvenance, attach, _read = _task6_provenance_api()
    tmp_path = make_test_dir(f"task6-provenance-lane-{lane.value}")
    current = datetime(2026, 7, 12, 8, 0, tzinfo=timezone.utc)
    playlist = _runtime_playlist(_runtime_plugin_data())
    task, _device_config, _clock = _make_runtime_task(tmp_path, playlists=[playlist])
    instance = playlist.plugins[0].snapshot()
    command = task._playlist_command(
        playlist.name,
        instance,
        source=CommandSource.BACKGROUND,
        intent=intent,
        display_cached_only=False,
        kind=CommandKind.CACHE_REFRESH,
        current_dt=current,
        cache_theme_mode="night" if lane is RefreshLane.THEME else None,
    )
    resolved = task._resolve_playlist_command(command)
    image = attach(
        Image.new("RGB", (32, 16), "white"),
        SourceProvenance.STALE_CACHE,
        detail="task6_test",
    )
    task._set_render_metadata(True, True, {})

    task._commit_command_result(command, resolved, image, current)

    state = task.runtime_state.snapshot().instances[instance.instance_uuid]
    assert getattr(state, lane.value).last_success_at == current.isoformat()
    assert getattr(state, lane.value).last_failure_at is None


def test_degraded_data_worker_reports_failure_and_keeps_backoff_after_safe_image(
    monkeypatch,
):
    SourceProvenance, attach, _read = _task6_provenance_api()

    class DegradedPlugin(DelegatingThemeWrapper):
        config = {}

        def generate_image(self, settings, device_config):
            return attach(
                Image.new("RGB", (32, 16), "white"),
                SourceProvenance.STALE_CACHE,
                detail="task6_test",
            )

    tmp_path = make_test_dir("task6-degraded-worker-backoff")
    legacy_success = "2026-07-12T07:30:00+00:00"
    current = datetime(2026, 7, 12, 8, 0, tzinfo=timezone.utc)
    playlist = _runtime_playlist(
        _runtime_plugin_data(latest_refresh_time=legacy_success)
    )
    task, device_config, _clock = _make_runtime_task(tmp_path, playlists=[playlist])
    device_config.config.update({"theme_mode": "day", "active_theme": "day"})
    instance = playlist.plugins[0].snapshot()
    task.runtime_state.record_success(
        instance.instance_uuid,
        legacy_success,
        lane=RefreshLane.DATA,
    )
    _write_runtime_cache(task, instance)
    monkeypatch.setattr(
        refresh_task_module,
        "get_plugin_instance",
        lambda _config: DegradedPlugin(),
    )
    monkeypatch.setattr(task, "_get_current_datetime", lambda: current)
    task.start()
    try:
        assert task.wait_until_waiting(timeout=1.0)
        command = task._playlist_command(
            playlist.name,
            device_config.playlist_manager.snapshot_instance(
                instance.instance_uuid
            ),
            source=CommandSource.BACKGROUND,
            intent=RefreshIntent.DATA_REFRESH,
            force=False,
            display_cached_only=False,
            kind=CommandKind.CACHE_REFRESH,
        )
        submitted = task.refresh_queue.submit(command)
        result = task.wait_for_job(submitted.id, timeout=1.0)

        state = task.runtime_state.snapshot().instances[instance.instance_uuid]
        retry_entries = task.retry_registry.snapshot()
        assert result["status"] == "failed"
        assert result["error_code"] == "degraded_result"
        assert Path(task._snapshot_cache_path(instance)).is_file()
        assert state.data.last_success_at == legacy_success
        assert state.data.last_failure_at == current.isoformat()
        assert state.data.next_retry_at is not None
        assert [(entry.key, entry.failure_count) for entry in retry_entries] == [
            (task._lane_retry_key(instance.instance_uuid, RefreshLane.DATA), 1)
        ]
        assert task.scheduler_state.snapshot().last_error.endswith("stale_cache")
    finally:
        task.stop(join_timeout=1.0)


def _nasapics_manifest():
    return PluginManifest.from_path(
        PLUGIN_SOURCE_ROOT / "apod" / "plugin-info.json"
    )


def _nasapics_runtime(
    name,
    *,
    current_dt,
    latest_refresh_time,
    display_manager=None,
):
    tmp_path = make_test_dir(name)
    plugin_data = _runtime_plugin_data(
        "apod",
        "NASAPics",
        latest_refresh_time=latest_refresh_time,
        interval=1800,
    )
    plugin_data["plugin_settings"].update(
        {
            "customDate": "",
            "randomizeApod": False,
            "refreshOnDisplay": False,
        }
    )
    playlist = _runtime_playlist(plugin_data)
    clock = RuntimeClock()
    device_config = RuntimeDeviceConfig(tmp_path, [playlist])
    device_config.config.update(
        {
            "active_theme": "day",
            "theme_mode": "day",
            "plugin_cycle_interval_seconds": 60,
        }
    )
    manifest = _nasapics_manifest()
    device_config.get_plugin = lambda plugin_id: {
        "id": plugin_id,
        "refresh_on_display": manifest.refresh_on_display,
        "_manifest": manifest,
    }
    display_manager = display_manager or PresentationTransactionDisplayManager()
    task = RefreshTask(
        device_config,
        display_manager,
        clock=clock.monotonic,
        wall_clock=clock.wall_time,
        retry_registry=RetryRegistry(jitter=lambda delay: delay),
    )
    task._get_current_datetime = lambda: current_dt
    task._memory_watchdog_should_restart = lambda: False
    task._resource_sample = lambda: ResourceSample(
        available_mb=512,
        swap_percent=0,
    )
    return task, device_config, clock, playlist, display_manager


class _NASAPicsRuntimePlugin(DelegatingThemeWrapper):
    config = {}

    def __init__(self, calls, *, outcome="success"):
        self.calls = calls
        self.outcome = outcome

    def generate_image(self, settings, device_config):
        self.calls.append(dict(settings))
        if self.outcome == "failure":
            raise RuntimeError("mandatory NASAPics core unavailable")
        image = Image.new("RGB", (32, 16), "white")
        if self.outcome == "skip":
            image.info[refresh_task_module.SKIP_CACHE_IMAGE_INFO_KEY] = True
        return image


class _NASAPicsAuxiliaryTriggerPlugin:
    def __init__(self):
        self.live_calls = 0

    def get_live_refresh_state(self, settings, current_dt):
        self.live_calls += 1
        return {"active": True, "interval_seconds": 60}


def _nasapics_data_command(task, playlist, instance, current_dt):
    return task._playlist_command(
        playlist.name,
        instance,
        source=CommandSource.BACKGROUND,
        intent=RefreshIntent.DATA_REFRESH,
        force=False,
        display_cached_only=False,
        priority=10,
        kind=CommandKind.CACHE_REFRESH,
        current_dt=current_dt,
    )


def test_nasapics_1800_second_data_interval_has_exact_boundary():
    latest = datetime(2026, 7, 22, 12, 0, tzinfo=timezone.utc)
    task, _config, _clock, playlist, _display = _nasapics_runtime(
        "nasapics-exact-data-boundary",
        current_dt=latest,
        latest_refresh_time=latest.isoformat(),
    )
    instance = playlist.plugins[0].snapshot()

    assert task._snapshot_should_refresh(
        instance,
        latest + timedelta(seconds=1799.999),
    ) is False
    assert task._snapshot_should_refresh(
        instance,
        latest + timedelta(seconds=1800),
    ) is True


def test_nasapics_normal_rotation_runs_data_then_provider_free_display(
    monkeypatch,
):
    current = datetime(2026, 7, 22, 12, 0, tzinfo=timezone.utc)
    task, device_config, clock, playlist, display = _nasapics_runtime(
        "nasapics-data-then-display",
        current_dt=current,
        latest_refresh_time=(current - timedelta(seconds=1800)).isoformat(),
    )
    instance = playlist.plugins[0].snapshot()
    device_config.refresh_info.refresh_time = (
        current - timedelta(minutes=2)
    ).isoformat()
    calls = []
    monkeypatch.setattr(
        refresh_task_module,
        "get_plugin_instance",
        lambda _config: _NASAPicsRuntimePlugin(calls),
    )

    task._schedule_if_due()
    data_entry = task.refresh_queue.take(timeout=0)
    assert data_entry is not None
    assert data_entry.command.intent is RefreshIntent.DATA_REFRESH
    task._process_queue_entry(data_entry)

    assert len(calls) == 1
    assert display.calls == []
    state = task.runtime_state.snapshot().instances[instance.instance_uuid]
    assert state.data.last_success_at == current.isoformat()

    _install_display_provider_plugin_sentinels(monkeypatch)
    clock.advance(60)
    task._schedule_if_due()
    display_entry = task.refresh_queue.take(timeout=0)
    assert display_entry is not None
    assert display_entry.command.intent is RefreshIntent.DISPLAY_CACHE
    task._process_queue_entry(display_entry)

    assert (
        task.refresh_queue.get_entry(display_entry.job.id).job.status
        is JobStatus.SUCCEEDED
    )
    assert len(display.calls) == 1
    assert display.calls[0]["force_hardware_write"] is True


@pytest.mark.parametrize("outcome", ["failure", "skip"])
def test_nasapics_unhealthy_data_preserves_canonical_bytes_mtime_and_success(
    monkeypatch,
    outcome,
):
    current = datetime(2026, 7, 22, 12, 0, tzinfo=timezone.utc)
    prior_success = current - timedelta(hours=1)
    task, _config, _clock, playlist, display = _nasapics_runtime(
        f"nasapics-preserve-last-good-{outcome}",
        current_dt=current,
        latest_refresh_time=prior_success.isoformat(),
    )
    instance = playlist.plugins[0].snapshot()
    canonical = _write_runtime_cache(
        task,
        instance,
        Image.new("RGB", (32, 16), "black"),
    )
    task.runtime_state.record_success(
        instance.instance_uuid,
        prior_success.isoformat(),
        lane=RefreshLane.DATA,
        last_good_cache=LastGoodCacheState(
            theme_mode=None,
            structural_generation=instance.structural_generation,
            settings_revision=instance.settings_revision,
            promoted_at=prior_success.isoformat(),
        ),
    )
    before_bytes = canonical.read_bytes()
    before_hash = hashlib.sha256(before_bytes).hexdigest()
    before_mtime_ns = canonical.stat().st_mtime_ns
    monkeypatch.setattr(
        refresh_task_module,
        "get_plugin_instance",
        lambda _config: _NASAPicsRuntimePlugin([], outcome=outcome),
    )
    command = _nasapics_data_command(task, playlist, instance, current)

    result = _queue_and_process(task, command)

    after = task.runtime_state.snapshot().instances[instance.instance_uuid]
    assert result.job.status is JobStatus.FAILED
    if outcome == "skip":
        assert result.job.error_code == "degraded_result"
    assert canonical.read_bytes() == before_bytes
    assert hashlib.sha256(canonical.read_bytes()).hexdigest() == before_hash
    assert canonical.stat().st_mtime_ns == before_mtime_ns
    assert after.data.last_success_at == prior_success.isoformat()
    assert after.data.next_retry_at is not None
    assert display.calls == []
    assert task._select_independent_refresh_command(
        current + timedelta(seconds=1)
    ) is None


def test_nasapics_first_core_failure_publishes_no_startup_shell(
    monkeypatch,
):
    current = datetime(2026, 7, 22, 12, 0, tzinfo=timezone.utc)
    task, _config, _clock, playlist, display = _nasapics_runtime(
        "nasapics-first-core-failure",
        current_dt=current,
        latest_refresh_time=None,
    )
    instance = playlist.plugins[0].snapshot()
    monkeypatch.setattr(
        refresh_task_module,
        "get_plugin_instance",
        lambda _config: _NASAPicsRuntimePlugin([], outcome="failure"),
    )

    result = _queue_and_process(
        task,
        _nasapics_data_command(task, playlist, instance, current),
    )

    assert result.job.status is JobStatus.FAILED
    assert not Path(task._snapshot_cache_path(instance)).exists()
    assert not Path(task.compatibility_cache_path_for_snapshot(instance)).exists()
    assert display.calls == []


def test_nasapics_sunrise_transition_schedules_no_theme_live_or_presentation(
    monkeypatch,
):
    current = datetime(2026, 7, 22, 12, 0, tzinfo=timezone.utc)
    task, device_config, _clock, playlist, _display = _nasapics_runtime(
        "nasapics-no-auxiliary-lanes",
        current_dt=current,
        latest_refresh_time=current.isoformat(),
    )
    playlist.plugins[0].settings["themeMode"] = "auto"
    instance = playlist.plugins[0].snapshot()
    _write_runtime_theme_cache(task, instance, "day")
    task.runtime_state.record_success(
        instance.instance_uuid,
        current.isoformat(),
        lane=RefreshLane.DATA,
        last_good_cache=LastGoodCacheState(
            theme_mode="day",
            structural_generation=instance.structural_generation,
            settings_revision=instance.settings_revision,
            promoted_at=current.isoformat(),
        ),
    )
    task.runtime_state.set_display_state(
        "committed",
        "nasapics-current-display",
        instance_uuid=instance.instance_uuid,
        changed_at=current.isoformat(),
    )
    request = PresentationRequestState(
        request_id="a" * 32,
        requested_at=(current - timedelta(minutes=1)).isoformat(),
        structural_generation=instance.structural_generation,
        settings_revision=instance.settings_revision,
        origin_theme_mode="day",
        origin_display_commit_id="nasapics-current-display",
    )
    assert task.runtime_state.request_presentation(
        instance.instance_uuid,
        request,
    )
    monkeypatch.setattr(
        refresh_task_module,
        "get_theme_context",
        lambda _config, now: {
            "mode": "night",
            "source": "weather",
            "reason": "sunset",
        },
    )
    trigger_plugin = _NASAPicsAuxiliaryTriggerPlugin()
    monkeypatch.setattr(
        refresh_task_module,
        "get_plugin_instance",
        lambda _config: trigger_plugin,
    )

    task._schedule_if_due()

    assert task.refresh_queue.take(timeout=0) is None
    assert trigger_plugin.live_calls == 0
    assert task.refresh_health_snapshot()["due_counts"] == {
        "data": 0,
        "presentation": 0,
        "live": 0,
        "theme": 0,
    }
    state = task.runtime_state.snapshot().instances[instance.instance_uuid]
    assert state.presentation_request == request
    assert state.presentation.last_attempt_at is None
    assert state.live.last_attempt_at is None
    assert state.theme.last_attempt_at is None


@pytest.mark.parametrize(
    ("enabled_lane", "expected_intent"),
    [
        ("presentation", RefreshIntent.PRESENTATION_REFRESH),
        ("live", RefreshIntent.LIVE_REFRESH),
        ("theme", RefreshIntent.THEME_REDRAW),
    ],
)
def test_nasapics_auxiliary_lane_fixture_is_triggerable_when_capability_enabled(
    monkeypatch,
    enabled_lane,
    expected_intent,
):
    current = datetime(2026, 7, 22, 12, 0, tzinfo=timezone.utc)
    task, device_config, _clock, playlist, _display = _nasapics_runtime(
        f"nasapics-counterfactual-{enabled_lane}",
        current_dt=current,
        latest_refresh_time=current.isoformat(),
    )
    device_config.config["display_triggered_refresh_enabled"] = True
    playlist.plugins[0].settings["themeMode"] = "auto"
    instance = playlist.plugins[0].snapshot()
    if enabled_lane == "theme":
        _write_runtime_theme_cache(task, instance, "day")
        last_good = LastGoodCacheState(
            theme_mode="day",
            structural_generation=instance.structural_generation,
            settings_revision=instance.settings_revision,
            promoted_at=current.isoformat(),
        )
    else:
        _write_runtime_cache(task, instance)
        last_good = LastGoodCacheState(
            theme_mode=None,
            structural_generation=instance.structural_generation,
            settings_revision=instance.settings_revision,
            promoted_at=current.isoformat(),
        )
    data_success = (
        current - timedelta(minutes=10)
        if enabled_lane == "live"
        else current
    )
    task.runtime_state.record_success(
        instance.instance_uuid,
        data_success.isoformat(),
        lane=RefreshLane.DATA,
        last_good_cache=last_good,
    )
    task.runtime_state.set_display_state(
        "committed",
        f"nasapics-{enabled_lane}-display",
        instance_uuid=instance.instance_uuid,
        changed_at=current.isoformat(),
    )
    if enabled_lane == "presentation":
        assert task.runtime_state.request_presentation(
            instance.instance_uuid,
            PresentationRequestState(
                request_id="b" * 32,
                requested_at=(current - timedelta(minutes=1)).isoformat(),
                structural_generation=instance.structural_generation,
                settings_revision=instance.settings_revision,
                origin_theme_mode=None,
                origin_display_commit_id="nasapics-presentation-display",
            ),
        )

    if enabled_lane == "theme":
        manifest = _theme_manifest("apod")
    else:
        manifest = PluginManifest(
            schema_version=2,
            id="apod",
            class_name="Apod",
            display_name="NASA Astronomy Picture Of the Day",
            refresh_on_display=False,
            capabilities=PluginCapabilities(
                supports_presentation_refresh=enabled_lane == "presentation",
                supports_live_refresh=enabled_lane == "live",
                supports_day_night_theme=False,
            ),
            raw={},
        )
    device_config.get_plugin = lambda plugin_id: {
        "id": plugin_id,
        "refresh_on_display": manifest.refresh_on_display,
        "_manifest": manifest,
    }
    monkeypatch.setattr(
        refresh_task_module,
        "get_theme_context",
        lambda _config, now: {
            "mode": "night",
            "source": "weather",
            "reason": "sunset",
        },
    )
    trigger_plugin = _NASAPicsAuxiliaryTriggerPlugin()
    monkeypatch.setattr(
        refresh_task_module,
        "get_plugin_instance",
        lambda _config: trigger_plugin,
    )

    command = task._select_independent_refresh_command(current)

    assert command is not None
    assert command.instance_uuid == instance.instance_uuid
    assert command.intent is expected_intent
    assert trigger_plugin.live_calls == int(enabled_lane == "live")


def test_nasapics_display_now_is_provider_free_and_forces_one_hardware_write(
    monkeypatch,
):
    current = datetime(2026, 7, 22, 12, 0, tzinfo=timezone.utc)
    task, device_config, _clock, playlist, display = _nasapics_runtime(
        "nasapics-display-now-cache-only",
        current_dt=current,
        latest_refresh_time=current.isoformat(),
    )
    instance = playlist.plugins[0].snapshot()
    _write_runtime_cache(task, instance)
    _install_display_provider_plugin_sentinels(monkeypatch)
    task.running = True
    try:
        submitted = task.submit_playlist_display(
            instance.instance_uuid,
            force=False,
            display_cached_only=True,
            force_hardware_write=True,
            request_presentation_after_display=False,
        )
        entry = task.refresh_queue.take(timeout=0)
        assert entry is not None
        task._process_queue_entry(entry)
    finally:
        task.running = False

    result = task.refresh_queue.get_entry(submitted["id"])
    assert result.job.status is JobStatus.SUCCEEDED
    assert result.command.allow_prepared_presentation is False
    assert len(display.calls) == 1
    assert display.calls[0]["force_hardware_write"] is True


def _weather_margin_runtime(name, current_dt):
    weather_data = _runtime_plugin_data(
        "weather",
        "AwesomeWeather",
        interval=60,
    )
    weather_data["instance_uuid"] = "00000000000000000000000000000001"
    ordinary_data = _runtime_plugin_data(
        "ordinary",
        "Ordinary",
        interval=60,
    )
    ordinary_data["instance_uuid"] = "11111111111111111111111111111111"
    playlist = _runtime_playlist(weather_data, ordinary_data)
    clock = RuntimeClock(wall=current_dt.timestamp())
    task, device_config, _clock = _make_runtime_task(
        make_test_dir(name),
        playlists=[playlist],
        clock=clock,
    )
    device_config.config.update(
        {
            "theme_mode": "day",
            "active_theme": "day",
            "weather_liveness_window_seconds": 90,
            "weather_liveness_cooldown_seconds": 300,
        }
    )
    weather, ordinary = [instance.snapshot() for instance in playlist.plugins]
    for instance in (weather, ordinary):
        _write_runtime_cache(task, instance)
        task.runtime_state.record_success(
            instance.instance_uuid,
            (current_dt - timedelta(minutes=20)).isoformat(),
            lane=RefreshLane.DATA,
        )
    return task, clock, weather, ordinary


def test_weather_scheduler_requires_normal_150_mib_start_margin(monkeypatch):
    current_dt = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)
    task, _clock, weather, _ordinary = _weather_margin_runtime(
        "weather-normal-start-margin",
        current_dt,
    )
    maintenance = []
    monkeypatch.setattr(
        task,
        "_resource_sample",
        lambda: ResourceSample(available_mb=149, swap_percent=0),
    )
    monkeypatch.setattr(
        task,
        "_run_memory_maintenance",
        lambda reason, *, force=False: maintenance.append((reason, force)),
    )

    command = task._select_independent_refresh_command(current_dt)

    assert command is None
    assert task._weather_liveness_window is not None
    assert task._weather_liveness_window.instance_uuid == weather.instance_uuid
    assert maintenance == [("weather-liveness-window", True)]
    state = task.runtime_state.snapshot().instances[weather.instance_uuid].data
    assert state.next_retry_at == (current_dt + timedelta(seconds=60)).isoformat()


def test_weather_with_normal_margin_keeps_ordinary_data_ordering(monkeypatch):
    current_dt = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)
    task, _clock, _weather, ordinary = _weather_margin_runtime(
        "weather-normal-margin-preserves-ordering",
        current_dt,
    )
    task.runtime_state.record_success(
        ordinary.instance_uuid,
        (current_dt - timedelta(minutes=30)).isoformat(),
        lane=RefreshLane.DATA,
    )
    monkeypatch.setattr(
        task,
        "_resource_sample",
        lambda: ResourceSample(available_mb=512, swap_percent=0),
    )

    command = task._select_independent_refresh_command(current_dt)

    assert command is not None
    assert command.instance_uuid == ordinary.instance_uuid
    assert command.priority == 10


def test_weather_window_starts_immediately_when_normal_margin_recovers(
    monkeypatch,
):
    current_dt = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)
    task, clock, weather, _ordinary = _weather_margin_runtime(
        "weather-window-early-margin-recovery",
        current_dt,
    )
    sample = {"value": ResourceSample(available_mb=149, swap_percent=0)}
    monkeypatch.setattr(task, "_resource_sample", lambda: sample["value"])
    monkeypatch.setattr(task, "_run_memory_maintenance", lambda *_args, **_kwargs: None)

    assert task._select_independent_refresh_command(current_dt) is None
    state = task.runtime_state.snapshot().instances[weather.instance_uuid].data
    assert state.next_retry_at == (current_dt + timedelta(seconds=60)).isoformat()

    sample["value"] = ResourceSample(available_mb=150, swap_percent=0)
    clock.advance(1)
    recovered = task._select_independent_refresh_command(
        current_dt + timedelta(seconds=1)
    )

    assert recovered is not None
    assert recovered.instance_uuid == weather.instance_uuid
    assert "weather_liveness_concession" not in recovered.payload


def test_weather_typed_pressure_ends_window_and_yields_retry_turn_to_ordinary(
    monkeypatch,
    caplog,
):
    current_dt = datetime(2026, 8, 11, 20, 23, 23, tzinfo=timezone.utc)
    task, clock, weather, ordinary = _weather_margin_runtime(
        "weather-window-typed-pressure-handoff",
        current_dt,
    )
    sample = {"value": ResourceSample(available_mb=146, swap_percent=75.3)}
    executions = []
    monkeypatch.setattr(task, "_resource_sample", lambda: sample["value"])
    monkeypatch.setattr(task, "_run_memory_maintenance", lambda *_args, **_kwargs: None)

    def execute(command):
        executions.append(command.instance_uuid)
        if command.instance_uuid == weather.instance_uuid:
            raise ResourcePressureDeferred(
                reason="browser_resource_pressure",
                phase="render",
                available_mb=104.5,
                swap_percent=99.1,
            )

    monkeypatch.setattr(task, "_execute_command", execute)

    assert task._select_independent_refresh_command(current_dt) is None
    assert task._weather_liveness_window is not None

    sample["value"] = ResourceSample(available_mb=150, swap_percent=0)
    clock.advance(30)
    weather_command = task._select_independent_refresh_command(
        current_dt + timedelta(seconds=30)
    )
    assert weather_command is not None
    assert weather_command.instance_uuid == weather.instance_uuid
    assert "weather_liveness_concession" not in weather_command.payload

    with caplog.at_level("WARNING", logger=refresh_task_module.__name__):
        deferred = _queue_and_process(task, weather_command)

    assert deferred.job.status is JobStatus.CANCELED
    assert deferred.job.error_code == "resource_pressure_deferred"
    retry_state = task.runtime_state.snapshot().instances[weather.instance_uuid].data
    assert retry_state.next_retry_at == (
        current_dt + timedelta(seconds=30, minutes=5)
    ).isoformat()
    assert task._weather_liveness_window is None
    assert "reason: resource_pressure" in caplog.text

    sample["value"] = ResourceSample(available_mb=146, swap_percent=75.3)
    clock.advance(60)
    next_command = task._select_independent_refresh_command(
        current_dt + timedelta(seconds=90)
    )

    assert next_command is not None
    assert next_command.instance_uuid == ordinary.instance_uuid
    completed = _queue_and_process(task, next_command)
    assert completed.job.status is JobStatus.SUCCEEDED
    assert executions == [weather.instance_uuid, ordinary.instance_uuid]


def test_weather_window_concedes_once_at_deadline_with_140_mib_floor(monkeypatch):
    current_dt = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)
    task, clock, weather, _ordinary = _weather_margin_runtime(
        "weather-deadline-concession",
        current_dt,
    )
    monkeypatch.setattr(
        task,
        "_resource_sample",
        lambda: ResourceSample(available_mb=146, swap_percent=75.3),
    )
    monkeypatch.setattr(task, "_run_memory_maintenance", lambda *_args, **_kwargs: None)

    assert task._select_independent_refresh_command(current_dt) is None
    clock.advance(90)
    concession = task._select_independent_refresh_command(
        current_dt + timedelta(seconds=90)
    )

    assert concession is not None
    assert concession.instance_uuid == weather.instance_uuid
    assert concession.payload["weather_liveness_concession"] is True
    submitted = task._submit_independent_refresh_command(concession)
    queued = task.refresh_queue.get_entry(submitted.id)

    assert queued is not None
    assert queued.job.status is JobStatus.QUEUED
    assert queued.command.payload["weather_liveness_concession"] is True
    assert task._weather_liveness_window is None
    assert task._weather_liveness_cooldown_until_monotonic > clock.monotonic()


def test_weather_concession_submit_rejection_preserves_window_and_cooldown(
    monkeypatch,
):
    current_dt = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)
    task, clock, weather, _ordinary = _weather_margin_runtime(
        "weather-concession-submit-rejected",
        current_dt,
    )
    monkeypatch.setattr(
        task,
        "_resource_sample",
        lambda: ResourceSample(available_mb=146, swap_percent=75.3),
    )
    monkeypatch.setattr(task, "_run_memory_maintenance", lambda *_a, **_k: None)

    assert task._select_independent_refresh_command(current_dt) is None
    original_window = task._weather_liveness_window
    assert original_window is not None
    clock.advance(90)

    monkeypatch.setattr(task, "_memory_watchdog_should_restart", lambda: False)
    monkeypatch.setattr(task, "_sample_disk_pressure", lambda: DiskPressureTier.HEALTHY)
    monkeypatch.setattr(task, "_select_prepared_display_retry_command", lambda _dt: None)
    monkeypatch.setattr(task, "_select_cached_display_command", lambda _dt: None)
    monkeypatch.setattr(task, "_run_cache_lifecycle_maintenance", lambda _tier: None)
    monkeypatch.setattr(task, "_cache_lifecycle_should_yield", lambda: False)
    monkeypatch.setattr(
        task,
        "_get_current_datetime",
        lambda: current_dt + timedelta(seconds=90),
    )
    rejected = []

    def reject_submission(command):
        rejected.append(command)
        raise QueueFullError("queue is full")

    monkeypatch.setattr(task.refresh_queue, "submit", reject_submission)

    assert task._run_one_iteration_for_test() is None

    assert len(rejected) == 1
    assert rejected[0].instance_uuid == weather.instance_uuid
    assert rejected[0].payload["weather_liveness_concession"] is True
    assert task._weather_liveness_window == original_window
    assert task._weather_liveness_cooldown_until_monotonic == 0
    assert task._burst_liveness_yield_ordinary_pending is False


def test_non_concession_independent_submit_keeps_submit_only_queue_contract():
    current_dt = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)
    task, _clock, _weather, ordinary = _weather_margin_runtime(
        "ordinary-submit-only-queue-contract",
        current_dt,
    )
    command = task._playlist_command(
        "DailyDoseOfDay",
        ordinary,
        source=CommandSource.BACKGROUND,
        intent=RefreshIntent.DATA_REFRESH,
        force=False,
        display_cached_only=False,
        kind=CommandKind.CACHE_REFRESH,
        current_dt=current_dt,
    )
    submitted = object()

    class SubmitOnlyQueue:
        def __init__(self):
            self.command = None

        def submit(self, candidate):
            self.command = candidate
            return submitted

    queue = SubmitOnlyQueue()
    task.refresh_queue = queue

    assert task._submit_independent_refresh_command(command) is submitted
    assert queue.command is command


def test_weather_below_140_mib_never_opens_window_or_holds_ordinary(monkeypatch):
    current_dt = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)
    task, clock, _weather, ordinary = _weather_margin_runtime(
        "weather-below-concession-floor",
        current_dt,
    )
    sample = {"value": ResourceSample(available_mb=100, swap_percent=80)}
    monkeypatch.setattr(task, "_resource_sample", lambda: sample["value"])

    assert task._select_independent_refresh_command(current_dt) is None
    assert task._weather_liveness_window is None

    sample["value"] = ResourceSample(available_mb=146, swap_percent=75.3)
    clock.advance(1)
    ordinary_command = task._select_independent_refresh_command(
        current_dt + timedelta(seconds=1)
    )

    assert ordinary_command is not None
    assert ordinary_command.instance_uuid == ordinary.instance_uuid


@pytest.mark.parametrize(
    ("available_mb", "swap_percent", "expected"),
    [
        (150, 69.9, True),
        (149.9, 0, False),
        (200, 70, False),
        (float("nan"), 0, False),
        (200, float("nan"), False),
    ],
)
def test_weather_normal_start_margin_is_strict_and_finite(
    available_mb,
    swap_percent,
    expected,
):
    current_dt = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)
    task, _clock, _weather, _ordinary = _weather_margin_runtime(
        f"weather-normal-margin-{available_mb}-{swap_percent}",
        current_dt,
    )

    available, required_mb, max_swap = task._weather_background_start_margin(
        ResourceSample(available_mb=available_mb, swap_percent=swap_percent)
    )

    assert available is expected
    assert required_mb == 150
    assert max_swap == 70


@pytest.mark.parametrize(
    ("available_mb", "expected"),
    [
        (140, True),
        (139.9, False),
        (float("nan"), False),
    ],
)
def test_weather_concession_keeps_the_measured_safe_memory_floor(
    available_mb,
    expected,
):
    current_dt = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)
    task, _clock, _weather, _ordinary = _weather_margin_runtime(
        f"weather-concession-margin-{available_mb}",
        current_dt,
    )

    available, required_mb = task._weather_concession_margin(
        ResourceSample(available_mb=available_mb, swap_percent=99)
    )

    assert available is expected
    assert required_mb == 140


def test_weather_concession_execution_race_stops_before_plugin_start(monkeypatch):
    current_dt = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)
    task, clock, weather, _ordinary = _weather_margin_runtime(
        "weather-concession-execution-race",
        current_dt,
    )
    sample = {"value": ResourceSample(available_mb=146, swap_percent=75.3)}
    monkeypatch.setattr(task, "_resource_sample", lambda: sample["value"])
    monkeypatch.setattr(task, "_run_memory_maintenance", lambda *_args, **_kwargs: None)
    plugin_starts = []
    monkeypatch.setattr(
        "src.refresh_task.get_plugin_instance",
        lambda config: plugin_starts.append(config),
    )

    assert task._select_independent_refresh_command(current_dt) is None
    clock.advance(90)
    concession = task._select_independent_refresh_command(
        current_dt + timedelta(seconds=90)
    )
    assert concession.payload["weather_liveness_concession"] is True

    sample["value"] = ResourceSample(available_mb=114.9, swap_percent=0)
    result = _queue_and_process(task, concession)

    assert result.job.status is JobStatus.CANCELED
    assert result.job.error_code == "weather_browser_start_margin"
    assert plugin_starts == []
    state = task.runtime_state.snapshot().instances[weather.instance_uuid].data
    assert state.next_retry_at == (current_dt + timedelta(seconds=150)).isoformat()
    assert state.last_failure_at is None


def test_weather_normal_execution_rechecks_150_mib_margin(monkeypatch):
    current_dt = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)
    task, clock, weather, _ordinary = _weather_margin_runtime(
        "weather-normal-execution-race",
        current_dt,
    )
    plugin_starts = []
    monkeypatch.setattr(
        task,
        "_resource_sample",
        lambda: ResourceSample(available_mb=149, swap_percent=0),
    )
    monkeypatch.setattr(
        "src.refresh_task.get_plugin_instance",
        lambda config: plugin_starts.append(config),
    )
    command = task._playlist_command(
        "DailyDoseOfDay",
        weather,
        source=CommandSource.BACKGROUND,
        intent=RefreshIntent.DATA_REFRESH,
        display_cached_only=False,
        kind=CommandKind.CACHE_REFRESH,
        current_dt=current_dt,
    )

    result = _queue_and_process(task, command)

    assert result.job.status is JobStatus.CANCELED
    assert result.job.error_code == "weather_browser_start_margin"
    assert plugin_starts == []
    state = task.runtime_state.snapshot().instances[weather.instance_uuid].data
    assert state.next_retry_at == (current_dt + timedelta(seconds=60)).isoformat()


def test_weather_concession_yields_next_soft_pressure_turn_to_ordinary_data(
    monkeypatch,
):
    current_dt = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)
    task, clock, weather, ordinary = _weather_margin_runtime(
        "weather-concession-ordinary-yield",
        current_dt,
    )
    monkeypatch.setattr(
        task,
        "_resource_sample",
        lambda: ResourceSample(available_mb=146, swap_percent=75.3),
    )
    monkeypatch.setattr(task, "_run_memory_maintenance", lambda *_args, **_kwargs: None)

    assert task._select_independent_refresh_command(current_dt) is None
    clock.advance(90)
    concession = task._select_independent_refresh_command(
        current_dt + timedelta(seconds=90)
    )
    assert concession.instance_uuid == weather.instance_uuid
    assert concession.payload["weather_liveness_concession"] is True
    submitted = task._submit_independent_refresh_command(concession)
    queued = task.refresh_queue.take(timeout=0)
    assert queued is not None
    assert queued.job.id == submitted.id
    task.refresh_queue.finish(queued.job.id, JobStatus.SUCCEEDED)

    clock.advance(1)
    ordinary_command = task._select_independent_refresh_command(
        current_dt + timedelta(seconds=91)
    )

    assert ordinary_command is not None
    assert ordinary_command.instance_uuid == ordinary.instance_uuid
    assert "weather_liveness_concession" not in ordinary_command.payload
    assert task._burst_liveness_yield_ordinary_pending is False
    assert task._weather_liveness_window is None


def test_weather_concession_long_execution_preserves_one_shot_ordinary_handoff(
    monkeypatch,
):
    current_dt = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)
    task, clock, weather, ordinary = _weather_margin_runtime(
        "weather-concession-long-execution-handoff",
        current_dt,
    )
    monkeypatch.setattr(
        task,
        "_resource_sample",
        lambda: ResourceSample(available_mb=146, swap_percent=75.3),
    )
    monkeypatch.setattr(task, "_run_memory_maintenance", lambda *_args, **_kwargs: None)

    assert task._select_independent_refresh_command(current_dt) is None
    clock.advance(90)
    concession = task._select_independent_refresh_command(
        current_dt + timedelta(seconds=90)
    )
    assert concession.instance_uuid == weather.instance_uuid
    assert concession.payload["weather_liveness_concession"] is True

    submitted = task._submit_independent_refresh_command(concession)
    queued = task.refresh_queue.take(timeout=0)
    assert queued is not None
    assert queued.job.id == submitted.id

    # The single worker can spend longer than the old 30-second handoff
    # deadline inside Weather/Chromium before another scheduler turn exists.
    clock.advance(31)
    task.refresh_queue.finish(queued.job.id, JobStatus.SUCCEEDED)

    scheduler_dt = {"value": current_dt + timedelta(seconds=121)}
    monkeypatch.setattr(task, "_get_current_datetime", lambda: scheduler_dt["value"])
    monkeypatch.setattr(task, "_memory_watchdog_should_restart", lambda: False)
    monkeypatch.setattr(
        task,
        "_sample_disk_pressure",
        lambda: DiskPressureTier.HEALTHY,
    )
    monkeypatch.setattr(task, "_select_prepared_display_retry_command", lambda _dt: None)
    monkeypatch.setattr(task, "_select_cached_display_command", lambda _dt: None)
    monkeypatch.setattr(task, "_run_cache_lifecycle_maintenance", lambda _tier: None)
    monkeypatch.setattr(task, "_execute_command", lambda _command: None)
    task.device_config.config["plugin_cycle_interval_seconds"] = 1

    ordinary_turn = task._run_one_iteration_for_test()

    assert ordinary_turn is not None
    assert ordinary_turn.command.instance_uuid == ordinary.instance_uuid
    assert task._burst_liveness_yield_ordinary_pending is False

    clock.advance(1)
    scheduler_dt["value"] += timedelta(seconds=1)
    repeated_bypass = task._run_one_iteration_for_test()

    assert repeated_bypass is None


def test_weather_handoff_without_ordinary_expires_before_specialized_liveness(
    monkeypatch,
):
    current_dt = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)
    sports_data = _runtime_plugin_data(
        "sports_dashboard",
        "Sports",
        interval=60,
    )
    sports_data["instance_uuid"] = "11111111111111111111111111111111"
    weather_data = _runtime_plugin_data(
        "weather",
        "Weather",
        interval=60,
    )
    weather_data["instance_uuid"] = "22222222222222222222222222222222"
    playlist = _runtime_playlist(sports_data, weather_data)
    clock = RuntimeClock(wall=current_dt.timestamp())
    task, device_config, _clock = _make_runtime_task(
        make_test_dir("weather-handoff-specialized-release"),
        playlists=[playlist],
        clock=clock,
    )
    device_config.config.update(
        {
            "theme_mode": "day",
            "active_theme": "day",
            "weather_liveness_window_seconds": 90,
            "weather_liveness_cooldown_seconds": 300,
            "burst_liveness_ordinary_yield_seconds": 30,
            "sports_isolated_liveness_starvation_seconds": 60 * 60,
            "sports_isolated_liveness_window_seconds": 60,
        }
    )
    sports, weather = [instance.snapshot() for instance in playlist.plugins]
    for instance in (sports, weather):
        _write_runtime_cache(task, instance)
        task.runtime_state.record_success(
            instance.instance_uuid,
            (current_dt - timedelta(minutes=20)).isoformat(),
            lane=RefreshLane.DATA,
        )
    monkeypatch.setattr(
        task,
        "_resource_sample",
        lambda: ResourceSample(available_mb=146, swap_percent=75.3),
    )
    monkeypatch.setattr(
        "src.refresh_task.get_plugin_instance",
        lambda _config: FakePlugin([]),
    )
    monkeypatch.setattr(task, "_run_memory_maintenance", lambda *_args, **_kwargs: None)

    assert task._select_independent_refresh_command(current_dt) is None
    assert task._weather_liveness_window is not None
    clock.advance(90)
    concession = task._select_independent_refresh_command(
        current_dt + timedelta(seconds=90)
    )
    assert concession.instance_uuid == weather.instance_uuid
    submitted = task._submit_independent_refresh_command(concession)
    queued = task.refresh_queue.take(timeout=0)
    assert queued is not None
    assert queued.job.id == submitted.id
    clock.advance(31)
    task.refresh_queue.finish(queued.job.id, JobStatus.SUCCEEDED)

    device_config.config["sports_isolated_liveness_starvation_seconds"] = 0
    assert (
        task._select_independent_refresh_command(
            current_dt + timedelta(seconds=121)
        )
        is None
    )
    assert task._sports_liveness_window is None

    clock.advance(31)
    assert (
        task._select_independent_refresh_command(
            current_dt + timedelta(seconds=152)
        )
        is None
    )
    assert task._burst_liveness_yield_ordinary_pending is False
    assert task._sports_liveness_window is not None
    assert task._sports_liveness_window.instance_uuid == sports.instance_uuid


def test_weather_quiet_window_does_not_block_cached_display(monkeypatch):
    current_dt = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)
    task, _clock, weather, _ordinary = _weather_margin_runtime(
        "weather-window-cached-display",
        current_dt,
    )
    monkeypatch.setattr(
        task,
        "_resource_sample",
        lambda: ResourceSample(available_mb=146, swap_percent=75.3),
    )
    monkeypatch.setattr(task, "_run_memory_maintenance", lambda *_args, **_kwargs: None)
    executions = []
    monkeypatch.setattr(
        task,
        "_execute_command",
        lambda command: executions.append(command.id),
    )

    assert task._select_independent_refresh_command(current_dt) is None
    assert task._weather_liveness_window is not None
    cached_display = task._playlist_command(
        "DailyDoseOfDay",
        weather,
        source=CommandSource.SCHEDULER,
        intent=RefreshIntent.DISPLAY_CACHE,
        display_cached_only=True,
        kind=CommandKind.DISPLAY,
        current_dt=current_dt,
    )

    result = _queue_and_process(task, cached_display)

    assert result.job.status is JobStatus.SUCCEEDED
    assert executions == [cached_display.id]
    assert task._weather_liveness_window is not None


def test_rotation_deadline_guard_blocks_weather_concession(monkeypatch, caplog):
    current_dt = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)
    task, clock, _weather, _ordinary = _weather_margin_runtime(
        "weather-concession-rotation-deadline-guard",
        current_dt,
    )
    monkeypatch.setattr(
        task,
        "_resource_sample",
        lambda: ResourceSample(available_mb=146, swap_percent=75.3),
    )
    monkeypatch.setattr(task, "_run_memory_maintenance", lambda *_args, **_kwargs: None)

    assert task._select_independent_refresh_command(current_dt) is None
    task._rotation_deadline_guard_active = True
    monkeypatch.setattr(task, "_get_rotation_wait_seconds", lambda: 60)
    clock.advance(90)

    assert (
        task._select_independent_refresh_command(
            current_dt + timedelta(seconds=90)
        )
        is None
    )
    clock.advance(1)
    assert (
        task._select_independent_refresh_command(
            current_dt + timedelta(seconds=91)
        )
        is None
    )
    assert task._weather_liveness_window is not None
    assert task._weather_liveness_cooldown_until_monotonic == 0
    assert task._burst_liveness_yield_ordinary_pending is False
    assert not [
        record
        for record in caplog.records
        if "Weather quiet window reached its bounded concession" in record.message
    ]

    task._rotation_deadline_guard_active = False
    monkeypatch.setattr(task, "_get_rotation_wait_seconds", lambda: 600)
    concession = task._select_independent_refresh_command(
        current_dt + timedelta(seconds=91)
    )
    assert concession is not None
    task._submit_independent_refresh_command(concession)

    assert len(
        [
            record
            for record in caplog.records
            if "Weather quiet window reached its bounded concession" in record.message
        ]
    ) == 1


@pytest.mark.parametrize("window_owner", ["sports", "ticketmaster", "weather"])
def test_burst_liveness_windows_are_mutually_exclusive(monkeypatch, window_owner):
    current_dt = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)
    plugin_data = []
    if window_owner == "sports":
        plugin_data.append(
            _runtime_plugin_data("sports_dashboard", "Sports", interval=60)
        )
    elif window_owner == "ticketmaster":
        plugin_data.append(
            _runtime_plugin_data("ticketmaster_events", "Tickets", interval=60)
        )
    else:
        plugin_data.extend(
            [
                _runtime_plugin_data("sports_dashboard", "Sports", interval=60),
                _runtime_plugin_data("ticketmaster_events", "Tickets", interval=60),
            ]
        )
    plugin_data.append(_runtime_plugin_data("weather", "Weather", interval=60))
    for index, data in enumerate(plugin_data, start=1):
        data["instance_uuid"] = f"{index:032x}"
    playlist = _runtime_playlist(*plugin_data)
    clock = RuntimeClock(wall=current_dt.timestamp())
    task, device_config, _clock = _make_runtime_task(
        make_test_dir(f"burst-window-exclusive-{window_owner}"),
        playlists=[playlist],
        clock=clock,
    )
    device_config.config.update(
        {
            "theme_mode": "day",
            "active_theme": "day",
            "sports_isolated_liveness_starvation_seconds": (
                0 if window_owner == "sports" else 60 * 60
            ),
            "ticketmaster_liveness_starvation_seconds": (
                0 if window_owner == "ticketmaster" else 60 * 60
            ),
            "weather_liveness_window_seconds": 90,
        }
    )
    for instance in playlist.plugins:
        snapshot = instance.snapshot()
        _write_runtime_cache(task, snapshot)
        task.runtime_state.record_success(
            snapshot.instance_uuid,
            (current_dt - timedelta(minutes=20)).isoformat(),
            lane=RefreshLane.DATA,
        )
    monkeypatch.setattr(
        task,
        "_resource_sample",
        lambda: ResourceSample(available_mb=146, swap_percent=75.3),
    )
    monkeypatch.setattr(task, "_run_memory_maintenance", lambda *_args, **_kwargs: None)

    assert task._select_independent_refresh_command(current_dt) is None
    windows = {
        "sports": task._sports_liveness_window,
        "ticketmaster": task._ticketmaster_liveness_window,
        "weather": task._weather_liveness_window,
    }

    assert windows[window_owner] is not None
    assert sum(window is not None for window in windows.values()) == 1
    if window_owner == "weather":
        device_config.config.update(
            {
                "sports_isolated_liveness_starvation_seconds": 0,
                "ticketmaster_liveness_starvation_seconds": 0,
            }
        )
        clock.advance(1)
        assert (
            task._select_independent_refresh_command(
                current_dt + timedelta(seconds=1)
            )
            is None
        )
        assert task._sports_liveness_window is None
        assert task._ticketmaster_liveness_window is None
