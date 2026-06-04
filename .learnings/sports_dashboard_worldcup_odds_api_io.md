# SportsDashboard World Cup odds-api.io deployment

## [LRN-20260603-002] best_practice

**Logged**: 2026-06-03T00:49:00-07:00
**Priority**: medium
**Status**: active
**Area**: epaper

### Summary
SportsDashboard World Cup odds can use odds-api.io when The Odds API rejects the key.

### Details
The user-provided 64-character odds key was invalid for `the-odds-api.com` but valid for `odds-api.io`. For odds-api.io, use `/v3/events?apiKey=...&sport=football&league=international-world-cup&status=pending&limit=10`, then `/v3/odds/multi?eventIds=...&bookmakers=Bet365`. The free account reported access limited to Bet365, so do not request multiple bookmakers unless the account settings are changed. Preserve existing InkyPi secrets by saving through `/api-keys/save` with existing keys marked `keepExisting=true`, then add `ODDS_API_IO_KEY`.

### Suggested Action
For future SportsDashboard odds work, support `worldCupOddsProvider=oddsapiio`, `worldCupOddsBookmakers=Bet365`, and `worldCupOddsApiIoLeague=international-world-cup`. Normalize country aliases by ignoring standalone `and` so `Bosnia-Herzegovina`, `Bosnia & Herzegovina`, and `Bosnia and Herzegovina` match the same fixture.

### Metadata
- Source: implementation
- Related Files: `inkypi-weather/package/InkyPi/src/plugins/sports_dashboard/sports_dashboard.py`, `inkypi-weather/package/InkyPi/src/plugins/sports_dashboard/settings.html`
- Tags: sports-dashboard, world-cup, odds, odds-api-io, epaperpod, deploy
