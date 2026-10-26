from copy import deepcopy
from datetime import datetime
from io import BytesIO
import hashlib
import json
import os
import re
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import pytest
from PIL import Image, ImageChops, ImageDraw, ImageFont


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from plugins.base_plugin.render_provenance import (  # noqa: E402
    SourceProvenance,
    read_source_provenance,
)
from plugins.vehicle_status import vehicle_status as vehicle_module  # noqa: E402
from plugins.vehicle_status.vehicle_status import (  # noqa: E402
    BRIDGE_SUMMARY_URL,
    VehicleStatus,
)
from security.ssrf import UnsafeTarget  # noqa: E402


class DeviceConfig:
    def __init__(self, token="bridge-token", env=None):
        self.env = {"EPAPER_VEHICLE_BRIDGE_TOKEN": token}
        self.env.update(env or {})
        self.loaded = []

    def get_resolution(self):
        return (800, 480)

    def get_config(self, key=None, default=None):
        return default

    def load_env_key(self, key):
        self.loaded.append(key)
        return self.env.get(key)


class FakeHttpClient:
    def __init__(self, payload=None, error=None):
        self.payload = payload
        self.error = error
        self.calls = []

    def request_json(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        if self.error:
            raise self.error
        return SimpleNamespace(status=200, data=self.payload, headers={}, url=url)


class LocationHttpClient:
    def __init__(self, bridge_payload, map_bytes, *, geocode_error=None, map_error=None):
        self.bridge_payload = bridge_payload
        self.map_bytes = map_bytes
        self.geocode_error = geocode_error
        self.map_error = map_error
        self.calls = []

    def request_json(self, method, url, **kwargs):
        self.calls.append(("json", method, url, kwargs))
        parsed = urlparse(url)
        if parsed.netloc == "epaper-vehicle-bridge.superxfy.workers.dev":
            return SimpleNamespace(status=200, data=self.bridge_payload, headers={}, url=url)
        if parsed.path == "/maps/api/geocode/json":
            if self.geocode_error:
                raise self.geocode_error
            return SimpleNamespace(
                status=200,
                data={
                    "status": "OK",
                    "results": [{"formatted_address": ("1600 Amphitheatre Parkway, Mountain View, CA 94043, USA")}],
                },
                headers={},
                url=url,
            )
        raise AssertionError(f"unexpected JSON host/path: {parsed.netloc}{parsed.path}")

    def request_bytes(self, method, url, **kwargs):
        self.calls.append(("bytes", method, url, kwargs))
        parsed = urlparse(url)
        assert parsed.netloc == "maps.googleapis.com"
        assert parsed.path == "/maps/api/staticmap"
        if self.map_error:
            raise self.map_error
        return SimpleNamespace(status=200, data=self.map_bytes, headers={}, url=url)


def _map_png(color=(255, 0, 255)):
    image = Image.new("RGB", vehicle_module.LOCATION_MAP_SIZE, color)
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


class AsciiOnlyFont:
    """Font boundary that exposes portable status-copy regressions."""

    def __init__(self, font):
        self._font = font

    @staticmethod
    def _require_ascii(text):
        if not str(text).isascii():
            value = str(text)
            index = next(i for i, character in enumerate(value) if not character.isascii())
            raise UnicodeEncodeError(
                "ascii",
                value,
                index,
                index + 1,
                "status copy must render with the portable footer font",
            )

    def getbbox(self, text, *args, **kwargs):
        self._require_ascii(text)
        return self._font.getbbox(text, *args, **kwargs)

    def getlength(self, text, *args, **kwargs):
        self._require_ascii(text)
        return self._font.getlength(text, *args, **kwargs)

    def getmask(self, text, *args, **kwargs):
        self._require_ascii(text)
        return self._font.getmask(text, *args, **kwargs)

    def getmask2(self, text, *args, **kwargs):
        self._require_ascii(text)
        return self._font.getmask2(text, *args, **kwargs)

    def __getattr__(self, name):
        return getattr(self._font, name)


def _require_portable_ascii_status_font(monkeypatch):
    real_font = vehicle_module._font

    def portable_status_font(size, bold=False):
        return AsciiOnlyFont(real_font(size, bold))

    monkeypatch.setattr(vehicle_module, "_font", portable_status_font)


def _summary(*, freshness="live", connectivity="online"):
    return {
        "schema_version": 1,
        "served_at": "2026-08-05T20:00:00.000Z",
        "snapshot": {
            "captured_at": "2026-08-05T19:59:55.000Z",
            "freshness": freshness,
            "age_seconds": 5,
            "vehicle_connectivity": connectivity,
        },
        "vehicle": {
            "key": "primary",
            "display_name": "Gray Bullet",
            "model": "Model Y",
            "trim": "Performance",
            "locked": True,
            "software_version": "2026.20.100",
            "odometer": {"value": 12345.6, "unit": "mi"},
        },
        "battery": {
            "level_percent": 78,
            "estimated_range": {"value": 215.4, "unit": "mi"},
            "charging_state": "Disconnected",
            "charge_limit_percent": 80,
            "time_to_full_minutes": None,
            "power_kw": 0,
        },
        "climate": {
            "inside_temp_c": 22.5,
            "outside_temp_c": 18.2,
            "is_climate_on": False,
        },
        "closures": {
            "all_closed": True,
            "open": [],
            "charge_port_open": False,
        },
    }


def _summary_v2(*, freshness="live", connectivity="online"):
    return {
        "schema_version": 2,
        "served_at": "2026-08-08T20:00:00.000Z",
        "snapshot": {
            "captured_at": "2026-08-08T19:59:55.000Z",
            "freshness": freshness,
            "age_seconds": 5,
            "vehicle_connectivity": connectivity,
        },
        "vehicle": {
            "key": "primary",
            "display_name": "Gray Bullet",
            "model": "Model Y",
            "trim": "Performance",
            "locked": True,
            "software_version": "2026.20.100",
            "odometer": {"value": 12345.6, "unit": "mi"},
            "exterior_color": "MidnightSilver",
            "wheel_type": "UberTurbine20",
            "roof_color": "Colored",
            "charge_port_type": "US",
            "efficiency_package": "MY2021",
            "rear_seat_heaters": "installed",
            "right_hand_drive": False,
            "europe_vehicle": False,
            "sunroof_installed": "not_installed",
            "sentry_mode": "on",
            "service_mode": False,
            "valet_mode": False,
            "center_display_state": "on",
            "speed_limit_mode": {
                "active": True,
                "limit": {"value": 75, "unit": "mi/h"},
            },
        },
        "battery": {
            "level_percent": 78,
            "usable_level_percent": 76,
            "rated_range": {"value": 218.25, "unit": "mi"},
            "estimated_range": {"value": 201.5, "unit": "mi"},
        },
        "charging": {
            "state": "Charging",
            "charge_limit_percent": 80,
            "time_to_full_minutes": 95,
            "power_kw": 11.5,
            "energy_added_kwh": 5.4,
            "rate": {"value": 44, "unit": "mi/h"},
            "actual_current_a": 32,
            "voltage_v": 240,
            "phases": 1,
            "requested_current_a": 32,
            "max_current_a": 48,
            "enabled": True,
            "cable_type": "iec",
            "fast_charger_present": False,
            "fast_charger_type": "sna",
            "port_latch": "engaged",
            "port_cold_weather_mode": False,
            "preconditioning": True,
            "not_enough_power_to_heat": False,
            "supercharger_trip_planner": False,
            "scheduled": {"pending": True, "mode": "start_at"},
        },
        "climate": {
            "inside_temp_c": 25,
            "outside_temp_c": 20,
            "is_climate_on": True,
            "driver_target_temp_c": 21.5,
            "passenger_target_temp_c": 22,
            "keeper_mode": "dog",
            "defrost_mode": "off",
            "rear_defroster_on": False,
            "battery_heater_on": True,
            "wiper_heater_on": False,
            "hvac_auto_mode": "on",
            "fan_status": 3,
            "steering_wheel_heat_level": 2,
            "steering_wheel_heat_auto": True,
            "seat_heaters": {
                "front_left": 1,
                "front_right": 2,
                "rear_left": 0,
                "rear_right": 0,
                "rear_center": 0,
            },
            "seat_cooling": {"front_left": 1, "front_right": 0},
            "auto_seat_climate": {"front_left": True, "front_right": False},
            "cabin_overheat": {"mode": "on", "temp_limit": "high"},
        },
        "closures": {
            "all_closed": False,
            "open": ["rear_trunk"],
            "charge_port_open": False,
            "doors": {
                "driver_front": False,
                "driver_rear": False,
                "passenger_front": False,
                "passenger_rear": False,
                "front_trunk": False,
                "rear_trunk": True,
            },
            "windows": {
                "driver_front": False,
                "driver_rear": False,
                "passenger_front": False,
                "passenger_rear": False,
            },
        },
        "tires": {
            "pressures": {
                "front_left": {"value": 2.8, "unit": "bar"},
                "front_right": {"value": 2.9, "unit": "bar"},
                "rear_left": {"value": 3.0, "unit": "bar"},
                "rear_right": {"value": 3.1, "unit": "bar"},
            },
            "soft_warnings": {
                "front_left": False,
                "front_right": False,
                "rear_left": True,
                "rear_right": False,
            },
            "hard_warnings": {
                "front_left": False,
                "front_right": False,
                "rear_left": False,
                "rear_right": True,
            },
        },
        "software_update": {
            "version": "2026.26.3",
            "download_percent": 54.5,
            "install_percent": 0,
            "expected_duration_minutes": 25,
        },
        "preferences": {
            "distance_unit": "mi",
            "temperature_unit": "F",
            "pressure_unit": "psi",
            "charge_display_unit": "distance",
            "use_24_hour_time": True,
        },
    }


# The page's regions: the verdict in the header, the status lamps under it, three columns, the
# vehicle strip and the footer.
VERDICT_REGION = (520, 20, 765, 58)
LAMP_REGION = (36, 66, 765, 124)
ENERGY_REGION = (36, 126, 320, 396)
BODY_COLUMN_REGION = (338, 126, 518, 392)
CLIMATE_REGION = (538, 126, 765, 204)
RIGHT_COLUMN_REGION = (538, 126, 765, 396)
STRIP_REGION = (36, 399, 765, 436)
FOOTER_REGION = (36, 436, 330, 460)
BODY_REGION = (36, 66, 765, 396)
LAMP_KEYS = ("link", "battery", "charging", "lock", "openings", "tyres", "modes", "climate", "update")


def _lamp_box(key):
    """One lamp's slot in the row: its tile and the short state under it."""
    width = (vehicle_module.PAGE_RIGHT - vehicle_module.PAGE_LEFT) / len(LAMP_KEYS)
    index = LAMP_KEYS.index(key)
    return (int(vehicle_module.PAGE_LEFT + width * index), 66, int(vehicle_module.PAGE_LEFT + width * (index + 1)) + 1,
            124)


def _assert_changes_only_within(before, after, *boxes):
    """Something changed, and every changed pixel lies in one of ``boxes``."""
    changed = ImageChops.difference(before, after).convert("L").point(lambda value: 255 if value else 0)
    assert changed.getbbox() is not None
    for box in boxes:
        changed.paste(0, box)
    assert changed.getbbox() is None

def _summary_v3(*, freshness="live", connectivity="online", location=True):
    payload = _summary_v2(freshness=freshness, connectivity=connectivity)
    payload["schema_version"] = 3
    payload["location"] = (
        {
            "captured_at": "2026-08-08T19:59:50.000Z",
            "age_seconds": 10,
            "latitude": 37.501235,
            "longitude": -122.001235,
        }
        if location
        else None
    )
    return payload


def _null_v2_summary():
    payload = _summary_v2()
    payload["vehicle"].update(
        {
            key: None
            for key in (
                "model",
                "trim",
                "locked",
                "software_version",
                "odometer",
                "exterior_color",
                "wheel_type",
                "roof_color",
                "charge_port_type",
                "efficiency_package",
                "rear_seat_heaters",
                "right_hand_drive",
                "europe_vehicle",
                "sunroof_installed",
                "sentry_mode",
                "service_mode",
                "valet_mode",
                "center_display_state",
            )
        }
    )
    payload["vehicle"]["speed_limit_mode"] = {"active": None, "limit": None}
    payload["battery"] = {key: None for key in payload["battery"]}
    payload["charging"] = {
        key: ({"pending": None, "mode": None} if key == "scheduled" else None) for key in payload["charging"]
    }
    payload["climate"] = {
        key: ({item: None for item in value} if isinstance(value, dict) else None)
        for key, value in payload["climate"].items()
    }
    payload["closures"] = {
        "all_closed": None,
        "open": [],
        "charge_port_open": None,
        "doors": {key: None for key in payload["closures"]["doors"]},
        "windows": {key: None for key in payload["closures"]["windows"]},
    }
    payload["tires"] = {group: {position: None for position in values} for group, values in payload["tires"].items()}
    payload["software_update"] = {key: None for key in payload["software_update"]}
    payload["preferences"] = {key: None for key in payload["preferences"]}
    return payload


def _plugin(tmp_path):
    plugin = VehicleStatus({"id": "vehicle_status"})
    plugin.get_plugin_dir = lambda path=None: str(tmp_path / path) if path else str(tmp_path)
    return plugin


def _render_payload(tmp_path, monkeypatch, payload, settings=None):
    client = FakeHttpClient(payload)
    monkeypatch.setattr(vehicle_module, "get_http_client", lambda: client)
    image = _plugin(tmp_path).generate_image(
        {"cacheSeconds": 0, **(settings or {})},
        DeviceConfig(),
    )
    assert client.calls
    return image


def _assert_change_is_inside(before, after, expected_box):
    bounds = ImageChops.difference(before, after).getbbox()
    assert bounds is not None
    left, top, right, bottom = bounds
    expected_left, expected_top, expected_right, expected_bottom = expected_box
    assert expected_left <= left < right <= expected_right
    assert expected_top <= top < bottom <= expected_bottom


def _visible_color_bbox(image, box, color):
    """Return the final-pixel bbox for one rendered color, relative to box."""
    crop = image.crop(box)
    mask = Image.new("L", crop.size)
    flattened = getattr(crop, "get_flattened_data", crop.getdata)
    mask.putdata([255 if pixel == color else 0 for pixel in flattened()])
    return mask.getbbox()


def _visible_non_background_bbox(image, box, background):
    """Return the final-pixel bbox for visible content, relative to box."""
    crop = image.crop(box)
    mask = Image.new("L", crop.size)
    flattened = getattr(crop, "get_flattened_data", crop.getdata)
    mask.putdata([255 if pixel != background else 0 for pixel in flattened()])
    return mask.getbbox()


def test_live_fetch_is_fixed_bounded_and_never_follows_redirects(tmp_path, monkeypatch):
    client = FakeHttpClient(_summary())
    monkeypatch.setattr(vehicle_module, "get_http_client", lambda: client)
    plugin = _plugin(tmp_path)
    device = DeviceConfig()

    image = plugin.generate_image({"cacheSeconds": 0}, device)

    assert image.size == (800, 480)
    assert image.mode == "RGB"
    assert read_source_provenance(image) is SourceProvenance.LIVE
    assert device.loaded == ["EPAPER_VEHICLE_BRIDGE_TOKEN", "EPAPER_VEHICLE_BRIDGE_ORIGIN"]
    assert len(client.calls) == 1
    method, url, kwargs = client.calls[0]
    assert (method, url) == ("GET", BRIDGE_SUMMARY_URL)
    assert url.endswith("?schema_version=3")
    assert kwargs["headers"] == {"Authorization": "Bearer bridge-token"}
    assert kwargs["allow_redirects"] is False
    assert kwargs["timeout"] == (4, 12)
    assert kwargs["max_bytes"] == 64 * 1024

    cache_text = plugin._cache_file().read_text(encoding="utf-8")
    assert "bridge-token" not in cache_text
    assert "vin" not in cache_text.lower()
    assert "location" not in cache_text.lower()


def test_v3_location_fetches_google_address_and_native_map_without_leaking_secrets(
    tmp_path,
    monkeypatch,
):
    now = datetime.fromisoformat("2026-08-08T20:00:00+00:00").timestamp()
    monkeypatch.setattr(vehicle_module.time, "time", lambda: now)
    client = LocationHttpClient(_summary_v3(), _map_png())
    monkeypatch.setattr(vehicle_module, "get_http_client", lambda: client)
    plugin = _plugin(tmp_path)
    device = DeviceConfig(
        env={"GOOGLE_MAPS_API_KEY": "google-maps-test-secret"},
    )

    image = plugin.generate_image(
        {
            "cacheSeconds": 0,
            "language": "en",
            "_inkypi_theme": {"mode": "day", "palette": {}},
        },
        device,
    )

    assert read_source_provenance(image) is SourceProvenance.LIVE
    assert device.loaded == [
        "EPAPER_VEHICLE_BRIDGE_TOKEN",
        "EPAPER_VEHICLE_BRIDGE_ORIGIN",
        "GOOGLE_MAPS_API_KEY",
    ]
    assert len(client.calls) == 3
    bridge_call, geocode_call, map_call = client.calls
    assert bridge_call[:3] == ("json", "GET", BRIDGE_SUMMARY_URL)
    assert bridge_call[3]["allow_redirects"] is False
    assert bridge_call[3]["max_bytes"] == 64 * 1024

    geocode_url = urlparse(geocode_call[2])
    geocode_query = parse_qs(geocode_url.query)
    assert (geocode_url.scheme, geocode_url.netloc, geocode_url.path) == (
        "https",
        "maps.googleapis.com",
        "/maps/api/geocode/json",
    )
    assert geocode_query == {
        "key": ["google-maps-test-secret"],
        "language": ["en"],
        "latlng": ["37.501235,-122.001235"],
    }
    assert geocode_call[3]["allow_redirects"] is False
    assert geocode_call[3]["max_bytes"] == 128 * 1024

    map_url = urlparse(map_call[2])
    map_query = parse_qs(map_url.query)
    assert (map_url.scheme, map_url.netloc, map_url.path) == (
        "https",
        "maps.googleapis.com",
        "/maps/api/staticmap",
    )
    assert map_query["size"] == ["{}x{}".format(*vehicle_module.LOCATION_MAP_SIZE)]
    assert map_query["scale"] == ["1"]
    assert map_query["key"] == ["google-maps-test-secret"]
    assert map_query["markers"] == ["color:red|37.501235,-122.001235"]
    assert map_call[3]["allow_redirects"] is False
    assert map_call[3]["max_bytes"] == 128 * 1024

    # The native map fills its box at full strength below its top fade, right across the
    # attribution strip (Google's logo and credit sit in its corners); its top edge fades
    # into the panel like the car's own map.
    map_left, map_top, map_right, map_bottom = vehicle_module.LOCATION_MAP_BOX
    assert _visible_color_bbox(image, (0, 0, 800, 480), (255, 0, 255)) == (
        map_left, map_top + vehicle_module.LOCATION_MAP_FADE_TOP, map_right, map_bottom)
    strip_y = map_bottom - vehicle_module.LOCATION_MAP_ATTRIBUTION_H // 2
    assert image.getpixel((map_left, strip_y)) == image.getpixel((map_right - 1, strip_y)) == (255, 0, 255)
    assert image.getpixel(((map_left + map_right) // 2, map_top)) != (255, 0, 255)

    summary_text = plugin._cache_file().read_text(encoding="utf-8")
    summary_payload = json.loads(summary_text)
    assert plugin._cache_file().name == "summary-v3.json"
    assert summary_payload["summary"]["location"] is None
    assert len(summary_payload["location_fingerprint"]) == 64
    for sensitive in (
        "37.501235",
        "-122.001235",
        "latitude",
        "longitude",
        "google-maps-test-secret",
    ):
        assert sensitive not in summary_text

    presentation_path = plugin._location_presentation_file()
    presentation_text = presentation_path.read_text(encoding="utf-8")
    assert presentation_path.stat().st_size <= 256 * 1024
    assert "1600 Amphitheatre Parkway" in presentation_text
    for sensitive in ("37.501235", "-122.001235", "google-maps-test-secret"):
        assert sensitive not in presentation_text
    if os.name != "nt":
        assert presentation_path.stat().st_mode & 0o777 == 0o600

    no_network = FakeHttpClient(error=AssertionError("theme render used network"))
    monkeypatch.setattr(vehicle_module, "get_http_client", lambda: no_network)
    theme_device = DeviceConfig(token=None, env={"GOOGLE_MAPS_API_KEY": "unused"})
    themed = plugin.generate_image(
        {
            "cacheSeconds": 900,
            "language": "en",
            "_theme_render_only": True,
            "_inkypi_theme": {"mode": "night", "palette": {}},
        },
        theme_device,
    )
    assert theme_device.loaded == []
    assert no_network.calls == []
    assert _visible_color_bbox(themed, (0, 0, 800, 480), (255, 0, 255)) == (
        map_left, map_top + vehicle_module.LOCATION_MAP_FADE_TOP, map_right, map_bottom)


def test_v3_location_contract_is_exact_and_bounded():
    assert vehicle_module.sanitize_summary(_summary_v3())["location"] == {
        "captured_at": "2026-08-08T19:59:50Z",
        "age_seconds": 10.0,
        "latitude": 37.501235,
        "longitude": -122.001235,
    }
    assert vehicle_module.sanitize_summary(_summary_v3(location=False))["location"] is None

    invalid = []
    extra = _summary_v3()
    extra["location"]["heading"] = 90
    invalid.append(extra)
    for key, value in (("latitude", 91), ("longitude", -181), ("age_seconds", 86_401)):
        payload = _summary_v3()
        payload["location"][key] = value
        invalid.append(payload)
    missing = _summary_v3()
    missing["location"].pop("longitude")
    invalid.append(missing)
    bad_time = _summary_v3()
    bad_time["location"]["captured_at"] = "not-a-time"
    invalid.append(bad_time)

    for payload in invalid:
        with pytest.raises(vehicle_module.SummaryContractError):
            vehicle_module.sanitize_summary(payload)


def test_new_coordinates_never_reuse_an_old_address_or_map_after_google_failure(
    tmp_path,
    monkeypatch,
):
    now = datetime.fromisoformat("2026-08-08T20:00:00+00:00").timestamp()
    monkeypatch.setattr(vehicle_module.time, "time", lambda: now)
    plugin = _plugin(tmp_path)
    device = DeviceConfig(env={"GOOGLE_MAPS_API_KEY": "google-maps-test-secret"})
    first_client = LocationHttpClient(_summary_v3(), _map_png())
    monkeypatch.setattr(vehicle_module, "get_http_client", lambda: first_client)
    first = plugin.generate_image({"cacheSeconds": 0}, device)
    presentation_path = plugin._location_presentation_file()
    original_presentation = presentation_path.read_bytes()
    original_payload = json.loads(original_presentation)

    moved = _summary_v3()
    moved["location"].update({"latitude": 37.601235, "longitude": -122.101235})
    failed_client = LocationHttpClient(
        moved,
        _map_png((0, 255, 0)),
        map_error=RuntimeError("secret query must not be logged"),
    )
    monkeypatch.setattr(vehicle_module, "get_http_client", lambda: failed_client)
    second = plugin.generate_image({"cacheSeconds": 0}, device)

    assert _visible_color_bbox(first, (0, 0, 800, 480), (255, 0, 255)) is not None
    assert _visible_color_bbox(second, (0, 0, 800, 480), (255, 0, 255)) is None
    assert presentation_path.read_bytes() == original_presentation
    summary_payload = json.loads(plugin._cache_file().read_text(encoding="utf-8"))
    assert summary_payload["location_fingerprint"] != original_payload["location_fingerprint"]
    assert len(failed_client.calls) == 3


def test_older_bridge_location_cannot_replace_a_newer_local_presentation(
    tmp_path,
    monkeypatch,
):
    now = datetime.fromisoformat("2026-08-08T20:00:00+00:00").timestamp()
    monkeypatch.setattr(vehicle_module.time, "time", lambda: now)
    plugin = _plugin(tmp_path)
    device = DeviceConfig(env={"GOOGLE_MAPS_API_KEY": "google-maps-test-secret"})

    first_client = LocationHttpClient(_summary_v3(), _map_png())
    monkeypatch.setattr(vehicle_module, "get_http_client", lambda: first_client)
    plugin.generate_image({"cacheSeconds": 0}, device)
    original_summary = plugin._cache_file().read_bytes()
    original_presentation = plugin._location_presentation_file().read_bytes()

    older = _summary_v3()
    older["snapshot"].update(
        {
            "captured_at": "2026-08-08T19:00:00Z",
            "freshness": "stale_cache",
            "age_seconds": 3_600,
            "vehicle_connectivity": "asleep",
        }
    )
    older["location"].update(
        {
            "captured_at": "2026-08-08T19:00:00Z",
            "age_seconds": 3_600,
            "latitude": 37.601235,
            "longitude": -122.101235,
        }
    )
    older_client = LocationHttpClient(older, _map_png((0, 255, 0)))
    monkeypatch.setattr(vehicle_module, "get_http_client", lambda: older_client)

    image = plugin.generate_image({"cacheSeconds": 0}, device)

    assert len(older_client.calls) == 1
    assert plugin._cache_file().read_bytes() == original_summary
    assert plugin._location_presentation_file().read_bytes() == original_presentation
    assert read_source_provenance(image) is SourceProvenance.FRESH_CACHE
    assert _visible_color_bbox(image, (0, 0, 800, 480), (255, 0, 255)) is not None
    assert _visible_color_bbox(image, (0, 0, 800, 480), (0, 255, 0)) is None


def test_summary_write_failure_never_commits_a_new_location_presentation(
    tmp_path,
    monkeypatch,
):
    now = datetime.fromisoformat("2026-08-08T20:00:00+00:00").timestamp()
    monkeypatch.setattr(vehicle_module.time, "time", lambda: now)
    plugin = _plugin(tmp_path)
    device = DeviceConfig(env={"GOOGLE_MAPS_API_KEY": "google-maps-test-secret"})

    first_client = LocationHttpClient(_summary_v3(), _map_png())
    monkeypatch.setattr(vehicle_module, "get_http_client", lambda: first_client)
    plugin.generate_image({"cacheSeconds": 0}, device)
    original_summary = plugin._cache_file().read_bytes()
    original_presentation = plugin._location_presentation_file().read_bytes()
    write_cache = plugin._write_cache_unlocked

    moved = _summary_v3()
    moved["location"].update({"latitude": 37.601235, "longitude": -122.101235})
    moved_client = LocationHttpClient(moved, _map_png((0, 255, 0)))
    monkeypatch.setattr(vehicle_module, "get_http_client", lambda: moved_client)
    monkeypatch.setattr(
        plugin,
        "_write_cache_unlocked",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("disk full")),
    )

    live = plugin.generate_image({"cacheSeconds": 0}, device)

    assert len(moved_client.calls) == 3
    assert _visible_color_bbox(live, (0, 0, 800, 480), (0, 255, 0)) is not None
    assert plugin._cache_file().read_bytes() == original_summary
    assert plugin._location_presentation_file().read_bytes() == original_presentation

    monkeypatch.setattr(plugin, "_write_cache_unlocked", write_cache)
    no_network = FakeHttpClient(error=AssertionError("theme render used network"))
    monkeypatch.setattr(vehicle_module, "get_http_client", lambda: no_network)
    themed = plugin.generate_image(
        {"cacheSeconds": 900, "_theme_render_only": True},
        DeviceConfig(token=None),
    )
    assert no_network.calls == []
    assert _visible_color_bbox(themed, (0, 0, 800, 480), (255, 0, 255)) is not None


def test_location_presentation_older_than_24_hours_is_not_rendered(
    tmp_path,
    monkeypatch,
):
    now = datetime.fromisoformat("2026-08-08T20:00:00+00:00").timestamp()
    monkeypatch.setattr(vehicle_module.time, "time", lambda: now)
    plugin = _plugin(tmp_path)
    client = LocationHttpClient(_summary_v3(), _map_png())
    monkeypatch.setattr(vehicle_module, "get_http_client", lambda: client)
    plugin.generate_image(
        {"cacheSeconds": 0},
        DeviceConfig(env={"GOOGLE_MAPS_API_KEY": "google-maps-test-secret"}),
    )
    path = plugin._location_presentation_file()
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["captured_at"] = "2026-08-07T19:59:58Z"
    path.write_text(json.dumps(payload), encoding="utf-8")
    no_network = FakeHttpClient(error=AssertionError("theme render used network"))
    monkeypatch.setattr(vehicle_module, "get_http_client", lambda: no_network)

    image = plugin.generate_image(
        {"cacheSeconds": 900, "_theme_render_only": True},
        DeviceConfig(token=None),
    )

    assert no_network.calls == []
    assert _visible_color_bbox(image, (0, 0, 800, 480), (255, 0, 255)) is None


@pytest.mark.parametrize(
    "captured_at",
    (
        "2026-08-07T19:59:59Z",
        "2026-08-08T20:05:01Z",
    ),
)
def test_live_location_outside_the_display_window_never_calls_google(
    tmp_path,
    monkeypatch,
    captured_at,
):
    now = datetime.fromisoformat("2026-08-08T20:00:00+00:00").timestamp()
    monkeypatch.setattr(vehicle_module.time, "time", lambda: now)
    payload = _summary_v3()
    payload["location"]["captured_at"] = captured_at
    client = LocationHttpClient(payload, _map_png())
    monkeypatch.setattr(vehicle_module, "get_http_client", lambda: client)
    device = DeviceConfig(env={"GOOGLE_MAPS_API_KEY": "google-maps-test-secret"})
    plugin = _plugin(tmp_path)

    image = plugin.generate_image({"cacheSeconds": 0}, device)

    assert read_source_provenance(image) is SourceProvenance.LIVE
    assert len(client.calls) == 1
    assert device.loaded == ["EPAPER_VEHICLE_BRIDGE_TOKEN", "EPAPER_VEHICLE_BRIDGE_ORIGIN"]
    assert _visible_color_bbox(image, (0, 0, 800, 480), (255, 0, 255)) is None
    cache_payload = json.loads(plugin._cache_file().read_text(encoding="utf-8"))
    assert "location_fingerprint" not in cache_payload


def test_location_presentation_is_language_specific(tmp_path, monkeypatch):
    now = datetime.fromisoformat("2026-08-08T20:00:00+00:00").timestamp()
    monkeypatch.setattr(vehicle_module.time, "time", lambda: now)
    plugin = _plugin(tmp_path)
    device = DeviceConfig(env={"GOOGLE_MAPS_API_KEY": "google-maps-test-secret"})

    english_client = LocationHttpClient(_summary_v3(), _map_png())
    monkeypatch.setattr(vehicle_module, "get_http_client", lambda: english_client)
    plugin.generate_image({"cacheSeconds": 0, "language": "en"}, device)
    english_fingerprint = json.loads(plugin._location_presentation_file().read_text(encoding="utf-8"))[
        "location_fingerprint"
    ]

    no_network = FakeHttpClient(error=AssertionError("theme render used network"))
    monkeypatch.setattr(vehicle_module, "get_http_client", lambda: no_network)
    mismatched_theme = plugin.generate_image(
        {
            "cacheSeconds": 900,
            "language": "zh-CN",
            "_theme_render_only": True,
        },
        DeviceConfig(token=None),
    )
    assert no_network.calls == []
    assert _visible_color_bbox(mismatched_theme, (0, 0, 800, 480), (255, 0, 255)) is None

    chinese_client = LocationHttpClient(_summary_v3(), _map_png((0, 255, 0)))
    monkeypatch.setattr(vehicle_module, "get_http_client", lambda: chinese_client)
    chinese = plugin.generate_image(
        {"cacheSeconds": 0, "language": "zh-CN"},
        device,
    )
    chinese_fingerprint = json.loads(plugin._location_presentation_file().read_text(encoding="utf-8"))[
        "location_fingerprint"
    ]

    assert len(chinese_client.calls) == 3
    geocode_query = parse_qs(urlparse(chinese_client.calls[1][2]).query)
    assert geocode_query["language"] == ["zh-CN"]
    assert chinese_fingerprint != english_fingerprint
    assert _visible_color_bbox(chinese, (0, 0, 800, 480), (0, 255, 0)) is not None


def test_header_reads_wordmark_model_then_verdict_on_one_ruled_line(tmp_path, monkeypatch):
    wordmark = Image.new("RGBA", (736, 172), (0, 0, 0, 255))
    real_safe_open_image = vehicle_module.safe_open_image

    def load_wordmark_sentinel(path, *args, **kwargs):
        name = Path(path).name if isinstance(path, (str, os.PathLike)) else ""
        if name == "grey_bullet_wordmark.png":
            return wordmark.copy()
        return real_safe_open_image(path, *args, **kwargs)

    monkeypatch.setattr(vehicle_module, "safe_open_image", load_wordmark_sentinel)
    image = _render_payload(tmp_path, monkeypatch, _summary_v2(),
                            {"language": "en", "_inkypi_theme": {"mode": "day", "palette": {}}})

    # The fixture's open rear trunk is a red lamp, so the verdict pill is red.
    ink, muted, red = (24, 28, 33), (91, 96, 102), (200, 30, 50)
    wordmark_bbox = _visible_color_bbox(image, (30, 16, 260, 60), ink)
    model_bbox = _visible_color_bbox(image, (200, 16, 520, 60), muted)
    verdict_bbox = _visible_color_bbox(image, (520, 16, 770, 60), red)
    assert wordmark_bbox is not None and model_bbox is not None and verdict_bbox is not None
    wordmark_right = 30 + wordmark_bbox[2]
    assert wordmark_right - (30 + wordmark_bbox[0]) >= 130
    assert 200 + model_bbox[0] - wordmark_right >= 8
    assert 520 + verdict_bbox[0] > 200 + model_bbox[2]
    assert 520 + verdict_bbox[2] == vehicle_module.PAGE_RIGHT + 1
    # The header rule spans the page under all three.
    rule = image.crop((36, 62, 764, 63))
    assert set(getattr(rule, "get_flattened_data", rule.getdata)()) == {ink}


@pytest.mark.parametrize("theme_mode", ["day", "night"])
def test_full_v2_dashboard_public_render_populates_every_region(
    tmp_path,
    monkeypatch,
    theme_mode,
):
    full = _render_payload(
        tmp_path / "full",
        monkeypatch,
        _summary_v2(),
        {
            "language": "zh-CN",
            "_inkypi_theme": {"mode": theme_mode, "palette": {}},
        },
    )
    empty = _render_payload(
        tmp_path / "empty",
        monkeypatch,
        _null_v2_summary(),
        {
            "language": "zh-CN",
            "_inkypi_theme": {"mode": theme_mode, "palette": {}},
        },
    )

    assert full.size == (800, 480)
    assert full.mode == "RGB"
    assert read_source_provenance(full) is SourceProvenance.LIVE
    difference = ImageChops.difference(full, empty)
    for region in (
        VERDICT_REGION,
        LAMP_REGION,
        ENERGY_REGION,
        BODY_COLUMN_REGION,
        CLIMATE_REGION,
        STRIP_REGION,
    ):
        assert difference.crop(region).getbbox() is not None


def test_full_v2_dashboard_uses_portable_ascii_copy(tmp_path, monkeypatch):
    _require_portable_ascii_status_font(monkeypatch)

    image = _render_payload(
        tmp_path,
        monkeypatch,
        _summary_v2(),
        {"language": "en"},
    )

    assert image.size == (800, 480)


def test_simplified_chinese_is_default_and_english_remains_selectable(
    tmp_path,
    monkeypatch,
):
    default_image = _render_payload(
        tmp_path / "default",
        monkeypatch,
        _summary_v2(),
    )
    chinese_image = _render_payload(
        tmp_path / "zh",
        monkeypatch,
        _summary_v2(),
        {"language": "zh-CN"},
    )
    english_image = _render_payload(
        tmp_path / "en",
        monkeypatch,
        _summary_v2(),
        {"language": "en"},
    )

    assert ImageChops.difference(default_image, chinese_image).getbbox() is None
    assert ImageChops.difference(chinese_image, english_image).getbbox() is not None


@pytest.mark.parametrize(
    "font_path",
    [
        path
        for path in (
            Path("C:/Windows/Fonts/msyh.ttc"),
            Path("C:/Windows/Fonts/msyhbd.ttc"),
        )
        if path.is_file()
    ],
)
def test_all_simplified_chinese_copy_has_real_msyh_glyphs(font_path):
    font = ImageFont.truetype(str(font_path), 14)
    notdef = _font_mask_signature(font, "\U0010ffff")

    characters = vehicle_module._fixed_ui_characters("zh-CN")

    assert characters
    missing = [character for character in characters if _font_mask_signature(font, character) == notdef]
    assert missing == []


@pytest.mark.parametrize("bold", [False, True])
def test_packaged_noto_font_covers_all_simplified_chinese_copy(monkeypatch, bold):
    monkeypatch.delenv("INKYPI_DATA_DIR", raising=False)
    expected = Path(__file__).resolve().parents[1] / "src/static/fonts/NotoSansSC-VF.ttf"

    font = vehicle_module.get_base_ui_font(14, bold=bold)

    assert Path(font.path).resolve() == expected.resolve()
    notdef = _font_mask_signature(font, "\U0010ffff")
    missing = [
        character
        for character in vehicle_module._fixed_ui_characters("zh-CN")
        if _font_mask_signature(font, character) == notdef
    ]
    assert missing == []


def test_long_chinese_v2_payload_keeps_panel_gutters_clean(tmp_path, monkeypatch):
    payload = _summary_v2()
    payload["vehicle"].update(
        {
            "display_name": "超" * 64,
            "model": "车型" * 20,
            "trim": "高性能版本" * 8,
            "software_version": "2026." + ("9" * 59),
            "exterior_color": "午夜银色车漆" * 9,
            "wheel_type": "二十一英寸涡轮轮毂" * 6,
            "roof_color": "车身同色车顶" * 9,
            "efficiency_package": "高效率套件" * 10,
        }
    )
    payload["closures"] = {
        "all_closed": False,
        "open": sorted(vehicle_module._OPEN_CLOSURES),
        "charge_port_open": True,
        "doors": {
            "driver_front": True,
            "driver_rear": True,
            "passenger_front": True,
            "passenger_rear": True,
            "front_trunk": True,
            "rear_trunk": True,
        },
        "windows": {
            "driver_front": True,
            "driver_rear": True,
            "passenger_front": True,
            "passenger_rear": True,
        },
    }

    image = _render_payload(
        tmp_path,
        monkeypatch,
        payload,
        {
            "language": "zh-CN",
            "_inkypi_theme": {"mode": "day", "palette": {}},
        },
    )

    assert read_source_provenance(image) is SourceProvenance.LIVE
    surface = (251, 249, 244)
    # Under the lamps, either side of the two column rules stays clear of the long copy.
    for gutter in (
        (319, 126, 327, 396),
        (329, 126, 337, 396),
        (517, 126, 526, 396),
        (528, 126, 537, 396),
    ):
        crop = image.crop(gutter)
        assert crop.getcolors(maxcolors=crop.width * crop.height) == [(crop.width * crop.height, surface)]


def _font_mask_signature(font, text):
    mask = font.getmask(text)
    return mask.size, bytes(mask)


def test_v2_unknown_openings_never_render_as_all_closed(tmp_path, monkeypatch):
    unknown = _null_v2_summary()
    closed = _null_v2_summary()
    closed["closures"] = {
        "all_closed": True,
        "open": [],
        "charge_port_open": False,
        "doors": {key: False for key in closed["closures"]["doors"]},
        "windows": {key: False for key in closed["closures"]["windows"]},
    }

    unknown_image = _render_payload(tmp_path / "unknown", monkeypatch, unknown)
    closed_image = _render_payload(tmp_path / "closed", monkeypatch, closed)

    difference = ImageChops.difference(unknown_image, closed_image)
    assert difference.crop(BODY_COLUMN_REGION).getbbox() is not None
    assert difference.crop(_lamp_box("openings")).getbbox() is not None
    _assert_changes_only_within(unknown_image, closed_image, BODY_COLUMN_REGION, _lamp_box("openings"))


def test_v2_rr_hard_warning_changes_only_the_tyre_map_lamp_and_verdict(tmp_path, monkeypatch):
    safe = _summary_v2()
    safe["tires"]["soft_warnings"] = {position: False for position in safe["tires"]["soft_warnings"]}
    safe["tires"]["hard_warnings"] = {position: False for position in safe["tires"]["hard_warnings"]}
    warning = deepcopy(safe)
    warning["tires"]["hard_warnings"]["rear_right"] = True

    safe_image = _render_payload(tmp_path / "safe", monkeypatch, safe)
    warning_image = _render_payload(tmp_path / "warning", monkeypatch, warning)

    # The rear-right pressure sits on a red plate, its lamp lights and the verdict counts one more.
    _assert_changes_only_within(safe_image, warning_image, (338, 126, 518, 330), _lamp_box("tyres"), VERDICT_REGION)
    red, rear_right_plate = (200, 30, 50), (466, 250, 518, 300)
    assert _visible_color_bbox(warning_image, rear_right_plate, red) is not None
    assert _visible_color_bbox(safe_image, rear_right_plate, red) is None


def test_v2_charging_values_change_only_energy_region(tmp_path, monkeypatch):
    disconnected = _summary_v2()
    disconnected["charging"].update(
        {
            "state": "Disconnected",
            "time_to_full_minutes": None,
            "power_kw": 0,
            "energy_added_kwh": 0,
            "rate": None,
            "actual_current_a": 0,
            "voltage_v": 0,
        }
    )
    charging = _summary_v2()

    disconnected_image = _render_payload(
        tmp_path / "disconnected",
        monkeypatch,
        disconnected,
    )
    charging_image = _render_payload(tmp_path / "charging", monkeypatch, charging)

    _assert_changes_only_within(disconnected_image, charging_image, ENERGY_REGION, _lamp_box("charging"))


def test_v2_charge_rate_matches_worker_5000_upper_bound(tmp_path, monkeypatch):
    boundary = _summary_v2()
    boundary["charging"]["rate"] = {"value": 5000, "unit": "mi/h"}
    boundary_image = _render_payload(
        tmp_path / "boundary",
        monkeypatch,
        boundary,
    )

    assert read_source_provenance(boundary_image) is SourceProvenance.LIVE

    above = _summary_v2()
    above["charging"]["rate"] = {"value": 5000.01, "unit": "mi/h"}
    client = FakeHttpClient(above)
    monkeypatch.setattr(vehicle_module, "get_http_client", lambda: client)
    plugin = _plugin(tmp_path / "above")

    above_image = plugin.generate_image({"cacheSeconds": 0}, DeviceConfig())

    assert read_source_provenance(above_image) is SourceProvenance.LOCAL_FALLBACK
    assert above_image.info["inkypi_skip_cache"] is True
    assert not plugin._cache_file(create=False).exists()


def test_v2_auto_units_follow_vehicle_preferences():
    preferences = {
        "distance_unit": "km",
        "temperature_unit": "F",
        "pressure_unit": "psi",
    }

    assert (
        vehicle_module._measurement_text(
            {"value": 100, "unit": "mi"},
            {"distanceUnit": "auto"},
            preferences,
        )
        == "161 km"
    )
    assert vehicle_module._temperature_text(0, {}, preferences) == "32 F"
    assert (
        vehicle_module._pressure_text(
            {"value": 2.758, "unit": "bar"},
            {},
            preferences,
        )
        == "40 PSI"
    )
    assert (
        vehicle_module._speed_text(
            {"value": 10, "unit": "mi/h"},
            {},
            preferences,
        )
        == "16 KM/H"
    )


def test_v2_contract_is_exact_and_contradictions_fail_closed(tmp_path, monkeypatch):
    extra = _summary_v2()
    extra["vehicle"]["vin"] = "5YJ3E1EA7KF000001"
    missing = _summary_v2()
    missing["charging"].pop("voltage_v")
    unsafe_closed = _summary_v2()
    unsafe_closed["closures"].update({"all_closed": True, "open": []})
    uncertain_closed = _summary_v2()
    uncertain_closed["closures"] = {
        "all_closed": True,
        "open": [],
        "charge_port_open": False,
        "doors": {key: (None if key == "driver_front" else False) for key in uncertain_closed["closures"]["doors"]},
        "windows": {key: False for key in uncertain_closed["closures"]["windows"]},
    }
    uncertain_listed_open = _summary_v2()
    uncertain_listed_open["closures"]["doors"]["rear_trunk"] = None
    unknown_with_known_open = _summary_v2()
    unknown_with_known_open["closures"]["all_closed"] = None
    unknown_with_all_known_closed = _summary_v2()
    unknown_with_all_known_closed["closures"] = {
        "all_closed": None,
        "open": [],
        "charge_port_open": False,
        "doors": {key: False for key in unknown_with_all_known_closed["closures"]["doors"]},
        "windows": {key: False for key in unknown_with_all_known_closed["closures"]["windows"]},
    }
    invalid_warning = _summary_v2()
    invalid_warning["tires"]["hard_warnings"]["rear_right"] = "yes"

    invalid_payloads = (
        extra,
        missing,
        unsafe_closed,
        uncertain_closed,
        uncertain_listed_open,
        unknown_with_known_open,
        unknown_with_all_known_closed,
        invalid_warning,
    )
    for index, payload in enumerate(invalid_payloads):
        client = FakeHttpClient(payload)
        monkeypatch.setattr(vehicle_module, "get_http_client", lambda: client)
        plugin = _plugin(tmp_path / str(index))

        image = plugin.generate_image({"cacheSeconds": 0}, DeviceConfig())

        assert read_source_provenance(image) is SourceProvenance.LOCAL_FALLBACK
        assert image.info["inkypi_skip_cache"] is True
        assert not plugin._cache_file(create=False).exists()


def test_legacy_summary_v1_cache_is_read_and_new_writes_use_v3_filename(
    tmp_path,
    monkeypatch,
):
    plugin = _plugin(tmp_path)
    cache_dir = Path(plugin.cache_dir(leaf="cache"))
    legacy_path = cache_dir / "summary-v1.json"
    legacy_path.write_text(
        json.dumps({"fetched_at": time.time(), "summary": _summary()}),
        encoding="utf-8",
    )
    client = FakeHttpClient(error=AssertionError("network called"))
    monkeypatch.setattr(vehicle_module, "get_http_client", lambda: client)

    image = plugin.generate_image({"cacheSeconds": 900}, DeviceConfig())

    assert client.calls == []
    assert read_source_provenance(image) is SourceProvenance.FRESH_CACHE
    assert legacy_path.exists()
    assert not (cache_dir / "summary-v3.json").exists()

    plugin._write_cache({"fetched_at": time.time(), "summary": _summary_v2()})
    assert plugin._cache_file().name == "summary-v3.json"
    assert plugin._cache_file().exists()


def test_fresh_legacy_cache_wins_over_expired_v2_after_rollback(tmp_path, monkeypatch):
    plugin = _plugin(tmp_path)
    cache_dir = Path(plugin.cache_dir(leaf="cache"))
    now = time.time()
    (cache_dir / "summary-v2.json").write_text(
        json.dumps({"fetched_at": now - 90_000, "summary": _summary_v2()}),
        encoding="utf-8",
    )
    (cache_dir / "summary-v1.json").write_text(
        json.dumps({"fetched_at": now, "summary": _summary()}),
        encoding="utf-8",
    )
    client = FakeHttpClient(error=AssertionError("network called"))
    monkeypatch.setattr(vehicle_module, "get_http_client", lambda: client)

    image = plugin.generate_image(
        {"cacheSeconds": 900, "language": "en"},
        DeviceConfig(token=""),
    )

    assert client.calls == []
    assert read_source_provenance(image) is SourceProvenance.FRESH_CACHE
    assert image.info.get("inkypi_skip_cache") is not True


def test_local_cache_hit_displays_effective_freshness(tmp_path, monkeypatch):
    now = 1_900_000_000.0
    monkeypatch.setattr(vehicle_module.time, "time", lambda: now)
    client = FakeHttpClient(_summary_v2())
    monkeypatch.setattr(vehicle_module, "get_http_client", lambda: client)
    plugin = _plugin(tmp_path)
    settings = {
        "cacheSeconds": 900,
        "language": "zh-CN",
        "_inkypi_theme": {"mode": "day", "palette": {}},
    }

    live_image = plugin.generate_image(settings, DeviceConfig())
    cached_image = plugin.generate_image(settings, DeviceConfig())

    assert len(client.calls) == 1
    assert read_source_provenance(live_image) is SourceProvenance.LIVE
    assert read_source_provenance(cached_image) is SourceProvenance.FRESH_CACHE
    # Seconds-old values read the same either way; the footer names where they came from.
    _assert_changes_only_within(live_image, cached_image, FOOTER_REGION)


def test_failed_refresh_displays_stale_effective_freshness(tmp_path, monkeypatch):
    now = 1_900_000_000.0
    monkeypatch.setattr(vehicle_module.time, "time", lambda: now)
    client = FakeHttpClient(_summary_v2())
    monkeypatch.setattr(vehicle_module, "get_http_client", lambda: client)
    plugin = _plugin(tmp_path)
    settings = {
        "cacheSeconds": 900,
        "language": "zh-CN",
        "_inkypi_theme": {"mode": "day", "palette": {}},
    }
    plugin.generate_image(settings, DeviceConfig())
    cached_image = plugin.generate_image(settings, DeviceConfig())
    client.error = TimeoutError("bridge unavailable")

    stale_image = plugin.generate_image(
        {**settings, "forceRefresh": True},
        DeviceConfig(),
    )

    assert len(client.calls) == 2
    assert read_source_provenance(cached_image) is SourceProvenance.FRESH_CACHE
    assert read_source_provenance(stale_image) is SourceProvenance.STALE_CACHE
    assert stale_image.info["inkypi_skip_cache"] is True
    _assert_changes_only_within(cached_image, stale_image, FOOTER_REGION)


def test_live_footer_renders_with_portable_ascii_status_font(tmp_path, monkeypatch):
    client = FakeHttpClient(_summary())
    monkeypatch.setattr(vehicle_module, "get_http_client", lambda: client)
    _require_portable_ascii_status_font(monkeypatch)

    image = _plugin(tmp_path).generate_image(
        {"cacheSeconds": 0, "language": "en"},
        DeviceConfig(),
    )

    assert image.size == (800, 480)


def test_vehicle_dashboard_renders_every_public_v1_information_group(
    tmp_path,
    monkeypatch,
):
    baseline = _summary()
    baseline_image = _render_payload(tmp_path, monkeypatch, baseline)

    energy = deepcopy(baseline)
    energy["battery"].update({"time_to_full_minutes": 95, "power_kw": 11})
    energy_image = _render_payload(tmp_path, monkeypatch, energy)
    _assert_change_is_inside(baseline_image, energy_image, ENERGY_REGION)

    security = deepcopy(baseline)
    security["closures"] = {
        "all_closed": False,
        "open": ["driver_front_door", "rear_trunk"],
        "charge_port_open": False,
    }
    security_image = _render_payload(tmp_path, monkeypatch, security)
    _assert_changes_only_within(baseline_image, security_image, BODY_COLUMN_REGION, _lamp_box("openings"),
                                VERDICT_REGION)

    climate = deepcopy(baseline)
    climate["climate"]["is_climate_on"] = True
    climate_image = _render_payload(tmp_path, monkeypatch, climate)
    _assert_changes_only_within(baseline_image, climate_image, CLIMATE_REGION, _lamp_box("climate"))

    vehicle = deepcopy(baseline)
    vehicle["vehicle"].update(
        {
            "software_version": "2026.26.3",
            "odometer": {"value": 54_321.9, "unit": "mi"},
        }
    )
    vehicle_image = _render_payload(tmp_path, monkeypatch, vehicle)
    _assert_change_is_inside(baseline_image, vehicle_image, STRIP_REGION)


def test_setup_message_renders_with_portable_ascii_status_font(tmp_path, monkeypatch):
    client = FakeHttpClient(error=AssertionError("network called"))
    monkeypatch.setattr(vehicle_module, "get_http_client", lambda: client)
    _require_portable_ascii_status_font(monkeypatch)

    image = _plugin(tmp_path).generate_image(
        {"language": "en"},
        DeviceConfig(token=""),
    )

    assert image.size == (800, 480)
    assert client.calls == []


def test_fresh_local_cache_avoids_network_and_attests_cache(tmp_path, monkeypatch):
    plugin = _plugin(tmp_path)
    plugin._write_cache({"fetched_at": time.time(), "summary": _summary()})
    client = FakeHttpClient(error=AssertionError("network called"))
    monkeypatch.setattr(vehicle_module, "get_http_client", lambda: client)

    image = plugin.generate_image({"cacheSeconds": 900}, DeviceConfig())

    assert client.calls == []
    assert read_source_provenance(image) is SourceProvenance.FRESH_CACHE
    assert image.info.get("inkypi_skip_cache") is not True


def test_failed_refresh_preserves_stale_cache_and_is_non_cacheable(tmp_path, monkeypatch):
    plugin = _plugin(tmp_path)
    plugin._write_cache({"fetched_at": time.time() - 901, "summary": _summary()})
    original = plugin._cache_file().read_bytes()
    client = FakeHttpClient(error=RuntimeError("secret provider body"))
    monkeypatch.setattr(vehicle_module, "get_http_client", lambda: client)

    image = plugin.generate_image({"cacheSeconds": 0}, DeviceConfig())

    assert len(client.calls) == 1
    assert read_source_provenance(image) is SourceProvenance.STALE_CACHE
    assert image.info["inkypi_skip_cache"] is True
    assert plugin._cache_file().read_bytes() == original


def test_failed_refresh_never_displays_vehicle_values_older_than_24_hours(tmp_path, monkeypatch):
    now = 1_800_000_000.0
    plugin = _plugin(tmp_path)
    plugin._write_cache({"fetched_at": now - 86_401, "summary": _summary()})
    client = FakeHttpClient(error=RuntimeError("provider unavailable"))
    monkeypatch.setattr(vehicle_module, "get_http_client", lambda: client)
    monkeypatch.setattr(vehicle_module.time, "time", lambda: now)
    monkeypatch.setattr(
        plugin,
        "_render_summary",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("expired vehicle values rendered")),
    )

    image = plugin.generate_image({"cacheSeconds": 900}, DeviceConfig())

    assert len(client.calls) == 1
    assert read_source_provenance(image) is SourceProvenance.LOCAL_FALLBACK
    assert image.info["inkypi_skip_cache"] is True


def test_theme_only_uses_cache_without_credentials_network_or_writes(tmp_path, monkeypatch):
    plugin = _plugin(tmp_path)
    plugin._write_cache({"fetched_at": time.time(), "summary": _summary()})
    original = plugin._cache_file().read_bytes()
    client = FakeHttpClient(error=AssertionError("network called"))
    monkeypatch.setattr(vehicle_module, "get_http_client", lambda: client)
    monkeypatch.setattr(
        plugin,
        "_write_cache",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("cache write")),
    )
    device = DeviceConfig(token=None)

    image = plugin.generate_image(
        {"cacheSeconds": 900, "_theme_render_only": True},
        device,
    )

    assert device.loaded == []
    assert client.calls == []
    assert plugin._cache_file().read_bytes() == original
    assert read_source_provenance(image) is SourceProvenance.FRESH_CACHE


