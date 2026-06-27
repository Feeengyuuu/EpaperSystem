import json
import sys
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

from PIL import Image, ImageChops, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.modules.setdefault("psutil", SimpleNamespace(virtual_memory=lambda: SimpleNamespace(total=2 * 1024**3)))

import plugins.earthspace_pulse.earthspace_pulse as earthspace_module  # noqa: E402
from plugins.earthspace_pulse.earthspace_pulse import (  # noqa: E402
    CACHE_SCHEMA_VERSION,
    LOCAL_SAMPLE_PAYLOAD,
    EarthspacePulse,
)


class DummyDeviceConfig:
    def __init__(self, resolution=(800, 480), orientation="horizontal", env=None):
        self.resolution = resolution
        self.orientation = orientation
        self.env = env or {}

    def get_resolution(self):
        return self.resolution

    def get_config(self, key=None, default=None):
        values = {"orientation": self.orientation, "timezone": "America/Los_Angeles"}
        if key is None:
            return values
        return values.get(key, default)

    def load_env_key(self, key):
        return self.env.get(key, "")


def make_plugin(tmp_path):
    plugin = EarthspacePulse({"id": "earthspace_pulse"})
    plugin._cache_dir = lambda: tmp_path
    return plugin


def usgs_feature(
    event_id,
    mag,
    place,
    time_ms,
    lon,
    lat,
    depth,
    alert=None,
    tsunami=0,
    sig=100,
):
    return {
        "id": event_id,
        "properties": {
            "mag": mag,
            "place": place,
            "time": time_ms,
            "alert": alert,
            "tsunami": tsunami,
            "sig": sig,
            "url": f"https://earthquake.usgs.gov/earthquakes/eventpage/{event_id}",
            "title": f"M {mag} - {place}",
        },
        "geometry": {"coordinates": [lon, lat, depth]},
    }


def sample_usgs_feed():
    return {
        "metadata": {"count": 3, "generated": 1782504000000},
        "features": [
            usgs_feature("recent-small", 2.1, "12 km S of A", 1782503900000, -122.1, 37.2, 7.4),
            usgs_feature("max-alert", 5.6, "45 km W of B", 1782503800000, 142.2, 38.4, 23.0, alert="yellow", tsunami=1, sig=620),
            usgs_feature("old-mid", 3.4, "80 km N of C", 1782503000000, -118.5, 34.4, 4.0),
        ],
    }


def test_plugin_info_matches_registration():
    info_path = Path(__file__).resolve().parents[1] / "src" / "plugins" / "earthspace_pulse" / "plugin-info.json"
    info = json.loads(info_path.read_text(encoding="utf-8"))

    assert info["id"] == "earthspace_pulse"
    assert info["class"] == "EarthspacePulse"
    assert info["display_name"] == "Earthspace Pulse / 地球太空脉搏"


def test_settings_defaults_are_declared():
    settings_path = Path(__file__).resolve().parents[1] / "src" / "plugins" / "earthspace_pulse" / "settings.html"
    html = settings_path.read_text(encoding="utf-8")

    assert 'name="themeMode"' in html
    assert "valueOrDefault('refreshMinutes', 30)" in html
    assert "valueOrDefault('quakeFeed', 'all_day')" in html
    assert "valueOrDefault('maxQuakes', 4)" in html
    assert "valueOrDefault('quakeMapMode', 'auto')" in html
    assert "valueOrDefault('googleMapType', 'terrain')" in html
    assert "valueOrDefault('mapCacheHours', 24)" in html
    assert 'name="showNearestToLocation"' in html
    assert 'name="forceRefresh"' in html


def test_parse_kp_observed_and_forecast(tmp_path):
    plugin = make_plugin(tmp_path)

    parsed = plugin._parse_kp(
        [
            {"time_tag": "2026-06-26T00:00:00", "Kp": "2.33"},
            {"time_tag": "2026-06-26T03:00:00", "Kp": "4.00"},
        ],
        [{"time_tag": "2026-06-26T06:00:00", "kp": "5.67", "observed": "forecast", "noaa_scale": "G1"}],
    )

    assert parsed["kp_now"] == 4.0
    assert parsed["kp_trend"] == {"direction": "rising", "delta": 1.67}
    assert parsed["kp_history"][-1]["time_tag"] == "2026-06-26T03:00:00"
    assert parsed["kp_forecast"][0]["kp"] == 5.67
    assert parsed["noaa_scale"] == "G1"


