"""Local Events' keyless sources beside Ticketmaster: DoTheBay's list of today and Luma's San
Francisco feed. The fixtures are cut from the two feeds' answers of 2026-11-11."""

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from PIL import Image, ImageDraw
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from plugins.ticketmaster_events import local_sources, week_page  # noqa: E402
from plugins.ticketmaster_events.ticketmaster_events import TicketmasterEvent, TicketmasterEvents  # noqa: E402
from tests.test_ticketmaster_events import DummyDeviceConfig, _canonical_theme  # noqa: E402

LA = ZoneInfo("America/Los_Angeles")
NOW = datetime(2026, 11, 11, 12, 55, tzinfo=LA)
HOME = (37.5202, -121.9264)
ASSET = "https://assets0.dostuffmedia.com/uploads/aws_asset/aws_asset/"


def _dothebay_event(identity, title, category, begin, adjusted, votes, ticket_info, venue, **more):
    name, city, lat, lon = venue
    return {"id": identity, "permalink": f"/events/2026/11/11/{identity}", "title": title, "category": category,
            "begin_time": f"{begin}-06:00", "tz_adjusted_begin_date": adjusted, "begin_date": begin[:10],
            "end_date": None, "votes": votes, "ticket_info": ticket_info, "is_free": False, "sold_out": False,
            "past": False, "is_ongoing": False, "buy_url": "https://example.org/tickets",
            "imagery": {"aws": {"cover_image_w_1200_h_450": f"{ASSET}{identity}/wide.jpg",
                                "cover_image_h_250_w_680": f"{ASSET}{identity}/strip.jpg",
                                "cover_image_h_50_w_50": f"{ASSET}{identity}/icon.jpg"}},
            "venue": {"title": name, "latitude": lat, "longitude": lon, "city": city, "state": "CA"}, **more}


DOTHEBAY = {"date": "2026-11-11", "paging": {"count": 66, "page_size": 25}, "events": [
    _dothebay_event(16881125, "Ólafur Arnalds", "Music", "2026-11-11T19:00:00", "2026-11-11T19:00:00-08:00", 261,
                    "All ages", ("The Castro", "San Francisco", 37.7619917, -122.4347365), sold_out=True,
                    buy_url="https://ticketmaster.evyy.net/c/253185/264167/4272?SharedId=DoStuff&u=https%3A%2F%2F"
                            "www.ticketmaster.com%2Folafur-arnalds-san-francisco%2Fevent%2F1C006425D688A714"),
    _dothebay_event(17640776, "Soft Cell", "Music", "2026-11-11T19:00:00", "2026-11-11T19:00:00-08:00", 137,
                    "All Ages", ("The Regency Ballroom", "San Francisco", 37.7879104, -122.4215079),
                    buy_url="https://www.theregencyballroom.com/events/detail/?event_id=1527107"),
    _dothebay_event(18024107, "The 30th Arab Film Festival", "Film", "2026-11-05T18:00:00",
                    "2026-11-05T18:00:00-08:00", 47, "", ("Various Locations", None, None, None), is_ongoing=True,
                    end_date="2026-11-14"),
    _dothebay_event(17610860, "Buzzcocks, Modern English", "Music", "2026-11-11T20:00:00",
                    "2026-11-11T20:00:00-08:00", 27, "$37.50-$42.50, All Ages",
                    ("The Chapel", "San Francisco", 37.760565, -122.421188)),
    _dothebay_event(16225298, "North Beach Food Tour", "Food & Drink", "2026-11-11T11:00:00",
                    "2026-09-09T11:00:00-07:00", 30, "$125", ("North Beach", "San Francisco", 37.7994642, -122.40891)),
    # A weekly night: its adjusted date is the series' first, its time of day still tonight's.
    _dothebay_event(13624765, "Karaoke Night", "Music", "2026-11-11T21:00:00", "2025-06-04T21:00:00-07:00", 9, "free",
                    ("Club 1220", "Walnut Creek", 37.910809, -122.060582), is_free=True),
    _dothebay_event(18061603, "Chuckles Comedy Show", "Comedy", "2026-11-11T20:00:00", "2026-11-11T20:00:00-08:00", 3,
                    "Sliding Scale $5-$10", ("El Rio", "San Francisco", 37.7468046, -122.4194395), is_free=True,
                    buy_url=None),
]}


