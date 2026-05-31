import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from plugins.flow_progress.flow_progress import (  # noqa: E402
    COMIC_DAY_CATEGORY_STYLES,
    COMIC_DAY_PAPER,
    FlowProgress,
)


class FakeDeviceConfig:
    def __init__(self, resolution=(800, 480), orientation="horizontal", timezone="America/Los_Angeles"):
        self.resolution = resolution
        self.orientation = orientation
        self.timezone = timezone

    def get_resolution(self):
        return self.resolution

    def get_config(self, key=None, default=None):
        values = {"orientation": self.orientation, "timezone": self.timezone}
        if key is None:
            return values
        return values.get(key, default)


def _near_color_count(image, target, tolerance=6):
    return sum(
        1
        for y in range(image.height)
        for x in range(image.width)
        for pixel in (image.getpixel((x, y)),)
        if max(abs(pixel[index] - target[index]) for index in range(3)) <= tolerance
    )


def test_generate_image_uses_comic_day_palette_for_legacy_dark_defaults():
    plugin = FlowProgress({"id": "flow_progress"})

    image = plugin.generate_image(
        {
            "language": "en",
            "numBars": "2",
            "numDots": "20",
            "primaryColor": "#ffffff",
            "secondaryColor": "#000000",
        },
        FakeDeviceConfig(),
    )

    assert image.size == (800, 480)
    assert image.mode == "RGB"
    assert _near_color_count(image, COMIC_DAY_PAPER, tolerance=3) > 20_000

    for style in COMIC_DAY_CATEGORY_STYLES:
        assert _near_color_count(image, style["color"], tolerance=8) > 1_000
