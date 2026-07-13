import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
import pytz
import requests
from PIL import Image

from plugins.weather import weather as weather_module
from plugins.weather.weather import Weather
from utils import theme_utils
from utils.theme_utils import EFFECTIVE_THEME_CONTEXT_INFO_KEY


class FakeDeviceConfig:
    def __init__(self, config=None):
        self.config = {
            "timezone": "America/Los_Angeles",
            "time_format": "24h",
            "theme_mode": "auto",
            **(config or {}),
        }

    def get_config(self, key=None, default=None):
        if key is None:
            return self.config
        return self.config.get(key, default)

    def get_resolution(self):
        return (64, 32)

    def load_env_key(self, key):
        assert key == "OPEN_WEATHER_MAP_SECRET"
        return "test-key"


def _plugin():
    plugin = Weather.__new__(Weather)
    plugin.config = {"id": "weather"}
    return plugin


def _settings(provider="OpenWeatherMap", **overrides):
    return {
        "latitude": "37.5485",
        "longitude": "-121.9886",
        "units": "metric",
        "weatherProvider": provider,
        "titleSelection": "custom",
        "customTitle": "Fremont, California",
        "weatherTimeZone": "locationTimeZone",
        "themeMode": "auto",
        **overrides,
    }


def _openweather_payload(
    *,
    timezone_name="America/Los_Angeles",
    current_sun=True,
    daily_sun=True,
):
    tz = pytz.timezone(timezone_name)
    noon = tz.localize(datetime(2026, 7, 12, 12, 0))
    sunrise = tz.localize(datetime(2026, 7, 12, 5, 56))
    sunset = tz.localize(datetime(2026, 7, 12, 20, 31))
    daily = {
        "dt": int(noon.timestamp()),
        "weather": [{"icon": "02d"}],
        "temp": {"max": 31, "min": 17},
        "moon_phase": 0.25,
    }
    if daily_sun:
        daily.update(
            {"sunrise": int(sunrise.timestamp()), "sunset": int(sunset.timestamp())}
        )
    current = {
        "dt": int(noon.timestamp()),
        "weather": [{"icon": "02d"}],
        "temp": 25,
        "feels_like": 24,
        "wind_deg": 90,
        "wind_speed": 3,
        "humidity": 40,
        "pressure": 1012,
        "uvi": 4,
        "visibility": 10000,
    }
    if current_sun:
        current.update(
            {"sunrise": int(sunrise.timestamp()), "sunset": int(sunset.timestamp())}
        )
    return {
        "timezone": timezone_name,
        "current": current,
        "daily": [daily],
        "hourly": [],
    }


def _openmeteo_payload(
    *,
    timezone_name="Asia/Tokyo",
    date="2026-07-12",
    sunrise="2026-07-12T04:35",
    sunset="2026-07-12T18:58",
):
    return {
        "timezone": timezone_name,
        "current": {
            "time": f"{date}T12:00",
            "temperature": 27,
            "apparent_temperature": 28,
            "weather_code": 2,
            "is_day": 1,
            "windspeed": 2,
            "winddirection": 180,
        },
        "daily": {
            "time": [date],
            "weathercode": [2],
            "temperature_2m_max": [31],
            "temperature_2m_min": [20],
            "sunrise": [] if sunrise is None else [sunrise],
            "sunset": [] if sunset is None else [sunset],
        },
        "hourly": {
            "time": [f"{date}T12:00"],
            "temperature_2m": [27],
            "precipitation": [0],
            "precipitation_probability": [0],
            "relative_humidity_2m": [55],
            "surface_pressure": [1010],
            "visibility": [10000],
            "weather_code": [2],
        },
    }


def _air_quality_openweather():
    return {"list": [{"main": {"aqi": 1}}]}


def _air_quality_openmeteo(date="2026-07-12"):
    return {
        "hourly": {
            "time": [f"{date}T12:00"],
            "european_aqi": [18],
            "uv_index": [4],
        }
    }


def _fixed_now(plugin, value):
    plugin._now = lambda tz: value.astimezone(tz)


def _capture_render(plugin):
    captured = {}

    def render(dimensions, html_file, css_file, template_params):
        captured.update(template_params)
        captured["_render_args"] = (dimensions, html_file, css_file)
        return Image.new("RGB", dimensions, "black")

    plugin.render_image = render
    return captured