def _luma_entry(identity, name, start, city, guests, host, address=None, coordinate=None, **ticket):
    geo = {"mode": "shown" if address else "obfuscated", "city": city, "city_state": f"{city}, CA",
           **({"address": address} if address else {})}
    return {"api_id": identity, "start_at": start, "guest_count": guests, "calendar": {"name": host},
            "hosts": [{"name": "Tina Wisdom"}],
            "event": {"api_id": identity, "name": name, "start_at": start, "timezone": "America/Los_Angeles",
                      "url": identity[4:12].lower(), "location_type": "offline", "visibility": "public",
                      "cover_url": f"https://images.lumacdn.com/event-covers/gx/{identity}.png",
                      "geo_address_info": geo, "coordinate": coordinate or {"latitude": 37.7780597,
                                                                            "longitude": -122.3994279}},
            "ticket_info": {"price": None, "is_free": True, "max_price": None, "is_sold_out": False,
                            "spots_remaining": None, "require_approval": False, **ticket}}


LUMA = {"has_more": True, "next_cursor": "eyJzdiI6", "entries": [
    _luma_entry("evt-BS4YQShJdrlKR5K", "Stripe Startups Build Day San Francisco", "2026-11-11T17:30:00.000Z",
                "South San Francisco", 0, "Stripe Startups", is_sold_out=True, require_approval=True),
    _luma_entry("evt-ypf0JB6FQZbXM8z", "SGLang x NVIDIA Meetup", "2026-11-12T01:30:00.000Z", "San Francisco", 859,
                "NVIDIA", coordinate={"latitude": 37.804504068620005, "longitude": -122.42593822698764},
                require_approval=True),
    _luma_entry("evt-nKtRnTU6YARvNW3", "Vlad Tenev (Co-Founder, RobinHood & HarmonicMath) @ Stanford AI Club",
                "2026-11-12T01:30:00.000Z", "Stanford", 1130, "Personal",
                address="Stanford University CEMEX Auditorium",
                coordinate={"longitude": -122.1630388, "latitude": 37.4286576}),
    _luma_entry("evt-53wpIQqBOkbBbUA", "From Bridgerton to Severance: The Art of the Film Score",
                "2026-11-12T03:00:00.000Z", "Berkeley", 74, "Big Brain Lectures - Bay Area",
                address="Donkey & Goat Winery", coordinate={"longitude": -122.3022627, "latitude": 37.878082899999995},
                is_free=False, price={"cents": 2700, "currency": "usd", "is_flexible": False}, spots_remaining=29),
    _luma_entry("evt-vHGzJ7vbVRF6ADG", "Humanmade Hardware Accelerator Demo Day", "2026-11-13T01:00:00.000Z",
                "San Francisco", 530, "Personal", address="655 Bryant St", spots_remaining=70),
]}


def _state(now=NOW):
    stamp = now.astimezone(timezone.utc).isoformat()
    return {"dothebay": {"fetched_at": stamp, "events": local_sources.parse_dothebay(DOTHEBAY)},
            "luma": {"fetched_at": stamp, "events": local_sources.parse_luma(LUMA)}}


