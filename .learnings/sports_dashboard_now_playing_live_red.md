# SportsDashboard NOW PLAYING live red label

## [LRN-20260603-009] project_pattern

**Logged**: 2026-06-03T03:49:00-07:00
**Priority**: medium
**Status**: active
**Area**: epaper

### Summary
SportsDashboard LPL `NOW PLAYING` label should use the live red background.

### Details
The live focus card keeps the deep-night card body, but the `NOW PLAYING` tag itself should use `COLORS["red"]` instead of the gold panel fill. This keeps the live state visually tied to the red accent bar and the top-right live indicator.

### Suggested Action
For future LPL visual tweaks, preserve the red `NOW PLAYING` tag in live state unless the user explicitly asks to redesign the whole focus card.

### Metadata
- Source: user_preference
- Related Files: `inkypi-weather/package/InkyPi/src/plugins/sports_dashboard/sports_dashboard.py`
- Tags: sports-dashboard, lpl, now-playing, live-red, visual-design