def test_parse_alerts_extracts_headline(tmp_path):
    plugin = make_plugin(tmp_path)

    alerts = plugin._parse_alerts(
        [
            {
                "product_id": "K04W",
                "issue_datetime": "2026-06-25 23:43:25.917",
                "message": "Space Weather Message Code: K04W\nSerial Number: 1\nEXTENDED WARNING: Geomagnetic K-index of 4 expected\nValid From: now",
            }
        ]
    )

    assert alerts == [
        {
            "product_id": "K04W",
            "issue_datetime": "2026-06-25 23:43:25.917",
            "headline": "EXTENDED WARNING: Geomagnetic K-index of 4 expected",
        }
    ]


def test_parse_aurora_summarizes_grid(tmp_path):
    plugin = make_plugin(tmp_path)

    aurora = plugin._parse_aurora(
        {
            "Observation Time": "2026-06-27T06:33:00Z",
            "Forecast Time": "2026-06-27T07:17:00Z",
            "coordinates": [[-100, 64, 3], [-90, 74, 23], [120, -66, 18], [130, -77, 28], [0, 10, 0]],
        }
    )

    assert aurora["observation_time"] == "2026-06-27T06:33:00Z"
    assert aurora["forecast_time"] == "2026-06-27T07:17:00Z"
    assert aurora["max"] == 28
    assert aurora["north_peak_lat"] == 74
    assert aurora["south_peak_lat"] == -77
    assert aurora["active_points"] == 4


def test_parse_xray_classifies_latest_long_channel(tmp_path):
    plugin = make_plugin(tmp_path)

    xray = plugin._parse_xray(
        [
            {"time_tag": "2026-06-26T06:35:00Z", "energy": "0.05-0.4nm", "flux": "9.9e-6"},
            {"time_tag": "2026-06-26T06:30:00Z", "energy": "0.1-0.8nm", "flux": "1.4e-6", "observed_flux": "1.3e-6"},
            {"time_tag": "2026-06-26T06:40:00Z", "energy": "0.1-0.8nm", "flux": "1.2e-5", "observed_flux": "1.1e-5"},
        ]
    )

    assert xray["time_tag"] == "2026-06-26T06:40:00Z"
    assert xray["class"] == "M1.2"
    assert xray["observed_flux"] == 1.1e-5


def test_parse_solar_wind_handles_plasma_and_mag_arrays(tmp_path):
    plugin = make_plugin(tmp_path)

    wind = plugin._parse_solar_wind(
        [
            ["time_tag", "density", "speed", "temperature"],
            ["2026-06-26 06:30:00.000", "4.2", "418.1", "86000"],
            ["2026-06-26 06:40:00.000", "5.1", "430.9", "90000"],
        ],
        [
            {"value": ["time_tag", "bx_gsm", "by_gsm", "bz_gsm", "lon_gsm", "lat_gsm", "bt"]},
            {"value": ["2026-06-26 06:40:00.000", "1.1", "2.2", "-3.4", "10", "-5", "5.6"]},
        ],
    )

    assert wind["time_tag"] == "2026-06-26 06:40:00.000"
    assert wind["density"] == 5.1
    assert wind["speed"] == 430.9
    assert wind["temperature"] == 90000.0
    assert wind["bz_gsm"] == -3.4
    assert wind["bt"] == 5.6


def test_parse_earthquakes_extracts_max_recent_depth_and_badges(tmp_path):
    plugin = make_plugin(tmp_path)

    quakes = plugin._parse_earthquakes(sample_usgs_feed(), {"maxQuakes": 2}, feed="all_day")

    assert quakes["feed"] == "all_day"
    assert quakes["count_24h"] == 3
    assert quakes["source_count"] == 3
    assert quakes["source_generated_at"] == "2026-06-26T20:00:00Z"
    assert quakes["max_event"]["id"] == "max-alert"
    assert quakes["max_event"]["depth_km"] == 23.0
    assert quakes["max_event"]["tsunami"] is True
    assert quakes["max_event"]["alert"] == "yellow"
    assert [event["id"] for event in quakes["recent_events"]] == ["recent-small", "max-alert"]


