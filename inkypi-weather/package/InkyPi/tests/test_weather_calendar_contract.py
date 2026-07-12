import json
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from plugins.simple_calendar.simple_calendar import SimpleCalendar
from plugins.weather import weather as weather_module
from plugins.weather.weather import Weather


@pytest.mark.parametrize(
    ("endpoint_name", "expected_path", "expected_query"),
    [
        (
            "WEATHER_URL",
            "/data/3.0/onecall",
            "lat={lat}&lon={long}&units={units}&exclude=minutely&appid={api_key}",
        ),
        (
            "AIR_QUALITY_URL",
            "/data/2.5/air_pollution",
            "lat={lat}&lon={long}&appid={api_key}",
        ),
        (
            "GEOCODING_URL",
            "/geo/1.0/reverse",
            "lat={lat}&lon={long}&limit=1&appid={api_key}",
        ),
    ],
)
def test_openweather_endpoints_use_https_without_changing_request_contract(
    endpoint_name, expected_path, expected_query
):
    endpoint = urlsplit(getattr(weather_module, endpoint_name))

    assert endpoint.scheme == "https"
    assert endpoint.path == expected_path
    assert endpoint.query == expected_query


class FakeDeviceConfig:
    def get_config(self, key=None, default=None):
        values = {
            "orientation": "horizontal",
            "timezone": "America/Los_Angeles",
        }
        if key is None:
            return values
        return values.get(key, default)

    def get_resolution(self):
        return (800, 480)


class RecordingSession:
    def __init__(self, weather_code=63, is_day=1):
        self.calls = []
        self.weather_code = weather_code
        self.is_day = is_day

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return RecordingResponse(self.weather_code, self.is_day)


class RecordingResponse:
    content = b"BEGIN:VCALENDAR\r\nVERSION:2.0\r\nEND:VCALENDAR\r\n"

    def __init__(self, weather_code, is_day):
        self.weather_code = weather_code
        self.is_day = is_day

    def raise_for_status(self):
        return None

    def json(self):
        return {
            "current": {
                "weather_code": self.weather_code,
                "is_day": self.is_day,
            }
        }


def _write_weather_context(cache_dir, monkeypatch, *, icon_code, generated_at):
    monkeypatch.setenv("INKYPI_CONTEXT_CACHE_DIR", str(cache_dir))
    weather = Weather({"id": "weather"})
    weather._write_weather_context(
        {
            "title": "Fremont, California",
            "current_temperature": "15",
            "temperature_unit": "°C",
            "feels_like": "15",
            "current_day_icon": weather.get_plugin_dir(f"icons/{icon_code}.png"),
            "forecast": [],
            "data_points": [],
        },
        generated_at,
    )
    return json.loads((cache_dir / "weather.json").read_text(encoding="utf-8"))


def test_weather_context_payload_is_consumed_by_calendar_without_absolute_paths(
    tmp_path, monkeypatch
):
    entry = _write_weather_context(
        tmp_path / "context",
        monkeypatch,
        icon_code="01n",
        generated_at=datetime.now(timezone.utc),
    )

    payload = entry["payload"]
    assert payload["icon_code"] == "01n"
    assert payload["background_slug"] == "clear_night"
    assert not Path(payload["icon_code"]).is_absolute()
    assert SimpleCalendar({"id": "simple_calendar"})._read_weather_context_background_slug() == "clear_night"


def test_calendar_prefers_canonical_weather_context_fields(monkeypatch):
    monkeypatch.setattr(
        "plugins.simple_calendar.simple_calendar.read_contexts",
        lambda *args, **kwargs: [
            {
                "payload": {
                    "background_slug": "rain",
                    "icon_code": "01d",
                    "weather_background_slug": "clear_day",
                    "current_day_icon": "/legacy/icons/01d.png",
                }
            }
        ],
    )

    plugin = SimpleCalendar({"id": "simple_calendar"})

    assert plugin._read_weather_context_background_slug() == "rain"


def test_weather_context_icon_mapping_keeps_partly_cloudy_out_of_clear_sky():
    assert Weather._background_slug_for_icon_code("01d") == "clear_day"
    assert Weather._background_slug_for_icon_code("01n") == "clear_night"
    assert Weather._background_slug_for_icon_code("02d") == "cloudy"


@pytest.mark.parametrize(
    ("icon_code", "expected_slug"),
    [("022d", "clear_day"), ("022n", "clear_night")],
)
def test_weather_context_maps_mainly_clear_icons_to_canonical_background_slug(
    icon_code, expected_slug
):
    assert Weather._background_slug_for_icon_code(icon_code) == expected_slug


