from datetime import date, datetime, timedelta, timezone
import json
import logging
import os
import sys
import time
from pathlib import Path

import icalendar
import pytest
import pytz
from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from plugins.simple_calendar import simple_calendar as calendar_module
from plugins.simple_calendar.simple_calendar import LOCALE_DATA, SimpleCalendar
from plugins.base_plugin.presentation import (
    PresentationMode,
    PresentationRequestContext,
)
from plugins.base_plugin.render_provenance import (
    SourceProvenance,
    read_source_provenance,
)


class PresentationDeviceConfig:
    def get_config(self, key=None, default=None):
        values = {
            "orientation": "horizontal",
            "timezone": "America/Los_Angeles",
        }
        if key is None:
            return values
        return values.get(key, default)

    def get_resolution(self):
        return (800, 480)


def _presentation_request(requested_at):
    return PresentationRequestContext(
        request_id="a" * 32,
        requested_at=requested_at.isoformat(),
        origin_display_commit_id="display-before-calendar-redraw",
        last_receipt=None,
    )


def _calendar_theme(mode):
    palettes = {
        "day": {
            "background": (245, 240, 229),
            "panel": (236, 230, 215),
            "ink": (20, 27, 31),
            "muted": (75, 82, 84),
            "rule": (160, 154, 137),
            "accent": (46, 106, 118),
        },
        "night": {
            "background": (11, 21, 24),
            "panel": (18, 32, 36),
            "ink": (236, 243, 239),
            "muted": (172, 187, 181),
            "rule": (72, 91, 87),
            "accent": (105, 185, 197),
        },
    }
    return {"mode": mode, "palette": palettes[mode]}


def test_default_us_cn_holiday_sources_when_enabled():
    plugin = SimpleCalendar({"id": "simple_calendar"})

    sources = plugin._get_holiday_sources({
        "showHolidays": "true",
        "holidayPreset": "us_cn",
    })

    assert [source["label"] for source in sources] == ["US", "CN"]
    assert all(source["url"].startswith("https://calendar.google.com/calendar/ical/") for source in sources)


def test_settings_template_persists_refresh_on_display_default():
    settings_path = Path(__file__).resolve().parents[1] / "src" / "plugins" / "simple_calendar" / "settings.html"
    html = settings_path.read_text(encoding="utf-8")

    assert 'name="refreshOnDisplay"' in html
    assert 'value="true"' in html



def test_extract_holiday_events_for_selected_month():
    plugin = SimpleCalendar({"id": "simple_calendar"})
    cal = icalendar.Calendar.from_ical(
        b"""BEGIN:VCALENDAR
BEGIN:VEVENT
DTSTART;VALUE=DATE:20260525
DTEND;VALUE=DATE:20260526
SUMMARY:Memorial Day
END:VEVENT
BEGIN:VEVENT
DTSTART;VALUE=DATE:20260619
DTEND;VALUE=DATE:20260620
SUMMARY:Juneteenth
END:VEVENT
END:VCALENDAR
"""
    )

    events = plugin._extract_holiday_events(
        cal,
        {"label": "US", "color": (52, 89, 149)},
        date(2026, 5, 28),
        pytz.timezone("America/Los_Angeles"),
    )

    assert events == [{
        "date": date(2026, 5, 25),
        "title": "Memorial Day",
        "label": "US",
        "color": (52, 89, 149),
        "kind": "holiday",
        "time": "",
    }]


def test_holiday_preset_off_disables_url_fetch(monkeypatch):
    plugin = SimpleCalendar({"id": "simple_calendar"})

    def fail_fetch(*args, **kwargs):
        raise AssertionError("holiday fetch should not run")

    monkeypatch.setattr(plugin, "_fetch_holiday_events", fail_fetch)

    events = plugin._get_holiday_events(
        {
            "holidayPreset": "off",
            "holidayCalendarURLs[]": ["https://example.com/holiday.ics"],
        },
        date(2026, 5, 28),
        pytz.timezone("America/Los_Angeles"),
    )

    assert events == []


def test_weather_code_maps_to_generated_panel_backgrounds():
    plugin = SimpleCalendar({"id": "simple_calendar"})

    assert plugin._weather_code_to_background_slug(0, 1) == "clear_day"
    assert plugin._weather_code_to_background_slug(0, 0) == "clear_night"
    assert plugin._weather_code_to_background_slug(63, 1) == "rain"
    assert plugin._weather_code_to_background_slug(73, 1) == "snow"
    assert plugin._weather_code_to_background_slug(95, 1) == "thunderstorm"


def test_weather_background_classic_style_uses_simple_calendar_panel_assets():
    plugin = SimpleCalendar({"id": "simple_calendar"})

    path = Path(plugin._weather_background_path("rain", {"weatherPanelBackgroundStyle": "classic"}))

    assert path.name == "rain.png"
    assert path.parent.name == "weather_panel_backgrounds"
    assert path.is_file()


def test_weather_background_default_uses_mixed_comic_pool():
    plugin = SimpleCalendar({"id": "simple_calendar"})

    path = Path(plugin._weather_background_path("clear_day", {}, date(2026, 5, 28)))

    assert "weather_panel_backgrounds_color" in path.parts
    assert path.parent.name in {
        "img2_original_heroes",
        "img2_original_heroes_weather",
        "img2_original_heroes_nyc_weather",
        "img2_original_heroes_local_top_weather",
    }
    assert path.is_file()


def test_weather_background_style_can_use_color_variant_pool():
    plugin = SimpleCalendar({"id": "simple_calendar"})

    path = Path(plugin._weather_background_path(
        "rain",
        {"weatherPanelBackgroundStyle": "img2_original_heroes_mixed"},
        date(2026, 5, 28),
    ))

    assert "weather_panel_backgrounds_color" in path.parts
    assert path.stem.startswith("rain")
    assert path.suffix == ".png"
    assert path.parent.name in {
        "img2_original_heroes",
        "img2_original_heroes_weather",
        "img2_original_heroes_nyc_weather",
        "img2_original_heroes_local_top_weather",
    }
    assert path.is_file()


def test_weather_background_mixed_style_includes_suffix_named_hero_assets():
    plugin = SimpleCalendar({"id": "simple_calendar"})

    candidates = plugin._weather_background_candidates("clear_day", "img2_original_heroes_mixed")

    assert any(
        path.parent.name == "img2_original_heroes" and path.name.endswith("_clear_day.png")
        for path in candidates
    )


def test_weather_background_variant_selection_is_stable_for_date():
    plugin = SimpleCalendar({"id": "simple_calendar"})
    settings = {"weatherPanelBackgroundStyle": "img2_original_heroes_mixed"}

    first = plugin._weather_background_path("clear_day", settings, date(2026, 5, 28))
    second = plugin._weather_background_path("clear_day", settings, date(2026, 5, 28))

    assert first == second


