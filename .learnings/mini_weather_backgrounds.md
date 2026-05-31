# Learning: Mini Weather weather-matched backgrounds

**Logged**: 2026-05-27T18:26:57-07:00
**Priority**: medium
**Status**: active
**Area**: epaper

## Summary
Mini Weather should use bundled 800x480 e-paper background assets selected from the current weather icon, not extra live API calls.

## Details
For weather-matched Mini Weather visuals, generate low-contrast monochrome landscape PNGs and store them under `src/plugins/mini_weather/backgrounds/`. Map the already-parsed current icon slug (`01d`, `10d`, `11d`, `13d`, `50d`, etc.) to a local background category so the feature works for both OpenWeatherMap and OpenMeteo without spending additional weather requests. Keep backgrounds very subtle and render them behind translucent panels; use a night-theme invert/filter rather than shipping separate dark variants for every condition.

Windows local HTML screenshot validation can use installed Edge headless when Chromium is not on PATH, but it may need sandbox escalation because Edge crashpad/profile setup can fail with `Access denied` inside the sandbox. Keep the headless profile and screenshots under project `.tmp`.

## Suggested Action
When adding more Mini Weather weather states, add a bundled `800x480` PNG and extend the icon-to-background mapping in `mini_weather.py`. Validate day and night screenshots at 800x480 before deploying to the Pi; treat `py_compile` `__pycache__` permission failures as environment noise and use AST/no-bytecode checks.

## Metadata
- Source: implementation
- Related Files: `inkypi-weather/package/InkyPi/src/plugins/mini_weather/mini_weather.py`, `inkypi-weather/package/InkyPi/src/plugins/mini_weather/render/mini_weather.css`, `inkypi-weather/package/InkyPi/src/plugins/mini_weather/backgrounds/`
- Tags: inkypi, epaper, mini-weather, weather-backgrounds, imagegen, edge-headless, windows
