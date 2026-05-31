# LiveRadar light mode uses white card shells

## [LRN-20260528-002] user_preference

**Logged**: 2026-05-28T15:25:00-07:00
**Priority**: high
**Status**: active
**Area**: epaper

### Summary
LiveRadar light/day mode should use white card shells with black text and outlines, including live streamer cards.

### Details
The user rejected black live cards in the daytime page because they did not fit the light tone. The preferred light-mode treatment is a white card theme across live/offline/list sections, while preserving original media colors for live screenshots and avatars.

### Suggested Action
When editing LiveRadar theme colors, keep `themeMode=light/day/paper` card fills white and use black ink/lines for contrast. Reserve dark fills for the night theme or very small icon badges only.

### Metadata
- Source: user_feedback
- Related Files: inkypi-weather/package/InkyPi/src/plugins/live_radar/live_radar.py
- Tags: inkypi, epaperpod, live-radar, light-theme, ui, user-preference
