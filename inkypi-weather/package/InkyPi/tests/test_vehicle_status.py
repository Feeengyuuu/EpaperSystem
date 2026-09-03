from copy import deepcopy
from datetime import datetime
from io import BytesIO
import hashlib
import json
import os
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
    image = Image.new("RGB", (208, 58), color)
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
    assert device.loaded == ["EPAPER_VEHICLE_BRIDGE_TOKEN"]
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
    assert map_query["size"] == ["208x58"]
    assert map_query["scale"] == ["1"]
    assert map_query["key"] == ["google-maps-test-secret"]
    assert map_query["markers"] == ["color:red|37.501235,-122.001235"]
    assert map_call[3]["allow_redirects"] is False
    assert map_call[3]["max_bytes"] == 128 * 1024

    assert _visible_color_bbox(image, (0, 0, 800, 480), (255, 0, 255)) == (
        394,
        18,
        602,
        76,
    )

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
        394,
        18,
        602,
        76,
    )


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
    assert device.loaded == ["EPAPER_VEHICLE_BRIDGE_TOKEN"]
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


@pytest.mark.parametrize(
    ("freshness", "age_seconds"),
    [
        ("fresh_cache", 3_600),
        ("stale_cache", 86_400),
    ],
)
def test_english_freshness_status_never_overwrites_location_map(
    tmp_path,
    monkeypatch,
    freshness,
    age_seconds,
):
    now = datetime.fromisoformat("2026-08-08T20:00:00+00:00").timestamp()
    monkeypatch.setattr(vehicle_module.time, "time", lambda: now)
    payload = _summary_v3(freshness=freshness)
    payload["snapshot"]["age_seconds"] = age_seconds
    map_color = (255, 0, 255)
    client = LocationHttpClient(payload, _map_png(map_color))
    monkeypatch.setattr(vehicle_module, "get_http_client", lambda: client)

    image = _plugin(tmp_path).generate_image(
        {"cacheSeconds": 0, "language": "en"},
        DeviceConfig(env={"GOOGLE_MAPS_API_KEY": "google-maps-test-secret"}),
    )

    map_box = (394, 18, 602, 76)
    expected_map = Image.new("RGB", (208, 58), map_color)
    assert ImageChops.difference(image.crop(map_box), expected_map).getbbox() is None


