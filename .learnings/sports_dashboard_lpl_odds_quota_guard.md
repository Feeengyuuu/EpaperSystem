# SportsDashboard LPL odds quota guard

## [LRN-20260603-003] user_preference

**Logged**: 2026-06-03T01:32:00-07:00
**Priority**: high
**Status**: active
**Area**: epaper

### Summary
LPL odds in SportsDashboard must protect the free Odds-API.io quota by default.

### Details
When adding LPL odds, the user explicitly said they do not want to pay for extra API usage. The implemented default is `lplOddsCacheHours=12` and `lplOddsDailyLimit=4`, with one LPL refresh consuming two calls at most: `/v3/events` and `/v3/odds/multi`. After deployment, the live cache summary showed `cache_events=3`, `cache_odds_events=2`, and `state_count=2` for `2026-06-03`, confirming the first refresh used the expected two calls and then wrote cache.

### Suggested Action
For future SportsDashboard odds changes, avoid lowering LPL odds cache duration or raising daily limits unless the user explicitly approves. Prefer cached/stale data over repeated live calls when quota is constrained.

### Metadata
- Source: user_preference
- Related Files: `inkypi-weather/package/InkyPi/src/plugins/sports_dashboard/sports_dashboard.py`, `inkypi-weather/package/InkyPi/src/plugins/sports_dashboard/settings.html`
- Tags: sports-dashboard, lpl, odds-api-io, quota, cache, free-tier
