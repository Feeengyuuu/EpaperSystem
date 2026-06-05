# Simple Calendar personal recurring events

## LRN-20260604-002
**Logged**: 2026-06-04T18:24:00-07:00
**Scope**: `inkypi-weather/package/InkyPi/src/plugins/simple_calendar/simple_calendar.py`

Google Calendar private ICS feeds can represent ongoing Personal events as one `VEVENT` with an old `DTSTART` plus `RRULE`, for example monthly pay/salary entries. The current Simple Calendar event extraction only checks the raw `DTSTART`/`DTEND` overlap with the selected month, so recurring events whose original start date is outside the selected month are skipped unless recurrence expansion is added.

When diagnosing a missing Personal event, inspect the raw ICS for `RRULE` before assuming the event is absent from the source.