def test_theme_only_marks_expired_cache_stale_and_advances_age(tmp_path, monkeypatch):
    now = 1_800_000_000.0
    plugin = _plugin(tmp_path)
    plugin._write_cache({"fetched_at": now - 901, "summary": _summary()})
    original = plugin._cache_file().read_bytes()
    rendered = {}
    render_summary = plugin._render_summary

    def capture_summary(
        summary,
        dimensions,
        theme,
        settings,
        location_presentation=None,
    ):
        rendered["summary"] = summary
        return render_summary(
            summary,
            dimensions,
            theme,
            settings,
            location_presentation=location_presentation,
        )

    monkeypatch.setattr(vehicle_module.time, "time", lambda: now)
    monkeypatch.setattr(plugin, "_render_summary", capture_summary)
    monkeypatch.setattr(
        plugin,
        "_write_cache",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("cache write")),
    )

    image = plugin.generate_image(
        {"cacheSeconds": 900, "_theme_render_only": True},
        DeviceConfig(token=None),
    )

    assert plugin._cache_file().read_bytes() == original
    assert rendered["summary"]["snapshot"]["age_seconds"] == 906
    assert read_source_provenance(image) is SourceProvenance.STALE_CACHE
    assert image.info["inkypi_skip_cache"] is True


def test_missing_token_renders_local_setup_without_network(tmp_path, monkeypatch):
    client = FakeHttpClient(error=AssertionError("network called"))
    monkeypatch.setattr(vehicle_module, "get_http_client", lambda: client)
    plugin = _plugin(tmp_path)

    image = plugin.generate_image({}, DeviceConfig(token="  "))

    assert client.calls == []
    assert read_source_provenance(image) is SourceProvenance.LOCAL_FALLBACK
    assert image.info["inkypi_skip_cache"] is True


