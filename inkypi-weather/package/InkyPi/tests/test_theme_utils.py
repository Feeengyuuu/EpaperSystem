from datetime import datetime

from src.utils import theme_utils


class FakeDeviceConfig:
    def __init__(self, config=None):
        self.config = dict(config or {})

    def get_config(self, key=None, default=None):
        if key is None:
            return self.config
        return self.config.get(key, default)


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
