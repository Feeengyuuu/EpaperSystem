import sys
from io import BytesIO
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from plugins.bambu_monitor import bambu_monitor as bambu_module
from plugins.bambu_monitor.bambu_monitor import (
    ACCENT_BLUE,
    ACCENT_GOLD,
    CINNABAR,
    MALACHITE,
    PAPER,
    BambuMonitor,
)


def _plugin(tmp_path):
    plugin = BambuMonitor({"id": "bambu_monitor"})

    def plugin_dir(path=None):
        return str(tmp_path / path) if path else str(tmp_path)

    plugin.get_plugin_dir = plugin_dir
    return plugin


def _jpeg_bytes(color=(18, 52, 86)):
    buf = BytesIO()
    Image.new("RGB", (8, 6), color).save(buf, "JPEG", quality=93)
    return buf.getvalue()


def _near_color_count(image, target, tolerance=8):
    return sum(
        1
        for y in range(image.height)
        for x in range(image.width)
        for pixel in (image.getpixel((x, y)),)
        if max(abs(pixel[index] - target[index]) for index in range(3)) <= tolerance
    )


def test_camera_frame_is_cached_as_original_jpeg_bytes(tmp_path, monkeypatch):
    frame = _jpeg_bytes()

    class FakeCameraClient:
        def __init__(self, host, port, access_code, timeout):
            self.args = (host, port, access_code, timeout)

        def capture_frame(self):
            return frame

    monkeypatch.setattr(bambu_module, "BambuCameraClient", FakeCameraClient)
    plugin = _plugin(tmp_path)
    status = {"serial": "SERIAL"}

    plugin._attach_camera_frame(status, "192.0.2.10", 6000, "secret", 3)

    assert Path(status["camera_path"]).read_bytes() == frame
    assert status["camera_failure_count"] == 0
    assert status["camera_waiting"] is False


def test_rendered_camera_panel_keeps_source_rgb_values(tmp_path):
    plugin = _plugin(tmp_path)
    status = plugin._demo_status()
    status["camera_image"] = Image.new("RGB", (640, 360), (18, 52, 86))

    image = plugin._render_status(status, (800, 480))

    assert image.mode == "RGB"
    assert image.getpixel((420, 180)) == (18, 52, 86)


def test_render_status_uses_comic_process_palette(tmp_path):
    plugin = _plugin(tmp_path)

    image = plugin._render_status(plugin._demo_status(), (800, 480))

    assert image.size == (800, 480)
    assert image.mode == "RGB"
    assert _near_color_count(image, PAPER, tolerance=4) > 20_000
    assert _near_color_count(image, ACCENT_GOLD, tolerance=8) > 1_000
    assert _near_color_count(image, ACCENT_BLUE, tolerance=8) > 1_000
    assert _near_color_count(image, MALACHITE, tolerance=8) > 500
    assert _near_color_count(image, CINNABAR, tolerance=8) > 200
