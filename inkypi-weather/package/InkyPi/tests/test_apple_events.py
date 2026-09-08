"""Apple announcements enter the calendar through its durable provider API."""
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from plugins.simple_calendar.apple_events import AppleEventProvider

NOW = datetime(2026, 9, 8, 5, tzinfo=timezone.utc)
HOME = "https://www.apple.com/apple-events/"
ICS = "https://www.apple.com/v/apple-events/home/ak/built/assets/event/event.ics"
SETTINGS = {"showAppleEvents": "true"}


def calendar_text(*, start="20260909T100000", uid="apple-september-2026", summary="Apple Event", sequence=0, status="CONFIRMED", modified=""):
    return f"""BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VEVENT
UID:{uid}
SUMMARY:{summary}
DTSTART;TZID=America/Los_Angeles:{start}
DTSTAMP:20260909T100000
SEQUENCE:{sequence}
STATUS:{status}
{modified}
END:VEVENT
END:VCALENDAR
"""


class Web:
    def __init__(self):
        self.calls = []
        self.ics_url = ICS
        self.calendar = calendar_text()
        self.failure = False

    def __call__(self, url):
        self.calls.append(url)
        if self.failure:
            raise OSError("offline")
        if url == HOME:
            return f'<html><main><h1>Apple Events</h1><a href="{self.ics_url}">Add to calendar</a><h2>WWDC June 8, 2026</h2></main></html>'
        assert url == self.ics_url
        return self.calendar


def test_official_calendar_discovered_and_projected_to_device_timezone(tmp_path):
    web = Web()
    provider = AppleEventProvider(tmp_path, fetch_text=web)
    result = provider.refresh(SETTINGS, now=NOW)
    assert len(result.events) == 1
    assert result.events[0]["starts_at"] == "2026-09-09T17:00:00+00:00"
    assert result.events[0]["published_at"] is None
    event = provider.calendar_events(result, date(2026, 9, 8), ZoneInfo("Asia/Shanghai"))[0]
    assert event["date"] == date(2026, 9, 10) and event["time"] == "01:00"
    assert event["title"] == "苹果发布会" and event["label"] == "APPLE"
    assert web.calls == [HOME, ICS]


def test_asset_change_reschedule_and_older_modified_copy_keep_one_identity(tmp_path):
    web = Web()
    web.calendar = calendar_text(modified="LAST-MODIFIED:20260908T050000Z")
    provider = AppleEventProvider(tmp_path, fetch_text=web)
    first = provider.refresh(SETTINGS, now=NOW)
    web.ics_url = ICS.replace("/ak/", "/next-version/")
    web.calendar = calendar_text(start="20260910T110000", modified="LAST-MODIFIED:20260908T070000Z")
    updated = provider.refresh(SETTINGS, now=NOW + timedelta(hours=3))
    assert len(updated.events) == 1 and updated.events[0]["id"] == first.events[0]["id"]
    assert updated.events[0]["starts_at"] == "2026-09-10T18:00:00+00:00"
    web.calendar = calendar_text(modified="LAST-MODIFIED:20260908T050000Z")
    regressed = provider.refresh(SETTINGS, now=NOW + timedelta(hours=6))
    assert regressed.events[0]["starts_at"] == "2026-09-10T18:00:00+00:00"


def test_three_hour_gate_survives_restart_and_failures_then_recovers(tmp_path):
    web = Web()
    first = AppleEventProvider(tmp_path, fetch_text=web).refresh(SETTINGS, now=NOW)
    web.calls.clear()
    restarted = AppleEventProvider(tmp_path, fetch_text=web)
    assert restarted.refresh(SETTINGS, now=NOW + timedelta(hours=2)).events == first.events
    assert web.calls == []
    web.failure = True
    failed = restarted.refresh(SETTINGS, now=NOW + timedelta(hours=3))
    assert failed.events[0]["pending_verification"] and failed.provenance.value == "stale_cache"
    web.calls.clear()
    restarted.refresh(SETTINGS, now=NOW + timedelta(hours=3, minutes=30))
    assert web.calls == []
    web.failure = False
    recovered = restarted.refresh(SETTINGS, now=NOW + timedelta(hours=6))
    assert not recovered.events[0]["pending_verification"]


