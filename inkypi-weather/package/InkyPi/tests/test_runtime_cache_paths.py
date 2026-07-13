from pathlib import Path

import pytest

from plugins import context_cache
from plugins.ai_image_multiverse import ai_image_multiverse
from plugins.backtothedate.backtothedate import BacktotheDate
from plugins.bambu_monitor.bambu_monitor import BambuMonitor
from plugins.base_plugin.base_plugin import BasePlugin
from plugins.chinese_literature_clock.chinese_literature_clock import (
    ChineseLiteratureClock,
)
from plugins.comic import comic_parser
from plugins.epaper_pet.epaper_pet import EpaperPet
from plugins.flight_radar.flight_radar import FlightRadar
from plugins.literature_clock import literature_clock
from plugins.literature_clock.literature_clock import LiteratureClock
from plugins.newspaper.newspaper import Newspaper
from plugins.pixiv_r18_ranking.pixiv_r18_ranking import PixivR18Ranking
from plugins.reddit_rule34_hot.reddit_rule34_hot import RedditRule34Hot
from plugins.sports_dashboard.sports_dashboard import SportsDashboard
from plugins.stocktracker import stocktracker
from plugins.stocktracker.stocktracker import StockTracker
from plugins.telegram_digest.telegram_digest import TelegramDigest
from plugins.weather.weather import Weather


def _base_plugin(plugin_id: str = "demo") -> BasePlugin:
    plugin = BasePlugin.__new__(BasePlugin)
    plugin.config = {"id": plugin_id}
    return plugin


def _plugin(plugin_class, plugin_id: str):
    plugin = plugin_class.__new__(plugin_class)
    plugin.config = {"id": plugin_id}
    return plugin


def test_base_plugin_cache_uses_global_runtime_root(monkeypatch, tmp_path):
    root = tmp_path / "runtime-cache"
    monkeypatch.setenv("INKYPI_CACHE_DIR", str(root))

    cache_dir = _base_plugin("daily_ai_news").cache_dir(leaf="cache")

    assert cache_dir == root / "plugins" / "daily_ai_news" / "cache"
    assert cache_dir.is_dir()


def test_relative_plugin_cache_override_stays_under_runtime_root(monkeypatch, tmp_path):
    root = tmp_path / "runtime-cache"
    monkeypatch.setenv("INKYPI_CACHE_DIR", str(root))
    monkeypatch.setenv("DEMO_CACHE_DIR", "custom")

    cache_dir = _base_plugin().cache_dir(env_var="DEMO_CACHE_DIR", leaf="cache")

    assert cache_dir == root / "plugins" / "demo" / "custom"
    assert cache_dir.is_dir()


def test_absolute_plugin_cache_override_keeps_precedence(monkeypatch, tmp_path):
    root = tmp_path / "runtime-cache"
    override = tmp_path / "explicit-cache"
    monkeypatch.setenv("INKYPI_CACHE_DIR", str(root))
    monkeypatch.setenv("DEMO_CACHE_DIR", str(override))

    cache_dir = _base_plugin().cache_dir(env_var="DEMO_CACHE_DIR", leaf="cache")

    assert cache_dir == override
    assert cache_dir.is_dir()


def test_base_plugin_data_uses_global_runtime_root(monkeypatch, tmp_path):
    root = tmp_path / "runtime-data"
    monkeypatch.setenv("INKYPI_DATA_DIR", str(root))

    data_dir = _base_plugin("epaper_pet").data_dir(leaf="pets")

    assert data_dir == root / "plugins" / "epaper_pet" / "pets"
    assert data_dir.is_dir()


def test_ai_multiverse_presets_use_runtime_data(monkeypatch, tmp_path):
    root = tmp_path / "runtime-data"
    monkeypatch.setenv("INKYPI_DATA_DIR", str(root))
    monkeypatch.delenv("INKYPI_AI_MULTIVERSE_PRESETS_FILE", raising=False)

    presets_file = ai_image_multiverse._presets_file()

    assert presets_file == (
        root
        / "plugins"
        / "ai_image_multiverse"
        / "presets.json"
    )


def test_epaper_pet_persistent_state_uses_runtime_data(monkeypatch, tmp_path):
    root = tmp_path / "runtime-data"
    monkeypatch.setenv("INKYPI_DATA_DIR", str(root))
    plugin = EpaperPet.__new__(EpaperPet)
    plugin.config = {"id": "epaper_pet"}

    state_file = plugin._state_file({"pet_id": "codex"})
    journal_file = plugin._journal_file({"pet_id": "codex"})

    assert state_file == root / "plugins" / "epaper_pet" / "pets" / "codex.json"
    assert journal_file == root / "plugins" / "epaper_pet" / "pets" / "codex.journal.md"


