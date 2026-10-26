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


FAA_XML = """<AIRPORT_STATUS_INFORMATION><Update_Time>Sun Oct 25 15:36:28 2026 GMT</Update_Time>
<Delay_type><Name>Ground Delay Programs</Name><Ground_Delay_List><Ground_Delay><ARPT>SFO</ARPT>
<Reason>low ceilings</Reason><Avg>1 hour and 3 minutes</Avg><Max>2 hours</Max></Ground_Delay></Ground_Delay_List></Delay_type>
<Delay_type><Name>General Arrival/Departure Delay Info</Name><Arrival_Departure_Delay_List><Delay><ARPT>SFO</ARPT>
<Reason>wind</Reason><Arrival_Departure Type="Arrival"><Min>16 minutes</Min><Max>30 minutes</Max></Arrival_Departure>
</Delay><Delay><ARPT>SJC</ARPT><Reason>runway</Reason><Arrival_Departure Type="Departure"><Min>15 minutes</Min>
<Max>45 minutes</Max></Arrival_Departure></Delay></Arrival_Departure_Delay_List></Delay_type>
<Delay_type><Name>Airport Closures</Name><Airport_Closure_List><Airport><ARPT>OAK</ARPT>
<Reason>!OAK 10/001 OAK AD AP CLSD TO NON SKED TRANSIENT GA ACFT EXC PPR</Reason></Airport>
<Airport><ARPT>LAX</ARPT><Reason>runway work</Reason></Airport></Airport_Closure_List></Delay_type>
</AIRPORT_STATUS_INFORMATION>"""


def test_faa_status_keeps_each_bay_airports_worst_problem_and_ignores_ga_only_closures():
    airports = flight_radar_module.parse_faa_status(FAA_XML)

    assert airports == {"SFO": {"kind": "gdp", "minutes": 63, "reason": "low ceilings"},
                        "SJC": {"kind": "delay", "minutes": 45, "reason": "runway"}}
    assert flight_radar_module.faa_label(airports["SFO"]) == "地面延误63分"
    assert flight_radar_module.faa_label(airports["SJC"]) == "延误45分"
    assert flight_radar_module.faa_label({}) == "正常"
    with pytest.raises(ValueError):
        flight_radar_module.parse_faa_status("<html/>")


def test_faa_status_is_cached_reused_offline_and_dropped_when_old(monkeypatch, tmp_path):
    monkeypatch.setenv("INKYPI_CACHE_DIR", str(tmp_path / "cache"))
    plugin = _plugin()
    calls = []

    class Client:
        def request_text(self, *_args, **_kwargs):
            calls.append("faa")
            return type("Result", (), {"data": FAA_XML})()

    monkeypatch.setattr(plugin, "_client", lambda: Client())
    first = plugin._airport_status(1000.0, allow_network=True)
    assert first["SFO"]["kind"] == "gdp" and calls == ["faa"]
    assert plugin._airport_status(1100.0, allow_network=True) == first and calls == ["faa"]

    class Down:
        def request_text(self, *_args, **_kwargs):
            raise OSError("offline")

    monkeypatch.setattr(plugin, "_client", lambda: Down())
    assert plugin._airport_status(1000.0 + 3600, allow_network=True) == first
    assert plugin._airport_status(1000.0 + 3600, allow_network=False) == first
    assert plugin._airport_status(1000.0 + 3 * 3600, allow_network=True) is None


METAR_ROWS = [
    {"icaoId": "KSFO", "fltCat": "IFR", "wdir": 280, "wspd": 18, "wgst": 27, "visib": "3", "temp": 14.4,
     "clouds": [{"cover": "FEW", "base": 600}, {"cover": "BKN", "base": 900}, {"cover": "OVC", "base": 2000}],
     "rawOb": "METAR KSFO 251656Z 28018G27KT 3SM BKN009 OVC020 14/12 A3005"},
    {"icaoId": "KSJC", "fltCat": "VFR", "wdir": "VRB", "wspd": 3, "visib": "10+", "temp": 11, "clouds": []},
    {"icaoId": "KLAX", "fltCat": "VFR", "wdir": 250, "wspd": 8},
]


