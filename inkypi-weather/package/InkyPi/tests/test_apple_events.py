"""Apple announcements enter the calendar through its durable provider API."""
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from plugins.simple_calendar.apple_events import AppleEventProvider

NOW = datetime(2026, 9, 8, 5, tzinfo=timezone.utc)
HOME = "https://www.apple.com/apple-events/"
ICS = "https://www.apple.com/v/apple-events/home/ak/built/assets/event/event.ics"
NEWSROOM = "https://www.apple.com/newsroom/rss-feed.rss"
MARCH = "https://www.apple.com/newsroom/2027/03/apples-worldwide-developers-conference-returns-the-week-of-june-7/"
MAY = "https://www.apple.com/newsroom/2027/05/apple-kicks-off-worldwide-developers-conference-on-june-7/"
JUNE = "https://www.apple.com/newsroom/2027/06/wwdc27-apple-unveils-new-software/"
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


def newsroom_feed(*stories):
    entries = "".join(
        f'<entry><title><![CDATA[{title}\n]]></title><link href="{url}"/><id>{url}</id>'
        f'<updated>2027-03-23T12:00:00Z</updated><content>Story</content></entry>'
        for url, title in stories
    )
    return f'<?xml version="1.0" encoding="UTF-8"?><feed xmlns="http://www.w3.org/2005/Atom"><title>Apple Newsroom</title>{entries}</feed>'


def newsroom_story(title, published, *blocks):
    # Apple writes headlines with non-breaking spaces; the copy-text widget is
    # not article prose, so its (deliberately wrong) schedule must be ignored.
    body = "".join(f'<div class="pagebody-copy">{block}</div>' for block in blocks)
    return (f'<html><script type="application/ld+json">{{"datePublished": "{published}Z"}}</script>'
            f'<article><h1 class="hero-headline">{title.replace(" ", chr(160))}</h1>{body}'
            '<div class="docsanddownloads text component"><p>Copy text: the Keynote streams June 9 at 9 a.m. PDT.</p></div>'
            '</article></html>')


MARCH_STORY = newsroom_story(
    "Apple’s Worldwide Developers Conference returns the week of June 7", "2027-03-22",
    "Apple today announced it will host its annual Worldwide Developers Conference (WWDC) online from June 7-11.",
    "WWDC kicks off with the Keynote and Platforms State of the Union on Monday, June 7. The conference continues online all week.",
)
MAY_STORY = newsroom_story(
    "Apple kicks off Worldwide Developers Conference on June 7", "2027-05-17",
    "<strong>Apple Keynote</strong>",
    "<strong>June 7, 10 a.m. PDT</strong><br>WWDC will open with a first look at the latest updates. The Keynote will be available to stream on apple.com.",
    "<strong>June 7, 1 p.m. PDT</strong><br>Shortly after the Keynote, the Platforms State of the Union will take a deeper dive.",
)
JUNE_STORY = newsroom_story(
    "WWDC27: Apple unveils new software", "2027-06-07",
    "Today, during the Worldwide Developers Conference keynote on June 7, Apple previewed its upcoming software.",
)


class Web:
    def __init__(self):
        self.calls = []
        self.ics_url = ICS
        self.calendar = calendar_text()
        self.failure = False
        self.newsroom = newsroom_feed()
        self.stories = {}

    def __call__(self, url):
        self.calls.append(url)
        if self.failure:
            raise OSError("offline")
        if url == HOME:
            return f'<html><main><h1>Apple Events</h1><a href="{self.ics_url}">Add to calendar</a><h2>WWDC June 8, 2026</h2></main></html>'
        if url == NEWSROOM:
            return self.newsroom
        if url in self.stories:
            return self.stories[url]
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
    assert web.calls == [HOME, ICS, NEWSROOM]


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
    assert web.calls == [HOME, NEWSROOM]


def test_floating_start_is_unavailable_instead_of_guessing_timezone(tmp_path):
    web = Web()
    web.calendar = calendar_text().replace("DTSTART;TZID=America/Los_Angeles:", "DTSTART:")
    result = AppleEventProvider(tmp_path, fetch_text=web).refresh(SETTINGS, now=NOW)
    assert result.events == [] and result.sources["apple"]["error"]


def wwdc_web():
    """Before WWDC the event page still shows last year's keynote and offers no calendar."""
    web = Web()
    web.ics_url = "/apple-events/watch/"
    web.stories = {MARCH: MARCH_STORY, MAY: MAY_STORY, JUNE: JUNE_STORY}
    return web


def test_newsroom_schedules_wwdc_keynote_and_later_story_adds_its_time(tmp_path):
    web = wwdc_web()
    web.newsroom = newsroom_feed(
        (MARCH, "Apple’s Worldwide Developers Conference returns the week of June 7"),
        ("https://www.apple.com/newsroom/2027/03/apple-opens-a-new-store/", "Apple opens a new store"),
        ("https://www.apple.com/uk/newsroom/2027/03/wwdc-returns/", "WWDC returns"),
    )
    provider = AppleEventProvider(tmp_path, fetch_text=web)
    first = provider.refresh(SETTINGS, now=datetime(2027, 3, 23, 17, tzinfo=timezone.utc))
    event, = first.events
    assert (event["title"], event["event_date"], event["starts_at"]) == ("WWDC 主题演讲", "2027-06-07", None)
    assert event["source_url"] == MARCH and not first.sources["apple"]["error"]
    assert web.calls == [HOME, NEWSROOM, MARCH]
    row, = provider.calendar_events(first, date(2027, 6, 1), ZoneInfo("America/Los_Angeles"))
    assert row["time"] == "时间待定"
    # March has scrolled out of the 20-story feed; it is rechecked from state.
    web.newsroom = newsroom_feed((MAY, "Apple kicks off Worldwide Developers Conference on June 7"))
    web.calls.clear()
    timed = provider.refresh(SETTINGS, now=datetime(2027, 5, 17, 18, tzinfo=timezone.utc))
    assert web.calls == [HOME, NEWSROOM, MARCH, MAY]
    event, = timed.events
    assert event["id"] == first.events[0]["id"] and not event["pending_verification"]
    assert event["starts_at"] == "2027-06-07T17:00:00+00:00"
    row, = provider.calendar_events(timed, date(2027, 6, 1), ZoneInfo("Asia/Shanghai"))
    assert (row["date"], row["time"], row["title"]) == (date(2027, 6, 8), "01:00", "WWDC 主题演讲")


def test_newsroom_reports_on_or_after_the_keynote_never_schedule_it(tmp_path):
    web = wwdc_web()
    web.newsroom = newsroom_feed((JUNE, "WWDC27: Apple unveils new software"))
    result = AppleEventProvider(tmp_path, fetch_text=web).refresh(SETTINGS, now=datetime(2027, 6, 7, 20, tzinfo=timezone.utc))
    assert result.events == [] and not result.sources["apple"]["error"]
    assert JUNE in web.calls


def test_newsroom_failure_keeps_the_event_page_calendar(tmp_path):
    web = Web()
    web.newsroom = "<html><body>Service unavailable</body></html>"
    result = AppleEventProvider(tmp_path, fetch_text=web).refresh(SETTINGS, now=NOW)
    event, = result.events
    assert event["starts_at"] == "2026-09-09T17:00:00+00:00" and not event["pending_verification"]
    assert result.sources["apple"]["error"] == "apple.com/newsroom: ValueError"
