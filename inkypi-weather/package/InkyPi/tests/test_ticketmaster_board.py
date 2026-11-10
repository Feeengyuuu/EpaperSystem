"""The Local Events list and the week's mix: each row's kind, sticker, price tag and buying facts."""

from datetime import datetime, timedelta, timezone

import pytest
from PIL import Image, ImageDraw

from plugins.ticketmaster_events.ticketmaster_events import TicketmasterEvent, TicketmasterEvents
from plugins.ticketmaster_events.week_page import (
    GENRE_ICON_DIR,
    GENRE_KINDS,
    OTHER_KINDS,
    SEGMENT_KINDS,
    UNCOUNTED,
    _genre_icon,
    chip_ink,
    category_color,
    category_label,
    event_category,
    kind_key,
    kind_of,
    price_label,
    sampled_mix,
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

    from plugins.ticketmaster_events.week_page import KIND_BAR, LIST, ROW_H, SLIP_TOP, THUMB, TIME_COLUMN

    sports = colors["segment_colors"]["sports"]
    for index in range(7):
        top = LIST[1] + SLIP_TOP + index * ROW_H
        # The kind's colour is a solid bar down the poster's left edge, as tall as the poster.
        bar = image.crop((LIST[0] + TIME_COLUMN, top + 3, LIST[0] + TIME_COLUMN + KIND_BAR, top + 3 + THUMB[1]))
        assert bar.getcolors() == [(KIND_BAR * THUMB[1], sports)]
        # A hairline parts the row from the one above; nothing frames it.
        assert image.getpixel((LIST[0] + 200, top - 1)) == colors["rule"]


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
    plugin._draw_slip(image, draw, show, (332, 92, 784, 138), "今天", colors, NOW)

    labels = [text for text, _box in calls]
    assert "电子" in labels and "21+" in labels and "改期" in labels and "10/28 开售" in labels
    assert any(text.endswith("123.4 mi") for text in labels)
    # The day's name sits on the hairline over the row; everything else stays within the row.
    day_box = next(box for text, box in calls if text == "今天")
    assert day_box[1] < 92 < day_box[3]
    assert all(332 <= box[0] <= box[2] <= 784 and 92 <= box[1] <= box[3] <= 138
               for text, box in calls if text != "今天")
    # The time over the kind's name, both left of the poster.
    time_box, kind_box = (next(box for text, box in calls if text == label) for label in ("20:30", "电子"))
    assert time_box[3] < kind_box[1] and max(time_box[2], kind_box[2]) <= 332 + 53
    title_box = next(box for text, box in calls if text.startswith("An exceptionally long artist"))
    place_box = next(box for text, box in calls if text.endswith("123.4 mi"))
    fact_box = next(box for text, box in calls if text == "21+")
    assert title_box[3] < place_box[1]
    assert abs(place_box[1] - fact_box[1]) <= 1
    facts = [box for text, box in calls if text in {"改期", "10/28 开售", "21+", "巡演"}]
    assert all(place_box[2] + 8 <= box[0] for box in facts)
    assert all(left[2] + 8 <= right[0] for left, right in zip(facts, facts[1:]))
    # The facts end at the row's right edge.
    assert 784 - 6 <= facts[-1][2] <= 784


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
    plugin._draw_slip(image, draw, show, (332, 92, 784, 138), "今天", colors, NOW)
    assert "售罄" in labels and "21+" in labels
    assert "免费" not in labels and "售票中" not in labels
    assert show.to_dict() == original


@pytest.mark.parametrize("title", ["Sold Out Tour", "Not sold out", "The Band (NOT SOLD OUT)", "The Band (Sold Out Soon)"])
def test_sold_out_is_not_guessed_from_loose_title_words(title):
    plugin = TicketmasterEvents({"id": "ticketmaster_events"})
    assert plugin._slip_facts(event(title=title, status="onsale"), NOW) == []


@pytest.mark.parametrize("status, label", [("postponed", "延期"), ("rescheduled", "改期")])
def test_featured_date_change_is_not_hidden_by_a_sold_out_title(status, label):
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
    show = event(title="Artist (SOLD OUT)", status=status, price="From $0")
    plugin._draw_featured(image, draw, show, NOW.date(), colors)
    assert label in labels and "售票中" not in labels and "免费" not in labels
    assert plugin._slip_facts(show, NOW)[:2] == [(label, True), ("售罄", True)]


def test_week_kinds_count_all_returned_rows_and_leave_unseen_events_unclassified():
    from tests.test_ticketmaster_events import _day_payload

    plugin = TicketmasterEvents({"id": "ticketmaster_events"})
    calls = []

    def fetch(params):
        calls.append(params)
        if "onsaleStartDateTime" in params:
            return {}
        payload = _day_payload(43, ["Music"] * 30, day=params["localStartDateTime"][:10])
        for index, row in enumerate(payload["_embedded"]["events"]):
            if index < 20:
                row["classifications"][0].update(genre={"name": "Rock"}, subGenre={"name": "Pop"})
            elif index < 28:
                row["classifications"][0]["genre"] = {"name": "Dance/Electronic"}
            else:
                row["classifications"] = []
        return payload

    plugin._fetch_ticketmaster_json = fetch
    events, week, _sales = plugin._load_overview({}, "key", NOW)
    day = week["days"][0]
    assert len(events) == 8 and len(calls) == 8  # Seven existing daily requests plus public sales.
    assert day["count"] == 43 and day["sample_count"] == 30
    assert day["kind_counts"] == {"music|pop": 20, "music|dance/electronic": 8, "|": 2}
    mix, sampled, total = sampled_mix([day], ["music|pop", "music|dance/electronic"])
    assert (sampled, total) == (30, 43)
    assert mix == {"music|pop": 20, "music|dance/electronic": 8, "|": 2, UNCOUNTED: 13}
    # A limited header never introduces an unexplained extra colour in the week bars.
    grouped, _sampled, _total = sampled_mix([day], ["music|pop"])
    assert grouped[OTHER_KINDS] == 8 and grouped[UNCOUNTED] == 13 and grouped["|"] == 2


@pytest.mark.parametrize("mode", ["day", "night"])
def test_each_listed_kind_keeps_its_own_colour_and_the_header_names_none(mode):
    from tests.test_ticketmaster_events import _canonical_theme

    plugin = TicketmasterEvents({"id": "ticketmaster_events"})
    colors = plugin._palette({"_inkypi_theme": _canonical_theme(mode)})
    shows = [event(genre=genre) for genre in ("Pop", "Hip-Hop/Rap", "Dance/Electronic", "Alternative", "Comedy")]
    image = Image.new("RGB", (800, 480), colors["paper"])
    draw = ImageDraw.Draw(image)
    labels = []
    original_text = draw.text

    def record(position, text, **kwargs):
        labels.append(text)
        original_text(position, text, **kwargs)

    draw.text = record
    groups = plugin._kind_groups(shows, [], colors)
    assert [keys for _colour, _label, keys in groups] == [[event_category(show)] for show in shows]
    for show, (colour, label, _keys) in zip(shows, groups):
        key = event_category(show)
        assert category_label(key) == kind_of(show)[0] == label
        assert category_color(key, colors) == plugin._event_color(show, colors) == colour
    # The header carries the title, the source and the summary: no legend of kinds.
    plugin._draw_page_header(image, draw, {}, NOW, False, [], colors)
    words = {label for _colour, label, _keys in groups} | {"音乐", "体育", "戏剧", "电影", "其他"}
    assert labels and not any(word in text for word in words for text in labels)


def test_old_week_cache_remains_neutral_counts_without_inventing_genre_percentages():
    from plugins.ticketmaster_events.week_page import WEEK_BAND

    plugin = TicketmasterEvents({"id": "ticketmaster_events"})
    colors = plugin._palette({})
    days = [{"date": "2026-10-26", "count": 43, "segments": {"music": 20, "arts & theatre": 10}}]
    image = Image.new("RGB", (800, 480), colors["paper"])
    draw = ImageDraw.Draw(image)
    labels = []
    original_text = draw.text

    def record(position, text, **kwargs):
        labels.append(text)
        original_text(position, text, **kwargs)

    draw.text = record
    plan = plugin._plan_section(draw, [], days, [], colors)
    plugin._draw_section(draw, plan, NOW.tzinfo, colors)
    plugin._draw_week_band(draw, days, NOW.date(), colors, plan["keys"])
    assert "大类样本 · 30/43场" in labels and "类型待更新" in labels
    assert "音乐 20" in labels and not any("%" in label or "流行" in label for label in labels)
    assert "kind_counts" not in days[0]
    # The day's bar is one neutral length, not shares of kinds nobody counted.
    x0, _y0, x1, y1 = WEEK_BAND
    bar = {image.getpixel((x, y1 - 3)) for x in range(x0, x0 + (x1 - x0) // 7)}
    assert bar == {colors["muted"], colors["paper"]}


def test_sample_bar_uses_total_events_as_its_denominator():
    plugin = TicketmasterEvents({"id": "ticketmaster_events"})
    colors = plugin._palette({})
    image = Image.new("RGB", (431, 12), colors["paper"])
    plugin._draw_mix_bar(ImageDraw.Draw(image), {"music|pop": 30, UNCOUNTED: 13},
                         (0, 0, 430, 10), colors, total=43)
    assert image.getpixel((299, 5)) == category_color("music|pop", colors)
    assert image.getpixel((300, 5)) == category_color(UNCOUNTED, colors)
    assert image.getpixel((429, 5)) == category_color(UNCOUNTED, colors)


def _legend(plugin, shows, days, colors, max_rows=3):
    image = Image.new("RGB", (800, 480), colors["paper"])
    return plugin._mix_legend(ImageDraw.Draw(image), shows, days, colors, 300, max_rows)


def test_additional_weekly_kinds_with_a_shared_fallback_colour_are_grouped_neutrally():
    plugin = TicketmasterEvents({"id": "ticketmaster_events"})
    colors = plugin._palette({})
    days = [{"count": 10, "sample_count": 10, "kind_counts": {"music|music": 2, "music|jazz": 8}}]
    shown, rows, counts, sampled, total = _legend(plugin, [event(genre="Other")], days, colors)
    assert shown == ["music|music"]
    mix, sampled, total = sampled_mix(days, shown)
    assert mix == {"music|music": 2, OTHER_KINDS: 8} and sampled == total == 10
    assert len({category_color(key, colors) for key in (OTHER_KINDS, "|", UNCOUNTED)}) == 3
    # The legend names only what the bar shows, each with its count, in the bar's order.
    assert [text for row in rows for _colour, text, _width in row] == ["音乐 2", "其他类型 8"]
    assert list(counts.items()) == [("music|music", 2), (OTHER_KINDS, 8)]


@pytest.mark.parametrize("mode", ["day", "night"])
def test_visible_shared_colour_has_one_named_legend_group_and_keeps_all_statistics_keys(mode):
    from tests.test_ticketmaster_events import _canonical_theme

    plugin = TicketmasterEvents({"id": "ticketmaster_events"})
    colors = plugin._palette({"_inkypi_theme": _canonical_theme(mode)})
    shows = [event(genre="Pop"), event(genre="R&B"), event(genre="Dance/Electronic")]
    days = [{"count": 43, "sample_count": 30,
             "kind_counts": {"music|pop": 10, "music|r&b": 12, "music|dance/electronic": 8}}]
    shown, rows, counts, sampled, total = _legend(plugin, shows, days, colors)
    assert set(shown) == {event_category(show) for show in shows}
    entries = [(colour, text) for row in rows for colour, text, _width in row]
    assert [text for _colour, text in entries] == ["流行 / R&B 22", "电子 8", "未统计 13"]
    assert entries[0][0] == category_color("music|pop", colors) == category_color("music|r&b", colors)
    assert all(sum(width for _colour, _text, width in row) <= 300 for row in rows)
    assert counts == {"music|pop": 10, "music|r&b": 12, "music|dance/electronic": 8, UNCOUNTED: 13}
    mix, sampled, total = sampled_mix(days, shown)
    assert mix["music|pop"] == 10 and mix["music|r&b"] == 12 and OTHER_KINDS not in mix
    assert (sampled, total, mix[UNCOUNTED]) == (30, 43, 13)


def test_a_legend_too_long_drops_the_counts_then_the_weeks_rarest_kinds():
    plugin = TicketmasterEvents({"id": "ticketmaster_events"})
    colors = plugin._palette({})
    kinds = {"music|pop": 30, "music|rock": 25, "music|hip-hop/rap": 20, "music|dance/electronic": 15,
             "music|alternative": 12, "arts & theatre|comedy": 11, "sports|basketball": 10, "film|film": 10,
             "arts & theatre|theatre": 10, "music|jazz": 10, "miscellaneous|fairs & festivals": 10}
    days = [{"count": sum(kinds.values()), "sample_count": sum(kinds.values()), "kind_counts": kinds}]
    shows = [event(genre="Alternative"), event("Film", "Drama")]

    def entries(rows):
        return [text for row in rows for _colour, text, _width in row]

    # Three rows hold every kind with its count.
    shown, rows, counts, _sampled, _total = _legend(plugin, shows, days, colors)
    assert set(shown) == set(kinds) and counts == kinds and len(rows) == 3
    assert "流行 30" in entries(rows) and len(entries(rows)) == len(kinds)
    assert all(sum(width for _colour, _text, width in row) <= 300 for row in rows)

    # Two rows: every kind keeps its colour, named without its count.
    shown, rows, counts, _sampled, _total = _legend(plugin, shows, days, colors, max_rows=2)
    assert set(shown) == set(kinds) and len(rows) == 2
    assert "流行" in entries(rows) and "流行 30" not in entries(rows)

    # One row: the rarest of the week's kinds join the neutral bin; the listed events' kinds stay.
    shown, rows, counts, _sampled, _total = _legend(plugin, shows, days, colors, max_rows=1)
    assert len(rows) == 1 and {"music|alternative", "film|film"} <= set(shown) < set(kinds)
    assert "music|pop" in shown and "miscellaneous|fairs & festivals" not in shown
    assert counts[OTHER_KINDS] == sum(count for key, count in kinds.items() if key not in shown)
    assert entries(rows)[-1] == f"其他类型 {counts[OTHER_KINDS]}" and sum(counts.values()) == sum(kinds.values())


def test_the_featured_columns_foot_holds_the_sales_over_the_mix_and_ends_at_the_columns_foot():
    from plugins.ticketmaster_events.week_page import HERO, LEGEND_ROWS, SALE_ROWS

    plugin = TicketmasterEvents({"id": "ticketmaster_events"})
    colors = plugin._palette({})
    days = [{"count": 43, "sample_count": 30, "kind_counts": {"music|pop": 18, "music|rock": 12}}]
    sales = [{"title": f"Tour {index}", "sale_start": "2026-10-28T17:00:00Z", "kind": "music|pop"} for index in range(4)]
    image = Image.new("RGB", (800, 480), colors["paper"])
    draw = ImageDraw.Draw(image)
    calls = []
    original_text = draw.text

    def record(position, text, **kwargs):
        calls.append((text, draw.textbbox(position, text, font=kwargs["font"])))
        original_text(position, text, **kwargs)

    draw.text = record
    quiet = plugin._plan_section(draw, [event(genre="Pop")], days, [], colors)
    plan = plugin._plan_section(draw, [event(genre="Pop")], days, sales, colors)
    assert len(plan["sales"]) == SALE_ROWS and len(plan["rows"]) <= LEGEND_ROWS - 1
    assert quiet["height"] < plan["height"] <= HERO[3] - HERO[1] - 116 - 165
    plugin._draw_section(draw, plan, NOW.tzinfo, colors)
    labels = [text for text, _box in calls]
    assert labels.index("即将开售") < labels.index("Tour 0") < labels.index("类型分布") < labels.index("流行 18")
    assert "已统计 30/43 场" in labels and "未统计 13" in labels and "Tour 2" not in labels
    top = HERO[3] - plan["height"]
    assert all(HERO[0] <= box[0] <= box[2] <= HERO[2] + 1 and top < box[1] <= box[3] <= HERO[3] for _text, box in calls)
    # One stacked bar across the column: the kinds in the legend's order, then what was never sampled.
    bar_y = next(box for text, box in calls if text == "类型分布")[3] + 10
    assert image.getpixel((HERO[0] + 2, bar_y)) == category_color("music|pop", colors)
    assert image.getpixel((HERO[2] - 3, bar_y)) == category_color(UNCOUNTED, colors)


def test_the_weeks_bars_stand_on_one_baseline_as_long_as_each_days_share_of_the_busiest():
    from plugins.ticketmaster_events.week_page import WEEK_BAND, WEEK_BAR_H

    plugin = TicketmasterEvents({"id": "ticketmaster_events"})
    colors = plugin._palette({})
    counts = (10, 40, 20, 0, 5, 30, 40)
    days = [{"date": (NOW.date() + timedelta(days=index)).isoformat(), "count": count, "sample_count": count,
             "kind_counts": {"music|pop": count}} for index, count in enumerate(counts)]
    image = Image.new("RGB", (800, 480), colors["paper"])
    draw = ImageDraw.Draw(image)
    drawn = []
    original_text = draw.text

    def record(position, text, **kwargs):
        drawn.append((text, kwargs["fill"]))
        original_text(position, text, **kwargs)

    draw.text = record
    plugin._draw_week_band(draw, days, NOW.date(), colors, ["music|pop"])

    x0, y0, x1, y1 = WEEK_BAND
    pop = category_color("music|pop", colors)
    lengths = []
    for index in range(7):
        left = round(x0 + index * (x1 - x0) / 7)
        row = [image.getpixel((x, y1 - 2)) for x in range(left, round(x0 + (index + 1) * (x1 - x0) / 7))]
        lengths.append(row.count(pop))
        assert image.getpixel((left, y1 - WEEK_BAR_H - 1)) == colors["paper"]
    assert lengths[3] == 0 and min(lengths[1], lengths[6]) >= max(lengths) - 1 > 40
    assert all(abs(length - lengths[1] * count / 40) <= 2 for length, count in zip(lengths, counts))
    # Every day is named with its date and count; today's count takes the accent.
    assert [text for text, _fill in drawn][:3] == ["今天 10/26", "10", "场"]
    assert [fill for text, fill in drawn if text in {"10", "40", "20", "0", "5", "30"}] == [
        colors["accent"], *[colors["ink"]] * 6]