def _install_openweather(plugin, payload=None):
    payload = payload or _openweather_payload()
    plugin.get_weather_data = lambda *_args: payload
    plugin.get_air_quality = lambda *_args: _air_quality_openweather()
    plugin.get_location = lambda *_args: "Fremont, California"
    plugin.parse_forecast = lambda *_args: []
    plugin.parse_hourly = lambda *_args: []


def _install_openmeteo(plugin, payload=None):
    payload = payload or _openmeteo_payload()
    plugin.get_open_meteo_data = lambda *_args, **_kwargs: payload
    plugin.get_open_meteo_air_quality = (
        lambda *_args, **_kwargs: _air_quality_openmeteo(payload["daily"]["time"][0])
    )
    plugin.parse_open_meteo_forecast = lambda *_args: []
    plugin.parse_open_meteo_hourly = lambda *_args: []


def _sun_points(template_params):
    return {
        point["label"]: (point["measurement"], point["unit"])
        for point in template_params["data_points"]
        if point["label"] in {"Sunrise", "Sunset"}
    }


def test_weather_success_publishes_astronomy_before_resolve_and_render(
    monkeypatch,
):
    plugin = _plugin()
    _install_openweather(plugin)
    _fixed_now(
        plugin,
        datetime(2026, 7, 12, 12, 0, tzinfo=timezone(timedelta(hours=-7))),
    )
    calls = []

    def write_context(*_args, **_kwargs):
        calls.append("write_context")
        return True

    def resolve_theme(*_args, **kwargs):
        calls.append("resolve_theme")
        assert kwargs["astronomy"]["timezone"] == "America/Los_Angeles"
        return {
            "requested_mode": "auto",
            "mode": "day",
            "source": "weather",
            "reason": "sunrise/sunset",
            **kwargs["astronomy"],
            "palette": {},
            "css": {},
        }

    def render(*_args, **_kwargs):
        calls.append("render_image")
        return Image.new("RGB", (64, 32), "white")

    monkeypatch.setattr(weather_module, "write_context", write_context)
    plugin.resolve_theme = resolve_theme
    plugin.render_image = render

    image = plugin.generate_image(_settings(), FakeDeviceConfig())

    assert calls == ["write_context", "resolve_theme", "render_image"]
    assert image.info[EFFECTIVE_THEME_CONTEXT_INFO_KEY]["mode"] == "day"


def test_weather_context_write_failure_aborts_before_render(monkeypatch):
    plugin = _plugin()
    _install_openweather(plugin)
    _fixed_now(
        plugin,
        datetime(2026, 7, 12, 12, 0, tzinfo=timezone(timedelta(hours=-7))),
    )
    monkeypatch.setattr(weather_module, "write_context", lambda *_a, **_k: False)
    plugin.resolve_theme = lambda *_a, **_k: pytest.fail("resolved after failed write")
    plugin.render_image = lambda *_a, **_k: pytest.fail("rendered after failed write")

    with pytest.raises(RuntimeError, match="context"):
        plugin.generate_image(_settings(), FakeDeviceConfig())


@pytest.mark.parametrize("current_sun", [True, False], ids=["current", "daily-fallback"])
def test_openweather_astronomy_and_visible_metrics_are_identical(
    monkeypatch,
    current_sun,
):
    plugin = _plugin()
    _install_openweather(
        plugin,
        _openweather_payload(current_sun=current_sun, daily_sun=True),
    )
    _fixed_now(
        plugin,
        datetime(2026, 7, 12, 12, 0, tzinfo=timezone(timedelta(hours=-7))),
    )
    rendered = _capture_render(plugin)
    published = {}
    monkeypatch.setattr(
        weather_module,
        "write_context",
        lambda _plugin_id, payload, **_kwargs: published.update(payload) or True,
    )

    plugin.generate_image(_settings(), FakeDeviceConfig())

    astronomy = published["astronomy"]
    assert astronomy == rendered["astronomy"]
    assert astronomy == {
        "source": "weather",
        "date": "2026-07-12",
        "timezone": "America/Los_Angeles",
        "sunrise": "2026-07-12T05:56:00-07:00",
        "sunset": "2026-07-12T20:31:00-07:00",
    }
    assert _sun_points(rendered) == {
        "Sunrise": ("05:56", ""),
        "Sunset": ("20:31", ""),
    }