def test_context_cache_uses_global_runtime_root(monkeypatch, tmp_path):
    root = tmp_path / "runtime-cache"
    monkeypatch.setenv("INKYPI_CACHE_DIR", str(root))
    monkeypatch.delenv("INKYPI_CONTEXT_CACHE_DIR", raising=False)

    cache_dir = context_cache._cache_dir()

    assert cache_dir == root / "context"
    assert cache_dir.is_dir()


def test_weather_cache_uses_global_runtime_root(monkeypatch, tmp_path):
    root = tmp_path / "runtime-cache"
    monkeypatch.setenv("INKYPI_CACHE_DIR", str(root))
    monkeypatch.delenv("OPENWEATHER_CACHE_DIR", raising=False)

    cache_dir = Path(Weather.__new__(Weather)._openweather_cache_dir())

    assert cache_dir == root / "weather"
    assert cache_dir.is_dir()


def test_chinese_literature_enrichment_cache_uses_runtime_root(monkeypatch, tmp_path):
    root = tmp_path / "runtime-cache"
    monkeypatch.setenv("INKYPI_CACHE_DIR", str(root))
    monkeypatch.delenv("INKYPI_CHINESE_LITCLOCK_OPEN_LIBRARY_CACHE", raising=False)
    plugin = ChineseLiteratureClock.__new__(ChineseLiteratureClock)
    plugin.config = {"id": "chinese_literature_clock"}

    cache_dir = plugin._open_library_cache_dir()

    assert cache_dir == (
        root
        / "plugins"
        / "chinese_literature_clock"
        / ".open_library_cache"
    )
    assert cache_dir.is_dir()


def test_bambu_status_and_camera_cache_use_runtime_root(monkeypatch, tmp_path):
    root = tmp_path / "runtime-cache"
    monkeypatch.setenv("INKYPI_CACHE_DIR", str(root))
    plugin = BambuMonitor.__new__(BambuMonitor)
    plugin.config = {"id": "bambu_monitor"}

    status_path = Path(plugin._cache_file("printer.local", "serial"))
    camera_path = Path(plugin._camera_file("printer.local", "serial"))

    expected = root / "plugins" / "bambu_monitor" / "cache"
    assert status_path.parent == expected
    assert camera_path.parent == expected


def test_flight_radar_all_cache_files_use_runtime_root(monkeypatch, tmp_path):
    root = tmp_path / "runtime-cache"
    monkeypatch.setenv("INKYPI_CACHE_DIR", str(root))
    expected = root / "plugins" / "flight_radar"

    assert FlightRadar._map_cache_file("https://example.test/map").parent == expected
    assert FlightRadar._route_cache_file().parent == expected
    assert FlightRadar._track_history_file().parent == expected
    assert FlightRadar._cache_file("snapshot").parent == expected


def test_comic_cache_uses_global_runtime_root(monkeypatch, tmp_path):
    root = tmp_path / "runtime-cache"
    monkeypatch.setenv("INKYPI_CACHE_DIR", str(root))

    cache_path = Path(comic_parser._panel_cache_path("XKCD"))

    assert cache_path.parent == root / "comic"
    assert cache_path.parent.is_dir()


def test_literature_clock_downloads_to_cache_and_seeds_bundled_fallback(
    monkeypatch, tmp_path
):
    cache_root = tmp_path / "runtime-cache"
    bundled_root = tmp_path / "bundled"
    bundled_file = bundled_root / "data" / "litclock_annotated.csv"
    bundled_file.parent.mkdir(parents=True)
    bundled_file.write_text("bundled", encoding="utf-8")
    monkeypatch.setenv("INKYPI_CACHE_DIR", str(cache_root))

    plugin = LiteratureClock.__new__(LiteratureClock)
    plugin.config = {"id": "literature_clock"}
    plugin.get_plugin_dir = lambda leaf=None: str(bundled_root / leaf) if leaf else str(bundled_root)
    monkeypatch.setattr(
        literature_clock,
        "ensure_dataset",
        lambda _path: (_ for _ in ()).throw(FileNotFoundError("offline")),
    )

    dataset_path = plugin._resolve_dataset_path()

    expected = (
        cache_root
        / "plugins"
        / "literature_clock"
        / "datasets"
        / "litclock_annotated.csv"
    )
    assert dataset_path == expected
    assert expected.read_text(encoding="utf-8") == "bundled"


