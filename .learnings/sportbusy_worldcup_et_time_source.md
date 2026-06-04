# SportBusy World Cup ET time source

## [LRN-20260602-008] data_source

**Logged**: 2026-06-02T16:00:00-07:00
**Priority**: high
**Status**: active
**Area**: epaper

### Summary
SportBusy's World Cup widget is visually strong but its schedule times are Eastern Time, not local device time.

### Details
SportBusy's World Cup schedule page labels match times as `Time (ET)`, and adding common timezone query parameters such as `timezone=America/Los_Angeles`, `tz=America/Los_Angeles`, or `tz=PT` did not change the embedded widget output. The first visible match stayed at `3:00 PM`, while Pacific/PDT sources list the opening match at `12:00 PDT`.

### Suggested Action
Do not deploy the raw SportBusy World Cup widget when the user requires California-local time. Either use a Pacific Time source, or build a thin wrapper that uses SportBusy's visual style/source but converts displayed ET times to `America/Los_Angeles`.

### Metadata
- Source: SportBusy widget validation
- Related Files: inkypi-weather/package/InkyPi/src/plugins/screenshot/screenshot.py
- Tags: sportbusy, world_cup, timezone, pacific_time, california_time, screenshot_plugin
