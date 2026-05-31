# Flight radar solid plane icons

## [LRN-20260531-001] visual correction

**Logged**: 2026-05-31
**Priority**: medium
**Status**: active
**Area**: plugin-rendering

### Summary
SkyRadar airplane markers should read as simple solid-color silhouettes, not multi-part icons with internal linework.

### Details
The user rejected the wing/body/tail segmented marker as too complex. Use a single filled aircraft silhouette with only an outer outline so altitude colors remain clear on the comic-style map.

### Suggested Action
When editing `_draw_plane_marker`, keep marker interiors flat and pure color. Validate with a rendered preview rather than only unit tests, because icon complexity is primarily a visual readability issue.

### Metadata
- Source: SkyRadar plane icon visual correction
- Related Files: `inkypi-weather/package/InkyPi/src/plugins/flight_radar/flight_radar.py`
- Tags: flight-radar, skyradar, icons, comic-ui

---
