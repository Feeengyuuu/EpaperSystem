"""Persistent synchronization is tested through its public read/refresh API."""
from datetime import datetime, timedelta, timezone
import json
import logging
from pathlib import Path

import pytest

from plugins.simple_calendar.game_events import GameEventProvider

NOW = datetime(2026, 9, 2, 12, tzinfo=timezone.utc)
URL = "https://blog.playstation.com/2026/08/31/state-of-play-returns/"
SETTINGS = {"showGameEvents": "true", "gameEventSeries[]": ["state_of_play"], "allowGameEventMedia": "false"}


def article(day=3, hour=6):
    return f'<h1>State of Play returns September {day}, 2026</h1><article>Watch State of Play live on September {day}, 2026 at {hour}am PT.</article>'


def feed(day=3, hour=6):
    return f'''<rss version="2.0" xmlns:content="http://purl.org/rss/1.0/modules/content/"><channel>
    <title>PlayStation</title><item><title>State of Play returns September {day}, 2026</title><link>{URL}</link>
    <pubDate>Mon, 31 Aug 2026 12:00:00 GMT</pubDate><content:encoded><![CDATA[{article(day, hour)}]]></content:encoded></item></channel></rss>'''


class Web:
    def __init__(self):
        self.calls = []
        self.day = 3
        self.hour = 6
        self.failure = False

    def __call__(self, url):
        self.calls.append(url)
        if self.failure:
            raise OSError("offline")
        return feed(self.day, self.hour) if url.endswith("/feed/") else article(self.day, self.hour)


def test_sync_persists_events_and_three_hour_poll_across_restart(tmp_path):
    web = Web()
    provider = GameEventProvider(tmp_path, fetch_text=web)
    result = provider.refresh(SETTINGS, now=NOW)
    assert len(result.events) == 1
    assert result.events[0]["starts_at"] == "2026-09-03T13:00:00+00:00"
    web.calls.clear()
    restarted = GameEventProvider(tmp_path, fetch_text=web)
    assert restarted.refresh(SETTINGS, now=NOW + timedelta(hours=2)).events == result.events
    assert web.calls == []
    web.day = 4
    updated = restarted.refresh(SETTINGS, now=NOW + timedelta(hours=3))
    assert len(updated.events) == 1
    assert updated.events[0]["id"] == result.events[0]["id"]
    assert updated.events[0]["starts_at"] == "2026-09-04T13:00:00+00:00"


def test_source_failure_keeps_pending_events_and_recovery_clears_marker(tmp_path):
    web = Web()
    provider = GameEventProvider(tmp_path, fetch_text=web)
    provider.refresh(SETTINGS, now=NOW)
    web.failure = True
    failed = provider.refresh(SETTINGS, now=NOW + timedelta(hours=3))
    assert len(failed.events) == 1
    assert failed.events[0]["pending_verification"] is True
    assert failed.provenance.value == "stale_cache"
    web.calls.clear()
    provider.refresh(SETTINGS, now=NOW + timedelta(hours=3, minutes=10))
    assert web.calls == []
    web.failure = False
    recovered = provider.refresh(SETTINGS, now=NOW + timedelta(hours=6))
    assert recovered.events[0]["pending_verification"] is False


def media_feed(host, *, official_link=True, day=3):
    evidence = f'<a href="{URL}">official announcement</a>' if official_link else ''
    return f'<rss version="2.0"><channel><title>News</title><item><title>Sony confirms State of Play September {day}, 2026</title><link>https://{host}/articles/state-of-play-confirmed</link><description><![CDATA[<p>Sony confirmed State of Play will air September {day}, 2026 at 6am PT.</p>{evidence}]]></description></item></channel></rss>'