def test_new_uid_is_distinct_and_sequence_cancellation_cannot_be_reversed(tmp_path):
    web = Web()
    provider = AppleEventProvider(tmp_path, fetch_text=web)
    first = provider.refresh(SETTINGS, now=NOW)
    web.calendar = calendar_text(uid="apple-october-2026", start="20261001T100000")
    other = provider.refresh(SETTINGS, now=NOW + timedelta(hours=3))
    assert len(other.events) == 2 and len({e["id"] for e in other.events}) == 2
    web.calendar = calendar_text(sequence=2, status="CANCELLED")
    cancelled = provider.refresh(SETTINGS, now=NOW + timedelta(hours=6))
    assert all(e["id"] != first.events[0]["id"] for e in cancelled.events)
    web.calendar = calendar_text(sequence=1)
    old = provider.refresh(SETTINGS, now=NOW + timedelta(hours=9))
    assert all(e["id"] != first.events[0]["id"] for e in old.events)


def test_missing_calendar_retains_pending_event_without_reading_archive_dates(tmp_path):
    web = Web()
    provider = AppleEventProvider(tmp_path, fetch_text=web)
    first = provider.refresh(SETTINGS, now=NOW)
    provider.fetch_text = lambda url: '<html><main><h1>Apple Events</h1><h2>WWDC June 8, 2026</h2></main></html>'
    result = provider.refresh(SETTINGS, now=NOW + timedelta(hours=3))
    assert len(result.events) == 1 and result.events[0]["id"] == first.events[0]["id"]
    assert result.events[0]["pending_verification"]
    assert AppleEventProvider(tmp_path / "new", fetch_text=provider.fetch_text).refresh(SETTINGS, now=NOW).events == []


@pytest.mark.parametrize("summary,expected", [("WWDC27 Keynote", "WWDC 主题演讲"), ("WWDC27 Conference", None), ("WWDC27 Keynote ASL Replay", None), ("iPhone preorder", None)])
def test_only_official_keynotes_are_admitted(tmp_path, summary, expected):
    web = Web()
    web.calendar = calendar_text(summary=summary)
    events = AppleEventProvider(tmp_path, fetch_text=web).refresh(SETTINGS, now=NOW).events
    assert [event["title"] for event in events] == ([expected] if expected else [])


def test_date_only_and_device_month_projection(tmp_path):
    web = Web()
    web.calendar = calendar_text(start="20260930T200000")
    provider = AppleEventProvider(tmp_path, fetch_text=web)
    result = provider.refresh(SETTINGS, now=NOW)
    assert provider.calendar_events(result, date(2026, 9, 1), ZoneInfo("Asia/Shanghai")) == []
    event = provider.calendar_events(result, date(2026, 10, 1), ZoneInfo("Asia/Shanghai"))[0]
    assert event["date"] == date(2026, 10, 1) and event["time"] == "11:00"
    web.calendar = calendar_text().replace("DTSTART;TZID=America/Los_Angeles:20260909T100000", "DTSTART;VALUE=DATE:20260909")
    result = provider.refresh(SETTINGS, now=NOW + timedelta(hours=3))
    event = provider.calendar_events(result, date(2026, 9, 1), ZoneInfo("Asia/Shanghai"))[0]
    assert event["date"] == date(2026, 9, 9) and event["time"] == "时间待定"


def test_disabled_apple_does_not_fetch_or_create_state_even_when_games_enabled(tmp_path):
    web = Web()
    provider = AppleEventProvider(tmp_path / "apple", fetch_text=web)
    for settings in ({}, {"showGameEvents": "true"}, {"showAppleEvents": "false"}):
        assert provider.refresh(settings, now=NOW).events == []
        assert provider.read(settings, now=NOW).events == []
    assert web.calls == [] and not provider.directory.exists()


@pytest.mark.parametrize("url", ["https://www.apple.com.evil.example/event.ics", "http://www.apple.com/event.ics", "https://user@www.apple.com/event.ics"])
def test_untrusted_calendar_links_are_not_fetched(tmp_path, url):
    web = Web()
    web.ics_url = url
    result = AppleEventProvider(tmp_path, fetch_text=web).refresh(SETTINGS, now=NOW)
    assert result.events == [] and result.sources["apple"]["error"]
    assert web.calls == [HOME]


def test_floating_start_is_unavailable_instead_of_guessing_timezone(tmp_path):
    web = Web()
    web.calendar = calendar_text().replace("DTSTART;TZID=America/Los_Angeles:", "DTSTART:")
    result = AppleEventProvider(tmp_path, fetch_text=web).refresh(SETTINGS, now=NOW)
    assert result.events == [] and result.sources["apple"]["error"]