class RecordingSSRFPolicy:
    def __init__(self, error=None):
        self.error = error
        self.validated = []

    def resolve_and_validate(self, url):
        self.validated.append(url)
        if self.error:
            raise self.error
        return SimpleNamespace(normalized_url=url)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("", vehicle_module.BRIDGE_ORIGIN),
        ("  ", vehicle_module.BRIDGE_ORIGIN),
        ("https://My-Bridge.Example.workers.dev/", "https://my-bridge.example.workers.dev"),
        ("https://bridge.example.com.", "https://bridge.example.com"),
        ("https://bridge.example.com:443", "https://bridge.example.com"),
        ("https://bridge.example.com:8443", "https://bridge.example.com:8443"),
    ],
)
def test_bridge_origin_normalizes_a_bare_https_origin(value, expected):
    assert vehicle_module.bridge_origin(value) == expected


@pytest.mark.parametrize(
    "value",
    [
        "http://bridge.example.com",
        "bridge.example.com",
        "ftp://bridge.example.com",
        "https://user:secret@bridge.example.com",
        "https://bridge.example.com/api/vehicle-summary",
        "https://bridge.example.com/?next=1",
        "https://bridge.example.com/#top",
        "https://203.0.113.7",
        "https://[2001:db8::1]",
        "https://localhost",
        "https://bridge.localhost",
        "https://raspberrypi.local",
        "https://bridge",
        "https://bridge.example.com:99999",
    ],
)
def test_bridge_origin_rejects_anything_but_a_public_https_origin(value):
    with pytest.raises(vehicle_module.BridgeOriginError):
        vehicle_module.bridge_origin(value)