def test_weather_background_variant_selection_rotates_by_date():
    plugin = SimpleCalendar({"id": "simple_calendar"})
    settings = {"weatherPanelBackgroundStyle": "img2_original_heroes_mixed"}

    first = plugin._weather_background_path("clear_day", settings, date(2026, 5, 28))
    next_day = plugin._weather_background_path("clear_day", settings, date(2026, 5, 29))

    assert first != next_day


def test_date_hero_overlay_defaults_to_comic_weather_styles():
    plugin = SimpleCalendar({"id": "simple_calendar"})

    assert plugin._date_hero_overlay_enabled({}) is True
    assert plugin._date_hero_overlay_enabled({"weatherPanelBackgroundStyle": "classic"}) is False
    assert plugin._date_hero_overlay_enabled({"weatherPanelBackgroundStyle": "img2_original_heroes_weather"}) is True
    assert plugin._date_hero_overlay_enabled({
        "weatherPanelBackgroundStyle": "img2_original_heroes_weather",
        "dateHeroOverlays": "false",
    }) is False


def test_date_hero_cutout_selection_rotates_by_date():
    plugin = SimpleCalendar({"id": "simple_calendar"})

    first = Path(plugin._date_hero_cutout_path(date(2026, 5, 28))).name
    next_day = Path(plugin._date_hero_cutout_path(date(2026, 5, 29))).name

    assert first != next_day


def test_date_hero_cutouts_are_transparent_pngs():
    plugin = SimpleCalendar({"id": "simple_calendar"})
    paths = plugin._date_hero_cutout_paths()

    assert len(paths) >= 7
    for path in paths:
        image = Image.open(path)
        assert image.mode == "RGBA"
        assert image.getpixel((0, 0))[3] == 0
        assert image.getpixel((image.width - 1, image.height - 1))[3] == 0


def test_weather_source_settings_can_come_from_playlist_config():
    plugin = SimpleCalendar({"id": "simple_calendar"})
    device_config = {
        "playlist_config": {
            "playlists": [
                {
                    "plugins": [
                        {
                            "plugin_id": "mini_weather",
                            "plugin_settings": {
                                "latitude": "34.0522",
                                "longitude": "-118.2437",
                            },
                        }
                    ]
                }
            ]
        }
    }

    settings = plugin._find_weather_source_settings({}, device_config)

    assert settings["latitude"] == "34.0522"
    assert settings["longitude"] == "-118.2437"


def test_personal_google_calendar_sources_are_ics_urls():
    plugin = SimpleCalendar({"id": "simple_calendar"})

    sources = plugin._get_personal_calendar_sources({
        "showPersonalCalendars": "true",
        "personalCalendarLabels[]": ["ME"],
        "personalCalendarColors[]": ["#2e7d32"],
        "personalCalendarURLs[]": ["https://calendar.google.com/calendar/ical/private/basic.ics"],
    })

    assert sources == [{
        "url": "https://calendar.google.com/calendar/ical/private/basic.ics",
        "label": "ME",
        "color": (46, 125, 50),
        "kind": "personal",
    }]


def test_extract_personal_calendar_event_includes_time_label():
    plugin = SimpleCalendar({"id": "simple_calendar"})
    tz = pytz.timezone("America/Los_Angeles")
    cal = icalendar.Calendar.from_ical(
        b"""BEGIN:VCALENDAR
BEGIN:VEVENT
DTSTART:20260528T153000Z
DTEND:20260528T163000Z
SUMMARY:Dentist
END:VEVENT
END:VCALENDAR
"""
    )

    events = plugin._extract_holiday_events(
        cal,
        {"label": "ME", "color": (46, 125, 50), "kind": "personal"},
        date(2026, 5, 28),
        tz,
    )

    assert events == [{
        "date": date(2026, 5, 28),
        "title": "Dentist",
        "label": "ME",
        "color": (46, 125, 50),
        "kind": "personal",
        "time": "8:30a",
        "starts_at": tz.localize(datetime(2026, 5, 28, 8, 30)),
    }]


def test_fetch_personal_calendar_can_read_file_url(tmp_path, monkeypatch):
    plugin = SimpleCalendar({"id": "simple_calendar"})
    tz = pytz.timezone("America/Los_Angeles")
    ics_path = tmp_path / "nintendo_direct.ics"
    ics_path.write_text(
        """BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VEVENT
DTSTART:20260609T140000Z
DTEND:20260609T145000Z
SUMMARY:Nintendo Direct + Treehouse Live
END:VEVENT
END:VCALENDAR
""",
        encoding="utf-8",
    )

    monkeypatch.setattr(
        "plugins.simple_calendar.simple_calendar.get_http_session",
        lambda: (_ for _ in ()).throw(AssertionError("network called")),
    )
    events = plugin._fetch_holiday_events(
        {
            "url": ics_path.as_uri(),
            "label": "NIN",
            "color": (230, 0, 18),
            "kind": "personal",
        },
        date(2026, 6, 8),
        tz,
    )

    assert events == [{
        "date": date(2026, 6, 9),
        "title": "Nintendo Direct + Treehouse Live",
        "label": "NIN",
        "color": (230, 0, 18),
        "kind": "personal",
        "time": "7a",
        "starts_at": tz.localize(datetime(2026, 6, 9, 7, 0)),
    }]


def test_legacy_calendar_file_url_prefers_durable_data_copy(tmp_path, monkeypatch):
    plugin = SimpleCalendar({"id": "simple_calendar"})
    legacy_dir = tmp_path / "legacy" / "static" / "calendar"
    data_dir = tmp_path / "data"
    durable_file = (
        data_dir
        / "plugins"
        / "simple_calendar"
        / "calendars"
        / "nintendo_direct.ics"
    )
    durable_file.parent.mkdir(parents=True)
    durable_file.write_bytes(b"BEGIN:VCALENDAR\nEND:VCALENDAR\n")

    monkeypatch.setenv("INKYPI_DATA_DIR", os.fspath(data_dir))
    monkeypatch.setattr(
        "plugins.simple_calendar.simple_calendar.LEGACY_CALENDAR_DIR",
        legacy_dir,
    )
    monkeypatch.setattr(
        "plugins.simple_calendar.simple_calendar.get_http_session",
        lambda: (_ for _ in ()).throw(AssertionError("network called")),
    )

    content = plugin._read_calendar_source(
        (legacy_dir / "nintendo_direct.ics").as_uri()
    )

    assert content == b"BEGIN:VCALENDAR\nEND:VCALENDAR\n"


