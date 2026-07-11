from copy import deepcopy
from datetime import datetime
from types import MappingProxyType, SimpleNamespace

import pytest
from PIL import Image

from plugins.base_plugin.base_plugin import BasePlugin
from plugins.plugin_manifest import PluginTheme


class FakeDeviceConfig:
    def __init__(self, config=None, resolution=(800, 480)):
        self.config = dict(config or {})
        self.resolution = resolution

    def get_config(self, key=None, default=None):
        if key is None:
            return self.config
        return self.config.get(key, default)

    def get_resolution(self):
        return self.resolution


def _plugin_config(presentation="ui"):
    theme = PluginTheme(
        presentation=presentation,
        day=MappingProxyType(
            {"background": "#f7f1e3", "accent": "#9b3424"}
        ),
        night=MappingProxyType(
            {"background": "#101820", "accent": "#f2aa4c"}
        ),
    )
    return {"id": "example", "_manifest": SimpleNamespace(theme=theme)}


def _plugin(config=None):
    plugin = BasePlugin.__new__(BasePlugin)
    plugin.config = config or _plugin_config()
    return plugin


class RecordingPlugin(BasePlugin):
    def __init__(self, config, image):
        self.config = config
        self.image = image
        self.seen_settings = None

    def generate_image(self, settings, device_config):
        self.seen_settings = settings
        return self.image


def _recording_plugin(*, presentation="ui", image=None):
    return RecordingPlugin(
        _plugin_config(presentation),
        image or Image.new("RGB", (32, 24), (93, 111, 129)),
    )


