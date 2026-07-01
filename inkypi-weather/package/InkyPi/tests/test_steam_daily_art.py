from datetime import datetime
from pathlib import Path
import sys

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from plugins.steam_daily_art.steam_daily_art import SteamDailyArt  # noqa: E402


class FakeDeviceConfig:
    def __init__(self, resolution=(800, 480), timezone="America/Los_Angeles"):
        self.resolution = resolution
        self.timezone = timezone

    def get_resolution(self):
        return self.resolution

    def get_config(self, key=None, default=None):
        values = {
            "timezone": self.timezone,
            "orientation": "horizontal",
        }
        if key is None:
            return values
        return values.get(key, default)


def make_plugin(tmp_path, monkeypatch):
    plugin = SteamDailyArt({"id": "steam_daily_art"})
    monkeypatch.setattr(plugin, "_cache_dir", lambda: tmp_path)
    monkeypatch.setattr(plugin, "_write_daily_art_context", lambda *args, **kwargs: None)
    monkeypatch.setattr(plugin, "_now_for_device", lambda device_config: datetime(2026, 6, 29, 15, 5, 0))
    return plugin


def base_settings():
    return {
        "sourceCategory": "fresh_frontpage",
        "selectionMode": "daily_rotation",
        "rotationCadence": "hourly",
        "imageMode": "library_hero",
        "logoOverlay": "hide",
        "logoPosition": "empty_space",
        "logoSize": "normal",
        "countryCode": "US",
        "language": "english",
        "showCaption": "false",
    }


def write_matching_cache(plugin, settings, tmp_path):
    image_path = tmp_path / "cached.png"
    Image.new("RGB", (800, 480), "black").save(image_path)
    rotation_key = "2026-06-29-15"
    plugin._write_cache({
        "cache_key": plugin._cache_key(settings, (800, 480), rotation_key),
        "rotation_key": rotation_key,
        "name": "Cached Game",
        "appid": 1,
        "image_path": str(image_path),
    })


def test_generate_image_uses_matching_cache_without_force(tmp_path, monkeypatch):
    plugin = make_plugin(tmp_path, monkeypatch)
    settings = base_settings()
    write_matching_cache(plugin, settings, tmp_path)

    monkeypatch.setattr(
        plugin,
        "_select_item",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("selection should not run")),
    )

    image = plugin.generate_image(settings, FakeDeviceConfig())

    assert image.getpixel((0, 0)) == (0, 0, 0)


def test_force_refresh_bypasses_matching_cache(tmp_path, monkeypatch):
    plugin = make_plugin(tmp_path, monkeypatch)
    settings = base_settings()
    write_matching_cache(plugin, settings, tmp_path)
    calls = []

    def select_item(received_settings, rotation_key):
        calls.append((received_settings.get("forceRefresh"), rotation_key))
        return {"id": 2, "name": "Fresh Game"}

    monkeypatch.setattr(plugin, "_select_item", select_item)
    monkeypatch.setattr(
        plugin,
        "_download_first_available_image",
        lambda item, received_settings: ("https://example.test/fresh.jpg", Image.new("RGB", (800, 480), "white")),
    )
    monkeypatch.setattr(plugin, "_download_first_available_logo", lambda item: (None, None))

    image = plugin.generate_image({**settings, "forceRefresh": True}, FakeDeviceConfig())
    cache_entry = plugin._read_cache()

    assert calls == [(True, "2026-06-29-15")]
    assert image.getpixel((0, 0)) == (255, 255, 255)
    assert cache_entry["name"] == "Fresh Game"


def test_settings_template_persists_refresh_on_display_default():
    settings_path = Path(__file__).resolve().parents[1] / "src" / "plugins" / "steam_daily_art" / "settings.html"
    html = settings_path.read_text(encoding="utf-8")

    assert 'name="refreshOnDisplay"' in html
    assert 'value="true"' in html