def test_legacy_calendar_path_mapping_rejects_parent_traversal(tmp_path, monkeypatch):
    plugin = SimpleCalendar({"id": "simple_calendar"})
    legacy_dir = tmp_path / "legacy" / "static" / "calendar"
    data_dir = tmp_path / "data"
    durable_file = (
        data_dir / "plugins" / "simple_calendar" / "calendars" / "secret.ics"
    )
    durable_file.parent.mkdir(parents=True)
    durable_file.write_bytes(b"not reachable")

    monkeypatch.setenv("INKYPI_DATA_DIR", os.fspath(data_dir))
    monkeypatch.setattr(
        "plugins.simple_calendar.simple_calendar.LEGACY_CALENDAR_DIR",
        legacy_dir,
    )

    with pytest.raises(FileNotFoundError):
        plugin._read_calendar_source((legacy_dir / ".." / "secret.ics").as_uri())


def test_legacy_calendar_mapping_never_probes_drive_like_name_outside_durable_root(
    tmp_path, monkeypatch
):
    plugin = SimpleCalendar({"id": "simple_calendar"})
    legacy_dir = tmp_path / "legacy" / "static" / "calendar"
    data_dir = tmp_path / "data"
    durable_root = data_dir / "plugins" / "simple_calendar" / "calendars"
    candidates = []

    monkeypatch.setenv("INKYPI_DATA_DIR", os.fspath(data_dir))
    monkeypatch.setattr(
        "plugins.simple_calendar.simple_calendar.LEGACY_CALENDAR_DIR",
        legacy_dir,
    )
    monkeypatch.setattr(
        Path,
        "is_file",
        lambda candidate: candidates.append(candidate) or False,
    )

    legacy_url = f"{legacy_dir.as_uri()}/C%3Atarget.ics"
    with pytest.raises(FileNotFoundError):
        plugin._read_calendar_source(legacy_url)

    assert len(candidates) <= 1
    assert all(candidate.is_relative_to(durable_root) for candidate in candidates)


def test_remote_calendar_source_uses_shared_http_session(monkeypatch):
    plugin = SimpleCalendar({"id": "simple_calendar"})
    calls = []

    class FakeResponse:
        content = b"BEGIN:VCALENDAR\nEND:VCALENDAR\n"

        def raise_for_status(self):
            calls.append(("raise_for_status",))

    class FakeSession:
        def get(self, url, timeout, headers, allow_redirects):
            calls.append(
                (url, timeout, headers.get("User-Agent"), allow_redirects)
            )
            return FakeResponse()

    monkeypatch.setattr(
        "plugins.simple_calendar.simple_calendar.get_http_session",
        lambda: FakeSession(),
    )

    content = plugin._read_calendar_source(
        "https://calendar.google.com/calendar/ical/private/basic.ics"
    )

    assert content == b"BEGIN:VCALENDAR\nEND:VCALENDAR\n"
    assert calls == [
        (
            "https://calendar.google.com/calendar/ical/private/basic.ics",
            20,
            "InkyPi SimpleCalendar/1.0",
            False,
        ),
        ("raise_for_status",),
    ]


def test_remote_calendar_source_rejects_redirect_to_private_network(monkeypatch):
    plugin = SimpleCalendar({"id": "simple_calendar"})
    calls = []

    class RedirectResponse:
        status_code = 302
        headers = {"Location": "http://127.0.0.1/private.ics"}
        content = b""

        def raise_for_status(self):
            raise AssertionError("redirect response was treated as final content")

    class FakeSession:
        def get(self, url, **kwargs):
            calls.append((url, kwargs))
            return RedirectResponse()

    monkeypatch.setattr(
        "plugins.simple_calendar.simple_calendar.get_http_session",
        lambda: FakeSession(),
    )

    with pytest.raises(RuntimeError, match="redirect"):
        plugin._read_calendar_source("https://calendar.example/private.ics?token=secret")

    assert [call[0] for call in calls] == [
        "https://calendar.example/private.ics?token=secret"
    ]


def _remote_calendar_settings():
    return {
        "showHolidays": "true",
        "holidayPreset": "custom",
        "holidayCalendarURLs[]": ["https://example.com/holidays.ics"],
        "holidayCalendarLabels[]": ["HOL"],
        "holidayCalendarColors[]": ["#345995"],
        "showPersonalCalendars": "true",
        "personalCalendarURLs[]": ["https://example.com/personal.ics"],
        "personalCalendarLabels[]": ["ME"],
        "personalCalendarColors[]": ["#2e7d32"],
    }


def _month_event(source, selected_date, tz):
    del tz
    return {
        "date": date(selected_date.year, selected_date.month, 4),
        "title": f"{source['label']} {selected_date:%Y-%m}",
        "label": source["label"],
        "color": source["color"],
        "kind": source["kind"],
        "time": "",
    }


def test_simple_calendar_declares_receipt_free_prepared_bank():
    plugin = SimpleCalendar({"id": "simple_calendar"})

    assert plugin.presentation_mode({}) is PresentationMode.PREPARED_BANK
    assert plugin.reconcile_presentation_receipt({}, None) is None


def test_data_refresh_writes_current_and_next_month_snapshots(tmp_path, monkeypatch):
    monkeypatch.setenv("INKYPI_DATA_DIR", os.fspath(tmp_path / "data"))
    plugin = SimpleCalendar({"id": "simple_calendar"})
    tz = pytz.timezone("America/Los_Angeles")
    calls = []

    def fetch(sources, selected_date, selected_tz):
        calls.append(selected_date)
        return (
            [_month_event(source, selected_date, selected_tz) for source in sources],
            [],
        )

    monkeypatch.setattr(plugin, "_fetch_calendar_sources", fetch)

    events, provenance = plugin._refresh_calendar_event_snapshots(
        _remote_calendar_settings(), date(2026, 7, 31), tz
    )

    assert calls == [date(2026, 7, 31), date(2026, 8, 1)]
    assert [event["date"] for event in events] == [
        date(2026, 7, 4),
        date(2026, 7, 4),
    ]
    assert provenance is SourceProvenance.LIVE
    payloads = [
        json.loads(path.read_text(encoding="utf-8"))
        for path in (tmp_path / "data" / "plugins" / "simple_calendar" / "event_snapshots").glob("*.json")
    ]
    assert {payload["month"] for payload in payloads} == {"2026-07", "2026-08"}
    assert {payload["data_month"] for payload in payloads} == {"2026-07"}


