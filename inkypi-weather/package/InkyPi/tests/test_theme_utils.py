from datetime import datetime

import pytest

from src.utils import theme_utils


NOON = datetime(2026, 5, 27, 12, 0)


class FakeDeviceConfig:
    def __init__(self, config=None):
        self.config = dict(config or {})

    def get_config(self, key=None, default=None):
        if key is None:
            return self.config
        return self.config.get(key, default)


def _relative_luminance(rgb):
    channels = []
    for channel in rgb:
        value = channel / 255
        channels.append(value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4)
    return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2]


def _contrast_ratio(first, second):
    lighter, darker = sorted((_relative_luminance(first), _relative_luminance(second)), reverse=True)
    return (lighter + 0.05) / (darker + 0.05)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("paper", "day"),
        ("light", "day"),
        ("comic", "day"),
        ("dark", "night"),
        ("cinema", "night"),
        ("streaming", "night"),
        ("midnight", "night"),
    ],
)
def test_normalize_theme_mode_accepts_legacy_aliases(raw, expected):
    assert theme_utils.normalize_theme_mode(raw) == expected


@pytest.mark.parametrize("raw", [None, "", "unknown"])
def test_normalize_theme_mode_uses_default_for_missing_or_unknown_values(raw):
    assert theme_utils.normalize_theme_mode(raw, "auto") == "auto"


def test_plugin_forced_mode_overrides_device_auto(monkeypatch):
    monkeypatch.setattr(theme_utils, "read_contexts", lambda *args, **kwargs: [])
    fake_device = FakeDeviceConfig({"timezone": "America/Los_Angeles", "theme_mode": "auto"})

    result = theme_utils.resolve_plugin_theme({"themeMode": "night"}, fake_device, now=NOON)

    assert result["requested_mode"] == "night"
    assert result["mode"] == "night"


def test_plugin_auto_uses_shared_sunrise_sunset(monkeypatch):
    monkeypatch.setattr(
        theme_utils,
        "read_contexts",
        lambda *args, **kwargs: [
            {
                "payload": {
                    "astronomy": {
                        "timezone": "America/Los_Angeles",
                        "sunrise": "2026-05-27T05:50:00-07:00",
                        "sunset": "2026-05-27T20:15:00-07:00",
                    }
                }
            }
        ],
    )
    fake_device = FakeDeviceConfig({"timezone": "America/Los_Angeles"})

    result = theme_utils.resolve_plugin_theme({"themeMode": "auto"}, fake_device, now=NOON)

    assert result["requested_mode"] == "auto"
    assert result["mode"] == "day"
    assert result["source"] == "weather"
    assert result["reason"] == "sunrise/sunset"


def test_plugin_theme_key_precedence(monkeypatch):
    monkeypatch.setattr(theme_utils, "read_contexts", lambda *args, **kwargs: [])
    settings = {
        "themeMode": "night",
        "theme_mode": "day",
        "theme": "day",
        "sportsDashboardTheme": "day",
    }

    result = theme_utils.resolve_plugin_theme(settings, now=NOON)

    assert result["requested_mode"] == "night"
    assert result["mode"] == "night"


@pytest.mark.parametrize("settings", [None, {}, {"themeMode": ""}, {"themeMode": "unknown"}])
def test_missing_empty_or_unknown_plugin_theme_resolves_to_auto(monkeypatch, settings):
    monkeypatch.setattr(theme_utils, "read_contexts", lambda *args, **kwargs: [])

    result = theme_utils.resolve_plugin_theme(settings, now=NOON)

    assert result["requested_mode"] == "auto"
    assert result["mode"] == "day"


