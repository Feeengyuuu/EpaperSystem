# SportsDashboard restart cache refresh delay

## [LRN-20260603-001] deployment

**Logged**: 2026-06-03T01:10:00-07:00
**Priority**: medium
**Status**: active
**Area**: epaper

### Summary
On ColoredEpaperFrame, `systemctl restart inkypi` can time out and leave the service temporarily in `deactivating` while shutdown-triggered plugin cache refreshes are still running.

### Details
During a SportsDashboard odds typography deploy, `sudo -n systemctl restart inkypi` exceeded a 30 second SSH timeout. `systemctl status` showed `deactivating (stop-sigterm)` with the old root-owned Python process still alive, plus Chromium screenshot child processes and `Refreshing due plugin instance cache` log lines. `sudo systemctl kill` was not available without a password. After waiting, systemd completed the stop/start sequence and the service returned to `active`; `/playlist` then returned HTTP 200. The subsequent SportsDashboard manual refresh exceeded the local HTTP client timeout, but logs showed `Updating display` for `sports_dashboard/SportsDashboard` and `/api/current_image` contained the new image.

### Suggested Action
For future ColoredEpaperFrame deploys, treat a restart or manual display HTTP timeout as inconclusive until `journalctl -u inkypi`, `systemctl is-active inkypi`, `/playlist`, and `/api/current_image` have been checked. If the service is still producing plugin-cache logs, wait for it to finish before escalating.

### Metadata
- Source: deployment
- Related Files: `inkypi-weather/package/InkyPi/src/plugins/sports_dashboard/sports_dashboard.py`
- Tags: colored-epaper-frame, sports-dashboard, deploy, systemd, restart-timeout, display-timeout, verification