def test_backtothedate_bank_uses_data_while_legacy_cache_stays_in_cache(
    monkeypatch, tmp_path
):
    data_root = tmp_path / "runtime-data"
    cache_root = tmp_path / "runtime-cache"
    monkeypatch.setenv("INKYPI_DATA_DIR", str(data_root))
    monkeypatch.setenv("INKYPI_CACHE_DIR", str(cache_root))
    plugin = _plugin(BacktotheDate, "backtothedate")

    state_path = plugin._state_path()
    media_dir = plugin._presentation_media_dir()
    legacy_state_path = plugin._legacy_state_path()
    legacy_output_dir = plugin.cache_dir(leaf="output")

    assert state_path == (
        data_root / "plugins" / "backtothedate" / ".backtothedate_state.json"
    )
    assert media_dir == data_root / "plugins" / "backtothedate" / "presentation-media"
    assert legacy_state_path == (
        cache_root / "plugins" / "backtothedate" / ".backtothedate_state.json"
    )
    assert legacy_output_dir == cache_root / "plugins" / "backtothedate" / "output"


def test_newspaper_rotation_state_uses_global_runtime_cache_root(monkeypatch, tmp_path):
    root = tmp_path / "runtime-cache"
    monkeypatch.setenv("INKYPI_CACHE_DIR", str(root))

    state_path = _plugin(Newspaper, "newspaper")._rotation_state_path()

    assert state_path == root / "plugins" / "newspaper" / ".newspaper_rotation_state.json"


@pytest.mark.parametrize(
    ("plugin_class", "plugin_id", "override_name", "leaf"),
    [
        (
            PixivR18Ranking,
            "pixiv_r18_ranking",
            "INKYPI_PIXIV_R18_CACHE",
            ".pixiv_r18_ranking_cache",
        ),
        (
            RedditRule34Hot,
            "reddit_rule34_hot",
            "INKYPI_REDDIT_RULE34_CACHE",
            ".reddit_rule34_hot_cache",
        ),
    ],
)
def test_ranking_plugin_cache_uses_global_runtime_root(
    monkeypatch,
    tmp_path,
    plugin_class,
    plugin_id,
    override_name,
    leaf,
):
    root = tmp_path / "runtime-cache"
    monkeypatch.setenv("INKYPI_CACHE_DIR", str(root))
    monkeypatch.delenv(override_name, raising=False)

    cache_dir = _plugin(plugin_class, plugin_id)._cache_dir()

    assert cache_dir == root / "plugins" / plugin_id / leaf


@pytest.mark.parametrize(
    ("plugin_class", "plugin_id", "override_name"),
    [
        (PixivR18Ranking, "pixiv_r18_ranking", "INKYPI_PIXIV_R18_CACHE"),
        (RedditRule34Hot, "reddit_rule34_hot", "INKYPI_REDDIT_RULE34_CACHE"),
    ],
)
def test_relative_ranking_plugin_override_stays_under_global_runtime_root(
    monkeypatch,
    tmp_path,
    plugin_class,
    plugin_id,
    override_name,
):
    root = tmp_path / "runtime-cache"
    monkeypatch.setenv("INKYPI_CACHE_DIR", str(root))
    monkeypatch.setenv(override_name, "custom")

    cache_dir = _plugin(plugin_class, plugin_id)._cache_dir()

    assert cache_dir == root / "plugins" / plugin_id / "custom"


@pytest.mark.parametrize(
    ("plugin_class", "plugin_id", "override_name"),
    [
        (PixivR18Ranking, "pixiv_r18_ranking", "INKYPI_PIXIV_R18_CACHE"),
        (RedditRule34Hot, "reddit_rule34_hot", "INKYPI_REDDIT_RULE34_CACHE"),
    ],
)
def test_absolute_ranking_plugin_override_keeps_precedence(
    monkeypatch,
    tmp_path,
    plugin_class,
    plugin_id,
    override_name,
):
    root = tmp_path / "runtime-cache"
    override = tmp_path / "absolute-cache"
    monkeypatch.setenv("INKYPI_CACHE_DIR", str(root))
    monkeypatch.setenv(override_name, str(override))

    cache_dir = _plugin(plugin_class, plugin_id)._cache_dir()

    assert cache_dir == override


@pytest.mark.parametrize(
    ("plugin_class", "plugin_id", "override_name", "leaf"),
    [
        (
            PixivR18Ranking,
            "pixiv_r18_ranking",
            "INKYPI_PIXIV_R18_CACHE",
            ".pixiv_r18_ranking_cache",
        ),
        (
            RedditRule34Hot,
            "reddit_rule34_hot",
            "INKYPI_REDDIT_RULE34_CACHE",
            ".reddit_rule34_hot_cache",
        ),
    ],
)
def test_ranking_plugin_cache_keeps_development_source_fallback(
    monkeypatch,
    plugin_class,
    plugin_id,
    override_name,
    leaf,
):
    monkeypatch.delenv("INKYPI_CACHE_DIR", raising=False)
    monkeypatch.delenv(override_name, raising=False)
    plugin = _plugin(plugin_class, plugin_id)

    cache_dir = plugin._cache_dir()

    assert cache_dir == Path(plugin.get_plugin_dir(leaf))


