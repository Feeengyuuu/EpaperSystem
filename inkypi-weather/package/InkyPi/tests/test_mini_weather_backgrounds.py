from datetime import date
import sys
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from plugins.mini_weather.mini_weather import MiniWeather
from plugins.mini_weather.mini_weather import get_language_labels


def test_default_weather_background_uses_mythic_color_assets():
    plugin = MiniWeather({"id": "mini_weather"})

    background = plugin._select_weather_background("022d.png", {}, date(2026, 5, 29))

    assert background["slug"] == "clear_day"
    assert background["style"] == "mythic_comic_1982"
    assert background["is_color"] is True
    assert "backgrounds_color" in Path(background["path"]).parts
    assert Path(background["path"]).parent.name == "mythic_comic_1982"
    with Image.open(background["path"]) as image:
        assert image.size == (800, 480)


def test_weather_background_classic_style_still_falls_back_to_legacy_assets():
    plugin = MiniWeather({"id": "mini_weather"})

    background = plugin._select_weather_background(
        "022d.png",
        {"weatherBackgroundStyle": "classic"},
        date(2026, 5, 29),
    )

    assert background["slug"] == "clear_day"
    assert background["style"] == "classic"
    assert background["is_color"] is False
    assert Path(background["path"]).parent.name == "backgrounds"


def test_default_weather_icons_use_shanghai_animation_assets():
    plugin = MiniWeather({"id": "mini_weather"})
    template_params = {
        "current_day_icon": plugin.get_plugin_dir("icons/022d.png"),
    }
    forecast_rows = [{
        "icon": plugin.get_plugin_dir("icons/10d.png"),
    }]

    plugin._apply_weather_icon_style(
        template_params,
        forecast_rows,
        plugin._weather_icon_style({}),
    )

    current_icon = Path(template_params["current_day_icon"])
    forecast_icon = Path(forecast_rows[0]["icon"])
    assert "icons_color" in current_icon.parts
    assert current_icon.parent.name == "shanghai_animation"
    assert current_icon.name == "022d.png"
    assert forecast_icon.parent.name == "shanghai_animation"
    assert forecast_icon.name == "10d.png"


def test_current_label_uses_today_weekday_abbreviation():
    labels = get_language_labels("en")

    assert labels["days"][date(2026, 5, 29).weekday()] == "Fri"
