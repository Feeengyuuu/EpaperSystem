# LiveRadar LIVE TOO thumbnails stay landscape

## [LRN-20260604-002] project_state

**Logged**: 2026-06-04T00:00:00-07:00
**Priority**: medium
**Status**: active
**Area**: epaper

### Summary
LiveRadar `LIVE TOO` snapshot mini cards must keep live screenshots in a horizontal landscape frame for one, two, three, or four lower-left cards.

### Details
The user clarified that the lower-left `LIVE TOO` cards should not show live screenshots as tall vertical strips. This applies not only when there is one extra live card, but also when there are two, three, or four cards in that section. The snapshot should remain unobstructed, with streamer avatar/name metadata in the text area beside the screenshot.

### Suggested Action
When editing `live_radar.py`, preserve the 16:9-ish snapshot mini thumbnail sizing across `LIVE TOO` counts of 1, 2, 3, and 4. Keep tests that sample the right edge of those thumbnails so regressions cannot collapse the screenshot back to a narrow vertical strip.

### Metadata
- Source: user correction
- Related Files: inkypi-weather/package/InkyPi/src/plugins/live_radar/live_radar.py, inkypi-weather/package/InkyPi/tests/test_live_radar.py
- Tags: live_radar, live-too, snapshot-mini, landscape-thumbnail, layout
