import sys
from pathlib import Path

from PIL import Image, ImageChops

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from plugins.live_radar import strip_art  # noqa: E402

PAGE = (254, 252, 246)
INK = (0, 0, 0)
BOX = (100, 10, 600, 32)


def _parade(title="LIVE NOW", seed="2026102411", count=8, box=BOX):
    image = Image.new("RGB", (700, 44), PAGE)
    drawn = strip_art.parade(image, box, title, seed, INK, PAGE, count)
    return image, drawn


def _inked_columns(image):
    background = Image.new("RGB", image.size, PAGE)
    diff = ImageChops.difference(image, background).convert("L").point(lambda value: 255 if value > 60 else 0)
    return diff.getbbox(), sum(1 for x in range(image.width) if diff.crop((x, 0, x + 1, image.height)).getbbox())


def test_each_section_has_its_own_stickers_inside_the_strip():
    for title in ("LIVE NOW", "LIVE TOO", "REPLAY", "OFFLINE"):
        image, drawn = _parade(title)
        bbox, _columns = _inked_columns(image)
        assert drawn
        assert BOX[0] - 9 <= bbox[0] and bbox[2] <= BOX[2] and BOX[1] <= bbox[1] and bbox[3] <= BOX[3]
    for name in ("live_now", "live_too", "offline"):
        assert all((Path(strip_art.STRIP_DIR) / f"{name}_{number}.png").is_file() for number in range(1, 9))
        assert (Path(strip_art.STRIP_DIR) / f"{name}.source.json").is_file()


def test_the_parade_changes_with_the_refresh_but_not_within_one():
    first, _ = _parade(seed="a")
    again, _ = _parade(seed="a")
    other, _ = _parade(seed="b")

    assert ImageChops.difference(first, again).getbbox() is None
    assert ImageChops.difference(first, other).getbbox() is not None


def test_a_busier_section_crowds_its_stickers_and_a_narrow_strip_stays_empty():
    _quiet_box, quiet = _inked_columns(_parade(count=2)[0])
    _busy_box, busy = _inked_columns(_parade(count=13)[0])

    assert busy > quiet
    assert _parade(box=(100, 10, 140, 32))[1] is False
    assert _parade(title="SOMETHING ELSE")[1] is False
