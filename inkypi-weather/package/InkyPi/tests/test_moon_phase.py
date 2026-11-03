import json
import sys
import threading
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from plugins.moon_phase import lunar_phases, sky_times
from plugins.moon_phase.moon_phase import (
    NEW_MOON_EPOCH_UTC,
    MoonPhase,
    calculate_moon_info,
)
from runtime.refresh_contracts import TaskContext

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "moon_phase" / "usno_phases_2026.json"
KINDS = {"New Moon": 0.0, "First Quarter": 0.25, "Full Moon": 0.5, "Last Quarter": 0.75}
HOME_CONFIG = {"playlist_config": {"playlists": [{"plugins": [
    {"plugin_id": "clock", "plugin_settings": {}},
    {"plugin_id": "weather", "plugin_settings": {"latitude": "37.5485", "longitude": "-121.9886"}},
]}]}}


class FakeDeviceConfig:
    def __init__(self, resolution=(800, 480), timezone_name="America/Los_Angeles", orientation="horizontal",
                 extra=None):
        self.resolution = resolution
        self.timezone_name = timezone_name
        self.orientation = orientation
        self.extra = extra or {}

    def get_resolution(self):
        return self.resolution

    def get_config(self, key=None, default=None):
        values = {
            "timezone": self.timezone_name,
            "orientation": self.orientation,
            **self.extra,
        }
        if key is None:
            return values
        return values.get(key, default)


def _usno_phases():
    rows = json.loads(FIXTURE.read_text(encoding="utf-8"))["phasedata"]
    for row in rows:
        hour, minute = map(int, row["time"].split(":"))
        yield KINDS[row["phase"]], datetime(row["year"], row["month"], row["day"], hour, minute, tzinfo=timezone.utc)


def test_true_phases_match_a_year_of_usno_times():
    phases = list(_usno_phases())
    assert len(phases) == 50
    for kind, usno in phases:
        ours = lunar_phases.next_phase(usno - timedelta(days=3), kind)
        assert abs((ours - usno).total_seconds()) <= 120, (kind, usno, ours)


def test_next_phases_from_2026_11_02_follow_usno():
    info = calculate_moon_info(datetime(2026, 11, 2, 23, 30, tzinfo=timezone.utc))

    expected = {
        "next_new_utc": datetime(2026, 11, 9, 7, 2, tzinfo=timezone.utc),
        "next_first_quarter_utc": datetime(2026, 11, 17, 11, 48, tzinfo=timezone.utc),
        "next_full_utc": datetime(2026, 11, 24, 14, 53, tzinfo=timezone.utc),
        "next_last_quarter_utc": datetime(2026, 12, 1, 6, 8, tzinfo=timezone.utc),
    }
    for field, usno in expected.items():
        assert abs((getattr(info, field) - usno).total_seconds()) <= 120, field
    # The new moon is on the evening of 11-08 in Pacific time, not 11-09.
    assert info.next_new_utc.astimezone(timezone(timedelta(hours=-8))).date() == date(2026, 11, 8)
    assert [kind for _, kind in info.upcoming] == [0.0, 0.25, 0.5, 0.75]
    assert info.phase_name == "Waning Crescent"
    assert info.direction == "Waning"
    assert abs(info.age_days - 23.32) < 0.02  # since the 10-10 15:50 UTC new moon
    # USNO lists 39% for this day (its value is for local noon, 20:00 UTC).
    assert abs(calculate_moon_info(datetime(2026, 11, 2, 20, tzinfo=timezone.utc)).illumination - 0.39) < 0.01


def test_principal_phase_instants_name_the_phase_and_light_the_disc():
    for kind, usno in _usno_phases():
        info = calculate_moon_info(usno + timedelta(minutes=5))
        name = {0.0: "New Moon", 0.25: "First Quarter", 0.5: "Full Moon", 0.75: "Last Quarter"}[kind]
        assert info.phase_name == name
        assert abs(info.illumination - {0.0: 0.0, 0.25: 0.5, 0.5: 1.0, 0.75: 0.5}[kind]) < 0.03
        assert abs(((info.phase_fraction - kind) + 0.5) % 1.0 - 0.5) < 0.01
        assert info.direction == ("Waxing" if kind in (0.0, 0.25) else "Waning")


def test_new_moon_epoch_has_dark_new_phase():
    info = calculate_moon_info(NEW_MOON_EPOCH_UTC + timedelta(minutes=1))

    assert info.phase_name == "New Moon"
    assert info.age_days < 0.01
    assert info.illumination < 0.001
    assert info.next_full_utc > NEW_MOON_EPOCH_UTC
    assert info.next_new_utc > NEW_MOON_EPOCH_UTC + timedelta(days=29)


def test_sky_table_parses_usno_and_drops_missing_events():
    payload = {"properties": {"data": {
        "moondata": [{"phen": "Upper Transit", "time": "06:51"}, {"phen": "Set", "time": "13:47"}],
        "sundata": [{"phen": "Begin Civil Twilight", "time": "06:07"}, {"phen": "Rise", "time": "06:34"},
                    {"phen": "Set", "time": "17:08"}, {"phen": "End Civil Twilight", "time": "17:36"}],
    }}}
    table = sky_times.parse_oneday(payload, date(2026, 11, 2))

    assert table == {"date": "2026-11-02", "moonset": "13:47", "transit": "06:51", "civil_begin": "06:07",
                     "civil_end": "17:36", "sunrise": "06:34", "sunset": "17:08"}
    assert sky_times.valid_sky_times(table)
    assert not sky_times.valid_sky_times({"date": "2026-11-02"})
    assert not sky_times.valid_sky_times({"date": "2026-11-02", "moonset": "25:00"})


