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

    for text in ("日出日落", "西南风 3 级", "空气质量", "紫外线", "湿度", "生活指数", "24 小时", "未来 7 天", "厚外套", "mw-bubble", "mw-effect",
                 "scale(1.0)"):
        assert text in html, text
    # Microsoft YaHei Bold draws U+00B7 as a box in Chromium, so separators are drawn in CSS.
    assert "·" not in html


def test_fun_features_follow_the_weather():
    now = LA.localize(datetime(2026, 10, 24, 9, 0))
    sunny = build_view(_params(now, feels="20"), now, art_dir=str(ART_DIR))

    assert sunny["mascot"]["key"] == "sunny" and sunny["mascot"]["image"].endswith("mascot-sunny.png")
    assert sunny["sun"]["countdown"].startswith("距日落")
    assert sunny["best"] and sunny["best"]["width"] > 0
    assert [index["name"] for index in sunny["indices"]] == ["跑步", "晾晒", "洗车", "户外"]
    assert all(1 <= index["stars"] <= 5 for index in sunny["indices"])
    assert sunny["effect"]["kind"] == "rays" and sunny["effect"]["marks"]

    rainy = build_view(_params(now, rain=0.7, icon="10d"), now, art_dir=str(ART_DIR))
    assert rainy["mascot"]["key"] == "rain" and "下雨" in rainy["mascot"]["line"]
    assert rainy["effect"]["kind"] == "rain"
    drying = next(index for index in rainy["indices"] if index["name"] == "晾晒")
    assert drying["stars"] == 1

    late = LA.localize(datetime(2026, 10, 24, 22, 30))
    night = build_view(_params(late, icon="01n", feels="12"), late, art_dir=str(ART_DIR))
    assert night["mascot"]["key"] == "night" and night["mascot"]["line"].startswith("晚安")
    assert night["sun"]["countdown"].startswith("距日出 8小时")
    assert night["effect"]["kind"] == "stars"


def test_every_weather_has_its_mascot():
    for key in set(modern_view.MASCOTS.values()):
        assert (ART_DIR / f"mascot-{key}.png").is_file(), key


def test_page_effect_is_stable_through_the_day():
    morning = LA.localize(datetime(2026, 10, 24, 8, 0))
    evening = LA.localize(datetime(2026, 10, 24, 17, 0))
    assert modern_view._page_effect("snow", morning) == modern_view._page_effect("snow", evening)


def test_every_card_has_its_icon_and_colour_class():
    now = LA.localize(datetime(2026, 10, 24, 12, 50))
    params = _params(now)
    view = build_view(params, now, art_dir=str(ART_DIR))
    assert set(view["icons"]) == set(modern_view.TILE_ICONS)
    plugin = Weather({"id": "weather"})
    html = plugin.env.get_template("weather_modern.html").render(
        **params, view=view, width=800, height=480, theme={"mode": "day", "css": {}},
        plugin_settings={"displayRefreshTime": "true"}, style_sheets=[], font_faces=[])
    for name in ("sun", "wind", "air", "uv", "humidity", "life", "hourly", "week"):
        assert f"mw-t-{name}" in html and f"icon-{name}.png" in html, name


def test_the_hero_illustration_follows_the_weather_the_hour_wind_and_heat():
    def art(code, night=False, force=0, celsius=18):
        return Path(modern_view._hero_art(str(ART_DIR), code, night, force, celsius)).stem

    assert art("01") == "clear-day" and art("01", celsius=34) == "hot-day" and art("01", night=True) == "clear-night"
    assert art("04", force=7) == "windy-day" and art("02", night=True, force=7) == "windy-night"
    assert art("11") == "thunder" and art("11", night=True) == "thunder-night"
    assert art("53", night=True) == "rain-night" and art("73", night=True) == "snow-night"
    assert art("10") == "sun-shower" and art("50") == "fog-day" and art("56") == "freezing-rain"
    assert modern_view._hero_art(str(ART_DIR), "99", False, 0, 18) == ""


def test_every_hero_illustration_is_installed_with_its_record():
    import json

    names = set(modern_view.HERO_ART.values()) | set(modern_view.HERO_NIGHT.values()) | {"windy", "hot"}
    record = json.loads((ART_DIR / "hero" / "hero.source.json").read_text(encoding="utf-8"))["assets"]
    for name in names:
        candidates = [f"{name}.png", f"{name}-day.png"]
        assert any((ART_DIR / "hero" / file).is_file() and file in record for file in candidates), name


def test_the_template_shows_the_hero_illustration_over_the_plain_icon():
    now = LA.localize(datetime(2026, 10, 24, 12, 0))
    view = build_view(_params(now, icon="11d"), now, art_dir=str(ART_DIR))
    assert Path(view["hero_art"]).name == "thunder.png"
    html = (ART_DIR.parent / "render" / "weather_modern.html").read_text(encoding="utf-8")
    assert "v.hero_art or current_day_icon" in html


def test_a_place_with_its_own_wordmark_shows_it_by_day_and_night():
    now = LA.localize(datetime(2026, 10, 24, 12, 50))
    params = {**_params(now), "title": "Fremont, California"}
    view = build_view(params, now, art_dir=str(ART_DIR))
    assert Path(view["place_mark"]["day"]).name == "fremont-california.png"
    assert Path(view["place_mark"]["night"]).name == "fremont-california-night.png"
    assert build_view({**params, "title": "Oakland, California"}, now, art_dir=str(ART_DIR))["place_mark"] is None

    plugin = Weather({"id": "weather"})
    html = plugin.env.get_template("weather_modern.html").render(
        **params, view=view, width=800, height=480, theme={"mode": "day", "css": {}},
        plugin_settings={}, style_sheets=[], font_faces=[])
    assert "mw-place-day" in html and "fremont-california-night.png" in html
    assert '<div class="mw-place">Fremont, California</div>' not in html
