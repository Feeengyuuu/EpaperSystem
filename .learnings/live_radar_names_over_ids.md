# LiveRadar visible names over room ids

## [LRN-20260527-055] user_preference

**Logged**: 2026-05-27T22:10:00-07:00
**Priority**: medium
**Status**: active
**Area**: epaper

### Summary
LiveRadar e-paper UI should show streamer names before numeric room ids in visible user-facing text.

### Details
For the `...are live too` overflow line, the user corrected that entries like `Zard1991` and `Mr. Quin` are preferred over numeric room ids. The API status includes `owner`, and room configuration can include `label`, so rendered summaries should use `owner -> label -> id` fallback order.

### Suggested Action
When adding or tuning LiveRadar labels, counters, summaries, or compact text, prefer streamer display names. Use numeric ids only as a fallback when no owner or label is available.

### Metadata
- Source: user_feedback
- Related Files: inkypi-weather/package/InkyPi/src/plugins/live_radar/live_radar.py
- Tags: inkypi, epaperpod, live-radar, ui, streamer-name, user-preference
