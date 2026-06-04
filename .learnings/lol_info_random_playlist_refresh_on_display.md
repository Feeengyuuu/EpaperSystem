# LoLInfo random playlist refresh-on-display

## [LRN-20260604-006] best_practice

**Logged**: 2026-06-04
**Priority**: medium
**Status**: active
**Area**: epaper, lol-info, playlist

### Summary
LoLInfo must carry `refreshOnDisplay=true` when it is added to the random playlist.

### Details
The LoLInfo skin-art panel intentionally rotates a Data Dragon splash from the player's commonly used and high-mastery champions. The standard playlist display path uses the cached instance image first, so the instance needs `refreshOnDisplay=true` in its persisted settings; otherwise LoLInfo can be in the random playlist but still show the same cached skin art until its normal refresh interval expires.

### Suggested Action
When adding or updating `LoLInfo` in `DailyDoseOfDay`, include `refreshOnDisplay=true` with the Riot ID settings. Use `tools/add_lol_info_to_random_list.py` as an idempotent helper once the live device is reachable.

### Metadata
- Source: production_playlist_setup
- Related Files: `tools/add_lol_info_to_random_list.py`, `.tmp/update_lol_info_instance.py`, `inkypi-weather/package/InkyPi/src/plugins/lol_info/settings.html`, `inkypi-weather/package/InkyPi/src/refresh_task.py`
- Tags: lol-info, random-playlist, refresh-on-display, skin-art, cache
