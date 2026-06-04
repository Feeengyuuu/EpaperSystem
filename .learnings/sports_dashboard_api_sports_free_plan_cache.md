# Sports dashboard API-Sports free plan cache

## [LRN-20260602-006] project_quirk

**Logged**: 2026-06-02T18:48:00-07:00
**Priority**: medium
**Status**: active
**Area**: epaper

### Summary
API-Sports/API-FOOTBALL free plan can authenticate successfully but rejects 2026 World Cup fixtures with a plan error, so `sports_dashboard` must negative-cache that error and keep using the screenshot/fallback source.

### Details
Live validation on `ColoredEpaperFrame` with the user's masked API key returned: free plans do not have access to the 2026 season and suggest seasons 2022 to 2024. The plugin now supports `apiSportsKey`, `apiFootballKey`, `API_SPORTS_KEY`, `APISPORTS_KEY`, `API_FOOTBALL_KEY`, `API_FPPTBALL_KEY`, `X_APISPORTS_KEY`, `World_CUP`, `WORLD_CUP`, and `WORLD_CUP_API_KEY`, reads them through plugin settings, device config, `load_env_key`, and `os.environ`, and writes an `API BLOCKED` cache with `blocked_until` to avoid spending requests every 15-minute refresh.

### Suggested Action
For free/no-paid World Cup work, do not rely on API-Sports for live 2026 fixtures unless the plan changes. Keep the screenshot/fallback path active, preserve the daily request cap, and verify `/usr/local/inkypi/src/plugins/sports_dashboard/cache/api_state.json` count stays flat after an `API BLOCKED` cache is present.

### Metadata
- Source: live_deployment
- Related Files: `inkypi-weather/package/InkyPi/src/plugins/sports_dashboard/sports_dashboard.py`
- Tags: sports_dashboard, api-sports, world-cup, free-plan, cache, coloredepaperframe

---
