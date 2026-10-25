"""The month grid's marks: a drop of blood on today, finger-painted crosses over the days gone by."""

import sys
from datetime import date
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from plugins.simple_calendar import simple_calendar as module  # noqa: E402
from plugins.simple_calendar.simple_calendar import SimpleCalendar  # noqa: E402


def test_the_blood_drop_lands_with_its_splat_on_today():
    image = Image.new("RGB", (200, 200), (255, 255, 255))

    assert SimpleCalendar._bleed_on_day(image, (100, 100), 30) is True

    red, green, blue = image.getpixel((100, 100))
    assert red > 80 and green < 40 and blue < 40  # the splat's solid middle sits on the day
    # The splat is about the today circle's size: solid well out from the middle, white paper
    # again well beyond it (away from the falling drop and the drip).
    assert image.getpixel((100 - 24, 100))[1] < 60
    assert image.getpixel((100 - 60, 100 - 10)) == (255, 255, 255)


def test_crosses_are_finger_painted_and_let_the_number_show_through():
    for variant in range(1, module.CALENDAR_CROSS_VARIANTS + 1):
        art = module._calendar_page_art(f"cross_{variant}.png")
        assert art is not None and art.size == (128, 128) and art.getchannel("A").getextrema()[0] == 0
    image = Image.new("RGB", (120, 120), (0, 0, 0))

    SimpleCalendar._cross_off_day(image, date(2026, 10, 3), (60, 60), 90)

    # Not one pixel is painted fully opaque: the struck-off number stays readable underneath.
    pixels = [image.getpixel((x, y)) for x in range(120) for y in range(120)]
    reddest = max(pixels, key=lambda pixel: pixel[0])
    assert 0 < reddest[0] < 255 * module.CALENDAR_CROSS_OPACITY + 2
