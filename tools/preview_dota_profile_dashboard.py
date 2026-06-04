from __future__ import annotations

import os
import sys
import types
import urllib.parse
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "inkypi-weather" / "package" / "InkyPi" / "src"
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

    class PreviewResponse:
        def __init__(self, url):
            with urllib.request.urlopen(url, timeout=25) as response:
                self.status_code = response.status
                self.content = response.read()

        def raise_for_status(self):
            if self.status_code >= 400:
                raise RuntimeError(f"HTTP {self.status_code}")

        def json(self):
            import json

            return json.loads(self.content.decode("utf-8"))

    class PreviewSession:
        def get(self, url, params=None, timeout=None):
            if params:
                separator = "&" if "?" in url else "?"
                url = f"{url}{separator}{urllib.parse.urlencode(params)}"
            return PreviewResponse(url)

    http = types.ModuleType("utils.http_client")
    http.get_http_session = lambda: PreviewSession()
    sys.modules.setdefault("utils.http_client", http)

    theme = types.ModuleType("utils.theme_utils")
    theme.get_theme_context = lambda *args, **kwargs: {}
    sys.modules.setdefault("utils.theme_utils", theme)


class FakeDeviceConfig:
    def get_resolution(self):
        return (800, 480)

    def get_config(self, key=None, default=None):
        values = {"orientation": "horizontal", "theme_mode": "night"}
        if key is None:
            return values
        return values.get(key, default)

    def load_env_key(self, key):
        return os.getenv(key)


def main():
    install_import_stubs()
    from plugins.dota_profile_dashboard.dota_profile_dashboard import DotaProfileDashboard

    output = ROOT / ".tmp" / "dota_profile_dashboard_preview.png"
    cache = ROOT / ".tmp" / "dota_profile_dashboard_preview_cache"
    output.parent.mkdir(parents=True, exist_ok=True)
    cache.mkdir(parents=True, exist_ok=True)

    plugin = DotaProfileDashboard({"id": "dota_profile_dashboard"})
    plugin._cache_dir = lambda: cache
    image = plugin.generate_image({"useMockData": "true", "forceRefresh": "true"}, FakeDeviceConfig())
    image.save(output)
    print(output)


if __name__ == "__main__":
    main()
