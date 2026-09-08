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