def test_custom_bridge_origin_is_validated_and_gets_the_fixed_summary_path(tmp_path, monkeypatch):
    client = FakeHttpClient(_summary())
    policy = RecordingSSRFPolicy()
    monkeypatch.setattr(vehicle_module, "get_http_client", lambda: client)
    monkeypatch.setattr(vehicle_module, "get_ssrf_policy", lambda: policy)
    plugin = _plugin(tmp_path)
    device = DeviceConfig(env={"EPAPER_VEHICLE_BRIDGE_ORIGIN": "https://my-bridge.example.workers.dev/"})

    image = plugin.generate_image({"cacheSeconds": 0}, device)

    assert read_source_provenance(image) is SourceProvenance.LIVE
    assert policy.validated == ["https://my-bridge.example.workers.dev"]
    method, url, kwargs = client.calls[0]
    assert (method, url) == ("GET", "https://my-bridge.example.workers.dev/api/vehicle-summary?schema_version=3")
    assert kwargs["headers"] == {"Authorization": "Bearer bridge-token"}
    assert kwargs["allow_redirects"] is False
    assert kwargs["max_bytes"] == 64 * 1024


def test_default_bridge_skips_the_dns_check(tmp_path, monkeypatch):
    client = FakeHttpClient(_summary())
    monkeypatch.setattr(vehicle_module, "get_http_client", lambda: client)
    monkeypatch.setattr(
        vehicle_module, "get_ssrf_policy", lambda: (_ for _ in ()).throw(AssertionError("policy used"))
    )

    plugin = _plugin(tmp_path)
    plugin.generate_image({"cacheSeconds": 0}, DeviceConfig())

    assert client.calls[0][1] == BRIDGE_SUMMARY_URL


