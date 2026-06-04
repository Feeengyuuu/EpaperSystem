# DailyArt Portrait Gallery Layout

## [LRN-20260603-004] best_practice

**Logged**: 2026-06-03
**Priority**: medium
**Status**: active
**Area**: epaper

### Summary
DailyArt should avoid showing a single narrow portrait artwork centered on the landscape e-paper screen.

### Details
On `ColoredEpaperFrame`, portrait museum scans look sparse when rendered one at a time with contain fit. The DailyArt plugin now supports `layoutMode=auto_gallery`, which keeps landscape artworks as a single large image only when no portrait candidates are available, but collects portrait candidates into a 3-item horizontal gallery by default. The cache payload records both `artwork` and `artworks`, and the context cache writes a `museum_artwork_gallery` item so downstream source display can cite all visible artworks.

### Suggested Action
For portrait-heavy visual plugins, prefer a 3-up gallery on the 800x480 panel. Include layout settings in the cache key, increase candidate/attempt limits enough to find multiple portrait images, and store multi-item source metadata rather than only the first image.

### Metadata
- Source: production_deploy
- Related Files: `inkypi-weather/package/InkyPi/src/plugins/daily_art/daily_art.py`, `inkypi-weather/package/InkyPi/src/plugins/daily_art/settings.html`, `inkypi-weather/package/InkyPi/tests/test_daily_art.py`
- Tags: inkypi, daily-art, museum-api, gallery, portrait, layout, context-cache