def test_presentation_reads_current_and_next_snapshots_without_provider_or_state_writes(
    tmp_path, monkeypatch
):
    data_root = tmp_path / "data"
    context_root = tmp_path / "context"
    context_root.mkdir()
    monkeypatch.setenv("INKYPI_DATA_DIR", os.fspath(data_root))
    monkeypatch.setenv("INKYPI_CONTEXT_CACHE_DIR", os.fspath(context_root))
    plugin = SimpleCalendar({"id": "simple_calendar"})
    tz = pytz.timezone("America/Los_Angeles")
    settings = {
        **_remote_calendar_settings(),
        "weatherPanelBackground": "false",
        "dateHeroOverlays": "false",
    }
    monkeypatch.setattr(
        plugin,
        "_fetch_calendar_sources",
        lambda sources, selected_date, selected_tz: (
            [_month_event(source, selected_date, selected_tz) for source in sources],
            [],
        ),
    )
    plugin._refresh_calendar_event_snapshots(settings, date(2026, 7, 11), tz)
    before = {
        path.relative_to(tmp_path): path.read_bytes()
        for path in tmp_path.rglob("*")
        if path.is_file()
    }
    reads = []
    original_read = plugin._read_event_snapshot_with_provenance

    def track_read(sources, selected_date, selected_tz):
        reads.append(selected_date)
        return original_read(sources, selected_date, selected_tz)

    monkeypatch.setattr(plugin, "_read_event_snapshot_with_provenance", track_read)
    monkeypatch.setattr(
        plugin,
        "_fetch_calendar_sources",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("presentation called a calendar provider")
        ),
    )
    monkeypatch.setattr(
        plugin,
        "_fetch_current_weather_background_slug",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("presentation called a weather provider")
        ),
    )

    preparation = plugin.prepare_presentation(
        settings,
        PresentationDeviceConfig(),
        request=_presentation_request(
            datetime(2026, 7, 12, 0, 5, tzinfo=timezone(timedelta(hours=-7)))
        ),
        resolved_theme_context=_calendar_theme("night"),
    )

    assert preparation.changed is True
    assert preparation.request_id == "a" * 32
    assert preparation.image.size == (800, 480)
    assert reads == [date(2026, 7, 12), date(2026, 8, 1)]
    assert not hasattr(preparation, "receipt") or preparation.receipt is None
    after = {
        path.relative_to(tmp_path): path.read_bytes()
        for path in tmp_path.rglob("*")
        if path.is_file()
    }
    assert after == before


def test_presentation_missing_current_month_snapshot_fails_closed_without_creating_state(
    tmp_path, monkeypatch
):
    data_root = tmp_path / "data"
    context_root = tmp_path / "context"
    monkeypatch.setenv("INKYPI_DATA_DIR", os.fspath(data_root))
    monkeypatch.setenv("INKYPI_CONTEXT_CACHE_DIR", os.fspath(context_root))
    plugin = SimpleCalendar({"id": "simple_calendar"})

    monkeypatch.setattr(
        plugin,
        "_fetch_calendar_sources",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("cold presentation called a calendar provider")
        ),
    )

    with pytest.raises(RuntimeError, match="current-month snapshot"):
        plugin.prepare_presentation(
            {**_remote_calendar_settings(), "weatherPanelBackground": "false"},
            PresentationDeviceConfig(),
            request=_presentation_request(
                datetime(2026, 8, 1, 0, 5, tzinfo=timezone(timedelta(hours=-7)))
            ),
            resolved_theme_context=_calendar_theme("night"),
        )

    assert not data_root.exists()
    assert not context_root.exists()