def test_invalid_bridge_origin_shows_a_local_message_and_never_sends_the_token(
    tmp_path, monkeypatch, caplog
):
    client = FakeHttpClient(error=AssertionError("network called"))
    monkeypatch.setattr(vehicle_module, "get_http_client", lambda: client)
    plugin = _plugin(tmp_path)
    shown = []
    render_message = plugin._render_local_message
    monkeypatch.setattr(
        plugin,
        "_render_local_message",
        lambda dimensions, theme, title, message, language: shown.append((title, message))
        or render_message(dimensions, theme, title, message, language),
    )
    device = DeviceConfig(env={"EPAPER_VEHICLE_BRIDGE_ORIGIN": "http://my-bridge.example.workers.dev"})

    image = plugin.generate_image({"cacheSeconds": 0, "language": "en"}, device)

    assert client.calls == []
    assert shown == [("CHECK BRIDGE ADDRESS", "EPAPER_VEHICLE_BRIDGE_ORIGIN must be https://<bridge host>.")]
    assert read_source_provenance(image) is SourceProvenance.LOCAL_FALLBACK
    assert "bridge-token" not in caplog.text


def test_bridge_origin_resolving_to_a_private_address_is_not_called(tmp_path, monkeypatch):
    client = FakeHttpClient(error=AssertionError("network called"))
    policy = RecordingSSRFPolicy(error=UnsafeTarget("target resolves to a private address"))
    monkeypatch.setattr(vehicle_module, "get_http_client", lambda: client)
    monkeypatch.setattr(vehicle_module, "get_ssrf_policy", lambda: policy)
    plugin = _plugin(tmp_path)
    device = DeviceConfig(env={"EPAPER_VEHICLE_BRIDGE_ORIGIN": "https://rebind.example.com"})

    image = plugin.generate_image({"cacheSeconds": 0}, device)

    assert policy.validated == ["https://rebind.example.com"]
    assert client.calls == []
    assert read_source_provenance(image) is SourceProvenance.LOCAL_FALLBACK


def test_worker_stale_snapshot_stays_stale_and_non_cacheable(tmp_path, monkeypatch):
    # An asleep/offline vehicle is healthy (see test_vehicle_status_resting.py);
    # only the bridge's provider fallback reports the source as stale.
    client = FakeHttpClient(_summary(freshness="stale_cache", connectivity="unavailable"))
    monkeypatch.setattr(vehicle_module, "get_http_client", lambda: client)
    plugin = _plugin(tmp_path)

    image = plugin.generate_image({"cacheSeconds": 0}, DeviceConfig())

    assert read_source_provenance(image) is SourceProvenance.STALE_CACHE
    assert image.info["inkypi_skip_cache"] is True


def test_same_snapshot_uses_new_bridge_status_without_renewing_source_cache(
    tmp_path, monkeypatch
):
    now = datetime.fromisoformat("2026-08-05T20:05:00+00:00").timestamp()
    plugin = _plugin(tmp_path)
    original = _summary(freshness="stale_cache", connectivity="offline")
    plugin._write_cache({"fetched_at": now - 300, "summary": original})
    updated = deepcopy(original)
    updated["served_at"] = "2026-08-05T20:05:00Z"
    updated["snapshot"].update(age_seconds=305, vehicle_connectivity="unavailable")
    client = FakeHttpClient(updated)
    rendered = {}
    render_summary = plugin._render_summary

    def capture_summary(summary, *args, **kwargs):
        rendered["summary"] = deepcopy(summary)
        return render_summary(summary, *args, **kwargs)

    monkeypatch.setattr(vehicle_module.time, "time", lambda: now)
    monkeypatch.setattr(vehicle_module, "get_http_client", lambda: client)
    monkeypatch.setattr(plugin, "_render_summary", capture_summary)

    image = plugin.generate_image({"cacheSeconds": 0}, DeviceConfig())

    assert rendered["summary"]["snapshot"] == {
        "captured_at": "2026-08-05T19:59:55Z",
        "freshness": "stale_cache",
        "age_seconds": 305,
        "vehicle_connectivity": "unavailable",
    }
    assert read_source_provenance(image) is SourceProvenance.STALE_CACHE
    assert image.info["inkypi_skip_cache"] is True
    cached = plugin._read_cache()
    assert cached["fetched_at"] == now - 300
    assert cached["summary"]["served_at"] == "2026-08-05T20:05:00Z"
    assert cached["summary"]["snapshot"]["captured_at"] == "2026-08-05T19:59:55Z"
    assert cached["summary"]["snapshot"]["age_seconds"] == 5
    assert cached["summary"]["snapshot"]["vehicle_connectivity"] == "unavailable"


@pytest.mark.parametrize("reported_age", [900, None])
def test_status_update_keeps_cache_deadline_and_monotonic_age(
    tmp_path, monkeypatch, reported_age
):
    now = datetime.fromisoformat("2026-08-05T20:14:59+00:00").timestamp()
    plugin = _plugin(tmp_path)
    original = _summary(freshness="stale_cache", connectivity="offline")
    plugin._write_cache({"fetched_at": now - 899, "summary": original})
    updated = deepcopy(original)
    updated["served_at"] = "2026-08-05T20:14:59Z"
    updated["snapshot"].update(age_seconds=reported_age, vehicle_connectivity="unavailable")
    client = FakeHttpClient(updated)
    rendered = []
    render_summary = plugin._render_summary

    def capture_summary(summary, *args, **kwargs):
        rendered.append(deepcopy(summary))
        return render_summary(summary, *args, **kwargs)

    monkeypatch.setattr(vehicle_module.time, "time", lambda: now)
    monkeypatch.setattr(vehicle_module, "get_http_client", lambda: client)
    monkeypatch.setattr(plugin, "_render_summary", capture_summary)

    image = plugin.generate_image({"cacheSeconds": 900, "forceRefresh": True}, DeviceConfig())
    assert rendered[-1]["snapshot"]["age_seconds"] == 904
    assert read_source_provenance(image) is SourceProvenance.STALE_CACHE

    now += 2
    plugin.generate_image({"_theme_render_only": True}, DeviceConfig(token=None))
    assert rendered[-1]["snapshot"]["age_seconds"] == 906
    assert rendered[-1]["snapshot"]["vehicle_connectivity"] == "unavailable"
    assert len(client.calls) == 1

    client.error = RuntimeError("bridge unavailable")
    image = plugin.generate_image({"cacheSeconds": 900}, DeviceConfig())
    assert len(client.calls) == 2
    assert rendered[-1]["snapshot"]["age_seconds"] == 906
    assert rendered[-1]["snapshot"]["vehicle_connectivity"] == "unavailable"
    assert read_source_provenance(image) is SourceProvenance.STALE_CACHE
    assert image.info["inkypi_skip_cache"] is True

    now = datetime.fromisoformat("2026-08-06T19:59:56+00:00").timestamp()
    rendered_count = len(rendered)
    image = plugin.generate_image({"_theme_render_only": True}, DeviceConfig(token=None))
    assert len(rendered) == rendered_count
    assert read_source_provenance(image) is SourceProvenance.LOCAL_FALLBACK
    assert image.info["inkypi_skip_cache"] is True


@pytest.mark.parametrize(
    "captured_at, served_at",
    [
        ("2026-08-05T19:59:00Z", "2026-08-05T20:05:00Z"),
        ("2026-08-05T19:59:55Z", "2026-08-05T19:59:59Z"),
        ("2026-08-05T19:59:55Z", "2026-08-05T20:00:00Z"),
    ],
)
def test_older_stale_response_cannot_downgrade_fresher_local_snapshot(
    tmp_path, monkeypatch, captured_at, served_at
):
    now = datetime.fromisoformat("2026-08-05T20:05:00+00:00").timestamp()
    plugin = _plugin(tmp_path)
    original = _summary()
    plugin._write_cache({"fetched_at": now - 300, "summary": original})
    original_bytes = plugin._cache_file().read_bytes()
    replay = deepcopy(original)
    replay["served_at"] = served_at
    replay["snapshot"].update(
        captured_at=captured_at,
        age_seconds=300,
        freshness="stale_cache",
        vehicle_connectivity="unavailable",
    )
    client = FakeHttpClient(replay)
    rendered = {}
    render_summary = plugin._render_summary

    def capture_summary(summary, *args, **kwargs):
        rendered["summary"] = deepcopy(summary)
        return render_summary(summary, *args, **kwargs)

    monkeypatch.setattr(vehicle_module.time, "time", lambda: now)
    monkeypatch.setattr(vehicle_module, "get_http_client", lambda: client)
    monkeypatch.setattr(plugin, "_render_summary", capture_summary)

    image = plugin.generate_image({"cacheSeconds": 0}, DeviceConfig())

    assert rendered["summary"]["snapshot"]["captured_at"] == "2026-08-05T19:59:55Z"
    assert rendered["summary"]["snapshot"]["vehicle_connectivity"] == "online"
    assert rendered["summary"]["snapshot"]["age_seconds"] == 305
    assert read_source_provenance(image) is SourceProvenance.FRESH_CACHE
    assert plugin._cache_file().read_bytes() == original_bytes


def test_new_usable_stale_snapshot_replaces_expired_local_cache(tmp_path, monkeypatch):
    now = datetime.fromisoformat("2026-08-05T20:00:00+00:00").timestamp()
    plugin = _plugin(tmp_path)
    expired = _summary()
    expired["served_at"] = "2026-08-04T19:00:00Z"
    expired["snapshot"]["captured_at"] = "2026-08-04T18:59:55Z"
    expired["vehicle"]["display_name"] = "Expired vehicle"
    plugin._write_cache({"fetched_at": now - 90_000, "summary": expired})
    replacement = _summary(freshness="stale_cache", connectivity="unavailable")
    replacement["vehicle"]["display_name"] = "Current vehicle"
    monkeypatch.setattr(vehicle_module.time, "time", lambda: now)
    monkeypatch.setattr(
        vehicle_module,
        "get_http_client",
        lambda: FakeHttpClient(replacement),
    )

    image = plugin.generate_image({"cacheSeconds": 0}, DeviceConfig())
    cached = plugin._read_cache()

    assert read_source_provenance(image) is SourceProvenance.STALE_CACHE
    assert cached["fetched_at"] == now
    assert cached["summary"]["vehicle"]["display_name"] == "Current vehicle"


