# Epaper multi-plugin SFTP batch deploy

## [LRN-20260602-005] project_quirk

**Logged**: 2026-06-02T02:22:00-07:00
**Priority**: medium
**Status**: active
**Area**: epaper

### Summary
For multi-plugin runtime deploys on `ColoredEpaperFrame`, use a single `sftp.exe -b` batch instead of one SCP connection per file.

### Details
Deploying the GitHub plugin plus other runtime files with repeated `scp` timed out before producing useful progress, even though the device stayed healthy. A generated SFTP batch with `mkdir` and `put` commands uploaded 37 files to both `/usr/local/inkypi/src` and `/home/feeengyuuu/inkypi-weather-pi-package-zero2w-20260531/InkyPi/src` in one connection and completed quickly. Existing-directory `mkdir` failures are acceptable when commands are prefixed with `-`.

### Suggested Action
When syncing more than a handful of EpaperSystem runtime files, generate a temporary `.sftp` batch under `.tmp`, upload to both active and mirror paths, then verify local-vs-remote SHA256 counts before rebooting.

### Metadata
- Source: deployment verification
- Related Files: inkypi-weather/package/InkyPi/src/plugins/github/github_contributions.py
- Tags: deploy, sftp, coloredepaperframe, runtime-sync, multi-plugin