def test_media_with_official_link_is_accepted_but_conflict_cannot_replace_official(tmp_path):
    settings = dict(SETTINGS, allowGameEventMedia="true")
    def fetch(url):
        if "eurogamer" in url:
            return media_feed("www.eurogamer.net", day=4)
        if "ign.com" in url:
            return '<rss version="2.0"><channel><title>News</title></channel></rss>'
        raise OSError("official unavailable")
    provider = GameEventProvider(tmp_path, fetch_text=fetch)
    result = provider.refresh(settings, now=NOW)
    assert len(result.events) == 1
    assert result.events[0]["authority"] == 1
    provider.fetch_text = lambda url: feed() if "playstation" in url else fetch(url)
    result = provider.refresh(settings, now=NOW + timedelta(hours=3))
    assert len(result.events) == 1
    assert result.events[0]["event_date"] == "2026-09-03"
    assert result.events[0]["conflict"] is True


def test_media_without_official_link_requires_two_independent_agreeing_publishers(tmp_path):
    settings = dict(SETTINGS, allowGameEventMedia="true")
    second = False
    def fetch(url):
        if "eurogamer" in url:
            return media_feed("www.eurogamer.net", official_link=False)
        if "ign.com" in url and second:
            return media_feed("www.ign.com", official_link=False)
        raise OSError("unavailable")
    provider = GameEventProvider(tmp_path, fetch_text=fetch)
    assert provider.refresh(settings, now=NOW).events == []
    second = True
    result = provider.refresh(settings, now=NOW + timedelta(hours=3))
    assert len(result.events) == 1


def test_cancelled_event_disappears_and_missing_feed_does_not_cancel(tmp_path):
    web = Web()
    provider = GameEventProvider(tmp_path, fetch_text=web)
    original = provider.refresh(SETTINGS, now=NOW)
    provider.fetch_text = lambda url: '<rss version="2.0"><channel><title>PS</title></channel></rss>' if url.endswith('/feed/') else '<html><h1>State of Play</h1><article>No announcement here</article></html>'
    missing = provider.refresh(SETTINGS, now=NOW + timedelta(hours=3))
    assert len(missing.events) == 1
    assert missing.events[0]["id"] == original.events[0]["id"]
    provider.fetch_text = lambda url: '<rss version="2.0"><channel><title>PS</title></channel></rss>' if url.endswith('/feed/') else '<h1>State of Play September 3, 2026 cancelled</h1><article>The State of Play broadcast has been cancelled.</article>'
    assert provider.refresh(SETTINGS, now=NOW + timedelta(hours=6)).events == []


def test_provider_read_is_write_free_and_uses_device_month_for_utc_cross_day(tmp_path):
    from zoneinfo import ZoneInfo
    provider = GameEventProvider(tmp_path, fetch_text=Web())
    result = provider.refresh(SETTINGS, now=NOW)
    before = provider.path.read_bytes()
    read = provider.read(SETTINGS, now=NOW + timedelta(hours=4))
    assert provider.path.read_bytes() == before
    assert read.events[0]["pending_verification"] is True
    events = provider.calendar_events(result, NOW.date(), ZoneInfo("Asia/Tokyo"))
    assert events[0]["time"] == "22:00"


def test_malformed_cache_is_reported_without_crashing_settings_or_rewriting(tmp_path):
    provider = GameEventProvider(tmp_path, fetch_text=Web())
    for raw in (b'[]', b'{"version":1,"sources":{"playstation":{"items":[null]}}}', b'{broken'):
        provider.path.write_bytes(raw)
        result = provider.read(SETTINGS, now=NOW)
        assert result.events == []
        assert result.sources["playstation"]["error"]
        assert provider.path.read_bytes() == raw


def test_date_only_official_notice_can_be_supplemented_with_agreeing_exact_time(tmp_path):
    settings = {"showGameEvents": "true", "gameEventSeries[]": ["the_game_awards"], "allowGameEventMedia": "false"}
    def fetch(url):
        if "addevent" in url:
            return 'BEGIN:VCALENDAR\nVERSION:2.0\nBEGIN:VEVENT\nUID:tga\nSUMMARY:The Game Awards\nDTSTART:20261211T003000Z\nEND:VEVENT\nEND:VCALENDAR'
        return '<html><h1>The Game Awards returns December 10, 2026</h1><article>The Game Awards will take place December 10, 2026.</article></html>'
    result = GameEventProvider(tmp_path, fetch_text=fetch).refresh(settings, now=NOW)
    assert len(result.events) == 1
    assert result.events[0]["starts_at"] == "2026-12-11T00:30:00+00:00"


