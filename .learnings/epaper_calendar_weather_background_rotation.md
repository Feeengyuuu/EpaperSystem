# Epaper calendar weather background rotation

## [LRN-20260529-112] pattern

**Logged**: 2026-05-29T00:00:00-07:00
**Priority**: medium
**Status**: active
**Area**: epaper

### Summary
Simple Calendar weather backgrounds should rotate by date within a weather/style candidate pool instead of choosing a purely random asset on each refresh.

### Details
The user wants more variety without seeing the same image repeatedly in a short time. For the Simple Calendar date panel, per-refresh randomness would make the e-paper display visually unstable. A deterministic date-based rotation with a per-weather/style hash offset gives each weather condition variety while keeping the same date stable across refreshes.

### Suggested Action
When adding new background sets, store each style under `weather_panel_backgrounds_color/<style>/` with one image per slug, or add variants as `<slug>_*.png` / `<slug>/*.png`. Use `weatherPanelBackgroundStyle=img2_original_heroes_mixed` to rotate across the current natural and NYC comic hero pools.

### Metadata
- Source: implementation
- Related Files: `inkypi-weather/package/InkyPi/src/plugins/simple_calendar/simple_calendar.py`, `inkypi-weather/package/InkyPi/src/plugins/simple_calendar/weather_panel_backgrounds_color/`
- Tags: inkypi, epaperpod, simple-calendar, weather-backgrounds, rotation
