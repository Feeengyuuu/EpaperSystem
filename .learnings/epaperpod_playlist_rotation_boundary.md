# EpaperPod playlist rotation boundary repeats

## [LRN-20260528-106] insight

**Logged**: 2026-05-28T18:04:49-07:00
**Priority**: medium
**Status**: active
**Area**: epaper

### Summary
InkyPi playlist rotation is shuffled and no-repeat only within the current queue round; at a round boundary, the last plugin of the old round can appear again as the second plugin of the new round.

### Details
`Playlist.get_next_plugin()` persists `plugin_rotation_queue` and `plugin_rotation_pool`, shuffles a full plugin-key list only when the queue is empty, and swaps the new first item only if it equals the currently displayed plugin. This prevents immediate back-to-back repeats, but it does not enforce a longer cooldown. On EpaperPod, logs showed `Date/simple_calendar` at 2026-05-28 17:46, `Steam Charts` at 17:52, and `Date/simple_calendar` again at 17:57, consistent with `Date` ending one shuffled round and appearing early in the next.

### Suggested Action
When the user asks whether plugin refresh is random and non-repeating, explain that the current guarantee is per-round uniqueness plus immediate-repeat avoidance, not a global recency window. If the desired behavior is "do not show the same plugin again until N other plugins have shown," add a persisted recent-history cooldown on top of `plugin_rotation_queue`.

### Metadata
- Source: live_device_inspection
- Related Files: `inkypi-weather/package/InkyPi/src/model.py`, `inkypi-weather/package/InkyPi/src/refresh_task.py`
- Tags: inkypi, epaperpod, playlist, rotation, random, no-repeat
