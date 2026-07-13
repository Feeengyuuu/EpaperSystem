import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from PIL import Image, ImageDraw

import plugins.flight_radar.flight_radar as flight_radar_module
from plugins.flight_radar.flight_radar import DEFAULT_TRACK_HISTORY_POINTS, FlightRadar, SourceStatus


class FakeDeviceConfig:
    def __init__(self, resolution=(800, 480), orientation="horizontal", timezone="America/Los_Angeles"):
        self.resolution = resolution
        self.orientation = orientation
        self.timezone = timezone

    def get_resolution(self):
        return self.resolution

    def get_config(self, key=None, default=None):
        values = {"orientation": self.orientation, "timezone": self.timezone}
        if key is None:
            return values
        return values.get(key, default)

    def load_env_key(self, key):
        return None


def _plugin():
    plugin = FlightRadar({"id": "flight_radar"})
    plugin._write_radar_context = lambda *args, **kwargs: None
    return plugin


def _canonical_theme(mode):
    if mode == "night":
        palette = {
            "background": (7, 20, 24),
            "panel": (13, 38, 44),
            "ink": (239, 250, 252),
            "muted": (166, 198, 205),
            "rule": (45, 84, 94),
            "accent": (99, 200, 227),
        }
    else:
        palette = {
            "background": (237, 245, 247),
            "panel": (250, 253, 253),
            "ink": (18, 42, 48),
            "muted": (73, 103, 111),
            "rule": (158, 188, 195),
            "accent": (20, 121, 149),
        }
    return {
        "requested_mode": "auto",
        "mode": mode,
        "source": "weather",
        "reason": "sunrise/sunset",
        "date": "2026-07-12",
        "palette": palette,
        "css": {},
    }


def _sample_snapshot():
    return {
        "schema": flight_radar_module.CACHE_SCHEMA_VERSION,
        "center": {"lat": 37.6213, "lon": -122.3790, "label": "SFO"},
        "radius_nm": 160,
        "source": "adsb_lol",
        "source_label": "ADSB.lol",
        "warning": "",
        "from_cache": False,
        "statuses": [SourceStatus("adsb_lol", "ADSB.lol", "ok", 1, 10).to_dict()],
        "generated_at": "2026-07-12T12:00:00-07:00",
        "aircraft": [
            {
                "callsign": "UAL123",
                "hex": "A12345",
                "lat": 37.7,
                "lon": -122.3,
                "altitude_ft": 32000,
                "speed_kt": 442,
                "track": 178,
                "distance_nm": 7.2,
                "source": "adsb_lol",
            }
        ],
    }


def test_readsb_aircraft_normalization_keeps_position_and_distance():
    plugin = _plugin()

    aircraft = plugin._normalize_readsb_aircraft(
        {
            "hex": "a12345",
            "flight": "UAL123 ",
            "lat": 37.7,
            "lon": -122.3,
            "alt_baro": 32000,
            "gs": 442,
            "track": 178,
        },
        "adsb_lol",
        37.6213,
        -122.3790,
    )

    assert aircraft["callsign"] == "UAL123"
    assert aircraft["altitude_ft"] == 32000
    assert aircraft["speed_kt"] == 442
    assert aircraft["distance_nm"] > 0


def test_route_formatting_prefers_origin_destination():
    assert (
        FlightRadar._format_route_line(
            {"route_label": "San Francisco -> Burbank", "route": "SFO-BUR"}
        )
        == "旧金山 -> 伯班克"
    )
    assert (
        FlightRadar._format_route_line(
            {"origin_city": "San Francisco", "destination_city": "San Diego", "route": "SFO-SAN"}
        )
        == "旧金山 -> 圣迭戈"
    )
    assert FlightRadar._format_route_line({"route": "SFO-BUR"}) == "旧金山 -> 伯班克"
    assert FlightRadar._format_route_line({"route": "KSFO-KLAX"}) == "旧金山 -> 洛杉矶"
    assert FlightRadar._format_route_line({"origin": "SFO", "destination": "SAN"}) == "旧金山 -> 圣迭戈"
    assert (
        FlightRadar._format_route_line({"route_label": "Honolulu -> Oakland"})
        == "檀香山 -> 奥克兰"
    )
    assert (
        FlightRadar._format_route_line({"route_label": "旧金山 -> 东京"})
        == "旧金山 -> 东京"
    )


