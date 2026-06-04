# AwesomeWeather display timeout verification

## [LRN-20260601-005] deployment

**Logged**: 2026-06-01T14:35:00-07:00
**Priority**: medium
**Status**: active
**Area**: epaper

### Summary
On ColoredEpaperFrame, an AwesomeWeather manual display request can exceed the local HTTP client timeout while the device still completes rendering and writing to the e-paper panel.

### Details
Replacing the active weather entry by adding `weather/AwesomeWeather` to `DailyDoseOfDay` succeeded through `/add_plugin`, but `POST /display_plugin_instance` timed out after 240 seconds. The service stayed active and journal logs later showed `Manual update requested`, `Refreshing plugin instance. | plugin_instance: 'AwesomeWeather'`, `Updating display` for `weather/AwesomeWeather`, and the Waveshare sleep message. After the display finished, both `/api/current_image` and `/plugin_instance_image/DailyDoseOfDay/weather/AwesomeWeather` returned HTTP 200 with the same PNG length.

### Suggested Action
For future AwesomeWeather deploy/display checks, treat an HTTP timeout as inconclusive until journal logs and image endpoints are checked. Wait for the Waveshare sleep log before judging final HTTP health.

### Metadata
- Source: deployment
- Related Files: `inkypi-weather/package/InkyPi/src/plugins/weather/weather.py`, `inkypi-weather/package/InkyPi/src/plugins/weather/render/weather.html`
- Tags: colored-epaper-frame, awesomeweather, weather, display-timeout, verification
