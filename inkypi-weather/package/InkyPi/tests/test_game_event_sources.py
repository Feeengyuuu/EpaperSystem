"""Source documents are the public parsing boundary; fixtures contain no live IO."""
from datetime import date, datetime, timezone
import json
import pytest

from plugins.simple_calendar.game_event_sources import parse_announcement, parse_source_page

NOW = datetime(2026, 9, 2, tzinfo=timezone.utc)


def test_sony_announcement_uses_broadcast_time_not_article_timestamp():
    events = parse_announcement(
        """<html><meta property="article:published_time" content="2026-08-31T12:00:00Z">
        <h1>State of Play returns on September 3</h1><article>
        <p>State of Play returns Thursday, September 3.</p>
        <p>Watch the broadcast live on September 3 starting at 6:00am PT / 9:00am ET.</p>
        </article></html>""",
        url="https://blog.playstation.com/2026/08/31/state-of-play-returns/",
        source_id="playstation",
        now=datetime(2026, 9, 2, tzinfo=timezone.utc),
    )
    assert len(events) == 1
    assert events[0].family == "state_of_play"
    assert events[0].event_date == date(2026, 9, 3)
    assert events[0].starts_at == datetime(2026, 9, 3, 13, tzinfo=timezone.utc)


@pytest.mark.parametrize("title,body,family,expected", [
    ("TGA Returns December 10, 2026", "The Game Awards will take place on Thursday, December 10, 2026.", "the_game_awards", None),
    ("Xbox Games Showcase returns June 7, 2026", "Tune in June 7, 2026 at 10am PT for the Xbox Games Showcase.", "xbox_showcase", datetime(2026, 6, 7, 17, tzinfo=timezone.utc)),
    ("Developer_Direct returns January 22, 2026", "Watch Developer_Direct live January 22, 2026 at 10am PT.", "xbox_showcase", datetime(2026, 1, 22, 18, tzinfo=timezone.utc)),
])
def test_official_articles_preserve_date_only_and_dst(title, body, family, expected):
    item, = parse_announcement(f"<h1>{title}</h1><article>{body}</article>", url="https://news.xbox.com/en-us/2026/01/01/event/", source_id="xbox", now=NOW)
    assert item.family == family
    assert item.starts_at == expected


@pytest.mark.parametrize("machine_day", ["03", "04"])
def test_nintendo_explicit_pacific_time_overrides_mislabeled_json_utc(machine_day):
    data = {"props": {"pageProps": {"initialApolloState": {"NintendoDirect:1": {
        "__typename": "NintendoDirect", "id": "direct-1", "name": "Nintendo Direct",
        "slug": "9-3-2026", "startDate": f"2026-09-{machine_day}T07:00:00.000Z",
        "description": {"text": "Tune in September 3, 2026 at 7am PT for a new Nintendo Direct."},
    }}}}}
    items, _ = parse_source_page("nintendo", '<script id="__NEXT_DATA__" type="application/json">' + json.dumps(data) + '</script>', "https://www.nintendo.com/us/nintendo-direct/", NOW)
    assert len(items) == 1
    assert items[0].starts_at == datetime(2026, 9, 3, 14, tzinfo=timezone.utc)


@pytest.mark.parametrize("body", [
    "Watch State of Play live September 3, 2026 at 6am PT.",
    "Sony has not confirmed State of Play. Watch the broadcast live September 3, 2026 at 6am PT.",
])
def test_media_must_explicitly_report_confirmation_even_with_a_link(body):
    html = '<h1>State of Play September 3 livestream</h1><article>' + body + '<a href="https://blog.playstation.com/2026/08/31/state-of-play/">official page</a></article>'
    assert parse_announcement(html, url="https://www.ign.com/articles/state-of-play", source_id="ign", now=NOW) == []


def test_ics_developer_direct_keeps_same_edition_as_official_article():
    ics = 'BEGIN:VCALENDAR\nVERSION:2.0\nBEGIN:VEVENT\nUID:xbox-direct\nSUMMARY:Xbox Developer_Direct\nDTSTART:20270121T180000Z\nEND:VEVENT\nEND:VCALENDAR'
    item, = parse_source_page("sgf", ics, "https://www.addevent.com/feed/eeidoioaw.ics", NOW)[0]
    assert item.edition == "developer_direct"


