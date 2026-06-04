# SportsDashboard deep-night theme context

## [LRN-20260603-005] project_pattern

**Logged**: 2026-06-03T01:56:00-07:00
**Priority**: high
**Status**: active
**Area**: epaper

### Summary
SportsDashboard should follow the shared day/night theme context and use a distinct deep-night palette at night.

### Details
The implemented SportsDashboard theme path keeps the existing warm paper comic palette for day mode and switches to a deep-night palette for night mode: near-black ground, dark blue/gold panels, cream text, and brighter process-color accents. `sportsDashboardTheme` defaults to auto, follows `utils.theme_utils.get_theme_context`, and can be forced to `day` or `night` for testing.

The plugin uses an active color proxy around the existing `COLORS[...]` token lookups so existing draw code keeps working without a large layout rewrite. Manual display requests can time out while the e-paper refresh still completes; verify with `journalctl -u inkypi`, `/api/current_image`, and API quota counters before retrying.

### Suggested Action
For future SportsDashboard visual work, preserve the two-mode palette split and validate both day and deep-night previews. Prefer checking logs/current image after a manual display timeout instead of immediately retrying.

### Metadata
- Source: project_pattern
- Related Files: `inkypi-weather/package/InkyPi/src/plugins/sports_dashboard/sports_dashboard.py`, `inkypi-weather/package/InkyPi/src/plugins/sports_dashboard/settings.html`
- Tags: sports-dashboard, theme-context, deep-night, epaper, deployment