def test_nearest_quake_distance_when_location_enabled(tmp_path):
    plugin = make_plugin(tmp_path)

    quakes = plugin._parse_earthquakes(
        sample_usgs_feed(),
        {"showNearestToLocation": "true", "latitude": "37.3", "longitude": "-122.0", "locationName": "Bay Area"},
    )

    assert quakes["nearest_event"]["id"] == "recent-small"
    assert 0 < quakes["nearest_event"]["distance_km"] < 20


def test_payload_uses_fresh_cache_without_live_fetch(tmp_path, monkeypatch):
    plugin = make_plugin(tmp_path)
    now = datetime(2026, 6, 26, 12, 0, tzinfo=timezone.utc)
    calls = {"count": 0}

    def fake_live(settings, generated_at):
        calls["count"] += 1
        payload = deepcopy(LOCAL_SAMPLE_PAYLOAD)
        payload["status"]["source_state"] = "live"
        payload["status"]["generated_at"] = generated_at.isoformat()
        return payload

    monkeypatch.setattr(plugin, "_fetch_live_payload", fake_live)

    first = plugin._payload({"refreshMinutes": 30}, now)
    second = plugin._payload({"refreshMinutes": 30}, now + timedelta(minutes=10))

    assert calls["count"] == 1
    assert first["status"]["source_state"] == "live"
    assert second["status"]["source_state"] == "cache"


def test_payload_falls_back_to_local_sample_when_live_and_cache_fail(tmp_path, monkeypatch):
    plugin = make_plugin(tmp_path)

    monkeypatch.setattr(plugin, "_fetch_live_payload", lambda settings, now: (_ for _ in ()).throw(RuntimeError("offline")))

    payload = plugin._payload({"refreshMinutes": 30}, datetime(2026, 6, 26, 12, 0, tzinfo=timezone.utc))

    assert payload["schema"] == CACHE_SCHEMA_VERSION
    assert payload["status"]["source_state"] == "local_sample"
    assert payload["space_weather"]["kp_now"] is not None
    assert payload["earthquakes"]["recent_events"]


def test_payload_ignores_stale_cache_when_live_fails(tmp_path, monkeypatch):
    plugin = make_plugin(tmp_path)
    now = datetime(2026, 6, 26, 12, 0, tzinfo=timezone.utc)
    stale = deepcopy(LOCAL_SAMPLE_PAYLOAD)
    stale["status"]["source_state"] = "stale-cache"
    plugin._write_cache({"schema": CACHE_SCHEMA_VERSION, "generated_at": (now - timedelta(hours=2)).isoformat(), "payload": stale})

    monkeypatch.setattr(plugin, "_fetch_live_payload", lambda settings, generated_at: (_ for _ in ()).throw(RuntimeError("offline")))

    payload = plugin._payload({"refreshMinutes": 30}, now)

    assert payload["status"]["source_state"] == "local_sample"

def test_google_maps_api_key_uses_settings_and_device_env(tmp_path):
    plugin = make_plugin(tmp_path)

    assert plugin._google_maps_api_key({"googleMapsApiKey": " explicit "}, DummyDeviceConfig(env={"GOOGLE_MAPS_API_KEY": "ENV"})) == "explicit"
    assert plugin._google_maps_api_key({}, DummyDeviceConfig(env={"Google_KEY": "ENVKEY"})) == "ENVKEY"
    assert plugin._google_maps_api_key({}, DummyDeviceConfig()) == ""


