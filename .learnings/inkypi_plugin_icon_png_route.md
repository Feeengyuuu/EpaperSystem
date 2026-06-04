# InkyPi plugin icon PNG route

## [LRN-20260603-003] best_practice

**Logged**: 2026-06-03T02:56:00-07:00
**Priority**: low
**Status**: active
**Area**: epaper

### Summary
Plugin cards load icons from each plugin directory's `icon.png`.

### Details
The plugin grid and playlist pages call the Flask route `/images/<plugin_id>/icon.png`, which serves files directly from `src/plugins/<plugin_id>/icon.png`. If the file is missing, the card shows broken-image fallback text. Fixing missing plugin icons only requires adding `icon.png` to the plugin directory and syncing it to the active device package path; no template edit or service restart is required if the service is already running.

### Suggested Action
For future missing plugin-card icons, add a transparent PNG named `icon.png` under the affected plugin directory, upload it to both the live package and `/usr/local/inkypi/src` mirror when working on ColoredEpaperFrame, then verify with `curl /images/<plugin_id>/icon.png`.

### Metadata
- Source: live icon repair
- Related Files: `inkypi-weather/package/InkyPi/src/templates/inky.html`, `inkypi-weather/package/InkyPi/src/templates/playlist.html`, `inkypi-weather/package/InkyPi/src/blueprints/plugin.py`
- Tags: icons, plugin-grid, flask-route, colored-epaper-frame
