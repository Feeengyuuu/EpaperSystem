# Epaper calendar background layering

## [LRN-20260529-110] constraint

**Logged**: 2026-05-29T00:00:00-07:00
**Priority**: medium
**Status**: active
**Area**: epaper

### Summary
Simple Calendar weather background assets must not bake in date numbers, weekday text, month text, or any other calendar typography.

### Details
The user corrected the comic weather background workflow: generated `img-2` assets are background images only. Even when exploring character/date interactions, the asset itself should stay text-free and digit-free so the renderer can draw dynamic dates. Any effect where a character visually covers or passes in front of a date number requires a separate transparent foreground overlay layer, not a single background image.

### Suggested Action
For future Simple Calendar visual work, generate and store a clean `304x480` background layer first. Use calendar-preview composites only for review. If interaction with digits is requested, design the implementation as `background -> dynamic text/date -> optional transparent foreground overlay`.

### Metadata
- Source: user correction
- Related Files: `inkypi-weather/package/InkyPi/src/plugins/simple_calendar/weather_panel_backgrounds_color/`
- Tags: inkypi, epaperpod, simple-calendar, img-2, asset-layering