def test_unavailable_second_media_confirmation_keeps_event_pending(tmp_path):
    settings = dict(SETTINGS, allowGameEventMedia="true")
    offline = set()
    def fetch(url):
        if "playstation" in url or any(host in url for host in offline):
            raise OSError("offline")
        host = "www.ign.com" if "ign.com" in url else "www.eurogamer.net"
        return media_feed(host, official_link=False)
    provider = GameEventProvider(tmp_path, fetch_text=fetch)
    assert provider.refresh(settings, now=NOW).events[0]["pending_verification"] is False
    offline.add("ign.com")
    assert provider.refresh(settings, now=NOW + timedelta(hours=3)).events[0]["pending_verification"] is True


def test_cold_failure_is_fallback_and_past_events_do_not_stay_stale(tmp_path):
    from plugins.base_plugin.render_provenance import SourceProvenance
    web = Web()
    web.failure = True
    provider = GameEventProvider(tmp_path, fetch_text=web)
    assert provider.refresh(SETTINGS, now=NOW).provenance == SourceProvenance.LOCAL_FALLBACK
    web.failure = False
    provider.refresh(SETTINGS, now=NOW + timedelta(hours=3))
    assert provider.read(SETTINGS, now=NOW + timedelta(days=3)).events[0]["pending_verification"] is False


def test_later_official_cancellation_beats_old_announcement_in_same_poll(tmp_path):
    cancelled_url = "https://blog.playstation.com/2026/09/02/state-of-play-cancelled/"
    cancellation = '<meta property="article:modified_time" content="2026-09-02T11:00:00Z"><h1>State of Play September 3, 2026 cancelled</h1><article>The State of Play broadcast has been cancelled.</article>'
    def fetch(url):
        if url == cancelled_url:
            return cancellation
        return feed().replace('</channel>', '<item><title>State of Play September 3, 2026 cancelled</title><link>' + cancelled_url + '</link></item></channel>') if url.endswith('/feed/') else article()
    provider = GameEventProvider(tmp_path, fetch_text=fetch)
    assert provider.refresh(SETTINGS, now=NOW).events == []
    assert provider.refresh(SETTINGS, now=NOW + timedelta(hours=3)).events == []


def test_utc_cross_month_event_enters_device_month_only(tmp_path):
    from zoneinfo import ZoneInfo
    from datetime import date
    def fetch(url):
        return '<html><h1>State of Play returns September 30, 2026</h1><article>Watch State of Play live September 30, 2026 at 8pm PT.</article></html>'
    provider = GameEventProvider(tmp_path, fetch_text=fetch)
    result = provider.refresh(SETTINGS, now=NOW)
    assert provider.calendar_events(result, date(2026, 9, 2), ZoneInfo("Asia/Shanghai")) == []
    projected = provider.calendar_events(result, date(2026, 10, 1), ZoneInfo("Asia/Shanghai"))
    assert projected[0]["date"] == date(2026, 10, 1)
    assert projected[0]["time"] == "11:00"


def test_same_instant_in_official_local_date_and_ics_utc_date_is_not_conflict(tmp_path):
    settings = {"showGameEvents": "true", "gameEventSeries[]": ["the_game_awards"], "allowGameEventMedia": "false"}
    def fetch(url):
        if "addevent" in url:
            return 'BEGIN:VCALENDAR\nVERSION:2.0\nBEGIN:VEVENT\nUID:tga\nSUMMARY:The Game Awards\nDTSTART:20261211T003000Z\nEND:VEVENT\nEND:VCALENDAR'
        return '<html><h1>The Game Awards returns December 10, 2026</h1><article>The Game Awards will take place December 10, 2026 at 4:30pm PT.</article></html>'
    result = GameEventProvider(tmp_path, fetch_text=fetch).refresh(settings, now=NOW)
    assert len(result.events) == 1
    assert result.events[0]["conflict"] is False
    assert result.events[0]["pending_verification"] is False


