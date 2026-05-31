# AwesomeWeather forecast metric nowrap rule

## [LRN-20260531-007] correction

**Logged**: 2026-05-31
**Priority**: high
**Status**: active
**Area**: ui

### Summary
Keep short forecast metrics in AwesomeWeather cards on one line by shrinking the text, not by allowing line breaks.

### Details
On `ColoredEpaperFrame`, the `weather` plugin instance named `AwesomeWeather` showed the moon phase percentage as `100` and `%` on separate lines inside the first forecast card. The card structure collapsed visually because the text wrapped at the space. The user explicitly wanted font-size adjustment instead of wrapping.

### Suggested Action
For compact e-paper forecast cards, treat values such as `100 %` as atomic labels:

- Use `white-space: nowrap`.
- Give the label a dedicated class instead of inline flex styles.
- Reduce font size within the card width rather than splitting the text across lines.
- Preserve the playlist structure: `plugin_id=weather`, `name=AwesomeWeather`, `refresh.interval=300`.

### Metadata
- Source: user_feedback
- Related Files: `inkypi-weather/package/InkyPi/src/plugins/weather/render/weather.html`, `inkypi-weather/package/InkyPi/src/plugins/weather/render/weather.css`
- Tags: inkypi, awesomeweather, weather, forecast-card, typography, colored-epaper-frame

---
