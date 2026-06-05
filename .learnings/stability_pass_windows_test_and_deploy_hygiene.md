# Stability pass Windows test and deploy hygiene

## [LRN-20260605-007] Windows pytest temp and Linux zip payloads

**Logged**: 2026-06-05T00:00:00-07:00
**Priority**: medium
**Status**: active
**Area**: epaper-stability-deploy

### Summary
For EpaperSystem stability work on this Windows machine, route pytest temporary paths into the workspace and build Linux deploy zip files with forward-slash archive entries.

### Details
The Windows sandbox can deny pytest cleanup under the default user temp directory and can also deny test artifact deletion in plugin test folders. A project-local `tmp_path` fixture plus `-p no:cacheprovider` keeps tests repeatable without writing pytest cache or using the restricted temp tree. For Pi deployment, PowerShell `Compress-Archive` can preserve backslashes as literal zip entry names, which creates incorrect files on Linux; build the zip with explicit forward-slash entry names instead. Inline SSH commands that contain Python one-liners are fragile, so upload small shell scripts for readiness checks when output must be reliable.

### Suggested Action
- Use `tools/run_inkypi_tests.ps1` for local regression runs.
- Keep pytest scratch data under `.tmp/pytest-fixtures`.
- Use explicit forward-slash archive entry names for payloads deployed to the Pi.
- Prefer uploaded shell scripts for multi-step remote smoke checks.

### Metadata
- Source: observed_failure
- Related Files: `tools/run_inkypi_tests.ps1`, `inkypi-weather/package/InkyPi/tests/conftest.py`
- Tags: windows, pytest, deploy, zip, epaperpod
