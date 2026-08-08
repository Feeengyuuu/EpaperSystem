import json
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image


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
    def __init__(self, token="bridge-token"):
        self.token = token
        self.loaded = []

    def get_resolution(self):
        return (800, 480)

    def get_config(self, key=None, default=None):
        return default

    def load_env_key(self, key):
        self.loaded.append(key)
        return self.token


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


def _plugin(tmp_path):
    plugin = VehicleStatus({"id": "vehicle_status"})
    plugin.get_plugin_dir = lambda path=None: str(tmp_path / path) if path else str(tmp_path)
    return plugin


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
    assert kwargs["headers"] == {"Authorization": "Bearer bridge-token"}
    assert kwargs["allow_redirects"] is False
    assert kwargs["timeout"] == (4, 12)
    assert kwargs["max_bytes"] == 64 * 1024

    cache_text = plugin._cache_file().read_text(encoding="utf-8")
    assert "bridge-token" not in cache_text
    assert "vin" not in cache_text.lower()
    assert "location" not in cache_text.lower()


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
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("expired vehicle values rendered")
        ),
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

    def capture_summary(summary, dimensions, theme, settings):
        rendered["summary"] = summary
        return render_summary(summary, dimensions, theme, settings)

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


def test_new_usable_stale_snapshot_replaces_expired_local_cache(tmp_path, monkeypatch):
    now = 1_800_000_000.0
    plugin = _plugin(tmp_path)
    expired = _summary()
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
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("contradictory closure state rendered")
        ),
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

    assert manifest["schema_version"] == 2
    assert manifest["capabilities"] == {
        "supports_live_refresh": False,
        "supports_presentation_refresh": False,
        "supports_day_night_theme": True,
    }
    assert manifest["refresh_on_display"] is False
    assert manifest["recommended_refresh"]["interval"] == 10800
    assert 'type="password"' not in settings_html
    assert "EPAPER_VEHICLE_BRIDGE_TOKEN" in settings_html
    assert (
        '<input type="checkbox" id="showClimate" name="showClimate" '
        'value="true" checked>'
    ) in settings_html
    assert '<input type="hidden" name="showClimate" value="false">' in settings_html
    assert "String(pluginSettings.showClimate).toLowerCase()" in settings_html


def test_vehicle_art_is_a_real_transparent_cutout_without_chroma_fringe():
    path = Path(vehicle_module.__file__).with_name("vehicle.png")

    image = Image.open(path).convert("RGBA")
    alpha = image.getchannel("A")
    visible = [pixel for pixel in image.get_flattened_data() if pixel[3] > 32]

    assert image.width > image.height * 2
    assert alpha.getextrema() == (0, 255)
    assert all(
        alpha.getpixel(point) == 0
        for point in ((0, 0), (image.width - 1, 0), (0, image.height - 1), (image.width - 1, image.height - 1))
    )
    assert len(visible) > image.width * image.height * 0.5
    assert not any(red < 50 and green > 235 and blue < 50 for red, green, blue, _alpha in visible)


def test_vehicle_art_stays_inside_header_and_clear_of_metric_cards(tmp_path, monkeypatch):
    plugin = _plugin(tmp_path)
    art = Image.new("RGBA", (300, 120), (255, 0, 255, 255))
    monkeypatch.setattr(vehicle_module, "_load_vehicle_art", lambda: art)

    image = plugin._render_summary(_summary(), (800, 480), {}, {})
    magenta_rows = [
        y
        for y in range(image.height)
        if any(image.getpixel((x, y)) == (255, 0, 255) for x in range(image.width))
    ]
    magenta_columns = [
        x
        for x in range(image.width)
        if any(image.getpixel((x, y)) == (255, 0, 255) for y in range(image.height))
    ]

    assert magenta_rows
    assert magenta_columns
    assert min(magenta_rows) >= 50
    assert max(magenta_rows) <= 164
    assert min(magenta_columns) >= 350
    assert max(magenta_columns) <= 619


def test_long_vehicle_identity_text_never_enters_vehicle_art_area(tmp_path, monkeypatch):
    plugin = _plugin(tmp_path)
    summary = _summary()
    summary["vehicle"].update(
        {
            "display_name": "W" * 64,
            "model": "W" * 40,
            "trim": "W" * 40,
        }
    )
    monkeypatch.setattr(
        vehicle_module,
        "_load_vehicle_art",
        lambda: Image.new("RGBA", (1, 1), (0, 0, 0, 0)),
    )

    image = plugin._render_summary(summary, (800, 480), {}, {})
    ink = (24, 28, 33)

    assert not any(
        image.getpixel((x, y)) == ink
        for y in range(78, 156)
        for x in range(335, 620)
    )
