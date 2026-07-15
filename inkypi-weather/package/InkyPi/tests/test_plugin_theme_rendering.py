from copy import deepcopy
from datetime import datetime
from types import MappingProxyType, SimpleNamespace

import pytest
from PIL import Image

from plugins.base_plugin.base_plugin import BasePlugin
from plugins.plugin_manifest import PluginTheme
from utils import theme_utils


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


class NestedThemePlugin(RecordingPlugin):
    def generate_image(self, settings, device_config):
        self.nested_theme = theme_utils.get_theme_context(device_config)
        return super().generate_image(settings, device_config)


class EffectiveContextPlugin(RecordingPlugin):
    def __init__(self, config, image, effective_context):
        super().__init__(config, image)
        self.effective_context = effective_context

    def generate_image(self, settings, device_config):
        image = super().generate_image(settings, device_config)
        image.info[theme_utils.EFFECTIVE_THEME_CONTEXT_INFO_KEY] = (
            self.effective_context
        )
        return image


def _recording_plugin(*, presentation="ui", image=None):
    return RecordingPlugin(
        _plugin_config(presentation),
        image or Image.new("RGB", (32, 24), (93, 111, 129)),
    )


def _full_theme_context(mode, *, requested_mode="auto"):
    palette = (
        {
            "background": (16, 24, 32),
            "panel": (16, 24, 32),
            "ink": (255, 255, 255),
            "muted": (194, 196, 202),
            "rule": (46, 48, 56),
            "accent": (242, 170, 76),
        }
        if mode == "night"
        else {
            "background": (247, 241, 227),
            "panel": (247, 241, 227),
            "ink": (10, 12, 15),
            "muted": (74, 78, 84),
            "rule": (185, 188, 194),
            "accent": (155, 52, 36),
        }
    )
    return {
        "requested_mode": requested_mode,
        "mode": mode,
        "source": "weather",
        "reason": "sunrise/sunset",
        "date": "2026-07-12",
        "timezone": "America/Los_Angeles",
        "sunrise": "2026-07-12T05:56:00-07:00",
        "sunset": "2026-07-12T20:31:00-07:00",
        "palette": palette,
        "css": {
            key: "#%02x%02x%02x" % value
            for key, value in palette.items()
        },
    }


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


def test_render_themed_image_uses_detached_supplied_context_without_resolving(
    monkeypatch,
):
    plugin = _recording_plugin()
    supplied = MappingProxyType(
        {
            "requested_mode": "night",
            "mode": "night",
            "source": "weather",
            "palette": MappingProxyType(
                {
                    "background": (16, 24, 32),
                    "accent": (242, 170, 76),
                }
            ),
            "css": MappingProxyType(
                {
                    "background": "rgb(16, 24, 32)",
                    "accent": "rgb(242, 170, 76)",
                }
            ),
        }
    )
    monkeypatch.setattr(
        plugin,
        "resolve_theme",
        lambda *_args, **_kwargs: pytest.fail("supplied context was re-resolved"),
    )

    plugin.render_themed_image(
        {"themeMode": "day"},
        FakeDeviceConfig({"theme_mode": "day"}),
        resolved_theme_context=supplied,
    )

    injected = plugin.seen_settings["_inkypi_theme"]
    assert injected["requested_mode"] == "night"
    assert injected["mode"] == "night"
    assert injected["palette"] == {
        "background": (16, 24, 32),
        "accent": (242, 170, 76),
    }
    assert injected is not supplied
    assert injected["palette"] is not supplied["palette"]
    injected["palette"]["background"] = (1, 2, 3)
    assert supplied["palette"]["background"] == (16, 24, 32)


def test_frozen_supplied_context_yields_pil_safe_palette_colors():
    from runtime.refresh_contracts import freeze_payload

    plugin = _recording_plugin()
    frozen = freeze_payload(_full_theme_context("night"))

    plugin.render_themed_image(
        {},
        FakeDeviceConfig(),
        resolved_theme_context=frozen,
    )

    palette = plugin.seen_settings["_inkypi_theme"]["palette"]
    for role, color in palette.items():
        assert isinstance(color, tuple), role
        Image.new("RGB", (2, 2), color)