def test_sports_dashboard_cache_uses_global_runtime_root(monkeypatch, tmp_path):
    root = tmp_path / "runtime-cache"
    monkeypatch.setenv("INKYPI_CACHE_DIR", str(root))

    cache_dir = _plugin(
        SportsDashboard,
        "sports_dashboard",
    )._sports_dashboard_cache_dir()

    assert cache_dir == root / "plugins" / "sports_dashboard" / "cache"
    assert cache_dir.is_dir()


def test_stocktracker_matplotlib_cache_uses_runtime_cache(monkeypatch, tmp_path):
    root = tmp_path / "runtime-cache"
    monkeypatch.setenv("INKYPI_CACHE_DIR", str(root))

    config_dir = Path(stocktracker._default_mpl_config_dir())

    assert config_dir == root / "plugins" / "stocktracker" / "matplotlib"


def test_stocktracker_history_uses_runtime_data(monkeypatch, tmp_path):
    root = tmp_path / "runtime-data"
    monkeypatch.setenv("INKYPI_DATA_DIR", str(root))
    monkeypatch.delenv("INKYPI_STOCKTRACKER_HISTORY_DIR", raising=False)
    monkeypatch.delenv("INKYPI_STOCKTRACKER_HISTORY_FILE", raising=False)
    plugin = StockTracker.__new__(StockTracker)
    plugin.config = {"id": "stocktracker"}

    history_path = Path(
        plugin._portfolio_history_path(
            [{"symbol": "AAPL", "shares": 1.0, "is_cash": False}]
        )
    )

    assert history_path.parent == root / "plugins" / "stocktracker" / "history"


@pytest.mark.parametrize("configured_path", ["", "accounts/my-session"])
def test_telegram_session_uses_runtime_data(monkeypatch, tmp_path, configured_path):
    root = tmp_path / "runtime-data"
    monkeypatch.setenv("INKYPI_DATA_DIR", str(root))
    plugin = TelegramDigest.__new__(TelegramDigest)
    plugin.config = {"id": "telegram_digest"}

    class DeviceConfig:
        def load_env_key(self, key):
            if key == "TELEGRAM_SESSION_PATH":
                return configured_path
            return ""

    config = plugin._account_config({}, DeviceConfig())

    expected = root / "plugins" / "telegram_digest"
    expected = expected / (configured_path or "telegram_account")
    assert Path(config["session_path"]) == expected
    assert Path(config["session_file"]) == Path(str(expected) + ".session")


def test_telegram_session_reuses_authorized_shared_runtime_session(monkeypatch, tmp_path):
    root = tmp_path / "runtime-data"
    root.mkdir()
    shared_session = root / "telegram_account.session"
    shared_session.write_text("authorized", encoding="utf-8")
    monkeypatch.setenv("INKYPI_DATA_DIR", str(root))
    plugin = TelegramDigest.__new__(TelegramDigest)
    plugin.config = {"id": "telegram_digest"}

    class DeviceConfig:
        def load_env_key(self, key):
            return ""

    config = plugin._account_config({}, DeviceConfig())

    assert Path(config["session_path"]) == root / "telegram_account"
    assert Path(config["session_file"]) == shared_session
    assert config["session_ready"] is True


def test_backtothedate_state_keeps_development_source_fallback(monkeypatch):
    monkeypatch.delenv("INKYPI_CACHE_DIR", raising=False)
    plugin = _plugin(BacktotheDate, "backtothedate")

    state_path = plugin._state_path()

    assert state_path == Path(plugin.get_plugin_dir(".backtothedate_state.json"))


def test_newspaper_rotation_state_keeps_development_source_fallback(monkeypatch):
    monkeypatch.delenv("INKYPI_CACHE_DIR", raising=False)
    plugin = _plugin(Newspaper, "newspaper")

    state_path = plugin._rotation_state_path()

    assert state_path == Path(plugin.get_plugin_dir(".newspaper_rotation_state.json"))


def test_sports_dashboard_cache_keeps_development_source_fallback(monkeypatch):
    monkeypatch.delenv("INKYPI_CACHE_DIR", raising=False)
    plugin = _plugin(SportsDashboard, "sports_dashboard")

    cache_dir = plugin._sports_dashboard_cache_dir()

    assert cache_dir == Path(plugin.get_plugin_dir("cache"))
