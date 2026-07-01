from datetime import date, datetime
import sys
from pathlib import Path

import icalendar
import pytz
from PIL import Image, ImageDraw

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


def test_remote_calendar_source_uses_shared_http_session(monkeypatch):
    plugin = SimpleCalendar({"id": "simple_calendar"})
    calls = []

    class FakeResponse:
        content = b"BEGIN:VCALENDAR\nEND:VCALENDAR\n"

        def raise_for_status(self):
            calls.append(("raise_for_status",))

    class FakeSession:
        def get(self, url, timeout, headers):
            calls.append((url, timeout, headers.get("User-Agent")))
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
        ),
        ("raise_for_status",),
    ]


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