def _ticketmaster(count=8, day="2026-11-11"):
    rows = [("Teddy Swims: The UGLY Tour", "19:00:00", "Chase Center"), ("Sparta, Public Opinion", "19:00:00", "The Ritz"),
            ("Peter Pan Goes Wrong", "19:00:00", "San Francisco Playhouse"), ("Jamie Kaler", "19:30:00", "Punch Line"),
            ("Aterciopelados", "20:00:00", "The UC Theatre"), ("The Damned", "20:00:00", "The Fillmore"),
            ("Brand New", "20:30:00", "Bill Graham Civic Auditorium"), ("Late Show", "22:00:00", "Cobb's Comedy Club")]
    return [TicketmasterEvent(rank=index + 1, title=title, event_id=f"tm{index}", local_date=day, local_time=clock,
                              venue_name=venue, city="San Francisco", segment="Music", genre="Rock", status="onsale",
                              distance="30.0 mi", extra={"source": "ticketmaster", "has_art": True})
            for index, (title, clock, venue) in enumerate(rows[:count])]


# --- the two feeds' parsers -----------------------------------------------------------------------

def test_dothebays_list_gives_todays_shows_with_their_time_place_and_price():
    rows = local_sources.parse_dothebay(DOTHEBAY)

    # The festival that began last week has no start of its own today; the rest keep the feed's order.
    assert [row["title"] for row in rows] == ["Ólafur Arnalds", "Soft Cell", "Buzzcocks, Modern English",
                                              "North Beach Food Tour", "Karaoke Night", "Chuckles Comedy Show"]
    soft_cell = rows[1]
    # The time of day is the adjusted date's (begin_time's offset is wrong), on the feed's day.
    assert (soft_cell["source"], soft_cell["id"], soft_cell["start"]) == ("dothebay", "17640776", "2026-11-11T19:00:00")
    assert (soft_cell["venue"], soft_cell["city"]) == ("The Regency Ballroom", "San Francisco")
    assert (soft_cell["segment"], soft_cell["genre"], soft_cell["popularity"]) == ("Music", "", 137)
    assert soft_cell["image"] == f"{ASSET}17640776/strip.jpg" and soft_cell["age"] == "全龄"
    assert soft_cell["url"] == "https://dothebay.com/events/2026/11/11/17640776"
    assert not soft_cell["ticketmaster"] and rows[0]["ticketmaster"] and rows[0]["sold_out"]
    # A weekly night starts tonight at its usual hour, whatever day its series began.
    assert rows[4]["start"] == "2026-11-11T21:00:00" and rows[4]["price"] == "From $0"
    assert rows[2]["price"] == "$38-42" and rows[3]["price"] == "From $125" and rows[5]["price"] == "From $0"
    assert (rows[5]["segment"], rows[5]["genre"]) == ("Arts & Theatre", "Comedy")
    assert (rows[3]["segment"], rows[3]["genre"]) == ("Miscellaneous", "Food & Drink")
    for broken in ({}, {"events": "none"}, [], None):
        with pytest.raises(ValueError):
            local_sources.parse_dothebay(broken)
    assert local_sources.parse_dothebay({"date": "2026-11-11", "events": [{"title": "No venue"}, "junk"]}) == []


def test_lumas_feed_gives_the_meetups_with_their_host_size_and_terms():
    rows = local_sources.parse_luma(LUMA)

    assert [row["title"] for row in rows][:2] == ["Stripe Startups Build Day San Francisco", "SGLang x NVIDIA Meetup"]
    nvidia = rows[1]
    assert (nvidia["source"], nvidia["start"]) == ("luma", "2026-11-12T01:30:00+00:00")
    # A hidden address leaves the host to say where; a shown one is the place.
    assert (nvidia["venue"], nvidia["city"], nvidia["popularity"]) == ("NVIDIA", "San Francisco", 859)
    assert nvidia["approval"] and nvidia["price"] == "From $0" and not nvidia["sold_out"]
    assert (nvidia["segment"], nvidia["genre"]) == ("Miscellaneous", "Tech Meetup")
    assert nvidia["url"] == "https://lu.ma/ypf0jb6f"
    # The cover is asked at a size near the row's picture, not as uploaded.
    assert nvidia["image"] == ("https://images.lumacdn.com/cdn-cgi/image/fit=cover,quality=75,width=400,height=225/"
                               "event-covers/gx/evt-ypf0JB6FQZbXM8z.png")
    assert rows[2]["venue"] == "Stanford University CEMEX Auditorium" and rows[0]["sold_out"]
    assert rows[3]["price"] == "From $27" and rows[4]["venue"] == "655 Bryant St"
    with pytest.raises(ValueError):
        local_sources.parse_luma({"entries": None})
    online = _luma_entry("evt-online0000001", "Online only", "2026-11-12T01:30:00.000Z", "San Francisco", 9, "X")
    online["event"]["location_type"] = "online"
    assert local_sources.parse_luma({"entries": [online, "junk", {}]}) == []