@pytest.mark.parametrize("status", ["confirmed", "cancelled"])
def test_gamescom_selects_main_event_across_split_rsc_chunks(status):
    frame = 'a:' + json.dumps({"relatedEvent": [{"id": "onl-2026", "slug": "gamescom-opening-night-live-2026", "title": "gamescom Opening Night Live 2026", "startsAt": "2026-08-25T20:00:00+02:00", "endsAt": "2026-08-25T22:00:00+02:00", "status": status.upper()}], "video": {"title": "Opening Night Live (w. Pre-show)", "startsAt": "2026-08-25T17:30:00Z"}}) + '\n'
    split = len(frame) // 2
    html = ''.join('<script>self.__next_f.push(' + json.dumps([1, part]) + ')</script>' for part in (frame[:split], frame[split:]))
    items, _ = parse_source_page("gamescom", html, "https://www.gamescom.global/en/program", NOW)
    assert len(items) == 1
    assert items[0].starts_at == datetime(2026, 8, 25, 18, tzinfo=timezone.utc)
    assert items[0].status == status


def test_gamescom_rsc_text_length_frame_can_end_immediately_before_event_json():
    raw_text = '<p>多字节文本\nf:[this is text</p>'
    payload = {"relatedEvent": [{"id": "onl-2026", "slug": "gamescom-opening-night-live-2026", "title": "gamescom Opening Night Live 2026", "startsAt": "2026-08-25T18:00:00Z"}]}
    frame = ':HL["/static/main.css","style"]\n' + f'1:T{len(raw_text.encode("utf-8")):x},' + raw_text + '2:' + json.dumps(payload) + '\n'
    html = '<script>self.__next_f.push(' + json.dumps([1, frame]) + ')</script>'
    item, = parse_source_page("gamescom", html, "https://www.gamescom.global/en/program", NOW)[0]
    assert item.starts_at == datetime(2026, 8, 25, 18, tzinfo=timezone.utc)


def test_calendar_revision_wins_over_old_entry_later_in_feed():
    ics = 'BEGIN:VCALENDAR\nVERSION:2.0\n'
    for revision, status in ((2, 'CANCELLED'), (1, 'CONFIRMED')):
        ics += f'BEGIN:VEVENT\nUID:sgf\nSUMMARY:Summer Game Fest Live\nDTSTART:20270604T210000Z\nSEQUENCE:{revision}\nSTATUS:{status}\nLAST-MODIFIED:20260902T120000Z\nEND:VEVENT\n'
    ics += 'END:VCALENDAR'
    item, = parse_source_page("sgf", ics, "https://www.addevent.com/feed/eeidoioaw.ics", NOW)[0]
    assert item.status == "cancelled"
    assert item.published_at == datetime(2026, 9, 2, 12, tzinfo=timezone.utc)


def test_sgf_calendar_keeps_uid_and_cancellation():
    ics = """BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VEVENT
UID:sgf-2026
SUMMARY:Summer Game Fest Live 2026
DTSTART;TZID=America/Los_Angeles:20260605T140000
END:VEVENT
BEGIN:VEVENT
UID:tga-2026
SUMMARY:The Game Awards 2026
DTSTART;VALUE=DATE:20261210
STATUS:CANCELLED
END:VEVENT
END:VCALENDAR
"""
    items, _ = parse_source_page("sgf", ics, "https://www.addevent.com/feed/eeidoioaw.ics", NOW)
    assert [(i.family, i.external_id, i.status) for i in items] == [("summer_game_fest", "sgf-2026", "confirmed"), ("the_game_awards", "tga-2026", "cancelled")]
    assert items[0].starts_at == datetime(2026, 6, 5, 21, tzinfo=timezone.utc)