def test_same_expired_snapshot_cannot_be_revived_by_reset_bridge_age(tmp_path, monkeypatch):
    now = datetime.fromisoformat("2026-08-06T20:00:00+00:00").timestamp()
    plugin = _plugin(tmp_path)
    original = _summary(freshness="stale_cache", connectivity="offline")
    plugin._write_cache({"fetched_at": now - 86_400, "summary": original})
    original_bytes = plugin._cache_file().read_bytes()
    replay = deepcopy(original)
    replay["served_at"] = "2026-08-06T20:00:00Z"
    replay["snapshot"]["vehicle_connectivity"] = "unavailable"
    client = FakeHttpClient(replay)
    monkeypatch.setattr(vehicle_module.time, "time", lambda: now)
    monkeypatch.setattr(vehicle_module, "get_http_client", lambda: client)
    monkeypatch.setattr(
        plugin,
        "_render_summary",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("expired vehicle values rendered")),
    )

    image = plugin.generate_image({"cacheSeconds": 0}, DeviceConfig())

    assert len(client.calls) == 1
    assert read_source_provenance(image) is SourceProvenance.LOCAL_FALLBACK
    assert image.info["inkypi_skip_cache"] is True
    assert plugin._cache_file().read_bytes() == original_bytes


def test_status_response_recovers_from_a_future_local_cache_clock(tmp_path, monkeypatch):
    now = datetime.fromisoformat("2026-08-05T20:05:00+00:00").timestamp()
    plugin = _plugin(tmp_path)
    original = _summary(freshness="stale_cache", connectivity="offline")
    plugin._write_cache({"fetched_at": now + 60, "summary": original})
    updated = deepcopy(original)
    updated["served_at"] = "2026-08-05T20:05:00Z"
    updated["snapshot"].update(age_seconds=None, vehicle_connectivity="unavailable")
    monkeypatch.setattr(vehicle_module.time, "time", lambda: now)
    monkeypatch.setattr(vehicle_module, "get_http_client", lambda: FakeHttpClient(updated))

    image = plugin.generate_image({"cacheSeconds": 900}, DeviceConfig())

    assert read_source_provenance(image) is SourceProvenance.STALE_CACHE
    assert image.info["inkypi_skip_cache"] is True
    assert plugin._read_cache()["fetched_at"] == now


def test_invalid_or_sensitive_response_is_never_cached(tmp_path, monkeypatch):
    payload = _summary()
    payload["vehicle"]["vin"] = "5YJ3E1EA7KF000001"
    client = FakeHttpClient(payload)
    monkeypatch.setattr(vehicle_module, "get_http_client", lambda: client)
    plugin = _plugin(tmp_path)

    image = plugin.generate_image({"cacheSeconds": 0}, DeviceConfig())

    assert read_source_provenance(image) is SourceProvenance.LOCAL_FALLBACK
    assert image.info["inkypi_skip_cache"] is True
    assert not plugin._cache_file(create=False).exists()


@pytest.mark.parametrize(
    "closures",
    [
        {
            "all_closed": True,
            "open": ["driver_front_door"],
            "charge_port_open": False,
        },
        {"all_closed": True, "open": [], "charge_port_open": True},
        {"all_closed": False, "open": [], "charge_port_open": False},
    ],
)
def test_contradictory_closure_summary_is_never_rendered_or_cached(
    tmp_path,
    monkeypatch,
    closures,
):
    payload = _summary()
    payload["closures"] = closures
    client = FakeHttpClient(payload)
    monkeypatch.setattr(vehicle_module, "get_http_client", lambda: client)
    plugin = _plugin(tmp_path)
    monkeypatch.setattr(
        plugin,
        "_render_summary",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("contradictory closure state rendered")),
    )

    image = plugin.generate_image({"cacheSeconds": 0}, DeviceConfig())

    assert read_source_provenance(image) is SourceProvenance.LOCAL_FALLBACK
    assert image.info["inkypi_skip_cache"] is True
    assert not plugin._cache_file(create=False).exists()


def test_null_measurement_value_is_rejected_and_never_cached(tmp_path, monkeypatch):
    payload = _summary()
    payload["battery"]["estimated_range"] = {"value": None, "unit": "mi"}
    client = FakeHttpClient(payload)
    monkeypatch.setattr(vehicle_module, "get_http_client", lambda: client)
    plugin = _plugin(tmp_path)

    image = plugin.generate_image({"cacheSeconds": 0}, DeviceConfig())

    assert read_source_provenance(image) is SourceProvenance.LOCAL_FALLBACK
    assert image.info["inkypi_skip_cache"] is True
    assert not plugin._cache_file(create=False).exists()


def test_manifest_and_settings_keep_secret_out_of_playlist_config():
    plugin_dir = Path(vehicle_module.__file__).parent
    manifest = json.loads((plugin_dir / "plugin-info.json").read_text(encoding="utf-8"))
    settings_html = (plugin_dir / "settings.html").read_text(encoding="utf-8")
    secret_schema = json.loads((plugin_dir.parents[1] / "config" / "secret_schema.json").read_text(encoding="utf-8"))

    assert manifest["schema_version"] == 2
    assert manifest["capabilities"] == {
        "supports_live_refresh": False,
        "supports_presentation_refresh": False,
        "supports_day_night_theme": True,
        "supports_cached_display_redraw": True,
    }
    assert manifest["refresh_on_display"] is False
    assert manifest["recommended_refresh"]["interval"] == 10800
    assert 'type="password"' not in settings_html
    assert "EPAPER_VEHICLE_BRIDGE_TOKEN" in settings_html
    assert "GOOGLE_MAPS_API_KEY" in settings_html
    assert "vehicle_location" in settings_html
    assert "never wakes the vehicle" in settings_html
    assert "requests location data" not in settings_html
    assert 'id="showClimate"' not in settings_html
    assert 'name="showClimate"' not in settings_html
    assert '<select id="language" name="language"' in settings_html
    assert '<option value="zh-CN">简体中文</option>' in settings_html
    assert '<option value="en">English</option>' in settings_html
    assert '<select id="temperatureUnit" name="temperatureUnit"' in settings_html
    assert '<option value="auto">Vehicle default</option>' in settings_html
    assert '<option value="C">Celsius</option>' in settings_html
    assert '<option value="F">Fahrenheit</option>' in settings_html
    assert '<select id="pressureUnit" name="pressureUnit"' in settings_html
    assert '<option value="psi">PSI</option>' in settings_html
    assert '<option value="bar">Bar</option>' in settings_html
    assert "pluginSettings.temperatureUnit || 'auto'" in settings_html
    assert "pluginSettings.pressureUnit || 'auto'" in settings_html
    assert "pluginSettings.language || 'zh-CN'" in settings_html
    google_entry = next(item for item in secret_schema["entries"] if item["canonical"] == "GOOGLE_MAPS_API_KEY")
    assert "Vehicle Status map and address" in google_entry["features"]


def test_vehicle_art_is_a_real_transparent_cutout_without_chroma_fringe():
    path = Path(vehicle_module.__file__).with_name("vehicle.png")

    image = Image.open(path).convert("RGBA")
    alpha = image.getchannel("A")
    flattened = getattr(image, "get_flattened_data", image.getdata)
    visible = [pixel for pixel in flattened() if pixel[3] > 32]

    # A low front three-quarter view of the owner's wrapped car, long side on.
    assert image.width > image.height * 1.4
    assert alpha.getextrema() == (0, 255)
    assert all(
        alpha.getpixel(point) == 0
        for point in ((0, 0), (image.width - 1, 0), (0, image.height - 1), (image.width - 1, image.height - 1))
    )
    assert len(visible) > image.width * image.height * 0.5
    assert not any(red < 50 and green > 235 and blue < 50 for red, green, blue, _alpha in visible)


def test_charging_car_art_is_the_same_cutout_plugged_in():
    plain = Image.open(Path(vehicle_module.__file__).with_name("vehicle.png")).convert("RGBA")
    charging = Image.open(Path(vehicle_module.__file__).with_name("vehicle_charging.png")).convert("RGBA")

    assert charging.getchannel("A").getextrema() == (0, 255)
    assert charging.width > charging.height * 1.4
    assert abs(charging.width / charging.height - plain.width / plain.height) < 0.35
    # The charge light and energy effects are the green the plain car does not have.
    def green(image):
        flattened = getattr(image, "get_flattened_data", image.getdata)
        return sum(1 for red, grn, blue, alpha in flattened() if alpha > 128 and grn > 160 and grn > red + 60)
    assert green(charging) > 200 and green(plain) < 50


@pytest.mark.parametrize(("state", "expected"), [
    ("Charging", "vehicle_charging.png"), ("Starting", "vehicle_charging.png"),
    ("Complete", "vehicle.png"), ("Disconnected", "vehicle.png"), (None, "vehicle.png"),
])
def test_the_car_art_shows_the_charging_scene_only_while_charging(monkeypatch, state, expected):
    opened = []

    def record(path, *args, **kwargs):
        opened.append(Path(path).name)
        return Image.new("RGBA", (40, 20), (0, 0, 0, 0))

    monkeypatch.setattr(vehicle_module, "safe_open_image", record)
    vehicle_module._load_vehicle_art(state)

    assert opened == [expected]


@pytest.mark.parametrize(
    ("language", "theme_mode", "ink", "muted", "surface"),
    [
        ("zh-CN", "day", (24, 28, 33), (91, 96, 102), (251, 249, 244)),
        ("en", "night", (240, 244, 247), (161, 174, 188), (22, 29, 38)),
    ],
)
@pytest.mark.parametrize("level", [78, 100])
def test_battery_percent_keeps_a_visible_gap_after_level(
    tmp_path,
    monkeypatch,
    language,
    theme_mode,
    ink,
    muted,
    surface,
    level,
):
    payload = _summary_v2()
    payload["battery"]["level_percent"] = level

    image = _render_payload(
        tmp_path,
        monkeypatch,
        payload,
        {
            "language": language,
            "_inkypi_theme": {"mode": theme_mode, "palette": {}},
        },
    )

    hero_box = (36, 148, 180, 190)  # below the section heading, left of the range figure
    number_bbox = _visible_color_bbox(image, hero_box, ink)
    percent_bbox = _visible_color_bbox(image, hero_box, muted)
    # Under the range figure, the charge-limit legend in ink explains the gauge's marker.
    legend_box = (180, 175, 320, 190)  # above the gauge marker, which starts at 191
    legend_bbox = _visible_color_bbox(image, legend_box, ink)
    bar_box = (36, 190, 320, 212)
    bar_bbox = _visible_non_background_bbox(image, bar_box, surface)

    assert number_bbox is not None
    assert percent_bbox is not None
    assert legend_bbox is not None
    assert bar_bbox is not None
    assert number_bbox[2] + 8 <= percent_bbox[0]
    assert abs(number_bbox[3] - percent_bbox[3]) <= 1
    bar_top = bar_box[1] + bar_bbox[1]
    assert bar_top - (hero_box[1] + number_bbox[3]) >= 3
    assert bar_top - (legend_box[1] + legend_bbox[3]) >= 1


def test_car_art_sits_between_the_gauge_and_charging_and_amid_the_tyres(tmp_path, monkeypatch):
    side_color, top_color = (255, 0, 255), (0, 255, 0)
    side = Image.new("RGBA", (231, 100), (*side_color, 255))
    top = Image.new("RGBA", (60, 120), (*top_color, 255))
    real_safe_open_image = vehicle_module.safe_open_image

    def load_sentinels(path, *args, **kwargs):
        name = Path(path).name
        if name in {"vehicle.png", "vehicle_charging.png"}:
            return side.copy()
        if name == "vehicle_top.png":
            return top.copy()
        return real_safe_open_image(path, *args, **kwargs)

    monkeypatch.setattr(vehicle_module, "safe_open_image", load_sentinels)
    image = _render_payload(tmp_path, monkeypatch, _summary_v2(),
                            {"language": "zh-CN", "_inkypi_theme": {"mode": "day", "palette": {}}})

    left, top_y, right, bottom = _visible_color_bbox(image, (0, 0, 800, 480), side_color)
    # Below the gauge's ruler, above the charging rule at y 306.
    assert 227 <= top_y and bottom <= vehicle_module.CHARGING_RULE_Y - 3
    assert right - left >= 160 and abs((left + right) / 2 - 177) <= 1
    left, top_y, right, bottom = _visible_color_bbox(image, (0, 0, 800, 480), top_color)
    assert (left, top_y, right, bottom) == (397, 172, 457, 292)


@pytest.mark.parametrize(
    ("display_name", "language", "theme_mode", "ink"),
    [
        ("Gray Bullet", "zh-CN", "day", (24, 28, 33)),
        ("Grey Bullet", "en", "night", (240, 244, 247)),
    ],
)
def test_grey_bullet_uses_a_prominent_theme_aware_wordmark(
    tmp_path,
    monkeypatch,
    display_name,
    language,
    theme_mode,
    ink,
):
    payload = _summary_v2()
    payload["vehicle"]["display_name"] = display_name

    image = _render_payload(
        tmp_path,
        monkeypatch,
        payload,
        {
            "language": language,
            "_inkypi_theme": {"mode": theme_mode, "palette": {}},
        },
    )
    visible = _visible_color_bbox(image, vehicle_module.VEHICLE_WORDMARK_BOX, ink)

    assert visible is not None
    assert visible[2] - visible[0] >= 120
    assert visible[3] - visible[1] >= 26