def test_home_comes_from_the_page_or_the_weather_page():
    assert sky_times.find_home({"latitude": "40.1", "longitude": "-74"}, FakeDeviceConfig()) == (40.1, -74.0)
    assert sky_times.find_home({}, FakeDeviceConfig(extra=HOME_CONFIG)) == (37.5485, -121.9886)
    assert sky_times.find_home({}, FakeDeviceConfig()) is None


def test_sky_times_request_uses_the_days_full_offset(monkeypatch):
    calls = []

    class Client:
        def request_json(self, method, url, **kwargs):
            calls.append((url, kwargs["params"]))
            return type("R", (), {"data": {"properties": {"data": {
                "moondata": [{"phen": "Rise", "time": "22:08"}], "sundata": []}}}})()

    monkeypatch.setattr(sky_times, "get_http_client", lambda: Client())
    context = TaskContext(threading.Event(), time.monotonic() + 30, time.monotonic)
    table = sky_times.fetch_sky_times((37.5485, -121.9886), date(2026, 7, 1), -7.0, context)

    assert table == {"date": "2026-07-01", "moonrise": "22:08"}
    assert calls == [(sky_times.USNO_ONEDAY_URL, {"date": "2026-07-01", "coords": "37.5485,-121.9886",
                                                  "tz": "-7", "dst": "false"})]


def test_page_without_home_never_touches_the_network(monkeypatch, tmp_path):
    monkeypatch.setenv("MOON_PHASE_CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(sky_times, "fetch_sky_times", lambda *args: pytest.fail("no home, no request"))
    image = MoonPhase({"id": "moon_phase"}).generate_image(
        {"debugNowUtc": "2026-11-02T23:30:00+00:00"}, FakeDeviceConfig())
    assert image.size == (800, 480)


def test_sky_table_is_fetched_once_a_day_and_cache_only_redraws_stay_offline(monkeypatch, tmp_path):
    monkeypatch.setenv("MOON_PHASE_CACHE_DIR", str(tmp_path))
    calls = []

    def fake_fetch(home, day, offset, context):
        calls.append((home, day, offset))
        return {"date": day.isoformat(), "moonset": "13:47", "civil_begin": "06:07", "civil_end": "17:36"}

    monkeypatch.setattr(sky_times, "fetch_sky_times", fake_fetch)
    plugin = MoonPhase({"id": "moon_phase"})
    device = FakeDeviceConfig(extra=HOME_CONFIG)
    settings = {"debugNowUtc": "2026-11-02T23:30:00+00:00"}
    plugin.generate_image(settings, device)
    plugin.generate_image(settings, device)
    assert calls == [((37.5485, -121.9886), date(2026, 11, 2), -8.0)]

    monkeypatch.setattr(sky_times, "fetch_sky_times", lambda *args: pytest.fail("cache-only redraw"))
    plugin.generate_image({**settings, "debugNowUtc": "2026-11-03T20:00:00+00:00", "_theme_render_only": True},
                          device)


def test_failed_sky_request_hides_the_rows(monkeypatch, tmp_path):
    monkeypatch.setenv("MOON_PHASE_CACHE_DIR", str(tmp_path))

    def broken(*args):
        raise ValueError("offline")

    monkeypatch.setattr(sky_times, "fetch_sky_times", broken)
    plugin = MoonPhase({"id": "moon_phase"})
    now = datetime(2026, 11, 2, 23, 30, tzinfo=timezone.utc)
    assert plugin._sky_times({}, FakeDeviceConfig(extra=HOME_CONFIG), now) == {}
    image = plugin.generate_image({"debugNowUtc": now.isoformat()}, FakeDeviceConfig(extra=HOME_CONFIG))
    assert image.getbbox() is not None


def test_generate_image_returns_nonblank_landscape_render():
    plugin = MoonPhase({"id": "moon_phase"})

    image = plugin.generate_image(
        {"debugNowUtc": "2026-05-28T12:00:00+00:00",
         "skyTimes": {"date": "2026-05-28", "moonrise": "13:02", "moonset": "01:40",
                      "civil_begin": "05:21", "civil_end": "20:52"}},
        FakeDeviceConfig(),
    )

    assert isinstance(image, Image.Image)
    assert image.mode == "RGB"
    assert image.size == (800, 480)
    assert len(image.getcolors(maxcolors=800 * 480)) > 20
    assert image.getbbox() is not None


def test_generate_image_respects_vertical_orientation():
    plugin = MoonPhase({"id": "moon_phase"})

    image = plugin.generate_image(
        {"debugNowUtc": datetime(2026, 5, 28, 12, tzinfo=timezone.utc), "themeMode": "day"},
        FakeDeviceConfig(resolution=(800, 480), orientation="vertical"),
    )

    assert image.size == (480, 800)
