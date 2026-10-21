import sys
from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from plugins.bambu_monitor import bambu_monitor as bambu_module
from plugins.bambu_monitor.bambu_monitor import (
    ACCENT_ORANGE,
    BAMBU_LAB_LOGO_GAP,
    BAMBU_LAB_LOGO_IMAGE,
    BAMBU_LAB_LOGO_SIZE,
    ACCENT_GOLD,
    CINNABAR,
    HEADER_RULE_Y,
    INK,
    MALACHITE,
    PAPER,
    TITLE_WORDMARK_IMAGE,
    TITLE_WORDMARK_SIZE,
    BambuMonitor,
)
from plugins.base_plugin.render_provenance import (  # noqa: E402
    SourceProvenance,
    read_source_provenance,
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


def _failing_camera(monkeypatch):
    class FailingCameraClient:
        def __init__(self, *args):
            pass

        def capture_frame(self):
            raise OSError(113, "No route to host")

    monkeypatch.setattr(bambu_module, "BambuCameraClient", FailingCameraClient)


def test_failed_capture_labels_the_cached_frame_with_its_own_time(tmp_path, monkeypatch):
    plugin = _plugin(tmp_path)
    stale = Path(plugin._camera_file("192.0.2.10", "SERIAL"))
    stale.parent.mkdir(parents=True, exist_ok=True)
    stale.write_bytes(_jpeg_bytes())
    captured_at = bambu_module.datetime(2026, 10, 20, 21, 21).timestamp()
    bambu_module.os.utime(stale, (captured_at, captured_at))
    _failing_camera(monkeypatch)
    status = {"serial": "SERIAL", "camera_updated_at": "19:09"}

    plugin._attach_camera_frame(status, "192.0.2.10", 6000, "secret", 3)

    assert status["camera_path"] == str(stale)
    assert status["camera_updated_at"] == "21:21"
    assert status["camera_stale"] is True


def test_successful_capture_clears_the_stale_mark(tmp_path, monkeypatch):
    frame = _jpeg_bytes()

    class FakeCameraClient:
        def __init__(self, *args):
            pass

        def capture_frame(self):
            return frame

    monkeypatch.setattr(bambu_module, "BambuCameraClient", FakeCameraClient)
    plugin = _plugin(tmp_path)
    status = {"serial": "SERIAL", "camera_stale": True}

    plugin._attach_camera_frame(status, "192.0.2.10", 6000, "secret", 3)

    assert status["camera_stale"] is False


@pytest.mark.parametrize(("stale", "chip", "fill"), [(True, "CAM 21:21 STALE", ACCENT_ORANGE), (False, "CAM 21:21", None)])
def test_camera_chip_marks_a_stale_frame(tmp_path, monkeypatch, stale, chip, fill):
    plugin = _plugin(tmp_path)
    status = plugin._demo_status()
    status.update({"camera_updated_at": "21:21", "camera_stale": stale})
    pills = []
    draw_pill = plugin._draw_pill

    def record_pill(draw, x, y, text, color, *args, **kwargs):
        pills.append((text, color))
        return draw_pill(draw, x, y, text, color, *args, **kwargs)

    monkeypatch.setattr(plugin, "_draw_pill", record_pill)

    plugin._render_status(status, (800, 480))

    texts = dict(pills)
    assert chip in texts
    assert ("CAM 21:21 STALE" in texts) is stale
    if fill:
        assert texts[chip] == fill


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
    assert _near_color_count(image, MALACHITE, tolerance=8) > 1_500
    assert _near_color_count(image, CINNABAR, tolerance=8) > 200
    assert _near_color_count(image, ACCENT_ORANGE, tolerance=8) > 200


def test_job_column_progress_bar_fills_to_the_print_progress(tmp_path):
    plugin = _plugin(tmp_path)
    status = plugin._demo_status()
    status["progress"] = 50

    image = plugin._render_status(status, (800, 480))

    bar_y = HEADER_RULE_Y + 2 + 85
    assert image.getpixel((600, bar_y)) == MALACHITE
    assert image.getpixel((700, bar_y)) != MALACHITE


def _drawn_text_boxes(monkeypatch):
    boxes = []
    original = ImageDraw.ImageDraw.text

    def record(self, xy, text, *args, **kwargs):
        font = kwargs.get("font")
        anchor = kwargs.get("anchor")
        boxes.append((str(text), self.textbbox(xy, str(text), font=font, anchor=anchor)))
        return original(self, xy, text, *args, **kwargs)

    monkeypatch.setattr(ImageDraw.ImageDraw, "text", record)
    return boxes


def test_full_percentage_never_runs_into_the_layer_count(tmp_path, monkeypatch):
    plugin = _plugin(tmp_path)
    status = plugin._demo_status()
    status.update({"state": "FINISH", "progress": 100, "layer": 75, "total_layers": 75})
    boxes = _drawn_text_boxes(monkeypatch)

    plugin._render_status(status, (800, 480))

    percent = next(box for text, box in boxes if text == "100%")
    layer = next(box for text, box in boxes if text == "LAYER 75/75")
    assert percent[2] + 6 <= layer[0]
    assert percent[3] - percent[1] >= 40
    assert layer[2] <= 800 - 22


@pytest.mark.parametrize(
    ("error", "text"),
    [(83902475, "0500-400B"), ("83902475", "0500-400B"), (0, ""), ("0", ""), (1, ""), (None, ""),
     ("Filament runout", "Filament runout")],
)
def test_print_error_reads_like_bambu_studio(error, text):
    assert BambuMonitor._print_error_text(error) == text


def test_idle_printer_shows_the_last_job_and_names_its_error(tmp_path, monkeypatch):
    plugin = _plugin(tmp_path)
    status = plugin._demo_status()
    status.update({"state": "IDLE", "stage": "", "progress": 100, "remaining_minutes": 0, "error": 83902475})
    boxes = _drawn_text_boxes(monkeypatch)

    plugin._render_status(status, (800, 480))

    texts = [text for text, _box in boxes]
    assert "LAST JOB" in texts and "JOB" not in texts
    error = next(box for text, box in boxes if text == "ERR 0500-400B")
    idle = next(box for text, box in boxes if text == "IDLE")
    assert idle[2] < error[0] and error[3] < 120
    assert not any("83902475" in text for text in texts)


def test_printing_job_keeps_the_job_label(tmp_path, monkeypatch):
    plugin = _plugin(tmp_path)
    boxes = _drawn_text_boxes(monkeypatch)

    plugin._render_status(plugin._demo_status(), (800, 480))

    texts = [text for text, _box in boxes]
    assert "JOB" in texts and "LAST JOB" not in texts
    assert not any(text.startswith("ERR") for text in texts)


def test_empty_tray_reads_empty_with_a_hollow_spool(tmp_path, monkeypatch):
    plugin = _plugin(tmp_path)
    boxes = _drawn_text_boxes(monkeypatch)
    image = Image.new("RGB", (200, 500), PAPER)
    draw = ImageDraw.Draw(image)
    box = (22, 376, 141, 466)

    plugin._draw_ams_tile(draw, box, {"id": "0-1", "active": False, "type": "FIL", "color": ""})

    texts = [text for text, _box in boxes]
    assert "EMPTY" in texts
    assert "FIL" not in texts and "COLOR --" not in texts
    cx, cy, radius = plugin._ams_tile_layout(box)["spool"]
    assert image.getpixel((cx + radius - 6, cy)) == bambu_module.PANEL
    assert CINNABAR not in {image.getpixel((x, y)) for x in range(cx - radius, cx + radius) for y in range(cy - radius, cy + radius)}


def test_switched_off_heaters_show_no_zero_target(tmp_path, monkeypatch):
    plugin = _plugin(tmp_path)
    status = plugin._demo_status()
    status.update({"nozzle": 25, "nozzle_target": 0, "bed": 26, "bed_target": 0})
    boxes = _drawn_text_boxes(monkeypatch)

    plugin._render_status(status, (800, 480))

    texts = [text for text, _box in boxes]
    assert "25°" in texts and "26°" in texts
    assert "/ 0°" not in texts


def test_camera_hero_is_a_wide_frame_beside_the_job_column(tmp_path):
    plugin = _plugin(tmp_path)
    status = plugin._demo_status()
    status["camera_image"] = Image.new("RGB", (1280, 720), (18, 52, 86))

    image = plugin._render_status(status, (800, 480))

    for point in ((40, 120), (500, 330), (270, 200)):
        assert image.getpixel(point) == (18, 52, 86)
    assert image.getpixel((540, 200)) == PAPER
    assert image.getpixel((270, 360)) != (18, 52, 86)


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


def test_settings_expose_machine_name_and_model_overrides():
    settings_html = Path(bambu_module.__file__).with_name("settings.html").read_text(encoding="utf-8")
    name_input = settings_html.split('id="printerName"', 1)[1].split(">", 1)[0]
    model_input = settings_html.split('id="printerModel"', 1)[1].split(">", 1)[0]

    assert 'id="printerName"' in settings_html
    assert 'id="printerModel"' in settings_html
    assert 'maxlength="28"' in name_input
    assert 'maxlength="16"' in model_input
    assert "pluginSettings.printerName" in settings_html
    assert "pluginSettings.printerModel" in settings_html


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


def test_machine_info_lines_show_name_model_and_mask_identifiers(tmp_path):
    plugin = _plugin(tmp_path)
    status = plugin._demo_status()
    status.update({
        "machine_name": "Workshop Printer",
        "machine_model": "A1",
        "serial": "SERIAL1234567890",
        "host": "192.0.2.55",
    })

    primary, secondary = plugin._machine_info_lines(status)

    assert primary == "Workshop Printer / A1"
    assert secondary == "SN ...7890 / HOST 192.x.x.55"
    assert status["serial"] not in primary + secondary
    assert status["host"] not in primary + secondary


def test_machine_info_lines_do_not_reveal_short_identifiers(tmp_path):
    plugin = _plugin(tmp_path)
    status = plugin._demo_status()
    status.update({"serial": "AB12", "host": "pi"})

    _, secondary = plugin._machine_info_lines(status)

    assert "AB12" not in secondary
    assert "pi" not in secondary
    assert secondary.startswith("SN ...12 / HOST p...")


def test_masked_host_strongly_redacts_hostnames_and_ipv6(tmp_path):
    plugin = _plugin(tmp_path)
    ipv6 = "2001:db8::1234"

    assert plugin._masked_host("bambu") == "b...u"
    assert plugin._masked_host("abcdef") == "a...f"
    assert plugin._masked_host("printer.local") == "pr...al"
    assert plugin._masked_host(ipv6) == "IPv6 ...34"
    assert "2001" not in plugin._masked_host(ipv6)
    assert "1234" not in plugin._masked_host(ipv6)


def test_demo_status_includes_machine_identity(tmp_path):
    status = _plugin(tmp_path)._demo_status()

    assert status["machine_name"] == "Workshop Printer"
    assert status["machine_model"] == "A1"


def test_normalize_report_includes_configured_machine_identity(tmp_path):
    plugin = _plugin(tmp_path)

    status = plugin._normalize_report(
        {"print": {"gcode_state": "IDLE"}},
        "192.0.2.55",
        "SERIAL1234567890",
        {"printerName": "Workshop Printer", "printerModel": "A1"},
    )

    assert status["machine_name"] == "Workshop Printer"
    assert status["machine_model"] == "A1"


def test_machine_identity_is_trimmed_and_bounded_server_side(tmp_path):
    plugin = _plugin(tmp_path)
    status = {}
    long_name = "  " + "N" * 40 + "  "
    long_model = "  " + "M" * 24 + "  "

    plugin._apply_machine_identity(
        status,
        {"printerName": long_name, "printerModel": long_model},
        host="192.0.2.55",
        serial="SERIAL1234567890",
    )

    assert status["machine_name"] == "N" * 28
    assert status["machine_model"] == "M" * 16
    assert status["host"] == "192.0.2.55"
    assert status["serial"] == "SERIAL1234567890"


def _all_drawn_text(monkeypatch):
    drawn = []
    original = ImageDraw.ImageDraw.text

    def record(self, xy, text, *args, **kwargs):
        drawn.append(str(text))
        return original(self, xy, text, *args, **kwargs)

    monkeypatch.setattr(ImageDraw.ImageDraw, "text", record)
    return drawn


def test_header_draws_masked_machine_info_right_aligned_before_the_clock(tmp_path, monkeypatch):
    plugin = _plugin(tmp_path)
    status = plugin._demo_status()
    status.update({
        "machine_name": "Workshop Printer",
        "machine_model": "A1",
        "serial": "SERIAL1234567890",
        "host": "192.0.2.55",
    })
    expected = plugin._machine_info_lines(status)
    calls = []
    draw_fit_text = plugin._draw_fit_text_ellipsis

    def record_text(draw, text, x, y, *args, **kwargs):
        calls.append((text, x, y, kwargs.get("anchor")))
        return draw_fit_text(draw, text, x, y, *args, **kwargs)

    monkeypatch.setattr(plugin, "_draw_fit_text_ellipsis", record_text)
    drawn = _all_drawn_text(monkeypatch)

    plugin._render_status(status, (800, 480))

    identity = [call for call in calls if call[0] in expected]
    assert [call[0] for call in identity] == list(expected)
    assert all(anchor == "ra" and y < HEADER_RULE_Y for _text, _x, y, anchor in identity)
    assert not any(status["serial"] in text or status["host"] in text for text in drawn)


def test_max_machine_identity_lines_fit_with_pixel_aware_ellipsis(tmp_path):
    plugin = _plugin(tmp_path)
    status = {}
    plugin._apply_machine_identity(
        status,
        {
            "printerName": "W" * 28,
            "printerModel": "M" * 16,
        },
        host="printer-with-a-very-long-hostname.local",
        serial="SERIAL1234567890",
    )
    identity_lines = plugin._machine_info_lines(status)
    draw = ImageDraw.Draw(Image.new("RGB", (800, 480), "white"))
    max_width = 238

    assert plugin._text_width(draw, identity_lines[0], plugin._font(9, True)) > max_width

    fitted = []
    for line, start_size in zip(identity_lines, (11, 10)):
        rendered_text, font = plugin._draw_fit_text_ellipsis(
            draw,
            line,
            0,
            0,
            max_width,
            start_size,
            True,
            INK,
        )
        bounds = draw.textbbox((0, 0), rendered_text, font=font)
        fitted.append((rendered_text, font))
        assert bounds[2] - bounds[0] <= max_width

    assert fitted[0][0].endswith("…")
    assert fitted[0][1].size == 9


def test_max_machine_identity_pixels_stay_between_title_and_clock(tmp_path, monkeypatch):
    plugin = _plugin(tmp_path)
    status = plugin._demo_status()
    plugin._apply_machine_identity(
        status,
        {
            "printerName": "W" * 28,
            "printerModel": "M" * 16,
        },
        host="printer-with-a-very-long-hostname.local",
        serial="SERIAL1234567890",
    )
    identity_lines = plugin._machine_info_lines(status)
    draw_identity = plugin._draw_fit_text_ellipsis
    drawn_identity = []

    def skip_identity(draw, text, *args, **kwargs):
        if text in identity_lines:
            return text, None
        return draw_identity(draw, text, *args, **kwargs)

    def record_identity(draw, text, x, y, max_width, *args, **kwargs):
        rendered_text, font = draw_identity(draw, text, x, y, max_width, *args, **kwargs)
        if text in identity_lines:
            drawn_identity.append((draw, rendered_text, font, max_width))
        return rendered_text, font

    monkeypatch.setattr(plugin, "_draw_fit_text_ellipsis", skip_identity)
    background = plugin._render_status(status, (800, 480))
    monkeypatch.setattr(plugin, "_draw_fit_text_ellipsis", record_identity)
    rendered = plugin._render_status(status, (800, 480))

    assert len(drawn_identity) == 2
    for draw, rendered_text, font, max_width in drawn_identity:
        bounds = draw.textbbox((0, 0), rendered_text, font=font)
        assert bounds[2] - bounds[0] <= max_width
    assert drawn_identity[0][1].endswith("…")

    title_right = 22 + BAMBU_LAB_LOGO_SIZE[0] + BAMBU_LAB_LOGO_GAP + TITLE_WORDMARK_SIZE[0]
    identity_pixels = [
        (x, y)
        for y in range(rendered.height)
        for x in range(rendered.width)
        if rendered.getpixel((x, y)) != background.getpixel((x, y))
    ]
    assert identity_pixels
    assert min(x for x, _ in identity_pixels) > title_right
    assert max(x for x, _ in identity_pixels) < 800 - 22 - 50
    assert max(y for _, y in identity_pixels) < HEADER_RULE_Y


def test_stage_line_falls_back_to_the_state_never_the_host(tmp_path, monkeypatch):
    plugin = _plugin(tmp_path)
    status = plugin._demo_status()
    status.update({"stage": "", "host": "192.0.2.55"})
    drawn = _all_drawn_text(monkeypatch)

    plugin._render_status(status, (800, 480))

    assert not any("192.0.2.55" in text for text in drawn)
    assert "PRINTING" in drawn
    assert "Printing" in drawn


def test_legacy_numeric_stage_is_not_shown(tmp_path, monkeypatch):
    plugin = _plugin(tmp_path)
    status = plugin._demo_status()
    status["stage"] = "2"
    drawn = _all_drawn_text(monkeypatch)

    plugin._render_status(status, (800, 480))

    assert not any("Stage 2" in text for text in drawn)
    assert BambuMonitor._display_stage("2") == ""
    assert BambuMonitor._display_stage("Heating hotend") == "Heating hotend"


def test_setup_and_error_renders_still_show_their_message(tmp_path, monkeypatch):
    plugin = _plugin(tmp_path)
    drawn = _all_drawn_text(monkeypatch)

    plugin._render_setup_required((800, 480), "", "SERIAL", False)
    plugin._render_error((800, 480), "No route to host", host="192.0.2.55", serial="SERIAL")

    joined = " ".join(drawn)
    assert "Enter printer IP, serial, and access code" in drawn
    assert "Missing:" in joined
    assert "Unable to read printer" in drawn
    assert "No route to host" in joined
    assert "192.0.2.55" not in joined


def test_stale_warning_is_drawn_over_the_camera(tmp_path, monkeypatch):
    plugin = _plugin(tmp_path)
    status = plugin._demo_status()
    status.update({"source": "stale", "warning": "No route to host"})
    drawn = _all_drawn_text(monkeypatch)

    plugin._render_status(status, (800, 480))

    assert "No route to host" in drawn
    assert "STALE" in drawn


def _report(**fields):
    return {"print": {"gcode_state": "RUNNING", **fields}}


def test_normalize_report_reads_stage_layers_fan_and_finish_time(tmp_path):
    plugin = _plugin(tmp_path)
    before = bambu_module.datetime.now()

    status = plugin._normalize_report(
        _report(stg_cur=2, mc_print_stage="2", layer_num=12, total_layer_num=48,
                cooling_fan_speed="15", mc_remaining_time=18),
        "192.0.2.55",
        "SERIAL",
    )

    after = bambu_module.datetime.now()
    assert status["stage"] == "Heatbed preheating"
    assert (status["layer"], status["total_layers"]) == (12, 48)
    assert status["fan"] == 100
    expected = {
        (moment + bambu_module.timedelta(minutes=18)).strftime("%H:%M") for moment in (before, after)
    }
    assert status["finish_at"] in expected


@pytest.mark.parametrize(
    ("fields", "stage"),
    [
        ({"stg_cur": 0}, "Printing"),
        ({"stg_cur": 0, "gcode_state": "IDLE"}, ""),
        ({"stg_cur": 255}, ""),
        ({"stg_cur": -1}, ""),
        ({"stg_cur": 99}, ""),
        ({"mc_print_stage": "2"}, ""),
        ({"mc_print_stage": "Calibrating"}, "Calibrating"),
    ],
)
def test_normalize_report_stage_text(tmp_path, fields, stage):
    status = _plugin(tmp_path)._normalize_report(_report(**fields), "192.0.2.55", "SERIAL")

    assert status["stage"] == stage


def test_normalize_report_fan_falls_back_and_finish_needs_remaining_time(tmp_path):
    plugin = _plugin(tmp_path)

    status = plugin._normalize_report(_report(fan_gear=70, mc_remaining_time=0), "192.0.2.55", "SERIAL")

    assert status["fan"] == 70
    assert status["finish_at"] is None
    assert status["layer"] is None and status["total_layers"] is None


def test_job_value_formatters():
    assert BambuMonitor._remaining_text(18) == "18m"
    assert BambuMonitor._remaining_text(65) == "1h 05m"
    assert BambuMonitor._remaining_text(None) == "--"
    assert BambuMonitor._speed_text("2") == "STANDARD"
    assert BambuMonitor._speed_text("4") == "LUDICROUS"
    assert BambuMonitor._speed_text("7") == "LV 7"
    assert BambuMonitor._speed_text("") == "--"
    assert BambuMonitor._fan_text(None) == "--"
    assert BambuMonitor._fan_text(100) == "100%"
    assert BambuMonitor._layer_text({"layer": 12, "total_layers": 48}) == "LAYER 12/48"
    assert BambuMonitor._layer_text({"layer": 0, "total_layers": 0}) == ""


def test_tray_slot_labels_follow_bambu_studio():
    assert BambuMonitor._tray_slot_label("0-1") == "A2"
    assert BambuMonitor._tray_slot_label("1-3") == "B4"
    assert BambuMonitor._tray_slot_label("254") == "254"


def test_visible_trays_show_the_unit_holding_the_active_tray():
    trays = [{"id": f"{index // 4}-{index % 4}", "active": index == 5} for index in range(8)]

    assert [tray["id"] for tray in BambuMonitor._visible_trays(trays)] == ["1-0", "1-1", "1-2", "1-3"]
    for tray in trays:
        tray["active"] = False
    assert [tray["id"] for tray in BambuMonitor._visible_trays(trays)] == ["0-0", "0-1", "0-2", "0-3"]


def test_in_use_pill_sits_roomily_under_the_spool(tmp_path, monkeypatch):
    plugin = _plugin(tmp_path)
    box = (22, 376, 141, 466)
    layout = plugin._ams_tile_layout(box)
    pills = []
    draw_pill = plugin._draw_pill

    def record_pill(draw, x, y, text, *args, **kwargs):
        drawn_box = draw_pill(draw, x, y, text, *args, **kwargs)
        pills.append((text, drawn_box))
        return drawn_box

    monkeypatch.setattr(plugin, "_draw_pill", record_pill)
    image = Image.new("RGB", (200, 500), PAPER)
    draw = ImageDraw.Draw(image)

    plugin._draw_ams_tile(draw, box, {"id": "0-1", "active": True, "type": "PETG", "color": "FFFFFFFF"})

    (text, (left, top, right, bottom)), = pills
    cx, _cy, _radius = layout["spool"]
    assert text == "USE"
    assert top >= layout["ring_bottom"] + 6
    assert bottom <= box[3] - 6 and box[0] < left and right < layout["text_x"]
    assert bottom - top == 18
    assert right - left >= plugin._text_width(draw, "USE", plugin._font(10, True)) + 22
    assert abs((left + right) / 2 - cx) <= 1
    assert image.getpixel((cx, (top + bottom) // 2 - 6)) == MALACHITE


def test_inactive_tray_has_no_in_use_pill(tmp_path, monkeypatch):
    plugin = _plugin(tmp_path)
    pills = []
    monkeypatch.setattr(plugin, "_draw_pill", lambda *args, **kwargs: pills.append(args))
    draw = ImageDraw.Draw(Image.new("RGB", (200, 500), PAPER))

    plugin._draw_ams_tile(draw, (22, 376, 141, 466), {"id": "0-0", "active": False, "type": "PLA", "color": "F2862EFF"})

    assert pills == []


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


def _theme_context(mode):
    palettes = {
        "day": {
            "background": (244, 238, 214),
            "panel": (252, 248, 232),
            "ink": (18, 24, 29),
            "muted": (78, 82, 84),
            "rule": (150, 142, 118),
            "accent": (12, 126, 92),
        },
        "night": {
            "background": (9, 18, 22),
            "panel": (18, 31, 35),
            "ink": (238, 244, 240),
            "muted": (174, 188, 181),
            "rule": (69, 91, 84),
            "accent": (102, 213, 170),
        },
    }
    return {
        "requested_mode": "auto",
        "mode": mode,
        "palette": palettes[mode],
        "css": {},
        "source": "test",
        "reason": "test",
    }


def test_theme_only_warm_cache_is_provider_free_and_byte_stable(tmp_path, monkeypatch):
    plugin = _plugin(tmp_path)
    settings = _connected_settings()
    settings.update(
        {
            "cacheSeconds": 60,
            "cameraEnabled": True,
            "_theme_render_only": True,
            "_inkypi_theme": _theme_context("night"),
        }
    )
    cache_file = plugin._cache_file(settings["host"], settings["serialNumber"])
    plugin._write_cache(
        cache_file,
        {"fetched_at": bambu_module.time.time(), "status": _cached_status()},
    )
    original_cache = Path(cache_file).read_bytes()
    calls = {"mqtt": 0, "camera": 0, "write": 0}

    def fake_fetch(*args, **kwargs):
        calls["mqtt"] += 1
        return {"print": {"gcode_state": "IDLE"}}

    def fake_camera(*args, **kwargs):
        calls["camera"] += 1

    def fake_write(*args, **kwargs):
        calls["write"] += 1

    monkeypatch.setattr(plugin, "_fetch_report", fake_fetch)
    monkeypatch.setattr(plugin, "_attach_camera_frame", fake_camera)
    monkeypatch.setattr(plugin, "_write_cache", fake_write)

    image = plugin.generate_image(settings, _BambuDeviceConfig())

    assert calls == {"mqtt": 0, "camera": 0, "write": 0}
    assert Path(cache_file).read_bytes() == original_cache
    assert image.info.get("inkypi_skip_cache") is not True


def _theme_only_render_at_age(tmp_path, monkeypatch, age_seconds, *, cache_seconds=60):
    plugin = _plugin(tmp_path)
    settings = _connected_settings()
    settings.update(
        {
            "cacheSeconds": cache_seconds,
            "cameraEnabled": True,
            "_theme_render_only": True,
            "_inkypi_theme": _theme_context("night"),
        }
    )
    cache_file = plugin._cache_file(settings["host"], settings["serialNumber"])
    plugin._write_cache(
        cache_file,
        {
            "fetched_at": bambu_module.time.time() - age_seconds,
            "status": _cached_status(),
        },
    )
    original_cache = Path(cache_file).read_bytes()
    rendered = {}

    def capture_status(status, dimensions):
        rendered.update(status)
        return Image.new("RGB", dimensions)

    monkeypatch.setattr(plugin, "_render_status", capture_status)
    monkeypatch.setattr(
        plugin,
        "_fetch_report",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("theme-only called MQTT")
        ),
    )
    monkeypatch.setattr(
        plugin,
        "_attach_camera_frame",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("theme-only called camera")
        ),
    )

    image = plugin.generate_image(settings, _BambuDeviceConfig())

    assert Path(cache_file).read_bytes() == original_cache
    return image, rendered


def test_theme_only_snapshot_from_the_last_data_cycle_is_cacheable(tmp_path, monkeypatch):
    # 2026-10-19 18:26 sunset catch-up ran 122 s after the 300 s DATA fetch.
    image, rendered = _theme_only_render_at_age(tmp_path, monkeypatch, 122)

    assert rendered["source"] == "cache"
    assert "warning" not in rendered
    assert image.info.get("inkypi_skip_cache") is not True
    assert read_source_provenance(image) is SourceProvenance.STALE_CACHE


@pytest.mark.parametrize(
    ("cache_seconds", "age_seconds", "cacheable"),
    [("0", 122, True), ("1800", 1200, True), ("1800", 1801, False)],
)
def test_theme_only_limit_is_the_larger_of_cache_seconds_and_the_theme_window(
    tmp_path, monkeypatch, cache_seconds, age_seconds, cacheable,
):
    image, rendered = _theme_only_render_at_age(
        tmp_path, monkeypatch, age_seconds, cache_seconds=cache_seconds,
    )

    assert (image.info.get("inkypi_skip_cache") is not True) is cacheable
    assert rendered["source"] == ("cache" if cacheable else "stale")


def test_theme_only_expired_cache_stays_stale_and_non_cacheable(tmp_path, monkeypatch):
    image, rendered = _theme_only_render_at_age(
        tmp_path,
        monkeypatch,
        bambu_module.THEME_ONLY_STATUS_MAX_AGE_SECONDS + 1,
    )

    assert rendered["source"] == "stale"
    assert "warning" in rendered
    assert image.info["inkypi_skip_cache"] is True
    assert read_source_provenance(image) is SourceProvenance.STALE_CACHE


def test_theme_only_cold_cache_stays_local_and_preserves_offline_semantics(tmp_path, monkeypatch):
    plugin = _plugin(tmp_path)
    settings = _connected_settings()
    settings.update(
        {
            "cameraEnabled": True,
            "_theme_render_only": True,
            "_inkypi_theme": _theme_context("night"),
        }
    )
    calls = {"mqtt": 0, "camera": 0, "write": 0}

    def count_call(name):
        def inner(*args, **kwargs):
            calls[name] += 1
            raise AssertionError(f"theme-only called {name}")

        return inner

    monkeypatch.setattr(plugin, "_fetch_report", count_call("mqtt"))
    monkeypatch.setattr(plugin, "_attach_camera_frame", count_call("camera"))
    monkeypatch.setattr(plugin, "_write_cache", count_call("write"))

    image = plugin.generate_image(settings, _BambuDeviceConfig())

    assert calls == {"mqtt": 0, "camera": 0, "write": 0}
    assert image.info["inkypi_skip_cache"] is True
    assert not (tmp_path / "cache").exists()


def test_original_day_palette_and_canonical_night_change_pixels(tmp_path):
    plugin = _plugin(tmp_path)
    settings = _connected_settings()
    settings.update({"cacheSeconds": 3600, "cameraEnabled": False})
    cache_file = plugin._cache_file(settings["host"], settings["serialNumber"])
    plugin._write_cache(
        cache_file,
        {"fetched_at": bambu_module.time.time(), "status": _cached_status()},
    )

    day_settings = {
        **settings,
        "themeMode": "night",
        "_inkypi_theme": _theme_context("day"),
    }
    night_settings = {
        **settings,
        "themeMode": "day",
        "_inkypi_theme": _theme_context("night"),
    }

    day = plugin.generate_image(day_settings, _BambuDeviceConfig())
    night = plugin.generate_image(night_settings, _BambuDeviceConfig())

    assert day.getpixel((0, 0)) == bambu_module.PAPER
    assert night.getpixel((0, 0)) == _theme_context("night")["palette"]["background"]
    assert day.tobytes() != night.tobytes()
    assert bambu_module._ACTIVE_COLORS.get() is None


def test_canonical_night_palette_recolors_neutral_wordmark_ink(tmp_path, monkeypatch):
    plugin = _plugin(tmp_path)
    night_ink = _theme_context("night")["palette"]["ink"]
    monkeypatch.setattr(plugin, "_load_bambulab_logo", lambda: None)
    monkeypatch.setattr(
        plugin,
        "_load_title_wordmark",
        lambda: Image.new("RGBA", TITLE_WORDMARK_SIZE, (0, 0, 0, 255)),
    )
    settings = {
        "demoMode": True,
        "_inkypi_theme": _theme_context("night"),
    }

    image = plugin.generate_image(settings, _BambuDeviceConfig())

    assert image.getpixel((23, 14)) == night_ink


def test_night_theme_black_and_white_filaments_keep_data_colors_and_contrast_borders(
    tmp_path,
):
    plugin = _plugin(tmp_path)
    colors = bambu_module._render_colors(_theme_context("night"))
    token = bambu_module._ACTIVE_COLORS.set(colors)
    try:
        image = Image.new("RGB", (70, 34), colors["panel"])
        draw = ImageDraw.Draw(image)
        plugin._draw_spool(draw, 16, 16, 14, "111111FF")
        plugin._draw_spool(draw, 50, 16, 14, "FFFFFFFF")
        black_fill = image.getpixel((16 + 10, 16))
        black_border = image.getpixel((16 + 14, 16))
        white_fill = image.getpixel((50 + 10, 16))
        white_border = image.getpixel((50 + 14, 16))
    finally:
        bambu_module._ACTIVE_COLORS.reset(token)

    assert black_fill != colors["ink"]
    assert white_fill != colors["ink"]
    assert bambu_module.BambuMonitor._luma(white_fill) - bambu_module.BambuMonitor._luma(black_fill) > 180
    assert abs(
        bambu_module.BambuMonitor._luma(black_border)
        - bambu_module.BambuMonitor._luma(black_fill)
    ) > 100
    assert abs(
        bambu_module.BambuMonitor._luma(white_border)
        - bambu_module.BambuMonitor._luma(white_fill)
    ) > 100


def _wcag_contrast(first, second):
    def luminance(color):
        channels = []
        for channel in color:
            value = channel / 255
            channels.append(
                value / 12.92
                if value <= 0.04045
                else ((value + 0.055) / 1.055) ** 2.4
            )
        return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2]

    lighter, darker = sorted((luminance(first), luminance(second)), reverse=True)
    return (lighter + 0.05) / (darker + 0.05)