def test_cross_month_prefetch_is_rendered_with_stale_cache_provenance(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("INKYPI_DATA_DIR", os.fspath(tmp_path / "data"))
    monkeypatch.setenv("INKYPI_CONTEXT_CACHE_DIR", os.fspath(tmp_path / "context"))
    plugin = SimpleCalendar({"id": "simple_calendar"})
    tz = pytz.timezone("America/Los_Angeles")
    settings = {
        **_remote_calendar_settings(),
        "weatherPanelBackground": "false",
        "dateHeroOverlays": "false",
    }
    monkeypatch.setattr(
        plugin,
        "_fetch_calendar_sources",
        lambda sources, selected_date, selected_tz: (
            [_month_event(source, selected_date, selected_tz) for source in sources],
            [],
        ),
    )
    plugin._refresh_calendar_event_snapshots(settings, date(2026, 7, 31), tz)

    preparation = plugin.prepare_presentation(
        settings,
        PresentationDeviceConfig(),
        request=_presentation_request(
            datetime(2026, 8, 1, 0, 5, tzinfo=timezone(timedelta(hours=-7)))
        ),
        resolved_theme_context=_calendar_theme("night"),
    )

    assert read_source_provenance(preparation.image) is SourceProvenance.STALE_CACHE


def test_snapshot_cleanup_protects_current_and_next_month(tmp_path):
    plugin = SimpleCalendar({"id": "simple_calendar"})
    directory = tmp_path / "snapshots"
    directory.mkdir()
    current = directory / ("a" * 64 + ".json")
    next_month = directory / ("b" * 64 + ".json")
    expired = directory / ("c" * 64 + ".json")
    for path in (current, next_month, expired):
        path.write_text("{}", encoding="utf-8")
        old = time.time() - 63 * 24 * 60 * 60
        os.utime(path, (old, old))

    plugin._prune_event_snapshots(
        directory,
        protected_paths={current, next_month},
    )

    assert current.exists()
    assert next_month.exists()
    assert not expired.exists()


def test_remote_event_snapshot_replays_holiday_and_personal_events_without_network(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("INKYPI_DATA_DIR", os.fspath(tmp_path / "data"))
    plugin = SimpleCalendar({"id": "simple_calendar"})
    selected_date = date(2026, 7, 11)
    tz = pytz.timezone("America/Los_Angeles")
    calls = []

    def fetch(source, _selected_date, _tz):
        calls.append(source["url"])
        if source["kind"] == "holiday":
            return [{
                "date": date(2026, 7, 4),
                "title": "Independence Day",
                "label": source["label"],
                "color": source["color"],
                "kind": "holiday",
                "time": "",
            }]
        return [{
            "date": date(2026, 7, 14),
            "title": "Dentist",
            "label": source["label"],
            "color": source["color"],
            "kind": "personal",
            "time": "8:30a",
            "starts_at": tz.localize(datetime(2026, 7, 14, 8, 30)),
        }]

    monkeypatch.setattr(plugin, "_fetch_holiday_events", fetch)
    data_events = plugin._get_calendar_events(
        _remote_calendar_settings(), selected_date, tz
    )
    assert calls == [
        "https://example.com/holidays.ics",
        "https://example.com/personal.ics",
    ]

    def fail_network(*args, **kwargs):
        raise AssertionError("theme-only redraw attempted calendar network access")

    monkeypatch.setattr(plugin, "_fetch_holiday_events", fail_network)
    theme_events = plugin._get_calendar_events(
        _remote_calendar_settings(), selected_date, tz, allow_remote=False
    )

    assert theme_events == data_events
    assert {event["kind"] for event in theme_events} == {"holiday", "personal"}
    snapshot_files = list(
        (tmp_path / "data" / "plugins" / "simple_calendar" / "event_snapshots").glob("*.json")
    )
    assert len(snapshot_files) == 1
    snapshot_text = snapshot_files[0].read_text(encoding="utf-8")
    assert "example.com" not in snapshot_text


def test_corrupt_remote_event_snapshot_refuses_theme_only_redraw(tmp_path, monkeypatch):
    monkeypatch.setenv("INKYPI_DATA_DIR", os.fspath(tmp_path / "data"))
    plugin = SimpleCalendar({"id": "simple_calendar"})
    selected_date = date(2026, 7, 11)
    tz = pytz.timezone("America/Los_Angeles")
    monkeypatch.setattr(
        plugin,
        "_fetch_holiday_events",
        lambda source, *_args: [{
            "date": date(2026, 7, 4),
            "title": "Independence Day",
            "label": source["label"],
            "color": source["color"],
            "kind": source["kind"],
            "time": "",
        }],
    )
    plugin._get_calendar_events(_remote_calendar_settings(), selected_date, tz)
    snapshot_path = next(
        (tmp_path / "data" / "plugins" / "simple_calendar" / "event_snapshots").glob("*.json")
    )
    snapshot_path.write_bytes(b'{"version": 1, "events": [')

    monkeypatch.setattr(
        plugin,
        "_fetch_holiday_events",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("theme-only redraw attempted calendar network access")
        ),
    )
    with pytest.raises(RuntimeError, match="event snapshot"):
        plugin._get_calendar_events(
            _remote_calendar_settings(), selected_date, tz, allow_remote=False
        )


def test_remote_event_snapshot_is_normalized_and_bounded(tmp_path, monkeypatch):
    monkeypatch.setenv("INKYPI_DATA_DIR", os.fspath(tmp_path / "data"))
    plugin = SimpleCalendar({"id": "simple_calendar"})
    selected_date = date(2026, 7, 11)
    tz = pytz.timezone("America/Los_Angeles")

    def many_events(source, *_args):
        return [
            {
                "date": date(2026, 7, (index % 28) + 1),
                "title": f"{index:04d}-" + "event" * 300,
                "label": source["label"] + "-label" * 20,
                "color": source["color"],
                "kind": source["kind"],
                "time": "12:34pm-and-extra-data",
            }
            for index in range(600)
        ]

    settings = _remote_calendar_settings()
    settings.update({"showPersonalCalendars": "false"})
    monkeypatch.setattr(plugin, "_fetch_holiday_events", many_events)

    events = plugin._get_calendar_events(settings, selected_date, tz)

    assert 0 < len(events) <= 512
    assert all(len(event["title"]) <= 256 for event in events)
    assert all(len(event["label"]) <= 16 for event in events)
    assert all(len(event.get("time", "")) <= 16 for event in events)
    snapshot_path = next(
        (tmp_path / "data" / "plugins" / "simple_calendar" / "event_snapshots").glob("*.json")
    )
    assert snapshot_path.stat().st_size <= 256 * 1024
    payload = json.loads(snapshot_path.read_text(encoding="utf-8"))
    assert len(payload["events"]) == len(events)


def test_data_refresh_replays_complete_snapshot_when_one_remote_source_fails(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("INKYPI_DATA_DIR", os.fspath(tmp_path / "data"))
    plugin = SimpleCalendar({"id": "simple_calendar"})
    selected_date = date(2026, 7, 11)
    tz = pytz.timezone("America/Los_Angeles")
    settings = _remote_calendar_settings()

    def full_fetch(source, *_args):
        return [{
            "date": date(2026, 7, 4 if source["kind"] == "holiday" else 14),
            "title": "Holiday" if source["kind"] == "holiday" else "Dentist",
            "label": source["label"],
            "color": source["color"],
            "kind": source["kind"],
            "time": "",
        }]

    monkeypatch.setattr(plugin, "_fetch_holiday_events", full_fetch)
    full_events = plugin._get_calendar_events(settings, selected_date, tz)
    snapshot_path = next(
        (tmp_path / "data" / "plugins" / "simple_calendar" / "event_snapshots").glob("*.json")
    )
    original_snapshot = snapshot_path.read_bytes()

    def partial_fetch(source, *_args):
        if source["kind"] == "personal":
            raise TimeoutError("private provider timed out")
        return [{
            "date": date(2026, 7, 5),
            "title": "Partial replacement must not render",
            "label": source["label"],
            "color": source["color"],
            "kind": source["kind"],
            "time": "",
        }]

    monkeypatch.setattr(plugin, "_fetch_holiday_events", partial_fetch)
    data_retry_events = plugin._get_calendar_events(settings, selected_date, tz)
    theme_events = plugin._get_calendar_events(
        settings, selected_date, tz, allow_remote=False
    )

    assert data_retry_events == full_events
    assert theme_events == full_events
    assert snapshot_path.read_bytes() == original_snapshot


def test_cold_partial_remote_failure_refuses_data_render_and_redacts_logs(
    tmp_path, monkeypatch, caplog
):
    monkeypatch.setenv("INKYPI_DATA_DIR", os.fspath(tmp_path / "data"))
    plugin = SimpleCalendar({"id": "simple_calendar"})
    selected_date = date(2026, 7, 11)
    tz = pytz.timezone("America/Los_Angeles")
    private_token = "VERY_PRIVATE_TOKEN_123"
    settings = _remote_calendar_settings()
    settings["personalCalendarURLs[]"] = [
        f"https://example.com/private.ics?token={private_token}"
    ]

    def partial_fetch(source, *_args):
        if source["kind"] == "personal":
            raise RuntimeError(f"provider rejected token {private_token}")
        return [{
            "date": date(2026, 7, 4),
            "title": "Partial holiday",
            "label": source["label"],
            "color": source["color"],
            "kind": source["kind"],
            "time": "",
        }]

    monkeypatch.setattr(plugin, "_fetch_holiday_events", partial_fetch)
    caplog.set_level(logging.WARNING)

    with pytest.raises(RuntimeError, match="event snapshot"):
        plugin._get_calendar_events(settings, selected_date, tz)

    assert private_token not in caplog.text
    assert "example.com" not in caplog.text
    assert "label=ME" in caplog.text
    assert "error_type=RuntimeError" in caplog.text


def _configured_remote_sources(plugin, settings):
    sources = []
    if plugin._holidays_enabled(settings):
        sources.extend(plugin._get_holiday_sources(settings))
    if plugin._personal_calendars_enabled(settings):
        sources.extend(plugin._get_personal_calendar_sources(settings))
    return [
        source
        for source in sources
        if plugin._calendar_source_requires_network(source)
    ]


def _symlink_or_skip(link, target, *, target_is_directory=False):
    try:
        link.symlink_to(target, target_is_directory=target_is_directory)
    except OSError as exc:
        pytest.skip(f"symlinks unavailable: {type(exc).__name__}")


def test_snapshot_fingerprint_cannot_escape_snapshot_root(tmp_path, monkeypatch):
    monkeypatch.setenv("INKYPI_DATA_DIR", os.fspath(tmp_path / "data"))
    plugin = SimpleCalendar({"id": "simple_calendar"})

    with pytest.raises(RuntimeError, match="snapshot"):
        plugin._event_snapshot_path("../escape", create=True)


def test_snapshot_write_rejects_symlink_directory(tmp_path, monkeypatch):
    data_root = tmp_path / "data"
    snapshot_parent = data_root / "plugins" / "simple_calendar"
    snapshot_parent.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    _symlink_or_skip(
        snapshot_parent / "event_snapshots",
        outside,
        target_is_directory=True,
    )
    monkeypatch.setenv("INKYPI_DATA_DIR", os.fspath(data_root))
    plugin = SimpleCalendar({"id": "simple_calendar"})
    settings = _remote_calendar_settings()
    monkeypatch.setattr(
        plugin,
        "_fetch_holiday_events",
        lambda source, *_args: [{
            "date": date(2026, 7, 4),
            "title": "Safe event",
            "label": source["label"],
            "color": source["color"],
            "kind": source["kind"],
            "time": "",
        }],
    )

    with pytest.raises(RuntimeError, match="snapshot"):
        plugin._get_calendar_events(
            settings,
            date(2026, 7, 11),
            pytz.timezone("America/Los_Angeles"),
        )

    assert list(outside.iterdir()) == []


def test_snapshot_read_rejects_symlink_file(tmp_path, monkeypatch):
    monkeypatch.setenv("INKYPI_DATA_DIR", os.fspath(tmp_path / "data"))
    plugin = SimpleCalendar({"id": "simple_calendar"})
    settings = _remote_calendar_settings()
    selected_date = date(2026, 7, 11)
    tz = pytz.timezone("America/Los_Angeles")
    monkeypatch.setattr(
        plugin,
        "_fetch_holiday_events",
        lambda source, *_args: [{
            "date": date(2026, 7, 4),
            "title": "Safe event",
            "label": source["label"],
            "color": source["color"],
            "kind": source["kind"],
            "time": "",
        }],
    )
    plugin._get_calendar_events(settings, selected_date, tz)
    snapshot_path = next(
        (tmp_path / "data" / "plugins" / "simple_calendar" / "event_snapshots").glob("*.json")
    )
    outside = tmp_path / "outside.json"
    outside.write_bytes(snapshot_path.read_bytes())
    snapshot_path.unlink()
    _symlink_or_skip(snapshot_path, outside)

    with pytest.raises(RuntimeError, match="snapshot"):
        plugin._get_calendar_events(
            settings, selected_date, tz, allow_remote=False
        )


def test_snapshot_reader_uses_bounded_read_instead_of_path_read_bytes(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("INKYPI_DATA_DIR", os.fspath(tmp_path / "data"))
    plugin = SimpleCalendar({"id": "simple_calendar"})
    settings = _remote_calendar_settings()
    selected_date = date(2026, 7, 11)
    tz = pytz.timezone("America/Los_Angeles")
    sources = _configured_remote_sources(plugin, settings)
    fingerprint = plugin._event_snapshot_fingerprint(sources, selected_date, tz)
    snapshot_path = plugin._event_snapshot_path(fingerprint, create=True)
    snapshot_path.write_bytes(b"x" * (256 * 1024 + 2))

    monkeypatch.setattr(
        Path,
        "read_bytes",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("unbounded Path.read_bytes used")
        ),
    )

    with pytest.raises(RuntimeError, match="oversized"):
        plugin._read_event_snapshot(sources, selected_date, tz)


def test_snapshot_prune_only_removes_expired_safe_regular_hash_files(tmp_path):
    plugin = SimpleCalendar({"id": "simple_calendar"})
    directory = tmp_path / "snapshots"
    directory.mkdir()
    safe_old = directory / ("a" * 64 + ".json")
    unsafe_name = directory / "notes.json"
    directory_entry = directory / ("b" * 64 + ".json")
    safe_old.write_text("{}", encoding="utf-8")
    unsafe_name.write_text("{}", encoding="utf-8")
    directory_entry.mkdir()
    old = time.time() - 63 * 24 * 60 * 60
    os.utime(safe_old, (old, old))
    os.utime(unsafe_name, (old, old))
    os.utime(directory_entry, (old, old))

    plugin._prune_event_snapshots(directory)

    assert not safe_old.exists()
    assert unsafe_name.exists()
    assert directory_entry.is_dir()


def test_nine_active_remote_snapshot_configurations_all_remain_replayable(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("INKYPI_DATA_DIR", os.fspath(tmp_path / "data"))
    plugin = SimpleCalendar({"id": "simple_calendar"})
    selected_date = date(2026, 7, 11)
    tz = pytz.timezone("America/Los_Angeles")
    settings_list = []

    def fetch(source, *_args):
        return [{
            "date": date(2026, 7, 4),
            "title": source["url"].rsplit("/", 1)[-1],
            "label": source["label"],
            "color": source["color"],
            "kind": source["kind"],
            "time": "",
        }]

    monkeypatch.setattr(plugin, "_fetch_holiday_events", fetch)
    for index in range(9):
        settings = {
            "showHolidays": "true",
            "holidayPreset": "custom",
            "holidayCalendarURLs[]": [f"https://example.com/{index}.ics"],
            "holidayCalendarLabels[]": [f"C{index}"],
        }
        settings_list.append(settings)
        plugin._get_calendar_events(settings, selected_date, tz)

    snapshot_dir = (
        tmp_path / "data" / "plugins" / "simple_calendar" / "event_snapshots"
    )
    assert len(list(snapshot_dir.glob("*.json"))) == 9
    for index, settings in enumerate(settings_list):
        replay = plugin._get_calendar_events(
            settings, selected_date, tz, allow_remote=False
        )
        assert [event["title"] for event in replay] == [f"{index}.ics"]


def test_snapshot_total_byte_budget_rejects_new_data_without_deleting_existing(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("INKYPI_DATA_DIR", os.fspath(tmp_path / "data"))
    plugin = SimpleCalendar({"id": "simple_calendar"})
    selected_date = date(2026, 7, 11)
    tz = pytz.timezone("America/Los_Angeles")
    first = {
        "showHolidays": "true",
        "holidayPreset": "custom",
        "holidayCalendarURLs[]": ["https://example.com/first.ics"],
        "holidayCalendarLabels[]": ["FIRST"],
    }
    second = {
        "showHolidays": "true",
        "holidayPreset": "custom",
        "holidayCalendarURLs[]": ["https://example.com/second.ics"],
        "holidayCalendarLabels[]": ["SECOND"],
    }
    monkeypatch.setattr(
        plugin,
        "_fetch_holiday_events",
        lambda source, *_args: [{
            "date": date(2026, 7, 4),
            "title": source["label"],
            "label": source["label"],
            "color": source["color"],
            "kind": source["kind"],
            "time": "",
        }],
    )
    first_events = plugin._get_calendar_events(first, selected_date, tz)
    snapshot_dir = (
        tmp_path / "data" / "plugins" / "simple_calendar" / "event_snapshots"
    )
    first_snapshot = next(snapshot_dir.glob("*.json"))
    monkeypatch.setattr(
        calendar_module,
        "EVENT_SNAPSHOT_MAX_TOTAL_BYTES",
        first_snapshot.stat().st_size + 1,
        raising=False,
    )

    with pytest.raises(RuntimeError, match="byte budget"):
        plugin._get_calendar_events(second, selected_date, tz)

    assert list(snapshot_dir.glob("*.json")) == [first_snapshot]
    assert plugin._get_calendar_events(
        first, selected_date, tz, allow_remote=False
    ) == first_events


@pytest.mark.parametrize(
    "url",
    [
        "file://calendar-host/share/private.ics",
        "file://192.0.2.10/share/private.ics",
        "file:////calendar-host/share/private.ics",
        r"file:\\calendar-host\share\private.ics",
        "file://localhost//calendar-host/share/private.ics",
        "file://localhost/%2Fcalendar-host/share/private.ics",
        r"\\calendar-host\share\private.ics",
    ],
)
def test_file_host_and_unc_calendar_sources_require_network(url):
    assert SimpleCalendar._calendar_source_requires_network({"url": url}) is True


@pytest.mark.parametrize(
    "url",
    ["file:///var/lib/inkypi/calendars/local.ics", "file://localhost/var/lib/inkypi/calendars/local.ics"],
)
def test_only_empty_or_localhost_file_authorities_are_local(url):
    assert SimpleCalendar._calendar_source_requires_network({"url": url}) is False


def test_theme_only_reads_localhost_file_calendar_directly(tmp_path, monkeypatch):
    plugin = SimpleCalendar({"id": "simple_calendar"})
    tz = pytz.timezone("America/Los_Angeles")
    ics_path = tmp_path / "local.ics"
    ics_path.write_text(
        """BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VEVENT
DTSTART;VALUE=DATE:20260721
DTEND;VALUE=DATE:20260722
SUMMARY:Offline local event
END:VEVENT
END:VCALENDAR
""",
        encoding="utf-8",
    )
    localhost_url = ics_path.as_uri().replace("file:///", "file://localhost/")
    settings = {
        "holidayPreset": "off",
        "showPersonalCalendars": "true",
        "personalCalendarURLs[]": [localhost_url],
        "personalCalendarLabels[]": ["LOCAL"],
    }
    monkeypatch.setattr(
        "plugins.simple_calendar.simple_calendar.get_http_session",
        lambda: (_ for _ in ()).throw(AssertionError("network called")),
    )

    events = plugin._get_calendar_events(
        settings, date(2026, 7, 11), tz, allow_remote=False
    )

    assert [event["title"] for event in events] == ["Offline local event"]


def test_extract_personal_monthly_rrule_expands_current_month():

    plugin = SimpleCalendar({"id": "simple_calendar"})
    tz = pytz.timezone("America/Los_Angeles")
    cal = icalendar.Calendar.from_ical(
        b"""BEGIN:VCALENDAR
BEGIN:VEVENT
UID:pay-10
DTSTART;VALUE=DATE:20250910
DTEND;VALUE=DATE:20250911
RRULE:FREQ=MONTHLY;UNTIL=20360910;BYMONTHDAY=10
SUMMARY:Pay Salary/401(k)
END:VEVENT
END:VCALENDAR
"""
    )

    events = plugin._extract_holiday_events(
        cal,
        {"label": "ME", "color": (46, 125, 50), "kind": "personal"},
        date(2026, 6, 4),
        tz,
    )

    assert events == [{
        "date": date(2026, 6, 10),
        "title": "Pay Salary/401(k)",
        "label": "ME",
        "color": (46, 125, 50),
        "kind": "personal",
        "time": "",
    }]


def test_current_month_rows_include_recurring_personal_before_holidays():
    plugin = SimpleCalendar({"id": "simple_calendar"})
    tz = pytz.timezone("America/Los_Angeles")
    cal = icalendar.Calendar.from_ical(
        b"""BEGIN:VCALENDAR
BEGIN:VEVENT
UID:pay-10
DTSTART;VALUE=DATE:20250910
DTEND;VALUE=DATE:20250911
RRULE:FREQ=MONTHLY;UNTIL=20360910;BYMONTHDAY=10
SUMMARY:Pay Salary/401(k)
END:VEVENT
BEGIN:VEVENT
DTSTART;VALUE=DATE:20260614
DTEND;VALUE=DATE:20260615
SUMMARY:Flag Day
END:VEVENT
BEGIN:VEVENT
DTSTART;VALUE=DATE:20260619
DTEND;VALUE=DATE:20260620
SUMMARY:Juneteenth
END:VEVENT
END:VCALENDAR
"""
    )
    events = plugin._extract_holiday_events(
        cal,
        {"label": "ME", "color": (46, 125, 50), "kind": "personal"},
        date(2026, 6, 4),
        tz,
    )

    rows = plugin._upcoming_event_rows(
        events,
        date(2026, 6, 4),
        reference_dt=tz.localize(datetime(2026, 6, 4, 18, 20)),
        limit=3,
    )

    assert [event["title"] for event in rows] == [
        "Pay Salary/401(k)",
        "Flag Day",
        "Juneteenth",
    ]


def test_calendar_title_cleaning_removes_emoji_symbol_noise():
    plugin = SimpleCalendar({"id": "simple_calendar"})

    assert plugin._clean_event_title("💵Pay Salary💵/401(k)") == "Pay Salary/401(k)"


def test_recurring_personal_event_honors_exdate_rdate_and_recurrence_override():
    plugin = SimpleCalendar({"id": "simple_calendar"})
    tz = pytz.timezone("America/Los_Angeles")
    cal = icalendar.Calendar.from_ical(
        b"""BEGIN:VCALENDAR
BEGIN:VEVENT
UID:standup
DTSTART;VALUE=DATE:20260610
DTEND;VALUE=DATE:20260611
RRULE:FREQ=DAILY;COUNT=2
RDATE;VALUE=DATE:20260613
EXDATE;VALUE=DATE:20260610
SUMMARY:Daily Standup
END:VEVENT
BEGIN:VEVENT
UID:standup
RECURRENCE-ID;VALUE=DATE:20260611
DTSTART;VALUE=DATE:20260612
DTEND;VALUE=DATE:20260613
SUMMARY:Moved Standup
END:VEVENT
END:VCALENDAR
"""
    )

    events = plugin._extract_holiday_events(
        cal,
        {"label": "ME", "color": (46, 125, 50), "kind": "personal"},
        date(2026, 6, 4),
        tz,
    )

    assert [(event["date"], event["title"]) for event in events] == [
        (date(2026, 6, 13), "Daily Standup"),
        (date(2026, 6, 12), "Moved Standup"),
    ]


def test_current_month_event_rows_drop_elapsed_events_without_next_month_backfill():
    plugin = SimpleCalendar({"id": "simple_calendar"})
    tz = pytz.timezone("America/Los_Angeles")
    selected_date = date(2026, 6, 4)
    reference_dt = tz.localize(datetime(2026, 6, 4, 15, 52))
    events = [
        {
            "date": date(2026, 6, 4),
            "title": "Quest Diagnostics Appointment",
            "label": "ME",
            "color": (46, 125, 50),
            "kind": "personal",
            "time": "3p",
            "starts_at": tz.localize(datetime(2026, 6, 4, 15, 0)),
        },
        {
            "date": date(2026, 6, 14),
            "title": "Flag Day",
            "label": "US",
            "color": (52, 89, 149),
            "kind": "holiday",
            "time": "",
        },
        {
            "date": date(2026, 6, 19),
            "title": "Juneteenth",
            "label": "US",
            "color": (52, 89, 149),
            "kind": "holiday",
            "time": "",
        },
        {
            "date": date(2026, 6, 21),
            "title": "Father's Day",
            "label": "US",
            "color": (52, 89, 149),
            "kind": "holiday",
            "time": "",
        },
        {
            "date": date(2026, 7, 4),
            "title": "Independence Day",
            "label": "US",
            "color": (52, 89, 149),
            "kind": "holiday",
            "time": "",
        },
    ]

    rows = plugin._upcoming_event_rows(events, selected_date, reference_dt=reference_dt, limit=3)

    assert [event["date"] for event in rows] == [
        date(2026, 6, 14),
        date(2026, 6, 19),
        date(2026, 6, 21),
    ]
    assert "Independence Day" not in [event["title"] for event in rows]


def test_focus_holiday_card_uses_spacious_illustrated_banner_style():
    plugin = SimpleCalendar({"id": "simple_calendar"})
    image = Image.new("RGB", (220, 120), (255, 255, 255))
    draw = ImageDraw.Draw(image)
    events = [{
        "date": date(2026, 6, 19),
        "title": "Dragon Boat / Juneteenth",
        "label": "CN/US",
        "color": (198, 40, 40),
    }]

    plugin._draw_focus_holiday(draw, events, 110, 55, 200, (0, 0, 0), (132, 132, 132))

    assert image.getpixel((21, 22)) == (166, 31, 36)
    assert image.getpixel((110, 26)) == (238, 218, 158)
    assert image.getpixel((194, 37)) == (238, 218, 158)
    assert image.getpixel((198, 17)) == (217, 177, 90)
    assert image.getpixel((210, 93)) == (25, 38, 45)

    label_pixels = [image.getpixel((px, py)) for py in range(22, 40) for px in range(34, 90)]
    assert any(r > 180 and g < 120 and b < 100 for r, g, b in label_pixels)
    assert any(b > 120 and r < 120 and g < 160 for r, g, b in label_pixels)


def test_focus_holiday_card_uses_bold_title_font(monkeypatch):
    plugin = SimpleCalendar({"id": "simple_calendar"})
    image = Image.new("RGB", (220, 120), (255, 255, 255))
    draw = ImageDraw.Draw(image)
    events = [{
        "date": date(2026, 6, 20),
        "title": "端午节",
        "label": "CN",
        "color": (198, 40, 40),
    }]
    calls = []
    original_get_holiday_title_font = plugin._get_holiday_title_font

    def track_holiday_title_font(font_size, bold=False):
        calls.append((font_size, bold))
        return original_get_holiday_title_font(font_size, bold=bold)

    monkeypatch.setattr(plugin, "_get_holiday_title_font", track_holiday_title_font)

    plugin._draw_focus_holiday(draw, events, 110, 55, 200, (0, 0, 0), (132, 132, 132))

    assert calls == [(14, True)]


def test_calendar_ui_font_uses_original_jost_faces():
    plugin = SimpleCalendar({"id": "simple_calendar"})

    regular = plugin._get_calendar_ui_font(18)
    bold = plugin._get_calendar_ui_font(42, bold=True)

    assert Path(regular.path).name == "Jost.ttf"
    assert Path(bold.path).name == "Jost-SemiBold.ttf"
    assert regular.getname()[0] == "Jost"
    assert bold.getname()[0] == "Jost"


def test_calendar_render_uses_original_ui_font_resolver(monkeypatch):
    plugin = SimpleCalendar({"id": "simple_calendar"})
    calls = []
    drawn_font_files = []
    original_font = plugin._get_calendar_ui_font
    original_text = ImageDraw.ImageDraw.text

    def track_font(font_size, bold=False):
        calls.append((font_size, bold))
        return original_font(font_size, bold=bold)

    def track_text(self, xy, text, *args, **kwargs):
        font = kwargs.get("font")
        if font is not None:
            drawn_font_files.append(Path(font.path).name)
        return original_text(self, xy, text, *args, **kwargs)

    monkeypatch.setattr(plugin, "_get_calendar_ui_font", track_font)
    monkeypatch.setattr(ImageDraw.ImageDraw, "text", track_text)

    image = plugin._render_calendar(
        (800, 480),
        date(2026, 7, 11),
        (230, 26, 26),
        (163, 13, 13),
        LOCALE_DATA["en"],
        "en",
    )

    assert image.size == (800, 480)
    assert any(bold for _size, bold in calls)
    assert any(not bold for _size, bold in calls)
    assert set(drawn_font_files) == {"Jost.ttf", "Jost-SemiBold.ttf"}


def test_holiday_list_source_labels_color_cn_and_us_independently():
    plugin = SimpleCalendar({"id": "simple_calendar"})
    image = Image.new("RGB", (320, 120), (255, 255, 255))
    draw = ImageDraw.Draw(image)
    event = {
        "date": date(2026, 6, 19),
        "title": "Dragon Boat / Juneteenth",
        "label": "CN/US",
        "color": (198, 40, 40),
    }

    plugin._draw_holiday_list(
        draw,
        [],
        date(2026, 6, 19),
        [event],
        0,
        0,
        320,
        120,
        (0, 0, 0),
        (132, 132, 132),
        (220, 220, 220),
    )

    label_pixels = [image.getpixel((px, py)) for py in range(12, 45) for px in range(50, 130)]
    assert any(r > 180 and g < 120 and b < 100 for r, g, b in label_pixels)
    assert any(b > 120 and r < 120 and g < 160 for r, g, b in label_pixels)
