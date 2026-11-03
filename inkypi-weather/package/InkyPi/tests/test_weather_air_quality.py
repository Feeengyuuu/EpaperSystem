import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest
import pytz

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from plugins.weather import air_quality  # noqa: E402
from plugins.weather import weather as weather_module  # noqa: E402
from plugins.weather.weather import Weather  # noqa: E402

LA = pytz.timezone("America/Los_Angeles")


@pytest.mark.parametrize(("aqi", "level", "name"), [
    (0, 0, "Good"), (50, 0, "Good"), (50.4, 0, "Good"), (51, 1, "Moderate"), (100, 1, "Moderate"),
    (101, 2, "Unhealthy for Sensitive Groups"), (151, 3, "Unhealthy"), (201, 4, "Very Unhealthy"),
    (300, 4, "Very Unhealthy"), (301, 5, "Hazardous"), (480, 5, "Hazardous"),
])
def test_us_categories_follow_the_epa_edges(aqi, level, name):
    assert air_quality.category(aqi) == (level, name)


def test_unknown_values_have_no_category():
    for value in (None, "N/A", float("nan"), -3):
        assert air_quality.category(value) == (None, "")
    point = air_quality.data_point(None)
    assert point["measurement"] == "N/A" and point["level"] is None and point["label"] == "Air Quality"


@pytest.mark.parametrize(("pollutant", "concentration", "expected"), [
    ("pm2_5", 4.5, 25), ("pm2_5", 9.0, 50), ("pm2_5", 12.0, 56), ("pm2_5", 35.4, 100), ("pm2_5", 55.5, 151),
    ("pm10", 154, 100), ("o3", 54, 50), ("o3", 71, 101), ("no2", 100, 100), ("co", 9.4, 100),
    ("pm2_5", 400, 500),
])
def test_sub_indices_interpolate_epa_breakpoints(pollutant, concentration, expected):
    assert air_quality.sub_index(pollutant, concentration) == expected


def test_openweathermap_components_give_the_us_index_of_the_worst_pollutant():
    # ug/m3 as OpenWeatherMap reports them: PM2.5 12 -> 56; ozone 92 ug/m3 = 46.9 ppb -> 43.
    result = air_quality.from_components({"pm2_5": 12.0, "pm10": 20.0, "o3": 92.0, "no2": 8.0, "so2": 1.0,
                                          "co": 230.0})
    assert result["aqi"] == 56 and result["dominant"] == "pm2_5"
    assert result["pollutants"]["o3"] == 43
    assert air_quality.from_components({}) == {} and air_quality.from_components(None) == {}


def test_both_providers_report_the_same_us_scale():
    plugin = Weather({"id": "weather"})
    plugin._now = lambda tz: LA.localize(datetime(2026, 11, 2, 16, 20))
    tz = LA

    openweather = plugin.parse_data_points(
        {"current": {"wind_speed": 2, "wind_deg": 90, "humidity": 50, "pressure": 1015, "uvi": 1,
                     "visibility": 10000}},
        {"list": [{"main": {"aqi": 2}, "components": {"pm2_5": 12.0, "o3": 92.0}}]},
        tz, "metric", "24h")
    air = next(point for point in openweather if point["label"] == "Air Quality")
    assert (air["measurement"], air["level"], air["unit"], air["scale"]) == (56, 1, "Moderate", "us")
    assert air_quality.data_point(128)["unit"] == "USG"
    assert air_quality.data_point(128)["category"] == "Unhealthy for Sensitive Groups"

    meteo = plugin.parse_open_meteo_data_points(
        {"current": {"windspeed": 2, "winddirection": 90}, "hourly": {}},
        {"current": {"time": "2026-11-02T16:00", "us_aqi": 53, "us_aqi_pm2_5": 53, "us_aqi_ozone": 30},
         "hourly": {"time": ["2026-11-02T16:00"], "us_aqi": [99], "uv_index": [0.2]}},
        "metric", tz, "24h")
    air = next(point for point in meteo if point["label"] == "Air Quality")
    assert (air["measurement"], air["level"], air["unit"]) == (53, 1, "Moderate")
    assert air["pollutants"] == {"pm2_5": 53, "o3": 30} and air["dominant"] == "pm2_5"

    # Without the current block the hourly US series stands in.
    fallback = plugin.parse_open_meteo_data_points(
        {"current": {}, "hourly": {}},
        {"hourly": {"time": ["2026-11-02T16:00"], "us_aqi": [112], "uv_index": [0.2]}},
        "metric", tz, "24h")
    air = next(point for point in fallback if point["label"] == "Air Quality")
    assert (air["measurement"], air["level"]) == (112, 2)


def test_open_meteo_requests_ask_for_the_us_index_nowcast_and_daily_peaks():
    forecast = parse_qs(urlsplit(weather_module.OPEN_METEO_FORECAST_URL).query)
    assert forecast["minutely_15"] == ["precipitation,precipitation_probability"]
    assert forecast["forecast_minutely_15"] == ["8"]
    for field in ("uv_index_max", "wind_gusts_10m_max", "precipitation_hours"):
        assert field in forecast["daily"][0].split(","), field
    air = parse_qs(urlsplit(weather_module.OPEN_METEO_AIR_QUALITY_URL).query)
    assert air["current"] == ["us_aqi,us_aqi_pm2_5,us_aqi_ozone"]
    assert "us_aqi" in air["hourly"][0] and "european_aqi" not in weather_module.OPEN_METEO_AIR_QUALITY_URL


def test_open_meteo_nowcast_and_daily_peaks_reach_the_template():
    plugin = Weather({"id": "weather"})
    steps = plugin.parse_open_meteo_nowcast({
        "time": ["2026-11-02T16:15", "2026-11-02T16:30", "2026-11-02T16:45"],
        "precipitation": [0.0, 0.3, None], "precipitation_probability": [5, 60, 70],
    }, LA)
    assert steps == [{"time": "16:15", "precipitation": 0.0, "chance": 5},
                     {"time": "16:30", "precipitation": 0.3, "chance": 60}]
    assert plugin.parse_open_meteo_nowcast(None, LA) == []

    forecast = plugin.parse_open_meteo_forecast({
        "time": ["2026-11-02", "2026-11-03"], "weathercode": [3, 61], "temperature_2m_max": [19, 17],
        "temperature_2m_min": [8, 10], "uv_index_max": [2.85, 1.85], "wind_gusts_10m_max": [4.6, 9.0],
        "precipitation_hours": [0.0, 3.0],
    }, "metric", LA, 1, 37.5)
    assert [(day["uv_max"], day["gust_max"], day["rain_hours"]) for day in forecast] == [
        (2.85, 4.6, 0.0), (1.85, 9.0, 3.0)]