@pytest.mark.parametrize("title,body", [
    ("Rumor: Nintendo Direct expected September 3", "Insiders expect Nintendo Direct on September 3 at 7am PT."),
    ("Everything announced at State of Play September 3", "The broadcast has ended. Game launches September 10 at 6am PT."),
    ("Nintendo Direct might air next week", "A game is released September 3, 2026 at 7am PT."),
])
def test_rumors_recaps_and_release_dates_do_not_become_broadcasts(title, body):
    assert parse_announcement(f"<h1>{title}</h1><article>{body}</article>", url="https://www.ign.com/articles/story", source_id="ign", now=NOW) == []


def test_tga_discovers_future_news_and_combines_only_event_faq_answers():
    payload = {"posts": [
        {"slug": "tga-returns-december-10-2026", "title": "TGA Returns December 10, 2026", "date": "2026-08-10T09:00:12"},
        {"slug": "the-game-awards-breaks-viewership-record", "title": "The Game Awards 2025 Breaks Record: 171 Million Livestreams"},
    ]}
    html = '<script>self.__next_f.push(' + json.dumps([1, 'a:' + json.dumps(payload) + '\n']) + ')</script>'
    _, links = parse_source_page("tga", html, "https://thegameawards.com/news", NOW)
    assert links == ["https://thegameawards.com/news/tga-returns-december-10-2026"]
    faq = {"items": [
        {"title": "When is The Game Awards?", "content": "The Game Awards will take place on December 10, 2026.", "category": "The Event"},
        {"title": "How can I watch the show?", "content": "Watch the show live starting at 4:30pm PT.", "category": "The Event"},
        {"title": "When does voting close?", "content": "December 8, 2026 at 6am PT.", "category": "Voting"},
    ]}
    html = '<script>self.__next_f.push(' + json.dumps([1, 'a:' + json.dumps(faq) + '\n']) + ')</script>'
    item, = parse_source_page("tga", html, "https://thegameawards.com/faq", NOW)[0]
    assert item.starts_at == datetime(2026, 12, 11, 0, 30, tzinfo=timezone.utc)


@pytest.mark.parametrize("year", [2026, 2027])
def test_tga_streams_live_faq_supplies_confirmed_time(year):
    faq = {"items": [
        {"title": "When is The Game Awards?", "content": f"The Game Awards streams live on December 10, {year}.", "category": "The Event"},
        {"title": "How can I watch the show?", "content": "The Game Awards will be available to stream, with the opening act starting at 7:30p ET / 4:30p PT.", "category": "The Event"},
    ]}
    html = '<script>self.__next_f.push(' + json.dumps([1, 'a:' + json.dumps(faq) + '\n']) + ')</script>'
    item, = parse_source_page("tga", html, "https://thegameawards.com/faq", NOW)[0]
    assert item.starts_at == datetime(year, 12, 11, 0, 30, tzinfo=timezone.utc)


def test_unrelated_official_store_link_does_not_qualify_single_media_report():
    item, = parse_announcement('<h1>Nintendo Direct confirmed September 3, 2026</h1><article>Nintendo confirmed Nintendo Direct will air September 3, 2026 at 7am PT. <a href="https://www.nintendo.com/us/store/products/some-game/">Buy the game</a></article>', url="https://www.ign.com/articles/direct-announcement", source_id="ign", now=NOW)
    assert item.official_url == ""


def test_reschedule_text_overrides_old_date_in_unchanged_article_title():
    item, = parse_announcement('<h1>State of Play returns September 3, 2026</h1><article>Update: State of Play has been rescheduled to September 4, 2026 at 7am PT.</article>', url="https://blog.playstation.com/2026/08/31/state-of-play/", source_id="playstation", now=NOW)
    assert item.starts_at == datetime(2026, 9, 4, 14, tzinfo=timezone.utc)


@pytest.mark.parametrize("verb", ["rescheduled", "moved"])
def test_rescheduled_from_old_date_to_new_date_binds_each_date_correctly(verb):
    item, = parse_announcement(f'<h1>State of Play {verb}</h1><article>The State of Play broadcast has been {verb} from September 3, 2026 to September 4, 2026 at 7am PT.</article>', url="https://blog.playstation.com/2026/09/02/state-of-play-update/", source_id="playstation", now=NOW)
    assert item.starts_at == datetime(2026, 9, 4, 14, tzinfo=timezone.utc)
    assert item.previous_date == date(2026, 9, 3)


