import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from plugins.moon_phase.moon_phase import (
    NEW_MOON_EPOCH_UTC,
    SYNODIC_MONTH_DAYS,
    MoonPhase,
    calculate_moon_info,
)


class FakeDeviceConfig:
    def __init__(self, resolution=(800, 480), timezone_name="America/Los_Angeles", orientation="horizontal"):
        self.resolution = resolution
        self.timezone_name = timezone_name
        self.orientation = orientation

    def get_resolution(self):
        return self.resolution

    def get_config(self, key=None, default=None):
        values = {
            "timezone": self.timezone_name,
            "orientation": self.orientation,
        }
        if key is None:
            return values
        return values.get(key, default)


def test_new_moon_epoch_has_dark_new_phase():
    info = calculate_moon_info(NEW_MOON_EPOCH_UTC)

    assert info.phase_name == "New Moon"
    assert info.age_days < 0.001
    assert info.illumination < 0.001
    assert info.next_full_utc > NEW_MOON_EPOCH_UTC
    assert info.next_new_utc > NEW_MOON_EPOCH_UTC


def test_half_synodic_month_is_full_moon():
    full_time = NEW_MOON_EPOCH_UTC + timedelta(days=SYNODIC_MONTH_DAYS / 2.0)

    info = calculate_moon_info(full_time)

    assert info.phase_name == "Full Moon"
    assert abs(info.age_days - SYNODIC_MONTH_DAYS / 2.0) < 0.001
    assert info.illumination > 0.999
    assert info.direction == "Waning"


def test_generate_image_returns_nonblank_landscape_render():
    plugin = MoonPhase({"id": "moon_phase"})

    image = plugin.generate_image(
        {"debugNowUtc": "2026-05-28T12:00:00+00:00"},
        FakeDeviceConfig(),
    )

    assert isinstance(image, Image.Image)
    assert image.mode == "RGB"
    assert image.size == (800, 480)
    assert len(image.getcolors(maxcolors=800 * 480)) > 20
    assert image.getbbox() is not None


def test_generate_image_respects_vertical_orientation():
    plugin = MoonPhase({"id": "moon_phase"})

    image = plugin.generate_image(
        {"debugNowUtc": datetime(2026, 5, 28, 12, tzinfo=timezone.utc)},
        FakeDeviceConfig(resolution=(800, 480), orientation="vertical"),
    )

    assert image.size == (480, 800)