# --- choosing and merging ------------------------------------------------------------------------

def test_the_keyless_rows_are_todays_most_popular_shows_ticketmaster_does_not_already_list():
    picked = local_sources.pick_local_events(_state(), _ticketmaster(), NOW, HOME, 50, limit=3)

    # DoTheBay's two best of tonight that are not Ticketmaster's own (Ólafur Arnalds is sold there)
    # and Luma's biggest meetup of today, in start order.
    assert [(row["extra"]["source"], row["title"]) for row in picked] == [
        ("luma", "Vlad Tenev (Co-Founder, RobinHood & HarmonicMath) @ Stanford AI Club"),
        ("dothebay", "Soft Cell"), ("dothebay", "Buzzcocks, Modern English")]
    meetup, soft_cell, buzzcocks = picked
    assert (meetup["local_date"], meetup["local_time"]) == ("2026-11-11", "17:30:00")
    assert (soft_cell["local_date"], soft_cell["local_time"]) == ("2026-11-11", "19:00:00")
    assert soft_cell["event_id"] == "dothebay-17640776" and soft_cell["status"] == ""
    # The distance is from home, as Ticketmaster gives it for its own rows.
    assert soft_cell["distance"] == "32.8 mi" and meetup["distance"] == "14.4 mi"
    assert buzzcocks["price"] == "$38-42" and buzzcocks["extra"]["age"] == "全龄"
    assert meetup["extra"]["guests"] == 1130 and not meetup["extra"].get("approval")
    assert all(row["extra"]["has_art"] and row["image_url"] == row["poster_url"] for row in picked)
    # Events from these rows become the page's own event records.
    assert TicketmasterEvent.from_dict(soft_cell).venue_name == "The Regency Ballroom"


def test_what_has_started_what_is_far_and_what_ticketmaster_lists_are_left_out():
    late = NOW.replace(hour=19, minute=30)
    picked = local_sources.pick_local_events(_state(late), _ticketmaster(), late, HOME, 50, limit=3)
    # After 19:30 only what starts later is left: tonight's 20:00 show, the 21:00 night and the
    # late show, and of Luma tomorrow's biggest.
    assert [row["title"] for row in picked] == ["Buzzcocks, Modern English", "Karaoke Night",
                                                "Humanmade Hardware Accelerator Demo Day"]
    assert picked[2]["local_date"] == "2026-11-12"

    # A show Ticketmaster lists the same day is not listed twice, however each names it.
    listed = _ticketmaster(2) + [TicketmasterEvent(rank=3, title="Soft Cell - The Final Tour", local_date="2026-11-11",
                                                   local_time="19:00:00", venue_name="The Regency Ballroom")]
    titles = [row["title"] for row in local_sources.pick_local_events(_state(), listed, NOW, HOME, 50, limit=3)]
    assert "Soft Cell" not in titles and "Buzzcocks, Modern English" in titles

    near = [row["title"] for row in local_sources.pick_local_events(_state(), _ticketmaster(), NOW, HOME, 20, limit=3)]
    # Twenty miles from home reaches Stanford, not San Francisco.
    assert near == ["Vlad Tenev (Co-Founder, RobinHood & HarmonicMath) @ Stanford AI Club"]
    assert local_sources.pick_local_events({}, _ticketmaster(), NOW, HOME, 50, limit=3) == []
    assert local_sources.pick_local_events(_state(), _ticketmaster(), NOW, HOME, 50, limit=0) == []
    # Without a known home nothing is measured and nothing is cut by distance.
    unmeasured = local_sources.pick_local_events(_state(), _ticketmaster(), NOW, None, 50, limit=3)
    assert len(unmeasured) == 3 and all(row["distance"] == "" for row in unmeasured)


