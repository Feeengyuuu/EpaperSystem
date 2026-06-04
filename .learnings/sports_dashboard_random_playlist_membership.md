# SportsDashboard random playlist membership

## [LRN-20260603-002] project_state

**Logged**: 2026-06-03T02:55:00-07:00
**Priority**: medium
**Status**: active
**Area**: epaper

### Summary
SportsDashboard is already in the `DailyDoseOfDay` random rotation on ColoredEpaperFrame.

### Details
The live config at `/home/feeengyuuu/inkypi-weather-pi-package-zero2w-20260531/InkyPi/src/config/device.json` and the mirrored `/usr/local/inkypi/src/config/device.json` both list `sports_dashboard` / `SportsDashboard` as plugin index 23 in `DailyDoseOfDay`. The `plugin_rotation_pool` also includes `["sports_dashboard","SportsDashboard"]`. An empty `plugin_rotation_queue` does not mean the plugin is absent; the queue is rebuilt from the pool/plugins when the next random round starts.

### Suggested Action
For future "add to random list" requests, first verify the playlist `plugins` list and `plugin_rotation_pool`. Only edit config if the plugin is absent there; do not force-edit `plugin_rotation_queue` just because it is empty.

### Metadata
- Source: live config verification
- Related Files: `inkypi-weather/package/InkyPi/src/model.py`, `inkypi-weather/package/InkyPi/src/config.py`
- Tags: sports-dashboard, playlist, random-rotation, dailydoseofday, colored-epaper-frame