def test_grey_bullet_wordmark_failure_falls_back_to_dynamic_name(
    tmp_path,
    monkeypatch,
):
    payload = _summary_v2()
    payload["vehicle"]["display_name"] = "Gray Bullet"
    settings = {
        "language": "zh-CN",
        "_inkypi_theme": {"mode": "day", "palette": {}},
    }
    normal = _render_payload(tmp_path, monkeypatch, payload, settings)
    real_safe_open_image = vehicle_module.safe_open_image

    def fail_wordmark(path, *args, **kwargs):
        if Path(path).name == "grey_bullet_wordmark.png":
            raise OSError("synthetic wordmark decode failure")
        return real_safe_open_image(path, *args, **kwargs)

    monkeypatch.setattr(vehicle_module, "safe_open_image", fail_wordmark)
    fallback = _render_payload(tmp_path, monkeypatch, payload, settings)
    difference = ImageChops.difference(normal, fallback).getbbox()

    assert fallback.size == (800, 480)
    assert fallback.mode == "RGB"
    assert read_source_provenance(fallback) is SourceProvenance.LIVE
    assert difference is not None
    # The name and the model line after it change; nothing below the header does.
    assert 36 <= difference[0] < difference[2] <= 524
    assert 16 <= difference[1] < difference[3] <= 60


def test_other_vehicle_names_do_not_use_the_grey_bullet_wordmark(
    tmp_path,
    monkeypatch,
):
    payload = _summary_v2()
    payload["vehicle"]["display_name"] = "Night Runner"
    settings = {
        "language": "en",
        "_inkypi_theme": {"mode": "night", "palette": {}},
    }
    normal = _render_payload(tmp_path, monkeypatch, payload, settings)
    real_safe_open_image = vehicle_module.safe_open_image

    def fail_if_wordmark_is_requested(path, *args, **kwargs):
        if Path(path).name == "grey_bullet_wordmark.png":
            raise AssertionError("unrelated vehicle requested the personalized wordmark")
        return real_safe_open_image(path, *args, **kwargs)

    monkeypatch.setattr(
        vehicle_module,
        "safe_open_image",
        fail_if_wordmark_is_requested,
    )
    repeated = _render_payload(tmp_path, monkeypatch, payload, settings)

    assert ImageChops.difference(normal, repeated).getbbox() is None


def test_grey_bullet_wordmark_is_a_small_transparent_img2_asset():
    path = Path(vehicle_module.__file__).with_name("grey_bullet_wordmark.png")

    assert path.is_file()
    assert path.stat().st_size <= 64 * 1024
    with Image.open(path) as source:
        assert source.format == "PNG"
        image = source.convert("RGBA")

    assert image.size == (736, 172)
    alpha = image.getchannel("A")
    assert alpha.getextrema() == (0, 255)
    assert all(alpha.getpixel(point) == 0 for point in ((0, 0), (735, 0), (0, 171), (735, 171)))
    left, top, right, bottom = alpha.getbbox()
    assert 6 <= left < right <= 730
    assert 6 <= top < bottom <= 166
    assert right - left >= 700
    assert bottom - top >= 145
    flattened = getattr(image, "get_flattened_data", image.getdata)
    visible = [pixel for pixel in flattened() if pixel[3] > 32]
    assert visible
    assert not any(green > 180 and red < 100 and blue < 100 for red, green, blue, _alpha in visible)


def test_night_vehicle_keeps_a_visible_outline_after_epd7in3e_quantization(
    tmp_path,
    monkeypatch,
):
    dark_vehicle = Image.new("RGBA", (160, 60), (0, 0, 0, 0))
    dark_draw = ImageDraw.Draw(dark_vehicle)
    dark_draw.rounded_rectangle((8, 10, 152, 50), radius=14, fill=(0, 0, 0, 255))
    real_safe_open_image = vehicle_module.safe_open_image

    def load_dark_vehicle(path, *args, **kwargs):
        if Path(path).name in {"vehicle.png", "vehicle_charging.png"}:
            return dark_vehicle.copy()
        return real_safe_open_image(path, *args, **kwargs)

    monkeypatch.setattr(vehicle_module, "safe_open_image", load_dark_vehicle)
    image = _render_payload(
        tmp_path,
        monkeypatch,
        _summary_v2(),
        {
            "language": "zh-CN",
            "_inkypi_theme": {"mode": "night", "palette": {}},
        },
    )

    palette = Image.new("P", (1, 1))
    palette.putpalette(
        (
            0,
            0,
            0,
            255,
            255,
            255,
            255,
            255,
            0,
            255,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            255,
            0,
            255,
            0,
        )
        + (0, 0, 0) * 249
    )
    panel_image = image.quantize(
        palette=palette,
        dither=Image.Dither.NONE,
    ).convert("RGB")
    vehicle_box = (246, 26, 376, 92)
    visible = _visible_non_background_bbox(panel_image, vehicle_box, (0, 0, 0))

    assert visible is not None
    assert visible[2] - visible[0] >= 122
    assert visible[3] - visible[1] >= 34


_DASHBOARD_ICON_ASSETS = (
    "energy.png",
    "security.png",
    "climate.png",
    "vehicle_info.png",
    "tires.png",
    "freshness.png",
)


@pytest.mark.parametrize("filename", _DASHBOARD_ICON_ASSETS)
def test_dashboard_icon_assets_are_small_binary_alpha_img2_glyphs(filename):
    path = Path(vehicle_module.__file__).with_name("dashboard_icons") / filename

    assert path.is_file()
    assert path.stat().st_size <= 64 * 1024
    with Image.open(path) as source:
        assert source.format == "PNG"
        image = source.convert("RGBA")

    assert image.size == (64, 64)
    alpha = image.getchannel("A")
    flattened_alpha = getattr(alpha, "get_flattened_data", alpha.getdata)
    alpha_values = set(flattened_alpha())
    assert alpha_values == {0, 255}
    assert all(alpha.getpixel(point) == 0 for point in ((0, 0), (63, 0), (0, 63), (63, 63)))
    left, top, right, bottom = alpha.getbbox()
    assert 4 <= left < right <= 60
    assert 4 <= top < bottom <= 60
    visible_width = right - left
    visible_height = bottom - top
    assert max(visible_width, visible_height) >= 48
    assert min(visible_width, visible_height) >= 24

    flattened = getattr(image, "get_flattened_data", image.getdata)
    visible = [pixel for pixel in flattened() if pixel[3] > 0]
    assert len(visible) >= 450
    assert not any(red > 180 and green < 80 and blue > 170 for red, green, blue, _alpha in visible)


def test_dashboard_icon_assets_are_visually_distinct():
    root = Path(vehicle_module.__file__).with_name("dashboard_icons")
    digests = set()
    for filename in _DASHBOARD_ICON_ASSETS:
        with Image.open(root / filename) as source:
            alpha = source.convert("RGBA").getchannel("A")
        digests.add(hashlib.sha256(alpha.tobytes()).hexdigest())

    assert len(digests) == len(_DASHBOARD_ICON_ASSETS)


@pytest.mark.parametrize(
    ("language", "theme_mode", "panel", "surface"),
    [
        ("zh-CN", "day", (242, 237, 228), (251, 249, 244)),
        ("zh-CN", "night", (11, 16, 23), (22, 29, 38)),
        ("en", "day", (242, 237, 228), (251, 249, 244)),
        ("en", "night", (11, 16, 23), (22, 29, 38)),
    ],
)
def test_dashboard_icons_are_static_scan_anchors_with_clean_title_gaps(
    tmp_path,
    monkeypatch,
    language,
    theme_mode,
    panel,
    surface,
):
    settings = {
        "language": language,
        "_inkypi_theme": {"mode": theme_mode, "palette": {}},
    }
    full = _render_payload(tmp_path, monkeypatch, _summary_v2(), settings)
    empty = _render_payload(tmp_path, monkeypatch, _null_v2_summary(), settings)
    # Battery, body, climate, location, charging and the vehicle strip: their places hold whatever
    # the data says, so each section is found in the same spot every time.
    icon_boxes = tuple(
        ((left, top, left + 20, top + 20), (left + 20, top, left + 26, top + 20))
        for left, top in ((36, 127), (338, 127), (538, 127), (538, 209), (36, 311), (36, 406))
    )

    for icon_box, gap_box in icon_boxes:
        full_icon = full.crop(icon_box)
        empty_icon = empty.crop(icon_box)
        visible = _visible_non_background_bbox(full, icon_box, surface)
        assert visible is not None
        visible_width = visible[2] - visible[0]
        visible_height = visible[3] - visible[1]
        assert max(visible_width, visible_height) >= 16
        assert min(visible_width, visible_height) >= 10
        assert ImageChops.difference(full_icon, empty_icon).getbbox() is None
        gap = full.crop(gap_box)
        gap_pixels = getattr(gap, "get_flattened_data", gap.getdata)
        assert set(gap_pixels()) == {surface}

    freshness_box = (36, 439, 52, 455)
    freshness_gap_box = (52, 439, 58, 455)
    freshness_visible = _visible_non_background_bbox(full, freshness_box, surface)
    assert freshness_visible is not None
    assert freshness_visible[2] - freshness_visible[0] >= 12
    assert freshness_visible[3] - freshness_visible[1] >= 12
    assert (
        ImageChops.difference(
            full.crop(freshness_box),
            empty.crop(freshness_box),
        ).getbbox()
        is None
    )
    freshness_gap = full.crop(freshness_gap_box)
    freshness_gap_pixels = getattr(
        freshness_gap,
        "get_flattened_data",
        freshness_gap.getdata,
    )
    assert set(freshness_gap_pixels()) == {surface}


def test_one_broken_dashboard_icon_falls_back_without_losing_live_page(
    tmp_path,
    monkeypatch,
):
    real_safe_open_image = vehicle_module.safe_open_image
    settings = {
        "language": "zh-CN",
        "_inkypi_theme": {"mode": "day", "palette": {}},
    }
    normal = _render_payload(
        tmp_path,
        monkeypatch,
        _summary_v2(),
        settings,
    )

    def fail_selected_icons(path, *args, **kwargs):
        if Path(path).name in {"security.png", "freshness.png"}:
            raise OSError("synthetic icon decode failure")
        return real_safe_open_image(path, *args, **kwargs)

    monkeypatch.setattr(vehicle_module, "safe_open_image", fail_selected_icons)
    degraded = _render_payload(
        tmp_path,
        monkeypatch,
        _summary_v2(),
        settings,
    )

    def fail_all_dashboard_icons(path, *args, **kwargs):
        if Path(path).parent.name == "dashboard_icons":
            raise OSError("synthetic icon decode failure")
        return real_safe_open_image(path, *args, **kwargs)

    monkeypatch.setattr(vehicle_module, "safe_open_image", fail_all_dashboard_icons)
    text_only = _render_payload(
        tmp_path,
        monkeypatch,
        _summary_v2(),
        settings,
    )

    assert degraded.mode == "RGB"
    assert degraded.size == (800, 480)
    assert read_source_provenance(degraded) is SourceProvenance.LIVE
    for box in ((338, 126, 460, 150), (36, 436, 220, 458)):
        assert (
            ImageChops.difference(
                degraded.crop(box),
                text_only.crop(box),
            ).getbbox()
            is None
        )
        assert (
            ImageChops.difference(
                normal.crop(box),
                degraded.crop(box),
            ).getbbox()
            is not None
        )


def test_security_card_lists_open_items_on_one_line_above_the_port_status():
    # Two detail rows used to reach y=234 and run into the charge-port line at y=232.
    draw = ImageDraw.Draw(Image.new("RGB", (400, 100), "white"))
    font = vehicle_module._font(11, True)
    closures = {"all_closed": False, "open": ["driver_rear_window"], "charge_port_open": True}
    items = vehicle_module._closure_detail_lines(closures, "zh-CN")

    assert len(items) == 2
    assert vehicle_module._closure_detail_line(draw, items, font, 166, "zh-CN") == " / ".join(items)

    crowded = vehicle_module._closure_detail_lines(
        {"all_closed": False, "open": sorted(vehicle_module._OPEN_CLOSURES), "charge_port_open": True},
        "en",
    )
    line = vehicle_module._closure_detail_line(draw, crowded, font, 166, "en")
    assert draw.textbbox((0, 0), line, font=font)[2] <= 166
    head, _, count = line.partition(" / ")
    assert crowded[0].startswith(head.removesuffix("...").rstrip())
    assert count == f"+{len(crowded) - 1} MORE"


@pytest.mark.parametrize(("level", "role"), [(78, "battery_high"), (40, "battery_mid"), (12, "battery_low")])
def test_battery_gauge_uses_battery_colours_and_marks_the_limit(level, role):
    colors = vehicle_module._render_colors({"mode": "day"})
    image = Image.new("RGB", (300, 40), colors["surface"])
    draw = ImageDraw.Draw(image)

    vehicle_module._battery_gauge(draw, (10, 12, 290, 24), level, 80, colors)

    filled = image.getpixel((14, 18))
    assert filled == colors[role]
    # The charge limit is an ink line through the bar at 80%; the ruler sits below it.
    assert image.getpixel((10 + round(280 * 0.8), 18)) == colors["ink"]
    assert _visible_color_bbox(image, (0, 25, 300, 40), colors["muted"]) is not None


def _all_clear_v3():
    """The owner's car parked, locked and shut, every tyre even, nothing to install."""
    payload = _summary_v3()
    payload["vehicle"].update(sentry_mode="off", right_hand_drive=False)
    payload["charging"].update(state="Disconnected", power_kw=0, preconditioning=False,
                               scheduled={"pending": False, "mode": None})
    payload["climate"].update(keeper_mode="off", is_climate_on=False, inside_temp_c=21)
    payload["closures"].update(all_closed=True, open=[], charge_port_open=False)
    payload["closures"]["doors"]["rear_trunk"] = False
    payload["tires"]["pressures"] = {position: {"value": 2.9, "unit": "bar"} for position in payload["tires"]["pressures"]}
    payload["tires"]["soft_warnings"] = dict.fromkeys(payload["tires"]["soft_warnings"], False)
    payload["tires"]["hard_warnings"] = dict.fromkeys(payload["tires"]["hard_warnings"], False)
    payload["software_update"] = dict.fromkeys(payload["software_update"])
    return payload


