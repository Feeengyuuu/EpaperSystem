import re
import sys
from pathlib import Path

import pytest
from flask import Flask

SRC_DIR = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC_DIR))

from blueprints.settings import settings_bp


class DeviceConfig:
    def __init__(self, **overrides):
        self.config = {
            "name": "Living Room",
            "orientation": "horizontal",
            "inverted_image": False,
            "log_system_stats": True,
            "timezone": "Asia/Shanghai",
            "time_format": "24h",
            "plugin_cycle_interval_seconds": 3600,
            "display_type": "inky",
            "image_settings": {"saturation": 1.2, "contrast": 1.0, "sharpness": 1.0, "brightness": 0.9},
        }
        self.config.update(overrides)

    def get_config(self, key=None, default=None):
        return self.config if key is None else self.config.get(key, default)


def _render(**overrides):
    app = Flask(__name__, template_folder=str(SRC_DIR / "templates"))
    app.config["DEVICE_CONFIG"] = DeviceConfig(**overrides)
    app.register_blueprint(settings_bp)
    response = app.test_client().get("/settings")
    assert response.status_code == 200
    return response.get_data(as_text=True)


def _checked_value(html, name):
    pattern = rf'<input type="radio" name="{name}" value="([^"]+)" checked>'
    return re.findall(pattern, html)


def test_device_page_posts_every_field_save_settings_reads():
    html = _render()

    for name in (
        "deviceName",
        "orientation",
        "invertImage",
        "timezoneName",
        "timeFormat",
        "interval",
        "unit",
        "logSystemStats",
        "saturation",
        "contrast",
        "sharpness",
        "brightness",
        "inky_saturation",
    ):
        assert f'name="{name}"' in html, name
    assert _checked_value(html, "timeFormat") == ["24h"]
    assert _checked_value(html, "orientation") == ["horizontal"]
    assert 'id="logSystemStats" name="logSystemStats" checked' in html
    assert 'class="nav-link is-active" href="/settings" aria-current="page"' in html


@pytest.mark.parametrize(
    ("seconds", "value", "unit"),
    ((3600, "1", "hour"), (7200, "2", "hour"), (5400, "90", "minute"), (900, "15", "minute")),
)
def test_device_page_shows_cycle_interval_without_rounding(seconds, value, unit):
    html = _render(plugin_cycle_interval_seconds=seconds)

    assert re.search(rf'name="interval" min="1" required\s+value="{value}"', html)
    assert _checked_value(html, "unit") == [unit]


def test_device_page_hides_inky_saturation_for_other_displays():
    html = _render(display_type="waveshare")

    assert 'name="inky_saturation"' not in html
