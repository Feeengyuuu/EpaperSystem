# SportsDashboard LPL Little Round live stats

## [LRN-20260603-008] project_pattern

**Logged**: 2026-06-03T03:36:00-07:00
**Priority**: high
**Status**: active
**Area**: epaper

### Summary
SportsDashboard can show LPL in-game kill score as `Little Round` by chaining LoLEsports event details to the live stats window.

### Details
The schedule endpoint can omit a top-level event id, but `match.id` is accepted by `getEventDetails?hl=en-US&id=...`. Event details include per-game ids and game states. For the active game, `https://feed.lolesports.com/livestats/v1/window/{game_id}` returns frames with `blueTeam.totalKills` and `redTeam.totalKills`.

Render the value in event team order, not raw blue-red order, because side assignments can flip between games. If the series is still unfinished but event details have no `inProgress` game and at least one completed game or series win, show `中场休息`.

### Suggested Action
For future LPL live refinements, refresh Little Round only when SportsDashboard itself renders. Do not add high-frequency background polling unless explicitly requested; stale or missing live stats should degrade by hiding the field or showing intermission instead of blocking the e-paper refresh.

### Metadata
- Source: live_device_verification
- Related Files: `inkypi-weather/package/InkyPi/src/plugins/sports_dashboard/sports_dashboard.py`, `inkypi-weather/package/InkyPi/tests/test_sports_dashboard.py`
- Tags: sports-dashboard, lpl, little-round, live-stats, lolesports, epaper
