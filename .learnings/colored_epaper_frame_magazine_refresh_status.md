# ColoredEpaperFrame MagazineCovers refresh status checks

## [LRN-20260601-001] workflow

**Logged**: 2026-06-01T00:05:00-07:00
**Priority**: medium
**Status**: active
**Area**: epaper

### Summary
For live MagazineCovers status checks on ColoredEpaperFrame, combine the playlist row, plugin image endpoint, and journal logs.

### Details
On `ColoredEpaperFrame` (`192.168.1.188`), `/playlist` showed `MagazineCovers` as `Displayed Now`, with `data-refresh='{"interval": 300}'`, and `/plugin_instance_image/DailyDoseOfDay/magazine_covers/MagazineCovers` returned HTTP 200. SSH with `.ssh\epaperpod_codex` was rejected, but `.ssh\epaperpod_codex_20260525` worked. Recent logs showed repeated successful cache refreshes and a scheduled display update for `MagazineCovers`; WIRED Japan oversized WebP warnings were recoverable source skips followed by another selected cover.

### Suggested Action
When the user asks whether MagazineCovers is refreshing normally on the color frame, use `Invoke-WebRequest http://192.168.1.188/playlist`, check the `MagazineCovers` block for `Displayed Now`, `latest-refresh`, and `data-refresh`, verify the plugin image endpoint returns 200, then use `ssh -i .ssh\epaperpod_codex_20260525 feeengyuuu@192.168.1.188` to inspect `journalctl -u inkypi` for recent `Refreshing due plugin instance cache`, `Selected magazine cover`, and `Updating display` lines.

### Metadata
- Source: production_debug
- Related Files: `inkypi-weather/package/InkyPi/src/plugins/magazine_covers/magazine_covers.py`
- Tags: epaperpod, colored-epaper-frame, magazine-covers, status-check, ssh
