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
            {"label": "Air Quality", "measurement": (25, 62, 128, 172, 240, 340)[aqi_level], "unit": "Moderate",
             "level": aqi_level, "scale": "us", "pollutants": {"pm2_5": 62, "o3": 30}, "dominant": "pm2_5"},
        ],
    }


def test_view_reads_conditions_tips_and_gauges():
    now = LA.localize(datetime(2026, 10, 24, 12, 50))
    view = build_view(_params(now), now, art_dir=str(ART_DIR))

    assert view["date"] == "10月24日 周六"
    assert view["condition"] == "晴" and view["scene"] == "clear-day"
    assert view["scene_image"].endswith("clear-day.jpg")
    assert view["tips"] == ["厚外套", "不用带伞", "注意防晒"]
    assert view["summary"][-1] == {"icon": "umbrella-off", "text": "24 小时无雨"}
    # Each tip's pill: its icon and the words it still needs.
    assert [(mark["icon"], mark["label"]) for mark in view["tip_marks"]] == [
        ("coat", "厚外套"), ("umbrella-off", "不用伞"), ("shield", "防晒")]
    assert view["wind"]["name"] == "西南风 3 级" and view["wind"]["heading"] == 45
    assert view["aqi"]["label"] == "中等" and view["aqi"]["ink"] == "#111418"
    assert view["aqi"]["value"] == 62 and view["aqi"]["main"] == "PM2.5" and not view["aqi"]["long"]
    # One band per US category.
    assert len(view["aqi"]["segments"]) == 6
    assert view["uv"]["label"] == "强" and 0 < view["uv"]["marker"] < 100
    assert view["humidity"] == {"value": 55, "feel": "舒适", "fill": 55.0}
    assert 0.4 < view["sun"]["progress"] < 0.6 and not view["sun"]["night"]
    assert view["sun"]["daylight"] == "10小时53分"


def test_rain_ahead_changes_summary_and_umbrella_tip():
    now = LA.localize(datetime(2026, 10, 24, 12, 0))
    view = build_view(_params(now, rain=0.6, icon="10d"), now)

    # Rain first, as it comes first; the warmest hour after it.
    assert view["summary"] == [{"icon": "rain", "text": "17:00 起 60%"}, {"icon": "warm", "text": "23:00 21°"}]
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
    # A dry day lets the curve take the rain bars' band as well.
    assert hourly["curve_bottom"] == modern_view.DRY_CURVE_BOTTOM
    for point in hourly["points"]:
        assert 0 <= point["x"] <= hourly["width"]
        assert modern_view.CURVE_TOP <= point["y"] <= hourly["curve_bottom"]
    assert build_view(_params(now, rain=0.5), now)["hourly"]["curve_bottom"] == modern_view.CURVE_BOTTOM
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


def test_an_evening_view_names_tomorrows_hours_as_tomorrow():
    now = LA.localize(datetime(2026, 10, 24, 17, 42))
    params = _params(now)
    params["current_temperature"] = "20"
    params["data_points"][3]["measurement"] = 0.4
    view = build_view(params, now, art_dir=str(ART_DIR))

    # Tomorrow's 09:00-16:00 is the best time out, said as tomorrow's everywhere it shows.
    assert (view["best"]["day"], view["best"]["start"], view["best"]["end"]) == ("明天 ", "09:00", "16:00")
    assert view["mascot"]["line"] == "明天 09:00 后出门正好！"
    assert view["summary"][0] == {"icon": "warm", "text": "明天 04:00 21°"}
    points = view["hourly"]["points"]
    assert points[6]["when"] == "23:00" and points[7]["when"] == "明天 00:00"
    assert view["hourly"]["midnight"]["x"] == points[7]["x"]
    # 36 minutes to sunset reads in minutes; a fall to 10° overnight replaces the sun-cream tip.
    assert view["sun"]["countdown"] == "距日落 36分钟"
    assert view["tips"][-1] == "夜间低至 10°" and view["tip_marks"][-1] == {"icon": "chill", "label": "夜间 10°"}

    plugin = Weather({"id": "weather"})
    html = plugin.env.get_template("weather_modern.html").render(
        **params, view=view, width=800, height=480, theme={"mode": "day", "css": {}},
        plugin_settings={}, style_sheets=[], font_faces=[])
    chip = html.split('<span class="mw-best-chip">', 1)[1].split("</span>", 1)[0]
    assert "</svg>明天 09:00–16:00" in chip and "mw-midnight" in html