def test_the_list_keeps_ticketmasters_featured_show_and_most_of_its_rows():
    plugin = TicketmasterEvents({"id": "ticketmaster_events"})
    events = _ticketmaster()
    extras = [TicketmasterEvent.from_dict(row)
              for row in local_sources.pick_local_events(_state(), events, NOW, HOME, 50, limit=3)]

    merged = plugin._merge_local_events(events, extras)

    assert len(merged) == 8 and merged[0].title == "Teddy Swims: The UGLY Tour"
    assert [event.rank for event in merged] == list(range(1, 9))
    sources = [(event.extra or {}).get("source") for event in merged]
    # Four of the seven rows stay Ticketmaster's (its three latest give way), all by start time.
    assert sources.count("ticketmaster") == 5 and sources.count("dothebay") == 2 and sources.count("luma") == 1
    assert [event.local_time for event in merged[1:]] == sorted(event.local_time for event in merged[1:])
    assert merged[1].title.startswith("Vlad Tenev") and merged[-1].title == "Buzzcocks, Modern English"
    # Nothing to add leaves the list as it was.
    assert plugin._merge_local_events(events, []) is events


# --- the caches -----------------------------------------------------------------------------------

def test_each_feed_is_asked_once_per_lifetime_and_keeps_its_last_answer_when_it_fails():
    calls = []

    def fetchers(fail=()):
        def ask(name, payload):
            def fetch():
                calls.append(name)
                if name in fail:
                    raise OSError("provider down")
                return payload
            return fetch
        return {"dothebay": ask("dothebay", DOTHEBAY), "luma": ask("luma", LUMA)}

    first = local_sources.load_local_sources({}, NOW, fetchers())
    assert calls == ["dothebay", "luma"] and len(first["dothebay"]["events"]) == 6 and len(first["luma"]["events"]) == 5

    # Within FRESH_HOURS the kept answer is used and no feed is asked.
    calls.clear()
    again = local_sources.load_local_sources(first, NOW + timedelta(hours=2), fetchers())
    assert calls == [] and again == first

    # Later both are asked; the one that fails keeps its last answer...
    later = NOW + timedelta(hours=local_sources.FRESH_HOURS + 1)
    kept = local_sources.load_local_sources(first, later, fetchers(fail={"dothebay"}))
    assert calls == ["dothebay", "luma"]
    assert kept["dothebay"] == first["dothebay"] and kept["luma"]["fetched_at"] != first["luma"]["fetched_at"]
    # ...until it is USABLE_HOURS old, then it has none; a failure is never an error for the page.
    old = NOW + timedelta(hours=local_sources.USABLE_HOURS + 1)
    gone = local_sources.load_local_sources(first, old, fetchers(fail={"dothebay", "luma"}))
    assert gone == {"dothebay": {"fetched_at": "", "events": []}, "luma": {"fetched_at": "", "events": []}}
    # Nonsense in the page's cache is no answer either.
    assert local_sources.load_local_sources({"dothebay": "junk", "luma": {"events": 3}}, NOW,
                                            fetchers(fail={"dothebay", "luma"})) == gone


