from plugins.base_plugin.base_plugin import BasePlugin
from plugins.base_plugin.render_provenance import (
    SourceProvenance,
    attach_source_provenance,
)

from PIL import Image, ImageDraw, ImageOps
from utils.app_utils import get_base_ui_font
from utils.image_utils import text_width
from utils.safe_image import ImageLimits, safe_open_image
from utils.theme_utils import contrast_ratio, relative_luminance
from datetime import datetime, timedelta
from contextvars import ContextVar
from functools import lru_cache
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
FILAMENT_BLACK = (14, 15, 16)
FILAMENT_WHITE = (250, 250, 246)
FILAMENT_DARK_BORDER = (24, 26, 28)
FILAMENT_LIGHT_BORDER = (232, 235, 230)


@lru_cache(maxsize=48)
def _icon(name, size, ground, ink):
    """A generated icon ``size`` px on its longer side, its outlines in ``ink`` where they would
    vanish on ``ground``; None when the file is missing."""
    path = os.path.join(ICON_DIR, f"{name}.png")
    if not os.path.isfile(path):
        return None
    try:
        with Image.open(path) as source:
            art = source.convert("RGBA")
    except (OSError, ValueError):
        return None
    art.thumbnail((size, size), Image.Resampling.LANCZOS)
    pixels = art.load()
    for y in range(art.height):
        for x in range(art.width):
            red, green, blue, alpha = pixels[x, y]
            if alpha >= 8 and contrast_ratio((red, green, blue), ground) < ICON_MIN_CONTRAST:
                pixels[x, y] = (*ink, alpha)
    return art


def _blend(foreground, background, amount):
    amount = max(0.0, min(1.0, float(amount)))
    return tuple(
        int(background[index] + (foreground[index] - background[index]) * amount)
        for index in range(3)
    )


_ACTIVE_COLORS = ContextVar("bambu_monitor_active_colors", default=None)


def _active_color(name, fallback):
    colors = _ACTIVE_COLORS.get()
    if isinstance(colors, dict):
        return colors.get(name, fallback)
    return fallback


def _render_colors(theme):
    if not isinstance(theme, dict) or theme.get("mode") != "night":
        return {
            "paper": PAPER,
            "panel": PANEL,
            "panel_blue": PANEL_BLUE,
            "panel_gold": PANEL_GOLD,
            "panel_green": PANEL_GREEN,
            "panel_orange": PANEL_ORANGE,
            "ink": INK,
            "muted": MUTED,
            "rule": RULE,
            "accent_blue": ACCENT_BLUE,
            "accent_cyan": ACCENT_CYAN,
            "accent_gold": ACCENT_GOLD,
            "accent_orange": ACCENT_ORANGE,
            "cinnabar": CINNABAR,
            "malachite": MALACHITE,
            "accent_purple": ACCENT_PURPLE,
            "brown": BROWN,
            "filament_gray": FILAMENT_GRAY,
            "filament_black": FILAMENT_BLACK,
            "filament_white": FILAMENT_WHITE,
            "filament_dark_border": FILAMENT_DARK_BORDER,
            "filament_light_border": FILAMENT_LIGHT_BORDER,
        }
    roles = theme.get("palette") if isinstance(theme, dict) else None
    roles = roles if isinstance(roles, dict) else {}

    def role(name, fallback):
        value = roles.get(name, fallback)
        try:
            rgb = tuple(int(channel) for channel in value)
        except (TypeError, ValueError):
            return fallback
        return rgb if len(rgb) == 3 else fallback

    paper = role("background", PAPER)
    panel = role("panel", PANEL)
    ink = role("ink", INK)
    muted = role("muted", MUTED)
    rule = role("rule", RULE)
    accent = role("accent", ACCENT_BLUE)

    def semantic(base):
        return _blend(base, accent, 0.78)

    gold = semantic(ACCENT_GOLD)
    orange = semantic(ACCENT_ORANGE)
    red = semantic(CINNABAR)
    green = semantic(MALACHITE)
    cyan = semantic(ACCENT_CYAN)
    purple = semantic(ACCENT_PURPLE)
    brown = semantic(BROWN)
    return {
        "paper": paper,
        "panel": panel,
        "panel_blue": _blend(accent, panel, 0.12),
        "panel_gold": _blend(gold, panel, 0.14),
        "panel_green": _blend(green, panel, 0.12),
        "panel_orange": _blend(orange, panel, 0.12),
        "ink": ink,
        "muted": muted,
        "rule": rule,
        "accent_blue": accent,
        "accent_cyan": cyan,
        "accent_gold": gold,
        "accent_orange": orange,
        "cinnabar": red,
        "malachite": green,
        "accent_purple": purple,
        "brown": brown,
        "filament_gray": _blend(muted, panel, 0.72),
        # Filament swatches represent printer data, not theme/UI semantics.
        "filament_black": FILAMENT_BLACK,
        "filament_white": FILAMENT_WHITE,
        "filament_dark_border": FILAMENT_DARK_BORDER,
        "filament_light_border": FILAMENT_LIGHT_BORDER,
    }

