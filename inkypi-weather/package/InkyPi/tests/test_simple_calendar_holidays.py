from datetime import date
import sys
from pathlib import Path

import icalendar
import pytz
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from plugins.simple_calendar.simple_calendar import SimpleCalendar


def test_default_us_cn_holiday_sources_when_enabled():
    plugin = SimpleCalendar({"id": "simple_calendar"})

    sources = plugin._get_holiday_sources({
        "showHolidays": "true",
        "holidayPreset": "us_cn",
    })

    assert [source["label"] for source in sources] == ["US", "CN"]
    assert all(source["url"].startswith("https://calendar.google.com/calendar/ical/") for source in sources)


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
        "img2_original_heroes_weather",
        "img2_original_heroes_nyc_weather",
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
        pytz.timezone("America/Los_Angeles"),
    )

    assert events == [{
        "date": date(2026, 5, 28),
        "title": "Dentist",
        "label": "ME",
        "color": (46, 125, 50),
        "kind": "personal",
        "time": "8:30a",
    }]
