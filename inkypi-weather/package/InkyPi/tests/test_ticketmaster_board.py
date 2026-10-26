"""The Local Events list as a notice board: each slip's kind, sticker, price tag and buying facts."""

from datetime import datetime, timezone

import pytest

from plugins.ticketmaster_events.ticketmaster_events import TicketmasterEvent, TicketmasterEvents
from plugins.ticketmaster_events.week_page import (
    GENRE_ICON_DIR,
    GENRE_KINDS,
    SEGMENT_KINDS,
    _genre_icon,
    kind_of,
    price_label,
)
from utils.theme_utils import contrast_ratio, relative_luminance

NOW = datetime(2026, 10, 26, 23, 0, tzinfo=timezone.utc)


def event(segment="Music", genre="Rock", subgenre="", **fields):
    fields.setdefault("title", "Show")
    return TicketmasterEvent(rank=1, segment=segment, genre=genre,
                             extra={"subgenre": subgenre, **fields.pop("extra", {})}, **fields)


@pytest.mark.parametrize("segment, genre, subgenre, kind", [
    ("Music", "Rock", "Psychedelic Rock", ("摇滚", "guitar")),
    ("Arts & Theatre", "Theatre", "Musical", ("音乐剧", "masks")),
    ("Sports", "Basketball", "NBA", ("篮球", "basketball")),
    ("Miscellaneous", "Ice Shows", "", ("冰演", "skate")),
    ("Film", "Comedy", "", ("电影", "film")),
    ("Music", "Chanson Francaise", "", ("音乐", "microphone")),
    ("", "", "", ("", "ticket")),
])
def test_each_kind_has_a_chinese_name_and_a_sticker(segment, genre, subgenre, kind):
    assert kind_of(event(segment, genre, subgenre)) == kind


def test_every_named_sticker_is_shipped():
    icons = {icon for _label, icon in [*GENRE_KINDS.values(), *SEGMENT_KINDS.values()]}
    assert all((GENRE_ICON_DIR / f"{icon}.png").is_file() for icon in icons | {"ticket"})


@pytest.mark.parametrize("price, label, lowest", [
    ("From $45", "$45 起", "$45 起"), ("$45-129", "$45–129", "$45 起"), ("From $0", "免费", "免费"),
    ("$0-0", "免费", "免费"), ("", "", ""),
])
def test_prices_read_as_a_tag(price, label, lowest):
    assert price_label(price) == label and price_label(price, lowest=True) == lowest


@pytest.mark.parametrize("row, age", [
    ({"pleaseNote": "This event is 21+. Valid ID required."}, "21+"),
    ({"info": "Ages 18 and over."}, "18+"),
    ({"ageRestrictions": {"legalAgeEnforced": True}}, "限龄"),
    ({"pleaseNote": "All ages welcome."}, "全龄"),
    ({"ageRestrictions": {"legalAgeEnforced": False}}, ""),
])
def test_the_age_limit_comes_from_the_notes_or_the_enforced_flag(row, age):
    assert TicketmasterEvents._age_limit(row) == age


def test_discovery_rows_carry_the_family_and_multi_day_flags():
    plugin = TicketmasterEvents({"id": "ticketmaster_events"})
    row = {"id": "e1", "name": "Disney On Ice", "pleaseNote": "All ages",
           "dates": {"start": {"localDate": "2026-10-28", "localTime": "18:30:00"}, "spanMultipleDays": True,
                     "status": {"code": "onsale"}},
           "classifications": [{"segment": {"name": "Miscellaneous"}, "genre": {"name": "Ice Shows"}, "family": True}],
           "_embedded": {"venues": [{"name": "SAP Center", "city": {"name": "San Jose"}}]}}

    extra = plugin._events_from_discovery({"_embedded": {"events": [row]}}, 5)[0].extra

    assert (extra["family"], extra["multi_day"], extra["age"]) == (True, True, "全龄")


def test_a_slips_facts_put_a_moved_date_first_then_what_to_know_before_buying():
    plugin = TicketmasterEvents({"id": "ticketmaster_events"})
    show = event("Miscellaneous", "Fairs & Festivals", status="rescheduled",
                 extra={"sale_start": "2026-10-28T17:00:00Z", "age": "21+", "more_showtimes": 2, "tour_dates": 30,
                        "family": True, "multi_day": True})

    facts = plugin._slip_facts(show, NOW)

    assert facts == [("改期", True), ("10/28 开售", False), ("21+", False), ("+2 场", False), ("巡演", False),
                     ("亲子", False), ("多日", False)]
    # A sale that has opened, a small tour and an on-sale show say nothing; a family kind is not repeated.
    quiet = event("Miscellaneous", "Family", status="onsale",
                  extra={"sale_start": "2026-10-01T17:00:00Z", "tour_dates": 3, "family": True})
    assert plugin._slip_facts(quiet, NOW) == []


def test_a_sticker_keeps_its_outline_on_the_night_paper():
    paper, ink = (0, 0, 0), (238, 238, 231)
    art = _genre_icon("guitar", 24, paper, ink)

    pixels = [art.getpixel((x, y)) for y in range(art.height) for x in range(art.width)]
    vanishing = [pixel for pixel in pixels if pixel[3] >= 8
                 and relative_luminance(pixel[:3]) < 0.18 and contrast_ratio(pixel[:3], paper) < 2.0]
    assert max(art.size) == 24 and not vanishing


def test_the_board_renders_a_slip_per_event_with_its_kind_colour(tmp_path):
    plugin = TicketmasterEvents({"id": "ticketmaster_events"})
    colors = plugin._palette({})
    events = [event("Sports", "Hockey", title=f"Game {index}", local_date="2026-10-26", local_time="19:30:00",
                    venue_name="SAP Center", city="San Jose", price="$38-250", distance="14.8 mi")
              for index in range(8)]
    for index, show in enumerate(events):
        show.rank = index + 1

    image = plugin._render_events((800, 480), events, {}, "Ticketmaster Discovery", NOW)

    from plugins.ticketmaster_events.week_page import LIST, ROW_H, SLIP_H, SLIP_TOP

    teal = colors["segment_colors"]["sports"]
    for index in range(7):
        top = LIST[1] + SLIP_TOP + index * ROW_H
        assert image.getpixel((LIST[0] + 10, top + SLIP_H // 2)) == teal
