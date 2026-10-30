from datetime import date

from PIL import ImageDraw
import pytest

from plugins.simple_calendar.simple_calendar import SimpleCalendar, LOCALE_DATA


def events():
    return [
        {"date": date(2026, 9, 9), "label": "GAME", "time": "07:00", "title": "Nintendo Direct 9.9.2026"},
        {"date": date(2026, 9, 9), "label": "APPLE", "time": "10:00", "title": "苹果特别活动：新品发布会"},
        {"date": date(2026, 9, 10), "label": "CN", "title": "教师节"},
        {"date": date(2026, 9, 10), "label": "ME", "title": "AppleCare"},
        {"date": date(2026, 9, 10), "label": "ME", "title": "Pay Salary/401(k)"},
        {"date": date(2026, 9, 25), "label": "CN", "title": "中秋节"},
    ]


def test_upcoming_events_keep_each_source_and_its_full_details():
    plugin = SimpleCalendar({"id": "simple_calendar"})
    rows = plugin._upcoming_event_rows(events(), date(2026, 9, 8))
    assert len(rows) == 6
    assert [r["label"] for r in rows[:2]] == ["GAME", "APPLE"]
    assert rows[0]["title"] == "07:00 Nintendo Direct 9.9.2026"
    assert rows[1]["title"] == "10:00 苹果特别活动：新品发布会"


@pytest.mark.parametrize("layout", ["left", "right"])
def test_september_agenda_shows_six_separate_events_without_label_overlap(monkeypatch, layout):
    records = []
    original = ImageDraw.ImageDraw.text

    def text(draw, xy, value, *args, **kwargs):
        records.append((str(value), draw.textbbox(xy, value, font=kwargs.get("font"), anchor=kwargs.get("anchor")), kwargs.get("font")))
        return original(draw, xy, value, *args, **kwargs)

    monkeypatch.setattr(ImageDraw.ImageDraw, "text", text)
    plugin = SimpleCalendar({"id": "simple_calendar"})
    result = plugin._render_calendar(
        (800, 480), date(2026, 9, 8), (30, 80, 110), (180, 20, 20),
        LOCALE_DATA["en"], "en", layout_position=layout, holiday_events=events(),
    )
    titles = [(t, b, f) for t, b, f in records if any(t.endswith(e["title"]) for e in events())]
    assert len(titles) == 6
    # Six events fit one column: whole titles, in the agenda's title size on an 800 x 480 page.
    assert {f.size for _, _, f in titles} == {14}
    assert len({box[1] for _, box, _ in titles}) == 6
    for _, box, _ in titles:
        assert 0 <= box[0] < box[2] <= 800
        assert 0 <= box[1] < box[3] <= 480
        labels = [b for t, b, _ in records if t in {"GAME", "APPLE", "CN", "ME"} and abs((b[1] + b[3]) / 2 - (box[1] + box[3]) / 2) < 7]
        assert labels and max(b[2] for b in labels) + 5 <= box[0]
    result.close()


@pytest.mark.parametrize("dimensions", [(800, 480), (640, 384)])
def test_six_week_agenda_stays_inside_the_screen(monkeypatch, dimensions):
    boxes = []
    original = ImageDraw.ImageDraw.text

    def text(draw, xy, value, *args, **kwargs):
        if kwargs.get("anchor") == "lm":
            boxes.append(draw.textbbox(xy, value, font=kwargs.get("font"), anchor="lm"))
        return original(draw, xy, value, *args, **kwargs)

    monkeypatch.setattr(ImageDraw.ImageDraw, "text", text)
    august = [{**e, "date": date(2026, 8, e["date"].day)} for e in events()]
    plugin = SimpleCalendar({"id": "simple_calendar"})
    result = plugin._render_calendar(
        dimensions, date(2026, 8, 1), (30, 80, 110), (180, 20, 20),
        LOCALE_DATA["en"], "en", holiday_events=august,
    )
    assert boxes
    assert all(0 <= b[0] < b[2] <= dimensions[0] and 0 <= b[1] < b[3] <= dimensions[1] for b in boxes)
    result.close()


def _recording(monkeypatch):
    records = []
    original = ImageDraw.ImageDraw.text

    def text(draw, xy, value, *args, **kwargs):
        records.append((str(value), draw.textbbox(xy, value, font=kwargs.get("font"), anchor=kwargs.get("anchor")),
                        kwargs.get("font")))
        return original(draw, xy, value, *args, **kwargs)

    monkeypatch.setattr(ImageDraw.ImageDraw, "text", text)
    return records


def _disjoint(boxes):
    return all(a[2] <= b[0] or b[2] <= a[0] or a[3] <= b[1] or b[3] <= a[1]
               for i, a in enumerate(boxes) for b in boxes[i + 1:])


