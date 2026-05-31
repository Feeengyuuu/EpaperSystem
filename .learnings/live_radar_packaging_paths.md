# LiveRadar plugin zip paths for Pi deployment

## [LRN-20260527-054] environment

**Logged**: 2026-05-27T22:02:00-07:00
**Priority**: medium
**Status**: active
**Area**: epaper

### Summary
Package InkyPi plugins for the Raspberry Pi with POSIX `/` zip paths, not PowerShell `Compress-Archive` output.

### Details
When `Compress-Archive` was used on Windows for the `live_radar` plugin, Raspberry Pi `unzip` warned that the archive used backslashes as path separators and did not overwrite the expected `plugins/live_radar/...` files. The service then kept loading the old module even after a restart. Repacking with Python `zipfile` and archive names like `live_radar/live_radar.py` fixed deployment.

### Suggested Action
For future InkyPi plugin deployments from Windows, create plugin zips with Python `zipfile` and explicit POSIX archive names. After unzipping on the Pi, grep for a newly added helper or constant in the remote file before restarting `inkypi`.

### Metadata
- Source: implementation
- Related Files: inkypi-weather/package/InkyPi/src/plugins/live_radar/live_radar.py
- Tags: inkypi, epaperpod, live-radar, deployment, zipfile, windows, raspberry-pi