def test_the_feeds_are_asked_through_the_shared_client_with_bounded_answers(monkeypatch):
    asked = []

    class Client:
        def request_json(self, method, url, **options):
            asked.append((method, url, options))
            return type("Result", (), {"data": DOTHEBAY if "dothebay" in url else LUMA})()

    monkeypatch.setattr(local_sources, "get_http_client", lambda: Client())
    state = local_sources.load_local_sources({}, NOW)

    assert [(method, url) for method, url, _options in asked] == [
        ("GET", "https://dothebay.com/events/today.json"), ("GET", "https://api.lu.ma/discover/get-paginated-events")]
    assert all(options["max_bytes"] == local_sources.MAX_BYTES and options["timeout"] <= 12 for _m, _u, options in asked)
    assert asked[1][2]["params"] == {"discover_place_api_id": "discplace-BDj7GNbGlsF7Cka", "pagination_limit": "20"}
    # DoTheBay answers a plain product token; a User-Agent carrying an address gets its 403.
    assert asked[0][2]["headers"]["User-Agent"] == "InkyPi-LocalEvents/1.0"
    assert len(state["dothebay"]["events"]) == 6
    # At most KEPT rows of a feed are kept in the page's state.
    assert all(len(source["events"]) <= local_sources.KEPT for source in state.values())


def test_by_default_the_feeds_are_offline_in_tests_and_the_page_goes_on():
    # conftest keeps the module's client away: the loader finds none and leaves the feeds out.
    assert local_sources.load_local_sources({}, NOW) == {"dothebay": {"fetched_at": "", "events": []},
                                                         "luma": {"fetched_at": "", "events": []}}


# --- the page's refresh ---------------------------------------------------------------------------

def _refreshing_plugin(monkeypatch, tmp_path, load_local):
    monkeypatch.setenv("INKYPI_TICKETMASTER_EVENTS_CACHE", str(tmp_path))
    plugin = TicketmasterEvents({"id": "ticketmaster_events"})
    monkeypatch.setattr(plugin, "_load_overview", lambda _settings, _key, _now: (_ticketmaster(), {}, []))
    monkeypatch.setattr(plugin, "_download_event_images", lambda _events, _dimensions: None)
    monkeypatch.setattr(plugin, "_write_ticketmaster_context", lambda *_args: None)
    monkeypatch.setattr(plugin, "_now_for_device", lambda _device: NOW)
    monkeypatch.setattr(local_sources, "load_local_sources", load_local)
    rendered = []
    monkeypatch.setattr(plugin, "_render_events", lambda _dimensions, events, *_args, **_kwargs: rendered.append(
        list(events)) or Image.new("RGB", (800, 480), "white"))
    return plugin, rendered


def test_a_refresh_merges_the_keyless_rows_and_a_redraw_reads_them_from_the_cache(monkeypatch, tmp_path):
    calls = []
    plugin, rendered = _refreshing_plugin(monkeypatch, tmp_path, lambda previous, now: calls.append(previous) or _state())
    monkeypatch.setattr(plugin, "_cache_is_fresh", lambda cache, *_args: bool(cache.get("events")))
    settings = {"apiKey": "test-key", "_inkypi_theme": _canonical_theme("day")}

    plugin.generate_image(settings, DummyDeviceConfig())

    assert calls == [{}]
    assert [(event.extra or {}).get("source") for event in rendered[0]].count("dothebay") == 2
    cache = plugin._read_cache()
    assert set(cache["local_sources"]) == {"dothebay", "luma"} and len(cache["events"]) == 8
    # A theme redraw and a fresh cache draw the same merged rows without asking any feed.
    plugin.generate_image({**settings, "_theme_render_only": True}, DummyDeviceConfig())
    plugin.generate_image(settings, DummyDeviceConfig())
    assert len(calls) == 1 and len(rendered) == 3
    assert [event.title for event in rendered[1]] == [event.title for event in rendered[0]] == [
        event.title for event in rendered[2]]
    # The next refresh hands the kept answers back to the loader.
    plugin.generate_image({**settings, "forceRefresh": True}, DummyDeviceConfig())
    assert calls[1] == cache["local_sources"]


