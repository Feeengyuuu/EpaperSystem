# Simple Calendar US/CN holiday ICS

## [LRN-20260528-107] insight

**Logged**: 2026-05-28T18:30:00-07:00
**Priority**: medium
**Status**: active
**Area**: epaper

### Summary
Simple Calendar should use public Google holiday ICS feeds for lightweight US/China holiday markers instead of switching to the HTML-rendered Calendar plugin.

### Details
The Pi can reach `https://calendar.google.com/calendar/ical/en.usa%23holiday%40group.v.calendar.google.com/public/basic.ics` and `https://calendar.google.com/calendar/ical/china__zh_cn%40holiday.calendar.google.com/public/basic.ics`. Adding holiday parsing to the PIL-rendered `simple_calendar` keeps the display stable on EpaperPod, while the older FullCalendar/HTML screenshot path is more vulnerable to screenshot failures on the Pi.

### Suggested Action
For future Simple Calendar changes, preserve the `showHolidays`, `holidayPreset`, and `holidayCalendarURLs[]` settings on the `Date` instance. Update live settings through `/update_plugin_instance/Date` and trigger `/display_plugin_instance`, rather than editing `device.json` while the service is running.

### Metadata
- Source: implementation
- Related Files: `inkypi-weather/package/InkyPi/src/plugins/simple_calendar/simple_calendar.py`, `inkypi-weather/package/InkyPi/src/plugins/simple_calendar/settings.html`
- Tags: inkypi, epaperpod, simple-calendar, holidays, ics, google-calendar
