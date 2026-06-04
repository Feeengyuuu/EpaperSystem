# SportsDashboard local preview timezone stub

## [LRN-20260603-004] environment_quirk

**Logged**: 2026-06-03T01:43:57-07:00
**Priority**: medium
**Status**: active
**Area**: epaper

### Summary
Local SportsDashboard preview scripts may fail on this Windows Python with `ZoneInfoNotFoundError` for `America/Los_Angeles`.

### Details
The local Python runtime used for quick image previews did not have the `tzdata` package available, so `ZoneInfo("America/Los_Angeles")` failed even though the Pi runtime supports the timezone. For local visual-only previews, using a fixed PDT offset with `datetime.timezone(timedelta(hours=-7))` is enough to render the LPL layout without installing dependencies or touching network/API state.

PowerShell stdin here-strings can also introduce a BOM before inline Python code in this environment; prefer `python -c` for short preview scripts.

### Suggested Action
For SportsDashboard local-only previews, stub missing dependencies through the existing `test_sports_dashboard.py` helpers and use a fixed offset timezone when exact DST lookup is not required. Keep real runtime timezone verification on the Pi.

### Metadata
- Source: environment_quirk
- Related Files: `inkypi-weather/package/InkyPi/src/plugins/sports_dashboard/sports_dashboard.py`, `inkypi-weather/package/InkyPi/tests/test_sports_dashboard.py`
- Tags: sports-dashboard, local-preview, zoneinfo, tzdata, powershell
