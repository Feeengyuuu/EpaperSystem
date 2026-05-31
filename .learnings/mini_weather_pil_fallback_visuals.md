# Mini Weather PIL fallback visual parity

## [LRN-20260529-118] best_practice

**Logged**: 2026-05-29T18:28:00-07:00
**Priority**: high
**Status**: active
**Area**: epaper

### Summary
Mini Weather visual changes must be applied to both HTML/CSS and PIL fallback on the Pi.

### Details
On the active EpaperPod, Mini Weather logged `HTML render failed; using PIL fallback renderer`, so CSS-only changes to card transparency did not affect the actual device image. The fallback renderer draws cards, labels, and background blending directly in Pillow. User-facing changes such as replacing `NOW` with the current weekday and making card backgrounds transparent must be reflected in `mini_weather.py` as well as `render/mini_weather.css`.

### Suggested Action
Before judging Mini Weather visual changes on hardware, inspect `journalctl -u inkypi` for HTML render fallback warnings. Keep a local PIL fallback preview script for card/background work, and deploy/restart Python changes when the fallback renderer is involved.

### Metadata
- Source: implementation
- Related Files: `inkypi-weather/package/InkyPi/src/plugins/mini_weather/mini_weather.py`, `inkypi-weather/package/InkyPi/src/plugins/mini_weather/render/mini_weather.css`
- Tags: inkypi, mini-weather, pil-fallback, css, visual-qa
