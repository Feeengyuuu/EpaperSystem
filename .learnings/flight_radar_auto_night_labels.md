# Flight radar auto night maps and map label suppression

## [LRN-20260531-001] correction

**Logged**: 2026-05-31T01:05:00-07:00
**Priority**: high
**Status**: active
**Area**: epaper

### Summary
SkyRadar map labels should be conservative: hide arrival, on-ground, and non-airline-like identifiers on the map layer to avoid black text clusters around SFO. Keep the aircraft icons, trails, and right-side aircraft list intact.

### Details
`flight_radar` now supports `googleMapTheme` values `day`, `night`, and `auto`. For the live `SkyRadar` instance, use `auto` so the Google Static Maps terrain background switches to the night comic palette during local night hours and returns to the day comic palette during daytime.

When persisting `googleMapTheme=auto` or similar plugin settings, stop `inkypi` before editing `device.json`; editing while the service is running can be overwritten by the service's old in-memory config on restart. After restart, verify both `/playlist` and `/api/current_image`; note that `/api/current_image` returns PNG bytes directly, not JSON.

### Suggested Action
For future SkyRadar display fixes, validate with a Pi-rendered preview first, then deploy the plugin and only edit config in the stop-write-start order. Do not print `/api/current_image` to terminal; save it to a PNG and compare SHA with the plugin image.

### Metadata
- Source: implementation
- Related Files: `inkypi-weather/package/InkyPi/src/plugins/flight_radar/flight_radar.py`, `inkypi-weather/package/InkyPi/src/plugins/flight_radar/settings.html`, `inkypi-weather/package/InkyPi/tests/test_flight_radar.py`
- Tags: flight-radar, skyradar, google-static-maps, night-map, labels, current-image, colored-epaper-frame

---
