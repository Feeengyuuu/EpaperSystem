# SportsDashboard manual retry force and timeout

## [LRN-20260602-009] project_state

**Logged**: 2026-06-02T22:58:43-07:00
**Priority**: high
**Status**: active
**Area**: epaper

### Summary
SportsDashboard manual retry must propagate force-refresh into plugin settings and must not let `/display_plugin_instance` wait forever.

### Details
The playlist `Display Now` route passes `PlaylistRefresh(..., force=True)`, but `PlaylistRefresh.execute()` previously called `plugin.generate_image(plugin_instance.settings, ...)` without putting a force flag into settings. Cache-aware plugins such as `sports_dashboard` could therefore keep honoring fresh cache or `API BLOCKED` negative cache during a user retry. A manual refresh request can also tie up the HTTP handler indefinitely if the refresh thread or display path stalls, making retry appear blocked.

During live deployment, the running process used `/home/feeengyuuu/inkypi-weather-pi-package-zero2w-20260531/InkyPi`, while `/usr/local/inkypi` was also present. For this plugin, deploy both paths or confirm the process path with `ps` before claiming activation.

### Suggested Action
Keep `_settings_with_force_refresh()` in `refresh_task.py` so `ManualRefresh`, `PlaylistRefresh(force=True)`, and forced background refreshes pass both `forceRefresh` and `force_refresh`. Keep `SportsDashboard._force_refresh_requested()` honoring `forceRefresh`, `force_refresh`, `refreshNow`, and `retry`, bypassing fresh cache and API block cache while still respecting daily request limits. Keep `manual_update_timeout_seconds` available so HTTP retry cannot wait forever.

### Metadata
- Source: live_deployment
- Related Files: inkypi-weather/package/InkyPi/src/refresh_task.py; inkypi-weather/package/InkyPi/src/plugins/sports_dashboard/sports_dashboard.py
- Tags: sports_dashboard, worldcup, retry, force_refresh, manual_update, deploy_path, coloredepaperframe

---
