"""The Local Events list as a notice board: each slip's kind, sticker, price tag and buying facts."""

from datetime import datetime, timezone

import pytest
from PIL import Image, ImageDraw

from plugins.ticketmaster_events.ticketmaster_events import TicketmasterEvent, TicketmasterEvents
from plugins.ticketmaster_events.week_page import (
    GENRE_ICON_DIR,
    GENRE_KINDS,
    SEGMENT_KINDS,
    _genre_icon,
    chip_ink,
    kind_key,
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

    from plugins.ticketmaster_events.week_page import LIST, ROW_H, SLIP_TOP

    teal = colors["segment_colors"]["sports"]
    for index in range(7):
        top = LIST[1] + SLIP_TOP + index * ROW_H
        assert image.getpixel((LIST[0] + 62, top + 32)) == teal


@pytest.mark.parametrize("mode", ["day", "night"])
def test_music_genres_have_distinct_colours_and_legible_labels(mode):
    from tests.test_ticketmaster_events import _canonical_theme

    plugin = TicketmasterEvents({"id": "ticketmaster_events"})
    colors = plugin._palette({"_inkypi_theme": _canonical_theme(mode)})
    genres = ["Pop", "Hip-Hop/Rap", "Dance/Electronic", "Alternative", "Rock", "Comedy"]
    palette = [plugin._event_color(event(genre=genre), colors) for genre in genres]

    assert len(set(palette)) == len(genres)
    for colour in palette + list(colors["segment_colors"].values()):
        assert contrast_ratio(colour, chip_ink(colour, colors)) >= 4.5


def test_type_label_icon_and_colour_follow_the_same_classification():
    plugin = TicketmasterEvents({"id": "ticketmaster_events"})
    colors = plugin._palette({})
    alternative = event("Music", "Rock", " Alternative ")
    assert kind_key(alternative) == "alternative"
    assert kind_of(alternative) == ("另类", "guitar")
    assert plugin._event_color(alternative, colors) == plugin._event_color(event(genre="Alternative"), colors)
    assert plugin._event_color(alternative, colors) != plugin._event_color(event(genre="Rock"), colors)
    # An unsupported subgenre falls back to the known genre, not a guess from the title.
    unknown = event("Music", "Pop", "Undefined", title="A Comedy of Errors")
    assert kind_key(unknown) == "pop"
    assert plugin._event_color(unknown, colors) == plugin._event_color(event(genre="Pop"), colors)
    assert plugin._kind_text(event("Music", "Comedy")) == "喜剧"
    assert kind_key(event("Film", "Comedy")) == "film"
    assert plugin._event_color(event("Film", "Comedy"), colors) == colors["segment_colors"]["film"]


@pytest.mark.parametrize("mode", ["day", "night"])
def test_dense_slip_preserves_distance_age_and_sale_state_without_overlapping(mode):
    from tests.test_ticketmaster_events import _canonical_theme

    plugin = TicketmasterEvents({"id": "ticketmaster_events"})
    colors = plugin._palette({"_inkypi_theme": _canonical_theme(mode)})
    image = Image.new("RGB", (800, 480), colors["paper"])
    draw = ImageDraw.Draw(image)
    calls = []
    original_text = draw.text

    def record_text(position, text, **kwargs):
        calls.append((text, draw.textbbox(position, text, font=kwargs["font"])))
        original_text(position, text, **kwargs)

    draw.text = record_text
    show = event("Music", "Dance/Electronic", title="An exceptionally long artist and tour title for this narrow list",
                 local_time="20:30:00", venue_name="An exceptionally long venue name that must fit", city="San Francisco",
                 distance="123.4 mi", status="rescheduled", price="$38-250",
                 extra={"age": "21+", "sale_start": "2026-10-28T17:00:00Z", "tour_dates": 30})
    plugin._draw_slip(image, draw, show, (344, 92, 776, 137), "今天", colors, NOW)

    labels = [text for text, _box in calls]
    assert "电子" in labels and "21+" in labels and "改期" in labels and "10/28 开售" in labels
    assert any(text.endswith("123.4 mi") for text in labels)
    assert all(344 <= box[0] <= box[2] <= 776 and 92 <= box[1] <= box[3] <= 137 for _text, box in calls)
    title_box = next(box for text, box in calls if text.startswith("An exceptionally long artist"))
    place_box = next(box for text, box in calls if text.endswith("123.4 mi"))
    fact_box = next(box for text, box in calls if text == "21+")
    assert title_box[3] <= place_box[1] and place_box[3] <= fact_box[1]


@pytest.mark.parametrize("marker", ["(SOLD OUT)", "[ sold out ]"])
def test_explicit_sold_out_title_wins_over_conflicting_onsale_zero_price_metadata(marker):
    plugin = TicketmasterEvents({"id": "ticketmaster_events"})
    colors = plugin._palette({})
    image = Image.new("RGB", (800, 480), colors["paper"])
    draw = ImageDraw.Draw(image)
    labels = []
    original_text = draw.text

    def record_text(position, text, **kwargs):
        labels.append(text)
        original_text(position, text, **kwargs)

    draw.text = record_text
    show = event("Music", "Alternative", "Indie Pop", title=f"Blu DeTiger - The Want You Baby Tonight Tour {marker}",
                 status="onsale", price="From $0", extra={"age": "21+"})
    original = show.to_dict()
    plugin._draw_slip(image, draw, show, (344, 92, 776, 137), "今天", colors, NOW)
    assert "售罄" in labels and "21+" in labels
    assert "免费" not in labels and "售票中" not in labels
    assert show.to_dict() == original


@pytest.mark.parametrize("title", ["Sold Out Tour", "Not sold out", "The Band (NOT SOLD OUT)", "The Band (Sold Out Soon)"])
def test_sold_out_is_not_guessed_from_loose_title_words(title):
    plugin = TicketmasterEvents({"id": "ticketmaster_events"})
    assert plugin._slip_facts(event(title=title, status="onsale"), NOW) == []