def test_evidence_seen_again_in_check_is_not_doubted_by_another_failed_url(tmp_path):
    def fetch(url):
        if url.endswith("/feed/"):
            return feed()
        raise OSError("article offline")
    result = GameEventProvider(tmp_path, fetch_text=fetch).refresh(SETTINGS, now=NOW)
    assert result.sources["playstation"]["error"] == "blog.playstation.com: OSError"
    assert result.events[0]["pending_verification"] is False
    assert result.provenance.value == "fresh_cache"


def test_evidence_missing_from_failed_check_stays_pending(tmp_path):
    provider = GameEventProvider(tmp_path, fetch_text=Web())
    provider.refresh(SETTINGS, now=NOW)
    def fetch(url):
        if url.endswith("/feed/"):
            return '<rss version="2.0"><channel><title>PS</title></channel></rss>'
        raise OSError("article offline")
    provider.fetch_text = fetch
    result = provider.refresh(SETTINGS, now=NOW + timedelta(hours=3))
    assert result.events[0]["pending_verification"] is True
    assert result.provenance.value == "stale_cache"


DEVICE_STATE = Path(__file__).parent / "fixtures" / "tga_2026" / "device_game_events_state.json"
DEVICE_SETTINGS = {"showGameEvents": "true", "allowGameEventMedia": "true", "gameEventSeries[]": [
    "state_of_play", "nintendo_direct", "summer_game_fest", "the_game_awards", "xbox_showcase", "gamescom_onl",
]}


@pytest.mark.parametrize("notice_checked_at,pending", [
    # The 2026-10-11 device check re-read the TGA notice while the index pages failed.
    ("2026-10-11T00:15:30.721066+00:00", False),
    # Evidence the failed check did not see again stays doubtful within the interval.
    ("2026-10-10T23:00:00+00:00", True),
])
def test_device_state_doubts_tga_only_when_failed_check_missed_its_evidence(tmp_path, notice_checked_at, pending):
    state = json.loads(DEVICE_STATE.read_text(encoding="utf-8"))
    notice = next(item for item in state["sources"]["tga"]["items"] if item["source_url"].endswith("/tga-returns-december-10-2026"))
    notice["checked_at"] = notice_checked_at
    provider = GameEventProvider(tmp_path)
    provider.path.write_text(json.dumps(state), encoding="utf-8")
    result = provider.read(DEVICE_SETTINGS, now=datetime(2026, 10, 11, 0, 20, tzinfo=timezone.utc))
    event, = [item for item in result.events if item["family"] == "the_game_awards"]
    assert event["event_date"] == "2026-12-10" and event["starts_at"] == "2026-12-11T00:30:00+00:00"
    assert event["pending_verification"] is pending
    assert result.provenance.value == ("stale_cache" if pending else "fresh_cache")


def test_cold_tga_check_in_january_is_healthy_while_only_last_years_show_is_listed(tmp_path):
    # Index cards still exist but all name the past show; that is not a source
    # failure, so a fresh install must not fall back to local-only data.
    pages = {"https://thegameawards.com/news": "news.html", "https://thegameawards.com/": "home.html"}
    def fetch(url):
        if url in pages:
            return (DEVICE_STATE.parent / pages[url]).read_text(encoding="utf-8")
        if url.startswith("https://www.addevent.com/"):
            return "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//test//EN\r\nEND:VCALENDAR\r\n"
        return "<html><h1>Summer Game Fest</h1><main>See you next June.</main></html>"
    settings = {"showGameEvents": "true", "gameEventSeries[]": ["the_game_awards"], "allowGameEventMedia": "false"}
    result = GameEventProvider(tmp_path, fetch_text=fetch).refresh(settings, now=datetime(2027, 1, 5, 18, tzinfo=timezone.utc))
    assert {source: status["error"] for source, status in result.sources.items()} == {"tga": "", "sgf": ""}
    assert result.events == []
    assert result.provenance.value == "fresh_cache"


def _source_logs(caplog, level):
    return [record.getMessage() for record in caplog.records
            if record.name == "plugins.simple_calendar.game_events" and record.levelno == level]