def test_the_summary_says_tomorrow_once_and_the_heading_keeps_one_line():
    now = LA.localize(datetime(2026, 10, 31, 17, 42))
    params = _params(now)
    params["hourly_forecast"][8]["precipitation"] = 0.63
    view = build_view(params, now, art_dir=str(ART_DIR))

    # Rain at 01:00 and the warmest hour at 04:00 are both tomorrow's: "明天" leads, once.
    assert [part["text"] for part in view["summary"]] == ["明天 01:00 起 63%", "04:00 21°"]
    plugin = Weather({"id": "weather"})
    html = plugin.env.get_template("weather_modern.html").render(
        **params, view=view, width=800, height=480, theme={"mode": "day", "css": {}},
        plugin_settings={}, style_sheets=[], font_faces=[])
    assert '<span class="mw-section-name">24 小时</span>' in html and "mw-hours-title" in html
    assert "63%<span class=\"mw-dot mw-note-dot\"" in html and "·" not in html


def test_readings_the_icons_name_drop_their_words():
    now = LA.localize(datetime(2026, 10, 24, 12, 50))
    params = _params(now)
    view = build_view(params, now, art_dir=str(ART_DIR))
    html = Weather({"id": "weather"}).env.get_template("weather_modern.html").render(
        **params, view=view, width=800, height=480, theme={"mode": "day", "css": {}},
        plugin_settings={"displayRefreshTime": "true"}, style_sheets=[], font_faces=[])

    # A gauge and an eye say pressure and visibility, a sun the daylight, the arrows sunrise and set.
    for word in ("气压", "能见度", "白昼", "更新", "↑", "↓", "最佳出门"):
        assert word not in html, word
    assert html.count('class="mw-g ') >= 15
    assert [index["icon"] for index in view["indices"]] == ["run", "laundry", "car", "outdoor"]


def test_wind_and_visibility_read_plainly():
    now = LA.localize(datetime(2026, 10, 24, 12, 50))
    view = build_view(_params(now), now)
    assert (view["wind"]["beaufort"], view["wind"]["sign"]) == ("微风", "旗帜展开")
    assert view["visibility"] == {"value": "≥10", "unit": "km"}
    assert modern_view._visibility({"measurement": "≥32.9", "unit": "km"})["value"] == "≥33"
    assert modern_view._visibility({"measurement": 6.4, "unit": "mi"})["value"] == 6.4


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


def test_every_card_has_its_own_line_drawing_in_its_colour():
    import json

    record = json.loads((ART_DIR / "doodles" / "doodles.source.json").read_text(encoding="utf-8"))["assets"]
    for name in modern_view.CARD_DOODLES:
        assert (ART_DIR / "doodles" / f"{name}.png").is_file() and f"{name}.png" in record, name

    now = LA.localize(datetime(2026, 10, 24, 12, 50))
    params = _params(now)
    view = build_view(params, now, art_dir=str(ART_DIR))
    assert set(view["doodles"]) == set(modern_view.CARD_DOODLES)
    assert all("\\" not in path for path in view["doodles"].values())
    html = Weather({"id": "weather"}).env.get_template("weather_modern.html").render(
        **params, view=view, width=800, height=480, theme={"mode": "day", "css": {}},
        plugin_settings={}, style_sheets=[], font_faces=[])
    # By day the sun card carries the sunrise drawing; every other card its own.
    for name in ("sun", "wind", "air", "uv", "humidity", "life", "hourly", "week"):
        assert f'mask="url(#mw-dm-{name})"' in html and f"doodles/{name}.png" in html, name
    assert "doodles/moon.png" not in html
    assert build_view(params, now)["doodles"] == {}


