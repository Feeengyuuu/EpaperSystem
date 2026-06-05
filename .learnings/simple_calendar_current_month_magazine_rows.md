# Simple Calendar current-month magazine rows

## LRN-20260604-001
**Logged**: 2026-06-04T18:16:00-07:00
**Scope**: `inkypi-weather/package/InkyPi/src/plugins/simple_calendar/simple_calendar.py`

For the bottom event list in the `Simple Calendar` / `Date` view, keep the "magazine" refill behavior inside the selected month only. When an event has passed, remove it and move the next upcoming event from the same month into the three-row list. Do not backfill with next-month events.

Timed personal events should use their start datetime when available, so a same-day appointment disappears after its start time instead of staying visible for the whole date.
