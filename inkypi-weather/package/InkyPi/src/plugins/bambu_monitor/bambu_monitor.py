from plugins.base_plugin.base_plugin import BasePlugin

from io import BytesIO
from PIL import Image, ImageDraw, ImageFont, ImageOps
from utils.app_utils import get_font
from datetime import datetime
import colorsys
import json
import logging
import os
import socket
import ssl
import struct
import time

logger = logging.getLogger(__name__)

WHITE = (255, 255, 255)

# Color tokens follow docs/color-ui-guidelines.md: warm paper, process black
# linework, and limited vintage comic process-color accents.
PAPER = (255, 248, 220)  # 25Y PANTONE 100, vintage comic paper ground
PANEL = (255, 253, 240)
PANEL_BLUE = (235, 246, 255)  # 25B PANTONE 304 family, paper-tinted
PANEL_GOLD = (255, 239, 176)  # 50Y PANTONE 101 family, paper-tinted
PANEL_GREEN = (235, 249, 236)  # 50Y-25B PANTONE 358 family, paper-tinted
PANEL_ORANGE = (255, 239, 222)  # 50Y-25R PANTONE 156 family, paper-tinted
INK = (8, 8, 8)  # PROCESS BLACK
MUTED = (126, 112, 82)  # 50Y-25R-25B PANTONE 465 family
RULE = (190, 177, 134)
ACCENT_BLUE = (0, 92, 185)  # 100B-25R PANTONE 285 family
ACCENT_CYAN = (0, 163, 173)  # 50Y-100B PANTONE 327 family
ACCENT_GOLD = (255, 196, 30)  # 100Y-25R PANTONE 123 family
ACCENT_ORANGE = (245, 122, 38)  # 100Y-50R PANTONE ORANGE 021 family
CINNABAR = (222, 45, 38)  # 100Y-100R PANTONE RED 032 family
MALACHITE = (0, 152, 82)  # 100Y-100B PANTONE 354 family
ACCENT_PURPLE = (98, 58, 160)  # 100R-100B PANTONE 266 family
BROWN = (137, 88, 56)  # 100Y-50R-50B PANTONE 470 family
FILAMENT_GRAY = (188, 177, 147)  # 50Y-25R-25B PANTONE 465 family, lightened


def _blend(foreground, background, amount):
    amount = max(0.0, min(1.0, float(amount)))
    return tuple(
        int(background[index] + (foreground[index] - background[index]) * amount)
        for index in range(3)
    )

MQTT_PROTOCOL_NAME = b"MQTT"
MQTT_PROTOCOL_LEVEL = 4
MQTT_KEEPALIVE = 30
MQTT_USERNAME = "bblp"
CAMERA_USERNAME = "bblp"
JPEG_START = b"\xff\xd8\xff"
JPEG_END = b"\xff\xd9"
CAMERA_WAITING_FAILURES = 3
CAMERA_FRAME_CENTERING = (0.5, 0.0)

class MqttProtocolError(RuntimeError):
    pass


def _encode_utf8(value):
    raw = str(value).encode("utf-8")
    return len(raw).to_bytes(2, "big") + raw


def _encode_remaining_length(value):
    encoded = bytearray()
    while True:
        byte = value % 128
        value //= 128
        if value > 0:
            byte |= 128
        encoded.append(byte)
        if value == 0:
            break
    return bytes(encoded)


def _decode_remaining_length(sock):
    multiplier = 1
    value = 0
    while True:
        raw = sock.recv(1)
        if not raw:
            raise MqttProtocolError("MQTT connection closed while reading length")
        byte = raw[0]
        value += (byte & 127) * multiplier
        if (byte & 128) == 0:
            return value
        multiplier *= 128
        if multiplier > 128 * 128 * 128:
            raise MqttProtocolError("Malformed MQTT remaining length")


def _read_exact(sock, size):
    chunks = []
    remaining = size
    while remaining > 0:
        chunk = sock.recv(remaining)
        if not chunk:
            raise MqttProtocolError("MQTT connection closed")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def _send_packet(sock, packet_type, payload):
    sock.sendall(bytes([packet_type]) + _encode_remaining_length(len(payload)) + payload)


def _read_packet(sock):
    fixed = sock.recv(1)
    if not fixed:
        raise MqttProtocolError("MQTT connection closed")
    remaining = _decode_remaining_length(sock)
    payload = _read_exact(sock, remaining) if remaining else b""
    return fixed[0], payload


