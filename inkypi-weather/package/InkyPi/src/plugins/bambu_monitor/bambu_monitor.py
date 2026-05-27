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
BLACK = (0, 0, 0)
GRAY = (170, 170, 170)

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
        dimensions = device_config.get_resolution()
        if device_config.get_config("orientation") == "vertical":
            dimensions = dimensions[::-1]

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
                img.convert("RGB").save(path, "JPEG", quality=85)
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
        img = Image.new("RGB", (width, height), BLACK)
        draw = ImageDraw.Draw(img)

        margin = 22
        draw.text((margin, 12), "BAMBU MONITOR", fill=WHITE, font=self._font(25, True))
        draw.text((width - margin, 18), status.get("updated_at", ""), fill=WHITE, font=self._font(13, True), anchor="ra")
        draw.line((margin, 48, width - margin, 48), fill=WHITE, width=2)

        left = (margin, 64, 292, 326)
        camera = (310, 64, width - margin, 326)
        thermals = (margin, 344, 370, height - margin)
        ams = (388, 344, width - margin, height - margin)
        self._draw_box(draw, left, "PRINT")
        self._draw_box(draw, camera, "LIVE VIEW")
        self._draw_box(draw, thermals, "THERMALS")
        self._draw_box(draw, ams, "AMS")

        self._draw_print_panel(draw, left, status)
        self._draw_camera_panel(draw, camera, status)
        self._draw_thermal_panel(draw, thermals, status)
        self._draw_ams_panel(draw, ams, status)

        source = str(status.get("source") or "").upper()
        if source:
            draw.text((width - margin, height - 16), source, fill=WHITE, font=self._font(11, True), anchor="ra")
        warning = status.get("warning")
        if warning:
            self._draw_fit_text(draw, str(warning), margin + 8, height - 37, width - margin - 80, 12, False)

        return self._threshold_image(img)

    def _draw_print_panel(self, draw, box, status):
        left, top, right, bottom = box
        state = self._state_label(status.get("state"))
        state_font = self._fit_font(draw, state, right - left - 28, 36, True, 20)
        draw.text((left + 14, top + 42), state, fill=WHITE, font=state_font)
        stage = self._display_stage(status.get("stage"), status.get("host"))
        self._draw_fit_text(draw, stage, left + 16, top + 84, right - left - 32, 14, False)

        progress = int(max(0, min(100, self._num(status.get("progress"), 0))))
        bar = (left + 16, top + 110, right - 16, top + 142)
        draw.rectangle(bar, outline=WHITE, width=2)
        fill_w = int((bar[2] - bar[0] - 4) * progress / 100)
        if fill_w > 0:
            draw.rectangle((bar[0] + 2, bar[1] + 2, bar[0] + 2 + fill_w, bar[3] - 2), fill=WHITE)
        draw.text((right - 18, top + 152), f"{progress}%", fill=WHITE, font=self._font(26, True), anchor="ra")

        remaining = self._remaining_text(status.get("remaining_minutes"))
        draw.text((left + 16, top + 190), remaining, fill=WHITE, font=self._font(16, True))
        file_name = str(status.get("file") or "No active job")
        self._draw_wrapped_fit_text(draw, file_name, left + 16, top + 224, right - left - 32, 2, 14, True)

    def _draw_camera_panel(self, draw, box, status):
        left, top, right, bottom = box
        image_box = (left + 14, top + 40, right - 14, bottom - 16)
        camera_img = self._load_camera_image(status)
        if camera_img:
            prepared = self._prepare_camera_image(
                camera_img,
                (image_box[2] - image_box[0], image_box[3] - image_box[1]),
                CAMERA_FRAME_CENTERING,
            )
            draw.rectangle(image_box, fill=BLACK)
            draw.bitmap((image_box[0], image_box[1]), prepared.convert("1"), fill=WHITE)
            draw.rectangle(image_box, outline=WHITE, width=1)
            label = status.get("camera_updated_at")
            if label:
                draw.text((right - 18, top + 13), f"FRAME {label}", fill=WHITE, font=self._font(10, True), anchor="ra")
            return

        waiting_img = self._load_waiting_image()
        if waiting_img:
            prepared = self._prepare_camera_image(waiting_img, (image_box[2] - image_box[0], image_box[3] - image_box[1]))
            draw.rectangle(image_box, fill=BLACK)
            draw.bitmap((image_box[0], image_box[1]), prepared.convert("1"), fill=WHITE)
            draw.rectangle(image_box, outline=WHITE, width=1)
            draw.text((right - 18, top + 13), "WAITING", fill=WHITE, font=self._font(10, True), anchor="ra")
            return

        draw.rectangle(image_box, outline=WHITE, width=1)
        draw.text(
            ((image_box[0] + image_box[2]) // 2, (image_box[1] + image_box[3]) // 2 - 10),
            "NO CAMERA FRAME",
            fill=WHITE,
            font=self._font(18, True),
            anchor="mm",
        )
        error = status.get("camera_error")
        if error:
            self._draw_fit_text(draw, error[:60], image_box[0] + 12, image_box[3] - 26, image_box[2] - image_box[0] - 24, 11, False)

    def _draw_thermal_panel(self, draw, box, status):
        left, top, right, bottom = box
        rows = [
            ("NOZZLE", self._temp_text(status.get("nozzle"), status.get("nozzle_target"))),
            ("BED", self._temp_text(status.get("bed"), status.get("bed_target"))),
            ("CHAMBER", self._temp_text(status.get("chamber"), None)),
            ("FAN", self._percent_text(status.get("fan"))),
            ("SPEED", self._speed_text(status.get("speed"))),
        ]
        y = top + 36
        for label, value in rows:
            draw.text((left + 16, y - 1), label, fill=GRAY, font=self._font(11, True))
            draw.text((right - 16, y - 2), value, fill=WHITE, font=self._font(13, True), anchor="ra")
            y += 16
            if y < bottom - 12:
                draw.line((left + 14, y - 3, right - 14, y - 3), fill=WHITE, width=1)

    def _draw_ams_panel(self, draw, box, status):
        left, top, right, bottom = box
        error = status.get("error")
        error_text = "OK" if not error or str(error) == "0" else f"ERR {error}"
        draw.text((right - 16, top + 11), error_text, fill=WHITE, font=self._font(13, True), anchor="ra")

        trays = status.get("ams") or []
        if not trays:
            draw.text((left + 16, top + 44), "AMS: not reported", fill=WHITE, font=self._font(16))
            return

        x = left + 16
        y = top + 42
        visible_trays = trays[:4]
        tray_w = max(64, min(110, int((right - left - 32 - 8 * (len(visible_trays) - 1)) / max(len(visible_trays), 1))))
        for tray in visible_trays:
            if x + tray_w > right - 10:
                break
            tray_box = (x, y, x + tray_w, bottom - 18)
            draw.rectangle(tray_box, outline=WHITE, width=3 if tray.get("active") else 1)
            draw.text((x + 7, y + 7), str(tray.get("id") or "?"), fill=WHITE, font=self._font(13, True))
            if tray.get("active"):
                tag = (x + tray_w - 30, y + 7, x + tray_w - 7, y + 20)
                draw.rectangle(tag, fill=WHITE)
                draw.text(((tag[0] + tag[2]) // 2, tag[1] + 1), "USE", fill=BLACK, font=self._font(9, True), anchor="ma")

            remain = tray.get("remain")
            detail = str(tray.get("type") or "FIL").upper()
            if self._is_nonnegative(remain):
                detail = f"{detail} {remain}%"
            self._draw_fit_text(draw, detail, x + 7, y + 24, tray_w - 14, 11, True)

            color = tray.get("color")
            chip = (x + 7, y + 41, x + 18, y + 52)
            self._draw_filament_color_chip(draw, chip, color)
            self._draw_fit_text(draw, self._filament_color_label(color), x + 23, y + 39, tray_w - 30, 10, False)
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

    def _draw_box(self, draw, box, title=None):
        draw.rectangle(box, outline=WHITE, width=2)
        if title:
            draw.text((box[0] + 12, box[1] + 8), title, fill=WHITE, font=self._font(15, True))

    def _draw_fit_text(self, draw, text, x, y, max_width, start_size, bold=False):
        font = self._fit_font(draw, text, max_width, start_size, bold, 9)
        draw.text((x, y), str(text), fill=WHITE, font=font)

    def _draw_filament_color_chip(self, draw, box, color):
        rgb = self._parse_filament_color(color)
        if not rgb:
            draw.rectangle(box, outline=WHITE)
            draw.line((box[0], box[1], box[2], box[3]), fill=WHITE, width=1)
            return

        r, g, b = rgb
        luminance = 0.2126 * r + 0.7152 * g + 0.0722 * b
        fill = WHITE if luminance >= 170 else BLACK
        draw.rectangle(box, outline=WHITE, fill=fill)
        if 55 <= luminance < 210:
            pattern = BLACK if fill == WHITE else WHITE
            left, top, right, bottom = box[0] + 2, box[1] + 2, box[2] - 2, box[3] - 2
            for y in range(top, bottom + 1, 4):
                draw.line((left, y, right, y), fill=pattern, width=1)

    def _draw_wrapped_fit_text(self, draw, text, x, y, max_width, max_lines, start_size, bold=False):
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
            self._draw_fit_text(draw, line, x, y + idx * (start_size + 4), max_width, start_size, bold)

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

    def _prepare_camera_image(self, image, size, centering=(0.5, 0.5)):
        image = ImageOps.fit(image.convert("L"), size, method=Image.Resampling.LANCZOS, centering=centering)
        image = ImageOps.autocontrast(image, cutoff=2)
        image = image.point(lambda p: 255 if p >= 112 else 0, mode="1")
        return image.convert("L")

    def _demo_camera_image(self):
        img = Image.new("RGB", (640, 360), (20, 20, 20))
        draw = ImageDraw.Draw(img)
        draw.rectangle((40, 90, 600, 280), outline=(240, 240, 240), width=8)
        draw.rectangle((170, 130, 470, 245), outline=(240, 240, 240), width=5)
        draw.line((80, 300, 560, 300), fill=(240, 240, 240), width=10)
        draw.ellipse((285, 160, 355, 230), fill=(240, 240, 240))
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
    def _threshold_image(img):
        return img.convert("L").point(lambda p: 255 if p >= 128 else 0, mode="1").convert("RGB")

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
