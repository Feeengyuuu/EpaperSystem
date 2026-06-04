# LiveRadar low-live snapshot cards

## [LRN-20260602-002] project_state

**Logged**: 2026-06-02T01:25:00-07:00
**Priority**: medium
**Status**: active
**Area**: epaper

### Summary
For LiveRadar, low online count snapshot cards belong in the existing `LIVE TOO` row slots, not only in an empty fallback panel.

### Details
The user clarified with a marked screenshot that when there are 7 or fewer live streamers, the red-boxed lower-left `LIVE TOO` positions are the intended small card positions. The fix is to render remaining live streamers after the top three as 2x2 snapshot mini cards when total live count is `<= 7`; keep the dense avatar/text live queue for more than 7 live streamers.

### Suggested Action
When modifying `live_radar.py`, preserve this threshold behavior: `LIVE <= 7` uses snapshot cards for queued live streamers, `LIVE > 7` uses dense queue rows, and only the no-extra-live fallback may use replay/offline snapshot candidates.

### Metadata
- Source: user correction
- Related Files: inkypi-weather/package/InkyPi/src/plugins/live_radar/live_radar.py
- Tags: live_radar, snapshot, live-too, layout, epaper