def test_airping_route_airports_create_city_label():
    route = FlightRadar._clean_route("SFO-BUR")
    info = FlightRadar._route_city_info(
        {
            "_airports": [
                {"iata": "SFO", "icao": "KSFO", "location": "San Francisco"},
                {"iata": "BUR", "icao": "KBUR", "location": "Burbank"},
            ]
        },
        route,
    )

    assert info["route_label"] == "San Francisco -> Burbank"
    assert info["origin_city"] == "San Francisco"
    assert info["destination_city"] == "Burbank"


def test_aircraft_flow_prefers_route_direction():
    assert FlightRadar._route_flow({"origin": "SFO", "destination": "LAX"}, "SFO") == "departure"
    assert FlightRadar._route_flow({"origin_city": "Seattle", "destination_city": "San Francisco"}, "SFO") == "arrival"
    assert (
        FlightRadar._route_flow(
            {"route_label": "San Francisco -> Chicago", "route": "SFO-ORD"},
            "SFO",
        )
        == "departure"
    )


def test_aircraft_flow_falls_back_to_radial_heading():
    assert FlightRadar._aircraft_flow({"track": 90}, "SFO", 100, 100, 120, 100, 90) == "departure"
    assert FlightRadar._aircraft_flow({"track": 270}, "SFO", 100, 100, 120, 100, 270) == "arrival"


def test_arriving_and_ground_aircraft_do_not_get_map_labels():
    assert not FlightRadar._should_label_plane(
        {"origin_city": "Seattle", "destination_city": "San Francisco", "track": 180},
        "SFO",
        100,
        100,
        100,
        80,
    )
    assert not FlightRadar._should_label_plane({"track": 270}, "SFO", 100, 100, 120, 100)
    assert not FlightRadar._should_label_plane(
        {"origin": "SFO", "destination": "LAX", "on_ground": True, "track": 90},
        "SFO",
        100,
        100,
        120,
        100,
    )
    assert FlightRadar._should_label_plane(
        {"callsign": "UAL123", "origin": "SFO", "destination": "LAX", "track": 270},
        "SFO",
        100,
        100,
        120,
        100,
    )
    assert not FlightRadar._should_label_plane({"callsign": "2B1D6", "track": 90}, "SFO", 100, 100, 120, 100)
    assert FlightRadar._should_label_plane({"callsign": "CPA873", "track": 90}, "SFO", 100, 100, 120, 100)


def test_google_static_map_url_has_distinct_day_and_night_styles():
    plugin = _plugin()
    inner = (0, 0, 320, 240)

    day_url = plugin._google_static_map_url(
        {"googleMapType": "terrain", "googleMapTheme": "day"},
        "TEST_KEY",
        37.6213,
        -122.3790,
        20,
        inner,
    )
    night_url = plugin._google_static_map_url(
        {"googleMapType": "terrain", "googleMapTheme": "night"},
        "TEST_KEY",
        37.6213,
        -122.3790,
        20,
        inner,
    )

    day_params = parse_qs(urlparse(day_url).query)
    night_params = parse_qs(urlparse(night_url).query)

    assert day_params["maptype"] == ["terrain"]
    assert night_params["maptype"] == ["terrain"]
    assert "feature:landscape|element:geometry|color:0xf4edd0" in day_params["style"]
    assert "feature:landscape|element:geometry|color:0x2f2d24" in night_params["style"]
    assert day_params["style"] != night_params["style"]