def test_nested_auto_renderer_uses_command_pinned_shared_context(monkeypatch):
    plugin = NestedThemePlugin(
        _plugin_config(),
        Image.new("RGB", (32, 24), "white"),
    )
    supplied = _full_theme_context("night")
    monkeypatch.setattr(
        theme_utils,
        "read_contexts",
        lambda *_args, **_kwargs: pytest.fail("nested render escaped command pin"),
    )

    plugin.render_themed_image(
        {"themeMode": "auto"},
        FakeDeviceConfig({"theme_mode": "auto"}),
        resolved_theme_context=supplied,
    )

    assert plugin.nested_theme["mode"] == "night"
    assert plugin.nested_theme["timezone"] == "America/Los_Angeles"
    assert plugin.nested_theme["sunrise"] == supplied["sunrise"]


def test_two_auto_plugins_share_identical_weather_projection(monkeypatch):
    supplied = _full_theme_context("day")
    monkeypatch.setattr(
        theme_utils,
        "read_contexts",
        lambda *_args, **_kwargs: pytest.fail("auto render escaped command pin"),
    )
    plugins = [
        NestedThemePlugin(
            _plugin_config(),
            Image.new("RGB", (32, 24), "white"),
        )
        for _ in range(2)
    ]

    for plugin in plugins:
        plugin.render_themed_image(
            {"themeMode": "auto"},
            FakeDeviceConfig({"theme_mode": "auto"}),
            resolved_theme_context=supplied,
        )

    shared = ("source", "date", "timezone", "sunrise", "sunset")
    assert {
        key: plugins[0].nested_theme[key]
        for key in shared
    } == {
        key: plugins[1].nested_theme[key]
        for key in shared
    }


def test_forced_plugin_mode_does_not_replace_global_auto_projection(
    monkeypatch,
):
    plugin = NestedThemePlugin(
        _plugin_config(),
        Image.new("RGB", (32, 24), "white"),
    )
    supplied = _full_theme_context("day", requested_mode="night")
    supplied["mode"] = "night"
    monkeypatch.setattr(
        theme_utils,
        "read_contexts",
        lambda *_args, **_kwargs: pytest.fail("forced render escaped command pin"),
    )

    result = plugin.render_themed_image(
        {"themeMode": "night"},
        FakeDeviceConfig({"theme_mode": "auto"}),
        resolved_theme_context=supplied,
    )

    assert result.info["inkypi_theme_mode"] == "night"
    assert plugin.nested_theme["source"] == "weather"
    assert plugin.nested_theme["date"] == "2026-07-12"
    assert plugin.nested_theme["sunrise"] == supplied["sunrise"]


def test_weather_effective_context_replaces_initial_wrapper_context():
    initial = _full_theme_context("day")
    effective = _full_theme_context("night")
    config = _plugin_config()
    config["id"] = "weather"
    plugin = EffectiveContextPlugin(
        config,
        Image.new("RGB", (32, 24), "black"),
        effective,
    )

    result = plugin.render_themed_image(
        {"themeMode": "auto"},
        FakeDeviceConfig({"theme_mode": "auto"}),
        resolved_theme_context=initial,
    )

    assert result.info["inkypi_theme_mode"] == "night"
    assert result.info[theme_utils.EFFECTIVE_THEME_CONTEXT_INFO_KEY] == effective


def test_malformed_weather_effective_context_is_removed_and_cannot_change_mode():
    initial = _full_theme_context("day")
    config = _plugin_config()
    config["id"] = "weather"
    plugin = EffectiveContextPlugin(
        config,
        Image.new("RGB", (32, 24), "black"),
        {**_full_theme_context("night"), "mode": "sepia"},
    )

    result = plugin.render_themed_image(
        {"themeMode": "auto"},
        FakeDeviceConfig({"theme_mode": "auto"}),
        resolved_theme_context=initial,
    )

    assert result.info["inkypi_theme_mode"] == "day"
    assert theme_utils.EFFECTIVE_THEME_CONTEXT_INFO_KEY not in result.info


def test_theme_redraw_rejects_weather_effective_context_override():
    initial = _full_theme_context("day")
    config = _plugin_config()
    config["id"] = "weather"
    plugin = EffectiveContextPlugin(
        config,
        Image.new("RGB", (32, 24), "black"),
        _full_theme_context("night"),
    )

    result = plugin.render_themed_image(
        {"themeMode": "auto"},
        FakeDeviceConfig({"theme_mode": "auto"}),
        theme_render_only=True,
        resolved_theme_context=initial,
    )

    assert result.info["inkypi_theme_mode"] == "day"
    assert theme_utils.EFFECTIVE_THEME_CONTEXT_INFO_KEY not in result.info


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


def test_media_day_preserves_original_pixels_while_night_adds_chrome():
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

    assert day.tobytes() == source.tobytes()
    assert night.getpixel((0, 0)) == (16, 24, 32)
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