def test_missing_palette_roles_receive_readable_fallbacks(monkeypatch):
    monkeypatch.setattr(theme_utils, "read_contexts", lambda *args, **kwargs: [])

    result = theme_utils.resolve_plugin_theme(
        {"themeMode": "night"},
        now=NOON,
        palette={"night": {"background": "#101010", "accent": "#ff8800"}},
    )

    required_roles = {"background", "panel", "ink", "muted", "rule", "accent"}
    assert required_roles <= result["palette"].keys()
    assert result["palette"]["background"] == (16, 16, 16)
    assert result["palette"]["accent"] == (255, 136, 0)
    assert _contrast_ratio(result["palette"]["background"], result["palette"]["ink"]) >= 4.5
    assert result["css"]["background"] == "#101010"
    assert result["css"]["accent"] == "#ff8800"


def test_plugin_theme_result_always_contains_canonical_contract(monkeypatch):
    monkeypatch.setattr(theme_utils, "read_contexts", lambda *args, **kwargs: [])

    result = theme_utils.resolve_plugin_theme(now=NOON)

    assert {"mode", "requested_mode", "palette", "css", "source", "reason"} <= result.keys()


def test_theme_context_uses_day_fallback_without_weather(monkeypatch):
    monkeypatch.setattr(theme_utils, "read_contexts", lambda *args, **kwargs: [])

    context = theme_utils.get_theme_context(
        FakeDeviceConfig({"timezone": "America/Los_Angeles"}),
        now=datetime(2026, 5, 27, 12, 0),
    )

    assert context["mode"] == "day"
    assert context["source"] == "fallback"
    assert context["palette"]["background"] == (255, 255, 255)


def test_theme_context_uses_night_fallback_without_weather(monkeypatch):
    monkeypatch.setattr(theme_utils, "read_contexts", lambda *args, **kwargs: [])

    context = theme_utils.get_theme_context(
        FakeDeviceConfig({"timezone": "America/Los_Angeles"}),
        now=datetime(2026, 5, 27, 22, 0),
    )

    assert context["mode"] == "night"
    assert context["source"] == "fallback"
    assert context["palette"]["background"] == (0, 0, 0)


def test_theme_context_uses_weather_sunrise_sunset(monkeypatch):
    def fake_contexts(*args, **kwargs):
        return [
            {
                "payload": {
                    "astronomy": {
                        "timezone": "America/Los_Angeles",
                        "sunrise": "2026-05-27T05:50:00-07:00",
                        "sunset": "2026-05-27T20:15:00-07:00",
                    }
                }
            }
        ]

    monkeypatch.setattr(theme_utils, "read_contexts", fake_contexts)

    day = theme_utils.get_theme_context(
        FakeDeviceConfig({"timezone": "America/Los_Angeles"}),
        now=datetime(2026, 5, 27, 12, 0),
    )
    night = theme_utils.get_theme_context(
        FakeDeviceConfig({"timezone": "America/Los_Angeles"}),
        now=datetime(2026, 5, 27, 21, 0),
    )

    assert day["mode"] == "day"
    assert day["source"] == "weather"
    assert night["mode"] == "night"
    assert night["source"] == "weather"


def test_theme_context_forced_mode_skips_weather(monkeypatch):
    def fail_if_called(*args, **kwargs):
        raise AssertionError("weather context should not be read for forced mode")

    monkeypatch.setattr(theme_utils, "read_contexts", fail_if_called)

    context = theme_utils.get_theme_context(
        FakeDeviceConfig({"timezone": "America/Los_Angeles", "display_theme_mode": "dark"}),
        now=datetime(2026, 5, 27, 12, 0),
    )

    assert context["mode"] == "night"
    assert context["source"] == "config"


def test_apply_theme_to_plugin_settings_overrides_render_colors():
    theme = theme_utils.get_theme_context(
        FakeDeviceConfig({"timezone": "America/Los_Angeles", "theme_mode": "light"}),
        now=datetime(2026, 5, 27, 22, 0),
    )

    settings = theme_utils.apply_theme_to_plugin_settings({"displayGraph": "true"}, theme)

    assert settings["displayGraph"] == "true"
    assert settings["backgroundOption"] == "color"
    assert settings["backgroundColor"] == "#ffffff"
    assert settings["textColor"] == "#0a0c0f"
