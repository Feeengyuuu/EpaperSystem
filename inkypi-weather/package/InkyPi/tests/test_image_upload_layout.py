import sys
from pathlib import Path

from PIL import Image, ImageOps

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from plugins.image_upload.image_upload import ImageUpload  # noqa: E402


class DeviceConfig:
    def get_resolution(self):
        return (900, 300)

    def get_config(self, key, default=None):
        if key == "orientation":
            return "horizontal"
        return default


class RecordingLoader:
    def __init__(self):
        self.calls = []

    def from_file(self, path, dimensions, resize=True, focus_crop=False):
        self.calls.append(
            {
                "path": path,
                "dimensions": dimensions,
                "resize": resize,
                "focus_crop": focus_crop,
            }
        )
        with Image.open(path) as image:
            image = ImageOps.exif_transpose(image).convert("RGB")
            if resize:
                return ImageOps.fit(image, dimensions, method=Image.Resampling.NEAREST)
            return image.copy()


def make_plugin():
    plugin = ImageUpload({"id": "image_upload"})
    plugin.image_loader = RecordingLoader()
    return plugin


def save_image(path, size, color):
    image = Image.new("RGB", size, color)
    image.save(path)
    return str(path)


def test_portrait_images_render_as_three_columns(tmp_path):
    red = save_image(tmp_path / "red.png", (300, 600), (255, 0, 0))
    green = save_image(tmp_path / "green.png", (300, 600), (0, 255, 0))
    blue = save_image(tmp_path / "blue.png", (300, 600), (0, 0, 255))
    plugin = make_plugin()

    image = plugin.generate_image(
        {
            "imageFiles[]": [red, green, blue],
            "displayMode": "sequential",
            "image_index": "0",
            "padImage": "false",
        },
        DeviceConfig(),
    )

    assert image.size == (900, 300)
    assert image.getpixel((150, 150)) == (255, 0, 0)
    assert image.getpixel((450, 150)) == (0, 255, 0)
    assert image.getpixel((750, 150)) == (0, 0, 255)
    assert [call["dimensions"] for call in plugin.image_loader.calls] == [(300, 300), (300, 300), (300, 300)]


def test_landscape_image_renders_once_full_screen(tmp_path):
    landscape = save_image(tmp_path / "wide.png", (900, 300), (32, 64, 96))
    portrait = save_image(tmp_path / "portrait.png", (300, 600), (255, 0, 0))
    plugin = make_plugin()

    image = plugin.generate_image(
        {
            "imageFiles[]": [landscape, portrait],
            "displayMode": "sequential",
            "image_index": "0",
            "padImage": "false",
        },
        DeviceConfig(),
    )

    assert image.size == (900, 300)
    assert image.getpixel((450, 150)) == (32, 64, 96)
    assert plugin.image_loader.calls == [
        {
            "path": landscape,
            "dimensions": (900, 300),
            "resize": True,
            "focus_crop": False,
        }
    ]