def test_calendar_theme_only_uses_compatible_stale_weather_context_without_http(
    tmp_path, monkeypatch
):
    _write_weather_context(
        tmp_path / "context",
        monkeypatch,
        icon_code="13d",
        generated_at=datetime.now(timezone.utc) - timedelta(hours=2, minutes=30),
    )
    session = RecordingSession(weather_code=63)
    monkeypatch.setattr(
        "plugins.simple_calendar.simple_calendar.get_http_session",
        lambda: session,
    )
    plugin = SimpleCalendar({"id": "simple_calendar"})

    path = plugin._get_weather_panel_background_path(
        {
            "_theme_render_only": True,
            "weatherLatitude": "37.5",
            "weatherLongitude": "-122.0",
            "weatherPanelBackgroundStyle": "classic",
        },
        FakeDeviceConfig(),
        date(2026, 7, 11),
    )

    assert session.calls == []
    assert Path(path).name == "snow.png"


def test_calendar_theme_only_refuses_remote_redraw_without_event_snapshot(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("INKYPI_CONTEXT_CACHE_DIR", str(tmp_path / "empty-context"))
    monkeypatch.setenv("INKYPI_DATA_DIR", str(tmp_path / "data"))
    session = RecordingSession(weather_code=0)
    monkeypatch.setattr(
        "plugins.simple_calendar.simple_calendar.get_http_session",
        lambda: session,
    )
    captured = {}
    plugin = SimpleCalendar({"id": "simple_calendar"})

    def capture_render(*args):
        captured["events"] = args[7]
        captured["background_path"] = args[8]
        return "rendered"

    monkeypatch.setattr(plugin, "_render_calendar", capture_render)

    with pytest.raises(RuntimeError, match="event snapshot"):
        plugin.generate_image(
            {
                "_theme_render_only": True,
                "showHolidays": "true",
                "holidayPreset": "custom",
                "holidayCalendarURLs[]": ["https://example.com/holidays.ics"],
                "showPersonalCalendars": "true",
                "personalCalendarURLs[]": ["https://example.com/personal.ics"],
                "weatherLatitude": "37.5",
                "weatherLongitude": "-122.0",
                "weatherPanelFallback": "cloudy",
                "weatherPanelBackgroundStyle": "classic",
            },
            FakeDeviceConfig(),
        )

    assert session.calls == []
    assert captured == {}


def _calendar_theme(mode):
    palettes = {
        "day": {
            "background": (245, 240, 229),
            "panel": (236, 230, 215),
            "ink": (20, 27, 31),
            "muted": (75, 82, 84),
            "rule": (160, 154, 137),
            "accent": (46, 106, 118),
        },
        "night": {
            "background": (11, 21, 24),
            "panel": (18, 32, 36),
            "ink": (236, 243, 239),
            "muted": (172, 187, 181),
            "rule": (72, 91, 87),
            "accent": (105, 185, 197),
        },
    }
    return {"mode": mode, "palette": palettes[mode]}


def _relative_luminance(color):
    channels = []
    for channel in color:
        value = channel / 255
        channels.append(
            value / 12.92
            if value <= 0.04045
            else ((value + 0.055) / 1.055) ** 2.4
        )
    return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2]


def _contrast_ratio(first, second):
    lighter, darker = sorted(
        (_relative_luminance(first), _relative_luminance(second)), reverse=True
    )
    return (lighter + 0.05) / (darker + 0.05)


def test_calendar_canonical_palette_changes_pixels_with_readable_contrast(monkeypatch):
    monkeypatch.setattr(
        "plugins.simple_calendar.simple_calendar.read_contexts", lambda *args, **kwargs: []
    )
    plugin = SimpleCalendar({"id": "simple_calendar"})
    base_settings = {
        "customDate": "2026-07-11",
        "holidayPreset": "off",
        "weatherPanelBackground": "false",
        "dateHeroOverlays": "false",
    }

    day = plugin.generate_image(
        {**base_settings, "_inkypi_theme": _calendar_theme("day")},
        FakeDeviceConfig(),
    )
    night = plugin.generate_image(
        {**base_settings, "_inkypi_theme": _calendar_theme("night")},
        FakeDeviceConfig(),
    )

    assert day.tobytes() != night.tobytes()
    for image, theme in (
        (day, _calendar_theme("day")),
        (night, _calendar_theme("night")),
    ):
        palette = theme["palette"]
        colors = {
            color
            for _count, color in image.getcolors(
                maxcolors=image.width * image.height
            )
        }
        assert palette["background"] in colors
        assert palette["panel"] in colors
        assert palette["ink"] in colors
        assert _contrast_ratio(palette["background"], palette["ink"]) >= 4.5


def test_calendar_normal_render_still_uses_open_meteo_when_context_is_missing(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("INKYPI_CONTEXT_CACHE_DIR", str(tmp_path / "empty-context"))
    session = RecordingSession(weather_code=63)
    monkeypatch.setattr(
        "plugins.simple_calendar.simple_calendar.get_http_session",
        lambda: session,
    )
    plugin = SimpleCalendar({"id": "simple_calendar"})

    path = plugin._get_weather_panel_background_path(
        {
            "weatherLatitude": "37.5",
            "weatherLongitude": "-122.0",
            "weatherPanelBackgroundStyle": "classic",
        },
        FakeDeviceConfig(),
        date(2026, 7, 11),
    )

    assert len(session.calls) == 1
    assert Path(path).name == "rain.png"