def test_openweather_stale_cache_keeps_original_age_and_omits_astronomy(
    monkeypatch,
    tmp_path,
):
    plugin = _plugin()
    monkeypatch.setenv("OPENWEATHER_CACHE_DIR", str(tmp_path))
    url = "https://example.test/weather"
    path = plugin._cache_path_for_url(str(tmp_path), "onecall", url)
    original_fetched_at = "2026-07-10T12:00:00+00:00"
    payload = _openweather_payload()
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(
            {
                "fetched_at": original_fetched_at,
                "stale": False,
                "data": payload,
            },
            handle,
        )
    monkeypatch.setattr(
        weather_module,
        "get_http_session",
        lambda: SimpleNamespace(
            get=lambda *_args, **_kwargs: (_ for _ in ()).throw(
                requests.ConnectionError("offline")
            )
        ),
    )

    assert plugin._request_openweather_json(url, "onecall", 0) == payload
    saved = json.loads(open(path, encoding="utf-8").read())
    assert saved["fetched_at"] == original_fetched_at
    assert saved["stale"] is True
    assert plugin._openweather_request_metadata["onecall"] == {
        "fetched_at": original_fetched_at,
        "stale": True,
    }

    def stale_weather(*_args):
        plugin._openweather_request_metadata["onecall"] = {
            "fetched_at": original_fetched_at,
            "stale": True,
        }
        return payload

    plugin.get_weather_data = stale_weather
    plugin.get_air_quality = lambda *_args: _air_quality_openweather()
    plugin.parse_forecast = lambda *_args: []
    plugin.parse_hourly = lambda *_args: []
    _fixed_now(
        plugin,
        datetime(2026, 7, 12, 12, 0, tzinfo=timezone(timedelta(hours=-7))),
    )
    rendered = _capture_render(plugin)
    published = {}
    monkeypatch.setattr(
        weather_module,
        "write_context",
        lambda _plugin_id, context, **_kwargs: published.update(context) or True,
    )

    plugin.generate_image(_settings(), FakeDeviceConfig())

    assert "astronomy" not in published
    assert _sun_points(rendered) == {}
    assert rendered["theme"]["source"] == "fallback"


def test_openmeteo_location_mode_uses_response_iana_timezone(monkeypatch):
    plugin = _plugin()
    payload = _openmeteo_payload(timezone_name="Asia/Tokyo")
    requested = []
    plugin.get_open_meteo_data = (
        lambda *_args, **kwargs: requested.append(("forecast", kwargs)) or payload
    )
    plugin.get_open_meteo_air_quality = (
        lambda *_args, **kwargs: requested.append(("air", kwargs))
        or _air_quality_openmeteo()
    )
    plugin.parse_open_meteo_forecast = lambda *_args: []
    plugin.parse_open_meteo_hourly = lambda *_args: []
    _fixed_now(
        plugin,
        datetime(2026, 7, 12, 12, 0, tzinfo=timezone(timedelta(hours=9))),
    )
    rendered = _capture_render(plugin)
    published = {}
    monkeypatch.setattr(
        weather_module,
        "write_context",
        lambda _plugin_id, context, **_kwargs: published.update(context) or True,
    )

    plugin.generate_image(
        _settings("OpenMeteo", weatherTimeZone="locationTimeZone"),
        FakeDeviceConfig({"timezone": "America/Los_Angeles"}),
    )

    assert requested == [
        ("forecast", {"timezone_name": "auto"}),
        ("air", {"timezone_name": "auto"}),
    ]
    assert published["astronomy"]["timezone"] == "Asia/Tokyo"
    assert published["astronomy"] == rendered["astronomy"]
    assert published["astronomy"]["sunrise"].endswith("+09:00")


def test_openmeteo_configured_mode_requests_and_uses_device_iana_timezone(
    monkeypatch,
):
    plugin = _plugin()
    payload = _openmeteo_payload(
        timezone_name="America/New_York",
        sunrise="2026-07-12T05:36",
        sunset="2026-07-12T20:27",
    )
    requested = []
    plugin.get_open_meteo_data = (
        lambda *_args, **kwargs: requested.append(("forecast", kwargs)) or payload
    )
    plugin.get_open_meteo_air_quality = (
        lambda *_args, **kwargs: requested.append(("air", kwargs))
        or _air_quality_openmeteo()
    )
    plugin.parse_open_meteo_forecast = lambda *_args: []
    plugin.parse_open_meteo_hourly = lambda *_args: []
    _fixed_now(
        plugin,
        datetime(2026, 7, 12, 12, 0, tzinfo=timezone(timedelta(hours=-4))),
    )
    rendered = _capture_render(plugin)
    published = {}
    monkeypatch.setattr(
        weather_module,
        "write_context",
        lambda _plugin_id, context, **_kwargs: published.update(context) or True,
    )

    plugin.generate_image(
        _settings("OpenMeteo", weatherTimeZone="configuredTimeZone"),
        FakeDeviceConfig({"timezone": "America/New_York"}),
    )

    assert requested == [
        ("forecast", {"timezone_name": "America/New_York"}),
        ("air", {"timezone_name": "America/New_York"}),
    ]
    assert published["astronomy"] == rendered["astronomy"]
    assert published["astronomy"]["timezone"] == "America/New_York"
    assert published["astronomy"]["sunrise"].endswith("-04:00")
    assert _sun_points(rendered)["Sunrise"] == ("05:36", "")


