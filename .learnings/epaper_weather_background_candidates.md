# Epaper weather background candidates

## [LRN-20260529-109] workflow

**Logged**: 2026-05-29T00:00:00-07:00
**Priority**: low
**Status**: active
**Area**: epaper

### Summary
When exploring colored weather backgrounds, store candidate assets in inactive sibling directories and preview them against the current rendered calendar image before changing live plugin behavior.

### Details
The project has two weather background sizes: Simple Calendar left panel assets at `304x480`, and Mini Weather full-canvas assets at `800x480`. Color candidates should preserve these dimensions and source composition so the date typography and weather mapping remain stable.

### Suggested Action
Use `weather_panel_backgrounds_color/<style>/` and `backgrounds_color/<style>/` for reserved color sets. Generate contact sheets plus a full Date-page preview by pasting the candidate left panel onto the latest `current_image.png`; only wire a candidate into runtime after the user picks a style.

### Metadata
- Source: implementation
- Related Files: `inkypi-weather/package/InkyPi/src/plugins/simple_calendar/weather_panel_backgrounds_color/`, `inkypi-weather/package/InkyPi/src/plugins/mini_weather/backgrounds_color/`
- Tags: inkypi, epaperpod, weather-backgrounds, visual-qa
