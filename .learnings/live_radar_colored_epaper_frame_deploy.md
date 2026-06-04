# LiveRadar deploy path on ColoredEpaperFrame

## [LRN-20260602-004] project_state

**Logged**: 2026-06-02T01:56:00-07:00
**Priority**: medium
**Status**: active
**Area**: epaper

### Summary
LiveRadar deployment on `ColoredEpaperFrame` should sync both `/usr/local/inkypi` and the home mirror package.

### Details
During the LiveRadar mini-card deploy, `systemctl cat inkypi` showed `ExecStart=/usr/local/bin/inkypi run`, but `pgrep -af inkypi` showed the live Python process running `/home/feeengyuuu/inkypi-weather-pi-package-zero2w-20260531/InkyPi/src/inkypi.py`. Uploading only `/usr/local/inkypi/src/plugins/live_radar/live_radar.py` would not be enough for the running process if the wrapper resolves into the mirror tree.

### Suggested Action
For LiveRadar or similar plugin deploys on `ColoredEpaperFrame`, upload changed runtime files to both `/usr/local/inkypi/src/...` and `/home/feeengyuuu/inkypi-weather-pi-package-zero2w-20260531/InkyPi/src/...`, then verify matching hashes and run `POST /display_plugin_instance`.

### Metadata
- Source: deployment verification
- Related Files: inkypi-weather/package/InkyPi/src/plugins/live_radar/live_radar.py
- Tags: live_radar, deploy, coloredepaperframe, mirror-path, verification