def test_openmeteo_naive_times_are_localized_not_assumed_system_timezone():
    plugin = _plugin()
    astronomy = plugin.parse_open_meteo_astronomy(
        {
            "sunrise": ["2026-11-01T06:25"],
            "sunset": ["2026-11-01T16:52"],
        },
        pytz.timezone("America/New_York"),
    )

    assert astronomy["sunrise"] == "2026-11-01T06:25:00-05:00"
    assert astronomy["sunset"] == "2026-11-01T16:52:00-05:00"


def test_polar_weather_omits_both_sun_metrics_and_uses_fallback_theme(
    monkeypatch,
):
    plugin = _plugin()
    payload = _openmeteo_payload(
        timezone_name="Arctic/Longyearbyen",
        sunrise=None,
        sunset=None,
    )
    _install_openmeteo(plugin, payload)
    _fixed_now(
        plugin,
        datetime(2026, 7, 12, 12, 0, tzinfo=timezone(timedelta(hours=2))),
    )
    rendered = _capture_render(plugin)
    published = {}
    monkeypatch.setattr(
        weather_module,
        "write_context",
        lambda _plugin_id, context, **_kwargs: published.update(context) or True,
    )

    image = plugin.generate_image(
        _settings("OpenMeteo"),
        FakeDeviceConfig({"timezone": "Europe/Oslo"}),
    )

    assert "astronomy" not in published
    assert _sun_points(rendered) == {}
    assert rendered["theme"]["source"] == "fallback"
    assert rendered["theme"]["sunrise"] is None
    assert rendered["theme"]["sunset"] is None
    assert image.info[EFFECTIVE_THEME_CONTEXT_INFO_KEY]["source"] == "fallback"


def test_openweather_malformed_current_sun_keeps_weather_refresh_healthy(
    monkeypatch,
):
    plugin = _plugin()
    payload = _openweather_payload()
    payload["current"]["sunrise"] = "not-an-epoch"
    plugin.get_weather_data = lambda *_args: payload
    plugin.get_air_quality = lambda *_args: _air_quality_openweather()
    _fixed_now(
        plugin,
        datetime(2026, 7, 12, 12, 0, tzinfo=timezone(timedelta(hours=-7))),
    )
    rendered = _capture_render(plugin)
    published = {}
    monkeypatch.setattr(
        weather_module,
        "write_context",
        lambda _plugin_id, context, **_kwargs: published.update(context) or True,
    )

    image = plugin.generate_image(_settings(), FakeDeviceConfig())

    assert "current 25°C" in published["summary"]
    assert "astronomy" not in published
    assert _sun_points(rendered) == {}
    assert {fact["label"] for fact in published["facts"]}.isdisjoint(
        {"Sunrise", "Sunset"}
    )
    assert rendered["theme"]["source"] == "fallback"
    assert image.info[EFFECTIVE_THEME_CONTEXT_INFO_KEY]["source"] == "fallback"


def test_openmeteo_malformed_daily_sun_keeps_weather_refresh_healthy(
    monkeypatch,
):
    plugin = _plugin()
    payload = _openmeteo_payload(sunrise="not-a-local-time")
    plugin.get_open_meteo_data = lambda *_args, **_kwargs: payload
    plugin.get_open_meteo_air_quality = (
        lambda *_args, **_kwargs: _air_quality_openmeteo()
    )
    _fixed_now(
        plugin,
        datetime(2026, 7, 12, 12, 0, tzinfo=timezone(timedelta(hours=9))),
    )
    rendered = _capture_render(plugin)
    published = {}
    monkeypatch.setattr(
        weather_module,
        "write_context",
        lambda _plugin_id, context, **_kwargs: published.update(context) or True,
    )

    image = plugin.generate_image(
        _settings("OpenMeteo"),
        FakeDeviceConfig({"timezone": "Asia/Tokyo"}),
    )

    assert "current 27°C" in published["summary"]
    assert "astronomy" not in published
    assert _sun_points(rendered) == {}
    assert {fact["label"] for fact in published["facts"]}.isdisjoint(
        {"Sunrise", "Sunset"}
    )
    assert rendered["theme"]["source"] == "fallback"
    assert image.info[EFFECTIVE_THEME_CONTEXT_INFO_KEY]["source"] == "fallback"


