# Sports dashboard live-next-recent buckets

## [LRN-20260602-005] project_state

**Logged**: 2026-06-02T15:10:00-07:00
**Priority**: medium
**Status**: active
**Area**: epaper

### Summary
Sports information pages on the static e-paper dashboard should keep live, next, and recent events as explicit data buckets instead of rendering from one mixed match list.

### Details
While adding `sports_dashboard`, the World Cup panel needed stable display of the next match, the previous completed match, and live scores when a match is in progress. A single sorted `matches` list made the renderer guess intent and made live-score refresh policy unclear. The plugin now stores `live_matches`, `next_matches`, and `recent_matches` for World Cup data, and applies a shorter live-score cache TTL when cached World Cup data includes live matches.

### Suggested Action
For future sports/e-sports plugins, normalize schedule feeds into semantic buckets before rendering. Use a short cache window for live games, but keep longer cache/fallback behavior for normal schedules so playlist rotation is not blocked by degraded upstream APIs.

### Metadata
- Source: sports_dashboard implementation
- Related Files: inkypi-weather/package/InkyPi/src/plugins/sports_dashboard/sports_dashboard.py
- Tags: sports_dashboard, world_cup, live_score, cache, epaper
