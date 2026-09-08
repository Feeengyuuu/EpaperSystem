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
    assert {f.size for _, _, f in titles} == {15}
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


@pytest.mark.parametrize("layout", ["left", "right"])
def test_agenda_uses_lower_right_space_for_later_events_without_shortening_near_titles(monkeypatch, layout):
    records = []
    original = ImageDraw.ImageDraw.text

    def text(draw, xy, value, *args, **kwargs):
        records.append((str(value), draw.textbbox(xy, value, font=kwargs.get("font"), anchor=kwargs.get("anchor")), kwargs.get("font")))
        return original(draw, xy, value, *args, **kwargs)

    monkeypatch.setattr(ImageDraw.ImageDraw, "text", text)
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
    titles = [(t, b, f) for t, b, f in records if any(t.endswith(e["title"]) for e in events() + later)]
    assert len(titles) == 9
    assert {f.size for _, _, f in titles} == {15}
    primary = [b for t, b, _ in titles if any(t.endswith(e["title"]) for e in events())]
    previews = [b for t, b, _ in titles if any(t.endswith(e["title"]) for e in later)]
    assert all(b[0] > primary[-1][2] + 15 for b in previews)
    assert all(0 <= b[0] < b[2] <= 800 and 0 <= b[1] < b[3] <= 480 for _, b, _ in titles)
    for i, (_, a, _) in enumerate(titles):
        for _, b, _ in titles[i + 1:]:
            assert a[2] <= b[0] or b[2] <= a[0] or a[3] <= b[1] or b[3] <= a[1]
    result.close()


def test_later_preview_yields_space_to_long_primary_titles(monkeypatch):
    drawn = []
    original = ImageDraw.ImageDraw.text

    def text(draw, xy, value, *args, **kwargs):
        drawn.append(str(value))
        return original(draw, xy, value, *args, **kwargs)

    monkeypatch.setattr(ImageDraw.ImageDraw, "text", text)
    near = [{**e, "title": "Detailed appointment with a long description"} for e in events()]
    later = {"date": date(2026, 9, 28), "label": "ME", "title": "体检"}
    plugin = SimpleCalendar({"id": "simple_calendar"})
    result = plugin._render_calendar(
        (800, 480), date(2026, 9, 8), (30, 80, 110), (180, 20, 20),
        LOCALE_DATA["en"], "en", holiday_events=near + [later],
    )
    assert "NEXT" not in drawn and "体检" not in drawn
    assert sum("Detailed appointment" in t for t in drawn) == 6
    result.close()