def test_header_identity_vehicle_map_and_status_have_even_visual_gaps(
    tmp_path,
    monkeypatch,
):
    vehicle_color = (255, 0, 255)
    map_color = (0, 255, 0)
    vehicle = Image.new("RGBA", (231, 100), (*vehicle_color, 255))
    wordmark = Image.new("RGBA", (736, 172), (0, 0, 0, 255))
    real_safe_open_image = vehicle_module.safe_open_image

    def load_header_sentinels(path, *args, **kwargs):
        name = Path(path).name if isinstance(path, (str, os.PathLike)) else ""
        if name == "vehicle.png":
            return vehicle.copy()
        if name == "grey_bullet_wordmark.png":
            return wordmark.copy()
        return real_safe_open_image(path, *args, **kwargs)

    monkeypatch.setattr(vehicle_module, "safe_open_image", load_header_sentinels)
    now = datetime.fromisoformat("2026-08-08T20:00:00+00:00").timestamp()
    monkeypatch.setattr(vehicle_module.time, "time", lambda: now)
    client = LocationHttpClient(_summary_v3(), _map_png(map_color))
    monkeypatch.setattr(vehicle_module, "get_http_client", lambda: client)

    image = _plugin(tmp_path).generate_image(
        {
            "cacheSeconds": 0,
            "language": "en",
            "_inkypi_theme": {"mode": "day", "palette": {}},
        },
        DeviceConfig(env={"GOOGLE_MAPS_API_KEY": "google-maps-test-secret"}),
    )

    identity_crop = (40, 18, 230, 62)
    identity = _visible_color_bbox(image, identity_crop, (24, 28, 33))
    vehicle_bbox = _visible_color_bbox(image, (0, 0, 800, 100), vehicle_color)
    map_bbox = _visible_color_bbox(image, (0, 0, 800, 100), map_color)
    status_crop = (600, 20, 761, 60)
    status = _visible_color_bbox(image, status_crop, (35, 116, 79))

    assert identity is not None
    assert vehicle_bbox is not None
    assert map_bbox is not None
    assert status is not None
    identity_right = identity_crop[0] + identity[2]
    status_left = status_crop[0] + status[0]
    gaps = (
        vehicle_bbox[0] - identity_right,
        map_bbox[0] - vehicle_bbox[2],
        status_left - map_bbox[2],
    )
    assert gaps == (18, 18, 18)


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
        (40, 112, 301, 417),
        (334, 112, 533, 255),
        (564, 112, 761, 255),
        (334, 278, 533, 417),
        (564, 278, 761, 417),
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
    for gutter in (
        (305, 112, 330, 417),
        (536, 112, 560, 417),
        (334, 258, 761, 275),
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
    assert difference.crop((334, 112, 533, 255)).getbbox() is not None
    assert difference.crop((40, 112, 301, 417)).getbbox() is None
    assert difference.crop((564, 112, 761, 417)).getbbox() is None


def test_v2_rr_hard_warning_changes_only_tire_region(tmp_path, monkeypatch):
    safe = _summary_v2()
    safe["tires"]["soft_warnings"] = {position: False for position in safe["tires"]["soft_warnings"]}
    safe["tires"]["hard_warnings"] = {position: False for position in safe["tires"]["hard_warnings"]}
    warning = deepcopy(safe)
    warning["tires"]["hard_warnings"]["rear_right"] = True

    safe_image = _render_payload(tmp_path / "safe", monkeypatch, safe)
    warning_image = _render_payload(tmp_path / "warning", monkeypatch, warning)

    _assert_change_is_inside(safe_image, warning_image, (564, 278, 761, 417))


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

    _assert_change_is_inside(disconnected_image, charging_image, (40, 112, 301, 417))


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
    difference = ImageChops.difference(live_image, cached_image)
    assert difference.crop((620, 60, 761, 96)).getbbox() is not None
    assert difference.crop((40, 430, 330, 457)).getbbox() is not None
    assert difference.crop((40, 112, 761, 417)).getbbox() is None


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
    difference = ImageChops.difference(cached_image, stale_image)
    assert difference.crop((620, 60, 761, 96)).getbbox() is not None
    assert difference.crop((40, 430, 330, 457)).getbbox() is not None
    assert difference.crop((40, 112, 761, 417)).getbbox() is None


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
    _assert_change_is_inside(baseline_image, energy_image, (40, 112, 301, 417))

    security = deepcopy(baseline)
    security["closures"] = {
        "all_closed": False,
        "open": ["driver_front_door", "rear_trunk"],
        "charge_port_open": False,
    }
    security_image = _render_payload(tmp_path, monkeypatch, security)
    _assert_change_is_inside(baseline_image, security_image, (334, 112, 532, 254))

    climate = deepcopy(baseline)
    climate["climate"]["is_climate_on"] = True
    climate_image = _render_payload(tmp_path, monkeypatch, climate)
    _assert_change_is_inside(baseline_image, climate_image, (564, 112, 761, 254))

    vehicle = deepcopy(baseline)
    vehicle["vehicle"].update(
        {
            "software_version": "2026.26.3",
            "odometer": {"value": 54_321.9, "unit": "mi"},
        }
    )
    vehicle_image = _render_payload(tmp_path, monkeypatch, vehicle)
    _assert_change_is_inside(baseline_image, vehicle_image, (334, 278, 761, 417))


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


def test_worker_stale_snapshot_stays_stale_and_non_cacheable(tmp_path, monkeypatch):
    client = FakeHttpClient(_summary(freshness="stale_cache", connectivity="asleep"))
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
    replacement = _summary(freshness="stale_cache", connectivity="asleep")
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

    assert image.width > image.height * 2
    assert alpha.getextrema() == (0, 255)
    assert all(
        alpha.getpixel(point) == 0
        for point in ((0, 0), (image.width - 1, 0), (0, image.height - 1), (image.width - 1, image.height - 1))
    )
    assert len(visible) > image.width * image.height * 0.5
    assert not any(red < 50 and green > 235 and blue < 50 for red, green, blue, _alpha in visible)


@pytest.mark.parametrize(
    ("language", "theme_mode", "ink", "muted", "panel"),
    [
        ("zh-CN", "day", (24, 28, 33), (91, 96, 102), (242, 237, 228)),
        ("en", "night", (240, 244, 247), (161, 174, 188), (11, 16, 23)),
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
    panel,
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

    hero_box = (50, 145, 190, 211)
    number_bbox = _visible_color_bbox(image, hero_box, ink)
    percent_bbox = _visible_color_bbox(image, hero_box, muted)
    usable_box = (50, 208, 190, 228)
    usable_bbox = _visible_color_bbox(image, usable_box, muted)
    bar_box = (50, 224, 290, 250)
    bar_bbox = _visible_non_background_bbox(image, bar_box, panel)

    assert number_bbox is not None
    assert percent_bbox is not None
    assert usable_bbox is not None
    assert bar_bbox is not None
    assert number_bbox[2] + 8 <= percent_bbox[0]
    assert abs(number_bbox[3] - percent_bbox[3]) <= 1
    number_bottom = hero_box[1] + number_bbox[3]
    usable_top = usable_box[1] + usable_bbox[1]
    usable_bottom = usable_box[1] + usable_bbox[3]
    bar_top = bar_box[1] + bar_bbox[1]
    assert usable_top - number_bottom >= 3
    assert bar_top - usable_bottom >= 3


@pytest.mark.parametrize(
    ("language", "theme_mode", "surface"),
    [
        ("zh-CN", "day", (251, 249, 244)),
        ("zh-CN", "night", (22, 29, 38)),
        ("en", "day", (251, 249, 244)),
        ("en", "night", (22, 29, 38)),
    ],
)
def test_header_vehicle_is_centered_between_identity_and_location_map(
    tmp_path,
    monkeypatch,
    language,
    theme_mode,
    surface,
):
    payload = _summary_v2()
    payload["vehicle"].update(
        {
            "display_name": "W" * 64,
            "model": "W" * 40,
            "trim": "W" * 40,
        }
    )

    image = _render_payload(
        tmp_path,
        monkeypatch,
        payload,
        {
            "language": language,
            "_inkypi_theme": {"mode": theme_mode, "palette": {}},
        },
    )

    identity_header = (40, 18, 246, 92)
    identity_bbox = _visible_non_background_bbox(image, identity_header, surface)
    middle_header = (246, 26, 376, 92)
    visible_bbox = _visible_non_background_bbox(image, middle_header, surface)

    assert identity_bbox is not None
    assert visible_bbox is not None
    identity_right = identity_header[0] + identity_bbox[2]
    left = middle_header[0] + visible_bbox[0]
    top = middle_header[1] + visible_bbox[1]
    right = middle_header[0] + visible_bbox[2]
    bottom = middle_header[1] + visible_bbox[3]
    assert 246 <= left < right <= 376
    assert 26 <= top < bottom <= 92
    assert right - left >= 130
    assert bottom - top >= 55
    assert abs(((left + right) / 2) - 311) <= 1
    assert left - identity_right >= 18


def test_header_vehicle_is_prominent_centered_and_clear_of_neighbors(
    tmp_path,
    monkeypatch,
):
    sentinel_color = (255, 0, 255)
    sentinel = Image.new("RGBA", (231, 100), (*sentinel_color, 255))
    real_safe_open_image = vehicle_module.safe_open_image

    def load_sentinel_vehicle(path, *args, **kwargs):
        if Path(path).name == "vehicle.png":
            return sentinel.copy()
        return real_safe_open_image(path, *args, **kwargs)

    monkeypatch.setattr(
        vehicle_module,
        "safe_open_image",
        load_sentinel_vehicle,
    )

    image = _render_payload(
        tmp_path,
        monkeypatch,
        _summary_v2(),
        {
            "language": "zh-CN",
            "_inkypi_theme": {"mode": "day", "palette": {}},
        },
    )
    bbox = _visible_color_bbox(image, (0, 0, 800, 480), sentinel_color)

    assert bbox is not None
    left, top, right, bottom = bbox
    width = right - left
    height = bottom - top

    assert 128 <= width <= 130
    assert 55 <= height <= 57
    assert 2.28 <= width / height <= 2.34
    assert abs(((left + right) / 2) - 311) <= 1
    assert left >= 246
    assert right <= 376
    assert top >= 26
    assert bottom <= 92


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
    name_box = (40, 18, 230, 62)
    visible = _visible_color_bbox(image, name_box, ink)

    assert visible is not None
    width = visible[2] - visible[0]
    height = visible[3] - visible[1]
    assert width >= 160
    assert height >= 30


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
    assert 40 <= difference[0] < difference[2] <= 230
    assert 18 <= difference[1] < difference[3] <= 62


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
        if Path(path).name == "vehicle.png":
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
    icon_boxes = (
        ((58, 124, 78, 144), (78, 124, 84, 144)),
        ((350, 124, 370, 144), (370, 124, 376, 144)),
        ((580, 124, 600, 144), (600, 124, 606, 144)),
        ((350, 290, 370, 310), (370, 290, 376, 310)),
        ((580, 290, 600, 310), (600, 290, 606, 310)),
    )

    for icon_box, gap_box in icon_boxes:
        full_icon = full.crop(icon_box)
        empty_icon = empty.crop(icon_box)
        visible = _visible_non_background_bbox(full, icon_box, panel)
        assert visible is not None
        visible_width = visible[2] - visible[0]
        visible_height = visible[3] - visible[1]
        assert max(visible_width, visible_height) >= 16
        assert min(visible_width, visible_height) >= 10
        assert ImageChops.difference(full_icon, empty_icon).getbbox() is None
        gap = full.crop(gap_box)
        gap_pixels = getattr(gap, "get_flattened_data", gap.getdata)
        assert set(gap_pixels()) == {panel}

    freshness_box = (40, 434, 56, 450)
    freshness_gap_box = (56, 434, 62, 450)
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
    for box in ((350, 122, 470, 145), (40, 432, 220, 455)):
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