def test_theme_uses_bolder_comic_chrome():
    theme = FlightRadar._theme()

    assert sum(theme["bg"]) / 3 < 140
    assert max(theme["header_bg"]) < 30
    assert min(theme["panel"]) > 90
    assert theme["line"] == theme["ink"]
    assert theme["header_ink"] != theme["ink"]
    assert theme["panel"][2] > theme["panel"][0]
    assert theme["panel2"][2] > theme["panel2"][0]
    assert theme["plane_low"][0] > theme["plane_low"][2]
    assert theme["plane_cruise"][0] > theme["plane_cruise"][2]
    assert theme["plane_high"][0] > theme["plane_high"][2]
    assert theme["plane_low"] != theme["cyan"]
    assert theme["plane_cruise"] != theme["amber"]
    assert min(theme["plane_halo"]) > 150


def test_aircraft_list_order_places_arrivals_after_in_flight():
    snapshot = {
        "center": {"lat": 37.6213, "lon": -122.3790, "label": "SFO"},
        "aircraft": [
            {"callsign": "ARR1", "origin_city": "Seattle", "destination_city": "San Francisco", "distance_nm": 2},
            {"callsign": "DEP1", "origin": "SFO", "destination": "LAX", "distance_nm": 12},
            {"callsign": "OVER1", "route": "LAX-SEA", "distance_nm": 5},
            {"callsign": "GND1", "on_ground": True, "distance_nm": 1},
        ],
    }

    ordered = FlightRadar._ordered_aircraft_for_list(snapshot)

    assert [plane["callsign"] for plane in ordered] == ["OVER1", "DEP1", "ARR1", "GND1"]


def test_aircraft_list_order_uses_heading_when_route_is_unknown():
    snapshot = {
        "center": {"lat": 37.6213, "lon": -122.3790, "label": "SFO"},
        "aircraft": [
            {"callsign": "HDGARR", "lat": 37.75, "lon": -122.3790, "track": 180, "distance_nm": 2},
            {"callsign": "HDGDEP", "lat": 37.75, "lon": -122.3790, "track": 360, "distance_nm": 9},
        ],
    }

    ordered = FlightRadar._ordered_aircraft_for_list(snapshot)

    assert [plane["callsign"] for plane in ordered] == ["HDGDEP", "HDGARR"]


def test_plane_marker_renders_colored_aircraft_icon():
    plugin = _plugin()
    theme = plugin._theme()
    image = Image.new("RGB", (96, 48), theme["panel"])
    draw = ImageDraw.Draw(image)

    plugin._draw_plane_marker(draw, 24, 24, {"track": 45, "altitude_ft": 6000}, theme)
    plugin._draw_plane_marker(draw, 72, 24, {"track": 225, "altitude_ft": 35000}, theme)

    pixels = list(image.get_flattened_data())
    assert pixels.count(theme["plane_low"]) > 10
    assert pixels.count(theme["plane_high"]) > 10
    assert pixels.count(theme["plane_halo"]) > 10
    assert pixels.count(theme["plane_outline"]) > 10


def test_track_history_keeps_current_plus_five_previous_refreshes(tmp_path):
    plugin = _plugin()
    cache_path = tmp_path / ".flight_radar_tracks_test.json"
    original_track_history_file = FlightRadar._track_history_file
    FlightRadar._track_history_file = staticmethod(lambda: cache_path)

    try:
        aircraft = [{"hex": "A12345", "callsign": "UAL123", "lat": 37.6213, "lon": -122.3790}]
        for index in range(8):
            aircraft[0]["lon"] = -122.3790 + (index * 0.006)
            plugin._attach_track_history(aircraft, {}, 37.6213, -122.3790, 160)

        track_points = aircraft[0]["track_points"]
        assert DEFAULT_TRACK_HISTORY_POINTS == 6
        assert len(track_points) == 6
        assert round(track_points[0]["lon"], 4) == round(-122.3790 + (2 * 0.006), 4)
        assert round(track_points[-1]["lon"], 4) == round(-122.3790 + (7 * 0.006), 4)
    finally:
        FlightRadar._track_history_file = original_track_history_file