def test_openweather_polar_payload_keeps_hourly_and_weather_refresh_healthy(
    monkeypatch,
):
    plugin = _plugin()
    payload = _openweather_payload(current_sun=False, daily_sun=False)
    payload["hourly"] = [
        {
            "dt": payload["current"]["dt"],
            "temp": 25,
            "pop": 0,
            "weather": [{"icon": "01d"}],
        }
    ]
    plugin.get_weather_data = lambda *_args: payload
    plugin.get_air_quality = lambda *_args: _air_quality_openweather()
    _fixed_now(
        plugin,
        datetime(2026, 7, 12, 12, 0, tzinfo=timezone(timedelta(hours=-7))),
    )
    rendered = _capture_render(plugin)
    published = {}
    monkeypatch.setattr(
        weather_module,
        "write_context",
        lambda _plugin_id, context, **_kwargs: published.update(context) or True,
    )

    image = plugin.generate_image(_settings(), FakeDeviceConfig())

    assert "current 25°C" in published["summary"]
    assert "astronomy" not in published
    assert _sun_points(rendered) == {}
    assert rendered["hourly_forecast"][0]["icon"].endswith("01n.png")
    assert rendered["theme"]["source"] == "fallback"
    assert image.info[EFFECTIVE_THEME_CONTEXT_INFO_KEY]["source"] == "fallback"


def test_weather_render_replaces_queued_pin_with_fresh_effective_context(
    monkeypatch,
):
    plugin = _plugin()
    _install_openweather(plugin)
    _fixed_now(
        plugin,
        datetime(2026, 7, 12, 21, 0, tzinfo=timezone(timedelta(hours=-7))),
    )
    monkeypatch.setattr(weather_module, "write_context", lambda *_a, **_k: True)
    observed = {}

    def render(dimensions, _html_file, _css_file, template_params):
        observed["template"] = template_params["theme"]
        observed["nested"] = theme_utils.get_theme_context(FakeDeviceConfig())
        return Image.new("RGB", dimensions, "black")

    plugin.render_image = render
    queued_day = {
        "requested_mode": "auto",
        "mode": "day",
        "source": "fallback",
        "reason": "queued before fresh weather",
        "palette": {},
        "css": {},
    }

    image = plugin.render_themed_image(
        _settings(),
        FakeDeviceConfig(),
        resolved_theme_context=queued_day,
    )

    assert observed["template"]["mode"] == "night"
    assert observed["nested"]["mode"] == "night"
    assert image.info["inkypi_theme_mode"] == "night"
    assert theme_utils.get_theme_context(
        FakeDeviceConfig({"theme_mode": "day"})
    )["mode"] == "day"
    assert theme_utils.get_theme_context(
        FakeDeviceConfig({"theme_mode": "night"})
    )["mode"] == "night"


def test_weather_success_fetches_each_endpoint_once(monkeypatch):
    plugin = _plugin()
    payload = _openweather_payload()
    calls = {"weather": 0, "air": 0, "location": 0}

    def weather(*_args):
        calls["weather"] += 1
        return payload

    def air(*_args):
        calls["air"] += 1
        return _air_quality_openweather()

    def location(*_args):
        calls["location"] += 1
        return "Fremont, California"

    plugin.get_weather_data = weather
    plugin.get_air_quality = air
    plugin.get_location = location
    plugin.parse_forecast = lambda *_args: []
    plugin.parse_hourly = lambda *_args: []
    _fixed_now(
        plugin,
        datetime(2026, 7, 12, 12, 0, tzinfo=timezone(timedelta(hours=-7))),
    )
    _capture_render(plugin)
    monkeypatch.setattr(weather_module, "write_context", lambda *_a, **_k: True)

    plugin.generate_image(
        _settings(titleSelection="location"),
        FakeDeviceConfig(),
    )

    assert calls == {"weather": 1, "air": 1, "location": 1}