def test_metar_gives_each_bay_airport_its_category_wind_and_ceiling():
    airports = flight_radar_module.parse_metar(METAR_ROWS)

    assert set(airports) == {"SFO", "SJC"}
    sfo = airports["SFO"]
    assert (sfo["category"], sfo["wind_dir"], sfo["wind_kt"], sfo["gust_kt"], sfo["ceiling_ft"]) == ("IFR", 280, 18, 27, 900)
    assert airports["SJC"]["wind_dir"] == "VRB" and airports["SJC"]["ceiling_ft"] is None
    assert flight_radar_module.parse_metar({"error": 1}) == {}


def test_targets_take_their_aircraft_class_and_keep_climb_category_and_squawk():
    from plugins.flight_radar import pro_view

    classes = {kind: pro_view.aircraft_class({"type": kind}) for kind in
               ("B77W", "B744", "A320", "CRJ2", "DH8D", "C56X", "C172", "C17", "EC35")}
    assert classes == {"B77W": "heavy", "B744": "quad", "A320": "narrowbody", "CRJ2": "regional",
                       "DH8D": "turboprop", "C56X": "bizjet", "C172": "light", "C17": "quad", "EC35": "helicopter"}
    assert pro_view.aircraft_class({"type": "", "category": "A7"}) == "helicopter"
    assert all((pro_view.AIRCRAFT_DIR / f"{name}.png").is_file() for name in pro_view.AIRCRAFT_SCALES)
    plane = _plugin()._normalize_readsb_aircraft(
        {"hex": "abc123", "flight": "SWA2031 ", "lat": 37.5, "lon": -122.2, "alt_baro": 4400, "gs": 210,
         "track": 120, "baro_rate": 1792, "category": "A3", "squawk": "7700", "t": "B38M"},
        "adsb_lol", 37.62, -122.38)
    assert (plane["vertical_rate"], plane["category"], plane["squawk"]) == (1792, "A3", "7700")


def test_an_emergency_squawk_rings_its_target_in_red():
    plugin = _plugin()
    snapshot = _sample_snapshot()
    snapshot["aircraft"][0].update(type="B738", squawk="7700", vertical_rate=-1500, altitude_ft=6000)
    theme = plugin._theme(_canonical_theme("day"))
    now = datetime(2026, 10, 25, 9, 12, tzinfo=flight_radar_module.ZoneInfo("America/Los_Angeles"))

    alarmed = plugin._render(snapshot, (800, 480), {"_inkypi_theme": _canonical_theme("day")}, now)
    snapshot["aircraft"][0]["squawk"] = "4521"
    quiet = plugin._render(snapshot, (800, 480), {"_inkypi_theme": _canonical_theme("day")}, now)

    scope = (16, 98, 456, 468)
    assert theme["red"] in set(alarmed.crop(scope).get_flattened_data())
    assert alarmed.crop(scope).tobytes() != quiet.crop(scope).tobytes()