def test_a_loader_that_fails_leaves_ticketmasters_page_as_it_is(monkeypatch, tmp_path):
    def broken(_previous, _now):
        raise RuntimeError("both feeds down")

    plugin, rendered = _refreshing_plugin(monkeypatch, tmp_path, broken)
    image = plugin.generate_image({"apiKey": "test-key", "_inkypi_theme": _canonical_theme("day")}, DummyDeviceConfig())

    assert [event.title for event in rendered[0]] == [event.title for event in _ticketmaster()]
    assert image.info.get("inkypi_skip_cache") is None
    assert plugin._read_cache()["local_sources"] == {}


# --- the page ------------------------------------------------------------------------------------

def _page(mode, events, tmp_path):
    poster = tmp_path / "poster.png"
    Image.new("RGB", (320, 180), (120, 40, 60)).save(poster)
    for event in events:
        event.poster_path = str(poster)
    plugin = TicketmasterEvents({"id": "ticketmaster_events"})
    settings = {"_inkypi_theme": _canonical_theme(mode), "postalCode": "94539", "radiusMiles": 50}
    return plugin, settings, plugin._render_events((800, 480), events, settings, "Ticketmaster Discovery", NOW)


def _merged_events():
    plugin = TicketmasterEvents({"id": "ticketmaster_events"})
    events = _ticketmaster()
    return plugin._merge_local_events(events, [TicketmasterEvent.from_dict(row) for row in
                                               local_sources.pick_local_events(_state(), events, NOW, HOME, 50, limit=3)])


