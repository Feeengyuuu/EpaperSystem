# Epaper calendar weather readability

## [LRN-20260529-111] constraint

**Logged**: 2026-05-29T00:00:00-07:00
**Priority**: medium
**Status**: active
**Area**: epaper

### Summary
Night weather backgrounds for Simple Calendar need a light central field because the date typography is rendered in black.

### Details
During the `img-2` comic weather background batch, the first clear-night candidate used a dark blue center. The actual calendar preview showed poor contrast for the black weekday, date, rule, and month text. Regenerating the clear-night asset with a pale moonlit center fixed readability while keeping night cues at the top corners and bottom horizon.

### Suggested Action
For future `304x480` Simple Calendar background assets, keep the center band around the dynamic date text light and low-detail for every weather state, including night and thunderstorm. Put dark weather cues, characters, and heavy linework near the top/bottom/edges unless a separate foreground overlay is intentionally being composited.

### Metadata
- Source: implementation
- Related Files: `inkypi-weather/package/InkyPi/src/plugins/simple_calendar/weather_panel_backgrounds_color/img2_original_heroes_weather/`
- Tags: inkypi, epaperpod, simple-calendar, weather-backgrounds, readability