def test_pro_view_orders_strips_and_lays_out_the_board_scope_and_strips():
    from plugins.flight_radar import pro_view

    plugin = _plugin()
    snapshot = _sample_snapshot()
    snapshot["aircraft"] = [
        {"callsign": "JBU115", "hex": "A1", "lat": 37.6196, "lon": -122.3802, "altitude_ft": 0, "on_ground": True,
         "distance_nm": 0.1, "route": "JFK-SFO", "origin": "JFK", "destination": "SFO", "type": "A21N"},
        {"callsign": "SKW5519", "hex": "A2", "lat": 37.55, "lon": -122.30, "altitude_ft": 3500, "speed_kt": 180,
         "track": 300, "distance_nm": 4.2, "type": "E75L"},
        {"callsign": "UAL1597", "hex": "A3", "lat": 37.68, "lon": -122.25, "altitude_ft": 9000, "speed_kt": 250,
         "track": 280, "distance_nm": 6.1, "route": "CMH-SFO", "origin": "CMH", "destination": "SFO", "type": "B738",
         "registration": "N76533"},
    ]
    snapshot["metar"] = flight_radar_module.parse_metar(METAR_ROWS)
    snapshot["airports"] = {"SFO": {"kind": "gdp", "minutes": 63, "reason": "low ceilings"}}

    ordered, _center = pro_view.strip_order(plugin, snapshot)
    # Airborne with a route, then airborne without, then on the ground.
    assert [plane["callsign"] for plane in ordered] == ["UAL1597", "SKW5519", "JBU115"]
    assert pro_view._route(plugin, ordered[0]).endswith("SFO")
    # Official marks are bundled with transparent backgrounds: airlines' wordmarks and the airports' logos.
    united = pro_view.airline_logo("UAL", 64, 28)
    assert united is not None and united.mode == "RGBA" and united.getpixel((0, 0))[3] == 0
    assert pro_view.airline_logo("ZZZ", 64, 28) is None
    assert all(pro_view.airport_logo(code, 16, 64) is not None for code in flight_radar_module.FAA_AIRPORTS)

    now = datetime(2026, 10, 25, 9, 12, tzinfo=flight_radar_module.ZoneInfo("America/Los_Angeles"))
    image = plugin._render(snapshot, (800, 480), {"_inkypi_theme": _canonical_theme("day")}, now)
    theme = plugin._theme(_canonical_theme("day"))

    assert image.size == (800, 480)
    # SFO's IFR chip is red on the airport board.
    x0, y0, _x1, _y1 = pro_view.BOARD
    chip = [image.getpixel((x, y0 + 12)) for x in range(int(x0) + 62, int(x0) + 100)]
    assert theme["red"] in chip
    # The classic layout is still there when asked for.
    classic = plugin._render(snapshot, (800, 480), {"_inkypi_theme": _canonical_theme("day"), "layout": "classic"}, now)
    assert classic.tobytes() != image.tobytes()


def test_strips_and_scope_carry_airport_marks_phase_icons_and_the_chart():
    from plugins.flight_radar import pro_view

    # An airport's own logo where Wikidata has one, else its country's flag, else nothing.
    assert pro_view.route_mark("ORD", 14, 34) is not None
    assert pro_view.route_mark("MEM", 14, 34) is not None  # the United States flag
    assert pro_view.route_mark("ZZZ", 14, 34) is None
    manifest, layers, features = pro_view._chart()
    assert layers.size == tuple(manifest["image_size"])
    idents = {navaid["ident"] for navaid in features["navaids"]}
    assert {"SFO", "OAK", "SJC", "OSI"} <= idents
    assert {airport["iata"] for airport in features["airports"]} >= {"SFO", "OAK", "SJC"}

    plugin = _plugin()
    theme = plugin._theme(_canonical_theme("day"))
    image = Image.new("RGB", (60, 40), theme["panel"])
    draw = ImageDraw.Draw(image)
    pro_view._phase_icon(plugin, draw, (4, 4, 30, 30), "departure", theme)
    # A plain icon: the aircraft and runway in the phase colour, no plate behind them.
    assert image.getpixel((5, 5)) == theme["panel"]
    assert theme["red"] in [image.getpixel((x, y)) for x in range(4, 30) for y in range(4, 30)]

    snapshot = _sample_snapshot()
    now = datetime(2026, 10, 25, 9, 12, tzinfo=flight_radar_module.ZoneInfo("America/Los_Angeles"))
    charted = plugin._render(snapshot, (800, 480), {"_inkypi_theme": _canonical_theme("day")}, now)
    plain = plugin._render(snapshot, (800, 480), {"_inkypi_theme": _canonical_theme("day"), "mapMode": "none"}, now)
    # The chart paints the Bay south of the scope's centre; with no map the panel stays plain there.
    x, y = 270, 300
    assert charted.getpixel((x, y)) != plain.getpixel((x, y))


