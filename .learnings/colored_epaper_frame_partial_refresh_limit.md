# ColoredEpaperFrame partial refresh limit

## [LRN-20260531-003] hardware_limit

**Logged**: 2026-05-31
**Priority**: high
**Status**: active
**Area**: display

### Summary
`ColoredEpaperFrame` currently uses the Waveshare 7.3 inch Spectra 6 / E6 full-color panel through the `epd7in3e` driver, and should be treated as full-refresh-only.

### Details
The active device profile is `epd7in3e`. Its driver exposes `display()` and `Clear()` as whole-frame operations and does not expose a partial/window refresh API. The current `WaveshareDisplay` wrapper also initializes, clears, writes the full image buffer, and sleeps the panel for each update.

### Suggested Action
Do not design current 7-color UI flows around true partial refresh. If a page needs faster apparent updates, cache expensive rendered regions in software and keep the layout stable, but still expect the physical panel update to be full-screen. For true hardware partial refresh, use a panel/driver that explicitly supports partial mode, such as some monochrome Waveshare drivers.

### Metadata
- Source: code_inspection
- Related Files: `inkypi-weather/package/InkyPi/install/config_base/device.json`, `inkypi-weather/package/InkyPi/src/display/waveshare_epd/epd7in3e.py`, `inkypi-weather/package/InkyPi/src/display/waveshare_display.py`
- Tags: colored-epaper-frame, waveshare, epd7in3e, partial-refresh, display

---
