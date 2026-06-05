# Magazine Covers daily refresh deploy behavior

## [LRN-20260605-006] daily cover freshness

**Logged**: 2026-06-05T00:00:00-07:00
**Priority**: medium
**Status**: active
**Area**: epaper-plugin-refresh

### Summary
Magazine Covers should use a faster daily-library refresh cadence and shorter cover cache to make new covers visible daily on the live EpaperPod.

### Details
The plugin already had daily-library mode, but the default hidden `libraryRefreshHours` was 23 and individual cover cache TTL was 48 hours. Updating the defaults to a 12-hour library refresh and 20-hour image cache makes the plugin refresh source pages more aggressively while avoiding high-frequency source polling. The live `ColoredEpaperFrame` instance needed its existing hidden setting updated from `23` to `12`.

### Suggested Action
- Keep `dailyLibraryMode=true`.
- Use `libraryRefreshHours=12` for the live Magazine Covers instance.
- Expect manual `display_plugin_instance` calls to exceed HTTP timeout during source/image work; verify success from `journalctl -u inkypi` and `/api/current_image`.
- Do not treat a timed-out manual refresh as failed if logs show `Selected magazine cover triptych from daily library` followed by `Updating display`.

### Metadata
- Source: observed_deploy
- Related Files: `inkypi-weather/package/InkyPi/src/plugins/magazine_covers/magazine_covers.py`, `inkypi-weather/package/InkyPi/src/plugins/magazine_covers/settings.html`
- Tags: magazine-covers, refresh, cache, deploy, ColoredEpaperFrame