def test_scope_frames_the_three_bay_airports_on_a_terrain_chart():
    from plugins.flight_radar import pro_view

    plugin = _plugin()
    snapshot = _sample_snapshot()
    lat, lon, radius = pro_view.scope_view(plugin, snapshot, {})
    assert radius < flight_radar_module.DEFAULT_DISPLAY_RADIUS_NM
    # SFO, OAK and SJC all sit inside the ring, at least a mile and a half in from it.
    for airport in pro_view.BAY_AIRPORTS.values():
        assert plugin._distance_nm(lat, lon, *airport) < radius - 1.5
    # The settings form's defaults keep that view; the page's own centre or range replaces it.
    defaults = {"latitude": "37.6213", "longitude": "-122.3790", "displayRadiusNm": "20"}
    assert pro_view.scope_view(plugin, snapshot, defaults) == (lat, lon, radius)
    assert pro_view.scope_view(plugin, snapshot, {"displayRadiusNm": "30"}) == (lat, lon, 30)
    own = pro_view.scope_view(plugin, {**snapshot, "center": {"lat": 37.7, "lon": -122.2}}, {"latitude": "37.7",
                                                                                             "longitude": "-122.2"})
    assert own == (37.7, -122.2, flight_radar_module.DEFAULT_DISPLAY_RADIUS_NM)

    # The chart covers that view and carries 3DEP heights and the summits' spot elevations.
    manifest, layers, features = pro_view._chart()
    bounds = manifest["bounds_wgs84"]
    assert bounds["south"] < lat - radius / 60 and bounds["north"] > lat + radius / 60
    heights = layers.getchannel("B")
    assert heights.getextrema()[1] * manifest["elevation_step_ft"] > 3000
    assert any(spot["feet"] > 2000 for spot in features["spots"])
    # Contours: one line where the height crosses a band; a double line for an index contour.
    ramp = Image.linear_gradient("L").resize((20, 40))
    assert 0 < pro_view._contour_lines(ramp, 64, False).histogram()[255] < pro_view._contour_lines(ramp, 64, True).histogram()[255]
    assert pro_view._contour_lines(Image.new("L", (20, 20), 30), 10, True).getextrema() == (0, 0)


def test_strips_estimate_landing_and_take_off_times_for_bay_flights():
    from plugins.flight_radar import pro_view

    plugin = _plugin()
    now = datetime(2026, 10, 25, 9, 30, tzinfo=flight_radar_module.ZoneInfo("America/Los_Angeles"))
    # 12 NM east of SFO at 180 kt, inbound: about 6.6 minutes with the approach.
    arrival = {"lat": 37.6213, "lon": -122.1270, "speed_kt": 180, "origin": "LAX", "destination": "SFO"}
    departure = {"lat": 37.6213, "lon": -122.1270, "speed_kt": 240, "origin": "SFO", "destination": "OGG"}
    overflight = {"lat": 37.6, "lon": -122.1, "speed_kt": 400, "origin": "SEA", "destination": "LAX"}

    assert pro_view.flight_time(plugin, arrival, now) == "预计 09:36 到"
    assert pro_view.flight_time(plugin, departure, now) == "约 09:25 起飞"
    assert pro_view.flight_time(plugin, overflight, now) == ""
    assert pro_view.flight_time(plugin, {**arrival, "on_ground": True}, now) == ""


def test_strips_show_each_route_as_its_progress_between_two_big_codes():
    from plugins.flight_radar import pro_view

    # The aircraft sits by its phase: just out, passing, nearly in; on the ground at its Bay end.
    assert pro_view._strip_progress("departure", {}) < pro_view._strip_progress("overflight", {}) \
        < pro_view._strip_progress("arrival", {})
    assert pro_view._strip_progress("ground", {"origin": "SFO", "destination": "JFK"}) == 0.0
    assert pro_view._strip_progress("ground", {"origin": "JFK", "destination": "SFO"}) == 1.0

    plugin = _plugin()
    theme = plugin._theme(_canonical_theme("day"))
    plane = {"callsign": "UAL737", "origin": "SEA", "destination": "SFO", "lat": 37.71, "lon": -122.2,
             "speed_kt": 176}
    image = Image.new("RGB", (300, 40), theme["panel2"])
    pro_view._strip_route(plugin, image, ImageDraw.Draw(image), plane, "arrival", (10, 4, 290, 36), "预计 13:18 到",
                          theme)
    # Both codes in ink at the ends, the route line and the aircraft in the arrival colour between.
    assert theme["ink"] in set(image.crop((10, 4, 70, 26)).get_flattened_data())
    assert theme["ink"] in set(image.crop((230, 4, 290, 26)).get_flattened_data())
    assert theme["blue"] in set(image.crop((100, 4, 230, 26)).get_flattened_data())
