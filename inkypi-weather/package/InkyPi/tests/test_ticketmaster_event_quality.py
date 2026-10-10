"""Discovery rows become distinct, public events with their own artwork.

Ticketmaster marks generic category art (the swirl, the blue bokeh) with
``fallback: true`` and serves it from ``/dam/c/``; real artwork lives under
``/dam/a/`` on the event, its attraction or its venue.
"""

from plugins.ticketmaster_events.ticketmaster_events import TicketmasterEvent, TicketmasterEvents

CATEGORY_ART = "https://s1.ticketm.net/dam/c/ed6/swirl_106201_TABLET_LANDSCAPE_LARGE_16_9.jpg"


def image(name, width=1024, height=576, ratio="16_9", fallback=False, folder="a"):
    return {"url": f"https://s1.ticketm.net/dam/{folder}/{name}.jpg", "ratio": ratio,
            "width": width, "height": height, "fallback": fallback}


def row(event_id, name, date="2026-10-10", time="19:30:00", venue="The Fillmore",
        images=None, attraction_images=None, venue_images=None, segment="Music", genre="Rock", **start):
    embedded = {"venues": [{"name": venue, "city": {"name": "San Francisco"},
                            "state": {"stateCode": "CA"}, "images": venue_images or []}]}
    if attraction_images is not None:
        embedded["attractions"] = [{"name": name, "images": attraction_images}]
    return {
        "id": event_id, "name": name, "distance": 3.0,
        "images": images if images is not None else [image(f"{event_id}-poster")],
        "dates": {"start": {"localDate": date, "localTime": time, **start}, "status": {"code": "onsale"}},
        "classifications": [{"segment": {"name": segment}, "genre": {"name": genre}}],
        "_embedded": embedded,
    }


def parse(*rows, count=5):
    plugin = TicketmasterEvents({"id": "ticketmaster_events"})
    return plugin._events_from_discovery({"_embedded": {"events": list(rows)}}, count)


def test_category_fallback_art_is_never_chosen_over_real_artwork():
    images = [image("big-swirl", 2048, 1152, fallback=True, folder="c"),
              {"url": CATEGORY_ART, "ratio": "16_9", "width": 1024, "height": 576},
              image("real-small", 640, 360), image("real-large", 1024, 576)]

    [event] = parse(row("1", "Show", images=images))

    assert event.image_url.endswith("/dam/a/real-large.jpg")


def test_event_with_only_category_art_uses_its_attraction_or_venue_artwork():
    category_only = [image("swirl", fallback=True, folder="c")]

    attraction, venue = parse(
        row("1", "Thomas and Percy's Halloween Party", images=category_only,
            attraction_images=[image("thomas-tank", fallback=False)]),
        row("2", "Harvest Fair", images=category_only, attraction_images=category_only,
            venue_images=[image("roaring-camp", 1024, 683, ratio="3_2")]),
    )

    assert attraction.image_url.endswith("/dam/a/thomas-tank.jpg")
    assert venue.image_url.endswith("/dam/a/roaring-camp.jpg")


def test_event_without_any_real_artwork_keeps_no_image_for_the_drawn_placeholder():
    [event] = parse(row("1", "Mystery Night", images=[image("bokeh", fallback=True, folder="c")]))

    assert event.image_url == ""


def test_private_venue_holds_are_not_listed():
    events = parse(row("1", "Private Event", time="00:00:00"), row("2", "PRIVATE EVENT - Hall B"),
                   row("3", "Public Concert"))

    assert [event.title for event in events] == ["Public Concert"]


def test_repeat_showtimes_collapse_into_one_listing_with_the_count():
    events = parse(
        row("a", "Thomas and Percy's Halloween Party", time="10:00:00", venue="Roaring Camp"),
        row("b", "Thomas and Percy's Halloween Party", time="11:00:00", venue="Roaring Camp"),
        row("c", "Thomas and Percy's Halloween Party", time="12:00:00", venue="Roaring Camp"),
        row("d", "Golden Bears Football", time="12:30:00", venue="Memorial Stadium"),
        row("e", "Thomas and Percy's Halloween Party", date="2026-10-11", time="10:00:00", venue="Roaring Camp"),
    )

    assert [(event.title, event.local_date, event.local_time) for event in events] == [
        ("Thomas and Percy's Halloween Party", "2026-10-10", "10:00:00"),
        ("Golden Bears Football", "2026-10-10", "12:30:00"),
        ("Thomas and Percy's Halloween Party", "2026-10-11", "10:00:00"),
    ]
    assert events[0].extra["more_showtimes"] == 2
    assert [event.rank for event in events] == [1, 2, 3]
    assert "+2 more times" in TicketmasterEvents({"id": "ticketmaster_events"})._tag_line(events[0])


def test_undefined_classifications_are_not_shown():
    [event] = parse(row("1", "Train Ride", segment="Undefined", genre="Undefined"))
    plugin = TicketmasterEvents({"id": "ticketmaster_events"})

    assert (event.segment, event.genre) == ("", "")
    assert plugin._tag_line(event) == "3.0 mi"


def test_events_without_a_specific_time_do_not_claim_midnight():
    [event] = parse(row("1", "All Day Festival", time="00:00:00", noSpecificTime=True))
    plugin = TicketmasterEvents({"id": "ticketmaster_events"})

    assert event.local_time == ""
    assert "12:00" not in plugin._detail_line(event)


def test_discovery_request_asks_for_enough_rows_to_fill_after_collapsing():
    plugin = TicketmasterEvents({"id": "ticketmaster_events"})

    assert int(plugin._base_params({}, "key", 5)["size"]) >= 20


def test_cached_events_from_before_the_artwork_fix_are_refetched():
    from plugins.ticketmaster_events import ticketmaster_events as module

    plugin = TicketmasterEvents({"id": "ticketmaster_events"})
    old = {"version": "ticketmaster-events-v1", "cache_key": "k", "generated_at": "2099-01-01T00:00:00+00:00",
           "events": [TicketmasterEvent(rank=1, title="Old", image_url=CATEGORY_ART).to_dict()]}

    assert module.STATE_VERSION != "ticketmaster-events-v1"
    assert plugin._cache_is_fresh(old, "k", 3) is False
