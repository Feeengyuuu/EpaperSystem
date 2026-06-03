# SportsDashboard LPL best-of live inference

## [LRN-20260603-007] project_pattern

**Logged**: 2026-06-03T03:16:00-07:00
**Priority**: high
**Status**: active
**Area**: epaper

### Summary
SportsDashboard should use LPL best-of metadata before treating a partial completed score as a finished match.

### Details
LoLEsports can return an LPL event as `completed` during a break between games inside the same BO3/BO5 series. On 2026-06-03, BLG vs EDG was still in progress while the feed exposed `state=completed`, score `1-0`, and `match.strategy.count=5`.

SportsDashboard now stores `best_of` from `match.strategy.count` and treats an event as inferred live during the six-hour match window when neither team has reached the required series wins (`best_of // 2 + 1`). This keeps partial BO5 scores such as `1-0` in `NOW PLAYING`, while deciding results such as `2-0` in BO3 or `3-1` in BO5 move to `RECENT`.

### Suggested Action
For future LPL state fixes, check both feed state and series completion. Do not rely on `completed` alone when the score is below the best-of win threshold and the match is still inside the live inference window.

### Metadata
- Source: live_device_verification
- Related Files: `inkypi-weather/package/InkyPi/src/plugins/sports_dashboard/sports_dashboard.py`, `inkypi-weather/package/InkyPi/tests/test_sports_dashboard.py`
- Tags: sports-dashboard, lpl, best-of, now-playing, live-inference, lolesports
