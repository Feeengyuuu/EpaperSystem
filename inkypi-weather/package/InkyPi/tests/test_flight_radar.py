import sys
from pathlib import Path
from urllib.parse import parse_qs, urlparse

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

    pixels = list(image.getdata())
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
    assert calls == [(16, True)]