MQTT_PROTOCOL_NAME = b"MQTT"
MQTT_PROTOCOL_LEVEL = 4
MQTT_KEEPALIVE = 30
MQTT_USERNAME = "bblp"
CAMERA_USERNAME = "bblp"
JPEG_START = b"\xff\xd8\xff"
JPEG_END = b"\xff\xd9"
CAMERA_WAITING_FAILURES = 3
CAMERA_FRAME_CENTERING = (0.5, 0.0)
# A theme-only redraw repaints the status snapshot of the last successful fetch,
# the same data the current image shows. cacheSeconds only spaces MQTT requests
# and is shorter than the DATA interval, so theme changes accept snapshots up to
# 900 s (the scheduler's stalled-DATA age for a 300 s interval) before going
# stale. A cacheSeconds above that is already covered by the fresh branch.
THEME_ONLY_STATUS_MAX_AGE_SECONDS = 900
SKIP_CACHE_IMAGE_INFO_KEY = "inkypi_skip_cache"
TITLE_WORDMARK_IMAGE = "title_wordmark.png"
TITLE_WORDMARK_SIZE = (292, 52)
BAMBU_LAB_LOGO_IMAGE = "bambulab_logo.png"
BAMBU_LAB_LOGO_SIZE = (116, 34)
BAMBU_LAB_LOGO_GAP = 8
MACHINE_NAME_MAX_LENGTH = 28
MACHINE_MODEL_MAX_LENGTH = 16
_MACHINE_IDENTITY_UNSET = object()
# Camera-hero layout: a 16:9 camera frame with the AMS spools under it on the
# left, the job column (progress, times, file, temperatures, speed) on the right.
LAYOUT_MARGIN = 22
LAYOUT_GAP = 16
JOB_COLUMN_WIDTH = 232
HEADER_RULE_Y = 54
CONTENT_TOP = 64
CARD_RADIUS = 10
NOZZLE_SCALE_C = 300
BED_SCALE_C = 120
AMS_SLOTS = 4
AMS_SLOT_GAP = 10
AMS_SPOOL_RADIUS = 22
AMS_USE_PILL_HEIGHT = 18
AMS_USE_PILL_PAD = 11
SPEED_LEVEL_NAMES = {"1": "SILENT", "2": "STANDARD", "3": "SPORT", "4": "LUDICROUS"}
# Generated comic-print icons (icons/, GPT Image 2.5; icons.source.json) beside each reading.
# On a dark page their black outlines take the page's ink.
ICON_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "icons")
ICON_MIN_CONTRAST = 2.0
STAT_ICON_SIZE = 26
LAYER_ICON_SIZE = 18
# The print's progress as a game-style gauge: PROGRESS_SEGMENTS slanted blocks (slant a share of
# the bar's height) PROGRESS_SEGMENT_GAP px apart.
PROGRESS_SEGMENTS, PROGRESS_SEGMENT_SLANT, PROGRESS_SEGMENT_GAP = 20, 0.45, 3
# The stage line's icon by what the printer is doing, first match wins (lower-case stage words).
STAGE_ICONS = (
    (("paused", "pause"), "paused"),
    (("filament",), "ams"),
    (("leveling", "scanning", "build plate", "lidar", "homing", "sweeping"), "leveling"),
    (("heat", "temperature", "preheat"), "heating"),
    (("calibrat", "inspecting", "cleaning"), "stage"),
    (("printing",), "nozzle"),
)
# The printer's stg_cur sub-stage codes; others (and -1/255 idle) are not shown.
PRINT_STAGE_NAMES = {
    0: "Printing",
    1: "Auto bed leveling",
    2: "Heatbed preheating",
    3: "Sweeping XY mech mode",
    4: "Changing filament",
    5: "M400 pause",
    6: "Paused: filament runout",
    7: "Heating hotend",
    8: "Calibrating extrusion",
    9: "Scanning bed surface",
    10: "Inspecting first layer",
    11: "Identifying build plate type",
    12: "Calibrating micro lidar",
    13: "Homing toolhead",
    14: "Cleaning nozzle tip",
    15: "Checking extruder temperature",
    16: "Paused by user",
    17: "Paused: front cover falling",
    18: "Calibrating micro lidar",
    19: "Calibrating extrusion flow",
    20: "Paused: nozzle temperature",
    21: "Paused: heatbed temperature",
}

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
        theme = settings.get("_inkypi_theme")
        if not isinstance(theme, dict):
            theme = self.resolve_theme(settings, device_config)
        token = _ACTIVE_COLORS.set(_render_colors(theme))
        try:
            return self._generate_image(settings, device_config)
        finally:
            _ACTIVE_COLORS.reset(token)

    def _generate_image(self, settings, device_config):
        dimensions = self.get_dimensions(device_config)

        if self._bool_setting(settings, "_theme_render_only", False):
            return self._render_theme_only(settings, dimensions)

        if self._bool_setting(settings, "demoMode", False):
            status = self._demo_status()
            self._apply_machine_identity(status, settings)
            return attach_source_provenance(
                self._non_cacheable_image(self._render_status(status, dimensions)),
                SourceProvenance.LOCAL_FALLBACK,
            )

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
            return attach_source_provenance(
                self._render_setup_required(
                    dimensions,
                    host,
                    serial,
                    bool(access_code),
                    settings,
                ),
                SourceProvenance.LOCAL_FALLBACK,
            )

        cache_file = self._cache_file(host, serial)
        cached = self._read_cache(cache_file)
        now = time.time()
        force_refresh = any(
            self._bool_setting(settings, key, False)
            for key in ("forceRefresh", "force_refresh")
        )
        if not force_refresh and self._cache_entry_is_fresh(cached, cache_seconds, now):
            status = dict(cached.get("status") or {})
            self._apply_machine_identity(status, settings, host=host, serial=serial)
            status["source"] = "cache"
            if camera_enabled:
                self._attach_camera_frame(status, host, camera_port, access_code, camera_timeout)
                self._write_cache(cache_file, {"fetched_at": cached.get("fetched_at", now), "status": status})
            return attach_source_provenance(
                self._render_status(status, dimensions),
                SourceProvenance.FRESH_CACHE,
            )

        try:
            report = self._fetch_report(host, port, serial, access_code, timeout, settings)
            status = self._normalize_report(report, host, serial, settings)
            if camera_enabled:
                self._attach_camera_frame(status, host, camera_port, access_code, camera_timeout)
            self._write_cache(cache_file, {"fetched_at": now, "status": status})
            return attach_source_provenance(
                self._render_status(status, dimensions),
                SourceProvenance.LIVE,
            )
        except Exception as exc:
            if self._is_expected_connection_failure(exc):
                logger.warning(f"Bambu printer unavailable; preserving previous cache: {exc}")
            else:
                logger.error(f"Bambu Monitor failed: {exc}", exc_info=True)
            if cached and cached.get("status"):
                status = dict(cached["status"])
                self._apply_machine_identity(status, settings, host=host, serial=serial)
                status["source"] = "stale"
                status["warning"] = str(exc)
                return attach_source_provenance(
                    self._non_cacheable_image(self._render_status(status, dimensions)),
                    SourceProvenance.STALE_CACHE,
                )
            return attach_source_provenance(
                self._render_error(
                    dimensions,
                    str(exc),
                    host=host,
                    serial=serial,
                    settings=settings,
                ),
                SourceProvenance.LOCAL_FALLBACK,
            )

    def _render_theme_only(self, settings, dimensions):
        if self._bool_setting(settings, "demoMode", False):
            status = self._demo_status()
            self._apply_machine_identity(status, settings)
            return attach_source_provenance(
                self._non_cacheable_image(self._render_status(status, dimensions)),
                SourceProvenance.LOCAL_FALLBACK,
            )

        host = str(settings.get("host") or "").strip()
        serial = str(settings.get("serialNumber") or "").strip()
        cache_seconds = self._int_setting(settings, "cacheSeconds", 60, 0, 3600)
        now = time.time()
        if host and serial:
            cached = self._read_cache(self._cache_file(host, serial, create=False))
            if cached and cached.get("status"):
                status = dict(cached["status"])
                self._apply_machine_identity(
                    status,
                    settings,
                    host=host,
                    serial=serial,
                )
                if self._cache_entry_is_fresh(cached, cache_seconds, now):
                    status["source"] = "cache"
                    return attach_source_provenance(
                        self._render_status(status, dimensions),
                        SourceProvenance.FRESH_CACHE,
                    )
                theme_max_age = max(cache_seconds, THEME_ONLY_STATUS_MAX_AGE_SECONDS)
                if self._cache_entry_is_fresh(cached, theme_max_age, now):
                    status["source"] = "cache"
                    return attach_source_provenance(
                        self._render_status(status, dimensions),
                        SourceProvenance.STALE_CACHE,
                    )
                status["source"] = "stale"
                status["warning"] = (
                    "Cached printer status is older than the theme redraw limit; "
                    "theme-only redraw kept it stale"
                )
                return attach_source_provenance(
                    self._non_cacheable_image(
                        self._render_status(status, dimensions)
                    ),
                    SourceProvenance.STALE_CACHE,
                )

        if not host or not serial:
            return attach_source_provenance(
                self._render_setup_required(
                    dimensions,
                    host,
                    serial,
                    bool(settings.get("accessCode") or settings.get("accessCodeEnv")),
                    settings,
                ),
                SourceProvenance.LOCAL_FALLBACK,
            )

        return attach_source_provenance(
            self._render_error(
                dimensions,
                "No cached printer status is available for theme redraw",
                host=host,
                serial=serial,
                settings=settings,
            ),
            SourceProvenance.LOCAL_FALLBACK,
        )

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
            safe_open_image(frame, limits=ImageLimits(max_bytes=4 * 1024 * 1024))
            with open(path, "wb") as f:
                f.write(frame)
            status["camera_path"] = path
            status["camera_updated_at"] = datetime.now().strftime("%H:%M")
            status["camera_stale"] = False
            status["camera_failure_count"] = 0
            status["camera_waiting"] = False
        except Exception as exc:
            logger.warning(f"Bambu camera frame unavailable: {exc}")
            status["camera_error"] = str(exc)
            failures = int(self._num(status.get("camera_failure_count"), 0) or 0) + 1
            status["camera_failure_count"] = failures
            stale_path = self._camera_file(host, status.get("serial") or "printer")
            if os.path.exists(stale_path) and failures < CAMERA_WAITING_FAILURES:
                # The cached frame keeps the time it was captured, so it never passes for a live one.
                status["camera_path"] = stale_path
                status["camera_updated_at"] = datetime.fromtimestamp(os.path.getmtime(stale_path)).strftime("%H:%M")
                status["camera_stale"] = True
                status["camera_waiting"] = False
            else:
                status["camera_path"] = None
                status["camera_waiting"] = True

    def _normalize_report(self, report, host, serial, settings=None):
        data = report.get("print") or {}
        settings = settings or {}
        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        status = {
            "updated_at": now,
            "source": "live",
            "state": str(data.get("gcode_state") or data.get("print_status") or data.get("stg_cur") or "UNKNOWN"),
            "stage": self._stage_text(data),
            "progress": self._num(data.get("mc_percent"), 0),
            "remaining_minutes": data.get("mc_remaining_time"),
            "finish_at": self._finish_at(data.get("mc_remaining_time")),
            "layer": self._positive_int(data.get("layer_num")),
            "total_layers": self._positive_int(data.get("total_layer_num")),
            "file": str(data.get("subtask_name") or data.get("gcode_file") or data.get("project_name") or ""),
            "nozzle": self._num(data.get("nozzle_temper")),
            "nozzle_target": self._num(data.get("nozzle_target_temper")),
            "bed": self._num(data.get("bed_temper")),
            "bed_target": self._num(data.get("bed_target_temper")),
            "chamber": self._num(data.get("chamber_temper")),
            "fan": self._fan_percent(data),
            "speed": str(data.get("spd_lvl") or data.get("speed_lvl") or ""),
            "error": data.get("print_error") or data.get("fail_reason") or 0,
            "ams": self._extract_ams(data),
            "camera_path": None,
            "camera_error": None,
            "camera_failure_count": 0,
            "camera_waiting": False,
        }
        return self._apply_machine_identity(
            status,
            settings,
            host=host,
            serial=serial,
        )

    @staticmethod
    def _stage_text(data):
        """A readable sub-stage from stg_cur, else a non-numeric mc_print_stage."""
        try:
            code = int(data.get("stg_cur"))
        except (TypeError, ValueError):
            code = None
        if code in PRINT_STAGE_NAMES:
            running = str(data.get("gcode_state") or "").upper() == "RUNNING"
            return PRINT_STAGE_NAMES[code] if code or running else ""
        text = str(data.get("mc_print_stage") or "").strip()
        return "" if not text or text.lstrip("-").isdigit() else text

    @staticmethod
    def _finish_at(remaining_minutes):
        try:
            minutes = int(float(remaining_minutes))
        except (TypeError, ValueError):
            return None
        return (datetime.now() + timedelta(minutes=minutes)).strftime("%H:%M") if minutes > 0 else None

    @staticmethod
    def _positive_int(value):
        try:
            number = int(float(value))
        except (TypeError, ValueError):
            return None
        return number if number > 0 else None

    @classmethod
    def _fan_percent(cls, data):
        """Part cooling fan as a percentage (cooling_fan_speed is 0-15)."""
        speed = cls._num(data.get("cooling_fan_speed"))
        if speed is not None and 0 <= speed <= 15:
            return round(speed / 15 * 100)
        return cls._num(data.get("fan_gear"))

    @staticmethod
    def _apply_machine_identity(
        status,
        settings,
        *,
        host=_MACHINE_IDENTITY_UNSET,
        serial=_MACHINE_IDENTITY_UNSET,
    ):
        settings = settings or {}
        name = (
            str(settings.get("printerName") or "").strip()[:MACHINE_NAME_MAX_LENGTH]
            or "Bambu Printer"
        )
        model = (
            str(settings.get("printerModel") or "").strip()[:MACHINE_MODEL_MAX_LENGTH]
            or "MODEL --"
        )
        if host is not _MACHINE_IDENTITY_UNSET:
            status["host"] = str(host or "").strip()
        if serial is not _MACHINE_IDENTITY_UNSET:
            status["serial"] = str(serial or "").strip()
        status["machine_name"] = name
        status["machine_model"] = model
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
            "machine_name": "Workshop Printer",
            "machine_model": "A1",
            "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "source": "demo",
            "state": "RUNNING",
            "stage": "Printing",
            "progress": 63,
            "remaining_minutes": 147,
            "finish_at": self._finish_at(147),
            "layer": 87,
            "total_layers": 240,
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
        """Camera-hero layout: frame and AMS spools on the left, the job column on the right."""
        width, height = dimensions
        img = Image.new("RGB", (width, height), _active_color("paper", PAPER))
        draw = ImageDraw.Draw(img)
        self._draw_header(img, draw, status, width)

        margin = LAYOUT_MARGIN
        camera_right = width - margin - JOB_COLUMN_WIDTH - LAYOUT_GAP
        camera_bottom = CONTENT_TOP + round((camera_right - margin) * 9 / 16)
        self._draw_camera_hero(img, draw, (margin, CONTENT_TOP, camera_right, camera_bottom), status)
        self._draw_ams_strip(draw, (margin, camera_bottom + 8, camera_right, height - 14), status, canvas=img)
        self._draw_job_column(
            draw,
            (camera_right + LAYOUT_GAP, HEADER_RULE_Y + 2, width - margin, height - 14),
            status,
            canvas=img,
        )
        return img

    def _paste_icon(self, canvas, name, x, y, size, ground=None, align="left"):
        """Paste a generated icon, its left (or right) edge at ``x`` and its middle at ``y``;
        returns its width, 0 when there is no canvas or file."""
        if canvas is None:
            return 0
        icon = _icon(name, size, tuple(ground or _active_color("paper", PAPER)), tuple(_active_color("ink", INK)))
        if icon is None:
            return 0
        left = x - icon.width if align == "right" else x
        canvas.paste(icon, (round(left), round(y - icon.height / 2)), icon)
        return icon.width

    def _stage_icon(self, status):
        """The icon for what the printer is doing: its state when idle, done, paused or failed,
        else the stage it reports."""
        state = self._state_label(status.get("state"))
        named = {"IDLE": "idle", "DONE": "finish", "PAUSED": "paused", "ERROR": "error", "OFFLINE": "error"}
        if state in named:
            return named[state]
        stage = str(self._display_stage(status.get("stage")) or "").lower()
        return next((icon for words, icon in STAGE_ICONS if any(word in stage for word in words)), "stage")

    def _draw_header(self, canvas, draw, status, width):
        margin = LAYOUT_MARGIN
        ink = _active_color("ink", INK)
        title_x = margin
        if self._draw_bambulab_logo(canvas, margin, 14, BAMBU_LAB_LOGO_SIZE):
            title_x = margin + BAMBU_LAB_LOGO_SIZE[0] + BAMBU_LAB_LOGO_GAP
        title_right = title_x + TITLE_WORDMARK_SIZE[0]
        if not self._draw_title_wordmark(canvas, title_x, 13, TITLE_WORDMARK_SIZE):
            title_right = title_x + 234
            title_box = (title_x, 12, title_right, 44)
            draw.rectangle((title_box[0] + 3, title_box[1] + 3, title_box[2] + 3, title_box[3] + 3),
                           fill=_active_color("accent_orange", ACCENT_ORANGE))
            draw.rectangle(title_box, fill=_active_color("accent_gold", ACCENT_GOLD))
            draw.rectangle(title_box, outline=ink, width=2)
            draw.text((title_x + 10, 16), "BAMBU MONITOR", fill=ink, font=self._font(23, True))

        date_text, time_text = self._split_updated_at(status.get("updated_at"))
        time_font = self._font(22, True)
        date_font = self._font(10, True)
        right = width - margin
        draw.text((right, 12), time_text, fill=ink, font=time_font, anchor="ra")
        draw.text((right, 38), date_text, fill=ink, font=date_font, anchor="ra")
        clock_w = max(self._text_width(draw, time_text, time_font), self._text_width(draw, date_text, date_font))
        identity_right = right - clock_w - 16
        identity_w = identity_right - (title_right + 12)
        if identity_w >= 60:
            name_line, endpoint_line = self._machine_info_lines(status)
            self._draw_fit_text_ellipsis(draw, name_line, identity_right, 17, identity_w, 11, True, ink, anchor="ra")
            self._draw_fit_text_ellipsis(draw, endpoint_line, identity_right, 33, identity_w, 10, True, ink, anchor="ra")
        draw.line((margin, HEADER_RULE_Y, right, HEADER_RULE_Y), fill=_active_color("rule", RULE), width=1)

    @classmethod
    def _machine_info_lines(cls, status):
        name = str(status.get("machine_name") or "Bambu Printer").strip()
        model = str(status.get("machine_model") or "MODEL --").strip()
        serial = str(status.get("serial") or "").strip()
        if len(serial) > 4:
            serial_tail = serial[-4:]
        elif len(serial) > 2:
            serial_tail = serial[-2:]
        else:
            serial_tail = "--"
        host = cls._masked_host(status.get("host"))
        return (
            f"{name} / {model}",
            f"SN ...{serial_tail} / HOST {host}",
        )

    @staticmethod
    def _masked_host(value):
        host = str(value or "").strip()
        octets = host.split(".")
        if len(octets) == 4 and all(part.isdigit() and 0 <= int(part) <= 255 for part in octets):
            return f"{octets[0]}.x.x.{octets[-1]}"
        if not host:
            return "--"
        if ":" in host:
            address = host.strip("[]").split("%", 1)[0]
            hex_chars = "".join(
                character
                for character in address
                if character in "0123456789abcdefABCDEF"
            )
            tail = hex_chars[-2:] if len(hex_chars) > 2 else "--"
            return f"IPv6 ...{tail}"
        if len(host) <= 4:
            return host[0] + "..."
        if len(host) <= 6:
            return f"{host[0]}...{host[-1]}"
        return f"{host[:2]}...{host[-2:]}"

    @staticmethod
    def _split_updated_at(value):
        text = str(value or "").strip()
        date_text, _, time_text = text.rpartition(" ")
        return date_text, time_text or "--:--"

    def _draw_camera_hero(self, canvas, draw, box, status):
        left, top, right, bottom = box
        size = (right - left, bottom - top)
        ink = _active_color("ink", INK)
        paper = _active_color("paper", PAPER)
        cinnabar = _active_color("cinnabar", CINNABAR)
        frame = self._load_camera_image(status)
        chip, chip_fill = None, _blend(ink, paper, 0.15)
        prepared = None
        if frame:
            prepared = self._fit_camera_image(frame, size, CAMERA_FRAME_CENTERING)
            if status.get("camera_updated_at"):
                chip = f"CAM {status['camera_updated_at']}"
                if status.get("camera_stale"):
                    chip, chip_fill = f"{chip} STALE", _active_color("accent_orange", ACCENT_ORANGE)
        else:
            waiting = self._load_waiting_image()
            if waiting:
                prepared = self._fit_camera_image(waiting, size)
                chip, chip_fill = "WAITING", _active_color("accent_orange", ACCENT_ORANGE)
        if prepared is not None:
            mask = Image.new("L", size, 0)
            ImageDraw.Draw(mask).rounded_rectangle((0, 0, size[0] - 1, size[1] - 1), radius=CARD_RADIUS, fill=255)
            canvas.paste(prepared, (left, top), mask)
        else:
            panel_blue = _active_color("panel_blue", PANEL_BLUE)
            accent_blue = _active_color("accent_blue", ACCENT_BLUE)
            draw.rounded_rectangle(box, radius=CARD_RADIUS, fill=_blend(accent_blue, panel_blue, 0.08))
            self._draw_halftone(draw, (left + 8, top + 8, right - 8, bottom - 8), accent_blue, panel_blue, 18, 1)
            draw.text(((left + right) // 2, (top + bottom) // 2 - 10), "NO CAMERA FRAME",
                      fill=accent_blue, font=self._font(18, True), anchor="mm")
            error = status.get("camera_error")
            if error:
                self._draw_fit_text_ellipsis(draw, str(error), left + 16, bottom - 30, right - left - 32, 11, False, cinnabar)
        draw.rounded_rectangle((left, top, right - 1, bottom - 1), radius=CARD_RADIUS, outline=ink, width=2)

        state = self._state_label(status.get("state"))
        state_box = self._draw_pill(draw, left + 10, top + 10, state,
                                    self._status_color(status.get("state"), status.get("error")))
        error_text = self._print_error_text(status.get("error"))
        if error_text:
            font = self._font(11, True)
            room = right - 130 - (state_box[2] + 6) - 18
            self._draw_pill(draw, state_box[2] + 6, top + 10, self._ellipsize(draw, f"ERR {error_text}", font, room),
                            cinnabar, size=11, dot=False, height=state_box[3] - state_box[1])
        if chip:
            self._draw_pill(draw, right - 10, top + 10, chip, chip_fill, size=10, dot=False, align="right")
        warning = status.get("warning")
        if warning:
            band = (left + 10, bottom - 36, right - 10, bottom - 10)
            draw.rounded_rectangle(band, radius=8, fill=_active_color("panel", PANEL), outline=cinnabar, width=1)
            self._draw_fit_text_ellipsis(draw, str(warning), band[0] + 10, band[1] + 6, band[2] - band[0] - 20, 12, True, cinnabar)

    def _draw_job_column(self, draw, box, status, canvas=None):
        x0, top, x1, bottom = box
        ink = _active_color("ink", INK)
        rule = _active_color("rule", RULE)
        state_color = self._status_color(status.get("state"), status.get("error"))
        progress = int(max(0, min(100, self._num(status.get("progress"), 0) or 0)))
        layers = self._layer_text(status)
        layer_font = self._font(11, True)
        layers_w = self._text_width(draw, layers, layer_font) if layers else 0
        # "100%" shrinks so it never runs into the layer count beside it.
        percent_w = x1 - x0 + 3 - (layers_w + 8 if layers else 0)
        percent_font = self._fit_font(draw, f"{progress}%", percent_w, 64, True, 44)
        draw.text((x0 - 3, top + 62), f"{progress}%", fill=ink, font=percent_font, anchor="ls")
        if layers:
            draw.text((x1, top + 36), layers, fill=ink, font=layer_font, anchor="ra")
            # The layers icon leads the count where the percentage leaves room, else stands over it;
            # it never makes the percentage smaller.
            percent_right = x0 - 3 + self._text_width(draw, f"{progress}%", percent_font)
            if x1 - layers_w - 4 - LAYER_ICON_SIZE >= percent_right + 6:
                self._paste_icon(canvas, "layers", x1 - layers_w - 4, top + 43, LAYER_ICON_SIZE, align="right")
            else:
                self._paste_icon(canvas, "layers", x1, top + 24, LAYER_ICON_SIZE, align="right")
        self._draw_segmented_bar(draw, (x0, top + 78, x1, top + 92), progress / 100, state_color, PROGRESS_SEGMENTS)

        chip_w = (x1 - x0 - 12) // 2
        self._draw_stat_chip(draw, (x0, top + 104, x0 + chip_w, top + 144), "REMAINING",
                             self._remaining_text(status.get("remaining_minutes")), canvas=canvas, icon="remaining")
        self._draw_stat_chip(draw, (x1 - chip_w, top + 104, x1, top + 144), "FINISH", status.get("finish_at") or "--",
                             canvas=canvas, icon="finish")

        # An idle or finished printer still reports the job it last ran.
        job_label = "LAST JOB" if self._state_label(status.get("state")) in ("IDLE", "DONE") else "JOB"
        self._draw_section_label(draw, x0, top + 156, job_label, _active_color("accent_gold", ACCENT_GOLD),
                                 canvas=canvas, icon="job")
        self._draw_wrapped_text(draw, str(status.get("file") or "No active job"), x0, top + 174, x1 - x0, 3, 15)
        draw.line((x0, top + 238, x1, top + 238), fill=rule, width=1)

        half = (x1 - x0 - 20) // 2
        self._draw_thermo(draw, x0, top + 248, half, "NOZZLE", status.get("nozzle"), status.get("nozzle_target"),
                          NOZZLE_SCALE_C, _active_color("cinnabar", CINNABAR), canvas=canvas, icon="nozzle")
        self._draw_thermo(draw, x1 - half, top + 248, half, "BED", status.get("bed"), status.get("bed_target"),
                          BED_SCALE_C, _active_color("accent_orange", ACCENT_ORANGE), canvas=canvas, icon="bed")
        self._draw_stat_chip(draw, (x0, top + 312, x0 + chip_w, top + 352), "SPEED", self._speed_text(status.get("speed")),
                             canvas=canvas, icon="speed")
        self._draw_stat_chip(draw, (x1 - chip_w, top + 312, x1, top + 352), "PART FAN", self._fan_text(status.get("fan")),
                             canvas=canvas, icon="fan")

        self._draw_section_label(draw, x0, top + 366, "STAGE", _active_color("accent_blue", ACCENT_BLUE),
                                 canvas=canvas, icon=self._stage_icon(status))
        source = str(status.get("source") or "").upper()
        if source:
            self._draw_pill(draw, x1, top + 362, source, self._source_color(source), size=10, dot=False, align="right")
        stage = self._display_stage(status.get("stage")) or self._state_label(status.get("state")).title()
        self._draw_fit_text_ellipsis(draw, stage, x0, top + 384, x1 - x0, 13, True, ink)

    def _draw_ams_strip(self, draw, box, status, canvas=None):
        left, top, right, bottom = box
        ink = _active_color("ink", INK)
        self._draw_section_label(draw, left, top, "AMS", _active_color("malachite", MALACHITE), canvas=canvas, icon="ams")

        tiles_top = top + 18
        trays = self._visible_trays(status.get("ams") or [])
        if not trays:
            draw.rounded_rectangle((left, tiles_top, right, bottom), radius=CARD_RADIUS,
                                   fill=_active_color("panel", PANEL), outline=_active_color("rule", RULE))
            draw.text(((left + right) // 2, (tiles_top + bottom) // 2), "AMS not reported", fill=ink,
                      font=self._font(14, True), anchor="mm")
            return
        slot_w = (right - left - (AMS_SLOTS - 1) * AMS_SLOT_GAP) // AMS_SLOTS
        for index, tray in enumerate(trays):
            x = left + index * (slot_w + AMS_SLOT_GAP)
            self._draw_ams_tile(draw, (x, tiles_top, x + slot_w, bottom), tray)

    @staticmethod
    def _visible_trays(trays):
        """At most one AMS unit's worth of trays, the one holding the tray in use."""
        trays = [tray for tray in trays if isinstance(tray, dict)]
        active = next((index for index, tray in enumerate(trays) if tray.get("active")), 0)
        start = (active // AMS_SLOTS) * AMS_SLOTS
        return trays[start:start + AMS_SLOTS]

    @staticmethod
    def _ams_tile_layout(box):
        """Spool, text column and in-use pill positions inside one AMS tile."""
        left, top, right, bottom = box
        radius = AMS_SPOOL_RADIUS
        centre = (left + 32, top + 32)
        pill_top = bottom - 8 - AMS_USE_PILL_HEIGHT
        return {
            "spool": (centre[0], centre[1], radius),
            "ring_bottom": centre[1] + radius + 4,
            "pill_top": pill_top,
            "text_x": left + 64,
            "text_w": right - 8 - (left + 64),
        }

    def _draw_ams_tile(self, draw, box, tray):
        left, top, right, bottom = box
        ink = _active_color("ink", INK)
        malachite = _active_color("malachite", MALACHITE)
        active = bool(tray.get("active"))
        draw.rounded_rectangle(box, radius=CARD_RADIUS, fill=_active_color("panel", PANEL),
                               outline=malachite if active else _active_color("rule", RULE), width=2 if active else 1)
        layout = self._ams_tile_layout(box)
        cx, cy, radius = layout["spool"]
        # A slot without filament reports neither a material nor a colour.
        empty = not str(tray.get("color") or "").strip() and str(tray.get("type") or "FIL").upper() in ("", "FIL")
        self._draw_spool(draw, cx, cy, radius, tray.get("color"), active, empty=empty)
        text_x, text_w = layout["text_x"], layout["text_w"]
        self._draw_fit_text_ellipsis(draw, self._tray_slot_label(tray.get("id")), text_x, top + 10, text_w, 15, True, ink)
        detail = "EMPTY" if empty else str(tray.get("type") or "FIL").upper()
        if self._is_nonnegative(tray.get("remain")):
            detail = f"{detail} {float(tray['remain']):.0f}%"
        self._draw_fit_text_ellipsis(draw, detail, text_x, top + 31, text_w, 13, True, ink)
        if not empty:
            self._draw_fit_text_ellipsis(draw, self._filament_color_label(tray.get("color")), text_x, top + 50,
                                         text_w, 10, True, ink)
        if active:
            self._draw_pill(draw, cx, layout["pill_top"], "USE", malachite, size=10, dot=False, align="center",
                            pad=AMS_USE_PILL_PAD, height=AMS_USE_PILL_HEIGHT)

    def _draw_spool(self, draw, cx, cy, radius, color, active=False, empty=False):
        swatch = self._filament_swatch_color(color)
        ink = _active_color("ink", INK)
        panel = _active_color("panel", PANEL)
        if empty:
            draw.ellipse((cx - radius, cy - radius, cx + radius, cy + radius), fill=panel,
                         outline=_active_color("rule", RULE), width=2)
        elif not swatch:
            draw.ellipse((cx - radius, cy - radius, cx + radius, cy + radius), fill=panel, outline=ink, width=1)
            draw.line((cx - radius * 0.7, cy + radius * 0.7, cx + radius * 0.7, cy - radius * 0.7),
                      fill=_active_color("cinnabar", CINNABAR), width=2)
        else:
            edge = self._filament_swatch_outline(color, swatch)
            draw.ellipse((cx - radius, cy - radius, cx + radius, cy + radius), fill=swatch, outline=edge, width=1)
            for ratio in (0.78, 0.62):
                ring = round(radius * ratio)
                draw.ellipse((cx - ring, cy - ring, cx + ring, cy + ring), outline=_blend(edge, swatch, 0.45), width=1)
        hub = round(radius * 0.42)
        draw.ellipse((cx - hub, cy - hub, cx + hub, cy + hub), fill=panel, outline=ink, width=2)
        hole = max(2, round(radius * 0.12))
        draw.ellipse((cx - hole, cy - hole, cx + hole, cy + hole), fill=ink)
        if active:
            ring = radius + 4
            draw.ellipse((cx - ring, cy - ring, cx + ring, cy + ring), outline=_active_color("malachite", MALACHITE), width=3)

    @staticmethod
    def _print_error_text(error):
        """A printer error code as Bambu Studio writes it (0500-400B); small internal codes stay unnamed."""
        text = str(error if error is not None else "").strip()
        if text.isdigit():
            code = int(text)
            return f"{code >> 16:04X}-{code & 0xFFFF:04X}" if code > 0xFFFF else ""
        return text

    @staticmethod
    def _tray_slot_label(tray_id):
        """Bambu Studio's slot name: AMS unit letter and 1-based tray, e.g. 0-1 -> A2."""
        unit, _, tray = str(tray_id or "").partition("-")
        if unit.isdigit() and tray.isdigit() and int(unit) < 26:
            return f"{chr(65 + int(unit))}{int(tray) + 1}"
        return str(tray_id or "?")

    def _draw_pill(self, draw, x, y, text, fill, size=13, dot=True, align="left", pad=9, height=None):
        """A rounded label; ``x`` is its left edge, right edge or centre per ``align``."""
        font = self._font(size, True)
        height = height or size + 11
        width = self._text_width(draw, text, font) + 2 * pad + (12 if dot else 0)
        left = {"right": x - width, "center": x - width / 2}.get(align, x)
        box = (round(left), y, round(left + width), y + height)
        draw.rounded_rectangle(box, radius=height // 2, fill=fill)
        ink = self._contrast_text(fill)
        text_x = box[0] + pad
        if dot:
            middle = y + height // 2
            draw.ellipse((text_x, middle - 3, text_x + 6, middle + 3), fill=ink)
            text_x += 12
        draw.text((text_x, y + height / 2), text, fill=ink, font=font, anchor="lm")
        return box

    def _draw_segmented_bar(self, draw, box, fraction, color, segments):
        """A game-style gauge: ``segments`` slanted blocks with gaps between them, those done in
        ``color``, the one under way filled as far as it has got, the rest in the track's tone."""
        left, top, right, bottom = box
        track = _blend(_active_color("rule", RULE), _active_color("panel", PANEL), 0.55)
        skew = (bottom - top) * PROGRESS_SEGMENT_SLANT
        pitch = (right - left - skew + PROGRESS_SEGMENT_GAP) / segments
        width = pitch - PROGRESS_SEGMENT_GAP
        done = max(0.0, min(1.0, fraction)) * segments

        def block(x0, x1):
            return [(x0 + skew, top), (x1 + skew, top), (x1, bottom), (x0, bottom)]

        for index in range(segments):
            x0 = left + index * pitch
            draw.polygon(block(x0, x0 + width), fill=track)
            share = max(0.0, min(1.0, done - index))
            if share >= 1:
                draw.polygon(block(x0, x0 + width), fill=color)
            elif share > 0:
                draw.polygon(block(x0, x0 + max(2.0, width * share)), fill=color)

    def _draw_progress_bar(self, draw, box, fraction, color, ticks=0):
        left, top, right, bottom = box
        radius = (bottom - top) // 2
        panel = _active_color("panel", PANEL)
        draw.rounded_rectangle(box, radius=radius, fill=_blend(_active_color("rule", RULE), panel, 0.55))
        fill_right = left + round((right - left) * max(0.0, min(1.0, fraction)))
        if fill_right - left >= 2 * radius:
            draw.rounded_rectangle((left, top, fill_right, bottom), radius=radius, fill=color)
        for index in range(1, ticks):
            x = left + round((right - left) * index / ticks)
            draw.line((x, top + 2, x, bottom - 2), fill=_active_color("paper", PAPER), width=1)

    def _draw_stat_chip(self, draw, box, label, value, canvas=None, icon=None):
        """A label over its value in a soft chip, the reading's icon at its right."""
        left, top, right, bottom = box
        ink = _active_color("ink", INK)
        fill = _blend(_active_color("rule", RULE), _active_color("panel", PANEL), 0.18)
        draw.rounded_rectangle(box, radius=8, fill=fill, outline=_active_color("rule", RULE), width=1)
        icon_w = self._paste_icon(canvas, icon, right - 7, (top + bottom) / 2, STAT_ICON_SIZE, ground=fill,
                                  align="right") if icon else 0
        draw.text((left + 10, top + 6), label, fill=ink, font=self._font(10, True))
        self._draw_fit_text_ellipsis(draw, str(value), left + 10, top + 19, right - left - 20 - (icon_w + 4 if icon_w else 0),
                                     15, True, ink)

    def _draw_section_label(self, draw, x, y, text, color, canvas=None, icon=None):
        """A section's name led by its icon, or by a short bar in its colour without one."""
        icon_w = self._paste_icon(canvas, icon, x - 1, y + 7, 17) if icon else 0
        if not icon_w:
            draw.rounded_rectangle((x, y + 2, x + 4, y + 12), radius=2, fill=color)
        draw.text((x + (icon_w + 4 if icon_w else 9), y), text, fill=_active_color("ink", INK), font=self._font(11, True))

    def _draw_thermo(self, draw, x, y, width, label, current, target, scale, color, canvas=None, icon=None):
        ink = _active_color("ink", INK)
        if icon:
            # The heater's icon stands at the right of its label row, clear of the reading below.
            self._paste_icon(canvas, icon, x + width, y + 6, 22, align="right")
        draw.text((x, y), label, fill=ink, font=self._font(10, True))
        current = self._num(current)
        target = self._num(target)
        value = "--" if current is None else f"{current:.0f}°"
        value_font = self._font(22, True)
        draw.text((x, y + 14), value, fill=ink, font=value_font)
        if target and current is not None:
            draw.text((x + self._text_width(draw, value, value_font) + 4, y + 22), f"/ {target:.0f}°",
                      fill=ink, font=self._font(12, True))
        bar_top = y + 44
        self._draw_progress_bar(draw, (x, bar_top, x + width, bar_top + 6), (current or 0) / scale, color)
        if target:
            tick = x + round(width * min(1.0, target / scale))
            draw.line((tick, bar_top - 3, tick, bar_top + 9), fill=ink, width=2)

    def _draw_wrapped_text(self, draw, text, x, y, max_width, max_lines, size, bold=True):
        """Greedy word wrap at one size; the last line ends in an ellipsis if text remains."""
        font = self._font(size, bold)
        words = str(text).replace("_", " ").split()
        lines = []
        while words and len(lines) < max_lines:
            line = words.pop(0)
            while words and self._text_width(draw, f"{line} {words[0]}", font) <= max_width:
                line = f"{line} {words.pop(0)}"
            lines.append(line)
        if words:
            lines[-1] = f"{lines[-1]} {' '.join(words)}"
        ink = _active_color("ink", INK)
        for index, line in enumerate(lines):
            draw.text((x, y + index * (size + 4)), self._ellipsize(draw, line, font, max_width), fill=ink, font=font)

    def _ellipsize(self, draw, text, font, max_width):
        if self._text_width(draw, text, font) <= max_width:
            return text
        while text and self._text_width(draw, text.rstrip() + "…", font) > max_width:
            text = text[:-1]
        return text.rstrip() + "…"

    def _render_setup_required(self, dimensions, host, serial, has_code, settings=None):
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
        self._apply_machine_identity(
            status,
            settings,
            host=host,
            serial=serial,
        )
        return self._render_status(status, dimensions)

    def _render_error(self, dimensions, message, host="", serial="", settings=None):
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
        self._apply_machine_identity(
            status,
            settings,
            host=host,
            serial=serial,
        )
        return self._non_cacheable_image(self._render_status(status, dimensions))

    @staticmethod
    def _non_cacheable_image(image):
        image.info[SKIP_CACHE_IMAGE_INFO_KEY] = True
        return image

    @staticmethod
    def _cache_entry_is_fresh(cached, cache_seconds, now):
        if not isinstance(cached, dict) or cache_seconds <= 0:
            return False
        try:
            age_seconds = float(now) - float(cached.get("fetched_at"))
        except (TypeError, ValueError):
            return False
        return 0 <= age_seconds < cache_seconds

    @staticmethod
    def _is_expected_connection_failure(exc):
        return isinstance(exc, (OSError, socket.timeout, TimeoutError, MqttProtocolError, ssl.SSLError))

    def _cache_file(self, host, serial, *, create=True):
        safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in f"{host}_{serial}")
        return str(self.cache_dir(leaf="cache", create=create) / f"{safe}.json")

    def _camera_file(self, host, serial):
        safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in f"{host}_{serial}")
        return str(self.cache_dir(leaf="cache") / f"{safe}_camera.jpg")

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

    @staticmethod
    def _trim_transparent(image):
        if image.mode != "RGBA":
            image = image.convert("RGBA")
        bbox = image.getchannel("A").getbbox()
        return image.crop(bbox) if bbox else image

    @staticmethod
    def _tint_neutral_asset_ink(image):
        target = _active_color("ink", INK)
        if target == INK:
            return image
        tinted = image.convert("RGBA").copy()
        pixels = tinted.load()
        for y in range(tinted.height):
            for x in range(tinted.width):
                red, green, blue, alpha = pixels[x, y]
                if alpha and max(red, green, blue) <= 80 and max(red, green, blue) - min(red, green, blue) <= 12:
                    pixels[x, y] = (*target, alpha)
        return tinted

    def _draw_bambulab_logo(self, canvas, x, y, size):
        source = self._load_bambulab_logo()
        if source is None:
            return False

        try:
            target_w, target_h = [int(value) for value in size]
            source = self._tint_neutral_asset_ink(self._trim_transparent(source))
            resample = Image.Resampling.LANCZOS if hasattr(Image, "Resampling") else Image.LANCZOS
            art = ImageOps.contain(source.copy(), (target_w, target_h), method=resample)
            layer = Image.new("RGBA", (target_w, target_h), (0, 0, 0, 0))
            layer.alpha_composite(art, ((target_w - art.width) // 2, (target_h - art.height) // 2))
            canvas.paste(layer.convert("RGB"), (int(x), int(y)), layer.getchannel("A"))
            return True
        except Exception as exc:
            logger.warning(f"Bambu Lab logo unavailable: {exc}")
            return False

    def _bambulab_logo_file(self):
        return os.path.join(os.path.dirname(__file__), BAMBU_LAB_LOGO_IMAGE)

    def _load_bambulab_logo(self):
        path = self._bambulab_logo_file()
        if not os.path.exists(path):
            return None
        try:
            return Image.open(path).convert("RGBA")
        except Exception as exc:
            logger.warning(f"Failed to load Bambu Lab logo: {exc}")
            return None

    def _draw_title_wordmark(self, canvas, x, y, size):
        source = self._load_title_wordmark()
        if source is None:
            return False

        try:
            target_w, target_h = [int(value) for value in size]
            source = self._tint_neutral_asset_ink(source)
            resample = Image.Resampling.LANCZOS if hasattr(Image, "Resampling") else Image.LANCZOS
            art = ImageOps.contain(source.copy(), (target_w, target_h), method=resample)
            layer = Image.new("RGBA", (target_w, target_h), (0, 0, 0, 0))
            layer.alpha_composite(art, ((target_w - art.width) // 2, (target_h - art.height) // 2))
            canvas.paste(layer.convert("RGB"), (int(x), int(y)), layer.getchannel("A"))
            return True
        except Exception as exc:
            logger.warning(f"Bambu title wordmark unavailable: {exc}")
            return False

    def _title_wordmark_file(self):
        return os.path.join(os.path.dirname(__file__), TITLE_WORDMARK_IMAGE)

    def _load_title_wordmark(self):
        path = self._title_wordmark_file()
        if not os.path.exists(path):
            return None
        try:
            return Image.open(path).convert("RGBA")
        except Exception as exc:
            logger.warning(f"Failed to load Bambu title wordmark: {exc}")
            return None

    def _draw_halftone(self, draw, bounds, color, paper, spacing, radius):
        left, top, right, bottom = [int(value) for value in bounds]
        dot = _blend(color, paper, 0.22)
        for y in range(top, bottom, max(4, int(spacing))):
            offset = (spacing // 2) if ((y // max(1, spacing)) % 2) else 0
            for x in range(left + offset, right, max(4, int(spacing))):
                draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill=dot)

    def _draw_fit_text_ellipsis(
        self,
        draw,
        text,
        x,
        y,
        max_width,
        start_size,
        bold=False,
        fill=INK,
        anchor="la",
    ):
        rendered = str(text)
        font = self._fit_font(draw, rendered, max_width, start_size, bold, 9)

        def rendered_width(value):
            bounds = draw.textbbox((0, 0), value, font=font)
            return max(0, bounds[2] - bounds[0])

        if rendered_width(rendered) > max_width:
            ellipsis = "…"
            if rendered_width(ellipsis) > max_width:
                rendered = ""
            else:
                low = 0
                high = len(rendered)
                while low < high:
                    midpoint = (low + high + 1) // 2
                    candidate = rendered[:midpoint].rstrip() + ellipsis
                    if rendered_width(candidate) <= max_width:
                        low = midpoint
                    else:
                        high = midpoint - 1
                rendered = rendered[:low].rstrip() + ellipsis
                while rendered and rendered_width(rendered) > max_width:
                    rendered = rendered[:-2].rstrip() + ellipsis

        draw.text((x, y), rendered, fill=fill, font=font, anchor=anchor)
        return rendered, font

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
        panel_blue = _active_color("panel_blue", PANEL_BLUE)
        panel = _active_color("panel", PANEL)
        ink = _active_color("ink", INK)
        accent_blue = _active_color("accent_blue", ACCENT_BLUE)
        accent_gold = _active_color("accent_gold", ACCENT_GOLD)
        accent_orange = _active_color("accent_orange", ACCENT_ORANGE)
        malachite = _active_color("malachite", MALACHITE)
        cinnabar = _active_color("cinnabar", CINNABAR)
        img = Image.new("RGB", (640, 360), panel_blue)
        draw = ImageDraw.Draw(img)
        self._draw_halftone(draw, (420, 22, 608, 132), accent_blue, panel_blue, 16, 2)
        draw.rectangle((0, 286, 640, 360), fill=_blend(accent_blue, panel_blue, 0.12))
        draw.rectangle((40, 90, 600, 280), fill=panel, outline=ink, width=8)
        draw.rectangle((170, 130, 470, 245), fill=_active_color("panel_gold", panel), outline=ink, width=5)
        draw.rectangle((232, 100, 408, 122), fill=malachite, outline=ink, width=3)
        draw.line((80, 300, 560, 300), fill=ink, width=10)
        draw.rectangle((72, 292, 168, 308), fill=accent_orange, outline=ink, width=3)
        draw.rectangle((472, 292, 568, 308), fill=accent_blue, outline=ink, width=3)
        draw.ellipse((285, 160, 355, 230), fill=accent_gold, outline=ink, width=4)
        draw.rectangle((292, 214, 348, 242), fill=cinnabar, outline=ink, width=3)
        return img

    def _font(self, size, bold=False):
        return get_base_ui_font(int(size), bold=bool(bold))

    @staticmethod
    def _text_width(draw, text, font):
        return text_width(draw, str(text), font)

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
            return _active_color("cinnabar", CINNABAR)
        normalized = cls._state_label(state)
        if normalized in ("PRINTING", "RUNNING", "DONE"):
            return _active_color("malachite", MALACHITE)
        if normalized in ("PAUSED", "CONFIG"):
            return _active_color("accent_orange", ACCENT_ORANGE)
        if normalized in ("ERROR", "FAILED", "OFFLINE"):
            return _active_color("cinnabar", CINNABAR)
        return _active_color("accent_blue", ACCENT_BLUE)

    @staticmethod
    def _source_color(source):
        normalized = str(source or "").strip().lower()
        if normalized in ("error", "offline"):
            return _active_color("cinnabar", CINNABAR)
        if normalized in ("setup", "stale", "cache"):
            return _active_color("accent_orange", ACCENT_ORANGE)
        if normalized == "demo":
            return _active_color("accent_gold", ACCENT_GOLD)
        return _active_color("accent_blue", ACCENT_BLUE)

    @staticmethod
    def _luma(rgb):
        r, g, b = rgb
        return 0.2126 * r + 0.7152 * g + 0.0722 * b

    @classmethod
    def _contrast_text(cls, fill):
        candidates = (
            _active_color("ink", INK),
            _active_color("paper", PAPER),
        )
        return max(
            candidates,
            key=lambda candidate: cls._contrast_ratio(fill, candidate),
        )

    _contrast_ratio = staticmethod(contrast_ratio)

    _relative_luminance = staticmethod(relative_luminance)

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
            return _active_color("filament_black", FILAMENT_BLACK)
        if min_channel > 215 and spread < 35:
            return _active_color("filament_white", FILAMENT_WHITE)
        if spread < 30:
            return _active_color("filament_gray", FILAMENT_GRAY)

        hue, saturation, value = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
        degrees = hue * 360
        if saturation < 0.18:
            if value > 0.75:
                return _active_color("filament_white", FILAMENT_WHITE)
            if value < 0.25:
                return _active_color("filament_black", FILAMENT_BLACK)
            return _active_color("filament_gray", FILAMENT_GRAY)
        if degrees < 15 or degrees >= 345:
            return _active_color("cinnabar", CINNABAR)
        if degrees < 45:
            return _active_color("accent_orange", ACCENT_ORANGE)
        if degrees < 75:
            return _active_color("accent_gold", ACCENT_GOLD)
        if degrees < 165:
            return _active_color("malachite", MALACHITE)
        if degrees < 195:
            return _active_color("accent_cyan", ACCENT_CYAN)
        if degrees < 255:
            return _active_color("accent_blue", ACCENT_BLUE)
        if degrees < 295:
            return _active_color("accent_purple", ACCENT_PURPLE)
        if degrees < 345:
            return _active_color("cinnabar", CINNABAR)
        return _active_color("brown", BROWN)

    @classmethod
    def _filament_swatch_outline(cls, color, swatch):
        rgb = cls._parse_filament_color(color)
        if rgb:
            max_channel = max(rgb)
            min_channel = min(rgb)
            spread = max_channel - min_channel
            if max_channel < 45 or (spread < 30 and max_channel < 64):
                return _active_color("filament_light_border", FILAMENT_LIGHT_BORDER)
            if min_channel > 215 and spread < 35:
                return _active_color("filament_dark_border", FILAMENT_DARK_BORDER)
        return (
            _active_color("filament_dark_border", FILAMENT_DARK_BORDER)
            if cls._luma(swatch) > 150
            else _active_color("filament_light_border", FILAMENT_LIGHT_BORDER)
        )

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
    def _display_stage(stage):
        """Readable stage text; a bare number (old mc_print_stage caches) is hidden."""
        text = str(stage or "").strip()
        return "" if text.lstrip("-").isdigit() else text

    @staticmethod
    def _remaining_text(value):
        try:
            minutes = int(float(value))
        except (TypeError, ValueError):
            return "--"
        if minutes < 0:
            return "--"
        hours, mins = divmod(minutes, 60)
        return f"{hours}h {mins:02d}m" if hours else f"{mins}m"

    @staticmethod
    def _layer_text(status):
        layer = BambuMonitor._positive_int(status.get("layer"))
        total = BambuMonitor._positive_int(status.get("total_layers"))
        return f"LAYER {layer}/{total}" if layer and total else ""

    @staticmethod
    def _fan_text(value):
        try:
            return f"{float(value):.0f}%"
        except (TypeError, ValueError):
            return "--"

    @staticmethod
    def _speed_text(value):
        if value in (None, ""):
            return "--"
        return SPEED_LEVEL_NAMES.get(str(value), f"LV {value}")
