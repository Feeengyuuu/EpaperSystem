# SportsDashboard LPL NOW PLAYING score snapshot

## [LRN-20260603-006] project_pattern

**Logged**: 2026-06-03T02:05:00-07:00
**Priority**: high
**Status**: active
**Area**: epaper

### Summary
SportsDashboard LPL live matches should render as `NOW PLAYING` without adding live score polling.

### Details
When LoLEsports marks an LPL event as live (`inProgress` normalized to `inprogress`, plus equivalent live state aliases), the LPL focus card should replace `NEXT MATCH` with `NOW PLAYING`, show the currently playing teams, and use the score fields available in the event payload for that render. Score values are a render-time snapshot and should update only when the plugin is refreshed by the normal scheduled/manual refresh path.

Live events should be excluded from the UPCOMING and RECENT row lists so the same match does not appear twice.

On 2026-06-03, LoLEsports returned BLG vs EDG as `completed` with `0-0` about 15 minutes after scheduled start even though the match was in progress. SportsDashboard now treats an LPL event as inferred live when it is within six hours after scheduled start and its score is still unresolved (`None`/`0-0`), so the focus card still renders `NOW PLAYING`.

### Suggested Action
For future LPL live-score work, do not add timers, background polling, or extra score endpoints unless the user explicitly asks for faster live updates. Keep score freshness tied to the existing e-paper refresh cadence, and account for LoLEsports schedule-state lag near match start.

### Metadata
- Source: user_preference
- Related Files: `inkypi-weather/package/InkyPi/src/plugins/sports_dashboard/sports_dashboard.py`, `inkypi-weather/package/InkyPi/tests/test_sports_dashboard.py`
- Tags: sports-dashboard, lpl, live, now-playing, score-snapshot, no-polling