def test_trail_limit_allows_longer_history_path():
    points = [(0, 0), (24, 0), (48, 0), (72, 0), (96, 0), (120, 0)]

    limited = FlightRadar._limit_trail_points(points)

    assert limited[0] == (24, 0)
    assert limited[-1] == (120, 0)
    assert len(limited) == 5


def test_fetch_sources_tries_next_source_when_first_fails():
    plugin = _plugin()
    calls = []

    def fake_fetch_source(settings, device_config, source_key, lat, lon, radius_nm):
        calls.append(source_key)
        if source_key == "adsb_lol":
            raise RuntimeError("temporary fail")
        return [
            {
                "callsign": "TEST2",
                "lat": 37.7,
                "lon": -122.3,
                "altitude_ft": 12000,
                "speed_kt": 250,
                "track": 90,
                "distance_nm": 8,
                "source": source_key,
            }
        ]

    plugin._fetch_source = fake_fetch_source
    aircraft, source, statuses = plugin._fetch_sources(
        {},
        FakeDeviceConfig(),
        37.6213,
        -122.3790,
        160,
        90,
        ["adsb_lol", "airplanes_live"],
    )

    assert calls == ["adsb_lol", "airplanes_live"]
    assert source == "airplanes_live"
    assert aircraft[0]["callsign"] == "TEST2"
    assert [status.state for status in statuses] == ["failed", "ok"]


def test_render_sample_dashboard_size():
    plugin = _plugin()
    now = plugin._now(FakeDeviceConfig())
    sample = {
        "center": {"lat": 37.6213, "lon": -122.3790, "label": "Bay Area"},
        "radius_nm": 160,
        "source_label": "ADSB.lol",
        "warning": "",
        "from_cache": False,
        "statuses": [SourceStatus("adsb_lol", "ADSB.lol", "ok", 2, 120).to_dict()],
        "aircraft": [
            {
                "callsign": "UAL123",
                "hex": "A12345",
                "lat": 37.7,
                "lon": -122.3,
                "altitude_ft": 32000,
                "speed_kt": 442,
                "track": 178,
                "distance_nm": 7.2,
                "source": "adsb_lol",
            },
            {
                "callsign": "SWA88",
                "hex": "B12345",
                "lat": 37.2,
                "lon": -121.9,
                "altitude_ft": 8500,
                "speed_kt": 218,
                "track": 303,
                "distance_nm": 33,
                "source": "adsb_lol",
            },
        ],
    }

    image = plugin._render(sample, (800, 480), {}, now)

    assert image.size == (800, 480)


def test_flight_radar_base_font_uses_shared_resolver(monkeypatch):
    sentinel = object()
    calls = []
    monkeypatch.setattr(
        flight_radar_module,
        "get_base_ui_font",
        lambda size, bold=False: calls.append((size, bold)) or sentinel,
        raising=False,
    )

    assert FlightRadar._font(16, "bold") is sentinel
    assert FlightRadar._city_font(14, "bold") is sentinel
    assert calls == [(16, True), (14, True)]