def test_night_theme_badges_choose_the_higher_contrast_text_candidate(tmp_path):
    plugin = _plugin(tmp_path)
    colors = bambu_module._render_colors(_theme_context("night"))
    token = bambu_module._ACTIVE_COLORS.set(colors)
    try:
        fills = [
            plugin._source_color("cache"),
            plugin._status_color("RUNNING", 0),
        ]
        for fill in fills:
            chosen = plugin._contrast_text(fill)
            candidate_ratios = {
                candidate: _wcag_contrast(fill, candidate)
                for candidate in (colors["ink"], colors["paper"])
            }
            assert candidate_ratios[chosen] == max(candidate_ratios.values())
            assert candidate_ratios[chosen] >= 4.5 or all(
                ratio < 4.5 for ratio in candidate_ratios.values()
            )
    finally:
        bambu_module._ACTIVE_COLORS.reset(token)


def test_generate_image_passes_machine_identity_settings_to_live_status(tmp_path, monkeypatch):
    plugin = _plugin(tmp_path)
    settings = _connected_settings()
    settings.update({"printerName": "Workshop Printer", "printerModel": "A1"})
    rendered = {}

    monkeypatch.setattr(
        plugin,
        "_fetch_report",
        lambda *args, **kwargs: {"print": {"gcode_state": "IDLE"}},
    )

    def capture_status(status, dimensions):
        rendered.update(status)
        return Image.new("RGB", dimensions)

    monkeypatch.setattr(plugin, "_render_status", capture_status)

    plugin.generate_image(settings, _BambuDeviceConfig())

    assert rendered["machine_name"] == "Workshop Printer"
    assert rendered["machine_model"] == "A1"