def test_the_night_hours_are_a_window_onto_a_night_sky():
    import json
    import re

    record = json.loads((ART_DIR / "generated_art.source.json").read_text(encoding="utf-8"))["assets"]
    assert (ART_DIR / modern_view.NIGHT_SKY).is_file() and modern_view.NIGHT_SKY in record
    now = LA.localize(datetime(2026, 10, 24, 12, 50))
    params = _params(now)
    view = build_view(params, now, art_dir=str(ART_DIR))
    bands = view["hourly"]["night_bands"]
    assert bands and "\\" not in view["night_sky"] and view["night_sky"].endswith("night-sky.jpg")
    html = Weather({"id": "weather"}).env.get_template("weather_modern.html").render(
        **params, view=view, width=800, height=480, theme={"mode": "day", "css": {}},
        plugin_settings={}, style_sheets=[], font_faces=[])
    # Each band shows the sky from its own left edge (the moon on the dusk side), dissolving into
    # the card at its sides, top and foot rather than clipped to a box.
    skies = re.findall(r'<image href="[^"]*night-sky\.jpg" x="([\d.]+)"[^>]*xMinYMid slice', html)
    assert [float(x) for x in skies] == [band["x"] for band in bands]
    assert "clip-path" not in html.split("mw-chart", 1)[1].split("</svg>", 1)[0]
    for index, band in enumerate(bands, 1):
        assert 0 < band["fade"] <= modern_view.NIGHT_SKY_MAX_FADE and band["fade_end"] == round(1 - band["fade"], 3)
        assert f'<g mask="url(#mw-night-sides-{index})">' in html
        # Eased in from each side: transparent at the edge, opaque a fade's width in.
        assert band["stops"][0] == (0.0, 0.0) and band["stops"][-1] == (1.0, 0.0)
        assert (band["fade"], 1.0) in band["stops"] and (band["fade_end"], 1.0) in band["stops"]
        assert f'<stop offset="{band["fade"]}" stop-color="#fff" stop-opacity="1.0"/>' in html
    assert html.count('mask="url(#mw-night-ends)"') == len(bands)
    assert 'class="mw-night"' not in html
    # Labels over the sky turn light; those by day stay in ink.
    night_times = [point["time"] for point in view["hourly"]["points"] if point["label"] and point["night"]]
    day_times = [point["time"] for point in view["hourly"]["points"][1:] if point["label"] and not point["night"]]
    assert night_times and day_times
    assert all(f'class="mw-time mw-on-sky">{time}<' in html for time in night_times)
    assert all(f'class="mw-time">{time}<' in html for time in day_times)
    # Without the picture the bands stay a flat tint.
    plain = build_view(params, now)
    assert plain["night_sky"] == ""


def test_the_heros_readings_are_each_led_by_an_icon():
    now = LA.localize(datetime(2026, 10, 24, 12, 50))
    params = _params(now)
    view = build_view(params, now, art_dir=str(ART_DIR))
    html = Weather({"id": "weather"}).env.get_template("weather_modern.html").render(
        **params, view=view, width=800, height=480, theme={"mode": "day", "css": {}},
        plugin_settings={}, style_sheets=[], font_faces=[])
    condition = html.split('<div class="mw-condition">', 1)[1].split("</div>", 1)[0]
    # The sky now before the condition, a thermometer before the feel; arrows before high and low.
    assert 'class="mw-cond-icon"' in condition and condition.index("mw-cond-icon") < condition.index(view["condition"])
    assert condition.index("mw-mini-feels") < condition.index("体感")
    readings = html.split('<div class="mw-range">', 1)[1].split("</div>", 1)[0]
    # The arrows say high and low: the readings are the degrees alone.
    assert readings.index("mw-mini-high") < readings.index(f">{view['high']}°") < readings.index("mw-mini-low")
    assert "最高" not in readings and "最低" not in readings


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