@pytest.mark.parametrize("mode", ["day", "night"])
def test_a_keyless_row_carries_its_sources_logo_and_the_header_names_the_sources_in_use(mode, tmp_path):
    plugin, settings, page = _page(mode, _merged_events(), tmp_path)
    _plugin, _settings, plain = _page(mode, _ticketmaster(), tmp_path)
    colors = plugin._palette(settings)
    ink = None if mode == "day" else tuple(colors["ink"])

    # The header: Ticketmaster's wordmark, then DoTheBay's emblem and Luma's wordmark, before the summary.
    ticketmaster = week_page._ticketmaster_logo(week_page.TICKETMASTER_LOGO_HEIGHT)
    start = (week_page.MARGIN + week_page._header_wordmark_width(*week_page.HEADER_WORDMARK)
             + week_page.SOURCE_LOGO_GAP + ticketmaster.width)
    marks = [week_page.source_logo(name, week_page.HEADER_SOURCE_HEIGHTS[name], ink) for name in ("dothebay", "luma")]
    assert all(mark is not None for mark in marks)
    band = (start + 4, 0, start + sum(mark.width for mark in marks) + 2 * week_page.SOURCE_LOGO_GAP, week_page.HEADER_H)
    assert page.crop(band).tobytes() != plain.crop(band).tobytes()
    paper_only = plain.crop(band)
    assert max(count for count, _colour in paper_only.getcolors(100000)) > 0.9 * paper_only.width * paper_only.height

    # Each keyless row ends its second line with its source's logo; Ticketmaster's rows carry none.
    x0, y0, x1, _y1 = week_page.LIST
    for index, event in enumerate(_merged_events()[1:]):
        source = (event.extra or {}).get("source")
        top = y0 + week_page.SLIP_TOP + index * week_page.ROW_H
        if source == "ticketmaster":
            continue
        mark = week_page.source_logo(source, week_page.SLIP_SOURCE_HEIGHTS[source], ink)
        corner = page.crop((x1 - mark.width, top + 34 - mark.height // 2, x1, top + 34 - mark.height // 2 + mark.height))
        expected = Image.new("RGB", mark.size, colors["paper"])
        expected.paste(mark, (0, 0), mark)
        differing = sum(a != b for a, b in zip(corner.get_flattened_data(), expected.get_flattened_data()))
        # The logo sits straight on the page (a quiet doodle stroke may cross it): no plate, no frame.
        assert differing <= 0.12 * mark.width * mark.height, (source, differing)


def test_without_keyless_rows_the_page_is_drawn_exactly_as_before(tmp_path):
    # Ticketmaster's own rows carry no mark and the header names Ticketmaster alone.
    events = _ticketmaster()
    for event in events:
        event.extra = {"has_art": True}
    _plugin, _settings, legacy = _page("day", events, tmp_path)
    _plugin, _settings, marked = _page("day", _ticketmaster(), tmp_path)
    assert legacy.tobytes() == marked.tobytes()


def test_a_keyless_rows_chips_say_free_sold_out_approval_and_size():
    plugin = TicketmasterEvents({"id": "ticketmaster_events"})
    event = TicketmasterEvent(rank=2, title="SGLang x NVIDIA Meetup", local_date="2026-11-11", local_time="17:30:00",
                              price="From $0", extra={"source": "luma", "approval": True, "guests": 859})
    assert plugin._slip_facts(event, NOW) == [("需审批", False), ("859 人", False)]
    assert week_page.price_label(event.price, lowest=True) == "免费"
    sold = TicketmasterEvent(rank=3, title="Ólafur Arnalds", extra={"source": "dothebay", "sold_out": True, "age": "全龄"})
    assert plugin._slip_facts(sold, NOW) == [("售罄", True), ("全龄", False)]
    # A meetup is its own kind, with a name and a sticker.
    meetup = TicketmasterEvent(rank=4, title="Meetup", segment="Miscellaneous", genre="Tech Meetup")
    assert week_page.kind_of(meetup) == ("科技", "microphone")


def test_the_two_logos_are_the_sites_own_and_recorded_with_their_sources():
    import json

    record = json.loads((Path(week_page.__file__).with_name("assets") / "source_logos.source.json").read_text(
        encoding="utf-8"))["logos"]
    assert record["dothebay"]["page"] == "https://dothebay.com/" and record["luma"]["page"] == "https://lu.ma/sf"
    for name in ("dothebay", "luma"):
        logo = week_page.source_logo(name, 20)
        assert logo is not None and logo.height == 20 and logo.mode == "RGBA"
        # Dark lettering by day; the page's ink by night.
        night = week_page.source_logo(name, 20, (238, 238, 231))
        dark = [pixel for pixel in logo.get_flattened_data() if pixel[3] > 200]
        light = [pixel for pixel in night.get_flattened_data() if pixel[3] > 200]
        assert dark and max(max(pixel[:3]) for pixel in dark) < 80
        assert light and min(min(pixel[:3]) for pixel in light) > 180
    assert week_page.source_logo("ticketmaster", 20) is None and week_page.source_logo("", 20) is None


def test_the_header_draws_a_source_only_while_it_clears_the_summary():
    plugin = TicketmasterEvents({"id": "ticketmaster_events"})
    settings = {"_inkypi_theme": _canonical_theme("day"), "postalCode": "94539", "radiusMiles": 250}
    colors = plugin._palette(settings)
    image = Image.new("RGB", (800, 480), colors["paper"])
    draw = ImageDraw.Draw(image)
    days = [{"date": "2026-11-11", "count": 1234567}]

    plugin._draw_page_header(image, draw, settings, NOW, True, days, colors, sources=("dothebay", "luma"))

    # However long the summary, nothing is drawn within 12 px of it.
    note = f"94539 周边 250 英里 · 缓存 {NOW:%m/%d %H:%M} 更新"
    left = 784 - draw.textlength(note, font=plugin._font(13, bold=True))
    gap = image.crop((round(left) - 12, 0, round(left) - 1, week_page.HEADER_H))
    assert gap.getcolors() == [(gap.width * gap.height, colors["paper"])]
