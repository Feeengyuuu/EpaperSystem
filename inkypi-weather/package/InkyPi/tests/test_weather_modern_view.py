import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytz

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from plugins.weather import modern_view  # noqa: E402
from plugins.weather.modern_view import build_view  # noqa: E402
from plugins.weather.weather import Weather  # noqa: E402

LA = pytz.timezone("America/Los_Angeles")
ART_DIR = Path(__file__).resolve().parents[1] / "src" / "plugins" / "weather" / "art"


def _params(now, *, rain=0.0, icon="01d", aqi_level=1, feels="9"):
    sunrise = now.replace(hour=7, minute=25)
    sunset = now.replace(hour=18, minute=18)
    hourly = []
    for index in range(24):
        moment = now.replace(minute=0) + timedelta(hours=index)
        hourly.append({"time": moment.strftime("%H:%M"), "temperature": 10 + (index % 12),
                       "precipitation": rain if index == 5 else 0.0, "rain": 0.0, "icon": f"icons/{icon}.png"})
    forecast = [{"day": day, "high": 20 + index, "low": 8 + index, "icon": "icons/04d.png",
                 "moon_phase_pct": "97", "moon_phase_icon": "icons/waxinggibbous.png"}
                for index, day in enumerate(("Sat", "Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"))]
    return {
        "title": "Fremont", "current_day_icon": f"icons/{icon}.png", "current_temperature": "11",
        "feels_like": feels, "temperature_unit": "°C", "units": "metric", "last_refresh_time": "2026-10-24 08:42",
        "astronomy": {"sunrise": sunrise.isoformat(), "sunset": sunset.isoformat()},
        "forecast": forecast, "hourly_forecast": hourly,
        "data_points": [
            {"label": "Wind", "measurement": 4.2, "unit": "m/s", "degrees": 225},
            {"label": "Humidity", "measurement": 55, "unit": "%"},
            {"label": "Pressure", "measurement": 1014, "unit": "hPa"},
            {"label": "UV Index", "measurement": 6.4, "unit": ""},
            {"label": "Visibility", "measurement": "≥10.0", "unit": "km"},
            {"label": "Air Quality", "measurement": 2, "unit": "Fair", "level": aqi_level},
        ],
    }


def test_view_reads_conditions_tips_and_gauges():
    now = LA.localize(datetime(2026, 10, 24, 12, 50))
    view = build_view(_params(now), now, art_dir=str(ART_DIR))

    assert view["date"] == "10月24日 周六"
    assert view["condition"] == "晴" and view["scene"] == "clear-day"
    assert view["scene_image"].endswith("clear-day.jpg")
    assert view["tips"] == ["厚外套", "不用带伞", "注意防晒"]
    assert view["summary"].endswith("未来 24 小时无雨")
    assert view["wind"]["name"] == "西南风 3 级" and view["wind"]["heading"] == 45
    assert view["aqi"]["label"] == "良" and view["aqi"]["ink"] == "#111418"
    assert len(view["aqi"]["segments"]) == 5
    assert view["uv"]["label"] == "强" and 0 < view["uv"]["marker"] < 100
    assert view["humidity"] == {"value": 55, "feel": "舒适", "fill": 55.0}
    assert 0.4 < view["sun"]["progress"] < 0.6 and not view["sun"]["night"]
    assert view["sun"]["daylight"] == "10小时53分"


def test_rain_ahead_changes_summary_and_umbrella_tip():
    now = LA.localize(datetime(2026, 10, 24, 12, 0))
    view = build_view(_params(now, rain=0.6, icon="10d"), now)

    assert "可能有雨（60%）" in view["summary"]
    assert "记得带伞" in view["tips"]
    assert view["scene"] == "rain"
    assert view["hourly"]["any_rain"] is True
    assert any(point["rain_height"] > 0 for point in view["hourly"]["points"])


def test_night_uses_night_scene_moon_and_bands():
    now = LA.localize(datetime(2026, 10, 24, 21, 0))
    view = build_view(_params(now, icon="02n"), now, art_dir=str(ART_DIR))

    assert view["sun"]["night"] is True
    assert view["scene"] == "cloudy-night" and view["scene_image"].endswith("cloudy-night.jpg")
    assert view["moon"]["name"] == "盈凸月"
    bands = view["hourly"]["night_bands"]
    assert bands and bands[0]["x"] == 0.0
    # Night from 21:00 until the next sunrise at 07:25, then day.
    assert view["hourly"]["points"][0]["night"] and not view["hourly"]["points"][12]["night"]


def test_hourly_curve_and_week_bars_stay_in_their_boxes():
    now = LA.localize(datetime(2026, 10, 24, 9, 0))
    view = build_view(_params(now), now)
    hourly = view["hourly"]

    assert hourly["path"].startswith("M") and hourly["path"].count("C") == 23
    for point in hourly["points"]:
        assert 0 <= point["x"] <= hourly["width"]
        assert modern_view.CURVE_TOP <= point["y"] <= modern_view.CURVE_BOTTOM
    assert [point["label"] for point in hourly["points"][:4]] == [True, False, False, True]
    week = view["week"]
    assert [day["name"] for day in week[:2]] == ["明天", "周一"] and len(week) == 7
    for day in week:
        assert 0 <= day["bar_left"] <= 100 and day["bar_left"] + day["bar_width"] <= 104


def test_every_scene_has_its_art():
    scenes = {f"{scene}-{part}" if scene in modern_view.SCENES_WITH_NIGHT else scene
              for _label, scene in modern_view.CONDITIONS.values() for part in ("day", "night")}
    for scene in scenes:
        assert (ART_DIR / f"{scene}.jpg").is_file(), scene


def test_modern_template_renders_every_section():
    now = LA.localize(datetime(2026, 10, 24, 12, 50))
    params = _params(now)
    plugin = Weather({"id": "weather"})
    template = plugin.env.get_template("weather_modern.html")
    html = template.render(**params, view=build_view(params, now), width=800, height=480, theme={"mode": "day", "css": {}},
                           plugin_settings={"displayRefreshTime": "true"}, style_sheets=[], font_faces=[])

    for text in ("日出日落", "西南风 3 级", "空气质量", "紫外线", "湿度", "24 小时", "未来 7 天", "厚外套",
                 "scale(1.0)"):
        assert text in html, text
