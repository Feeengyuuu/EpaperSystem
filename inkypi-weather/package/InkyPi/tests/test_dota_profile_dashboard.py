import os
import sys
import types
from pathlib import Path

from PIL import Image

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))


def install_import_stubs():
    base_pkg = types.ModuleType("plugins.base_plugin")
    sys.modules.setdefault("plugins.base_plugin", base_pkg)
    base = types.ModuleType("plugins.base_plugin.base_plugin")

    class BasePlugin:
        def __init__(self, config, **_dependencies):
            self.config = config

        def get_plugin_id(self):
            return self.config.get("id")

        def get_plugin_dir(self, path=None):
            plugin_dir = SRC / "plugins" / self.get_plugin_id()
            return str(plugin_dir / path) if path else str(plugin_dir)

        def generate_settings_template(self):
            return {"settings_template": "base_plugin/settings.html"}

    base.BasePlugin = BasePlugin
    sys.modules.setdefault("plugins.base_plugin.base_plugin", base)

    context = types.ModuleType("plugins.context_cache")
    context.write_context = lambda *args, **kwargs: None
    sys.modules.setdefault("plugins.context_cache", context)

    http = types.ModuleType("utils.http_client")
    http.get_http_session = lambda: None
    sys.modules.setdefault("utils.http_client", http)

    theme = types.ModuleType("utils.theme_utils")
    theme.get_theme_context = lambda *args, **kwargs: {}
    sys.modules.setdefault("utils.theme_utils", theme)


install_import_stubs()

from plugins.dota_profile_dashboard.dota_profile_dashboard import (  # noqa: E402
    DEFAULT_ACCOUNT_ID,
    DEFAULT_STEAM_ID64,
    DotaProfileDashboard,
)


class FakeDeviceConfig:
    def __init__(self, resolution=(800, 480), orientation="horizontal", env=None):
        self.resolution = resolution
        self.orientation = orientation
        self.env = env or {}

    def get_resolution(self):
        return self.resolution

    def get_config(self, key=None, default=None):
        values = {"orientation": self.orientation, "theme_mode": "night"}
        if key is None:
            return values
        return values.get(key, default)

    def load_env_key(self, key):
        return self.env.get(key)


def make_plugin(tmp_path):
    plugin = DotaProfileDashboard({"id": "dota_profile_dashboard"})
    plugin._cache_dir = lambda: tmp_path
    return plugin


def test_account_id_defaults_to_known_steam_profile(tmp_path):
    plugin = make_plugin(tmp_path)

    assert plugin._account_id({}) == DEFAULT_ACCOUNT_ID
    assert plugin._account_id({"steamId64": DEFAULT_STEAM_ID64}) == DEFAULT_ACCOUNT_ID
    assert plugin._account_id({"accountId": "12345"}) == "12345"


def test_mock_generate_image_renders_dashboard(tmp_path):
    plugin = make_plugin(tmp_path)

    image = plugin.generate_image({"useMockData": "true"}, FakeDeviceConfig())

    assert isinstance(image, Image.Image)
    assert image.size == (800, 480)
    generated = list(tmp_path.glob("*.png"))
    assert generated
    assert not any(path.name.startswith("image_") for path in generated)


def test_fetch_uses_no_key_endpoints_and_hero_cache(tmp_path, monkeypatch):
    plugin = make_plugin(tmp_path)
    seen_paths = []

    def fake_get_json(path, params=None):
        seen_paths.append((path, params or {}))
        if path == "/heroStats":
            return [{"id": 1, "localized_name": "敌法师"}]
        if path.endswith("/wl"):
            return {"win": 1, "lose": 1}
        if path.endswith("/recentMatches"):
            return []
        if path.endswith("/heroes"):
            return []
        if path.endswith("/totals"):
            return []
        if path.endswith("/counts"):
            return {}
        if path.endswith("/rankings"):
            return []
        if path.endswith("/wordcloud"):
            return {}
        if "/records/" in path:
            return []
        return {"profile": {"personaname": "Tester"}, "rank_tier": 53}

    monkeypatch.setattr(plugin, "_get_json", fake_get_json)

    data = plugin._fetch_dashboard_data("123", {"includeWordcloud": "true", "includeRecords": "true"}, FakeDeviceConfig())

    assert data["profile"]["rank_tier"] == 53
    assert data["wl"]["win"] == 1
    assert any(path == "/players/123/recentMatches" for path, _ in seen_paths)
    assert any(path == "/heroStats" for path, _ in seen_paths)
    assert all("api_key" not in params for _, params in seen_paths)


def test_record_and_wordcloud_helpers_are_stable(tmp_path):
    plugin = make_plugin(tmp_path)
    data = plugin._sample_payload()

    assert plugin._record_lines(data)
    assert plugin._wordcloud_terms(data["wordcloud"])[:2] == ["push", "roshan"]
    assert "game_mode" in plugin._counts_line(data["counts"])


def test_hero_image_urls_prefer_working_steam_cdn(tmp_path):
    plugin = make_plugin(tmp_path)

    urls = plugin._hero_image_urls({"icon": "/apps/dota2/images/dota_react/heroes/icons/antimage.png?"})

    assert urls[0] == "https://cdn.cloudflare.steamstatic.com/apps/dota2/images/dota_react/heroes/icons/antimage.png?"
    assert "https://api.opendota.com/apps/dota2/images/dota_react/heroes/icons/antimage.png?" in urls


def test_hero_stats_apply_simplified_chinese_names(tmp_path):
    plugin = make_plugin(tmp_path)

    heroes = plugin._apply_hero_names(
        [{"id": 1, "localized_name": "Anti-Mage"}, {"id": 3, "localized_name": "Bane"}],
        {1: "敌法师", 3: "祸乱之源"},
    )

    assert heroes[1]["localized_name"] == "敌法师"
    assert heroes[1]["localized_name_en"] == "Anti-Mage"
    assert heroes[3]["localized_name"] == "祸乱之源"


def test_hero_square_icon_uses_black_background(tmp_path):
    plugin = make_plugin(tmp_path)
    raw = Image.new("RGBA", (16, 8), (255, 255, 255, 255))
    raw.putpixel((8, 4), (255, 0, 0, 255))

    icon = plugin._square_icon(raw, 24)

    assert icon.getpixel((1, 1))[:3] == (0, 0, 0)
    assert icon.getpixel((12, 12))[0] > 0
    assert icon.getpixel((12, 10))[:3] == (0, 0, 0)
