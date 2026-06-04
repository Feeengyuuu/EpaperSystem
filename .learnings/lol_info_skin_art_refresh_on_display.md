# LoLInfo skin art refreshes on every display

## [LRN-20260604-003] project_state

**Logged**: 2026-06-04T00:00:00-07:00
**Priority**: medium
**Status**: active
**Area**: epaper

### Summary
LoLInfo lower-right skin art should be treated as display-level rotating content and refreshed every time the plugin is shown.

### Details
The user clarified that the skin image should refresh faster: each display pass should redraw/select a skin image, even when the Riot/player data cache is still valid. Existing LoLInfo instances may not have `refreshOnDisplay` persisted in their settings, so the scheduler should default `lol_info` to refresh-on-display instead of relying only on the settings HTML hidden field.

### Suggested Action
Keep `lol_info` in the scheduler's default refresh-on-display plugin set. Preserve LoLInfo's internal data cache so every display re-renders the dashboard and rotates the skin art without forcing Riot API refetches.

### Metadata
- Source: user correction
- Related Files: inkypi-weather/package/InkyPi/src/refresh_task.py, inkypi-weather/package/InkyPi/src/plugins/lol_info/lol_info.py, inkypi-weather/package/InkyPi/tests/test_refresh_task.py
- Tags: lol_info, refresh-on-display, skin-art, rotation, scheduler
