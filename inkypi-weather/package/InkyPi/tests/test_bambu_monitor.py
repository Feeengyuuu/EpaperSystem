import sys
from io import BytesIO
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from plugins.bambu_monitor import bambu_monitor as bambu_module
from plugins.bambu_monitor.bambu_monitor import (
    ACCENT_BLUE,
    BAMBU_LAB_LOGO_GAP,
    BAMBU_LAB_LOGO_IMAGE,
    BAMBU_LAB_LOGO_SIZE,
    ACCENT_GOLD,
    CINNABAR,
    MALACHITE,
    PAPER,
    SECTION_WORDMARK_IMAGES,
    TITLE_WORDMARK_IMAGE,
    TITLE_WORDMARK_SIZE,
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


def test_title_wordmark_asset_is_transparent_measured_strip():
    path = Path(bambu_module.__file__).with_name(TITLE_WORDMARK_IMAGE)

    image = Image.open(path).convert("RGBA")

    assert image.size == TITLE_WORDMARK_SIZE
    assert image.getchannel("A").getextrema() == (0, 255)
    assert image.getchannel("A").getbbox() is not None
    assert image.getpixel((0, 0))[3] == 0
    assert image.getpixel((image.width - 1, image.height - 1))[3] == 0


def test_bambulab_logo_asset_is_transparent():
    path = Path(bambu_module.__file__).with_name(BAMBU_LAB_LOGO_IMAGE)

    image = Image.open(path).convert("RGBA")

    assert image.getchannel("A").getextrema() == (0, 255)
    assert image.getchannel("A").getbbox() is not None
    assert image.width > image.height * 3
    assert image.width <= 300
    assert image.height <= 100


def test_section_wordmark_assets_are_transparent():
    for title, (filename, size) in SECTION_WORDMARK_IMAGES.items():
        path = Path(bambu_module.__file__).with_name(filename)

        image = Image.open(path).convert("RGBA")

        assert image.size == size, title
        assert image.getchannel("A").getextrema() == (0, 255)
        assert image.getchannel("A").getbbox() is not None
        assert image.getpixel((0, 0))[3] == 0
        assert image.getpixel((image.width - 1, image.height - 1))[3] == 0


def test_render_status_uses_section_wordmarks(tmp_path, monkeypatch):
    plugin = _plugin(tmp_path)
    colors = {
        "PRINT": (11, 22, 33, 255),
        "LIVE VIEW": (44, 55, 66, 255),
        "THERMALS": (77, 88, 99, 255),
        "AMS": (111, 122, 133, 255),
    }

    def fake_section_wordmark(title):
        return Image.new("RGBA", SECTION_WORDMARK_IMAGES[title][1], colors[title])

    monkeypatch.setattr(plugin, "_load_section_wordmark", fake_section_wordmark)

    image = plugin._render_status(plugin._demo_status(), (800, 480))

    assert image.getpixel((32, 72)) == colors["PRINT"][:3]
    assert image.getpixel((320, 72)) == colors["LIVE VIEW"][:3]
    assert image.getpixel((32, 352)) == colors["THERMALS"][:3]
    assert image.getpixel((398, 352)) == colors["AMS"][:3]


def test_render_status_draws_bambulab_logo_and_shifts_title(tmp_path, monkeypatch):
    plugin = _plugin(tmp_path)
    fake_logo = Image.new("RGBA", (320, 320), (0, 0, 0, 0))
    for x in range(22, 298):
        for y in range(120, 199):
            fake_logo.putpixel((x, y), (1, 2, 3, 255))
    fake_wordmark = Image.new("RGBA", TITLE_WORDMARK_SIZE, (17, 34, 51, 255))
    monkeypatch.setattr(plugin, "_load_bambulab_logo", lambda: fake_logo)
    monkeypatch.setattr(plugin, "_load_title_wordmark", lambda: fake_wordmark)

    image = plugin._render_status(plugin._demo_status(), (800, 480))

    shifted_x = 22 + BAMBU_LAB_LOGO_SIZE[0] + BAMBU_LAB_LOGO_GAP
    assert image.getpixel((23, 18)) == (1, 2, 3)
    assert image.getpixel((shifted_x, 13)) == (17, 34, 51)
    assert image.getpixel((shifted_x - 1, 13)) != (17, 34, 51)


def test_render_status_uses_title_wordmark_when_available(tmp_path, monkeypatch):
    plugin = _plugin(tmp_path)
    fake_wordmark = Image.new("RGBA", TITLE_WORDMARK_SIZE, (17, 34, 51, 255))
    monkeypatch.setattr(plugin, "_load_bambulab_logo", lambda: None)
    monkeypatch.setattr(plugin, "_load_title_wordmark", lambda: fake_wordmark)

    image = plugin._render_status(plugin._demo_status(), (800, 480))

    assert image.getpixel((23, 14)) == (17, 34, 51)
    title_area = image.crop((22, 12, 256, 44))
    assert _near_color_count(title_area, ACCENT_GOLD, tolerance=4) < 500


def test_render_status_falls_back_to_text_title_when_wordmark_missing(tmp_path, monkeypatch):
    plugin = _plugin(tmp_path)
    monkeypatch.setattr(plugin, "_load_bambulab_logo", lambda: None)
    monkeypatch.setattr(plugin, "_load_title_wordmark", lambda: None)

    image = plugin._render_status(plugin._demo_status(), (800, 480))

    title_area = image.crop((22, 12, 256, 44))
    assert _near_color_count(title_area, ACCENT_GOLD, tolerance=4) > 4_500


class _BambuDeviceConfig:
    def get_resolution(self):
        return (800, 480)

    def get_config(self, key=None, default=None):
        return default

    def load_env_key(self, key):
        return "secret"


def _connected_settings():
    return {
        "host": "192.0.2.55",
        "serialNumber": "SERIAL",
        "accessCode": "secret",
        "timeoutSeconds": 2,
        "cacheSeconds": 0,
        "cameraEnabled": False,
    }


def _raise_no_route(*args, **kwargs):
    raise OSError(113, "No route to host")


def _cached_status():
    return {
        "host": "192.0.2.55",
        "serial": "SERIAL",
        "updated_at": "2026-06-24 10:00",
        "source": "live",
        "state": "RUNNING",
        "stage": "Printing",
        "progress": 42,
        "remaining_minutes": 12,
        "file": "plate.3mf",
        "nozzle": 219,
        "nozzle_target": 220,
        "bed": 64,
        "bed_target": 65,
        "chamber": 35,
        "fan": 70,
        "speed": "2",
        "error": 0,
        "ams": [],
        "camera_path": None,
        "camera_error": None,
        "camera_failure_count": 0,
        "camera_waiting": False,
    }


def test_bambu_connection_failure_image_is_non_cacheable(tmp_path, monkeypatch):
    plugin = _plugin(tmp_path)
    monkeypatch.setattr(plugin, "_fetch_report", _raise_no_route)

    image = plugin.generate_image(_connected_settings(), _BambuDeviceConfig())

    assert image.size == (800, 480)
    assert image.info["inkypi_skip_cache"] is True


def test_bambu_connection_failure_preserves_cached_status(tmp_path, monkeypatch):
    plugin = _plugin(tmp_path)
    settings = _connected_settings()
    cache_file = plugin._cache_file(settings["host"], settings["serialNumber"])
    status = _cached_status()
    plugin._write_cache(cache_file, {"fetched_at": 0, "status": status})
    monkeypatch.setattr(plugin, "_fetch_report", _raise_no_route)

    image = plugin.generate_image(settings, _BambuDeviceConfig())

    assert image.info["inkypi_skip_cache"] is True
    cached = plugin._read_cache(cache_file)
    assert cached["status"]["source"] == "live"
    assert "warning" not in cached["status"]
