# GitHub playlist instance

## [LRN-20260531-004] project_state

**Logged**: 2026-05-31T17:54:00-07:00
**Priority**: medium
**Status**: active
**Area**: epaper

### Summary
`DailyDoseOfDay` now includes a GitHub Contributions instance named `GitHub`.

### Details
The instance was added through the live app `/add_plugin` endpoint on `ColoredEpaperFrame`. It uses username `Feeengyuuu`, `githubType=contributions`, the original GitHub green heatmap colors, `selectedFrame=Rectangle`, and a daily refresh interval of `86400` seconds. The persistent config path on the live package is `/home/feeengyuuu/inkypi-weather-pi-package-zero2w-20260531/InkyPi/src/config/device.json`.

### Suggested Action
For future GitHub display-list work, prefer updating the existing `DailyDoseOfDay` / `github` / `GitHub` instance instead of creating another one. Verify with `/playlist`, `/display_plugin_instance`, and the `Updating display` journal log.

### Metadata
- Source: live debug
- Related Files: inkypi-weather/package/InkyPi/src/plugins/github/github_contributions.py
- Tags: github, playlist, dailydoseofday, colored-epaper-frame