def test_google_quake_map_url_contains_static_map_markers(tmp_path):
    plugin = make_plugin(tmp_path)
    quakes = deepcopy(LOCAL_SAMPLE_PAYLOAD)["earthquakes"]

    url = plugin._google_quake_map_url({"googleMapType": "terrain", "quakeMapZoom": 1}, "TESTKEY", quakes, (296, 124))
    parsed = urlparse(url)
    query = parse_qs(parsed.query)

    assert parsed.scheme == "https"
    assert parsed.netloc == "maps.googleapis.com"
    assert parsed.path == "/maps/api/staticmap"
    assert query["key"] == ["TESTKEY"]
    assert query["size"] == ["296x124"]
    assert query["zoom"] == ["1"]
    assert query["maptype"] == ["terrain"]
    markers = query["markers"]
    assert any("label:M" in marker and "-58.20000,-25.10000" in marker for marker in markers)
    assert any("38.82100,-122.81250" in marker for marker in markers)
    assert query.get("style")


def test_load_quake_map_uses_fresh_cache_before_network(tmp_path, monkeypatch):
    plugin = make_plugin(tmp_path)
    quakes = deepcopy(LOCAL_SAMPLE_PAYLOAD)["earthquakes"]
    settings = {"googleMapsApiKey": "TESTKEY", "quakeMapZoom": 1, "googleMapType": "terrain", "mapCacheHours": 24}
    url = plugin._google_quake_map_url(settings, "TESTKEY", quakes, (260, 100))
    cache_file = plugin._map_cache_file(url)
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (32, 16), (11, 22, 33)).save(cache_file)

    class FailingSession:
        def get(self, *args, **kwargs):
            raise AssertionError("network should not be used when map cache is fresh")

    monkeypatch.setattr(earthspace_module, "get_http_session", lambda: FailingSession())

    loaded = plugin._load_quake_map(settings, None, quakes, (260, 100))

    assert loaded.size == (32, 16)
    assert loaded.getpixel((0, 0)) == (11, 22, 33)


def test_render_uses_google_map_when_available(tmp_path, monkeypatch):
    plugin = make_plugin(tmp_path)

    monkeypatch.setattr(plugin, "_load_quake_map", lambda *args: Image.new("RGB", (24, 24), (70, 80, 90)))
    monkeypatch.setattr(plugin, "_draw_quake_map", lambda *args: (_ for _ in ()).throw(AssertionError("drawn fallback should not be used")))

    image = plugin._render_page(
        (800, 480),
        deepcopy(LOCAL_SAMPLE_PAYLOAD),
        {"quakeMapMode": "google"},
        datetime(2026, 6, 26, 12, 0, tzinfo=timezone.utc),
        device_config=DummyDeviceConfig(env={"GOOGLE_MAPS_API_KEY": "TESTKEY"}),
    )

    assert image.size == (800, 480)

def test_render_page_returns_nonblank_800x480(tmp_path):
    plugin = make_plugin(tmp_path)
    image = plugin._render_page((800, 480), deepcopy(LOCAL_SAMPLE_PAYLOAD), {}, datetime(2026, 6, 26, 12, 0, tzinfo=timezone.utc))

    assert image.size == (800, 480)
    diff = ImageChops.difference(image, Image.new("RGB", image.size, image.getpixel((0, 0))))
    assert diff.getbbox() is not None


def test_render_page_draws_key_labels(tmp_path, monkeypatch):
    plugin = make_plugin(tmp_path)
    labels = []
    original_text = ImageDraw.ImageDraw.text

    def spy_text(self, xy, text, *args, **kwargs):
        labels.append(str(text))
        return original_text(self, xy, text, *args, **kwargs)

    monkeypatch.setattr(ImageDraw.ImageDraw, "text", spy_text)

    plugin._render_page((800, 480), deepcopy(LOCAL_SAMPLE_PAYLOAD), {}, datetime(2026, 6, 26, 12, 0, tzinfo=timezone.utc))

    assert "Earthspace Pulse" in labels
    assert "SPACE WEATHER" in labels
    assert "EARTH PULSE" in labels


def test_generate_image_returns_expected_size_from_device_config(tmp_path, monkeypatch):
    plugin = make_plugin(tmp_path)
    monkeypatch.setattr(plugin, "_payload", lambda settings, now: deepcopy(LOCAL_SAMPLE_PAYLOAD))
    monkeypatch.setattr(plugin, "_write_context", lambda payload, now: None)

    image = plugin.generate_image({}, DummyDeviceConfig())

    assert image.size == (800, 480)
