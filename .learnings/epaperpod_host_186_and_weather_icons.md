# EpaperPod host and Mini Weather icon style

## [LRN-20260529-117] correction

**Logged**: 2026-05-29T17:35:00-07:00
**Priority**: high
**Status**: active
**Area**: epaper

### Summary
The active EpaperPod device URL is `http://192.168.1.186/`, and Mini Weather icon previews should use the approved old Chinese animation-inspired style.

### Details
The user corrected the device target from `.183` to `.186`. The `.186` host responded with HTTP 200 and SSH hostname `EpaperPodBeta`. For Mini Weather, the requested visual direction is not to copy specific Shanghai Animation Film Studio frames or characters, but to make original weather icons that evoke old Chinese animation craft: ink line, cut-paper shapes, water/ink wash, mineral colors, auspicious cloud forms, and paper texture.

### Suggested Action
For future EpaperPod deploys in this workspace, default to `feeengyuuu@192.168.1.186` unless the user gives a newer address. For Mini Weather icon work, store candidate icons under `icons_color/<style>/`, keep the original `icons/` assets as fallback, and preview against the real Mini Weather layout before finalizing.

### Metadata
- Source: user_feedback
- Related Files: `inkypi-weather/package/InkyPi/src/plugins/mini_weather/icons_color/shanghai_animation/`, `inkypi-weather/package/InkyPi/src/plugins/mini_weather/mini_weather.py`
- Tags: inkypi, epaperpod, host, mini-weather, icons, shanghai-animation