def _pattern_image(size=(800, 480)):
    byte_count = size[0] * size[1] * 3
    pattern = (bytes(range(256)) * ((byte_count + 255) // 256))[:byte_count]
    return Image.frombytes("RGB", size, pattern)


def _apply_media_theme_chrome(image, plugin_id, theme, dimensions):
    from plugins.base_plugin.theme_presentation import (
        apply_media_theme_chrome,
    )

    return apply_media_theme_chrome(image, plugin_id, theme, dimensions)


def test_resolve_theme_uses_forced_mode_and_immutable_manifest_palette():
    plugin = _plugin()

    theme = plugin.resolve_theme(
        {"themeMode": "night"},
        FakeDeviceConfig({"theme_mode": "day"}),
        now=datetime(2026, 7, 11, 12, 0),
    )

    assert theme["requested_mode"] == "night"
    assert theme["mode"] == "night"
    assert theme["palette"]["background"] == (16, 24, 32)
    assert theme["palette"]["accent"] == (242, 170, 76)


def test_render_themed_image_copies_settings_and_injects_theme_contract():
    plugin = _recording_plugin()
    settings = {
        "themeMode": "night",
        "forceRefresh": "true",
        "nested": {"keep": "unchanged"},
    }
    original = deepcopy(settings)

    plugin.render_themed_image(
        settings,
        FakeDeviceConfig({"theme_mode": "day"}),
    )

    assert settings == original
    assert plugin.seen_settings is not settings
    assert plugin.seen_settings["nested"] is settings["nested"]
    assert plugin.seen_settings["_inkypi_theme"]["mode"] == "night"
    assert plugin.seen_settings["_theme_render_only"] is False


def test_theme_only_render_removes_both_force_keys_from_copy():
    plugin = _recording_plugin()
    settings = {
        "themeMode": "night",
        "forceRefresh": "true",
        "force_refresh": True,
    }

    plugin.render_themed_image(
        settings,
        FakeDeviceConfig({"theme_mode": "day"}),
        theme_render_only=True,
    )

    assert settings["forceRefresh"] == "true"
    assert settings["force_refresh"] is True
    assert "forceRefresh" not in plugin.seen_settings
    assert "force_refresh" not in plugin.seen_settings
    assert plugin.seen_settings["_theme_render_only"] is True


def test_normal_render_preserves_both_force_keys_in_copy():
    plugin = _recording_plugin()
    settings = {
        "forceRefresh": "true",
        "force_refresh": True,
    }

    plugin.render_themed_image(
        settings,
        FakeDeviceConfig({"theme_mode": "day"}),
    )

    assert plugin.seen_settings["forceRefresh"] == "true"
    assert plugin.seen_settings["force_refresh"] is True


@pytest.mark.parametrize("presentation", ["ui", None])
def test_ui_and_disabled_theme_presentations_preserve_pixels_and_metadata(
    presentation,
):
    image = Image.new("RGB", (32, 24))
    image.putpixel((3, 4), (17, 33, 65))
    image.info["inkypi_skip_cache"] = True
    image.info["source"] = "provider"
    config = _plugin_config(presentation) if presentation else {"id": "example"}
    plugin = RecordingPlugin(config, image)
    pixels_before = image.tobytes()

    result = plugin.render_themed_image(
        {"themeMode": "night"},
        FakeDeviceConfig({"theme_mode": "day"}),
    )

    assert result.tobytes() == pixels_before
    assert result.info["inkypi_skip_cache"] is True
    assert result.info["source"] == "provider"
    assert result.info["inkypi_theme_mode"] == "night"


def test_media_chrome_preserves_800x480_inner_bytes_and_source_metadata():
    source = _pattern_image()
    source.info["inkypi_skip_cache"] = True
    source.info["provider_etag"] = "unchanged"
    expected_inner = source.crop((8, 8, 792, 472)).tobytes()
    plugin = _recording_plugin(presentation="media", image=source)

    result = plugin.render_themed_image(
        {"themeMode": "night"},
        FakeDeviceConfig({"theme_mode": "day"}),
    )

    assert result.mode == "RGB"
    assert result.size == (800, 480)
    assert result.crop((8, 8, 792, 472)).tobytes() == expected_inner
    assert result.getpixel((0, 0)) == (16, 24, 32)
    assert result.getpixel((6, 6)) == (242, 170, 76)
    assert result.getpixel((7, 7)) == (242, 170, 76)
    assert result.info["inkypi_skip_cache"] is True
    assert result.info["provider_etag"] == "unchanged"
    assert result.info["inkypi_theme_mode"] == "night"


def test_media_day_and_night_chrome_differ_without_changing_inner_media():
    source = _pattern_image()
    expected_inner = source.crop((8, 8, 792, 472)).tobytes()
    device = FakeDeviceConfig({"theme_mode": "day"})

    day = _recording_plugin(
        presentation="media",
        image=source.copy(),
    ).render_themed_image({"themeMode": "day"}, device)
    night = _recording_plugin(
        presentation="media",
        image=source.copy(),
    ).render_themed_image({"themeMode": "night"}, device)

    assert day.getpixel((0, 0)) == (247, 241, 227)
    assert night.getpixel((0, 0)) == (16, 24, 32)
    assert day.getpixel((6, 6)) == (155, 52, 36)
    assert night.getpixel((6, 6)) == (242, 170, 76)
    assert day.crop((8, 8, 792, 472)).tobytes() == expected_inner
    assert night.crop((8, 8, 792, 472)).tobytes() == expected_inner


def test_media_chrome_supports_minimum_dimensions_and_converts_to_rgb():
    source = Image.new("RGBA", (18, 18), (12, 34, 56, 127))
    source.putpixel((8, 8), (201, 133, 77, 63))
    source.info["inkypi_skip_cache"] = "keep"
    theme = {
        "palette": {
            "background": (1, 2, 3),
            "accent": (4, 5, 6),
        }
    }
    expected_inner = source.convert("RGB").crop((8, 8, 10, 10)).tobytes()

    result = _apply_media_theme_chrome(
        source,
        "minimum",
        theme,
        (18, 18),
    )

    assert result.mode == "RGB"
    assert result.crop((8, 8, 10, 10)).tobytes() == expected_inner
    assert result.getpixel((0, 0)) == (1, 2, 3)
    assert result.getpixel((6, 6)) == (4, 5, 6)
    assert result.info["inkypi_skip_cache"] == "keep"


def test_media_chrome_rejects_source_size_mismatch():
    theme = {
        "palette": {
            "background": (1, 2, 3),
            "accent": (4, 5, 6),
        }
    }

    with pytest.raises(
        ValueError,
        match=r"example.*800x480.*799x480",
    ):
        _apply_media_theme_chrome(
            Image.new("RGB", (799, 480)),
            "example",
            theme,
            (800, 480),
        )


@pytest.mark.parametrize("dimensions", [(17, 18), (18, 17)])
def test_media_chrome_rejects_dimensions_smaller_than_18(dimensions):
    theme = {
        "palette": {
            "background": (1, 2, 3),
            "accent": (4, 5, 6),
        }
    }

    with pytest.raises(ValueError, match=r"at least 18x18"):
        _apply_media_theme_chrome(
            Image.new("RGB", dimensions),
            "example",
            theme,
            dimensions,
        )