def test_demo_mode_uses_current_machine_identity_settings(tmp_path, monkeypatch):
    plugin = _plugin(tmp_path)
    settings = _connected_settings()
    settings.update({
        "demoMode": True,
        "printerName": "Current Device",
        "printerModel": "P1S",
    })
    rendered = {}

    def capture_status(status, dimensions):
        rendered.update(status)
        return Image.new("RGB", dimensions)

    monkeypatch.setattr(plugin, "_render_status", capture_status)

    image = plugin.generate_image(settings, _BambuDeviceConfig())

    assert rendered["machine_name"] == "Current Device"
    assert rendered["machine_model"] == "P1S"
    assert rendered["host"] == "demo.local"
    assert rendered["serial"] == "01P-DEMO"
    assert read_source_provenance(image) is SourceProvenance.LOCAL_FALLBACK
    assert image.info["inkypi_skip_cache"] is True


def test_setup_required_keeps_known_machine_identity(tmp_path, monkeypatch):
    plugin = _plugin(tmp_path)
    settings = _connected_settings()
    settings.update({
        "accessCode": "",
        "printerName": "Current Device",
        "printerModel": "P1S",
    })
    rendered = {}

    class MissingCredentialConfig(_BambuDeviceConfig):
        def load_env_key(self, key):
            return ""

    def capture_status(status, dimensions):
        rendered.update(status)
        return Image.new("RGB", dimensions)

    monkeypatch.setattr(plugin, "_render_status", capture_status)

    plugin.generate_image(settings, MissingCredentialConfig())

    assert rendered["source"] == "setup"
    assert rendered["machine_name"] == "Current Device"
    assert rendered["machine_model"] == "P1S"
    assert rendered["host"] == settings["host"]
    assert rendered["serial"] == settings["serialNumber"]


