# Screenshot widget virtual time budget

## [LRN-20260602-007] environment

**Logged**: 2026-06-02T15:45:00-07:00
**Priority**: high
**Status**: active
**Area**: epaper

### Summary
Screenshot-based widgets that load data client-side need Chromium `--virtual-time-budget`; `--timeout` alone can capture the loading screen.

### Details
SportBusy's World Cup widget initially rendered as `Loading World Cup matches...` when captured immediately by Chrome headless. Adding `--virtual-time-budget=10000` allowed the widget JavaScript to fetch and render the match list with flags before screenshot capture. On Windows, headless Chrome also needed a dedicated temporary `--user-data-dir` plus crash reporter flags to avoid default profile/crashpad permission failures.

### Suggested Action
For URL screenshot plugins, pass a virtual time budget when `timeout_ms` is provided, and use a temporary browser profile directory. Keep this behavior in the shared screenshot utility so existing screenshot-based plugins can render JS-fed widgets without custom plugin code.

### Metadata
- Source: SportBusy World Cup widget screenshot validation
- Related Files: inkypi-weather/package/InkyPi/src/utils/image_utils.py
- Tags: screenshot_plugin, chromium, javascript_widget, sportbusy, world_cup, epaper