def test_flight_auto_main_and_google_map_use_pinned_weather_theme_without_clock(monkeypatch):
    plugin = _plugin()
    snapshot = _sample_snapshot()
    now = datetime.fromisoformat(snapshot["generated_at"])
    monkeypatch.setattr(
        plugin,
        "_now",
        lambda *_args, **_kwargs: pytest.fail("map theme re-read the wall clock"),
    )
    monkeypatch.setattr(
        plugin,
        "resolve_theme",
        lambda *_args, **_kwargs: pytest.fail("pinned theme was re-resolved"),
    )

    day_settings = {"googleMapTheme": "auto", "_inkypi_theme": _canonical_theme("day")}
    night_settings = {"googleMapTheme": "auto", "_inkypi_theme": _canonical_theme("night")}
    day = plugin._render(snapshot, (800, 480), day_settings, now, FakeDeviceConfig())
    night = plugin._render(snapshot, (800, 480), night_settings, now, FakeDeviceConfig())

    assert plugin._google_map_theme(day_settings, FakeDeviceConfig()) == "day"
    assert plugin._google_map_theme(night_settings, FakeDeviceConfig()) == "night"
    assert day.getpixel((0, 100)) == day_settings["_inkypi_theme"]["palette"]["background"]
    assert night.getpixel((0, 100)) == night_settings["_inkypi_theme"]["palette"]["background"]
    assert FlightRadar._theme(day_settings["_inkypi_theme"])["panel"] == day_settings["_inkypi_theme"]["palette"]["panel"]
    assert FlightRadar._theme(night_settings["_inkypi_theme"])["panel"] == night_settings["_inkypi_theme"]["palette"]["panel"]
    assert list(day.get_flattened_data()).count(day_settings["_inkypi_theme"]["palette"]["panel"]) > 1_000
    assert list(night.get_flattened_data()).count(night_settings["_inkypi_theme"]["palette"]["panel"]) > 1_000
    assert day.tobytes() != night.tobytes()


def test_flight_theme_only_uses_matching_snapshot_and_never_writes_or_calls_provider(
    monkeypatch,
    tmp_path,
):
    plugin = _plugin()
    device = FakeDeviceConfig()
    monkeypatch.setenv("INKYPI_CACHE_DIR", str(tmp_path / "cache"))
    settings = {
        "mapMode": "google",
        "googleMapsApiKey": "TEST_KEY",
        "googleMapTheme": "auto",
        "_theme_render_only": True,
        "_inkypi_theme": _canonical_theme("night"),
    }
    source_order = plugin._source_order(settings)
    cache_key = plugin._cache_key(
        flight_radar_module.DEFAULT_LATITUDE,
        flight_radar_module.DEFAULT_LONGITUDE,
        flight_radar_module.DEFAULT_RADIUS_NM,
        flight_radar_module.DEFAULT_MAX_AIRCRAFT,
        source_order,
    )
    cache_file = plugin._cache_file(cache_key)
    plugin._write_json(
        cache_file,
        {
            "schema": flight_radar_module.CACHE_SCHEMA_VERSION,
            "fetched_at": 1.0,
            "snapshot": _sample_snapshot(),
        },
    )
    source_bytes = cache_file.read_bytes()

    monkeypatch.setattr(plugin, "_now", lambda *_args: pytest.fail("theme-only read wall clock"))
    monkeypatch.setattr(plugin, "_fetch_sources", lambda *_args: pytest.fail("theme-only called aircraft provider"))
    monkeypatch.setattr(plugin, "_attach_track_history", lambda *_args: pytest.fail("theme-only advanced track history"))
    monkeypatch.setattr(plugin, "_write_radar_context", lambda *_args: pytest.fail("theme-only advanced context"))
    monkeypatch.setattr(plugin, "_write_json", lambda *_args: pytest.fail("theme-only rewrote state"))
    monkeypatch.setattr(plugin, "_client", lambda: pytest.fail("theme-only fetched a map"))

    image = plugin.generate_image(settings, device)

    assert image.size == (800, 480)
    assert image.getpixel((0, 100)) == _canonical_theme("night")["palette"]["background"]
    assert cache_file.read_bytes() == source_bytes


def test_flight_theme_only_cold_or_incompatible_snapshot_fails_closed_without_provider(
    monkeypatch,
    tmp_path,
):
    plugin = _plugin()
    device = FakeDeviceConfig()
    monkeypatch.setenv("INKYPI_CACHE_DIR", str(tmp_path / "cache"))
    provider_calls = []
    monkeypatch.setattr(
        plugin,
        "_fetch_sources",
        lambda *_args: provider_calls.append("provider") or ([], "none", []),
    )

    with pytest.raises(RuntimeError, match="matching FlightRadar source cache"):
        plugin.generate_image(
            {
                "_theme_render_only": True,
                "_inkypi_theme": _canonical_theme("day"),
            },
            device,
        )

    assert provider_calls == []
