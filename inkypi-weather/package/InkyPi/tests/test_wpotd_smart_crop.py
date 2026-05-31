import sys
from pathlib import Path

from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from plugins.wpotd.wpotd import Wpotd
from utils.image_loader import AdaptiveImageLoader


def _dark_pixel_count(image):
    return sum(image.convert("L").histogram()[:32])


def _face_skin_pixel_count(image):
    data = image.convert("RGB").tobytes()
    count = 0
    for index in range(0, len(data), 3):
        r, g, b = data[index], data[index + 1], data[index + 2]
        if r > 180 and 120 < g < 220 and 80 < b < 180:
            count += 1
    return count


def test_focus_crop_keeps_off_center_horizontal_subject():
    loader = AdaptiveImageLoader.__new__(AdaptiveImageLoader)
    source = Image.new("RGB", (1600, 480), "white")
    draw = ImageDraw.Draw(source)
    draw.rectangle((1220, 120, 1460, 360), fill="black")

    centered = loader._fit_image(source, (800, 480), Image.NEAREST, focus_crop=False)
    focused = loader._fit_image(source, (800, 480), Image.NEAREST, focus_crop=True)

    assert _dark_pixel_count(centered) == 0
    assert _dark_pixel_count(focused) > 40000


def test_focus_crop_prioritizes_off_center_face_over_detail():
    loader = AdaptiveImageLoader.__new__(AdaptiveImageLoader)
    source = Image.new("RGB", (1600, 480), "white")
    draw = ImageDraw.Draw(source)

    for x in range(40, 720, 30):
        draw.line((x, 20, x + 180, 460), fill="black", width=8)

    draw.ellipse((1280, 110, 1450, 320), fill=(226, 174, 132), outline="black", width=3)
    draw.ellipse((1325, 180, 1345, 202), fill="black")
    draw.ellipse((1385, 180, 1405, 202), fill="black")
    draw.arc((1340, 220, 1395, 270), 20, 160, fill="black", width=4)

    centered = loader._fit_image(source, (800, 480), Image.NEAREST, focus_crop=False)
    focused = loader._fit_image(source, (800, 480), Image.NEAREST, focus_crop=True)

    assert _face_skin_pixel_count(centered) == 0
    assert _face_skin_pixel_count(focused) > 25000


def test_focus_crop_keeps_off_center_vertical_subject():
    loader = AdaptiveImageLoader.__new__(AdaptiveImageLoader)
    source = Image.new("RGB", (800, 1600), "white")
    draw = ImageDraw.Draw(source)
    draw.rectangle((260, 1220, 540, 1460), fill="black")

    centered = loader._fit_image(source, (800, 480), Image.NEAREST, focus_crop=False)
    focused = loader._fit_image(source, (800, 480), Image.NEAREST, focus_crop=True)

    assert _dark_pixel_count(centered) == 0
    assert _dark_pixel_count(focused) > 50000


def test_focus_crop_prioritizes_face_in_tall_character_image():
    loader = AdaptiveImageLoader.__new__(AdaptiveImageLoader)
    source = Image.new("RGB", (800, 1600), "white")
    draw = ImageDraw.Draw(source)

    for y in range(860, 1520, 32):
        draw.rectangle((80, y, 720, y + 12), fill="black")

    draw.ellipse((300, 120, 500, 340), fill=(226, 174, 132), outline="black", width=3)
    draw.ellipse((352, 210, 374, 235), fill="black")
    draw.ellipse((426, 210, 448, 235), fill="black")
    draw.arc((358, 255, 442, 310), 20, 160, fill="black", width=5)

    centered = loader._fit_image(source, (800, 480), Image.NEAREST, focus_crop=False)
    focused = loader._fit_image(source, (800, 480), Image.NEAREST, focus_crop=True)

    assert _face_skin_pixel_count(centered) == 0
    assert _face_skin_pixel_count(focused) > 30000


class FakeImageLoader:
    def __init__(self):
        self.calls = []

    def from_url(self, url, dimensions, timeout_ms=40000, resize=True, headers=None, focus_crop=False):
        self.calls.append(
            {
                "url": url,
                "dimensions": dimensions,
                "timeout_ms": timeout_ms,
                "resize": resize,
                "headers": headers,
                "focus_crop": focus_crop,
            }
        )
        return Image.new("RGB", dimensions, "white")


def test_wpotd_download_forwards_focus_crop_to_loader():
    plugin = Wpotd.__new__(Wpotd)
    loader = FakeImageLoader()
    plugin.image_loader = loader

    image = plugin._download_image(
        "https://upload.wikimedia.org/example.jpg",
        dimensions=(800, 480),
        resize=True,
        focus_crop=True,
    )

    assert image.size == (800, 480)
    assert loader.calls == [
        {
            "url": "https://upload.wikimedia.org/example.jpg",
            "dimensions": (800, 480),
            "timeout_ms": 10000,
            "resize": True,
            "headers": Wpotd.HEADERS,
            "focus_crop": True,
        }
    ]


def test_wpotd_fit_settings_default_to_enabled():
    plugin = Wpotd.__new__(Wpotd)

    assert plugin._setting_enabled({}, "shrinkToFitWpotd", default=True) is True
    assert plugin._setting_enabled({"smartCropWpotd": "false"}, "smartCropWpotd", default=True) is False


def test_wpotd_fetch_image_src_prefers_generated_thumbnail_for_video():
    plugin = Wpotd.__new__(Wpotd)

    def fake_make_request(params):
        assert params["iiurlwidth"] == Wpotd.THUMBNAIL_WIDTH
        return {
            "query": {
                "pages": {
                    "123": {
                        "imageinfo": [
                            {
                                "url": "https://upload.wikimedia.org/example.webm",
                                "thumburl": "https://upload.wikimedia.org/example.webm/1200px-example.webm.jpg",
                                "mime": "video/webm",
                            }
                        ]
                    }
                }
            }
        }

    plugin._make_request = fake_make_request

    assert plugin._fetch_image_src("File:Example.webm").endswith(".webm.jpg")


def test_wpotd_fetch_image_src_rejects_direct_video_without_thumbnail():
    plugin = Wpotd.__new__(Wpotd)
    plugin._make_request = lambda params: {
        "query": {
            "pages": {
                "123": {
                    "imageinfo": [
                        {
                            "url": "https://upload.wikimedia.org/example.webm",
                            "mime": "video/webm",
                        }
                    ]
                }
            }
        }
    }

    try:
        plugin._fetch_image_src("File:Example.webm")
    except RuntimeError as exc:
        assert str(exc) == "Failed to retrieve image URL."
    else:
        raise AssertionError("Expected direct video URL without thumbnail to fail")