def test_cached_status_uses_current_machine_identity_settings(tmp_path, monkeypatch):
    plugin = _plugin(tmp_path)
    settings = _connected_settings()
    settings.update({
        "cacheSeconds": 60,
        "printerName": "Workshop Printer",
        "printerModel": "A1",
    })
    cache_file = plugin._cache_file(settings["host"], settings["serialNumber"])
    plugin._write_cache(
        cache_file,
        {"fetched_at": bambu_module.time.time(), "status": _cached_status()},
    )
    rendered = {}

    def capture_status(status, dimensions):
        rendered.update(status)
        return Image.new("RGB", dimensions)

    monkeypatch.setattr(plugin, "_render_status", capture_status)

    plugin.generate_image(settings, _BambuDeviceConfig())

    assert rendered["machine_name"] == "Workshop Printer"
    assert rendered["machine_model"] == "A1"


def test_no_cache_error_keeps_known_machine_identity(tmp_path, monkeypatch):
    plugin = _plugin(tmp_path)
    settings = _connected_settings()
    settings.update({"printerName": "Current Device", "printerModel": "P1S"})
    rendered = {}

    monkeypatch.setattr(plugin, "_fetch_report", _raise_no_route)

    def capture_status(status, dimensions):
        rendered.update(status)
        return Image.new("RGB", dimensions)

    monkeypatch.setattr(plugin, "_render_status", capture_status)

    image = plugin.generate_image(settings, _BambuDeviceConfig())

    assert image.info["inkypi_skip_cache"] is True
    assert rendered["source"] == "error"
    assert rendered["machine_name"] == "Current Device"
    assert rendered["machine_model"] == "P1S"
    assert rendered["host"] == settings["host"]
    assert rendered["serial"] == settings["serialNumber"]


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
    assert read_source_provenance(image) is SourceProvenance.STALE_CACHE
    cached = plugin._read_cache(cache_file)
    assert cached["status"]["source"] == "live"
    assert "warning" not in cached["status"]


def test_force_refresh_aliases_bypass_fresh_bambu_cache(tmp_path, monkeypatch):
    plugin = _plugin(tmp_path)
    settings = _connected_settings()
    settings["cacheSeconds"] = 3600
    cache_file = plugin._cache_file(settings["host"], settings["serialNumber"])
    fetch_calls = []

    def fetch_report(*_args, **_kwargs):
        fetch_calls.append(True)
        return {"print": {"gcode_state": "IDLE"}}

    monkeypatch.setattr(plugin, "_fetch_report", fetch_report)
    monkeypatch.setattr(
        plugin,
        "_render_status",
        lambda _status, dimensions: Image.new("RGB", dimensions),
    )

    for force_key in ("forceRefresh", "force_refresh"):
        plugin._write_cache(
            cache_file,
            {"fetched_at": bambu_module.time.time(), "status": _cached_status()},
        )
        image = plugin.generate_image(
            {**settings, force_key: "true"},
            _BambuDeviceConfig(),
        )
        assert read_source_provenance(image) is SourceProvenance.LIVE

    assert len(fetch_calls) == 2