def test_negated_cancellation_keeps_confirmed_broadcast():
    item, = parse_announcement('<h1>State of Play September 3, 2026</h1><article>State of Play will air September 3, 2026 at 6am PT. The broadcast has not been cancelled.</article>', url="https://blog.playstation.com/2026/09/02/state-of-play/", source_id="playstation", now=NOW)
    assert item.status == "confirmed"


def test_nintendo_description_can_explicitly_cancel_a_known_direct():
    payload = {"event": {"__typename": "NintendoDirect", "id": "direct", "slug": "9-3-2026", "name": "Nintendo Direct", "startDate": "2026-09-03T07:00:00Z", "description": {"text": "The Nintendo Direct scheduled for September 3, 2026 at 7am PT has been cancelled."}}}
    html = '<script id="__NEXT_DATA__">' + json.dumps(payload) + '</script>'
    item, = parse_source_page("nintendo", html, "https://www.nintendo.com/us/nintendo-direct/", NOW)[0]
    assert item.status == "cancelled"


def test_relative_following_show_does_not_get_made_up_time():
    item, = parse_announcement('<h1>State of Play Japan returns September 3, 2026</h1><article>State of Play Japan will air immediately following State of Play. The first broadcast starts at 6am PT.</article>', url="https://blog.playstation.com/2026/08/31/state-of-play-japan/", source_id="playstation", now=NOW)
    assert item.starts_at is None


def test_explicit_new_year_date_does_not_get_inferred_from_article_date():
    item, = parse_announcement('<h1>State of Play returns January 4, 2027</h1><article>Watch State of Play live January 4, 2027 at 8pm PT.</article>', url="https://blog.playstation.com/2026/12/28/state-of-play/", source_id="playstation", now=NOW)
    assert item.starts_at == datetime(2027, 1, 5, 4, tzinfo=timezone.utc)


def test_media_body_selector_avoids_main_publication_date_and_supports_day_first():
    html = '<main><p>Updated August 31, 2026</p><h1>Sony confirms next State of Play</h1><article><div class="article_body_content"><p>Sony announced State of Play will air Thursday, 3rd September, 2026 at 6am PT.</p></div></article></main>'
    item, = parse_announcement(html, url="https://www.eurogamer.net/sony-playstation-state-of-play-september-date-time", source_id="eurogamer", now=NOW)
    assert item.starts_at == datetime(2026, 9, 3, 13, tzinfo=timezone.utc)


@pytest.mark.parametrize("title,body", [
    ("State of Play June 2026: all the announcements", "The new game will be released October 2, 2026. Watch its trailer live."),
    ("Marvel game at State of Play", "We announced a new game at State of Play. It returns on September 15, 2026."),
])
def test_game_news_mentioning_show_cannot_create_release_date_event(title, body):
    assert parse_announcement(f'<h1>{title}</h1><article>{body}</article>', url="https://blog.playstation.com/2026/06/02/game/", source_id="playstation", now=NOW) == []


def test_xbox_follow_on_direct_is_still_xbox_and_short_meridiem_is_supported():
    item, = parse_announcement('<h1>Xbox Games Showcase 2026 Followed by Gears of War: E-Day Direct Airs June 7</h1><article>Xbox Games Showcase will air June 7, 2026 at 10a Pacific.</article>', url="https://news.xbox.com/en-us/2026/03/30/showcase/", source_id="xbox", now=NOW)
    assert item.family == "xbox_showcase"
    assert item.starts_at == datetime(2026, 6, 7, 17, tzinfo=timezone.utc)


def test_html_challenge_is_failure_not_successful_empty_calendar():
    from plugins.simple_calendar.game_event_sources import fetch_source
    for source in ("nintendo", "gamescom", "tga"):
        batch = fetch_source(source, lambda _: '<html><title>Please wait</title><body>Checking your browser</body></html>', [], NOW)
        assert batch.announcements == []
        assert batch.errors