@pytest.mark.parametrize("layout", ["left", "right"])
def test_a_long_agenda_runs_on_into_a_second_column(monkeypatch, layout):
    records = _recording(monkeypatch)
    later = [
        {"date": date(2026, 9, 26), "label": "GAME", "title": "东京电玩展"},
        {"date": date(2026, 9, 27), "label": "ME", "title": "家庭聚会"},
        {"date": date(2026, 9, 28), "label": "ME", "title": "体检"},
    ]
    plugin = SimpleCalendar({"id": "simple_calendar"})
    result = plugin._render_calendar(
        (800, 480), date(2026, 9, 8), (30, 80, 110), (180, 20, 20),
        LOCALE_DATA["en"], "en", layout_position=layout, holiday_events=events() + later,
    )
    titles = []
    for event in events() + later:
        shown = plugin._event_display_title(event)
        titles.append(next(box for text, box, _ in records if text[:2] == shown[:2] and box[1] > 300))
    # Down the first column in date order, then on at the top of the second.
    split = next(i for i in range(1, len(titles)) if titles[i][1] < titles[i - 1][1])
    assert 0 < split < len(titles)
    assert all(b[0] > max(a[2] for a in titles[:split]) for b in titles[split:])
    assert all(0 <= b[0] < b[2] <= 800 and 0 <= b[1] < b[3] <= 480 for b in titles)
    assert _disjoint(titles)
    result.close()


def test_an_agenda_that_fits_one_column_spans_the_band(monkeypatch):
    records = _recording(monkeypatch)
    near = [{**e, "title": "Detailed appointment with a long description", "time": None} for e in events()]
    later = {"date": date(2026, 9, 28), "label": "ME", "title": "体检"}
    plugin = SimpleCalendar({"id": "simple_calendar"})
    result = plugin._render_calendar(
        (800, 480), date(2026, 9, 8), (30, 80, 110), (180, 20, 20),
        LOCALE_DATA["en"], "en", holiday_events=near + [later],
    )
    drawn = [text for text, _, _ in records]
    # Seven entries fit one column of seven rows: none is cut short.
    assert drawn.count("Detailed appointment with a long description") == 6 and "体检" in drawn
    result.close()


@pytest.mark.parametrize("selected,following", [(date(2026, 9, 9), date(2026, 10, 1)), (date(2026, 12, 9), date(2027, 1, 1))])
def test_next_month_follows_this_month_under_its_heading(monkeypatch, selected, following):
    records = _recording(monkeypatch)
    current = [{"date": selected.replace(day=10 + index), "label": "ME", "title": f"Item {index}"} for index in range(7)]
    future = [
        {"date": following, "label": "ME", "title": "Future A"},
        {"date": following.replace(day=2), "label": "ME", "title": "Future B"},
        {"date": following.replace(day=3), "label": "ME", "title": "Future C"},
        {"date": following.replace(day=4), "label": "ME", "title": "Future D"},
    ]
    plugin = SimpleCalendar({"id": "simple_calendar"})
    result = plugin._render_calendar((800, 480), selected, (30, 80, 110), (180, 20, 20),
                                    LOCALE_DATA["en"], "en", holiday_events=current + list(reversed(future)))
    words = [text for text, _, _ in records]
    order = ["Item 0", "Item 6", "NEXT MONTH", "Future A", "Future B", "Future C", "Future D"]
    assert [words.index(word) for word in order] == sorted(words.index(word) for word in order)
    assert "NEXT" not in words
    entries = [(text, box, font) for text, box, font in records if text.startswith(("Item ", "Future ", "NEXT"))]
    heading = next(font.size for text, _, font in entries if text == "NEXT MONTH")
    assert {font.size for text, _, font in entries if text != "NEXT MONTH"} == {14} and heading < 14
    assert all(0 <= b[0] < b[2] <= 800 and 0 <= b[1] < b[3] <= 480 for _, b, _ in entries)
    assert _disjoint([box for _, box, _ in entries])
    result.close()


def test_a_heading_never_ends_a_column(monkeypatch):
    records = _recording(monkeypatch)
    current = [{"date": date(2026, 9, 10 + index), "label": "ME", "title": f"Item {index}"} for index in range(6)]
    future = [{"date": date(2026, 10, 1 + index), "label": "ME", "title": f"Future {index}"} for index in range(3)]
    plugin = SimpleCalendar({"id": "simple_calendar"})
    result = plugin._render_calendar((800, 480), date(2026, 9, 9), (30, 80, 110), (180, 20, 20),
                                    LOCALE_DATA["en"], "en", holiday_events=current + future)
    boxes = {text: box for text, box, _ in records if text.startswith(("Item ", "Future ", "NEXT MONTH"))}
    # Six events fill the first column but its last row; the heading opens the second, over its events.
    assert boxes["NEXT MONTH"][0] > boxes["Item 5"][2]
    assert boxes["NEXT MONTH"][1] < boxes["Item 1"][1]
    assert boxes["NEXT MONTH"][3] <= boxes["Future 0"][1]
    result.close()


def test_next_month_can_be_shown_after_all_current_events_have_elapsed(monkeypatch):
    drawn = []
    original = ImageDraw.ImageDraw.text
    def text(draw, xy, value, *args, **kwargs):
        drawn.append(str(value))
        return original(draw, xy, value, *args, **kwargs)
    monkeypatch.setattr(ImageDraw.ImageDraw, "text", text)
    plugin = SimpleCalendar({"id": "simple_calendar"})
    result = plugin._render_calendar((800, 480), date(2026, 9, 30), (30, 80, 110), (180, 20, 20),
        LOCALE_DATA["en"], "en", holiday_events=[{"date": date(2026, 10, 1), "label": "ME", "title": "Next appointment"}])
    assert "NEXT MONTH" in drawn and any(word.startswith("Next app") for word in drawn)
    assert "NEXT" not in drawn
    result.close()
