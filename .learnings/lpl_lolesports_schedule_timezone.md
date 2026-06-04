# LPL LoL Esports schedule timezone

## [LRN-20260602-006] project_state

**Logged**: 2026-06-02T16:15:00-07:00
**Priority**: medium
**Status**: active
**Area**: epaper

### Summary
LoL Esports schedule `startTime` values are UTC and must be converted to the device or requested local timezone at render time.

### Details
While building the sports dashboard, the LPL persisted schedule endpoint returned upcoming match times such as `2026-06-03T09:00:00Z`, which renders as `2026-06-03 02:00 PDT` for `America/Los_Angeles`. Earlier cached or manually noted UTC hours can become stale, so the plugin should trust the current API payload and convert with `ZoneInfo` during each refresh.

### Suggested Action
For future esports widgets, keep API timestamps in UTC until render selection, then convert once with the configured local timezone. Use fallback data only as a temporary display fallback and keep it synchronized with the latest verified API shape.

### Metadata
- Source: sports_dashboard LPL sidebar implementation
- Related Files: inkypi-weather/package/InkyPi/src/plugins/sports_dashboard/sports_dashboard.py
- Tags: sports_dashboard, lpl, timezone, lolesports, epaper
