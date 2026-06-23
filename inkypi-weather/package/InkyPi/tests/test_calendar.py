import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from plugins.calendar.calendar import Calendar


def test_list_month_range_extends_through_next_month_for_fill():
    plugin = Calendar({"id": "calendar"})

    start, end, visible_end = plugin.get_view_range(
        "listMonth", datetime(2026, 6, 22, 15, 30), {}
    )

    assert start == datetime(2026, 6, 22)
    assert end == datetime(2026, 8, 1)
    assert visible_end == datetime(2026, 8, 1)


def test_list_month_range_handles_december_rollover():
    plugin = Calendar({"id": "calendar"})

    start, end, visible_end = plugin.get_view_range(
        "listMonth", datetime(2026, 12, 20, 9, 0), {}
    )

    assert start == datetime(2026, 12, 20)
    assert end == datetime(2027, 2, 1)
    assert visible_end == datetime(2027, 2, 1)


def test_non_list_view_does_not_get_extended_visible_range():
    plugin = Calendar({"id": "calendar"})

    start, end, visible_end = plugin.get_view_range(
        "timeGridDay", datetime(2026, 6, 22, 15, 30), {}
    )

    assert start == datetime(2026, 6, 22)
    assert end == datetime(2026, 6, 23)
    assert visible_end is None