def test_us_aqi_categories_set_the_dial_chip_and_advice():
    now = LA.localize(datetime(2026, 10, 24, 12, 50))
    labels = []
    for level in range(6):
        view = build_view(_params(now, aqi_level=level), now)
        labels.append(view["aqi"]["label"])
        assert view["aqi"]["level"] == level
    assert labels == ["良好", "中等", "对敏感人群不健康", "不健康", "非常不健康", "危险"]

    sensitive = build_view(_params(now, aqi_level=2), now)
    assert sensitive["aqi"]["long"] and sensitive["aqi"]["ink"] == "#111418"
    assert sensitive["mascot"]["line"] == "空气不好，少开窗"
    assert build_view(_params(now, aqi_level=3), now)["aqi"]["ink"] == "#ffffff"
    # The needle moves with the value inside its band: 128 sits right of 101.
    low = dict(_params(now, aqi_level=2))
    low["data_points"] = [dict(point, measurement=101) if point["label"] == "Air Quality" else point
                          for point in low["data_points"]]
    assert build_view(low, now)["aqi"]["needle_x"] < sensitive["aqi"]["needle_x"]
    runs = {level: next(index["stars"] for index in build_view(_params(now, aqi_level=level), now)["indices"]
                        if index["name"] == "跑步") for level in (0, 1, 2)}
    assert runs[0] > runs[1] > runs[2]


def test_nowcast_shows_only_when_rain_is_likely_within_two_hours():
    now = LA.localize(datetime(2026, 10, 24, 12, 50))
    dry = _params(now)
    dry["nowcast"] = [{"time": f"13:{minute:02d}", "precipitation": 0.0, "chance": 5} for minute in (0, 15, 30, 45)]
    assert build_view(dry, now)["nowcast"] == {}

    times = ["12:45", "13:00", "13:15", "13:30", "13:45", "14:00", "14:15", "14:30"]
    wet = _params(now)
    wet["nowcast"] = [{"time": time, "precipitation": amount, "chance": chance} for time, amount, chance in
                      zip(times, [0, 0, 0.1, 0.4, 1.2, 0.8, 0.3, 0.1], [10, 25, 45, 70, 85, 80, 60, 40])]
    view = build_view(wet, now)
    assert view["nowcast"]["headline"] == "13:15 起有雨"
    assert view["nowcast"]["chance"] == "85%" and view["nowcast"]["amount"] == "2.9 mm"
    assert [bar["shade"] for bar in view["nowcast"]["bars"]][:5] == ["light", "light", "rain", "rain", "heavy"]
    assert all(0 < bar["height"] <= 20 for bar in view["nowcast"]["bars"])

    raining = _params(now)
    raining["nowcast"] = [{"time": time, "precipitation": amount, "chance": 90 if amount else 10}
                          for time, amount in zip(times, [0.5, 0.5, 0.2, 0, 0, 0, 0, 0])]
    assert build_view(raining, now)["nowcast"]["headline"] == "13:30 前后雨停"

    html = Weather({"id": "weather"}).env.get_template("weather_modern.html").render(
        **wet, view=view, width=800, height=480, theme={"mode": "day", "css": {}},
        plugin_settings={}, style_sheets=[], font_faces=[])
    # The bars take the mascot's bubble; separators stay drawn.
    assert "mw-nowcast" in html and "mw-bubble" not in html and "·" not in html


def test_daily_peaks_show_on_the_uv_wind_and_week_cards():
    now = LA.localize(datetime(2026, 10, 24, 9, 0))
    params = _params(now)
    params["data_points"] = [dict(point, measurement=1.2) if point["label"] == "UV Index" else point
                             for point in params["data_points"]]
    params["forecast"][0].update(uv_max=4.6, gust_max=9.3)
    params["forecast"][2].update(rain_hours=3.0)
    view = build_view(params, now)

    assert view["uv"]["peak"] == "5" and view["uv"]["peak_marker"] > view["uv"]["marker"]
    assert view["wind"]["gust"] == "9.3"
    assert [day["rain_hours"] for day in view["week"]][:3] == ["", "3h", ""]
    assert view["week_rain"] is True
    html = Weather({"id": "weather"}).env.get_template("weather_modern.html").render(
        **params, view=view, width=800, height=480, theme={"mode": "day", "css": {}},
        plugin_settings={}, style_sheets=[], font_faces=[])
    for text in ("最高 5", "阵风 9.3", "3h", "mw-uv-peak"):
        assert text in html, text

    # Without the daily figures the cards look as before.
    plain = build_view(_params(now), now)
    assert plain["uv"]["peak"] == "" and plain["wind"]["gust"] == "" and plain["week_rain"] is False
