import json
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from plugins.simple_calendar.simple_calendar import SimpleCalendar
from plugins.weather.weather import Weather


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


def test_calendar_theme_only_skips_remote_ics_and_weather_http(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("INKYPI_CONTEXT_CACHE_DIR", str(tmp_path / "empty-context"))
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

    result = plugin.generate_image(
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

    assert result == "rendered"
    assert session.calls == []
    assert captured["events"] == []
    assert Path(captured["background_path"]).name == "cloudy.png"


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
