# Simple Calendar recurring calendar expansion

## LRN-20260604-003
**Logged**: 2026-06-04T18:54:00-07:00
**Scope**: `inkypi-weather/package/InkyPi/src/plugins/simple_calendar/simple_calendar.py`

Simple Calendar now expands Google Calendar recurrence data into the selected month before applying the three-row current-month display rule. The implementation covers common ICS recurrence fields including `RRULE`, `RDATE`, `EXDATE`, and `RECURRENCE-ID` overrides for Personal and holiday sources.

For the live `Date` instance, this is required for monthly salary/payroll entries exported as old `VEVENT` starts plus monthly `RRULE`. Also strip emoji-style symbol decorations from event titles because the e-paper font stack renders them as tofu boxes; keep the readable title text such as `Pay Salary/401(k)`.
