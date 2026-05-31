# Flight radar five-refresh trails

## [LRN-20260531-002] trail history default

**Logged**: 2026-05-31
**Priority**: medium
**Status**: active
**Area**: plugin-rendering

### Summary
SkyRadar tails should use a compact refresh-history model: current position plus the previous five refresh positions.

### Details
The user wanted longer tails that reflect recent refresh history. Use `DEFAULT_TRACK_HISTORY_POINTS = 6` and a longer `_limit_trail_points` pixel cap so the rendered trail is visibly longer without allowing stale jumps across the map.

### Suggested Action
After trail changes, verify both the rendered preview and the live `tracks_v1.json` max point count. The expected max track length is `6`.

### Metadata
- Source: SkyRadar tail history refinement
- Related Files: `inkypi-weather/package/InkyPi/src/plugins/flight_radar/flight_radar.py`, `inkypi-weather/package/InkyPi/tests/test_flight_radar.py`
- Tags: flight-radar, skyradar, trail-history, refresh-history

---
