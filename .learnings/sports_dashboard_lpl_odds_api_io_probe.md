# SportsDashboard LPL odds API probe

## [LRN-20260603-002] deployment

**Logged**: 2026-06-03T01:20:00-07:00
**Priority**: medium
**Status**: active
**Area**: epaper

### Summary
Odds-API.io can provide League of Legends LPL odds for SportsDashboard using the existing stored `ODDS_API_IO_KEY`.

### Details
A read-only probe from ColoredEpaperFrame queried `GET /v3/leagues?sport=esports` and returned `League of Legends - LPL` with slug `league-of-legends-lpl`. Querying `GET /v3/events?sport=esports&league=league-of-legends-lpl&status=pending&limit=5` returned upcoming LPL events including `Bilibili Gaming` vs `Edward Gaming`, `Anyones Legend` vs `LGD Gaming`, and `TOP Esports` vs `Team WE`. Querying `GET /v3/odds/multi` with `bookmakers=Bet365` returned two odds records for the LPL event sample. LPL odds are two-way match winner lines; there is no draw leg like World Cup soccer.

### Suggested Action
For LPL odds in SportsDashboard, reuse the existing Odds-API.io flow with `sport=esports`, `league=league-of-legends-lpl`, `status=pending`, and the configured bookmaker list. Render only team A and team B odds, and do not reserve center space for a draw odds value.

### Metadata
- Source: deployment
- Related Files: `inkypi-weather/package/InkyPi/src/plugins/sports_dashboard/sports_dashboard.py`
- Tags: colored-epaper-frame, sports-dashboard, lpl, odds-api-io, esports, league-of-legends