class BambuMqttClient:
    def __init__(self, host, port, serial, access_code, timeout):
        self.host = host
        self.port = int(port)
        self.serial = serial
        self.access_code = access_code
        self.timeout = float(timeout)
        self.sock = None

    def __enter__(self):
        raw_sock = socket.create_connection((self.host, self.port), timeout=self.timeout)
        raw_sock.settimeout(self.timeout)
        context = ssl.create_default_context()
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        self.sock = context.wrap_socket(raw_sock, server_hostname=self.host)
        self._connect()
        return self

    def __exit__(self, exc_type, exc, tb):
        try:
            if self.sock:
                _send_packet(self.sock, 0xE0, b"")
        except Exception:
            pass
        try:
            if self.sock:
                self.sock.close()
        except Exception:
            pass

    def _connect(self):
        client_id = f"inkypi_bambu_{int(time.time())}"
        variable_header = (
            _encode_utf8(MQTT_PROTOCOL_NAME.decode("ascii"))
            + bytes([MQTT_PROTOCOL_LEVEL, 0xC2])
            + MQTT_KEEPALIVE.to_bytes(2, "big")
        )
        payload = (
            _encode_utf8(client_id)
            + _encode_utf8(MQTT_USERNAME)
            + _encode_utf8(self.access_code)
        )
        _send_packet(self.sock, 0x10, variable_header + payload)
        packet_type, body = _read_packet(self.sock)
        if packet_type != 0x20 or len(body) < 2:
            raise MqttProtocolError("Invalid MQTT CONNACK")
        if body[1] != 0:
            raise MqttProtocolError(f"MQTT auth/connect failed with code {body[1]}")

    def subscribe_report(self):
        topic = f"device/{self.serial}/report"
        packet_id = 1
        payload = packet_id.to_bytes(2, "big") + _encode_utf8(topic) + b"\x00"
        _send_packet(self.sock, 0x82, payload)
        end = time.time() + self.timeout
        while time.time() < end:
            packet_type, body = _read_packet(self.sock)
            if packet_type == 0x90:
                return
        raise MqttProtocolError("No MQTT SUBACK received")

    def request_full_update(self):
        topic = f"device/{self.serial}/request"
        message = {
            "pushing": {
                "sequence_id": "0",
                "command": "pushall",
            }
        }
        payload = _encode_utf8(topic) + json.dumps(message, separators=(",", ":")).encode("utf-8")
        _send_packet(self.sock, 0x30, payload)

    def wait_for_report(self):
        end = time.time() + self.timeout
        last_error = None
        while time.time() < end:
            try:
                packet_type, body = _read_packet(self.sock)
            except socket.timeout as exc:
                last_error = exc
                break
            packet_base = packet_type & 0xF0
            if packet_base == 0x30 and len(body) >= 2:
                topic_len = int.from_bytes(body[:2], "big")
                payload = body[2 + topic_len:]
                try:
                    data = json.loads(payload.decode("utf-8", errors="replace"))
                except json.JSONDecodeError:
                    continue
                if "print" in data:
                    return data
        if last_error:
            raise MqttProtocolError("Timed out waiting for printer report")
        raise MqttProtocolError("No printer report received")


class BambuCameraClient:
    def __init__(self, host, port, access_code, timeout):
        self.host = host
        self.port = int(port)
        self.access_code = access_code
        self.timeout = float(timeout)

    def capture_frame(self):
        auth = self._auth_packet()
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        with socket.create_connection((self.host, self.port), timeout=self.timeout) as raw_sock:
            raw_sock.settimeout(self.timeout)
            with context.wrap_socket(raw_sock, server_hostname=self.host) as sock:
                sock.settimeout(self.timeout)
                sock.sendall(auth)
                return self._read_jpeg(sock)

    def _auth_packet(self):
        packet = bytearray()
        packet += struct.pack("<IIII", 0x40, 0x3000, 0, 0)
        packet += CAMERA_USERNAME.encode("ascii")[:32].ljust(32, b"\x00")
        packet += str(self.access_code).encode("ascii")[:32].ljust(32, b"\x00")
        return bytes(packet)

    def _read_jpeg(self, sock):
        deadline = time.time() + self.timeout
        buffer = bytearray()
        while time.time() < deadline:
            try:
                chunk = sock.recv(16384)
            except socket.timeout:
                break
            if not chunk:
                break
            buffer += chunk

            if len(buffer) >= 16:
                frame_size = struct.unpack("<I", buffer[:4])[0]
                if 0 < frame_size <= 4 * 1024 * 1024 and len(buffer) >= 16 + frame_size:
                    frame = bytes(buffer[16:16 + frame_size])
                    if frame.startswith(JPEG_START[:2]):
                        return frame

            start = buffer.find(JPEG_START)
            if start >= 0:
                end = buffer.find(JPEG_END, start + len(JPEG_START))
                if end >= 0:
                    return bytes(buffer[start:end + len(JPEG_END)])
                if start > 0:
                    del buffer[:start]
            if len(buffer) > 4 * 1024 * 1024:
                del buffer[:-1024]
        raise RuntimeError("No camera JPEG frame received")