def test_source_failure_logs_once_per_change_and_recovery(tmp_path, caplog):
    caplog.set_level(logging.INFO, logger="plugins.simple_calendar.game_events")
    web = Web()
    provider = GameEventProvider(tmp_path, fetch_text=web)
    provider.refresh(SETTINGS, now=NOW)
    web.failure = True
    for hours in (3, 3.1, 6, 9):
        provider.refresh(SETTINGS, now=NOW + timedelta(hours=hours))
    warnings = _source_logs(caplog, logging.WARNING)
    assert len(warnings) == 1
    assert "playstation" in warnings[0] and "blog.playstation.com: OSError: offline" in warnings[0]
    def changed(url):
        raise ValueError("unrecognized feed")
    provider.fetch_text = changed
    provider.refresh(SETTINGS, now=NOW + timedelta(hours=12))
    assert len(_source_logs(caplog, logging.WARNING)) == 2
    assert _source_logs(caplog, logging.INFO) == []
    provider.fetch_text = Web()
    provider.refresh(SETTINGS, now=NOW + timedelta(hours=15))
    provider.refresh(SETTINGS, now=NOW + timedelta(hours=18))
    recovered = _source_logs(caplog, logging.INFO)
    assert len(recovered) == 1 and "playstation" in recovered[0] and "recovered" in recovered[0]
    assert len(_source_logs(caplog, logging.WARNING)) == 2


def test_retained_evidence_missing_from_healthy_check_is_logged_once(tmp_path, caplog):
    caplog.set_level(logging.INFO, logger="plugins.simple_calendar.game_events")
    provider = GameEventProvider(tmp_path, fetch_text=Web())
    provider.refresh(SETTINGS, now=NOW)
    # The feed drops the story and the retained article no longer states a date:
    # no source error, yet the event will be pending, so the loss must be visible.
    provider.fetch_text = lambda url: ('<rss version="2.0"><channel><title>PS</title></channel></rss>' if url.endswith('/feed/')
                                       else '<html><h1>State of Play</h1><article>No announcement here</article></html>')
    result = provider.refresh(SETTINGS, now=NOW + timedelta(hours=3))
    assert result.sources["playstation"]["error"] == ""
    assert result.events[0]["pending_verification"] is True
    provider.refresh(SETTINGS, now=NOW + timedelta(hours=6))
    warnings = _source_logs(caplog, logging.WARNING)
    assert len(warnings) == 1
    assert "playstation" in warnings[0] and "blog.playstation.com/2026/08/31/state-of-play-returns/" in warnings[0]


def test_apple_source_failure_uses_shared_transition_log(tmp_path, caplog):
    from plugins.simple_calendar.apple_events import AppleEventProvider
    caplog.set_level(logging.INFO, logger="plugins.simple_calendar.game_events")
    def offline(url):
        raise OSError("offline")
    provider = AppleEventProvider(tmp_path, fetch_text=offline)
    provider.refresh({"showAppleEvents": "true"}, now=NOW)
    provider.refresh({"showAppleEvents": "true"}, now=NOW + timedelta(hours=3))
    warnings = _source_logs(caplog, logging.WARNING)
    assert len(warnings) == 1 and "apple_events" in warnings[0] and "apple.com: OSError" in warnings[0]


def test_new_reschedule_notice_reuses_original_event_identity(tmp_path):
    provider = GameEventProvider(tmp_path, fetch_text=Web())
    original = provider.refresh(SETTINGS, now=NOW).events[0]
    new_url = "https://blog.playstation.com/2026/09/02/state-of-play-rescheduled/"
    update = '<meta property="article:modified_time" content="2026-09-02T13:00:00Z"><h1>State of Play rescheduled</h1><article>The State of Play broadcast originally scheduled for September 3, 2026 has been rescheduled to September 4, 2026 at 7am PT.</article>'
    def fetch(url):
        if url == new_url:
            return update
        if url.endswith('/feed/'):
            return feed().replace('</channel>', '<item><title>State of Play rescheduled</title><link>' + new_url + '</link></item></channel>')
        return article()
    provider.fetch_text = fetch
    events = provider.refresh(SETTINGS, now=NOW + timedelta(hours=3)).events
    assert len(events) == 1
    assert events[0]["id"] == original["id"]
    assert events[0]["starts_at"] == "2026-09-04T14:00:00+00:00"