def _lamps(payload, language="zh-CN"):
    summary = vehicle_module.sanitize_summary(payload)
    return {
        lamp.key: lamp
        for lamp in vehicle_module._status_lamps(summary, summary["charging"], summary["tires"],
                                                 summary["software_update"], True, {}, summary["preferences"],
                                                 language)
    }


def test_status_lamps_stay_quiet_when_the_car_is_fine():
    lamps = _lamps(_all_clear_v3())

    assert tuple(lamps) == LAMP_KEYS
    assert {key: lamp.level for key, lamp in lamps.items()} == dict.fromkeys(LAMP_KEYS, "idle")
    assert [lamps[key].text for key in ("link", "lock", "openings", "tyres", "update")] == [
        "在线", "已锁车", "门窗关闭", "胎压正常", "无更新"]
    assert vehicle_module._attention_lamps(tuple(lamps.values())) == []


def test_status_lamps_light_each_problem_worst_first():
    payload = _all_clear_v3()
    payload["snapshot"].update(freshness="stale_cache", vehicle_connectivity="offline", age_seconds=14 * 3_600)
    payload["vehicle"]["locked"] = False
    payload["closures"].update(all_closed=False, charge_port_open=True)
    payload["tires"]["pressures"]["rear_left"] = {"value": 2.2, "unit": "bar"}
    payload["tires"]["soft_warnings"]["rear_left"] = True

    lamps = _lamps(payload)

    assert (lamps["link"].level, lamps["link"].text) == ("warn", "离线 14小时")
    assert (lamps["lock"].level, lamps["lock"].icon, lamps["lock"].text) == ("crit", "unlocked", "未锁车")
    assert (lamps["openings"].level, lamps["openings"].icon, lamps["openings"].text) == (
        "warn", "charge_port", "充电口未关")
    assert (lamps["tyres"].level, lamps["tyres"].text) == ("warn", "左后偏低")
    attention = vehicle_module._attention_lamps(tuple(lamps.values()))
    assert [lamp.key for lamp in attention] == ["lock", "link", "openings", "tyres"]
    english = _lamps(payload, "en")
    assert [english[key].text for key in ("link", "lock", "openings", "tyres")] == [
        "OFFLINE 14H", "UNLOCKED", "PORT OPEN", "RL LOW"]
    assert all(lamp.text.isascii() for lamp in english.values())


def test_open_charge_port_is_a_problem_only_without_a_cable():
    unplugged = _all_clear_v3()
    unplugged["closures"].update(all_closed=False, charge_port_open=True)
    plugged = deepcopy(unplugged)
    plugged["charging"]["state"] = "Charging"

    assert _lamps(unplugged)["openings"].level == "warn"
    assert _lamps(plugged)["openings"].level == "idle"
    assert _lamps(plugged)["charging"].level == "active"


@pytest.mark.parametrize(("doors", "windows", "open_items", "icon", "text"), [
    ({"driver_front": True}, {}, ["driver_front_door"], "door_open", "左前门未关"),
    ({"rear_trunk": True}, {}, ["rear_trunk"], "trunk_open", "后备箱未关"),
    ({"front_trunk": True}, {}, ["front_trunk"], "frunk_open", "前备箱未关"),
    ({}, {"passenger_rear": True}, ["passenger_rear_window"], "window_open", "右后窗未关"),
    ({"driver_front": True, "rear_trunk": True}, {}, ["driver_front_door", "rear_trunk"], "door_open", "2 处未关"),
])
def test_any_open_door_window_or_trunk_lights_the_openings_lamp_red(doors, windows, open_items, icon, text):
    payload = _all_clear_v3()
    payload["closures"]["doors"].update(doors)
    payload["closures"]["windows"].update(windows)
    payload["closures"].update(all_closed=False, open=open_items)

    lamp = _lamps(payload)["openings"]

    assert (lamp.level, lamp.icon, lamp.text) == ("crit", icon, text)


def test_a_right_hand_drive_cars_driver_door_is_on_the_right():
    payload = _all_clear_v3()
    payload["vehicle"]["right_hand_drive"] = True
    payload["closures"]["doors"]["driver_front"] = True
    payload["closures"].update(all_closed=False, open=["driver_front_door"])

    assert _lamps(payload)["openings"].text == "右前门未关"


@pytest.mark.parametrize(("pressures", "hard", "soft", "level", "text"), [
    ((2.9, 2.9, 2.9, 2.9), (), (), "idle", "胎压正常"),
    ((2.9, 2.9, 2.6, 2.9), (), (), "warn", "胎压不均"),
    ((2.9, 2.9, 2.9, 2.9), ("rear_right",), (), "crit", "右后告警"),
    ((2.9, 2.9, 2.2, 2.1), (), ("rear_left", "rear_right"), "warn", "2 轮偏低"),
])
def test_tyre_lamp_reads_teslas_warnings_and_uneven_axles(pressures, hard, soft, level, text):
    payload = _all_clear_v3()
    positions = ("front_left", "front_right", "rear_left", "rear_right")
    payload["tires"]["pressures"] = {
        position: {"value": value, "unit": "bar"} for position, value in zip(positions, pressures)
    }
    for position in hard:
        payload["tires"]["hard_warnings"][position] = True
    for position in soft:
        payload["tires"]["soft_warnings"][position] = True

    lamp = _lamps(payload)["tyres"]

    assert (lamp.level, lamp.text) == (level, text)


def test_low_battery_failed_charge_and_pending_update_lamps():
    payload = _all_clear_v3()
    payload["battery"]["level_percent"] = 8
    payload["charging"].update(state="NoPower")
    payload["software_update"].update(version="2026.44.1", download_percent=54, install_percent=0)

    lamps = _lamps(payload)

    assert (lamps["battery"].level, lamps["battery"].text) == ("crit", "电量 8%")
    assert (lamps["charging"].level, lamps["charging"].text) == ("crit", "无电源")
    assert (lamps["update"].level, lamps["update"].text) == ("info", "下载 54%")
    assert vehicle_module._enum_text("en", "charging", "NoPower") == "NO POWER"


@pytest.mark.parametrize("theme_mode", ["day", "night"])
def test_verdict_pill_is_green_when_clear_and_takes_the_worst_lamps_colour(tmp_path, monkeypatch, theme_mode):
    settings = {"language": "zh-CN", "_inkypi_theme": {"mode": theme_mode, "palette": {}}}
    colors = vehicle_module._render_colors({"mode": theme_mode})
    clear = _render_payload(tmp_path / "clear", monkeypatch, _all_clear_v3(), settings)
    unlocked_payload = _all_clear_v3()
    unlocked_payload["vehicle"]["locked"] = False
    unlocked = _render_payload(tmp_path / "unlocked", monkeypatch, unlocked_payload, settings)

    assert _visible_color_bbox(clear, VERDICT_REGION, colors["alert_active"]) is not None
    assert _visible_color_bbox(unlocked, VERDICT_REGION, colors["alert_crit"]) is not None
    # The lock lamp lights in the same red; quiet lamps keep their grey tiles.
    assert _visible_color_bbox(unlocked, _lamp_box("lock"), colors["alert_crit"]) is not None
    assert _visible_color_bbox(clear, _lamp_box("lock"), colors["alert_crit"]) is None
    assert _visible_color_bbox(clear, _lamp_box("lock"), colors["lamp_idle"]) is not None


def test_body_map_swings_an_open_door_out_on_its_own_side(tmp_path, monkeypatch):
    settings = {"language": "zh-CN", "_inkypi_theme": {"mode": "day", "palette": {}}}
    red = vehicle_module._render_colors({"mode": "day"})["alert_crit"]
    shut = _render_payload(tmp_path / "shut", monkeypatch, _all_clear_v3(), settings)
    driver = _all_clear_v3()
    driver["closures"]["doors"]["driver_front"] = True
    driver["closures"].update(all_closed=False, open=["driver_front_door"])
    driver_open = _render_payload(tmp_path / "driver", monkeypatch, driver, settings)
    passenger = _all_clear_v3()
    passenger["closures"]["doors"]["passenger_front"] = True
    passenger["closures"].update(all_closed=False, open=["passenger_front_door"])
    passenger_open = _render_payload(tmp_path / "passenger", monkeypatch, passenger, settings)

    car_middle = (vehicle_module.COLUMNS[1][0] + vehicle_module.COLUMNS[1][1]) // 2
    left_half = (vehicle_module.COLUMNS[1][0], 200, car_middle, 260)
    right_half = (car_middle, 200, vehicle_module.COLUMNS[1][1], 260)
    assert _visible_color_bbox(shut, BODY_COLUMN_REGION, red) is None
    assert _visible_color_bbox(driver_open, left_half, red) is not None
    assert _visible_color_bbox(driver_open, right_half, red) is None
    assert _visible_color_bbox(passenger_open, right_half, red) is not None
    assert _visible_color_bbox(passenger_open, left_half, red) is None


def test_lamp_row_fits_long_english_states_with_the_portable_font(tmp_path, monkeypatch):
    _require_portable_ascii_status_font(monkeypatch)
    payload = _all_clear_v3()
    payload["snapshot"].update(vehicle_connectivity="unavailable", age_seconds=23 * 3_600)
    payload["charging"]["state"] = "Disconnected"

    image = _render_payload(tmp_path, monkeypatch, payload, {"language": "en"})

    assert image.size == (800, 480)
    lamps = _lamps(payload, "en")
    draw = ImageDraw.Draw(Image.new("RGB", (10, 10)))
    width = (vehicle_module.PAGE_RIGHT - vehicle_module.PAGE_LEFT) / len(LAMP_KEYS) - 6
    assert all(draw.textbbox((0, 0), lamps[key].text, font=vehicle_module._font(10, True))[2] <= width
               for key in ("link", "charging", "modes"))


# Latin that may stay in Chinese copy: the model's name, units, and names of things to type.
_ZH_ALLOWED_LATIN = {"Model", "Y", "API", "https", "C", "F"}


def test_simplified_chinese_copy_has_no_english_words():
    for key, text in vehicle_module._UI_TEXT["zh-CN"].items():
        words = set(re.findall(r"[A-Za-z]+", re.sub(r"\{[a-z_]+\}", "", text)))
        assert words <= _ZH_ALLOWED_LATIN, (key, text)
    for category, values in vehicle_module._ENUM_TEXT["zh-CN"].items():
        for key, text in values.items():
            assert set(re.findall(r"[A-Za-z]+", text)) <= _ZH_ALLOWED_LATIN, (category, key, text)


def test_tesla_codes_reach_the_chinese_page_as_chinese():
    vehicle = {"exterior_color": "StealthGrey", "roof_color": "RoofColorGlass", "wheel_type": "Arachnid21Grey"}
    climate = vehicle_module.sanitize_summary(_summary_v2())["climate"]
    climate["cabin_overheat"] = {"mode": "FanOnly", "temp_limit": "High"}
    charging = vehicle_module.sanitize_summary(_summary_v2())["charging"]
    charging.update(state="Disconnected", cable_type="GB_AC")

    assert vehicle_module._vehicle_appearance_text(vehicle, "zh-CN") == "星空灰/玻璃顶/21寸"
    assert vehicle_module._vehicle_appearance_text(vehicle, "en") == "STEALTHGREY/ROOFCOLORGLASS/ARACHNID21GREY"
    assert dict(vehicle_module._climate_cells(climate, True, {}, {}, "zh-CN"))["过热保护"] == "仅风扇/高"
    cells = dict(vehicle_module._charging_cells({"estimated_range": None}, charging, True, {}, {}, "zh-CN"))
    assert cells["接口"] == "国标交流"
    assert vehicle_module._enum_text("zh-CN", "provider", "LongRange") == "长续航版"
    assert vehicle_module._t("zh-CN", "pressure_unit_label", unit=vehicle_module._t("zh-CN", "unit_psi")) == "单位 磅"
    assert vehicle_module._pressure_text({"value": 2.62, "unit": "bar"}, {"pressureUnit": "psi"}, {}, "zh-CN") == "38 磅"


_ALERT_ICON_ASSETS = tuple(sorted(vehicle_module.ALERT_ICON_NAMES))


@pytest.mark.parametrize("name", _ALERT_ICON_ASSETS)
def test_alert_icons_are_small_generated_alpha_glyphs(name):
    path = Path(vehicle_module.__file__).with_name("alert_icons") / f"{name}.png"

    with Image.open(path) as source:
        assert source.format == "PNG"
        image = source.convert("RGBA")

    assert path.stat().st_size <= 8 * 1024
    assert image.size == (64, 64)
    alpha = image.getchannel("A")
    assert all(alpha.getpixel(point) == 0 for point in ((0, 0), (63, 0), (0, 63), (63, 63)))
    left, top, right, bottom = alpha.getbbox()
    assert max(right - left, bottom - top) >= 46
    assert 6 <= left and right <= 58 and 6 <= top and bottom <= 58


def test_alert_icons_are_visually_distinct_and_each_lamp_has_one():
    root = Path(vehicle_module.__file__).with_name("alert_icons")
    digests = set()
    for name in _ALERT_ICON_ASSETS:
        with Image.open(root / f"{name}.png") as source:
            digests.add(hashlib.sha256(source.convert("RGBA").getchannel("A").tobytes()).hexdigest())

    assert len(digests) == len(_ALERT_ICON_ASSETS)
    assert {lamp.icon for lamp in _lamps(_all_clear_v3()).values()} <= vehicle_module.ALERT_ICON_NAMES
    assert (root / "SOURCES.md").is_file()


def test_a_broken_alert_icon_leaves_its_lamp_tile_without_failing_the_page(tmp_path, monkeypatch):
    real_safe_open_image = vehicle_module.safe_open_image

    def fail_alert_icons(path, *args, **kwargs):
        if Path(path).parent.name == "alert_icons":
            raise OSError("synthetic icon decode failure")
        return real_safe_open_image(path, *args, **kwargs)

    monkeypatch.setattr(vehicle_module, "safe_open_image", fail_alert_icons)
    image = _render_payload(tmp_path, monkeypatch, _summary_v2(), {"language": "zh-CN"})

    assert read_source_provenance(image) is SourceProvenance.LIVE
    assert image.size == (800, 480)
