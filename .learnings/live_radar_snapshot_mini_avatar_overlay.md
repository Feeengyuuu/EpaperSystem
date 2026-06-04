# LiveRadar snapshot mini avatar placement

## [LRN-20260602-003] project_state

**Logged**: 2026-06-02T01:30:00-07:00
**Priority**: medium
**Status**: active
**Area**: epaper

### Summary
LiveRadar snapshot mini cards should include each streamer's small avatar beside the streamer name, not over the screenshot.

### Details
After the low-live `LIVE TOO` snapshot-card behavior was added, the user asked for each lower mini card to show its own small avatar. A first pass overlaid the avatar on the screenshot thumbnail, but the user corrected that this blocks the screenshot. The preferred layout is a small circular avatar in the right-side text area, immediately before the streamer name. The second row should stay compact: keep the platform short name text and replace only the `live` word with a green status dot.

### Suggested Action
When revising LiveRadar mini cards, keep the screenshot thumbnail unobstructed and place the avatar beside the streamer name. Keep status metadata compact as platform short text plus a dot status indicator, without the `live` word. Continue passing `avatar_cache_seconds` into the snapshot mini renderer so live API avatars are reused consistently.

### Metadata
- Source: user correction
- Related Files: inkypi-weather/package/InkyPi/src/plugins/live_radar/live_radar.py
- Tags: live_radar, avatar, snapshot-mini, live-too, layout, status-dot
