# GCD comic covers Comic Vine priority recycle

## [LRN-20260604-008] best_practice

**Logged**: 2026-06-04
**Priority**: high
**Status**: active
**Area**: epaper, comic-covers, source-fallback

### Summary
ComicCovers should default to `mixed` and recycle Comic Vine recent covers before falling back to GCD metadata.

### Details
On ColoredEpaperFrame, the `ComicCovers` instance had no persisted `sourceMode`, so the previous default `gcd` skipped the available Comic Vine key. GCD then returned many issues with no cover URL plus 403/429 image/API failures, causing the plugin to render a text-only metadata card. After switching the default and instance setting to `mixed`, Comic Vine returned 24 candidates with real image URLs. A second issue appeared when the day's Comic Vine priority candidates were already marked seen: the ordering code dropped to GCD candidates while lower-quality GCD candidates were still unexhausted. Recycling the Comic Vine priority pool before GCD fallback restored real cover images.

### Suggested Action
For `gcd_comic_covers`, keep `DEFAULT_SOURCE_MODE = "mixed"` and make the settings page default to mixed. When `match_quality == "comicvine_recent"` candidates exist but are all seen for the day, recycle that priority pool while avoiding the immediate last issue when possible. When updating an existing instance through `/update_plugin_instance/<name>`, include `plugin_id=gcd_comic_covers`; the persisted config field is `plugin_settings`.

### Metadata
- Source: production_debug
- Related Files: `inkypi-weather/package/InkyPi/src/plugins/gcd_comic_covers/gcd_comic_covers.py`, `inkypi-weather/package/InkyPi/src/plugins/gcd_comic_covers/settings.html`, `inkypi-weather/package/InkyPi/tests/test_gcd_comic_covers.py`
- Tags: gcd-comic-covers, comic-vine, gdc, 429, 403, plugin-settings, random-playlist

---