class BambuMonitor(BasePlugin):
    def generate_settings_template(self):
        template_params = super().generate_settings_template()
        template_params["api_key"] = {
            "required": False,
            "service": "Bambu Lab",
            "expected_key": "BAMBU_ACCESS_CODE",
        }
        template_params["style_settings"] = False
        return template_params

    def generate_image(self, settings, device_config):
        dimensions = self.get_dimensions(device_config)

        if self._bool_setting(settings, "demoMode", False):
            status = self._demo_status()
            return self._render_status(status, dimensions)

        host = str(settings.get("host") or "").strip()
        serial = str(settings.get("serialNumber") or "").strip()
        port = self._int_setting(settings, "port", 8883, 1, 65535)
        timeout = self._int_setting(settings, "timeoutSeconds", 6, 2, 20)
        cache_seconds = self._int_setting(settings, "cacheSeconds", 60, 0, 3600)
        camera_enabled = self._bool_setting(settings, "cameraEnabled", True)
        camera_port = self._int_setting(settings, "cameraPort", 6000, 1, 65535)
        camera_timeout = self._int_setting(settings, "cameraTimeoutSeconds", 18, 2, 20)
        env_key = str(settings.get("accessCodeEnv") or "BAMBU_ACCESS_CODE").strip()
        access_code = str(settings.get("accessCode") or "").strip()
        if not access_code and env_key:
            access_code = str(device_config.load_env_key(env_key) or "").strip()

        if not host or not serial or not access_code:
            return self._render_setup_required(dimensions, host, serial, bool(access_code))

        cache_file = self._cache_file(host, serial)
        cached = self._read_cache(cache_file)
        now = time.time()
        if cached and cache_seconds > 0 and now - cached.get("fetched_at", 0) < cache_seconds:
            status = cached.get("status") or {}
            status["source"] = "cache"
            if camera_enabled:
                self._attach_camera_frame(status, host, camera_port, access_code, camera_timeout)
                self._write_cache(cache_file, {"fetched_at": cached.get("fetched_at", now), "status": status})
            return self._render_status(status, dimensions)

        try:
            report = self._fetch_report(host, port, serial, access_code, timeout, settings)
            status = self._normalize_report(report, host, serial)
            if camera_enabled:
                self._attach_camera_frame(status, host, camera_port, access_code, camera_timeout)
            self._write_cache(cache_file, {"fetched_at": now, "status": status})
            return self._render_status(status, dimensions)
        except Exception as exc:
            logger.error(f"Bambu Monitor failed: {exc}", exc_info=True)
            if cached and cached.get("status"):
                status = cached["status"]
                status["source"] = "stale"
                status["warning"] = str(exc)
                return self._render_status(status, dimensions)
            return self._render_error(dimensions, str(exc))

    def _fetch_report(self, host, port, serial, access_code, timeout, settings):
        with BambuMqttClient(host, port, serial, access_code, timeout) as client:
            client.subscribe_report()
            if self._bool_setting(settings, "requestFullUpdate", True):
                client.request_full_update()
            return client.wait_for_report()

    def _attach_camera_frame(self, status, host, camera_port, access_code, timeout):
        try:
            frame = BambuCameraClient(host, camera_port, access_code, timeout).capture_frame()
            path = self._camera_file(host, status.get("serial") or "printer")
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with Image.open(BytesIO(frame)) as img:
                img.verify()
            with open(path, "wb") as f:
                f.write(frame)
            status["camera_path"] = path
            status["camera_updated_at"] = datetime.now().strftime("%H:%M")
            status["camera_failure_count"] = 0
            status["camera_waiting"] = False
        except Exception as exc:
            logger.warning(f"Bambu camera frame unavailable: {exc}")
            status["camera_error"] = str(exc)
            failures = int(self._num(status.get("camera_failure_count"), 0) or 0) + 1
            status["camera_failure_count"] = failures
            stale_path = self._camera_file(host, status.get("serial") or "printer")
            if os.path.exists(stale_path) and failures < CAMERA_WAITING_FAILURES:
                status["camera_path"] = stale_path
                status["camera_waiting"] = False
            else:
                status["camera_path"] = None
                status["camera_waiting"] = True

    def _normalize_report(self, report, host, serial):
        data = report.get("print") or {}
        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        status = {
            "host": host,
            "serial": serial,
            "updated_at": now,
            "source": "live",
            "state": str(data.get("gcode_state") or data.get("print_status") or data.get("stg_cur") or "UNKNOWN"),
            "stage": str(data.get("mc_print_stage") or data.get("stg_cur") or ""),
            "progress": self._num(data.get("mc_percent"), 0),
            "remaining_minutes": data.get("mc_remaining_time"),
            "file": str(data.get("subtask_name") or data.get("gcode_file") or data.get("project_name") or ""),
            "nozzle": self._num(data.get("nozzle_temper")),
            "nozzle_target": self._num(data.get("nozzle_target_temper")),
            "bed": self._num(data.get("bed_temper")),
            "bed_target": self._num(data.get("bed_target_temper")),
            "chamber": self._num(data.get("chamber_temper")),
            "fan": self._num(data.get("fan_gear")),
            "speed": str(data.get("spd_lvl") or data.get("speed_lvl") or ""),
            "error": data.get("print_error") or data.get("fail_reason") or 0,
            "ams": self._extract_ams(data),
            "camera_path": None,
            "camera_error": None,
            "camera_failure_count": 0,
            "camera_waiting": False,
        }
        return status

    def _extract_ams(self, data):
        ams = data.get("ams")
        if not isinstance(ams, dict):
            return []
        active = str(ams.get("tray_now") or ams.get("tray_tar") or "")
        trays = []
        for unit in ams.get("ams", []) or []:
            unit_id = str(unit.get("id", ""))
            for tray in unit.get("tray", []) or []:
                tray_id = str(tray.get("id", tray.get("tray_id", "")))
                label = f"{unit_id}-{tray_id}" if unit_id else tray_id
                trays.append({
                    "id": label or "?",
                    "active": tray_id == active or label == active,
                    "type": str(tray.get("tray_type") or tray.get("tray_sub_brands") or tray.get("type") or "FIL"),
                    "color": str(tray.get("tray_color") or ""),
                    "remain": tray.get("remain"),
                })
        return trays[:8]

    def _demo_status(self):
        return {
            "host": "demo.local",
            "serial": "01P-DEMO",
            "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "source": "demo",
            "state": "RUNNING",
            "stage": "Printing",
            "progress": 63,
            "remaining_minutes": 147,
            "file": "desk_organizer_v4_plate_1.3mf",
            "nozzle": 219,
            "nozzle_target": 220,
            "bed": 64,
            "bed_target": 65,
            "chamber": 35,
            "fan": 70,
            "speed": "2",
            "error": 0,
            "camera_image": self._demo_camera_image(),
            "camera_updated_at": datetime.now().strftime("%H:%M"),
            "ams": [
                {"id": "0-0", "active": False, "type": "PLA", "color": "FFFFFFFF", "remain": 92},
                {"id": "0-1", "active": True, "type": "PLA", "color": "111111FF", "remain": 54},
                {"id": "0-2", "active": False, "type": "PETG", "color": "E67E22FF", "remain": 71},
                {"id": "0-3", "active": False, "type": "SUP", "color": "CCCCCCFF", "remain": 38},
            ],
        }

    def _render_status(self, status, dimensions):
        width, height = dimensions
        img = Image.new("RGB", (width, height), PAPER)
        draw = ImageDraw.Draw(img)

        margin = 22
        self._draw_halftone(draw, (width - 190, 8, width - 28, 50), ACCENT_BLUE, PAPER, 9, 1)
        self._draw_halftone(draw, (22, height - 46, 178, height - 12), CINNABAR, PAPER, 10, 1)
        title_box = (margin, 12, margin + 234, 44)
        draw.rectangle((title_box[0] + 3, title_box[1] + 3, title_box[2] + 3, title_box[3] + 3), fill=ACCENT_ORANGE)
        draw.rectangle(title_box, fill=ACCENT_GOLD)
        draw.rectangle(title_box, outline=INK, width=2)
        draw.text((margin + 10, 16), "BAMBU MONITOR", fill=INK, font=self._font(23, True))
        draw.text((width - margin, 18), status.get("updated_at", ""), fill=MUTED, font=self._font(13, True), anchor="ra")
        draw.line((margin, 52, width - margin, 52), fill=INK, width=2)
        draw.line((margin, 56, width - margin, 56), fill=ACCENT_BLUE, width=2)

        left = (margin, 64, 292, 326)
        camera = (310, 64, width - margin, 326)
        thermals = (margin, 344, 370, height - margin)
        ams = (388, 344, width - margin, height - margin)
        self._draw_box(draw, left, "PRINT", ACCENT_GOLD, PANEL_GOLD)
        self._draw_box(draw, camera, "LIVE VIEW", ACCENT_BLUE, PANEL_BLUE)
        self._draw_box(draw, thermals, "THERMALS", CINNABAR, PANEL_ORANGE)
        self._draw_box(draw, ams, "AMS", MALACHITE, PANEL_GREEN)

        self._draw_print_panel(draw, left, status)
        self._draw_camera_panel(img, draw, camera, status)
        self._draw_thermal_panel(draw, thermals, status)
        self._draw_ams_panel(draw, ams, status)

        source = str(status.get("source") or "").upper()
        if source:
            source_font = self._font(11, True)
            source_w = self._text_width(draw, source, source_font)
            source_box = (width - margin - source_w - 18, height - 29, width - margin, height - 12)
            source_color = self._source_color(source)
            draw.rectangle(source_box, fill=source_color, outline=INK, width=1)
            draw.text(
                (source_box[0] + 9, height - 28),
                source,
                fill=self._contrast_text(source_color),
                font=source_font,
            )
        warning = status.get("warning")
        if warning:
            self._draw_fit_text(draw, str(warning), margin + 8, height - 39, width - margin - 90, 12, False, CINNABAR)

        return img

    def _draw_print_panel(self, draw, box, status):
        left, top, right, bottom = box
        state = self._state_label(status.get("state"))
        state_color = self._status_color(status.get("state"), status.get("error"))
        state_font = self._fit_font(draw, state, right - left - 28, 36, True, 20)
        state_box = (left + 14, top + 38, right - 14, top + 76)
        draw.rectangle(state_box, fill=state_color, outline=INK, width=2)
        state_text_box = draw.textbbox((0, 0), state, font=state_font)
        state_y = state_box[1] + 5 - state_text_box[1]
        draw.text((left + 24, state_y), state, fill=self._contrast_text(state_color), font=state_font)
        stage = self._display_stage(status.get("stage"), status.get("host"))
        self._draw_fit_text(draw, stage, left + 16, top + 84, right - left - 32, 14, False, MUTED)

        progress = int(max(0, min(100, self._num(status.get("progress"), 0))))
        bar = (left + 16, top + 110, right - 16, top + 142)
        draw.rectangle(bar, fill=WHITE, outline=INK, width=2)
        fill_w = int((bar[2] - bar[0] - 4) * progress / 100)
        if fill_w > 0:
            fill_box = (bar[0] + 2, bar[1] + 2, bar[0] + 2 + fill_w, bar[3] - 2)
            draw.rectangle(fill_box, fill=state_color)
            for x in range(fill_box[0] + 8, fill_box[2], 12):
                draw.line((x, fill_box[1], x - 8, fill_box[3]), fill=INK, width=1)
        draw.text((right - 18, top + 152), f"{progress}%", fill=INK, font=self._font(26, True), anchor="ra")

        remaining = self._remaining_text(status.get("remaining_minutes"))
        remaining_box = (left + 16, top + 187, right - 16, top + 211)
        draw.rectangle(remaining_box, fill=PANEL, outline=INK, width=1)
        draw.rectangle((remaining_box[0], remaining_box[1], remaining_box[0] + 7, remaining_box[3]), fill=ACCENT_BLUE)
        draw.text((left + 29, top + 190), remaining, fill=INK, font=self._font(16, True))
        file_name = str(status.get("file") or "No active job")
        self._draw_wrapped_fit_text(draw, file_name, left + 16, top + 224, right - left - 32, 2, 14, True, INK)

    def _draw_camera_panel(self, canvas, draw, box, status):
        left, top, right, bottom = box
        image_box = (left + 14, top + 40, right - 14, bottom - 16)
        camera_img = self._load_camera_image(status)
        if camera_img:
            prepared = self._fit_camera_image(
                camera_img,
                (image_box[2] - image_box[0], image_box[3] - image_box[1]),
                CAMERA_FRAME_CENTERING,
            )
            draw.rectangle(image_box, fill=INK)
            canvas.paste(prepared, (image_box[0], image_box[1]))
            draw.rectangle(image_box, outline=INK, width=2)
            label = status.get("camera_updated_at")
            if label:
                draw.text((right - 18, top + 13), f"FRAME {label}", fill=ACCENT_BLUE, font=self._font(10, True), anchor="ra")
            return

        waiting_img = self._load_waiting_image()
        if waiting_img:
            prepared = self._fit_camera_image(waiting_img, (image_box[2] - image_box[0], image_box[3] - image_box[1]))
            draw.rectangle(image_box, fill=INK)
            canvas.paste(prepared, (image_box[0], image_box[1]))
            draw.rectangle(image_box, outline=INK, width=2)
            draw.text((right - 18, top + 13), "WAITING", fill=ACCENT_ORANGE, font=self._font(10, True), anchor="ra")
            return

        draw.rectangle(image_box, fill=_blend(ACCENT_BLUE, PANEL_BLUE, 0.08), outline=INK, width=2)
        self._draw_halftone(draw, image_box, ACCENT_BLUE, PANEL_BLUE, 18, 1)
        draw.text(
            ((image_box[0] + image_box[2]) // 2, (image_box[1] + image_box[3]) // 2 - 10),
            "NO CAMERA FRAME",
            fill=ACCENT_BLUE,
            font=self._font(18, True),
            anchor="mm",
        )
        error = status.get("camera_error")
        if error:
            self._draw_fit_text(
                draw,
                error[:60],
                image_box[0] + 12,
                image_box[3] - 26,
                image_box[2] - image_box[0] - 24,
                11,
                False,
                CINNABAR,
            )

    def _draw_thermal_panel(self, draw, box, status):
        left, top, right, bottom = box
        rows = [
            ("NOZZLE", self._temp_text(status.get("nozzle"), status.get("nozzle_target")), CINNABAR),
            ("BED", self._temp_text(status.get("bed"), status.get("bed_target")), ACCENT_ORANGE),
            ("CHAMBER", self._temp_text(status.get("chamber"), None), ACCENT_GOLD),
            ("FAN", self._percent_text(status.get("fan")), ACCENT_BLUE),
            ("SPEED", self._speed_text(status.get("speed")), MALACHITE),
        ]
        y = top + 36
        for label, value, accent in rows:
            draw.rectangle((left + 16, y + 1, left + 25, y + 10), fill=accent, outline=INK, width=1)
            draw.text((left + 32, y - 1), label, fill=MUTED, font=self._font(11, True))
            draw.text((right - 16, y - 2), value, fill=INK, font=self._font(13, True), anchor="ra")
            y += 16
            if y < bottom - 12:
                draw.line((left + 14, y - 3, right - 14, y - 3), fill=RULE, width=1)

    def _draw_ams_panel(self, draw, box, status):
        left, top, right, bottom = box
        error = status.get("error")
        error_text = "OK" if not error or str(error) == "0" else f"ERR {error}"
        error_color = MALACHITE if error_text == "OK" else CINNABAR
        draw.text((right - 16, top + 11), error_text, fill=error_color, font=self._font(13, True), anchor="ra")

        trays = status.get("ams") or []
        if not trays:
            draw.text((left + 16, top + 44), "AMS: not reported", fill=INK, font=self._font(16))
            return

        x = left + 16
        y = top + 42
        visible_trays = trays[:4]
        tray_w = max(64, min(110, int((right - left - 32 - 8 * (len(visible_trays) - 1)) / max(len(visible_trays), 1))))
        for tray in visible_trays:
            if x + tray_w > right - 10:
                break
            tray_box = (x, y, x + tray_w, bottom - 18)
            swatch = self._filament_swatch_color(tray.get("color")) or FILAMENT_GRAY
            tray_fill = _blend(swatch, PANEL, 0.10)
            outline = MALACHITE if tray.get("active") else INK
            draw.rectangle(tray_box, fill=tray_fill, outline=outline, width=3 if tray.get("active") else 1)
            draw.rectangle((tray_box[0], tray_box[1], tray_box[2], tray_box[1] + 5), fill=swatch)
            draw.text((x + 7, y + 9), str(tray.get("id") or "?"), fill=INK, font=self._font(13, True))
            if tray.get("active"):
                tag = (x + tray_w - 30, y + 7, x + tray_w - 7, y + 20)
                draw.rectangle(tag, fill=MALACHITE, outline=INK, width=1)
                draw.text(((tag[0] + tag[2]) // 2, tag[1] + 1), "USE", fill=PAPER, font=self._font(9, True), anchor="ma")

            remain = tray.get("remain")
            detail = str(tray.get("type") or "FIL").upper()
            if self._is_nonnegative(remain):
                detail = f"{detail} {remain}%"
            self._draw_fit_text(draw, detail, x + 7, y + 27, tray_w - 14, 11, True, INK)

            color = tray.get("color")
            chip = (x + 7, y + 41, x + 18, y + 52)
            self._draw_filament_color_chip(draw, chip, color)
            self._draw_fit_text(draw, self._filament_color_label(color), x + 23, y + 39, tray_w - 30, 10, False, MUTED)
            x += tray_w + 8

    def _render_setup_required(self, dimensions, host, serial, has_code):
        status = {
            "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "source": "setup",
            "state": "CONFIG",
            "stage": "Enter printer IP, serial, and access code",
            "progress": 0,
            "remaining_minutes": None,
            "file": "Missing: "
            + ", ".join(
                name for name, present in [
                    ("host", bool(host)),
                    ("serial", bool(serial)),
                    ("access code", has_code),
                ] if not present
            ),
            "nozzle": None,
            "nozzle_target": None,
            "bed": None,
            "bed_target": None,
            "chamber": None,
            "fan": None,
            "speed": "",
            "error": 0,
            "ams": [],
        }
        return self._render_status(status, dimensions)

    def _render_error(self, dimensions, message):
        status = {
            "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "source": "error",
            "state": "OFFLINE",
            "stage": "Unable to read printer",
            "progress": 0,
            "remaining_minutes": None,
            "file": message[:120],
            "nozzle": None,
            "nozzle_target": None,
            "bed": None,
            "bed_target": None,
            "chamber": None,
            "fan": None,
            "speed": "",
            "error": 1,
            "ams": [],
        }
        return self._render_status(status, dimensions)

    def _cache_file(self, host, serial):
        safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in f"{host}_{serial}")
        return os.path.join(self.get_plugin_dir("cache"), f"{safe}.json")

    def _camera_file(self, host, serial):
        safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in f"{host}_{serial}")
        return os.path.join(self.get_plugin_dir("cache"), f"{safe}_camera.jpg")

    def _camera_waiting_file(self):
        return os.path.join(os.path.dirname(__file__), "camera_waiting.png")

    def _read_cache(self, path):
        try:
            if os.path.exists(path):
                with open(path, "r", encoding="utf-8") as f:
                    return json.load(f)
        except Exception as exc:
            logger.warning(f"Failed to read Bambu cache: {exc}")
        return None

    def _write_cache(self, path, data):
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=True, indent=2)
        except Exception as exc:
            logger.warning(f"Failed to write Bambu cache: {exc}")

    def _draw_box(self, draw, box, title=None, accent=ACCENT_BLUE, fill=PANEL):
        left, top, right, bottom = [int(value) for value in box]
        draw.rectangle((left + 3, top + 4, right + 3, bottom + 4), fill=ACCENT_ORANGE)
        draw.rectangle((left, top, right, bottom), fill=fill)
        draw.rectangle((left, top, right, bottom), outline=INK, width=2)
        draw.rectangle((left, top, right, top + 7), fill=accent)
        if title:
            draw.text((left + 12, top + 12), title, fill=INK, font=self._font(15, True))

    def _draw_halftone(self, draw, bounds, color, paper, spacing, radius):
        left, top, right, bottom = [int(value) for value in bounds]
        dot = _blend(color, paper, 0.22)
        for y in range(top, bottom, max(4, int(spacing))):
            offset = (spacing // 2) if ((y // max(1, spacing)) % 2) else 0
            for x in range(left + offset, right, max(4, int(spacing))):
                draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill=dot)

    def _draw_fit_text(self, draw, text, x, y, max_width, start_size, bold=False, fill=INK):
        font = self._fit_font(draw, text, max_width, start_size, bold, 9)
        draw.text((x, y), str(text), fill=fill, font=font)

    def _draw_filament_color_chip(self, draw, box, color):
        swatch = self._filament_swatch_color(color)
        if not swatch:
            draw.rectangle(box, fill=PANEL, outline=INK)
            draw.line((box[0], box[1], box[2], box[3]), fill=CINNABAR, width=1)
            return

        draw.rectangle(box, outline=INK, fill=swatch)
        luminance = self._luma(swatch)
        if 55 <= luminance < 210:
            pattern = INK if luminance > 150 else PAPER
            left, top, right, bottom = box[0] + 2, box[1] + 2, box[2] - 2, box[3] - 2
            for y in range(top, bottom + 1, 4):
                draw.line((left, y, right, y), fill=pattern, width=1)

    def _draw_wrapped_fit_text(self, draw, text, x, y, max_width, max_lines, start_size, bold=False, fill=INK):
        words = str(text).replace("_", " ").split()
        lines = []
        current = ""
        font = self._font(start_size, bold)
        for word in words:
            candidate = f"{current} {word}".strip()
            if self._text_width(draw, candidate, font) <= max_width:
                current = candidate
            else:
                if current:
                    lines.append(current)
                current = word
            if len(lines) >= max_lines:
                break
        if current and len(lines) < max_lines:
            lines.append(current)
        for idx, line in enumerate(lines[:max_lines]):
            self._draw_fit_text(draw, line, x, y + idx * (start_size + 4), max_width, start_size, bold, fill)

    def _load_camera_image(self, status):
        image = status.get("camera_image")
        if image:
            return image
        path = status.get("camera_path")
        if not path or not os.path.exists(path):
            return None
        try:
            return Image.open(path).convert("RGB")
        except Exception as exc:
            logger.warning(f"Failed to load Bambu camera image: {exc}")
            return None

    def _load_waiting_image(self):
        path = self._camera_waiting_file()
        if not os.path.exists(path):
            return None
        try:
            return Image.open(path).convert("RGB")
        except Exception as exc:
            logger.warning(f"Failed to load Bambu waiting image: {exc}")
            return None

    def _fit_camera_image(self, image, size, centering=(0.5, 0.5)):
        return ImageOps.fit(image.convert("RGB"), size, method=Image.Resampling.LANCZOS, centering=centering)

    def _demo_camera_image(self):
        img = Image.new("RGB", (640, 360), PANEL_BLUE)
        draw = ImageDraw.Draw(img)
        self._draw_halftone(draw, (420, 22, 608, 132), ACCENT_BLUE, PANEL_BLUE, 16, 2)
        draw.rectangle((0, 286, 640, 360), fill=_blend(ACCENT_BLUE, PANEL_BLUE, 0.12))
        draw.rectangle((40, 90, 600, 280), fill=PANEL, outline=INK, width=8)
        draw.rectangle((170, 130, 470, 245), fill=PANEL_GOLD, outline=INK, width=5)
        draw.rectangle((232, 100, 408, 122), fill=MALACHITE, outline=INK, width=3)
        draw.line((80, 300, 560, 300), fill=INK, width=10)
        draw.rectangle((72, 292, 168, 308), fill=ACCENT_ORANGE, outline=INK, width=3)
        draw.rectangle((472, 292, 568, 308), fill=ACCENT_BLUE, outline=INK, width=3)
        draw.ellipse((285, 160, 355, 230), fill=ACCENT_GOLD, outline=INK, width=4)
        draw.rectangle((292, 214, 348, 242), fill=CINNABAR, outline=INK, width=3)
        return img

    def _font(self, size, bold=False):
        font_size = int(size)
        font_name = "Jost-SemiBold.ttf" if bold else "Jost.ttf"
        font_paths = [
            os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "static", "fonts", font_name)),
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        ]
        try:
            for font_path in font_paths:
                if os.path.isfile(font_path):
                    return ImageFont.truetype(font_path, font_size)
            font = get_font("Jost", font_size, "bold" if bold else "normal")
            if font:
                return font
        except Exception as exc:
            logger.warning(f"Falling back to default font: {exc}")
        return ImageFont.load_default()

    @staticmethod
    def _text_width(draw, text, font):
        bbox = draw.textbbox((0, 0), str(text), font=font)
        return bbox[2] - bbox[0]

    def _fit_font(self, draw, text, max_width, start_size, bold=False, min_size=9):
        size = int(start_size)
        while size > min_size:
            font = self._font(size, bold)
            if self._text_width(draw, text, font) <= max_width:
                return font
            size -= 1
        return self._font(min_size, bold)


    @staticmethod
    def _bool_setting(settings, key, default=False):
        value = settings.get(key)
        if value is None:
            return default
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() not in ("", "0", "false", "off", "no")

    @staticmethod
    def _int_setting(settings, key, default, minimum, maximum):
        try:
            value = int(float(settings.get(key, default)))
        except (TypeError, ValueError):
            value = default
        return max(minimum, min(maximum, value))

    @staticmethod
    def _num(value, default=None):
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _is_nonnegative(value):
        try:
            return float(value) >= 0
        except (TypeError, ValueError):
            return False

    @classmethod
    def _status_color(cls, state, error=0):
        if error and str(error) != "0":
            return CINNABAR
        normalized = cls._state_label(state)
        if normalized in ("PRINTING", "RUNNING", "DONE"):
            return MALACHITE
        if normalized in ("PAUSED", "CONFIG"):
            return ACCENT_ORANGE
        if normalized in ("ERROR", "FAILED", "OFFLINE"):
            return CINNABAR
        return ACCENT_BLUE

    @staticmethod
    def _source_color(source):
        normalized = str(source or "").strip().lower()
        if normalized in ("error", "offline"):
            return CINNABAR
        if normalized in ("setup", "stale", "cache"):
            return ACCENT_ORANGE
        if normalized == "demo":
            return ACCENT_GOLD
        return ACCENT_BLUE

    @staticmethod
    def _luma(rgb):
        r, g, b = rgb
        return 0.2126 * r + 0.7152 * g + 0.0722 * b

    @classmethod
    def _contrast_text(cls, fill):
        return INK if cls._luma(fill) > 150 else PAPER

    @staticmethod
    def _parse_filament_color(color):
        raw = str(color or "").strip().lstrip("#")
        if len(raw) < 6:
            return None
        try:
            return int(raw[0:2], 16), int(raw[2:4], 16), int(raw[4:6], 16)
        except ValueError:
            return None

    @classmethod
    def _filament_swatch_color(cls, color):
        rgb = cls._parse_filament_color(color)
        if not rgb:
            return None

        r, g, b = rgb
        max_channel = max(rgb)
        min_channel = min(rgb)
        spread = max_channel - min_channel
        if max_channel < 45:
            return INK
        if min_channel > 215 and spread < 35:
            return WHITE
        if spread < 30:
            return FILAMENT_GRAY

        hue, saturation, value = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
        degrees = hue * 360
        if saturation < 0.18:
            if value > 0.75:
                return WHITE
            if value < 0.25:
                return INK
            return FILAMENT_GRAY
        if degrees < 15 or degrees >= 345:
            return CINNABAR
        if degrees < 45:
            return ACCENT_ORANGE
        if degrees < 75:
            return ACCENT_GOLD
        if degrees < 165:
            return MALACHITE
        if degrees < 195:
            return ACCENT_CYAN
        if degrees < 255:
            return ACCENT_BLUE
        if degrees < 295:
            return ACCENT_PURPLE
        if degrees < 345:
            return CINNABAR
        return BROWN

    @classmethod
    def _filament_color_label(cls, color):
        rgb = cls._parse_filament_color(color)
        if not rgb:
            return "COLOR --"

        r, g, b = rgb
        max_channel = max(rgb)
        min_channel = min(rgb)
        spread = max_channel - min_channel
        if max_channel < 45:
            return "BLACK"
        if min_channel > 215 and spread < 35:
            return "WHITE"
        if spread < 30:
            return "GRAY"

        hue, saturation, value = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
        degrees = hue * 360
        if saturation < 0.18:
            if value > 0.75:
                return "WHITE"
            if value < 0.25:
                return "BLACK"
            return "GRAY"
        if degrees < 15 or degrees >= 345:
            return "RED"
        if degrees < 45:
            return "ORANGE"
        if degrees < 75:
            return "YELLOW"
        if degrees < 165:
            return "GREEN"
        if degrees < 195:
            return "CYAN"
        if degrees < 255:
            return "BLUE"
        if degrees < 295:
            return "PURPLE"
        if degrees < 345:
            return "PINK"
        return "COLOR"

    @staticmethod
    def _state_label(value):
        state = str(value or "UNKNOWN").upper()
        aliases = {
            "RUNNING": "PRINTING",
            "FINISH": "DONE",
            "FINISHED": "DONE",
            "FAILED": "ERROR",
            "PAUSE": "PAUSED",
        }
        return aliases.get(state, state)

    @staticmethod
    def _display_stage(stage, fallback):
        text = str(stage or "").strip()
        if not text:
            return str(fallback or "")
        if text.isdigit():
            return f"Stage {text}"
        return text

    @staticmethod
    def _remaining_text(value):
        try:
            minutes = int(float(value))
        except (TypeError, ValueError):
            return "Remaining: --"
        hours = minutes // 60
        mins = minutes % 60
        if hours:
            return f"Remaining: {hours}h {mins}m"
        return f"Remaining: {mins}m"

    @staticmethod
    def _temp_text(current, target):
        if current is None:
            return "--"
        current_text = f"{float(current):.0f}C"
        if target not in (None, ""):
            return f"{current_text}/{float(target):.0f}C"
        return current_text

    @staticmethod
    def _percent_text(value):
        if value is None:
            return "--"
        return f"{float(value):.0f}%"

    @staticmethod
    def _speed_text(value):
        if value in (None, ""):
            return "--"
        return f"LV {value}"
